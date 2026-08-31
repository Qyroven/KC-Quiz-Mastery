"""Portable contracts shared by extraction and future authoring stages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

EXTRACTED_SOURCE_SCHEMA_VERSION = "extracted-source.v2"
LocalizationStatus = Literal["located", "unresolved"]
GeometryState = Literal["located", "unresolved", "invalid"]


def normalized_geometry_bbox(geometry: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """Return valid normalized top-left bounds from a supported geometry shape."""

    values: object
    if set(geometry) >= {"x", "y", "w", "h"}:
        values = [geometry["x"], geometry["y"], geometry["w"], geometry["h"]]
    else:
        values = geometry.get("bbox")
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        return None
    x, y, width, height = (float(value) for value in values)
    epsilon = 1e-6
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
        return None
    if x + width > 1 + epsilon or y + height > 1 + epsilon:
        return None
    return x, y, width, height


class WarningRecord(BaseModel):
    """Visible uncertainty or non-blocking issue for a reviewer."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    block_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class SourceDescriptor(BaseModel):
    """Code-owned identity of the PDF used by an extraction run."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    media_type: Literal["application/pdf"] = "application/pdf"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)


class SourceRegion(BaseModel):
    """Page-local evidence geometry with an explicit coordinate system."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    coordinate_system: str = Field(default="normalized_top_left", min_length=1)
    localization_status: LocalizationStatus = Field(
        description=(
            "Use 'located' only when geometry contains valid normalized bounds. "
            "Use 'unresolved' when the source-visible block cannot yet be located reliably."
        )
    )
    geometry: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Normalized top-left block bounds. Prefer x, y, w, h numeric fields in [0,1]. "
            "Use an empty object only with localization_status='unresolved'."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def infer_historical_localization_status(cls, value: Any) -> Any:
        """Load v2 artifacts written before localization_status was explicit."""

        if not isinstance(value, dict) or "localization_status" in value:
            return value
        geometry = value.get("geometry")
        inferred: LocalizationStatus = (
            "located"
            if isinstance(geometry, dict) and normalized_geometry_bbox(geometry) is not None
            else "unresolved"
        )
        return {**value, "localization_status": inferred}


def source_region_geometry_state(region: SourceRegion) -> GeometryState:
    """Classify region geometry without conflating an empty box with a malformed box."""

    bounds = normalized_geometry_bbox(region.geometry)
    if region.coordinate_system != "normalized_top_left":
        return "invalid"
    if region.geometry and bounds is None:
        return "invalid"
    if bounds is None or region.localization_status == "unresolved":
        return "unresolved"
    return "located"


class BlockRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_type: str = Field(min_length=1)
    target_block_id: str = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)


class CrossPageRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_type: str = Field(min_length=1)
    source_block_id: str = Field(min_length=1)
    target_block_id: str = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)


class SemanticBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    content: str | dict[str, Any] | list[Any] | None = None
    region: SourceRegion
    asset_refs: list[str] = Field(default_factory=list)
    relations: list[BlockRelation] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    uncertainties: list[str] = Field(default_factory=list)


