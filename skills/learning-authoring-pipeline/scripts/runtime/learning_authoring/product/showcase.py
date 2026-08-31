"""Build a connected, publish-safe static portal for one authoring run.

The builder copies an explicit allowlist only. It never copies a run directory,
source document, raw candidate file, prompt package, provider response, or secret.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from base64 import urlsafe_b64decode
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from learning_authoring.artifacts import require_current_revision
from learning_authoring.authoring_context import load_authoring_context
from learning_authoring.contracts import SourceDescriptor

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE_DIR = PACKAGE_ROOT / "showcase_assets"
MANAGED_BY = "learning-authoring portal-build"
LEGACY_MANAGED_BY = "scripts/publish_showcase.py"
MANIFEST_NAME = "showcase-manifest.json"
SOURCE_MANIFEST_NAME = "source-manifest.json"


class PublishSafetyError(RuntimeError):
    """Raised when an input or output is unsafe to publish or replace."""


@dataclass(frozen=True)
class ReviewArtifact:
    entrypoint: str
    source_name: str
    stage: str
    public_status: str


@dataclass(frozen=True)
class ReviewFiles:
    """Exact run-local review filenames selected for one portal."""

    extractor: str = "extraction-review.html"
    kc_recall: str = "kc-recall.html"
    kc_scroll: str = "kc-scroll.html"
    quiz: str = "quiz-review.html"


@dataclass(frozen=True)
class ReviewBackendConfig:
    """Public browser configuration for an optional shared review backend."""

    supabase_url: str
    supabase_publishable_key: str


@dataclass(frozen=True)
class PageImage:
    page: int
    image_ref: str
    image_sha256: str


@dataclass(frozen=True)
class SourceMetadata:
    filename: str
    page_count: int
    source_id: str
    source_sha256: str
    page_images: tuple[PageImage, ...]


@dataclass(frozen=True)
class StageStatus:
    code: str
    label: str
    css_class: str
    description: str


@dataclass(frozen=True)
class ExtractionState:
    status: StageStatus
    artifact_path: Path
    proposed_sha256: str | None
    approved_sha256: str | None


@dataclass(frozen=True)
class RunSummary:
    extraction_status: StageStatus
    kc_upstream_extraction_status: str
    kc_status: StageStatus
    quiz_status: StageStatus
    leaf_kc_count: int
    kc_group_count: int
    selected_kc_count: int
    question_count: int


DEFAULT_REVIEW_FILES = ReviewFiles()

STATIC_TEMPLATE_FILES = ("index.html", "vercel.json", "robots.txt", "review-runtime.js")
GENERATED_TEMPLATE_FILES = ("review-config.js",)
TEMPLATE_FILES = STATIC_TEMPLATE_FILES + GENERATED_TEMPLATE_FILES
LEARNING_TEMPLATE_FILES = (
    "learning.html", "learning-core.js", "learning-runtime.js", "learning-style.css",
)
LEARNING_GENERATED_FILES = ("learning-data.js",)
FORBIDDEN_NAME_PARTS = (
    ".env",
    "api-response",
    "request-preview",
    "prompt-package",
    "background-checkpoint",
    "output.raw",
    "source.pdf",
)
SECRET_PATTERNS = (
    (
        "OpenAI-compatible secret",
        re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"),
    ),
    (
        "bearer credential",
        re.compile(rb"(?i)(?<![A-Za-z0-9])Bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    ),
    (
        "private key",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "AWS access key",
        re.compile(rb"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])"),
    ),
)
LOCAL_PATH_PATTERNS = (
    re.compile(
        r"(?:file://)?/(?:Users|var/folders|private/var|private/tmp|tmp|home)/"
        r"[^\"'<>\r\n]+"
    ),
    re.compile(r"[A-Za-z]:\\\\Users\\\\[^\"'<>\r\n]+"),
)
FORBIDDEN_CONTENT_MARKERS = (
    b"/Users/",
    b"/var/folders/",
    b"/private/var/",
    b"/private/tmp/",
    b"/tmp/",
    b"file://",
    b"\\\\Users\\\\",
)
PORTAL_PLACEHOLDER_PATTERN = re.compile(r"\{\{[A-Z0-9_]+\}\}")
SAFE_REVIEW_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.html")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SUPABASE_PROJECT_HOST = re.compile(r"[a-z0-9]{10,}\.supabase\.co")

HUMAN_APPROVED = StageStatus(
    code="HUMAN_APPROVED",
    label="HUMAN APPROVED",
    css_class="approved",
    description=(
        "Extraction đã vượt qua review và boundary phê duyệt của con người. "
        "Reviewer, note và approval metadata không nằm trong portal."
    ),
)
PROPOSED = StageStatus(
    code="PROPOSED",
    label="PROPOSED · REVIEW NEEDED",
    css_class="proposed",
    description="Extraction là proposed output và vẫn cần con người review.",
)
KC_PROPOSED = StageStatus(
    code="PROPOSED",
    label="PROPOSED · REVIEW NEEDED",
    css_class="proposed",
    description=(
        "Hai cách xem KC trong run này cùng trình bày proposed output; "
        "KC chưa có approval boundary chính thức."
    ),
)
QUIZ_EXPERIMENTAL = StageStatus(
    code="EXPERIMENTAL_UNAPPROVED",
    label="EXPERIMENTAL · UNAPPROVED",
    css_class="experimental",
    description=(
        "Quiz review được chọn từ run này là experimental và chưa được duyệt. "
        "Contract hoặc form checks không chứng minh chất lượng ngữ nghĩa."
    ),
)
LEARNING_DISABLED = StageStatus(
    code="NOT_ENABLED",
    label="MVP · CHƯA BẬT",
    css_class="roadmap",
    description=(
        "Bản này chỉ chứa Authoring review. Build với --with-learning để thêm "
        "lượt làm, evidence và mastery ban đầu; không tạo sẵn kết quả người học."
    ),
)
LEARNING_MVP = StageStatus(
    code="PROVISIONAL_EVIDENCE_MVP",
    label="MVP · EVIDENCE BAN ĐẦU",
    css_class="proposed",
    description=(
        "Làm quiz và dùng hint → chấm → evidence → trạng thái từng KC → học tiếp. "
        "Mastery là quy tắc ban đầu, chưa hiệu chuẩn; feedback chất lượng nằm riêng."
    ),
)


def _review_artifacts(review_files: ReviewFiles) -> tuple[ReviewArtifact, ...]:
    artifacts = (
        ReviewArtifact("extractor", review_files.extractor, "extractor", PROPOSED.code),
        ReviewArtifact("kc_recall", review_files.kc_recall, "kc", KC_PROPOSED.code),
        ReviewArtifact("kc_scroll", review_files.kc_scroll, "kc", KC_PROPOSED.code),
        ReviewArtifact(
            "quiz_experiment",
            review_files.quiz,
            "quiz",
            QUIZ_EXPERIMENTAL.code,
        ),
    )
    names = [artifact.source_name for artifact in artifacts]
    if len(names) != len(set(names)):
        raise PublishSafetyError("Review filenames must be distinct")
    for name in names:
        if not SAFE_REVIEW_FILENAME.fullmatch(name):
            raise PublishSafetyError(
                f"Review selection must be one run-local HTML filename: {name!r}"
            )
        lowered = name.lower()
        if any(forbidden in lowered for forbidden in FORBIDDEN_NAME_PARTS):
            raise PublishSafetyError(f"Forbidden review filename selected: {name}")
    return artifacts


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _reject_symlink_components(path: Path, label: str) -> None:
    """Reject an existing symlink anywhere in an absolute path."""

    absolute = _absolute_without_resolving(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise PublishSafetyError(
                f"Symlink path components are not allowed for {label}: {current}"
            )


def _run_path(run_dir: Path, relative: str | Path, label: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise PublishSafetyError(f"{label} must stay inside the run directory: {relative}")
    candidate = run_dir / relative_path
    try:
        candidate.relative_to(run_dir)
    except ValueError as exc:
        raise PublishSafetyError(f"{label} must stay inside the run directory: {relative}") from exc
    current = run_dir
    for part in relative_path.parts:
        current /= part
        if current.is_symlink():
            raise PublishSafetyError(
                f"Symlink path components are not allowed for {label}: {current}"
            )
    try:
        candidate.resolve(strict=False).relative_to(run_dir)
    except ValueError as exc:
        raise PublishSafetyError(f"{label} escapes the run directory: {relative}") from exc
    return candidate


def _require_run_file(run_dir: Path, relative: str | Path, label: str) -> Path:
    path = _run_path(run_dir, relative, label)
    _require_regular_file(path)
    return path


def _has_run_file(run_dir: Path, relative: str | Path, label: str) -> bool:
    path = _run_path(run_dir, relative, label)
    if path.exists() and not path.is_file():
        raise PublishSafetyError(f"Expected a regular file for {label}: {path}")
    return path.is_file()


def _page_inventory(payload: dict[str, object], page_count: int) -> tuple[PageImage, ...]:
    records = payload.get("page_records")
    if not isinstance(records, list) or len(records) != page_count:
        raise PublishSafetyError(
            "source-manifest page_records must be a complete ordered inventory"
        )
    images: list[PageImage] = []
    seen_refs: set[str] = set()
    for expected_page, record in enumerate(records, start=1):
        if not isinstance(record, dict) or record.get("page") != expected_page:
            raise PublishSafetyError(
                "source-manifest page_records must contain unique pages in order"
            )
        image_ref = record.get("image_ref")
        image_sha256 = record.get("image_sha256")
        if not isinstance(image_ref, str) or not image_ref:
            raise PublishSafetyError(f"page_records[{expected_page}] has an invalid image_ref")
        pure_ref = PurePosixPath(image_ref)
        if (
            pure_ref.is_absolute()
            or pure_ref.as_posix() != image_ref
            or len(pure_ref.parts) < 2
            or pure_ref.parts[0] != "pages"
            or any(part in {"", ".", ".."} for part in pure_ref.parts)
            or pure_ref.suffix.lower() != ".png"
        ):
            raise PublishSafetyError(
                f"page_records[{expected_page}] image_ref must be a run-local pages/*.png path"
            )
        if image_ref in seen_refs:
            raise PublishSafetyError(
                "source-manifest page_records contain duplicate image_ref values"
            )
        if not isinstance(image_sha256, str) or not SHA256_PATTERN.fullmatch(image_sha256):
            raise PublishSafetyError(f"page_records[{expected_page}] has an invalid image_sha256")
        seen_refs.add(image_ref)
        images.append(PageImage(expected_page, image_ref, image_sha256))
    return tuple(images)


def _load_source_metadata(run_dir: Path) -> SourceMetadata:
    manifest_path = _require_run_file(
        run_dir,
        SOURCE_MANIFEST_NAME,
        "source manifest",
    )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PublishSafetyError(f"Invalid source manifest: {manifest_path}") from exc
    source = payload.get("source")
    if not isinstance(source, dict):
        raise PublishSafetyError("source-manifest.json must contain a source object")
    filename = source.get("filename")
    page_count = source.get("page_count")
    source_id = source.get("source_id")
    source_sha256 = source.get("sha256")
    if not isinstance(filename, str) or not filename.strip() or Path(filename).name != filename:
        raise PublishSafetyError("source.filename must be one non-empty basename")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
        raise PublishSafetyError("source.page_count must be a positive integer")
    if not isinstance(source_id, str) or not source_id.strip():
        raise PublishSafetyError("source.source_id must be a non-empty string")
    if not isinstance(source_sha256, str) or not SHA256_PATTERN.fullmatch(source_sha256):
        raise PublishSafetyError("source.sha256 must be a lowercase SHA-256 digest")
    page_images = _page_inventory(payload, page_count)
    return SourceMetadata(
        filename=filename.strip(),
        page_count=page_count,
        source_id=source_id,
        source_sha256=source_sha256,
        page_images=page_images,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_review_backend(
    backend: ReviewBackendConfig | None,
) -> ReviewBackendConfig | None:
    if backend is None:
        return None
    url = backend.supabase_url.strip().rstrip("/")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or not SUPABASE_PROJECT_HOST.fullmatch(parsed.hostname)
    ):
        raise PublishSafetyError(
            "Shared review requires an exact Supabase project URL such as "
            "https://<project-ref>.supabase.co"
        )

    key = backend.supabase_publishable_key.strip()
    if key.startswith("sb_publishable_") and len(key) >= 24:
        return ReviewBackendConfig(url, key)
    if key.count(".") == 2:
        try:
            encoded = key.split(".")[1]
            padding = "=" * (-len(encoded) % 4)
            claims = json.loads(urlsafe_b64decode(encoded + padding))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublishSafetyError("Invalid Supabase browser key") from exc
        if claims.get("role") == "service_role":
            raise PublishSafetyError("Supabase service-role keys must never enter the portal")
        if claims.get("role") == "anon":
            return ReviewBackendConfig(url, key)
    raise PublishSafetyError(
        "Shared review requires a Supabase publishable key (or legacy anon browser key)"
    )


def _require_regular_file(path: Path) -> None:
    if path.is_symlink():
        raise PublishSafetyError(f"Symlink inputs are not allowed: {path}")
    if not path.is_file():
        raise PublishSafetyError(f"Required file is missing: {path}")


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    _require_regular_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PublishSafetyError(f"Invalid {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise PublishSafetyError(f"{label} must contain a JSON object: {path}")
    return payload


def _read_run_json(run_dir: Path, relative: str | Path, label: str) -> dict[str, object]:
    return _read_json_object(_require_run_file(run_dir, relative, label), label)


def _derive_extraction_state(run_dir: Path, metadata: SourceMetadata) -> ExtractionState:
    """Verify the approval boundary without copying approval data to output."""

    proposed_name = "extracted-source.proposed.json"
    approved_name = "extracted-source.approved.json"
    approval_name = "extraction-approval.json"
    proposed_path = _run_path(run_dir, proposed_name, "proposed extraction")
    approved_path = _run_path(run_dir, approved_name, "approved extraction")
    proposed_exists = _has_run_file(run_dir, proposed_name, "proposed extraction")
    approved_exists = _has_run_file(run_dir, approved_name, "approved extraction")
    approval_exists = _has_run_file(run_dir, approval_name, "extraction approval")
    proposed_sha256: str | None = None
    if proposed_exists:
        _read_run_json(run_dir, proposed_name, "proposed extraction")
        proposed_sha256 = _sha256(proposed_path)

    if approved_exists != approval_exists:
        raise PublishSafetyError(
            "Incomplete extraction approval boundary: both extraction-approval.json and "
            "extracted-source.approved.json are required"
        )

    if approved_exists and approval_exists:
        approval = _read_run_json(run_dir, approval_name, "extraction approval")
        approved = _read_run_json(run_dir, approved_name, "approved extraction")
        if approval.get("status") != "approved":
            raise PublishSafetyError("Extraction approval status is not approved")
        recorded_hash = approval.get("approved_sha256")
        if not isinstance(recorded_hash, str) or recorded_hash != _sha256(approved_path):
            raise PublishSafetyError("Approved extraction hash does not match approval record")
        approval_schema = approval.get("schema_version")
        approved_schema = approved.get("schema_version")
        if (
            not isinstance(approval_schema, str)
            or not isinstance(approved_schema, str)
            or approval_schema != approved_schema
        ):
            raise PublishSafetyError("Approved extraction schema does not match approval record")
        approval_source_hash = approval.get("source_sha256")
        if metadata.source_sha256 is not None and (
            not isinstance(approval_source_hash, str)
            or approval_source_hash != metadata.source_sha256
        ):
            raise PublishSafetyError("Approved extraction source does not match source manifest")
        approved_sha256 = _sha256(approved_path)
        return ExtractionState(
            HUMAN_APPROVED,
            approved_path,
            proposed_sha256,
            approved_sha256,
        )

    if proposed_exists:
        assert proposed_sha256 is not None
        return ExtractionState(
            PROPOSED,
            proposed_path,
            proposed_sha256,
            None,
        )

    raise PublishSafetyError(
        "Run has neither a verified human-approved extraction nor a proposed extraction"
    )


def _read_review_html(path: Path, *, run_dir: Path | None = None) -> str:
    if run_dir is None:
        _require_regular_file(path)
    else:
        try:
            relative = path.relative_to(run_dir)
        except ValueError as exc:
            raise PublishSafetyError(f"Review HTML escapes the run directory: {path}") from exc
        path = _require_run_file(run_dir, relative, "review HTML")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PublishSafetyError(f"Review HTML is not UTF-8: {path}") from exc


def _json_assignment(content: str, marker: str, label: str) -> dict[str, object]:
    """Decode a JSON object immediately following a deterministic HTML marker."""

    start = content.find(marker)
    if start < 0:
        raise PublishSafetyError(f"{label} is missing from selected review HTML")
    try:
        value, _ = json.JSONDecoder().raw_decode(content[start + len(marker) :])
    except json.JSONDecodeError as exc:
        raise PublishSafetyError(f"Invalid {label} in selected review HTML") from exc
    if not isinstance(value, dict):
        raise PublishSafetyError(f"{label} must be a JSON object")
    return value


def _nested_object(payload: dict[str, object], *keys: str, label: str) -> dict[str, object]:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            raise PublishSafetyError(f"{label} must be an object")
        current = current.get(key)
    if not isinstance(current, dict):
        raise PublishSafetyError(f"{label} must be an object")
    return current


def _positive_count(value: object, label: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise PublishSafetyError(f"{label} must be a {qualifier} integer")
    return value


def _require_source_identity(
    source: dict[str, object],
    metadata: SourceMetadata,
    label: str,
) -> None:
    expected = {
        "filename": metadata.filename,
        "page_count": metadata.page_count,
        "source_id": metadata.source_id,
        "sha256": metadata.source_sha256,
    }
    for key, expected_value in expected.items():
        if expected_value is not None and source.get(key) != expected_value:
            raise PublishSafetyError(f"{label} {key} does not match source manifest")


def _parse_extraction_review(
    path: Path,
    run_dir: Path,
    metadata: SourceMetadata,
    extraction_path: Path,
) -> None:
    content = _read_review_html(path, run_dir=run_dir)
    source_payload = _json_assignment(content, "const source=", "extraction source payload")
    canonical_extraction = _read_run_json(
        run_dir,
        extraction_path.relative_to(run_dir),
        "connected extraction artifact",
    )
    if source_payload != canonical_extraction:
        raise PublishSafetyError(
            "Selected Extraction review does not match the connected extraction artifact"
        )
    source = _nested_object(source_payload, "source", label="extraction source identity")
    _require_source_identity(source, metadata, "Extraction review")
    pages = source_payload.get("pages")
    if not isinstance(pages, list) or len(pages) != metadata.page_count:
        raise PublishSafetyError("Extraction review page count does not match source manifest")
    metrics = _json_assignment(content, "const metrics=", "extraction metrics")
    if metrics.get("source_page_count") != metadata.page_count:
        raise PublishSafetyError("Extraction metrics page count does not match source manifest")
    embedded_manifest = _json_assignment(
        content,
        "const sourceManifest=",
        "embedded source manifest",
    )
    embedded_source = _nested_object(
        embedded_manifest,
        "source",
        label="embedded source manifest identity",
    )
    _require_source_identity(embedded_source, metadata, "Extraction embedded manifest")


def _require_context_lineage(
    source_ref: dict[str, object], run_dir: Path, metadata: SourceMetadata, label: str
) -> None:
    """Verify supplemental input identity without copying private context inputs."""

    try:
        context = load_authoring_context(
            run_dir,
            SourceDescriptor(
                source_id=metadata.source_id,
                filename=metadata.filename,
                sha256=metadata.source_sha256,
                page_count=metadata.page_count,
            ),
        )
    except (OSError, ValueError) as exc:
        raise PublishSafetyError(f"{label} authoring context is invalid: {exc}") from exc
    expected_hash = context.sha256 if context is not None else None
    if source_ref.get("authoring_context_sha256") != expected_hash:
        raise PublishSafetyError(f"{label} authoring context hash is stale or missing")


def _parse_kc_review(
    path: Path,
    run_dir: Path,
    metadata: SourceMetadata,
    extraction_state: ExtractionState,
    *,
    expected_scroll_mode: bool,
) -> tuple[dict[str, object], str]:
    content = _read_review_html(path, run_dir=run_dir)
    payload = _json_assignment(content, "const DATA=", "KC review payload")
    source = _nested_object(payload, "source", "source", label="KC source identity")
    _require_source_identity(source, metadata, "KC review")
    stage_metadata = _nested_object(payload, "candidate", "metadata", label="KC metadata")
    if stage_metadata.get("stage") != "kc":
        raise PublishSafetyError("Selected KC review does not declare the KC stage")
    upstream = _nested_object(
        stage_metadata,
        "upstream_extraction",
        label="KC upstream extraction",
    )
    upstream_status = upstream.get("status")
    if upstream_status == "PROPOSED_DEMO_ONLY":
        expected_sha256 = extraction_state.proposed_sha256
        expected_demo_only = True
    elif upstream_status == "HUMAN_APPROVED":
        expected_sha256 = extraction_state.approved_sha256
        expected_demo_only = False
    else:
        raise PublishSafetyError(f"Unsupported KC upstream extraction status: {upstream_status!r}")
    if expected_sha256 is None:
        raise PublishSafetyError(f"KC upstream {upstream_status} artifact is missing from this run")
    if upstream.get("sha256") != expected_sha256:
        raise PublishSafetyError("KC review is not connected to this extraction artifact")
    if upstream.get("demo_only") is not expected_demo_only:
        raise PublishSafetyError("KC upstream demo boundary does not match extraction status")
    candidate = _nested_object(payload, "candidate", "proposed", label="KC candidate")
    source_ref = _nested_object(candidate, "source_ref", label="KC source reference")
    if source_ref.get("source_sha256") != metadata.source_sha256:
        raise PublishSafetyError("KC candidate source hash does not match source manifest")
    if metadata.source_id is not None and source_ref.get("source_id") != metadata.source_id:
        raise PublishSafetyError("KC candidate source id does not match source manifest")
    _require_context_lineage(source_ref, run_dir, metadata, "KC candidate")
    if payload.get("scroll_mode") is not expected_scroll_mode:
        expected = "scroll" if expected_scroll_mode else "recall"
        raise PublishSafetyError(f"Selected KC {expected} review has the wrong view mode")
    return payload, upstream_status


def _validate_kc_pair(
    recall: dict[str, object],
    scroll: dict[str, object],
    run_dir: Path,
) -> tuple[StageStatus, int, int, set[str], str]:
    recall_metadata = _nested_object(
        recall,
        "candidate",
        "metadata",
        label="KC recall metadata",
    )
    scroll_metadata = _nested_object(
        scroll,
        "candidate",
        "metadata",
        label="KC scroll metadata",
    )
    lineage_keys = ("request_fingerprint", "candidate_raw_sha256", "approval_status")
    for key in lineage_keys:
        if recall_metadata.get(key) != scroll_metadata.get(key):
            raise PublishSafetyError(f"KC recall and scroll reviews disagree on {key}")
    status_code = recall_metadata.get("approval_status")
    if status_code != KC_PROPOSED.code:
        raise PublishSafetyError(f"Unsupported KC publish status: {status_code!r}")
    recall_candidate = _nested_object(recall, "candidate", "proposed", label="KC recall candidate")
    scroll_candidate = _nested_object(scroll, "candidate", "proposed", label="KC scroll candidate")
    if recall_candidate != scroll_candidate:
        raise PublishSafetyError("KC recall and scroll reviews contain different KC sets")

    canonical_metadata = _read_run_json(
        run_dir,
        "kc-generation-metadata.json",
        "KC generation metadata",
    )
    if recall_metadata != canonical_metadata or scroll_metadata != canonical_metadata:
        raise PublishSafetyError("Selected KC reviews do not match run-local KC metadata")
    recall_raw_metrics = _nested_object(
        recall,
        "candidate",
        "raw_metrics",
        label="KC recall raw metrics",
    )
    scroll_raw_metrics = _nested_object(
        scroll,
        "candidate",
        "raw_metrics",
        label="KC scroll raw metrics",
    )
    canonical_metrics = _read_run_json(run_dir, "kc-run-metrics.json", "KC run metrics")
    if recall_raw_metrics != canonical_metrics or scroll_raw_metrics != canonical_metrics:
        raise PublishSafetyError("Selected KC reviews do not match run-local KC metrics")

    kc_path = _require_run_file(run_dir, "kc-proposed.json", "proposed KC set")
    kc_file = _read_run_json(run_dir, "kc-proposed.json", "proposed KC set")
    if kc_file != recall_candidate:
        raise PublishSafetyError("Selected KC reviews do not match run-local kc-proposed.json")
    leaf_kcs = recall_candidate.get("leaf_kcs")
    groups = recall_candidate.get("kc_groups")
    if not isinstance(leaf_kcs, list) or not isinstance(groups, list):
        raise PublishSafetyError("KC candidate must contain leaf_kcs and kc_groups arrays")
    leaf_ids = {
        row.get("kc_id")
        for row in leaf_kcs
        if isinstance(row, dict) and isinstance(row.get("kc_id"), str)
    }
    if len(leaf_ids) != len(leaf_kcs):
        raise PublishSafetyError("KC candidate contains missing or duplicate KC ids")
    metrics = _nested_object(recall, "candidate", "metrics", label="KC metrics")
    leaf_count = _positive_count(metrics.get("leaf_kcs"), "KC leaf count")
    group_count = _positive_count(metrics.get("groups"), "KC group count")
    if leaf_count != len(leaf_kcs) or group_count != len(groups):
        raise PublishSafetyError("KC review counts do not match the connected KC set")
    return KC_PROPOSED, leaf_count, group_count, leaf_ids, _sha256(kc_path)


def _parse_quiz_review(
    path: Path,
    run_dir: Path,
    metadata: SourceMetadata,
    kc_sha256: str,
    leaf_kc_ids: set[str],
    expected_upstream_status: str,
) -> tuple[StageStatus, int, int]:
    content = _read_review_html(path, run_dir=run_dir)
    marker = '<script id="payload" type="application/json">'
    payload = _json_assignment(content, marker, "Quiz review payload")
    canonical_files = {
        "quiz": ("quiz-proposed.json", "Quiz proposed artifact"),
        "input": ("quiz-input.json", "Quiz input"),
        "metrics": ("quiz-run-metrics.json", "Quiz run metrics"),
        "metadata": ("quiz-generation-metadata.json", "Quiz generation metadata"),
        "form_audit": ("quiz-form-audit.json", "Quiz form audit"),
    }
    for payload_key, (filename, label) in canonical_files.items():
        canonical = _read_run_json(run_dir, Path("quiz") / filename, label)
        if payload.get(payload_key) != canonical:
            raise PublishSafetyError(f"Selected Quiz review does not match run-local {filename}")
    from learning_authoring.quiz_review_state import load_quiz_semantic_state

    semantic_state = load_quiz_semantic_state(run_dir)
    if "semantic_audit" in payload or semantic_state["status"] != "NOT_REVIEWED":
        if payload.get("semantic_audit") != semantic_state:
            raise PublishSafetyError(
                "Selected Quiz review has stale or unbound semantic status; rebuild the review"
            )
    quiz = _nested_object(payload, "quiz", label="Quiz artifact")
    source_ref = _nested_object(quiz, "source_ref", label="Quiz source reference")
    expected_ref = {
        "extraction_source_sha256": metadata.source_sha256,
        "kc_set_sha256": kc_sha256,
    }
    if metadata.source_id is not None:
        expected_ref["extraction_source_id"] = metadata.source_id
    for key, value in expected_ref.items():
        if source_ref.get(key) != value:
            raise PublishSafetyError(f"Quiz {key} does not match its connected upstream artifact")
    _require_context_lineage(source_ref, run_dir, metadata, "Quiz")
    quiz_input_ref = _nested_object(payload, "input", "source_ref", label="Quiz input source")
    # A single-source run may serialize either optional lineage hash as absent
    # or explicit null. Those two encodings carry the same identity; every
    # populated or unexpected field remains strict, and the raw artifacts stay
    # untouched.
    optional_null_fields = {
        "authoring_context_sha256": None,
        "source_bundle_sha256": None,
    }
    normalized_input_ref = {**optional_null_fields, **quiz_input_ref}
    normalized_output_ref = {**optional_null_fields, **source_ref}
    if normalized_input_ref != normalized_output_ref:
        raise PublishSafetyError("Quiz input and output source references do not match")
    stage_metadata = _nested_object(payload, "metadata", label="Quiz metadata")
    if stage_metadata.get("stage") != "quiz":
        raise PublishSafetyError("Selected Quiz review does not declare the Quiz stage")
    if stage_metadata.get("upstream_extraction_status") != expected_upstream_status:
        raise PublishSafetyError("Quiz upstream extraction status does not match the KC lineage")
    status_code = stage_metadata.get("approval_status")
    if status_code != QUIZ_EXPERIMENTAL.code:
        raise PublishSafetyError(f"Unsupported Quiz publish status: {status_code!r}")
    quality_status = stage_metadata.get("quality_status")
    if quality_status != "experimental_unapproved":
        raise PublishSafetyError(f"Unsupported Quiz quality status: {quality_status!r}")
    metadata_kc = _nested_object(stage_metadata, "kc_set", label="Quiz KC set")
    if metadata_kc.get("sha256") != kc_sha256:
        raise PublishSafetyError("Quiz metadata is not connected to run-local kc-proposed.json")
    selected = stage_metadata.get("selected_kc_ids")
    questions = quiz.get("questions")
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(kc_id, str) for kc_id in selected)
        or len(set(selected)) != len(selected)
    ):
        raise PublishSafetyError("Quiz selected_kc_ids must be a non-empty unique list")
    if not set(selected) <= leaf_kc_ids:
        raise PublishSafetyError("Quiz selects KC ids outside the connected KC set")
    if not isinstance(questions, list) or not questions:
        raise PublishSafetyError("Quiz review must contain at least one question")
    question_kcs = {
        row.get("kc_id")
        for row in questions
        if isinstance(row, dict) and isinstance(row.get("kc_id"), str)
    }
    if not question_kcs <= set(selected):
        raise PublishSafetyError("Quiz questions reference KCs outside the selected KC set")
    metrics = _nested_object(payload, "metrics", label="Quiz metrics")
    selected_count = _positive_count(metrics.get("selected_kc_count"), "selected KC count")
    question_count = _positive_count(metrics.get("question_count"), "Quiz question count")
    if selected_count != len(selected) or question_count != len(questions):
        raise PublishSafetyError("Quiz review counts do not match its connected artifact")
    return QUIZ_EXPERIMENTAL, selected_count, question_count


def _derive_run_summary(
    run_dir: Path,
    metadata: SourceMetadata,
    extraction_state: ExtractionState,
    review_files: ReviewFiles,
) -> RunSummary:
    _parse_extraction_review(
        run_dir / review_files.extractor,
        run_dir,
        metadata,
        extraction_state.artifact_path,
    )
    recall, recall_upstream_status = _parse_kc_review(
        run_dir / review_files.kc_recall,
        run_dir,
        metadata,
        extraction_state,
        expected_scroll_mode=False,
    )
    scroll, scroll_upstream_status = _parse_kc_review(
        run_dir / review_files.kc_scroll,
        run_dir,
        metadata,
        extraction_state,
        expected_scroll_mode=True,
    )
    if recall_upstream_status != scroll_upstream_status:
        raise PublishSafetyError("KC recall and scroll reviews disagree on upstream provenance")
    kc_status, leaf_count, group_count, leaf_ids, kc_sha256 = _validate_kc_pair(
        recall,
        scroll,
        run_dir,
    )
    quiz_status, selected_count, question_count = _parse_quiz_review(
        run_dir / review_files.quiz,
        run_dir,
        metadata,
        kc_sha256,
        leaf_ids,
        recall_upstream_status,
    )
    return RunSummary(
        extraction_status=extraction_state.status,
        kc_upstream_extraction_status=recall_upstream_status,
        kc_status=kc_status,
        quiz_status=quiz_status,
        leaf_kc_count=leaf_count,
        kc_group_count=group_count,
        selected_kc_count=selected_count,
        question_count=question_count,
    )


def _copy_file(source: Path, destination: Path) -> None:
    _require_regular_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _copy_review_html(
    source: Path,
    destination: Path,
    *,
    run_dir: Path,
) -> None:
    """Copy a review page while removing machine-local paths from metadata."""

    content = _read_review_html(source, run_dir=run_dir)
    for pattern in LOCAL_PATH_PATTERNS:
        content = pattern.sub("[local-path-redacted]", content)
    required_scripts = (
        '<script src="review-config.js"></script>',
        '<script src="review-runtime.js"></script>',
    )
    missing_scripts = "".join(script for script in required_scripts if script not in content)
    if missing_scripts:
        if "</body>" not in content:
            raise PublishSafetyError(f"Review HTML is missing </body>: {source}")
        content = content.replace("</body>", f"{missing_scripts}</body>", 1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def _render_review_config(
    destination: Path,
    *,
    metadata: SourceMetadata,
    run_name: str,
    backend: ReviewBackendConfig | None,
) -> None:
    payload: dict[str, object] = {
        "schemaVersion": "learning-authoring-shared-review.v1",
        "enabled": backend is not None,
        "runId": run_name,
        "sourceId": metadata.source_id,
        "sourceFilename": metadata.filename,
    }
    if backend is not None:
        payload.update(
            {
                "supabaseUrl": backend.supabase_url,
                "supabasePublishableKey": backend.supabase_publishable_key,
            }
        )
    destination.write_text(
        "window.LEARNING_AUTHORING_REVIEW="
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )


def _render_vercel_config(
    source: Path,
    destination: Path,
    *,
    backend: ReviewBackendConfig | None,
) -> None:
    _require_regular_file(source)
    content = source.read_text(encoding="utf-8")
    marker = "{{REVIEW_CONNECT_SRC}}"
    if marker not in content:
        raise PublishSafetyError(f"Vercel template is missing {marker}")
    connect_src = f"{backend.supabase_url}" if backend is not None else "'none'"
    content = content.replace(marker, connect_src)
    try:
        json.loads(content)
    except json.JSONDecodeError as exc:
        raise PublishSafetyError("Rendered Vercel configuration is invalid JSON") from exc
    destination.write_text(content, encoding="utf-8")


def _portal_replacements(
    *,
    metadata: SourceMetadata,
    summary: RunSummary,
    run_name: str,
    artifacts: tuple[ReviewArtifact, ...],
    include_learning: bool = False,
) -> dict[str, str]:
    entrypoints = {artifact.entrypoint: artifact.source_name for artifact in artifacts}
    statuses = {
        "EXTRACTOR": summary.extraction_status,
        "KC": summary.kc_status,
        "QUIZ": summary.quiz_status,
        "MASTERY": LEARNING_MVP if include_learning else LEARNING_DISABLED,
    }
    replacements = {
        "{{SOURCE_FILENAME}}": metadata.filename,
        "{{SOURCE_RUN}}": run_name,
        "{{PAGE_COUNT}}": str(metadata.page_count),
        "{{REVIEW_VIEW_COUNT}}": str(len(artifacts)),
        "{{WORKFLOW_STAGE_COUNT}}": str(
            len({artifact.stage for artifact in artifacts}) + int(include_learning)
        ),
        "{{KC_UPSTREAM_EXTRACTION_STATUS}}": summary.kc_upstream_extraction_status,
        "{{LEAF_KC_COUNT}}": str(summary.leaf_kc_count),
        "{{KC_GROUP_COUNT}}": str(summary.kc_group_count),
        "{{SELECTED_KC_COUNT}}": str(summary.selected_kc_count),
        "{{QUESTION_COUNT}}": str(summary.question_count),
        "{{EXTRACTOR_HREF}}": f"{entrypoints['extractor']}#1",
        "{{KC_RECALL_HREF}}": f"{entrypoints['kc_recall']}#1",
        "{{KC_SCROLL_HREF}}": f"{entrypoints['kc_scroll']}#1",
        "{{QUIZ_HREF}}": entrypoints["quiz_experiment"],
        "{{LEARNING_HREF}}": "learning.html",
    }
    for key, status in statuses.items():
        replacements[f"{{{{{key}_STATUS_CLASS}}}}"] = status.css_class
        replacements[f"{{{{{key}_STATUS_LABEL}}}}"] = status.label
        replacements[f"{{{{{key}_STATUS_DESCRIPTION}}}}"] = status.description
    return replacements


def _render_portal(
    source: Path,
    destination: Path,
    *,
    metadata: SourceMetadata,
    summary: RunSummary,
    run_name: str,
    artifacts: tuple[ReviewArtifact, ...],
    include_learning: bool = False,
) -> None:
    """Render the controlled portal template with escaped run metadata."""

    _require_regular_file(source)
    try:
        content = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PublishSafetyError(f"Portal template is not UTF-8: {source}") from exc
    for placeholder, value in _portal_replacements(
        metadata=metadata,
        summary=summary,
        run_name=run_name,
        artifacts=artifacts,
        include_learning=include_learning,
    ).items():
        if placeholder not in content:
            raise PublishSafetyError(f"Portal template is missing {placeholder}")
        content = content.replace(placeholder, html.escape(value, quote=True))
    # Only controlled template blocks are conditional; course text never becomes HTML.
    for block, enabled in (
        ("LEARNING_ENABLED", include_learning), ("LEARNING_DISABLED", not include_learning),
    ):
        pattern = rf"<!-- {block} -->(.*?)<!-- /{block} -->"
        content = re.sub(pattern, r"\1" if enabled else "", content, flags=re.S)
    unresolved = sorted(set(PORTAL_PLACEHOLDER_PATTERN.findall(content)))
    if unresolved:
        raise PublishSafetyError(f"Unresolved portal placeholders: {unresolved}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def _relative_files(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise PublishSafetyError(f"Symlinks are not allowed in showcase output: {path}")
        if path.is_file():
            files.add(path.relative_to(root).as_posix())
    return files


def _audit_names(root: Path) -> None:
    for relative in _relative_files(root):
        lowered = relative.lower()
        for forbidden in FORBIDDEN_NAME_PARTS:
            if forbidden in lowered:
                raise PublishSafetyError(
                    f"Forbidden artifact name found in showcase: {relative} ({forbidden})"
                )


def _audit_secrets(root: Path) -> None:
    for relative in sorted(_relative_files(root)):
        content = (root / relative).read_bytes()
        for marker in FORBIDDEN_CONTENT_MARKERS:
            if marker in content:
                raise PublishSafetyError(
                    f"Machine-local absolute path found in publish file: {relative}"
                )
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                raise PublishSafetyError(f"Potential {label} found in publish file: {relative}")


def _record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _expected_paths(
    page_images: tuple[PageImage, ...],
    artifacts: tuple[ReviewArtifact, ...],
    *,
    include_learning: bool = False,
) -> set[str]:
    paths = set(TEMPLATE_FILES)
    paths.update(artifact.source_name for artifact in artifacts)
    paths.update(image.image_ref for image in page_images)
    if include_learning:
        paths.update(LEARNING_TEMPLATE_FILES + LEARNING_GENERATED_FILES)
    return paths


def _verify_managed_output(output_dir: Path) -> None:
    """Refuse to replace a directory not created solely by this builder."""

    if output_dir.is_symlink() or not output_dir.is_dir():
        raise PublishSafetyError(f"Refusing to replace unmanaged output: {output_dir}")
    manifest_path = output_dir / MANIFEST_NAME
    _require_regular_file(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PublishSafetyError(f"Invalid managed-output manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict) or manifest.get("managed_by") not in {
        MANAGED_BY,
        LEGACY_MANAGED_BY,
    }:
        raise PublishSafetyError(f"Refusing to replace unmanaged output: {output_dir}")
    entries = manifest.get("files")
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("path"), str)
        or not isinstance(entry.get("bytes"), int)
        or not isinstance(entry.get("sha256"), str)
        for entry in entries
    ):
        raise PublishSafetyError(f"Invalid managed-output file allowlist: {manifest_path}")
    recorded = {entry["path"] for entry in entries}
    expected = recorded | {MANIFEST_NAME}
    actual = _relative_files(output_dir)
    if actual != expected:
        unexpected = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise PublishSafetyError(
            "Refusing to replace modified showcase output; "
            f"unexpected={unexpected}, missing={missing}"
        )
    for entry in entries:
        path = output_dir / entry["path"]
        if path.stat().st_size != entry["bytes"] or _sha256(path) != entry["sha256"]:
            raise PublishSafetyError(
                f"Refusing to replace modified showcase output: {entry['path']}"
            )


def build_showcase(
    run_dir: Path,
    output_dir: Path,
    *,
    template_dir: Path | None = None,
    review_files: ReviewFiles = DEFAULT_REVIEW_FILES,
    review_backend: ReviewBackendConfig | None = None,
    include_learning: bool = False,
) -> dict[str, object]:
    """Create a connected static portal from exact allowlisted inputs."""

    run_dir_input = _absolute_without_resolving(run_dir)
    _reject_symlink_components(run_dir_input, "run directory")
    if not run_dir_input.is_dir():
        raise PublishSafetyError(f"Run directory does not exist: {run_dir_input}")
    run_dir = run_dir_input.resolve(strict=True)
    for stage in ("kc", "quiz"):
        require_current_revision(run_dir, stage)
    output_dir = output_dir.resolve()
    template_dir = (template_dir or DEFAULT_TEMPLATE_DIR).resolve()
    review_backend = _validated_review_backend(review_backend)
    if output_dir in {run_dir, template_dir}:
        raise PublishSafetyError("Output directory cannot replace an input directory")
    if output_dir.parent == output_dir:
        raise PublishSafetyError("Output directory cannot be a filesystem root")

    metadata = _load_source_metadata(run_dir)
    artifacts = _review_artifacts(review_files)
    extraction_state = _derive_extraction_state(run_dir, metadata)
    summary = _derive_run_summary(run_dir, metadata, extraction_state, review_files)
    learning_package = None
    if include_learning:
        from learning_authoring.product.learning import build_learning_package

        learning_package = build_learning_package(run_dir, review_files=review_files)
    expected_paths = _expected_paths(
        metadata.page_images, artifacts, include_learning=include_learning,
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}-staging-", dir=output_dir.parent)
    )

    try:
        _render_portal(
            template_dir / "index.html",
            staging_dir / "index.html",
            metadata=metadata,
            summary=summary,
            run_name=run_dir.name,
            artifacts=artifacts,
            include_learning=include_learning,
        )
        _render_vercel_config(
            template_dir / "vercel.json",
            staging_dir / "vercel.json",
            backend=review_backend,
        )
        _copy_file(template_dir / "robots.txt", staging_dir / "robots.txt")
        runtime_source = template_dir / "review-runtime.js"
        if not runtime_source.is_file():
            runtime_source = DEFAULT_TEMPLATE_DIR / "review-runtime.js"
        _copy_file(runtime_source, staging_dir / "review-runtime.js")
        _render_review_config(
            staging_dir / "review-config.js",
            metadata=metadata,
            run_name=run_dir.name,
            backend=review_backend,
        )
        if learning_package is not None:
            from learning_authoring.product.learning import write_learning_data

            for name in LEARNING_TEMPLATE_FILES:
                _copy_file(DEFAULT_TEMPLATE_DIR / name, staging_dir / name)
            write_learning_data(learning_package, staging_dir / "learning-data.js")
        for artifact in artifacts:
            _copy_review_html(
                run_dir / artifact.source_name,
                staging_dir / artifact.source_name,
                run_dir=run_dir,
            )
        for image in metadata.page_images:
            relative = Path(*PurePosixPath(image.image_ref).parts)
            source = _require_run_file(run_dir, relative, f"page {image.page} image")
            if _sha256(source) != image.image_sha256:
                raise PublishSafetyError(
                    f"Page image hash mismatch for page {image.page}: {image.image_ref}"
                )
            _copy_file(source, staging_dir / relative)

        actual_before_manifest = _relative_files(staging_dir)
        if actual_before_manifest != expected_paths:
            raise PublishSafetyError(
                "Allowlist mismatch before manifest generation: "
                f"expected={sorted(expected_paths)}, actual={sorted(actual_before_manifest)}"
            )

        file_records = [
            _record(staging_dir / relative, staging_dir)
            for relative in sorted(actual_before_manifest)
        ]
        stage_status = {
            "extractor": summary.extraction_status.code,
            "kc": summary.kc_status.code,
            "quiz": summary.quiz_status.code,
            "mastery": (LEARNING_MVP if include_learning else LEARNING_DISABLED).code,
        }
        from learning_authoring.quiz_review_state import load_quiz_semantic_state

        semantic_state = load_quiz_semantic_state(run_dir)
        manifest: dict[str, object] = {
            "schema_version": "learning-authoring-showcase.v4",
            "managed_by": MANAGED_BY,
            "source_run": run_dir.name,
            "source": {
                "filename": metadata.filename,
                "source_id": metadata.source_id,
                "page_count": metadata.page_count,
            },
            "page_count": metadata.page_count,
            "page_images": [
                {
                    "page": image.page,
                    "path": image.image_ref,
                    "sha256": image.image_sha256,
                }
                for image in metadata.page_images
            ],
            "review_view_count": len(artifacts),
            "counts": {
                "pages": metadata.page_count,
                "review_views": len(artifacts),
                "leaf_kcs": summary.leaf_kc_count,
                "kc_groups": summary.kc_group_count,
                "selected_kcs": summary.selected_kc_count,
                "quiz_questions": summary.question_count,
            },
            "lineage": {
                "extraction_to_kc": "VERIFIED",
                "kc_upstream_extraction_status": summary.kc_upstream_extraction_status,
                "kc_to_quiz": "VERIFIED",
            },
            "stage_status": stage_status,
            "quiz_initial_check": {
                "status": semantic_state["status"],
                "counts": semantic_state.get("counts", {}),
                "human_approved": False,
                "scope": "selected_quiz_and_cited_sources_not_course_certification",
            },
            "shared_review": {
                "enabled": review_backend is not None,
                "provider": "supabase" if review_backend is not None else None,
                "identity": "anonymous_auth_with_display_name"
                if review_backend is not None
                else None,
                "raw_artifacts_mutable": False,
            },
            "entrypoints": {"portal": "index.html"}
            | {artifact.entrypoint: artifact.source_name for artifact in artifacts}
            | ({"learning": "learning.html"} if include_learning else {}),
            "learning": {
                "enabled": include_learning,
                "mode": ("shared" if review_backend else "local_only")
                if include_learning else None,
                "policy_version": learning_package["versions"]["policy_version"]
                if learning_package else None,
                "model_provider_calls": 0,
                "calibrated_mastery": False,
                "feedback_changes_grades": False,
            },
            "files": file_records,
        }
        (staging_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        expected_with_manifest = expected_paths | {MANIFEST_NAME}
        actual_with_manifest = _relative_files(staging_dir)
        if actual_with_manifest != expected_with_manifest:
            raise PublishSafetyError("Unexpected files appeared during showcase build")
        _audit_names(staging_dir)
        _audit_secrets(staging_dir)

        if output_dir.exists():
            _verify_managed_output(output_dir)
            shutil.rmtree(output_dir)
        os.replace(staging_dir, output_dir)
        return manifest
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise
