#!/usr/bin/env python3
"""Build a static, allowlisted showcase from one local authoring run.

This script deliberately copies review HTML and rendered page images only. It never
copies run directories wholesale, provider responses, prompt packages, request
previews, raw model output, environment files, or credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = REPOSITORY_ROOT / "runs" / "day16-track1-pm-prompt-rerun"
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "showcase-dist"
DEFAULT_TEMPLATE_DIR = REPOSITORY_ROOT / "showcase"
MANAGED_BY = "scripts/publish_showcase.py"
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
    """Exact run-local review filenames selected for one showcase."""

    extractor: str = "extraction-review.html"
    kc_recall: str = "kc-recall.html"
    kc_scroll: str = "kc-scroll.html"
    quiz: str = "quiz-review.html"


@dataclass(frozen=True)
class SourceMetadata:
    filename: str
    page_count: int
    source_id: str | None
    source_sha256: str | None


@dataclass(frozen=True)
class ExtractionStatus:
    code: str
    label: str
    css_class: str
    description: str


DEFAULT_REVIEW_FILES = ReviewFiles()

TEMPLATE_FILES = ("index.html", "vercel.json", "robots.txt")
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
    re.compile(r"(?:file://)?/(?:Users|var/folders|private/var|home)/[^\"'<>\r\n]+"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\[^\"'<>\r\n]+"),
)
FORBIDDEN_CONTENT_MARKERS = (
    b"/Users/",
    b"/var/folders/",
    b"/private/var/",
    b"file://",
    b"\\\\Users\\\\",
)
QUIZ_STATUS_BANNER = """
<aside id="showcase-quiz-status" role="status" aria-label="Quiz publish status"
  style="position:fixed;left:16px;bottom:16px;z-index:2147483647;max-width:440px;
  padding:12px 15px;border:1px solid #f1b75d;border-radius:12px;background:#fff7e8;
  color:#794900;box-shadow:0 12px 32px rgba(42,31,12,.18);font:600 13px/1.4
  -apple-system,BlinkMacSystemFont,'SF Pro Text',sans-serif">
  <strong style="display:block;color:#9b5700">Showcase · Quiz experimental / unapproved</strong>
  Contract và form checks không chứng minh semantic validity. Không dùng như output production.
