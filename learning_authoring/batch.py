"""Explicit, fail-safe multi-document extraction planning and execution."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from learning_authoring.artifacts import read_json, write_json
from learning_authoring.source import inspect_pdf

if TYPE_CHECKING:
    from learning_authoring.extractor import ExtractionConfig

BATCH_VERSION = "extraction-batch.v1"
DAY_PATTERN = re.compile(r"day[ _-]*0*([0-9]{1,2})", re.IGNORECASE)
EXPECTED_DAYS = tuple(range(1, 16))


def _day_number(filename: str) -> int | None:
    match = DAY_PATTERN.search(filename)
    if match is None:
        return None
    day = int(match.group(1))
    return day if day in EXPECTED_DAYS else None


def _pdf_metadata(path: Path) -> dict[str, Any]:
    metadata = inspect_pdf(path)
    return {
        key: metadata[key]
        for key in ("filename", "size_bytes", "sha256", "page_count")
    }


def create_batch_plan(source_dir: Path, manifest_path: Path, *, runs_dir: Path) -> dict[str, Any]:
    source = source_dir.expanduser().resolve()
    manifest = manifest_path.expanduser().resolve()
    runs = runs_dir.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"source directory does not exist: {source}")
    if manifest.exists():
        raise FileExistsError(f"batch manifest already exists: {manifest}")

    candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
    unassigned: list[str] = []
    for pdf_path in sorted(source.glob("*.pdf"), key=lambda item: item.name.casefold()):
        day = _day_number(pdf_path.name)
        if day is None:
            unassigned.append(pdf_path.name)
            continue
        candidates[day].append(_pdf_metadata(pdf_path))

    days: list[dict[str, Any]] = []
    for day in EXPECTED_DAYS:
        choices = candidates.get(day, [])
        days.append(
            {
                "day": day,
                "run_name": f"day-{day:02d}",
                "selected_pdf": choices[0]["filename"] if len(choices) == 1 else None,
                "candidates": choices,
            }
        )
    payload = {
        "batch_version": BATCH_VERSION,
        "name": "phase-1",
        "source_dir": os.path.relpath(source, manifest.parent),
        "runs_dir": os.path.relpath(runs, manifest.parent),
        "expected_days": list(EXPECTED_DAYS),
        "selection_status": (
            "ready" if all(day["selected_pdf"] is not None for day in days) else "incomplete"
        ),
        "days": days,
        "unassigned_files": unassigned,
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_json(manifest, payload)
    return payload


def _resolve_from_manifest(manifest_path: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = manifest_path.parent / candidate
    return candidate.resolve()


def preflight_batch(manifest_path: Path) -> dict[str, Any]:
    manifest = manifest_path.expanduser().resolve()
    payload = read_json(manifest)
    errors: list[str] = []
    if payload.get("batch_version") != BATCH_VERSION:
        errors.append(f"batch_version must be {BATCH_VERSION}")
    source_dir = _resolve_from_manifest(manifest, str(payload.get("source_dir", "")))
    runs_dir = _resolve_from_manifest(manifest, str(payload.get("runs_dir", "")))
    if not source_dir.is_dir():
        errors.append(f"source_dir does not exist: {source_dir}")

    documents: list[dict[str, Any]] = []
    days = payload.get("days")
    if not isinstance(days, list):
        errors.append("days must be a list")
        days = []
    seen_days: set[int] = set()
    seen_sources: set[Path] = set()
    seen_runs: set[str] = set()
    for row in days:
        if not isinstance(row, dict):
            errors.append("each days entry must be an object")
            continue
        day = row.get("day")
        selected = row.get("selected_pdf")
        run_name = row.get("run_name")
        if not isinstance(day, int) or day not in EXPECTED_DAYS:
            errors.append(f"invalid day: {day!r}")
            continue
        if day in seen_days:
            errors.append(f"duplicate day: {day}")
        seen_days.add(day)
        if not isinstance(selected, str) or not selected.strip():
            errors.append(f"day {day:02d} has no selected_pdf")
            continue
        if not isinstance(run_name, str) or Path(run_name).name != run_name or not run_name:
            errors.append(f"day {day:02d} has unsafe run_name")
            continue
        if run_name in seen_runs:
            errors.append(f"duplicate run_name: {run_name}")
        seen_runs.add(run_name)
        source_path = (source_dir / selected).resolve()
        if source_dir not in source_path.parents:
            errors.append(f"day {day:02d} selected_pdf escapes source_dir")
            continue
        if not source_path.is_file() or source_path.suffix.lower() != ".pdf":
            errors.append(f"day {day:02d} selected PDF does not exist: {selected}")
            continue
        if source_path in seen_sources:
            errors.append(f"PDF selected more than once: {selected}")
        seen_sources.add(source_path)
        try:
            metadata = _pdf_metadata(source_path)
        except Exception as exc:
            errors.append(f"day {day:02d} invalid PDF ({type(exc).__name__}): {selected}")
            continue
        documents.append(
            {
                "day": day,
                "source_pdf": str(source_path),
                "run_dir": str(runs_dir / run_name),
                **metadata,
            }
        )
    missing = sorted(set(EXPECTED_DAYS) - seen_days)
    extra = sorted(seen_days - set(EXPECTED_DAYS))
    if missing:
        errors.append(f"missing day entries: {missing}")
    if extra:
        errors.append(f"unexpected day entries: {extra}")
    documents.sort(key=lambda row: row["day"])
    return {
        "batch_version": BATCH_VERSION,
        "manifest": str(manifest),
        "ready": not errors and len(documents) == len(EXPECTED_DAYS),
        "errors": errors,
        "document_count": len(documents),
        "total_pages": sum(row["page_count"] for row in documents),
        "documents": documents,
    }


def run_batch(
    manifest_path: Path,
    *,
    config: ExtractionConfig,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    """Run selected documents sequentially with isolated resumable run directories."""

    from learning_authoring.extractor import run_extraction

    preflight = preflight_batch(manifest_path)
    if not preflight["ready"]:
        raise ValueError("batch preflight failed: " + "; ".join(preflight["errors"]))
    manifest = Path(preflight["manifest"])
    payload = read_json(manifest)
    runs_dir = _resolve_from_manifest(manifest, payload["runs_dir"])
    status_path = runs_dir / "batch-status.json"
    status: dict[str, Any] = {
        "batch_version": BATCH_VERSION,
        "manifest": str(manifest),
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "documents": [],
    }
    write_json(status_path, status)
    for document in preflight["documents"]:
        row = {
            "day": document["day"],
            "source_pdf": document["source_pdf"],
            "run_dir": document["run_dir"],
            "status": "running",
        }
        status["documents"].append(row)
        write_json(status_path, status)
        try:
            result = run_extraction(
                Path(document["source_pdf"]),
                Path(document["run_dir"]),
                config=config,
                progress=lambda message, day=document["day"]: print(
                    f"[day {day:02d}] {message}", flush=True
                ),
            )
            row.update(
                {
                    "status": "proposed",
                    "cached": result.cached,
                    "resumed": result.resumed,
                    "proposed_path": str(result.proposed_path),
                }
            )
        except Exception as exc:
            row.update(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            status["status"] = "failed"
            write_json(status_path, status)
            if not continue_on_error:
                raise
        write_json(status_path, status)
    failed = [row for row in status["documents"] if row["status"] == "failed"]
    status["status"] = "completed_with_failures" if failed else "proposed"
    status["completed_at"] = datetime.now(UTC).isoformat()
    write_json(status_path, status)
    return status
