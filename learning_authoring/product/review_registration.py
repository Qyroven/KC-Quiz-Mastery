"""Offline, insert-only shared-review registration for one generated run.

This module never connects to Supabase, reads keys, or changes an authoring run.
Node.js is required for the browser's exact JSON number/string/sort semantics;
Python's ``json.dumps(sort_keys=True)`` is not an equivalent baseline hash.
Export the SQL outside the run and static portal, then have an authorized
administrator apply it. An existing run ID deliberately aborts the transaction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from learning_authoring.product.showcase import (
    DEFAULT_REVIEW_FILES,
    DEFAULT_TEMPLATE_DIR,
    PublishSafetyError,
    ReviewFiles,
    _absolute_without_resolving,
    _derive_extraction_state,
    _derive_run_summary,
    _json_assignment,
    _load_source_metadata,
    _read_json_object,
    _read_review_html,
    _reject_symlink_components,
    _require_run_file,
    _review_artifacts,
    _sha256,
)

HASH_ALGORITHM = "review-runtime.canonical-json-sha256.v1"
# Kept byte-for-byte in sync with the packaged renderer, checked before use.
_CANONICAL_JS = (
    "  function canonical(value) {\n"
    '    if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;\n'
    '    if (value && typeof value === "object") {\n'
    "      return `{${Object.keys(value).sort().map(key => "
    '`${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;\n'
    "    }\n"
    "    return JSON.stringify(value);\n"
    "  }"
)
# A target-adapter change needs an explicit exporter/parity-test review as well.
_TARGET_ADAPTER_SHA256 = "ae7b45895dce675cf71d5a76fc38ca633ccfbbb15b483d1710425a543ea6b8af"
_NODE_HASH_SCRIPT = (
    '"use strict";\n'
    + _CANONICAL_JS
    + """
const fs = require('node:fs'), crypto = require('node:crypto');
const values = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(values.map(value =>
  crypto.createHash('sha256').update(canonical(value), 'utf8').digest('hex'))));