</aside>
""".strip()
PORTAL_PLACEHOLDER_PATTERN = re.compile(r"\{\{[A-Z0-9_]+\}\}")
SAFE_REVIEW_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.html")
HUMAN_APPROVED = ExtractionStatus(
    code="HUMAN_APPROVED",
    label="HUMAN APPROVED",
    css_class="usable",
    description=(
        "Extraction đã vượt qua review và boundary phê duyệt của con người. "
        "Showcase chỉ công khai trạng thái; reviewer, note và approval metadata "
        "không được đóng gói."
    ),
)
PROPOSED = ExtractionStatus(
    code="PROPOSED",
    label="PROPOSED · REVIEW NEEDED",
    css_class="proposed",
    description=(
        "Extraction hiện là proposed output và chưa vượt qua boundary phê duyệt của con người."
    ),
)


def _review_artifacts(review_files: ReviewFiles) -> tuple[ReviewArtifact, ...]:
    artifacts = (
        ReviewArtifact("extractor", review_files.extractor, "extractor", "usable"),
        ReviewArtifact("kc_recall", review_files.kc_recall, "kc", "usable-proposed"),
        ReviewArtifact("kc_scroll", review_files.kc_scroll, "kc", "usable-proposed"),
        ReviewArtifact(
            "quiz_experiment",
            review_files.quiz,
            "quiz",
            "experimental-unapproved",
        ),
    )
    names = [artifact.source_name for artifact in artifacts]
    if len(names) != len(set(names)):
        raise PublishSafetyError("Review filenames must be distinct")
    for name in names:
        if not SAFE_REVIEW_FILENAME.fullmatch(name):
            raise PublishSafetyError(
                "Review selection must be one run-local HTML filename: " f"{name!r}"
            )
        lowered = name.lower()
        if any(forbidden in lowered for forbidden in FORBIDDEN_NAME_PARTS):
            raise PublishSafetyError(f"Forbidden review filename selected: {name}")
    return artifacts


def _load_source_metadata(run_dir: Path) -> SourceMetadata:
    manifest_path = run_dir / SOURCE_MANIFEST_NAME
    _require_regular_file(manifest_path)
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
    if (
        not isinstance(filename, str)
        or not filename.strip()
        or Path(filename).name != filename
    ):
        raise PublishSafetyError("source.filename must be one non-empty basename")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
        raise PublishSafetyError("source.page_count must be a positive integer")
    if source_id is not None and not isinstance(source_id, str):
        raise PublishSafetyError("source.source_id must be a string when present")
    if source_sha256 is not None and not isinstance(source_sha256, str):
        raise PublishSafetyError("source.sha256 must be a string when present")
    return SourceMetadata(
        filename=filename.strip(),
        page_count=page_count,
        source_id=source_id,
        source_sha256=source_sha256,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_file(path: Path) -> None:
    if path.is_symlink():
        raise PublishSafetyError(f"Symlink inputs are not allowed: {path}")
    if not path.is_file():
        raise PublishSafetyError(f"Required file is missing: {path}")


def _has_regular_file(path: Path) -> bool:
    if path.is_symlink():
        raise PublishSafetyError(f"Symlink inputs are not allowed: {path}")
    if path.exists() and not path.is_file():
        raise PublishSafetyError(f"Expected a regular file: {path}")
    return path.is_file()


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    _require_regular_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PublishSafetyError(f"Invalid {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise PublishSafetyError(f"{label} must contain a JSON object: {path}")
    return payload


def _derive_extraction_status(
    run_dir: Path,
    metadata: SourceMetadata,
) -> ExtractionStatus:
    """Verify the approval boundary without copying approval data to output."""

    proposed_path = run_dir / "extracted-source.proposed.json"
    approved_path = run_dir / "extracted-source.approved.json"
    approval_path = run_dir / "extraction-approval.json"
    proposed_exists = _has_regular_file(proposed_path)
    approved_exists = _has_regular_file(approved_path)
    approval_exists = _has_regular_file(approval_path)

    if approved_exists != approval_exists:
        raise PublishSafetyError(
            "Incomplete extraction approval boundary: both extraction-approval.json and "
            "extracted-source.approved.json are required"
        )

    if approved_exists and approval_exists:
        approval = _read_json_object(approval_path, "extraction approval")
        approved = _read_json_object(approved_path, "approved extraction")
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
        return HUMAN_APPROVED

    if proposed_exists:
        _read_json_object(proposed_path, "proposed extraction")
        return PROPOSED

    raise PublishSafetyError(
        "Run has neither a verified human-approved extraction nor a proposed extraction"
    )


def _require_inside(path: Path, parent: Path, label: str) -> None:
    resolved_path = path.resolve()
    resolved_parent = parent.resolve()
    try:
        resolved_path.relative_to(resolved_parent)
    except ValueError as exc:
        raise PublishSafetyError(f"{label} must be inside {resolved_parent}: {path}") from exc


def _copy_file(source: Path, destination: Path) -> None:
    _require_regular_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _copy_review_html(
    source: Path,
    destination: Path,
    artifact: ReviewArtifact,
) -> None:
    """Copy a review page while removing machine-local paths from embedded metadata."""

    _require_regular_file(source)
    try:
        content = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PublishSafetyError(f"Review HTML is not UTF-8: {source}") from exc
    for pattern in LOCAL_PATH_PATTERNS:
        content = pattern.sub("[local-path-redacted]", content)
    if artifact.stage == "quiz":
        body_match = re.search(r"<body(?:\s[^>]*)?>", content, flags=re.IGNORECASE)
        if body_match is None:
            raise PublishSafetyError(f"Quiz review has no body element: {source}")
        content = (
            content[: body_match.end()]
            + "\n"
            + QUIZ_STATUS_BANNER
            + content[body_match.end() :]
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def _render_portal(
    source: Path,
    destination: Path,
    *,
    metadata: SourceMetadata,
    extraction_status: ExtractionStatus,
    run_name: str,
    artifacts: tuple[ReviewArtifact, ...],
) -> None:
    """Render the controlled portal template with escaped run metadata."""

    _require_regular_file(source)
    try:
        content = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PublishSafetyError(f"Portal template is not UTF-8: {source}") from exc
    entrypoints = {artifact.entrypoint: artifact.source_name for artifact in artifacts}
    replacements = {
        "{{SOURCE_FILENAME}}": metadata.filename,
        "{{SOURCE_RUN}}": run_name,
        "{{PAGE_COUNT}}": str(metadata.page_count),
        "{{EXTRACTOR_STATUS_CLASS}}": extraction_status.css_class,
        "{{EXTRACTOR_STATUS_LABEL}}": extraction_status.label,
        "{{EXTRACTOR_STATUS_DESCRIPTION}}": extraction_status.description,
        "{{EXTRACTOR_HREF}}": f"{entrypoints['extractor']}#1",
        "{{KC_RECALL_HREF}}": f"{entrypoints['kc_recall']}#1",
        "{{KC_SCROLL_HREF}}": f"{entrypoints['kc_scroll']}#1",
        "{{QUIZ_HREF}}": entrypoints["quiz_experiment"],
    }
    for placeholder, value in replacements.items():
        if placeholder not in content:
            raise PublishSafetyError(f"Portal template is missing {placeholder}")
        content = content.replace(placeholder, html.escape(value, quote=True))
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
        path = root / relative
        content = path.read_bytes()
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
    page_count: int,
    artifacts: tuple[ReviewArtifact, ...],
) -> set[str]:
    paths = set(TEMPLATE_FILES)
    paths.update(artifact.source_name for artifact in artifacts)
    paths.update(f"pages/page-{page:04d}.png" for page in range(1, page_count + 1))
    return paths


def _verify_managed_output(output_dir: Path) -> None:
    """Refuse to replace a directory not created solely by this script."""

    if output_dir.is_symlink() or not output_dir.is_dir():
        raise PublishSafetyError(f"Refusing to replace unmanaged output: {output_dir}")
    manifest_path = output_dir / MANIFEST_NAME
    _require_regular_file(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PublishSafetyError(f"Invalid managed-output manifest: {manifest_path}") from exc
    if manifest.get("managed_by") != MANAGED_BY:
        raise PublishSafetyError(f"Refusing to replace unmanaged output: {output_dir}")
    recorded = {entry["path"] for entry in manifest.get("files", [])}
    expected = recorded | {MANIFEST_NAME}
    actual = _relative_files(output_dir)
    if actual != expected:
        unexpected = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise PublishSafetyError(
            "Refusing to replace modified showcase output; "
            f"unexpected={unexpected}, missing={missing}"
        )


def build_showcase(
    run_dir: Path,
    output_dir: Path,
    *,
    template_dir: Path = DEFAULT_TEMPLATE_DIR,
    review_files: ReviewFiles = DEFAULT_REVIEW_FILES,
) -> dict[str, object]:
    """Create a self-contained static package from exact allowlisted inputs."""

    run_dir = run_dir.resolve()
    output_dir = output_dir.resolve()
    template_dir = template_dir.resolve()
    if not run_dir.is_dir():
        raise PublishSafetyError(f"Run directory does not exist: {run_dir}")
    if output_dir in {run_dir, template_dir}:
        raise PublishSafetyError("Output directory cannot replace an input directory")
    if output_dir == REPOSITORY_ROOT.resolve():
        raise PublishSafetyError("Output directory cannot be the repository root")

    metadata = _load_source_metadata(run_dir)
    extraction_status = _derive_extraction_status(run_dir, metadata)
    artifacts = _review_artifacts(review_files)
    expected_paths = _expected_paths(metadata.page_count, artifacts)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}-staging-", dir=output_dir.parent)
    )

    try:
        _render_portal(
            template_dir / "index.html",
            staging_dir / "index.html",
            metadata=metadata,
            extraction_status=extraction_status,
            run_name=run_dir.name,
            artifacts=artifacts,
        )
        for name in ("vercel.json", "robots.txt"):
            _copy_file(template_dir / name, staging_dir / name)
        for artifact in artifacts:
            _copy_review_html(
                run_dir / artifact.source_name,
                staging_dir / artifact.source_name,
                artifact,
            )
        for page in range(1, metadata.page_count + 1):
            relative = Path("pages") / f"page-{page:04d}.png"
            _copy_file(run_dir / relative, staging_dir / relative)

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
        manifest: dict[str, object] = {
            "schema_version": "learning-authoring-showcase.v2",
            "managed_by": MANAGED_BY,
            "source_run": run_dir.name,
            "source": {
                "filename": metadata.filename,
                "source_id": metadata.source_id,
                "page_count": metadata.page_count,
            },
            "page_count": metadata.page_count,
            "stage_status": {
                "extractor": extraction_status.code,
                "kc": "PROPOSED",
                "quiz": "EXPERIMENTAL_UNAPPROVED",
                "mastery": "NOT_IMPLEMENTED",
            },
            "entrypoints": {"portal": "index.html"}
            | {artifact.entrypoint: artifact.source_name for artifact in artifacts},
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=DEFAULT_RUN_DIR,
        help=f"Source run (default: {DEFAULT_RUN_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Generated static package (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--extractor-review",
        default=DEFAULT_REVIEW_FILES.extractor,
        help="Exact run-local Extractor review HTML filename",
    )
    parser.add_argument(
        "--kc-recall-review",
        default=DEFAULT_REVIEW_FILES.kc_recall,
        help="Exact run-local KC recall review HTML filename",
    )
    parser.add_argument(
        "--kc-scroll-review",
        default=DEFAULT_REVIEW_FILES.kc_scroll,
        help="Exact run-local KC scroll review HTML filename",
    )
    parser.add_argument(
        "--quiz-review",
        default=DEFAULT_REVIEW_FILES.quiz,
        help=(
            "Exact run-local experimental Quiz review HTML filename; "
            "select pilots explicitly"
        ),
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    _require_inside(args.run_dir, REPOSITORY_ROOT / "runs", "run-dir")
    manifest = build_showcase(
        args.run_dir,
        args.output_dir,
        review_files=ReviewFiles(
            extractor=args.extractor_review,
            kc_recall=args.kc_recall_review,
            kc_scroll=args.kc_scroll_review,
            quiz=args.quiz_review,
        ),
    )
    total_bytes = sum(int(entry["bytes"]) for entry in manifest["files"])
    print(f"Built {args.output_dir.resolve()}")
    print(f"Files: {len(manifest['files']) + 1}; bytes: {total_bytes}")
    print("Security audit: passed (allowlist, names, symlinks, credential patterns)")
    print("Deployment: not performed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
