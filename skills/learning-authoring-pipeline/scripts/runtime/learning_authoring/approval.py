"""Explicit human approval gate for proposed extraction artifacts."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from learning_authoring.artifacts import RunArtifacts, read_json, sha256_file, write_json
from learning_authoring.contracts import ExtractedSource, WarningRecord


def _review_warnings(extracted: ExtractedSource) -> list[WarningRecord]:
    warnings = [*extracted.warnings]
    warnings.extend(warning for page in extracted.pages for warning in page.warnings)
    return [
        warning
        for warning in warnings
        if warning.details.get("review_disposition", "review") == "review"
    ]


def approve_extraction(
    run_dir: Path,
    *,
    reviewer: str,
    note: str | None = None,
    acknowledge_warnings: bool = False,
) -> dict[str, Any]:
    """Validate and copy proposed output to the immutable consumer-facing name."""

    artifacts = RunArtifacts(run_dir.expanduser().resolve())
    if not reviewer.strip():
        raise ValueError("reviewer must not be empty")
    if not artifacts.review_html.is_file():
        raise RuntimeError("build and inspect extraction-review.html before approval")
    extracted = ExtractedSource.model_validate(read_json(artifacts.proposed))
    warnings = _review_warnings(extracted)
    if warnings and not acknowledge_warnings:
        raise RuntimeError(
            f"{len(warnings)} review warning(s) remain; pass acknowledge_warnings=True "
            "only after reviewing them"
        )
    if artifacts.approved.exists() or artifacts.approval.exists():
        raise FileExistsError("this run already has an approved artifact")
    temporary = artifacts.approved.with_name(f".{artifacts.approved.name}.tmp")
    shutil.copy2(artifacts.proposed, temporary)
    temporary.replace(artifacts.approved)
    if sha256_file(artifacts.approved) != sha256_file(artifacts.proposed):
        artifacts.approved.unlink(missing_ok=True)
        raise RuntimeError("approved artifact differs from proposed artifact")
    record = {
        "approval_version": "extraction-approval.v1",
        "status": "approved",
        "reviewer": reviewer.strip(),
        "note": note,
        "approved_at": datetime.now(UTC).isoformat(),
        "schema_version": extracted.schema_version,
        "source_sha256": extracted.source.sha256,
        "proposed_sha256": sha256_file(artifacts.proposed),
        "approved_sha256": sha256_file(artifacts.approved),
        "review_warning_count": len(warnings),
        "warnings_acknowledged": bool(warnings and acknowledge_warnings),
    }
    write_json(artifacts.approval, record)
    if artifacts.metrics.is_file():
        metrics = read_json(artifacts.metrics)
        metrics["approved"] = True
        metrics["approved_at"] = record["approved_at"]
        write_json(artifacts.metrics, metrics)
    return record
