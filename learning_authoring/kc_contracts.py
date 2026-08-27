"""Strict contracts for KC proposals grounded in an approved extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from learning_authoring.contracts import ExtractedSource


class KCSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["extracted-source.v2"]
    source_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class KCPageAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    classification: Literal[
        "learning_content",
        "example",
        "exercise",
        "context",
        "administrative",
        "cover",
        "section_divider",
        "unclear",
    ]
    summary: str = Field(min_length=1)
    kc_ids: list[str] = Field(default_factory=list)
    source_block_ids: list[str] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)


class KCGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(pattern=r"^KCG-[0-9]+$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    leaf_kc_ids: list[str] = Field(min_length=1)


class AssessmentBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    included: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)


class KCEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^EVD-[0-9]+$")
    page: int = Field(ge=1)
    block_ids: list[str] = Field(min_length=1)
    description: str = Field(min_length=1)
    supports: str = Field(min_length=1)


class LeafKC(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kc_id: str = Field(pattern=r"^KC-[0-9]+$")
    group_id: str = Field(pattern=r"^KCG-[0-9]+$")
    name: str = Field(min_length=1)
    semantic_form: Literal[
        "fact", "concept", "distinction", "principle", "procedure", "decision_rule"
    ]
    knowledge_description: str = Field(min_length=1)
    observable_claim: str = Field(min_length=1)
    assessment_boundary: AssessmentBoundary
    source_evidence: list[KCEvidence] = Field(min_length=1)
    warning_codes: list[str] = Field(default_factory=list)
    status: Literal["PROPOSED"]


class UncoveredContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    block_ids: list[str] = Field(min_length=1)
    description: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class KCGenerationWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    pages: list[int] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    kc_ids: list[str] = Field(default_factory=list)


class ProposedKCSet(BaseModel):
    """Model output before human KC review and approval."""

    model_config = ConfigDict(extra="forbid")

    source_ref: KCSourceRef
    source_summary: str = Field(min_length=1)
    page_audit: list[KCPageAudit] = Field(min_length=1)
    kc_groups: list[KCGroup]
    leaf_kcs: list[LeafKC]
    uncovered_content: list[UncoveredContent]
    generation_warnings: list[KCGenerationWarning]

    @model_validator(mode="after")
    def validate_internal_references(self) -> ProposedKCSet:
        kc_ids = [kc.kc_id for kc in self.leaf_kcs]
        group_ids = [group.group_id for group in self.kc_groups]
        evidence_ids = [
            evidence.evidence_id for kc in self.leaf_kcs for evidence in kc.source_evidence
        ]
        for label, values in (
            ("KC", kc_ids),
            ("group", group_ids),
            ("evidence", evidence_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label} IDs")

        known_kcs = set(kc_ids)
        known_groups = set(group_ids)
        for kc in self.leaf_kcs:
            if kc.group_id not in known_groups:
                raise ValueError(f"KC {kc.kc_id} references unknown group {kc.group_id}")
        for group in self.kc_groups:
            unknown = set(group.leaf_kc_ids) - known_kcs
            if unknown:
                raise ValueError(f"group {group.group_id} references unknown KCs {sorted(unknown)}")
            expected = {kc.kc_id for kc in self.leaf_kcs if kc.group_id == group.group_id}
            if set(group.leaf_kc_ids) != expected:
                raise ValueError(f"group {group.group_id} membership is not bidirectional")
        for audit in self.page_audit:
            unknown = set(audit.kc_ids) - known_kcs
            if unknown:
                raise ValueError(
                    f"page {audit.page} audit references unknown KCs {sorted(unknown)}"
                )
        for warning in self.generation_warnings:
            unknown = set(warning.kc_ids) - known_kcs
            if unknown:
                raise ValueError(f"warning references unknown KCs {sorted(unknown)}")
        return self

    def validate_against_source(self, source: ExtractedSource) -> None:
        """Validate identity, page coverage, and every referenced extraction block."""

        if self.source_ref.schema_version != source.schema_version:
            raise ValueError("KC source schema version does not match approved extraction")
        if self.source_ref.source_id != source.source.source_id:
            raise ValueError("KC source_id does not match approved extraction")
        if self.source_ref.source_sha256 != source.source.sha256:
            raise ValueError("KC source SHA-256 does not match approved extraction")

        expected_pages = list(range(1, source.source.page_count + 1))
        actual_pages = [entry.page for entry in self.page_audit]
        if actual_pages != expected_pages:
            raise ValueError("KC page_audit must be ordered and cover every page from 1..N")

        page_blocks = {
            page.page_number: {block.block_id for block in page.blocks} for page in source.pages
        }
        all_blocks = set().union(*page_blocks.values()) if page_blocks else set()

        def validate_page_blocks(page: int, block_ids: list[str], context: str) -> None:
            if page not in page_blocks:
                raise ValueError(f"{context} references unknown page {page}")
            unknown = set(block_ids) - page_blocks[page]
            if unknown:
                raise ValueError(
                    f"{context} references blocks outside page {page}: {sorted(unknown)}"
                )

        for audit in self.page_audit:
            validate_page_blocks(audit.page, audit.source_block_ids, f"page {audit.page} audit")
        for kc in self.leaf_kcs:
            for evidence in kc.source_evidence:
                validate_page_blocks(
                    evidence.page, evidence.block_ids, f"evidence {evidence.evidence_id}"
                )
        for item in self.uncovered_content:
            validate_page_blocks(item.page, item.block_ids, "uncovered content")
        for warning in self.generation_warnings:
            unknown_pages = set(warning.pages) - set(expected_pages)
            if unknown_pages:
                raise ValueError(f"warning references unknown pages {sorted(unknown_pages)}")
            unknown_blocks = set(warning.block_ids) - all_blocks
            if unknown_blocks:
                raise ValueError(f"warning references unknown blocks {sorted(unknown_blocks)}")
