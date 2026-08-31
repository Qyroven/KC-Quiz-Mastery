from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from learning_authoring.artifacts import sha256_bytes, write_json
from learning_authoring.contracts import ExtractedSource, SourceDescriptor
from learning_authoring.source_bundle import (
    SourceBundleKCSet,
    bundle_kc_source_ref,
    load_bundle_extractions,
    load_source_bundle,
    prepare_source_bundle,
    validate_kc_set_against_bundle,
)
from tests.conftest import payload


def _source_run(root: Path, name: str) -> tuple[Path, ExtractedSource]:
    run = root / "sources" / name
    run.mkdir(parents=True)
    pdf_bytes = f"%PDF-1.4\n% synthetic source {name}\n%%EOF\n".encode()
    source_sha256 = sha256_bytes(pdf_bytes)
    source = SourceDescriptor(
        source_id=f"sha256:{source_sha256[:16]}",
        filename=f"{name}.pdf",
        sha256=source_sha256,
        page_count=len(payload().pages),
    )
    extracted = payload().with_source(source)
    (run / "source.pdf").write_bytes(pdf_bytes)
    write_json(
        run / "source-manifest.json",
        {
            "manifest_version": "source-package.v2",
            "source": source.model_dump(mode="json"),
        },
    )
    write_json(run / "extracted-source.proposed.json", extracted.model_dump(mode="json"))
    return run, extracted


def test_bundle_refuses_explicitly_blocked_extraction(tmp_path: Path) -> None:
    run, _ = _source_run(tmp_path, "blocked")
    write_json(run / "extraction-metadata.json", {"promotion_gate_passed": False})

    with pytest.raises(ValueError, match="failed its promotion gate"):
        prepare_source_bundle(tmp_path, [run])


def _legacy_kc(extracted: ExtractedSource) -> dict:
    source = extracted.source
    return {
        "source_ref": {
            "schema_version": extracted.schema_version,
            "source_id": source.source_id,
            "source_sha256": source.sha256,
        },
        "source_summary": "One synthetic source.",
        "page_audit": [
            {
                "page": page.page_number,
                "classification": "learning_content",
                "summary": f"Synthetic page {page.page_number}.",
                "kc_ids": ["KC-001"] if page.page_number == extracted.pages[0].page_number else [],
                "source_block_ids": [block.block_id for block in page.blocks],
                "warning_codes": [],
            }
            for page in extracted.pages
        ],
        "kc_groups": [
            {
                "group_id": "KCG-001",
                "name": "Synthetic group",
                "description": "Fixture group.",
                "leaf_kc_ids": ["KC-001"],
            }
        ],
        "leaf_kcs": [
            {
                "kc_id": "KC-001",
                "group_id": "KCG-001",
                "name": "Synthetic concept",
                "semantic_form": "concept",
                "knowledge_description": "The first visible source concept.",
                "observable_claim": "Given the source, explain the concept.",
                "assessment_boundary": {"included": ["Meaning"], "excluded": []},
                "source_evidence": [
                    {
                        "evidence_id": "EVD-001",
                        "page": extracted.pages[0].page_number,
                        "block_ids": [extracted.pages[0].blocks[0].block_id],
                        "description": "Visible source content.",
                        "supports": "Concept meaning.",
                    }
                ],
                "warning_codes": [],
                "status": "PROPOSED",
            }
        ],
        "uncovered_content": [],
        "generation_warnings": [],
    }


def _bundled_kc(bundle, extractions: dict[str, ExtractedSource]) -> dict:
    first_pages = {source_id: extracted.pages[0] for source_id, extracted in extractions.items()}
    return {
        "source_ref": bundle_kc_source_ref(bundle).model_dump(mode="json"),
        "source_summary": "One shared synthetic KC across the ordered sources.",
        "page_audit": [
            {
                "source_id": entry.source.source_id,
                "page": page.page_number,
                "classification": "learning_content",
                "summary": f"Synthetic page from {entry.source.filename}.",
                "kc_ids": ["KC-001"] if page == first_pages[entry.source.source_id] else [],
                "source_block_ids": [block.block_id for block in page.blocks],
                "warning_codes": [],
            }
            for entry in bundle.sources
            for page in extractions[entry.source.source_id].pages
        ],
        "kc_groups": [
            {
                "group_id": "KCG-001",
                "name": "Shared group",
                "description": "A group spanning all selected PDFs.",
                "leaf_kc_ids": ["KC-001"],
            }
        ],
        "leaf_kcs": [
            {
                "kc_id": "KC-001",
                "group_id": "KCG-001",
                "name": "Shared concept",
                "semantic_form": "concept",
                "knowledge_description": "The related concepts visible in the selected PDFs.",
                "observable_claim": "Given either source, explain the shared concept.",
                "assessment_boundary": {"included": ["Meaning"], "excluded": []},
                "source_evidence": [
                    {
                        "source_id": entry.source.source_id,
                        "evidence_id": f"EVD-{index:03d}",
                        "page": first_pages[entry.source.source_id].page_number,
                        "block_ids": [first_pages[entry.source.source_id].blocks[0].block_id],
                        "description": f"Evidence from {entry.source.filename}.",
                        "supports": "The shared concept.",
                    }
                    for index, entry in enumerate(bundle.sources, start=1)
                ],
                "warning_codes": [],
                "status": "PROPOSED",
            }
        ],
        "uncovered_content": [],
        "generation_warnings": [],
        "context_audit": [],
    }


