"""Deterministic extraction diagnostics; never a semantic accuracy score."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from learning_authoring.contracts import ExtractedSource, source_region_geometry_state

AUDIT_VERSION = "extraction-audit.v5"
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def validate_extraction_geometry(extracted: ExtractedSource) -> None:
    """Reject malformed coordinates without treating unresolved geometry as an error.

    A block may honestly remain ``unresolved``.  A non-empty geometry object that
    lies outside the declared normalized coordinate system is different: it is a
    contradictory machine-readable claim and must not enter the canonical
    Extraction artifact.
    """

    invalid = [
        {"page": page.page_number, "block_id": block.block_id}
        for page in extracted.pages
        for block in page.blocks
        if source_region_geometry_state(block.region) == "invalid"
    ]
    if invalid:
        preview = ", ".join(
            f"page {row['page']} block {row['block_id']}" for row in invalid[:5]
        )
        suffix = f" (+{len(invalid) - 5} more)" if len(invalid) > 5 else ""
        raise ValueError(
            "Extraction contains invalid normalized geometry: " + preview + suffix
        )


def response_usage(raw: dict[str, Any]) -> dict[str, int]:
    usage = raw.get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}

    def integer(value: Any) -> int:
        return int(value) if isinstance(value, (int, float)) else 0

    return {
        "input_tokens": integer(usage.get("input_tokens")),
        "cached_input_tokens": integer(input_details.get("cached_tokens")),
        "output_tokens": integer(usage.get("output_tokens")),
        "reasoning_tokens": integer(output_details.get("reasoning_tokens")),
        "total_tokens": integer(usage.get("total_tokens")),
    }


def reported_cost(raw: dict[str, Any]) -> float | None:
    candidates = [
        raw.get("response_cost"),
        (raw.get("usage") or {}).get("cost"),
        (raw.get("_hidden_params") or {}).get("response_cost"),
    ]
    return next(
        (float(value) for value in candidates if isinstance(value, (int, float)) and value >= 0),
        None,
    )


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _strings(nested)


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(text)}


def build_audit(extracted: ExtractedSource, run_dir: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    missing_geometry_blocks: list[dict[str, Any]] = []
    unresolved_geometry_blocks: list[dict[str, Any]] = []
    invalid_geometry_blocks: list[dict[str, Any]] = []
    for page in extracted.pages:
        model_text = "\n".join(value for block in page.blocks for value in _strings(block.content))
        local_text = (run_dir / "text-audit" / f"page-{page.page_number:04d}.txt").read_text(
            encoding="utf-8"
        )
        local_tokens = _tokens(local_text)
        model_tokens = _tokens(model_text)
        overlap = (
            round(len(local_tokens & model_tokens) / len(local_tokens), 4) if local_tokens else None
        )
        state_by_block = {
            block.block_id: source_region_geometry_state(block.region) for block in page.blocks
        }
        page_unresolved_geometry = [
            block_id for block_id, state in state_by_block.items() if state == "unresolved"
        ]
        page_invalid_geometry = [
            block_id for block_id, state in state_by_block.items() if state == "invalid"
        ]
        page_missing_geometry = [
            block.block_id for block in page.blocks if state_by_block[block.block_id] != "located"
        ]
        page_missing_records = [
            {
                "page": page.page_number,
                "block_id": block.block_id,
                "kind": block.kind,
            }
            for block in page.blocks
            if state_by_block[block.block_id] != "located"
        ]
        missing_geometry_blocks.extend(page_missing_records)
        unresolved_geometry_blocks.extend(
            record
            for record in page_missing_records
            if state_by_block[record["block_id"]] == "unresolved"
        )
        invalid_geometry_blocks.extend(
            record
            for record in page_missing_records
            if state_by_block[record["block_id"]] == "invalid"
        )
        block_count = len(page.blocks)
        located_count = block_count - len(page_missing_geometry)
        rows.append(
            {
                "page": page.page_number,
                "role": page.role,
                "model_block_count": len(page.blocks),
                "model_content_chars": len(model_text.strip()),
                "local_text_layer_chars": len(local_text.strip()),
                "diagnostic_text_token_overlap": overlap,
                "located_block_count": located_count,
                "missing_geometry_block_ids": page_missing_geometry,
                "unresolved_geometry_block_ids": page_unresolved_geometry,
                "invalid_geometry_block_ids": page_invalid_geometry,
                "geometry_coverage": round(located_count / block_count, 4) if block_count else 1.0,
                "note": "Review aid only; not accuracy, confidence, or completeness.",
            }
        )
    return {
        "audit_version": AUDIT_VERSION,
        "source_page_count": extracted.source.page_count,
        "extracted_page_count": len(extracted.pages),
        "page_note_count": sum(page.page_note is not None for page in extracted.pages),
        "all_pages_accounted_for": len(extracted.pages) == extracted.source.page_count,
        "reconstruction_ready": not missing_geometry_blocks,
        "missing_geometry_block_count": len(missing_geometry_blocks),
        "missing_geometry_blocks": missing_geometry_blocks,
        "unresolved_geometry_block_count": len(unresolved_geometry_blocks),
        "unresolved_geometry_blocks": unresolved_geometry_blocks,
        "invalid_geometry_block_count": len(invalid_geometry_blocks),
        "invalid_geometry_blocks": invalid_geometry_blocks,
        "pages": rows,
        "human_review_required": True,
    }
