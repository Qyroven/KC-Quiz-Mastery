"""Deterministic PDF text-layer extraction with source-local geometry.

This module deliberately does not infer teaching meaning.  It preserves the PDF's
native text order, attaches character-derived regions, and marks pages that need
targeted visual inspection.  Semantic interpretation belongs to the KC stage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from learning_authoring.artifacts import RunArtifacts, read_json, sha256_file, write_json
from learning_authoring.audit import build_audit, validate_extraction_geometry
from learning_authoring.contracts import (
    ExtractedPage,
    ExtractedSource,
    PageNote,
    SemanticBlock,
    SourceDescriptor,
    SourceRegion,
    WarningRecord,
)
from learning_authoring.review import build_review

NATIVE_EXTRACTION_VERSION = "native-text-geometry.v1"


@dataclass(frozen=True)
class _Glyph:
    text: str
    box: tuple[float, float, float, float] | None


def _safe_char(text_page: Any, index: int) -> str:
    try:
        return text_page.get_text_range(index, 1)
    except Exception:
        return ""


def _safe_box(text_page: Any, index: int) -> tuple[float, float, float, float] | None:
    try:
        left, bottom, right, top = (float(value) for value in text_page.get_charbox(index))
    except Exception:
        return None
    if right <= left or top <= bottom:
        return None
    return left, bottom, right, top


def _glyphs(text_page: Any) -> list[_Glyph]:
    """Return internal PDF characters without assuming string/index equivalence."""

    count = text_page.count_chars()
    whole = text_page.get_text_range()
    if len(whole) == count:
        return [
            _Glyph(char, None if char in "\r\n" else _safe_box(text_page, index))
            for index, char in enumerate(whole)
        ]
    glyphs: list[_Glyph] = []
    for index in range(count):
        char = _safe_char(text_page, index)
        glyphs.append(
            _Glyph(char, None if char in "\r\n" else _safe_box(text_page, index))
        )
    return glyphs


def _vertical_break(previous: _Glyph, current: _Glyph) -> bool:
    if previous.box is None or current.box is None:
        return False
    _, previous_bottom, _, previous_top = previous.box
    _, current_bottom, _, current_top = current.box
    previous_height = previous_top - previous_bottom
    current_height = current_top - current_bottom
    tolerance = max(previous_height, current_height) * 0.65
    previous_center = (previous_bottom + previous_top) / 2
    current_center = (current_bottom + current_top) / 2
    return abs(previous_center - current_center) > tolerance


def _line_glyphs(glyphs: list[_Glyph]) -> list[list[_Glyph]]:
    lines: list[list[_Glyph]] = []
    current: list[_Glyph] = []
    previous_visible: _Glyph | None = None
    for glyph in glyphs:
        if glyph.text in {"\r", "\n"}:
            if current:
                lines.append(current)
                current = []
            previous_visible = None
            continue
        if (
            current
            and glyph.text.strip()
            and previous_visible is not None
            and _vertical_break(previous_visible, glyph)
        ):
            lines.append(current)
            current = []
        current.append(glyph)
        if glyph.text.strip():
            previous_visible = glyph
    if current:
        lines.append(current)
    return lines


def _normalized_region(
    page_number: int,
    line: list[_Glyph],
    page_width: float,
    page_height: float,
) -> SourceRegion:
    boxes = [glyph.box for glyph in line if glyph.box is not None and glyph.text.strip()]
    if not boxes or page_width <= 0 or page_height <= 0:
        return SourceRegion(
            page=page_number,
            localization_status="unresolved",
            geometry={},
        )
    left = min(box[0] for box in boxes)
    bottom = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    top = max(box[3] for box in boxes)
    x = max(0.0, min(1.0, left / page_width))
    y = max(0.0, min(1.0, (page_height - top) / page_height))
    width = max(1e-6, min(1.0 - x, (right - left) / page_width))
    height = max(1e-6, min(1.0 - y, (top - bottom) / page_height))
    return SourceRegion(
        page=page_number,
        localization_status="located",
        geometry={
            "x": round(x, 6),
            "y": round(y, 6),
            "w": round(width, 6),
            "h": round(height, 6),
        },
    )


def _page_from_text_layer(page: Any, page_number: int) -> ExtractedPage:
    page_width, page_height = (float(value) for value in page.get_size())
    text_page = page.get_textpage()
    try:
        glyphs = _glyphs(text_page)
    finally:
        text_page.close()
    blocks: list[SemanticBlock] = []
    for line in _line_glyphs(glyphs):
        content = "".join(glyph.text for glyph in line).replace("\x00", "").strip()
        if not content:
            continue
        block_id = f"p{page_number:04d}-b{len(blocks) + 1:03d}"
        region = _normalized_region(page_number, line, page_width, page_height)
        blocks.append(
            SemanticBlock(
                block_id=block_id,
                kind="native_text_line",
                content=content,
                region=region,
                asset_refs=[f"pages/page-{page_number:04d}.png"],
                attributes={
                    "extraction_method": NATIVE_EXTRACTION_VERSION,
                    "semantic_interpretation": "not_performed",
                },
                uncertainties=(
                    []
                    if region.localization_status == "located"
                    else ["Character geometry unavailable"]
                ),
            )
        )

    unresolved = [
        block.block_id for block in blocks if block.region.localization_status == "unresolved"
    ]
    warnings: list[WarningRecord] = []
    if not blocks:
        warnings.append(
            WarningRecord(
                code="NO_NATIVE_TEXT",
                message=(
                    "No native PDF text was found; inspect this rendered page for visual content."
                ),
                page=page_number,
                details={"next_action": "targeted_visual_review"},
            )
        )
    if unresolved:
        warnings.append(
            WarningRecord(
                code="TEXT_GEOMETRY_UNRESOLVED",
                message="Some native text could not be localized from PDF character geometry.",
                page=page_number,
                block_ids=unresolved,
                details={"next_action": "targeted_visual_review"},
            )
        )
    evidence_ids = [block.block_id for block in blocks]
    return ExtractedPage(
        page_number=page_number,
        role="source_page",
        blocks=blocks,
        reading_order=evidence_ids,
        page_note=PageNote(
            summary=f"Native PDF text inventory for source page {page_number}.",
            explanation=(
                "Semantic summary intentionally deferred to the KC stage; inspect the page image "
                "when visual relationships matter."
            ),
            evidence_block_ids=evidence_ids,
            uncertainties=(
                ["Page requires targeted visual inspection because it has no native text."]
                if not blocks
                else []
            ),
        ),
        warnings=warnings,
    )


def build_native_extraction(run_dir: Path) -> dict[str, Any]:
    """Create or reuse a deterministic proposed Extraction for one prepared PDF."""

    root = run_dir.expanduser().resolve()
    artifacts = RunArtifacts(root)
    source = SourceDescriptor.model_validate(read_json(artifacts.source_manifest)["source"])
    if artifacts.proposed.is_file():
        existing = ExtractedSource.model_validate(read_json(artifacts.proposed))
        if existing.source != source:
            raise RuntimeError("existing Extraction belongs to a different source")
        metadata = read_json(artifacts.metadata) if artifacts.metadata.is_file() else {}
        return {
            "status": "REUSED",
            "proposed": str(artifacts.proposed),
            "method": metadata.get("extraction_method", "existing_artifact"),
            "review": str(build_review(root)),
        }

    started = time.perf_counter()
    document = pdfium.PdfDocument(artifacts.source_pdf)
    pages: list[ExtractedPage] = []
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                pages.append(_page_from_text_layer(page, index + 1))
            finally:
                page.close()
    finally:
        document.close()
    extracted = ExtractedSource(
        schema_version="extracted-source.v2",
        source=source,
        pages=pages,
        warnings=[
            WarningRecord(
                code="VISUAL_SEMANTICS_NOT_INFERRED",
                message=(
                    "Native text and geometry are deterministic; charts, diagrams, images, and "
                    "spatial relationships still require targeted review."
                ),
                details={"next_action": "inspect_only_relevant_visual_pages_during_kc_authoring"},
            )
        ],
    )
    validate_extraction_geometry(extracted)
    write_json(artifacts.proposed, extracted.model_dump(mode="json"))
    deterministic_snapshot = (
        root / "agent-session" / "deterministic" / "extracted-source.native.json"
    )
    write_json(deterministic_snapshot, extracted.model_dump(mode="json"))
    audit = build_audit(extracted, root)
    write_json(artifacts.audit, audit)
    elapsed = round(time.perf_counter() - started, 6)
    metadata = {
        "stage": "extract",
        "stage_version": NATIVE_EXTRACTION_VERSION,
        "extraction_method": NATIVE_EXTRACTION_VERSION,
        "semantic_generation_performed": False,
        "provider_api_calls": 0,
        "source": source.model_dump(mode="json"),
        "deterministic_snapshot": str(deterministic_snapshot),
        "deterministic_snapshot_sha256": sha256_file(deterministic_snapshot),
        "human_review_required": True,
        "approval_status": "PROPOSED",
        "promotion_gate_passed": True,
        "promotion_gate_basis": "deterministic_contract_and_geometry_valid",
        "audit_findings": audit["fresh_candidate_guidance"],
        "created_at": datetime.now(UTC).isoformat(),
    }
    metrics = {
        "metrics_version": "native-extraction-run-metrics.v1",
        "execution_mode": "deterministic_local_runtime",
        "provider_api_calls": 0,
        "usage": None,
        "usage_available": False,
        "usage_status": "not_applicable_deterministic_extraction",
        "gateway_reported_cost_usd": None,
        "cost_available": False,
        "cost_status": "not_applicable_deterministic_extraction",
        "source_page_count": source.page_count,
        "block_count": sum(len(page.blocks) for page in pages),
        "native_text_page_count": sum(bool(page.blocks) for page in pages),
        "targeted_visual_review_page_count": sum(not page.blocks for page in pages),
        "total_elapsed_seconds": elapsed,
        "human_review_required": True,
        "approved": False,
    }
    write_json(artifacts.metadata, metadata)
    write_json(artifacts.metrics, metrics)
    review = build_review(root)
    return {
        "status": "PROPOSED",
        "proposed": str(artifacts.proposed),
        "method": NATIVE_EXTRACTION_VERSION,
        "block_count": metrics["block_count"],
        "targeted_visual_review_page_count": metrics["targeted_visual_review_page_count"],
        "review": str(review),
    }