def test_one_pdf_bundle_accepts_legacy_kc_without_mutating_its_evidence(tmp_path) -> None:
    run, extracted = _source_run(tmp_path, "concepts")
    bundle = prepare_source_bundle(tmp_path, [run])
    extractions = load_bundle_extractions(tmp_path, bundle)
    candidate = _legacy_kc(extracted)
    before = deepcopy(candidate)

    parsed = validate_kc_set_against_bundle(candidate, bundle, extractions)

    assert [entry.source.source_id for entry in bundle.sources] == [extracted.source.source_id]
    assert [item.model_dump(mode="json") for item in parsed.leaf_kcs[0].source_evidence] == (
        candidate["leaf_kcs"][0]["source_evidence"]
    )
    assert candidate == before
    assert all("source_id" not in item for item in candidate["leaf_kcs"][0]["source_evidence"])


def test_n_pdf_bundle_validates_one_shared_kc_with_source_qualified_refs(tmp_path) -> None:
    prepared = [_source_run(tmp_path, name) for name in ("concepts", "exceptions", "examples")]
    bundle = prepare_source_bundle(tmp_path, [run for run, _ in prepared])
    extractions = load_bundle_extractions(tmp_path, bundle)
    candidate = _bundled_kc(bundle, extractions)

    parsed = validate_kc_set_against_bundle(candidate, bundle, extractions)

    assert isinstance(parsed, SourceBundleKCSet)
    assert [item.source_id for kc in parsed.leaf_kcs for item in kc.source_evidence] == [
        entry.source.source_id for entry in bundle.sources
    ]
    assert len(parsed.page_audit) == sum(entry.source.page_count for entry in bundle.sources)
    with pytest.raises(ValueError, match="one-PDF bundle"):
        validate_kc_set_against_bundle(_legacy_kc(prepared[0][1]), bundle, extractions)


def test_bundle_kc_rejects_page_audit_claim_without_same_location_evidence(tmp_path) -> None:
    prepared = [_source_run(tmp_path, name) for name in ("concepts", "exceptions")]
    bundle = prepare_source_bundle(tmp_path, [run for run, _ in prepared])
    extractions = load_bundle_extractions(tmp_path, bundle)
    candidate = _bundled_kc(bundle, extractions)
    kc = candidate["leaf_kcs"][0]
    claimed = kc["source_evidence"].pop(0)

    with pytest.raises(
        ValueError,
        match=(
            rf"page_audit claims KC {kc['kc_id']} at {claimed['source_id']} "
            rf"page {claimed['page']} without matching source_evidence"
        ),
    ):
        SourceBundleKCSet.model_validate(candidate)


def test_bundle_kc_rejects_evidence_omitted_from_matching_page_audit(tmp_path) -> None:
    prepared = [_source_run(tmp_path, name) for name in ("concepts", "exceptions")]
    bundle = prepare_source_bundle(tmp_path, [run for run, _ in prepared])
    extractions = load_bundle_extractions(tmp_path, bundle)
    candidate = _bundled_kc(bundle, extractions)
    kc = candidate["leaf_kcs"][0]
    evidence = kc["source_evidence"][0]
    matching_audit = next(
        audit
        for audit in candidate["page_audit"]
        if (audit["source_id"], audit["page"]) == (evidence["source_id"], evidence["page"])
    )
    matching_audit["kc_ids"].remove(kc["kc_id"])

    with pytest.raises(
        ValueError,
        match=(
            rf"source_evidence for KC {kc['kc_id']} at {evidence['source_id']} "
            rf"page {evidence['page']} is omitted from page_audit"
        ),
    ):
        SourceBundleKCSet.model_validate(candidate)


