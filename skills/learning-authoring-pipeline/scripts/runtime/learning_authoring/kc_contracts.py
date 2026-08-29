"""KC proposals grounded in canonical PDF extraction and optional lecturer context."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from learning_authoring.authoring_context import AuthoringContext
from learning_authoring.contracts import ExtractedSource


class KCSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["extracted-source.v2"]
    source_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authoring_context_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


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


class KCContextEvidence(BaseModel):
    """An exact lecturer-text quote or an explicitly inspected attachment observation.

    Pages are optional semantic links, never PDF evidence or extraction block IDs.
    The context package and its raw-item hashes provide the independent provenance.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "anyOf": [
                {"required": ["excerpt"], "properties": {"excerpt": {"type": "string"}}},
                {
                    "required": ["description"],
                    "properties": {"description": {"type": "string"}},
                },
            ]
        },
    )

    context_id: str = Field(pattern=r"^CTX-[0-9]+$")
    excerpt: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    supports: str = Field(min_length=1)
    source_id: str | None = Field(default=None, min_length=1)
    pages: list[Annotated[int, Field(ge=1, strict=True)]] = Field(default_factory=list)
    mapping_method: Literal[
        "explicit_page_reference", "semantic_alignment", "document_level", "unmapped"
    ] = "unmapped"
    mapping_confidence: Literal["high", "medium", "low", "unmapped"] = "unmapped"

    @field_validator("excerpt", "description", "supports")
    @classmethod
    def validate_nonblank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("context evidence text must not be blank")
        return value

    @model_validator(mode="after")
    def validate_mapping(self) -> KCContextEvidence:
        if self.excerpt is None and self.description is None:
            raise ValueError("context evidence requires an excerpt or attachment description")
        if len(self.pages) != len(set(self.pages)):
            raise ValueError("context evidence pages must not contain duplicates")
        page_mapped = self.mapping_method in {"explicit_page_reference", "semantic_alignment"}
        if bool(self.pages) != page_mapped:
            raise ValueError("context evidence pages must agree with its mapping_method")
        if self.source_id is not None and not page_mapped:
            raise ValueError("document-level or unmapped context evidence must not name a source")
        if page_mapped and self.mapping_confidence == "unmapped":
            raise ValueError("page-mapped context evidence requires mapping confidence")
        if self.mapping_method == "unmapped" and self.mapping_confidence != "unmapped":
            raise ValueError("unmapped context evidence must not claim mapping confidence")
        return self

    def validate_against_context(self, authoring_context: AuthoringContext) -> None:
        item = next(
            (item for item in authoring_context.items if item.context_id == self.context_id),
            None,
        )
        if item is None:
            raise ValueError(f"context evidence references unknown context_id {self.context_id}")
        page_count = getattr(authoring_context.source_ref, "page_count", None)
        if page_count is not None and any(page > page_count for page in self.pages):
            raise ValueError(f"context evidence {self.context_id} references unknown PDF page")
        if item.text is not None:
            if self.excerpt is None or self.excerpt not in item.text:
                raise ValueError(
                    f"context evidence excerpt is not an exact quote from {self.context_id}"
                )
        elif self.excerpt is not None or self.description is None:
            raise ValueError(
                f"attachment evidence {self.context_id} requires an inspection description, "
                "not an unverified text excerpt"
            )


class LeafKC(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "anyOf": [
                {
                    "required": ["source_evidence"],
                    "properties": {"source_evidence": {"minItems": 1}},
                },
                {
                    "required": ["context_evidence"],
                    "properties": {"context_evidence": {"minItems": 1}},
                },
            ]
        },
    )

    kc_id: str = Field(pattern=r"^KC-[0-9]+$")
    group_id: str = Field(pattern=r"^KCG-[0-9]+$")
    name: str = Field(min_length=1)
    semantic_form: Literal[
        "fact", "concept", "distinction", "principle", "procedure", "decision_rule"
    ]
    knowledge_description: str = Field(min_length=1)
    observable_claim: str = Field(min_length=1)
    assessment_boundary: AssessmentBoundary
    source_evidence: list[KCEvidence]
    context_evidence: list[KCContextEvidence] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)
    status: Literal["PROPOSED"]

    @model_validator(mode="after")
    def require_evidence(self) -> LeafKC:
        if not self.source_evidence and not self.context_evidence:
            raise ValueError("Leaf KC requires PDF source_evidence or valid context_evidence")
        return self


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


