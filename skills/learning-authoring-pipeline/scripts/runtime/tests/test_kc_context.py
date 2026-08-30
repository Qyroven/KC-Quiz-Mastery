from __future__ import annotations

import json
from pathlib import Path

import pytest

from learning_authoring.authoring_context import prepare_authoring_context
from learning_authoring.kc_contracts import KCContextEvidence, ProposedKCSet
from tests.conftest import payload


def _candidate(source, *, context=None, context_only=False) -> dict:
    result = {
        "source_ref": {
            "schema_version": "extracted-source.v2",
            "source_id": source.source_id,
            "source_sha256": source.sha256,
        },
        "source_summary": "The PDF and separately attributed optional lecture context.",
        "page_audit": [
            {
                "page": index,
                "classification": "learning_content",
                "summary": f"PDF page {index}",
                "kc_ids": [] if context_only else ["KC-001"],
                "source_block_ids": [f"b{index}"],
            }
            for index in range(1, source.page_count + 1)
        ],
        "kc_groups": [{
            "group_id": "KCG-001",
            "name": "Group",
            "description": "A concept group",
            "leaf_kc_ids": ["KC-001"],
        }],
        "leaf_kcs": [{
            "kc_id": "KC-001",
            "group_id": "KCG-001",
            "name": "Choosing an exception",
            "semantic_form": "decision_rule",
            "knowledge_description": "The exception and its condition.",
            "observable_claim": "Given the condition, select the exception.",
            "assessment_boundary": {"included": ["Exception choice"], "excluded": []},
            "source_evidence": [] if context_only else [{
                "evidence_id": "EVD-001",
                "page": 1,
                "block_ids": ["b1"],
                "description": "Visible PDF content",
                "supports": "The base concept",
            }],
            "status": "PROPOSED",
        }],
        "uncovered_content": [],
        "generation_warnings": [],
    }
    if context is not None:
        result["source_ref"]["authoring_context_sha256"] = context.sha256
        result["leaf_kcs"][0]["context_evidence"] = [{
            "context_id": context.items[0].context_id,
            "excerpt": "Focus on the exception",
            "supports": "The lecturer's emphasis on choosing exceptions",
            "pages": [],
            "mapping_method": "document_level",
            "mapping_confidence": "high",
        }]
    return result


def _context(tmp_path, source):
    result = prepare_authoring_context(
        tmp_path, source,
        context_texts=["Loose lecture note: Focus on the exception. No slide number."],
    )
    assert result is not None
    return result


def test_legacy_pdf_only_kcs_still_validate(source) -> None:
    candidate = _candidate(source)
    proposed = ProposedKCSet.model_validate(candidate)
    proposed.validate_against_source(payload().with_source(source))
    assert proposed.source_ref.authoring_context_sha256 is None
    assert proposed.leaf_kcs[0].context_evidence == []
    assert "context_evidence" not in candidate["leaf_kcs"][0]


@pytest.mark.parametrize("context_only", [False, True])
def test_context_evidence_can_supplement_or_independently_ground_a_kc(
    tmp_path, source, context_only
) -> None:
    context = _context(tmp_path, source)
    proposed = ProposedKCSet.model_validate(
        _candidate(source, context=context, context_only=context_only)
    )
    proposed.validate_against_source(payload().with_source(source), authoring_context=context)

    assert proposed.leaf_kcs[0].context_evidence[0].pages == []
    assert bool(proposed.leaf_kcs[0].source_evidence) is not context_only


def test_note_only_kc_cannot_drop_all_evidence(source) -> None:
    candidate = _candidate(source, context_only=True)
    with pytest.raises(ValueError, match="requires PDF source_evidence or valid context_evidence"):
        ProposedKCSet.model_validate(candidate)


@pytest.mark.parametrize("hash_value", [None, "0" * 64])
def test_context_hash_cannot_be_dropped_or_changed(tmp_path, source, hash_value) -> None:
    context = _context(tmp_path, source)
    candidate = _candidate(source, context=context)
    candidate["source_ref"]["authoring_context_sha256"] = hash_value
    proposed = ProposedKCSet.model_validate(candidate)

    with pytest.raises(ValueError, match="context SHA-256 is missing or does not match"):
        proposed.validate_against_source(payload().with_source(source), authoring_context=context)


def test_unused_context_still_has_to_bind_the_kc_lineage(tmp_path, source) -> None:
    context = _context(tmp_path, source)
    proposed = ProposedKCSet.model_validate(_candidate(source))
    with pytest.raises(ValueError, match="context SHA-256 is missing"):
        proposed.validate_against_source(payload().with_source(source), authoring_context=context)

    bound = _candidate(source)
    bound["source_ref"]["authoring_context_sha256"] = context.sha256
    ProposedKCSet.model_validate(bound).validate_against_source(
        payload().with_source(source), authoring_context=context
    )


def test_context_citations_require_the_bound_context_package(tmp_path, source) -> None:
    context = _context(tmp_path, source)
    proposed = ProposedKCSet.model_validate(_candidate(source, context=context))
    with pytest.raises(ValueError, match="requires the bound authoring context"):
        proposed.validate_against_source(payload().with_source(source))


def test_citations_cannot_evade_context_binding_by_omitting_the_hash(tmp_path, source) -> None:
    candidate = _candidate(source, context=_context(tmp_path, source))
    del candidate["source_ref"]["authoring_context_sha256"]
    with pytest.raises(ValueError, match="requires the bound authoring context"):
        ProposedKCSet.model_validate(candidate).validate_against_source(payload().with_source(source))