def test_context_lineage_is_separate_from_source_bundle_identity(tmp_path) -> None:
    prepared = [_source_run(tmp_path, name) for name in ("concepts", "exceptions")]
    bundle = prepare_source_bundle(tmp_path, [run for run, _ in prepared])
    extractions = load_bundle_extractions(tmp_path, bundle)
    candidate = _bundled_kc(bundle, extractions)
    original_bundle_hash = bundle.bundle_sha256
    context_hash = "c" * 64
    candidate["leaf_kcs"][0]["context_evidence"] = [
        {
            "context_id": "CTX-001",
            "excerpt": "A separate lecturer qualification.",
            "description": None,
            "supports": "A contextual limitation.",
            "pages": [],
            "mapping_method": "document_level",
            "mapping_confidence": "high",
        }
    ]
    candidate["context_audit"] = [
        {
            "context_id": "CTX-001",
            "excerpt": "A separate lecturer qualification.",
            "description": None,
            "claim": "Retain the lecturer qualification.",
            "disposition": "represented",
            "kc_ids": ["KC-001"],
            "reason": "It bounds the shared concept.",
        }
    ]

    without_context = SourceBundleKCSet.model_validate(candidate)
    with pytest.raises(ValueError, match="bound bundle context"):
        without_context.validate_against_bundle(bundle, extractions)

    candidate["source_ref"] = bundle_kc_source_ref(
        bundle, authoring_context_sha256=context_hash
    ).model_dump(mode="json")
    with pytest.raises(ValueError, match="bound bundle context"):
        SourceBundleKCSet.model_validate(candidate).validate_against_bundle(bundle, extractions)
    assert load_source_bundle(tmp_path).bundle_sha256 == original_bundle_hash
    assert "authoring_context" not in bundle.model_dump(mode="json")


@pytest.mark.parametrize("bad_ref", ["source", "page", "block"])
def test_multi_source_evidence_must_resolve_inside_its_qualified_source(tmp_path, bad_ref) -> None:
    prepared = [_source_run(tmp_path, name) for name in ("concepts", "exceptions")]
    bundle = prepare_source_bundle(tmp_path, [run for run, _ in prepared])
    extractions = load_bundle_extractions(tmp_path, bundle)
    candidate = _bundled_kc(bundle, extractions)
    evidence = candidate["leaf_kcs"][0]["source_evidence"][0]
    if bad_ref == "source":
        evidence["source_id"] = "sha256:unknown"
    elif bad_ref == "page":
        evidence["page"] = extractions[bundle.sources[0].source.source_id].source.page_count + 1
    else:
        evidence["block_ids"] = ["unknown-block"]

    with pytest.raises(
        ValueError,
        match="without matching source_evidence|blocks outside",
    ):
        parsed = SourceBundleKCSet.model_validate(candidate)
        parsed.validate_against_bundle(bundle, extractions)


def test_bundle_hash_binds_source_order(tmp_path) -> None:
    prepared = [_source_run(tmp_path, name) for name in ("concepts", "exceptions")]
    runs = [run for run, _ in prepared]
    forward = prepare_source_bundle(tmp_path, runs)
    reverse = prepare_source_bundle(tmp_path, list(reversed(runs)))

    assert reverse.bundle_sha256 != forward.bundle_sha256
    assert [entry.source.source_id for entry in reverse.sources] == list(
        reversed([entry.source.source_id for entry in forward.sources])
    )


@pytest.mark.parametrize("artifact", ["source_manifest_ref", "extraction_ref", "source_pdf"])
def test_bundle_invalidates_changed_bound_source_artifacts(tmp_path, artifact) -> None:
    run, _ = _source_run(tmp_path, "concepts")
    bundle = prepare_source_bundle(tmp_path, [run])
    entry = bundle.sources[0]
    bound_path = (
        run / "source.pdf" if artifact == "source_pdf" else tmp_path / getattr(entry, artifact)
    )
    bound_path.write_bytes(bound_path.read_bytes() + b"\n")
    changed = "source manifest changed|Extraction changed|source PDF changed"
    with pytest.raises(ValueError, match=changed):
        load_source_bundle(tmp_path)


def test_bundle_candidate_hash_cannot_be_reused_for_another_source_order(tmp_path) -> None:
    prepared = [_source_run(tmp_path, name) for name in ("concepts", "exceptions")]
    runs = [run for run, _ in prepared]
    forward = prepare_source_bundle(tmp_path, runs)
    extractions = load_bundle_extractions(tmp_path, forward)
    candidate = SourceBundleKCSet.model_validate(_bundled_kc(forward, extractions))
    reverse = prepare_source_bundle(tmp_path, list(reversed(runs)))

    with pytest.raises(ValueError, match="bundle SHA-256"):
        candidate.validate_against_bundle(reverse, extractions)


def test_bundle_model_rejects_tampered_manifest_digest(tmp_path) -> None:
    run, _ = _source_run(tmp_path, "concepts")
    prepare_source_bundle(tmp_path, [run])
    manifest = tmp_path / "source-bundle.json"
    value = deepcopy(json.loads(manifest.read_text()))
    value["bundle_sha256"] = "0" * 64
    write_json(manifest, value)

    with pytest.raises(ValueError, match="ordered contents"):
        load_source_bundle(tmp_path)