class PageNote(BaseModel):
    """Source-grounded page explanation required by the v2 contract."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        min_length=1,
        description="What this page communicates, or its non-teaching role; not an inspection log.",
    )
    explanation: str | None = Field(
        default=None,
        description=(
            "Agent-authored explanation of source-supported meaning, relationships and conditions "
            "across the page. Use when the summary alone is insufficient; retain detailed blocks. "
            "Not lecturer notes, invented teaching content, or a substitute for unreadable content."
        ),
    )
    key_takeaways: list[str] = Field(
        default_factory=list, description="Source-supported takeaways, with no fixed count."
    )
    evidence_block_ids: list[str] = Field(
        default_factory=list, description="Same-page blocks supporting the explanation."
    )
    uncertainties: list[str] = Field(
        default_factory=list,
        description="Specific reading gaps or ambiguous meaning, separate from recovered content.",
    )


class ExtractedPage(BaseModel):
    """Complete semantic inventory for exactly one source page."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    role: str = Field(min_length=1)
    blocks: list[SemanticBlock] = Field(default_factory=list)
    reading_order: list[str] = Field(default_factory=list)
    page_note: PageNote
    warnings: list[WarningRecord] = Field(default_factory=list)
    layout_text: str | None = Field(
        default=None,
        description="Geometry-derived reading aid, not semantic structure; cite original blocks.",
    )

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = handler(self)
        if "layout_text" not in self.model_fields_set:
            result.pop("layout_text", None)
        return result

    @model_validator(mode="after")
    def validate_page_references(self) -> ExtractedPage:
        block_ids = [block.block_id for block in self.blocks]
        known = set(block_ids)
        if len(block_ids) != len(known):
            raise ValueError(f"page {self.page_number} contains duplicate block ids")
        if self.reading_order != list(dict.fromkeys(self.reading_order)):
            raise ValueError(f"page {self.page_number} reading_order contains duplicates")
        if set(self.reading_order) != known or len(self.reading_order) != len(block_ids):
            raise ValueError(
                f"page {self.page_number} reading_order must reference every block exactly once"
            )
        evidence_ids = self.page_note.evidence_block_ids
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError(f"page {self.page_number} page_note has duplicate evidence ids")
        unknown_evidence = set(evidence_ids) - known
        if unknown_evidence:
            raise ValueError(
                f"page {self.page_number} page_note references unknown blocks "
                f"{sorted(unknown_evidence)}"
            )
        for block in self.blocks:
            if block.region.page != self.page_number:
                raise ValueError(
                    f"block {block.block_id} points to page {block.region.page}; "
                    f"expected {self.page_number}"
                )
        for warning in self.warnings:
            if warning.page not in {None, self.page_number}:
                raise ValueError(
                    f"page {self.page_number} contains warning for page {warning.page}"
                )
            unknown_warning_blocks = set(warning.block_ids) - known
            if unknown_warning_blocks:
                raise ValueError(
                    f"page {self.page_number} warning references unknown blocks "
                    f"{sorted(unknown_warning_blocks)}"
                )
        return self


class ExtractedSourcePayload(BaseModel):
    """Model-owned extraction output before code binds source identity."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["extracted-source.v2"]
    pages: list[ExtractedPage] = Field(min_length=1)
    cross_page_relations: list[CrossPageRelation] = Field(default_factory=list)
    warnings: list[WarningRecord] = Field(default_factory=list)

    def with_source(self, source: SourceDescriptor) -> ExtractedSource:
        return ExtractedSource(
            schema_version=self.schema_version,
            source=source,
            pages=self.pages,
            cross_page_relations=self.cross_page_relations,
            warnings=self.warnings,
        )


class ExtractedSource(BaseModel):
    """Canonical approved input for the future KC generator."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["extracted-source.v2"]
    source: SourceDescriptor
    pages: list[ExtractedPage] = Field(min_length=1)
    cross_page_relations: list[CrossPageRelation] = Field(default_factory=list)
    warnings: list[WarningRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_document(self) -> ExtractedSource:
        expected_pages = list(range(1, self.source.page_count + 1))
        actual_pages = [page.page_number for page in self.pages]
        if actual_pages != expected_pages:
            raise ValueError("pages must be ordered and cover every source page from 1..N")

        all_blocks = [block for page in self.pages for block in page.blocks]
        block_ids = [block.block_id for block in all_blocks]
        known = set(block_ids)
        if len(block_ids) != len(known):
            raise ValueError("block ids must be unique across the entire source")
        for block in all_blocks:
            for relation in block.relations:
                if relation.target_block_id not in known:
                    raise ValueError(
                        f"block {block.block_id} references unknown block "
                        f"{relation.target_block_id}"
                    )
        for relation in self.cross_page_relations:
            missing = {relation.source_block_id, relation.target_block_id} - known
            if missing:
                raise ValueError(f"cross-page relation references unknown blocks {sorted(missing)}")
        for warning in self.warnings:
            if warning.page is not None and warning.page > self.source.page_count:
                raise ValueError(f"document warning references unknown page {warning.page}")
            missing = set(warning.block_ids) - known
            if missing:
                raise ValueError(f"document warning references unknown blocks {sorted(missing)}")
        return self