"""
)


class RegistrationSafetyError(PublishSafetyError):
    """The generated baselines cannot be registered safely."""


@dataclass(frozen=True)
class ReviewTarget:
    stage: str
    item_type: str
    item_key: str
    identity_field: str
    identity_value: str | int
    base_artifact_sha256: str


@dataclass(frozen=True)
class ReviewRegistration:
    run_id: str
    source_id: str
    source_filename: str
    source_sha256: str
    targets: tuple[ReviewTarget, ...]
    artifact_sha256: tuple[tuple[str, str], ...]
    renderer_sha256: str

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for target in self.targets:
            key = f"{target.stage}/{target.item_type}"
            counts[key] = counts.get(key, 0) + 1
        return counts


def _renderer_contract(runtime_path: Path | None) -> tuple[Path, str]:
    path = _absolute_without_resolving(runtime_path or DEFAULT_TEMPLATE_DIR / "review-runtime.js")
    _reject_symlink_components(path, "review runtime")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise RegistrationSafetyError("Cannot read the review renderer") from exc
    if text.count(_CANONICAL_JS) != 1:
        raise RegistrationSafetyError(
            "Renderer canonical hash changed; update and test the exporter"
        )
    adapter = re.search(r"  function detectAdapter\(\) \{.*?\n  \}", text, re.S)
    if adapter is None or hashlib.sha256(adapter[0].encode()).hexdigest() != _TARGET_ADAPTER_SHA256:
        raise RegistrationSafetyError(
            "Renderer target adapters changed; update and test the exporter"
        )
    return path, hashlib.sha256(raw).hexdigest()


def _payload_hashes(values: list[object], node_executable: str | Path | None) -> list[str]:
    node = str(node_executable) if node_executable is not None else shutil.which("node")
    if not node:
        raise RegistrationSafetyError("Node.js is required for exact renderer baseline hashes")
    try:
        raw = json.dumps(values, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RegistrationSafetyError("Baseline contains non-JSON or non-finite values") from exc
    try:
        result = subprocess.run(
            [node, "--input-type=commonjs", "-e", _NODE_HASH_SCRIPT],
            input=raw,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            timeout=30,
            # Do not inherit NODE_OPTIONS, preload hooks, or credential variables.
            env={"PATH": os.environ.get("PATH", "")},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RegistrationSafetyError("Local Node baseline hashing failed") from exc
    try:
        hashes = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RegistrationSafetyError("Local Node baseline hashing failed") from exc
    if result.returncode or not isinstance(hashes, list) or len(hashes) != len(values):
        raise RegistrationSafetyError("Local Node baseline hashing returned invalid results")
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes
    ):
        raise RegistrationSafetyError("Local Node baseline hashing returned invalid digests")
    return hashes


def renderer_payload_sha256(
    payload: object,
    *,
    node_executable: str | Path | None = None,
    runtime_path: Path | None = None,
) -> str:
    """Hash a parsed JSON payload exactly as the supported browser renderer does."""
    _renderer_contract(runtime_path)
    return _payload_hashes([payload], node_executable)[0]


def _objects(value: object, label: str) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise RegistrationSafetyError(f"{label} must be an array of objects")
    return value


def _page_number(value: object, page_count: int) -> int:
    if type(value) is not int or not 1 <= value <= page_count:
        raise RegistrationSafetyError("Invalid or out-of-range review page identity")
    return value


def _sql_text(value: str) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise RegistrationSafetyError("SQL text must be a string without NUL")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise RegistrationSafetyError("SQL text contains an invalid Unicode surrogate") from exc
    return "E'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _check_inline_json(value: object) -> None:
    # Extraction/KC use JS object literals, where __proto__ is not an own key.
    # Refuse that exceptional shape rather than silently hash different data.
    if isinstance(value, dict):
        if "__proto__" in value:
            raise RegistrationSafetyError("Inline review JSON contains unsupported __proto__ key")
        for child in value.values():
            _check_inline_json(child)
    elif isinstance(value, list):
        for child in value:
            _check_inline_json(child)


def prepare_review_registration(
    run_dir: Path,
    *,
    review_files: ReviewFiles = DEFAULT_REVIEW_FILES,
    node_executable: str | Path | None = None,
    runtime_path: Path | None = None,
) -> ReviewRegistration:
    """Validate current review artifacts and prepare immutable registration rows.

    The run ID is the directory basename, exactly like portal-build. No override
    can accidentally attach new output to an old review namespace. With custom
    portal assets, pass the exact generated ``review-runtime.js`` as runtime_path.
    No generated HTML or course content is executed; only JSON is decoded.
    """
    root = _absolute_without_resolving(run_dir)
    _reject_symlink_components(root, "run directory")
    if not root.is_dir():
        raise RegistrationSafetyError("Run directory does not exist")
    artifacts = _review_artifacts(review_files)
    runtime, runtime_hash = _renderer_contract(runtime_path)
    metadata = _load_source_metadata(root)
    extraction_state = _derive_extraction_state(root, metadata)
    names = [
        "source-manifest.json",
        extraction_state.artifact_path.name,
        "kc-proposed.json",
        "kc-generation-metadata.json",
        "kc-run-metrics.json",
        *(
            f"quiz/{name}"
            for name in (
                "quiz-proposed.json",
                "quiz-input.json",
                "quiz-generation-metadata.json",
                "quiz-run-metrics.json",
                "quiz-form-audit.json",
            )
        ),
        *(artifact.source_name for artifact in artifacts),
    ]
    names.extend(
        name
        for name in (
            "extracted-source.proposed.json",
            "extracted-source.approved.json",
            "extraction-approval.json",
            "authoring-context.json",
        )
        if (root / name).exists()
    )
    paths = {name: _require_run_file(root, name, "registration input") for name in names}
    before = {name: _sha256(path) for name, path in paths.items()}
    # Reuse the same source/approval/context/candidate lineage gate as publication.
    _derive_run_summary(root, metadata, extraction_state, review_files)
    contents = {
        item.source_name: _read_review_html(root / item.source_name, run_dir=root)
        for item in artifacts
    }
    extraction = _json_assignment(contents[review_files.extractor], "const source=", "Extraction")
    recall = _json_assignment(contents[review_files.kc_recall], "const DATA=", "KC recall")
    scroll = _json_assignment(contents[review_files.kc_scroll], "const DATA=", "KC scroll")
    quiz = _json_assignment(
        contents[review_files.quiz], '<script id="payload" type="application/json">', "Quiz"
    )
    for value in (extraction, recall, scroll):
        _check_inline_json(value)
    proposal = recall["candidate"]["proposed"]
    pages = _objects(extraction.get("pages"), "Extraction pages")
    page_numbers = [_page_number(page.get("page_number"), metadata.page_count) for page in pages]
    if page_numbers != list(range(1, metadata.page_count + 1)):
        raise RegistrationSafetyError("Extraction pages must have unique, ordered identities")
    kcs = _objects(proposal.get("leaf_kcs"), "Leaf KCs")
    audits = _objects(proposal.get("page_audit"), "KC page audits")
    audit_pages = [_page_number(row.get("page"), metadata.page_count) for row in audits]
    if sorted(audit_pages) != page_numbers:
        raise RegistrationSafetyError("KC page audits must cover every page exactly once")
    evidence_pages: set[int] = set()
    for kc in kcs:
        evidence = _objects(kc.get("source_evidence"), "KC source evidence")
        context = _objects(kc.get("context_evidence", []), "KC context evidence")
        if not evidence and not context:
            raise RegistrationSafetyError("A KC without evidence is unreachable in the review UI")
        evidence_pages.update(
            _page_number(row.get("page"), metadata.page_count) for row in evidence
        )
    questions = _objects(quiz["quiz"].get("questions"), "Quiz questions")
    rows: list[tuple[str, str, str, str, str | int, dict]] = []
    for page in pages:
        number = page["page_number"]
        rows.append(("extraction", "page", f"page:{number:04d}", "page_number", number, page))
    rows.extend(("kc", "leaf_kc", kc.get("kc_id"), "kc_id", kc.get("kc_id"), kc) for kc in kcs)
    rows.extend(
        ("kc", "page_audit", f"page:{row['page']:04d}", "page", row["page"], row)
        for row in audits
        if row["page"] not in evidence_pages
    )
    rows.extend(
        ("quiz", "question", q.get("question_id"), "question_id", q.get("question_id"), q)
        for q in questions
    )
    identities = set()
    for stage, kind, key, _, identity, _ in rows:
        if not isinstance(key, str) or not 1 <= len(key) <= 160 or not key.strip():
            raise RegistrationSafetyError("Review target identity must contain 1–160 characters")
        _sql_text(key)
        if not isinstance(identity, (str, int)) or isinstance(identity, bool):
            raise RegistrationSafetyError("Invalid review target identity value")
        if (stage, kind, key) in identities:
            raise RegistrationSafetyError("Duplicate review target identity")
        identities.add((stage, kind, key))

    # Exact browser hashes also catch Python's True == 1 equality ambiguity, and
    # ensure the UI's upstream-staleness checks see the registered KC/page copies.
    checks = [
        (extraction, _read_json_object(extraction_state.artifact_path, "Extraction")),
        (extraction, recall.get("source")),
        (extraction, scroll.get("source")),
        (proposal, scroll["candidate"]["proposed"]),
        (proposal, _read_json_object(paths["kc-proposed.json"], "KC")),
        (quiz["quiz"], _read_json_object(paths["quiz/quiz-proposed.json"], "Quiz")),
    ]
    kc_by_id = {kc["kc_id"]: kc for kc in kcs}
    input_kcs = _objects(quiz["input"].get("leaf_kcs"), "Quiz input KCs")
    input_ids = [kc.get("kc_id") for kc in input_kcs]
    selected_ids = quiz["metadata"]["selected_kc_ids"]
    if any(not isinstance(key, str) for key in input_ids) or sorted(input_ids) != sorted(
        selected_ids
    ):
        raise RegistrationSafetyError("Quiz input KCs do not match its selected KC identities")
    for selected in input_kcs:
        checks.append((selected, kc_by_id.get(selected.get("kc_id"))))
    values = [row[-1] for row in rows] + [value for pair in checks for value in pair]
    hashes = _payload_hashes(values, node_executable)
    for index in range(len(checks)):
        offset = len(rows) + index * 2
        if hashes[offset] != hashes[offset + 1]:
            raise RegistrationSafetyError(
                "Review baseline or upstream copy differs from its artifact"
            )
    if (
        before != {name: _sha256(path) for name, path in paths.items()}
        or _sha256(runtime) != runtime_hash
    ):
        raise RegistrationSafetyError(
            "Registration inputs changed during export; retry on a frozen run"
        )
    return ReviewRegistration(
        run_id=root.name,
        source_id=metadata.source_id,
        source_filename=metadata.filename,
        source_sha256=metadata.source_sha256,
        targets=tuple(
            ReviewTarget(*row[:-1], digest)
            for row, digest in zip(rows, hashes[: len(rows)], strict=True)
        ),
        artifact_sha256=tuple(sorted(before.items())),
        renderer_sha256=runtime_hash,
    )


def registration_sql(
    registration: ReviewRegistration,
    *,
    is_public: bool = False,
    review_open: bool = False,
) -> str:
    """Render one atomic INSERT-only transaction, closed/private by default."""
    if type(is_public) is not bool or type(review_open) is not bool:
        raise RegistrationSafetyError("Run visibility and review-open settings must be booleans")
    if review_open and not is_public:
        raise RegistrationSafetyError("An open review run must also be public")
    metadata = json.dumps(
        {
            "schema_version": "learning-authoring-review-registration.v1",
            "baseline_hash_algorithm": HASH_ALGORITHM,
            "renderer_sha256": registration.renderer_sha256,
            "artifact_sha256": dict(registration.artifact_sha256),
            "counts": registration.counts,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    run_values = [
        _sql_text(registration.run_id),
        _sql_text(registration.source_id),
        _sql_text(registration.source_filename),
        _sql_text(registration.source_sha256),
        str(is_public).lower(),
        str(review_open).lower(),
        _sql_text(metadata) + "::jsonb",
    ]
    values = []
    for target in registration.targets:
        fields = [
            registration.run_id,
            target.stage,
            target.item_type,
            target.item_key,
            target.identity_field,
        ]
        identity = json.dumps(target.identity_value, ensure_ascii=False, allow_nan=False)
        values.append(
            "  ("
            + ", ".join(
                [
                    *map(_sql_text, fields),
                    _sql_text(identity) + "::jsonb",
                    _sql_text(target.base_artifact_sha256),
                ]
            )
            + ")"
        )
    if not values:
        raise RegistrationSafetyError("Cannot register a run with no review targets")
    return (
        "-- Offline registration; no credentials or review events.\n"
        "-- A duplicate run ID aborts this transaction; never rebase existing history.\n"
        "BEGIN;\n"
        "INSERT INTO public.review_runs\n"
        "  (id, source_id, source_filename, source_sha256, is_public, review_open, metadata)\n"
        "VALUES (" + ", ".join(run_values) + ");\n"
        "INSERT INTO public.review_targets\n"
        "  (run_id, stage, item_type, item_key, identity_field, "
        "identity_value, base_artifact_sha256)\n"
        "VALUES\n" + ",\n".join(values) + ";\nCOMMIT;\n"
    )


def export_review_registration(
    run_dir: Path,
    output_path: Path,
    *,
    review_files: ReviewFiles = DEFAULT_REVIEW_FILES,
    node_executable: str | Path | None = None,
    runtime_path: Path | None = None,
    is_public: bool = False,
    review_open: bool = False,
) -> ReviewRegistration:
    """Write SQL to a NEW external path; never overwrite a file or touch the run."""
    output = _absolute_without_resolving(output_path)
    _reject_symlink_components(output, "registration output")
    if output.is_relative_to(_absolute_without_resolving(run_dir)):
        raise RegistrationSafetyError("Registration SQL must be outside the immutable run")
    if any((parent / "showcase-manifest.json").is_file() for parent in output.parents):
        raise RegistrationSafetyError("Registration SQL must not be included in a static portal")
    if output.exists():
        raise RegistrationSafetyError("Registration output already exists; choose a new path")
    registration = prepare_review_registration(
        run_dir,
        review_files=review_files,
        node_executable=node_executable,
        runtime_path=runtime_path,
    )
    sql = registration_sql(registration, is_public=is_public, review_open=review_open)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(sql)
    return registration


def main(argv: list[str] | None = None) -> int:
    """Standalone offline exporter; deliberately has no apply/DB/credential flags."""
    parser = argparse.ArgumentParser(
        description="Export NEW insert-only shared-review SQL offline."
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("output_path", type=Path, help="New SQL file outside the run and portal")
    parser.add_argument("--runtime-path", type=Path, help="Exact generated review-runtime.js")
    parser.add_argument("--node", dest="node_executable", help="Local Node.js executable")
    parser.add_argument("--public", dest="is_public", action="store_true")
    parser.add_argument("--review-open", action="store_true")
    args = parser.parse_args(argv)
    try:
        registration = export_review_registration(**vars(args))
    except (PublishSafetyError, OSError) as exc:
        parser.exit(2, f"Registration not exported: {exc}\n")
    print(
        json.dumps(
            {
                "run_id": registration.run_id,
                "target_count": len(registration.targets),
                "counts": registration.counts,
                "sql_sha256": _sha256(args.output_path),
                "backend_writes": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