class KCContextAudit(BaseModel):
    """A source-to-KC accountability record, not a semantic coverage score.

    Units follow meaningful claims in free-form context, not Markdown headings,
    page ordinals, or a prescribed number of notes/KCs.
    """

    model_config = ConfigDict(extra="forbid")

    context_id: str = Field(pattern=r"^CTX-[0-9]+$")
    excerpt: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    claim: str = Field(min_length=1)
    disposition: Literal["represented", "supporting_example", "not_assessed", "unresolved"]
    kc_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @field_validator("excerpt", "description", "claim", "reason")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("context audit text must not be blank")
        return value

    @model_validator(mode="after")
    def validate_disposition(self) -> KCContextAudit:
        if self.excerpt is None and self.description is None:
            raise ValueError("context audit requires a quote or attachment/limitation description")
        if len(self.kc_ids) != len(set(self.kc_ids)):
            raise ValueError("context audit repeats a KC reference")
        if self.disposition == "represented" and not self.kc_ids:
            raise ValueError("represented context audit requires KC references")
        if self.disposition in {"not_assessed", "unresolved"} and self.kc_ids:
            raise ValueError("unrepresented context audit must not claim KC coverage")
        return self

    def validate_against_context(self, context: AuthoringContext) -> None:
        item = next((item for item in context.items if item.context_id == self.context_id), None)
        if item is None:
            raise ValueError(f"context audit references unknown context_id {self.context_id}")
        if item.text is not None:
            if self.excerpt is None or self.excerpt not in item.text:
                raise ValueError(
                    f"context audit excerpt is not an exact quote from {self.context_id}"
                )
        elif self.excerpt is not None or self.description is None:
            raise ValueError(
                "non-text context audit needs an observation or limitation, not a quote"
            )


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
    # Optional for historical artifacts. Fresh context-bearing native tasks require it.
    context_audit: list[KCContextAudit] = Field(default_factory=list)

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
        for audit in self.context_audit:
            unknown = set(audit.kc_ids) - known_kcs
            if unknown:
                raise ValueError(f"context audit references unknown KCs {sorted(unknown)}")
            for kc_id in audit.kc_ids:
                kc = next(kc for kc in self.leaf_kcs if kc.kc_id == kc_id)
                if not any(e.context_id == audit.context_id for e in kc.context_evidence):
                    raise ValueError(f"context audit KC {kc_id} lacks the cited context evidence")
        return self

    def validate_against_source(
        self,
        source: ExtractedSource,
        authoring_context: AuthoringContext | None = None,
        *,
        require_context_audit: bool = False,
    ) -> None:
        """Validate source/context identity, every citation, and PDF page coverage."""

        if self.source_ref.schema_version != source.schema_version:
            raise ValueError("KC source schema version does not match approved extraction")
        if self.source_ref.source_id != source.source.source_id:
            raise ValueError("KC source_id does not match approved extraction")
        if self.source_ref.source_sha256 != source.source.sha256:
            raise ValueError("KC source SHA-256 does not match approved extraction")
        if authoring_context is not None:
            authoring_context.validate_against_source(source.source)
            if self.source_ref.authoring_context_sha256 != authoring_context.sha256:
                raise ValueError("KC authoring context SHA-256 is missing or does not match")
            for audit in self.context_audit:
                audit.validate_against_context(authoring_context)
            if require_context_audit:
                missing = {item.context_id for item in authoring_context.items} - {
                    audit.context_id for audit in self.context_audit
                }
                if missing:
                    raise ValueError(
                        f"KC context_audit omits supplied context inputs: {sorted(missing)}"
                    )
        elif (
            self.source_ref.authoring_context_sha256 is not None
            or any(kc.context_evidence for kc in self.leaf_kcs)
            or self.context_audit
        ):
            raise ValueError("KC context lineage requires the bound authoring context")

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
            for evidence in kc.context_evidence:
                # Context-bearing proposals were rejected above if context is absent.
                assert authoring_context is not None
                if evidence.source_id not in {None, source.source.source_id}:
                    raise ValueError(
                        f"context evidence {evidence.context_id} names a different PDF source"
                    )
                evidence.validate_against_context(authoring_context)
        for item in self.uncovered_content:
            validate_page_blocks(item.page, item.block_ids, "uncovered content")
        for warning in self.generation_warnings:
            unknown_pages = set(warning.pages) - set(expected_pages)
            if unknown_pages:
                raise ValueError(f"warning references unknown pages {sorted(unknown_pages)}")
            unknown_blocks = set(warning.block_ids) - all_blocks
            if unknown_blocks:
                raise ValueError(f"warning references unknown blocks {sorted(unknown_blocks)}")