def test_context_from_a_different_pdf_is_rejected_even_with_its_hash(tmp_path, source) -> None:
    different = source.model_copy(update={"sha256": "b" * 64})
    context = _context(tmp_path, different)
    proposed = ProposedKCSet.model_validate(_candidate(source, context=context))
    with pytest.raises(ValueError, match="context source SHA-256"):
        proposed.validate_against_source(payload().with_source(source), authoring_context=context)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"context_id": "CTX-999"}, "unknown context_id"),
        ({"excerpt": "The note never says this"}, "not an exact quote"),
        ({"excerpt": None, "description": "Paraphrase"}, "not an exact quote"),
        ({"pages": [3], "mapping_method": "semantic_alignment"}, "unknown PDF page"),
    ],
)
def test_context_citation_identity_quotes_and_pages_are_validated(
    tmp_path, source, changes, message
) -> None:
    context = _context(tmp_path, source)
    candidate = _candidate(source, context=context, context_only=True)
    candidate["leaf_kcs"][0]["context_evidence"][0].update(changes)
    proposed = ProposedKCSet.model_validate(candidate)
    with pytest.raises(ValueError, match=message):
        proposed.validate_against_source(payload().with_source(source), authoring_context=context)


@pytest.mark.parametrize(
    "changes",
    [
        {"context_id": "b1"},
        {"excerpt": "   "},
        {"supports": "\n\t"},
        {"excerpt": None, "description": None},
        {"pages": [0]},
        {"pages": [True]},
        {"pages": [1, 1]},
        {"pages": [1], "mapping_method": "document_level"},
        {"pages": [], "mapping_method": "explicit_page_reference"},
        {"mapping_method": "unmapped", "mapping_confidence": "high"},
        {"pages": [1], "mapping_method": "semantic_alignment", "mapping_confidence": "unmapped"},
    ],
)
def test_context_evidence_needs_nonempty_content_and_honest_mapping(changes) -> None:
    evidence = {
        "context_id": "CTX-001",
        "excerpt": "Some exact note",
        "supports": "Some claim",
        "pages": [],
        "mapping_method": "document_level",
        "mapping_confidence": "high",
        **changes,
    }
    with pytest.raises(ValueError):
        KCContextEvidence.model_validate(evidence)


def test_semantic_mapping_does_not_create_pdf_block_evidence(tmp_path, source) -> None:
    context = _context(tmp_path, source)
    candidate = _candidate(source, context=context, context_only=True)
    evidence = candidate["leaf_kcs"][0]["context_evidence"][0]
    evidence.update(pages=[2], mapping_method="semantic_alignment", mapping_confidence="low")
    proposed = ProposedKCSet.model_validate(candidate)
    proposed.validate_against_source(payload().with_source(source), authoring_context=context)
    assert proposed.leaf_kcs[0].source_evidence == []


def test_adding_context_does_not_make_fabricated_pdf_blocks_valid(tmp_path, source) -> None:
    context = _context(tmp_path, source)
    candidate = _candidate(source, context=context)
    candidate["leaf_kcs"][0]["source_evidence"][0]["block_ids"] = ["CTX-001"]
    proposed = ProposedKCSet.model_validate(candidate)
    with pytest.raises(ValueError, match="outside page 1"):
        proposed.validate_against_source(payload().with_source(source), authoring_context=context)


def test_attachment_observation_is_distinct_from_an_unverified_quote(tmp_path, source) -> None:
    image = tmp_path / "lecturer-diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x00attachment-test")
    context = prepare_authoring_context(tmp_path / "run", source, context_files=[image])
    assert context is not None
    candidate = _candidate(source, context=context, context_only=True)
    evidence = candidate["leaf_kcs"][0]["context_evidence"][0]
    evidence.update(excerpt=None, description="The lecturer diagram shows two alternative paths.")
    proposed = ProposedKCSet.model_validate(candidate)
    proposed.validate_against_source(payload().with_source(source), authoring_context=context)

    evidence["excerpt"] = "An invented transcript"
    with pytest.raises(ValueError, match="inspection description"):
        ProposedKCSet.model_validate(candidate).validate_against_source(
            payload().with_source(source), authoring_context=context
        )


def test_previously_valid_kc_cannot_be_rebound_after_context_changes(tmp_path, source) -> None:
    context = _context(tmp_path, source)
    proposed = ProposedKCSet.model_validate(_candidate(source, context=context))
    changed = prepare_authoring_context(
        tmp_path, source, context_texts=["Different authoring intent"]
    )
    assert changed is not None and changed.sha256 != context.sha256
    with pytest.raises(ValueError, match="context SHA-256"):
        proposed.validate_against_source(payload().with_source(source), authoring_context=changed)


def test_static_kc_schema_matches_runtime_contract() -> None:
    path = (
        Path(__file__).parents[1]
        / "learning_authoring" / "prompts" / "kc-v1" / "output.schema.json"
    )
    static = json.loads(path.read_text())
    assert static.pop("$schema") == "https://json-schema.org/draft/2020-12/schema"
    assert static == ProposedKCSet.model_json_schema()
    assert "authoring_context_sha256" not in static["$defs"]["KCSourceRef"]["required"]
    assert "context_evidence" not in static["$defs"]["LeafKC"]["required"]


def test_kc_prompts_keep_optional_freeform_context_out_of_extraction() -> None:
    prompts = Path(__file__).parents[1] / "learning_authoring" / "prompts" / "kc-v1"
    rulebook = (prompts / "rulebook.md").read_text()
    task = (prompts / "task.md").read_text()
    assert "optional lecturer context" in task
    assert "supplementary context" in rulebook
    assert "never becomes PDF evidence" in rulebook
    assert "Never invent a page map" in rulebook
    assert "Copy `input_boundary.expected_source_ref` exactly" in task
