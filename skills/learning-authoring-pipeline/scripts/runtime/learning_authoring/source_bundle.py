"""Ordered multi-source lineage without changing the existing one-PDF contracts.

Every PDF remains an ordinary authoring run with its own source manifest and
canonical Extraction.  A bundle binds those exact artifacts in caller-supplied
order.  Multi-source KC proposals use source-qualified page/block references;
the historical :class:`ProposedKCSet` remains valid for a one-source bundle.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from learning_authoring.artifacts import read_json, sha256_file, write_json
from learning_authoring.authoring_context import AuthoringContext
from learning_authoring.contracts import ExtractedSource, SourceDescriptor
from learning_authoring.kc_contracts import (
    KCContextAudit,
    KCEvidence,
    KCGroup,
    KCPageAudit,
    LeafKC,
    ProposedKCSet,
    UncoveredContent,
)

SOURCE_BUNDLE_SCHEMA_VERSION = "source-bundle.v1"
SOURCE_BUNDLE_MANIFEST = "source-bundle.json"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    from learning_authoring.artifacts import sha256_bytes

    bound = {key: value for key, value in payload.items() if key != "bundle_sha256"}
    return sha256_bytes(
        json.dumps(bound, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _safe_relative_ref(value: str, *, label: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\\" in value:
        raise ValueError(f"{label} must be a relative POSIX path")
    if str(path) != value or value in {".", ""}:
        raise ValueError(f"{label} must be a normalized relative POSIX path")
    return value


class SourceBundleEntry(BaseModel):
    """One exact Extraction subrun in an ordered source collection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_ref: str = Field(min_length=1)
    source_manifest_ref: str = Field(min_length=1)
    source_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    extraction_ref: str = Field(min_length=1)
    extraction_sha256: str = Field(pattern=SHA256_PATTERN)
    extraction_status: Literal["PROPOSED", "HUMAN_APPROVED"]
    source: SourceDescriptor

    @field_validator("run_ref", "source_manifest_ref", "extraction_ref")
    @classmethod
    def paths_are_relative(cls, value: str, info) -> str:
        return _safe_relative_ref(value, label=info.field_name)

    @model_validator(mode="after")
    def artifact_refs_stay_in_the_source_run(self) -> SourceBundleEntry:
        run = PurePosixPath(self.run_ref)
        for name, value in (
            ("source_manifest_ref", self.source_manifest_ref),
            ("extraction_ref", self.extraction_ref),
        ):
            path = PurePosixPath(value)
            if path.parent != run:
                raise ValueError(f"{name} must point directly inside run_ref")
        return self


class SourceBundle(BaseModel):
    """Code-owned identity for one ordered, nonempty collection of PDF runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["source-bundle.v1"] = SOURCE_BUNDLE_SCHEMA_VERSION
    sources: list[SourceBundleEntry] = Field(min_length=1)
    bundle_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def identity_is_unique_and_content_addressed(self) -> SourceBundle:
        source_ids = [entry.source.source_id for entry in self.sources]
        source_hashes = [entry.source.sha256 for entry in self.sources]
        run_refs = [entry.run_ref for entry in self.sources]
        for label, values in (
            ("source IDs", source_ids),
            ("source SHA-256 values", source_hashes),
            ("source run references", run_refs),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"source bundle contains duplicate {label}")
        if self.bundle_sha256 != _canonical_digest(self.model_dump(mode="json")):
            raise ValueError("source bundle SHA-256 does not match its ordered contents")
        return self


class SourceBundleKCRef(BaseModel):
    """Lineage copied into a shared KC proposal, with context kept separate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["source-bundle.v1"] = SOURCE_BUNDLE_SCHEMA_VERSION
    source_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    authoring_context_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)


class SourceQualifiedKCPageAudit(KCPageAudit):
    source_id: str = Field(min_length=1)


class SourceQualifiedKCEvidence(KCEvidence):
    source_id: str = Field(min_length=1)


class SourceQualifiedLeafKC(LeafKC):
    source_evidence: list[SourceQualifiedKCEvidence]


