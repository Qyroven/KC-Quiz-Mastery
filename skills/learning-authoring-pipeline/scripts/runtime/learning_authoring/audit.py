"""Deterministic extraction diagnostics; never a semantic accuracy score."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from learning_authoring.contracts import ExtractedSource, source_region_geometry_state

AUDIT_VERSION = "extraction-audit.v7"
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_INVALID_TEXT_CODEPOINTS = {0xFFFE, 0xFFFF}


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
        preview = ", ".join(f"page {row['page']} block {row['block_id']}" for row in invalid[:5])
        suffix = f" (+{len(invalid) - 5} more)" if len(invalid) > 5 else ""
        raise ValueError("Extraction contains invalid normalized geometry: " + preview + suffix)


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


def _text_artifacts(extracted: ExtractedSource) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for page in extracted.pages:
        for block in page.blocks:
            for value in _strings(block.content):
                bad = sorted(
                    {
                        f"U+{ord(char):04X}"
                        for char in value
                        if ord(char) in _INVALID_TEXT_CODEPOINTS
                        or (ord(char) < 32 and char not in "\n\r\t")
                    }
                )
                if bad:
                    artifacts.append(
                        {
                            "page": page.page_number,
                            "block_id": block.block_id,
                            "codepoints": bad,
                        }
                    )
    return artifacts


def _geometry_signature(block: Any) -> str | None:
    if source_region_geometry_state(block.region) != "located":
        return None
    geometry = block.region.geometry or {}
    bbox = geometry.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        values = bbox
    elif all(name in geometry for name in ("x", "y", "w", "h")):
        values = [geometry[name] for name in ("x", "y", "w", "h")]
    else:
        return None
    try:
        return ",".join(f"{float(value):.3f}" for value in values)
    except (TypeError, ValueError):
        return None


def _fresh_candidate_guidance(
    extracted: ExtractedSource,
    rows: list[dict[str, Any]],
    *,
    text_artifacts: list[dict[str, Any]],
    unresolved_geometry_count: int,
) -> dict[str, Any]:
    """Detect mechanical extraction failures, never semantic correctness."""

    trigger_codes: list[str] = []
    details: list[dict[str, Any]] = []
    if text_artifacts:
        trigger_codes.append("INVALID_TEXT_CODEPOINT")
        details.append(
            {
                "code": "INVALID_TEXT_CODEPOINT",
                "count": len(text_artifacts),
                "examples": text_artifacts[:12],
            }
        )

    page_count = len(rows)
    nonempty_rows = [row for row in rows if row["model_block_count"]]
    count_frequency = Counter(row["model_block_count"] for row in nonempty_rows)
    modal_count, modal_pages = count_frequency.most_common(1)[0] if count_frequency else (0, 0)
    modal_share = modal_pages / len(nonempty_rows) if nonempty_rows else 0.0

    signatures: Counter[str] = Counter()
    signature_pages: dict[str, set[int]] = {}
    for page in extracted.pages:
        for block in page.blocks:
            signature = _geometry_signature(block)
            if signature is None:
                continue
            signatures[signature] += 1
            signature_pages.setdefault(signature, set()).add(page.page_number)
    repeated_signature, repeated_count = signatures.most_common(1)[0] if signatures else (None, 0)
    repeated_pages = (
        len(signature_pages.get(repeated_signature, set())) if repeated_signature else 0
    )
    template_shortcut = (
        page_count >= 12
        and modal_count <= 2
        and modal_share >= 0.85
        and repeated_pages >= max(8, round(page_count * 0.6))
    )
    if template_shortcut:
        trigger_codes.append("REPEATED_PAGE_TEMPLATE")
        details.append(
            {
                "code": "REPEATED_PAGE_TEMPLATE",
                "modal_block_count": modal_count,
                "modal_page_share": round(modal_share, 4),
                "repeated_geometry_signature": repeated_signature,
                "repeated_geometry_occurrences": repeated_count,
                "pages_with_signature": repeated_pages,
            }
        )

    eligible = [row for row in rows if row["local_text_layer_chars"] >= 40]
    low_coverage = [
        row
        for row in eligible
        if row["diagnostic_text_token_overlap"] is not None
        and row["diagnostic_text_token_overlap"] < 0.65
    ]
    if len(eligible) >= 8 and len(low_coverage) / len(eligible) >= 0.3:
        trigger_codes.append("SYSTEMIC_TEXT_OMISSION")
        details.append(
            {
                "code": "SYSTEMIC_TEXT_OMISSION",
                "eligible_page_count": len(eligible),
                "low_coverage_page_count": len(low_coverage),
                "pages": [row["page"] for row in low_coverage[:20]],
            }
        )

    block_count = sum(row["model_block_count"] for row in rows)
    if block_count >= 20 and unresolved_geometry_count / block_count >= 0.2:
        trigger_codes.append("SYSTEMIC_UNRESOLVED_GEOMETRY")
        details.append(
            {
                "code": "SYSTEMIC_UNRESOLVED_GEOMETRY",
                "block_count": block_count,
                "unresolved_geometry_count": unresolved_geometry_count,
            }
        )

    return {
        "recommended": bool(trigger_codes),
        "trigger_codes": trigger_codes,
        "details": details,
        "next_action": (
            "inspect_flagged_source_content_and_revise_if_supported"
            if trigger_codes
            else "continue_authoring_with_semantic_checks"
        ),
        "interpretation": (
            "Diagnostic warnings only, not an approval or a revision limit. Inspect the source "
            "before revising; preserve archived versions and recheck affected downstream work. "
            "No trigger is proof of semantic completeness."
        ),
    }


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
    text_artifacts = _text_artifacts(extracted)
    guidance = _fresh_candidate_guidance(
        extracted,
        rows,
        text_artifacts=text_artifacts,
        unresolved_geometry_count=len(unresolved_geometry_blocks),
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
        "invalid_text_artifact_count": len(text_artifacts),
        "invalid_text_artifacts": text_artifacts,
        "fresh_candidate_guidance": guidance,
        "pages": rows,
        "human_review_required": True,
    }