class SourceQualifiedUncoveredContent(UncoveredContent):
    source_id: str = Field(min_length=1)


class SourceBlockLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    page: Annotated[int, Field(ge=1, strict=True)]
    block_ids: list[str] = Field(default_factory=list)


class SourceBundleKCGenerationWarning(BaseModel):
    """A warning may name zero or more unambiguous source locations."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source_locations: list[SourceBlockLocation] = Field(default_factory=list)
    kc_ids: list[str] = Field(default_factory=list)


class SourceBundleKCSet(BaseModel):
    """One proposed KC set spanning every Extraction in a source bundle."""

    model_config = ConfigDict(extra="forbid")

    source_ref: SourceBundleKCRef
    source_summary: str = Field(min_length=1)
    page_audit: list[SourceQualifiedKCPageAudit] = Field(min_length=1)
    kc_groups: list[KCGroup]
    leaf_kcs: list[SourceQualifiedLeafKC]
    uncovered_content: list[SourceQualifiedUncoveredContent]
    generation_warnings: list[SourceBundleKCGenerationWarning]
    context_audit: list[KCContextAudit] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_internal_references(self) -> SourceBundleKCSet:
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
                raise ValueError(f"source page audit references unknown KCs {sorted(unknown)}")
        audit_links = {
            (audit.source_id, audit.page, kc_id)
            for audit in self.page_audit
            for kc_id in audit.kc_ids
        }
        evidence_links = {
            (evidence.source_id, evidence.page, kc.kc_id)
            for kc in self.leaf_kcs
            for evidence in kc.source_evidence
        }
        audit_only = sorted(audit_links - evidence_links)
        if audit_only:
            source_id, page, kc_id = audit_only[0]
            raise ValueError(
                f"page_audit claims KC {kc_id} at {source_id} page {page} "
                "without matching source_evidence"
            )
        evidence_only = sorted(evidence_links - audit_links)
        if evidence_only:
            source_id, page, kc_id = evidence_only[0]
            raise ValueError(
                f"source_evidence for KC {kc_id} at {source_id} page {page} "
                "is omitted from page_audit"
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

    def validate_against_bundle(
        self,
        bundle: SourceBundle,
        extractions: Mapping[str, ExtractedSource],
        authoring_context: AuthoringContext | None = None,
    ) -> None:
        """Verify bundle identity and every source-qualified page/block locator."""

        # Revalidate mutable nested lists even if callers pass parsed models.
        bundle = SourceBundle.model_validate(bundle.model_dump(mode="json"))
        if self.source_ref.source_bundle_sha256 != bundle.bundle_sha256:
            raise ValueError("KC source bundle SHA-256 does not match the frozen collection")

        expected_ids = [entry.source.source_id for entry in bundle.sources]
        if set(extractions) != set(expected_ids):
            raise ValueError("KC validation requires exactly the bundle's extracted sources")
        page_blocks: dict[str, dict[int, set[str]]] = {}
        for entry in bundle.sources:
            extracted = ExtractedSource.model_validate(
                extractions[entry.source.source_id].model_dump(mode="json")
            )
            if extracted.source != entry.source:
                raise ValueError(f"Extraction source identity differs for {entry.source.source_id}")
            page_blocks[entry.source.source_id] = {
                page.page_number: {block.block_id for block in page.blocks}
                for page in extracted.pages
            }

        expected_pages = [
            (entry.source.source_id, page)
            for entry in bundle.sources
            for page in range(1, entry.source.page_count + 1)
        ]
        actual_pages = [(audit.source_id, audit.page) for audit in self.page_audit]
        if actual_pages != expected_pages:
            raise ValueError("KC page_audit must cover every bundled source page in bundle order")

        def validate_location(
            source_id: str, page: int, block_ids: Sequence[str], label: str
        ) -> None:
            if source_id not in page_blocks:
                raise ValueError(f"{label} references unknown source {source_id}")
            if page not in page_blocks[source_id]:
                raise ValueError(f"{label} references unknown page {page} in {source_id}")
            unknown = set(block_ids) - page_blocks[source_id][page]
            if unknown:
                raise ValueError(
                    f"{label} references blocks outside {source_id} page {page}: {sorted(unknown)}"
                )

        for audit in self.page_audit:
            validate_location(audit.source_id, audit.page, audit.source_block_ids, "page audit")
        for kc in self.leaf_kcs:
            for evidence in kc.source_evidence:
                validate_location(
                    evidence.source_id,
                    evidence.page,
                    evidence.block_ids,
                    f"evidence {evidence.evidence_id}",
                )
        for item in self.uncovered_content:
            validate_location(item.source_id, item.page, item.block_ids, "uncovered content")
        for warning in self.generation_warnings:
            for location in warning.source_locations:
                validate_location(
                    location.source_id,
                    location.page,
                    location.block_ids,
                    f"warning {warning.code}",
                )

        has_context = any(kc.context_evidence for kc in self.leaf_kcs) or bool(self.context_audit)
        if authoring_context is None:
            if has_context or self.source_ref.authoring_context_sha256 is not None:
                raise ValueError("KC context lineage requires the bound bundle context")
            return

        authoring_context.validate_against_bundle(bundle)
        if self.source_ref.authoring_context_sha256 != authoring_context.sha256:
            raise ValueError("KC authoring context SHA-256 is missing or does not match")
        page_counts = {entry.source.source_id: entry.source.page_count for entry in bundle.sources}
        for kc in self.leaf_kcs:
            for evidence in kc.context_evidence:
                if evidence.pages:
                    if evidence.source_id is None:
                        raise ValueError(
                            "page-mapped bundle context evidence requires a source_id; "
                            "do not infer the same ordinal across PDFs"
                        )
                    page_count = page_counts.get(evidence.source_id)
                    if page_count is None:
                        raise ValueError(
                            f"context evidence references unknown source {evidence.source_id}"
                        )
                    if any(page > page_count for page in evidence.pages):
                        raise ValueError(
                            f"context evidence {evidence.context_id} references an unknown page "
                            f"in {evidence.source_id}"
                        )
                evidence.validate_against_context(authoring_context)
        for audit in self.context_audit:
            audit.validate_against_context(authoring_context)
        missing = {item.context_id for item in authoring_context.items} - {
            audit.context_id for audit in self.context_audit
        }
        if missing:
            raise ValueError(f"KC context_audit omits supplied context inputs: {sorted(missing)}")


def _bundle_payload(entries: Sequence[SourceBundleEntry]) -> dict[str, Any]:
    return {
        "schema_version": SOURCE_BUNDLE_SCHEMA_VERSION,
        "sources": [entry.model_dump(mode="json") for entry in entries],
    }


def _resolve_inside(root: Path, reference: str, *, label: str) -> Path:
    path = root / _safe_relative_ref(reference, label=label)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError(f"{label} escapes the source bundle or is a symlink")
    return path


def _assert_proposed_extraction_is_promoted(run_dir: Path) -> None:
    """Reject an explicitly blocked proposal, including stale canonical bytes."""

    metadata_path = run_dir / "extraction-metadata.json"
    if not metadata_path.is_file():
        return
    metadata = read_json(metadata_path)
    if metadata.get("promotion_gate_passed") is False:
        raise ValueError(
            f"source run Extraction failed its promotion gate: {run_dir}"
        )


def _extraction_for_run(run_dir: Path) -> tuple[Path, ExtractedSource, str]:
    # Approval loading belongs to the file-backed boundary. Keep the contract
    # module importable by native task/schema tooling without importing a
    # generation stage at module import time.
    from learning_authoring.kc import load_approved_extraction

    approved = run_dir / "extracted-source.approved.json"
    approval = run_dir / "extraction-approval.json"
    if approved.exists() or approval.exists():
        if not (approved.is_file() and approval.is_file()):
            raise ValueError("source run has an incomplete Extraction approval boundary")
        extracted, _, _ = load_approved_extraction(run_dir)
        return approved, extracted, "HUMAN_APPROVED"
    proposed = run_dir / "extracted-source.proposed.json"
    if not proposed.is_file():
        raise ValueError(f"source run has no canonical Extraction: {run_dir}")
    _assert_proposed_extraction_is_promoted(run_dir)
    return proposed, ExtractedSource.model_validate(read_json(proposed)), "PROPOSED"


def _entry_from_run(root: Path, run_dir: Path) -> SourceBundleEntry:
    run = run_dir.expanduser().resolve()
    if not run.is_dir() or not run.is_relative_to(root):
        raise ValueError("every source run must be a directory inside the bundle root")
    run_ref = run.relative_to(root).as_posix()
    if run_ref == ".":
        raise ValueError("a bundled PDF must retain its own Extraction subrun")
    manifest = run / "source-manifest.json"
    source_pdf = run / "source.pdf"
    if not manifest.is_file() or not source_pdf.is_file():
        raise ValueError(f"source run is incomplete: {run}")
    source = SourceDescriptor.model_validate(read_json(manifest)["source"])
    if sha256_file(source_pdf) != source.sha256:
        raise ValueError(f"source PDF differs from its source manifest: {run_ref}")
    extraction_path, extracted, status = _extraction_for_run(run)
    if extracted.source != source:
        raise ValueError(f"Extraction differs from its source manifest: {run_ref}")
    return SourceBundleEntry(
        run_ref=run_ref,
        source_manifest_ref=(PurePosixPath(run_ref) / manifest.name).as_posix(),
        source_manifest_sha256=sha256_file(manifest),
        extraction_ref=(PurePosixPath(run_ref) / extraction_path.name).as_posix(),
        extraction_sha256=sha256_file(extraction_path),
        extraction_status=status,
        source=source,
    )


def prepare_source_bundle(
    bundle_root: Path,
    source_runs: Sequence[Path],
    *,
    manifest_path: Path | None = None,
) -> SourceBundle:
    """Freeze an ordered list of existing one-PDF Extraction subruns."""

    root = bundle_root.expanduser().resolve()
    if not source_runs:
        raise ValueError("source bundle requires at least one PDF run")
    root.mkdir(parents=True, exist_ok=True)
    entries = [_entry_from_run(root, Path(run)) for run in source_runs]
    payload = _bundle_payload(entries)
    bundle = SourceBundle.model_validate({**payload, "bundle_sha256": _canonical_digest(payload)})
    destination = (manifest_path or root / SOURCE_BUNDLE_MANIFEST).expanduser().resolve()
    if not destination.is_relative_to(root) or destination.is_symlink():
        raise ValueError("source bundle manifest must be a regular file inside the bundle root")
    write_json(destination, bundle.model_dump(mode="json"))
    return load_source_bundle(root, manifest_path=destination)


def load_source_bundle(
    bundle_root: Path,
    *,
    manifest_path: Path | None = None,
) -> SourceBundle:
    """Load a bundle and fail if any bound source or Extraction bytes changed."""

    root = bundle_root.expanduser().resolve()
    path = (manifest_path or root / SOURCE_BUNDLE_MANIFEST).expanduser().resolve()
    if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
        raise ValueError("source bundle manifest must be a regular file inside the bundle root")
    bundle = SourceBundle.model_validate(read_json(path))
    for entry in bundle.sources:
        manifest = _resolve_inside(root, entry.source_manifest_ref, label="source_manifest_ref")
        extraction = _resolve_inside(root, entry.extraction_ref, label="extraction_ref")
        run = _resolve_inside(root, entry.run_ref, label="run_ref")
        if not manifest.is_file() or sha256_file(manifest) != entry.source_manifest_sha256:
            raise ValueError(f"source manifest changed for {entry.source.source_id}")
        current_source = SourceDescriptor.model_validate(read_json(manifest)["source"])
        if current_source != entry.source:
            raise ValueError(f"source identity changed for {entry.source.source_id}")
        source_pdf = run / "source.pdf"
        if not source_pdf.is_file() or sha256_file(source_pdf) != entry.source.sha256:
            raise ValueError(f"source PDF changed for {entry.source.source_id}")
        if not extraction.is_file() or sha256_file(extraction) != entry.extraction_sha256:
            raise ValueError(f"Extraction changed for {entry.source.source_id}")
        if entry.extraction_status == "PROPOSED":
            _assert_proposed_extraction_is_promoted(run)
        extracted = ExtractedSource.model_validate(read_json(extraction))
        if extracted.source != entry.source:
            raise ValueError(f"Extraction identity changed for {entry.source.source_id}")
    return bundle


def load_bundle_extractions(
    bundle_root: Path,
    bundle: SourceBundle | None = None,
) -> dict[str, ExtractedSource]:
    """Return verified Extractions keyed by source ID in manifest order."""

    root = bundle_root.expanduser().resolve()
    current = bundle or load_source_bundle(root)
    current = SourceBundle.model_validate(current.model_dump(mode="json"))
    result: dict[str, ExtractedSource] = {}
    for entry in current.sources:
        path = _resolve_inside(root, entry.extraction_ref, label="extraction_ref")
        if sha256_file(path) != entry.extraction_sha256:
            raise ValueError(f"Extraction changed for {entry.source.source_id}")
        result[entry.source.source_id] = ExtractedSource.model_validate(read_json(path))
    return result


def bundle_kc_source_ref(
    bundle: SourceBundle,
    *,
    authoring_context_sha256: str | None = None,
) -> SourceBundleKCRef:
    """Bind shared KCs to sources while keeping optional context on a separate axis."""

    return SourceBundleKCRef(
        source_bundle_sha256=bundle.bundle_sha256,
        authoring_context_sha256=authoring_context_sha256,
    )


KCSet = ProposedKCSet | SourceBundleKCSet


def validate_kc_set_against_bundle(
    value: KCSet | Mapping[str, Any],
    bundle: SourceBundle,
    extractions: Mapping[str, ExtractedSource],
    *,
    authoring_context: AuthoringContext | None = None,
) -> KCSet:
    """Validate a shared KC set, accepting legacy shape only for one PDF.

    This dispatcher is the intended integration seam for a future bundle-aware
    ``agent-task kc``.  It avoids changing the existing one-source JSON schema.
    """

    if isinstance(value, SourceBundleKCSet):
        parsed: KCSet = SourceBundleKCSet.model_validate(value.model_dump(mode="json"))
    elif isinstance(value, ProposedKCSet):
        parsed = ProposedKCSet.model_validate(value.model_dump(mode="json"))
    else:
        source_ref = value.get("source_ref") if isinstance(value, Mapping) else None
        if isinstance(source_ref, Mapping) and source_ref.get("schema_version") == (
            SOURCE_BUNDLE_SCHEMA_VERSION
        ):
            parsed = SourceBundleKCSet.model_validate(value)
        else:
            parsed = ProposedKCSet.model_validate(value)

    if isinstance(parsed, SourceBundleKCSet):
        parsed.validate_against_bundle(bundle, extractions, authoring_context)
        return parsed
    if len(bundle.sources) != 1:
        raise ValueError("legacy KC source references are valid only for a one-PDF bundle")
    only = bundle.sources[0].source.source_id
    if set(extractions) != {only}:
        raise ValueError("legacy KC validation requires exactly the bundle's one Extraction")
    parsed.validate_against_source(extractions[only], authoring_context=authoring_context)
    return parsed


def source_qualified_evidence(
    kc_set: KCSet,
    bundle: SourceBundle,
) -> list[dict[str, Any]]:
    """Project all PDF evidence into one unambiguous downstream representation."""

    if isinstance(kc_set, SourceBundleKCSet):
        return [
            evidence.model_dump(mode="json")
            for kc in kc_set.leaf_kcs
            for evidence in kc.source_evidence
        ]
    if len(bundle.sources) != 1:
        raise ValueError("legacy KC evidence cannot be inferred for multiple sources")
    source_id = bundle.sources[0].source.source_id
    return [
        {"source_id": source_id, **evidence.model_dump(mode="json")}
        for kc in kc_set.leaf_kcs
        for evidence in kc.source_evidence
    ]
