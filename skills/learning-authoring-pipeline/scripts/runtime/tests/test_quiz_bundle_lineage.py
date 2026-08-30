from __future__ import annotations

import copy
import hashlib
import json

import pytest
from pydantic import ValidationError

from learning_authoring.authoring_context import prepare_bundle_authoring_context
from learning_authoring.quiz import QuizConfig, build_quiz_input
from learning_authoring.quiz_contracts import QuizBatchV3, QuizSourceRef
from learning_authoring.quiz_semantics import (
    CRITERIA,
    QuizSemanticAudit,
    semantic_audit_summary,
    validate_semantic_audit,
)
from learning_authoring.source_bundle import (
    SourceBundleKCSet,
    bundle_kc_source_ref,
    load_bundle_extractions,
    prepare_source_bundle,
)
from tests.test_quiz_adaptive import adaptive_output
from tests.test_source_bundle import _bundled_kc, _source_run

BUNDLE_SHA256 = "d" * 64
KC_SET_SHA256 = "b" * 64


def _bundle_kc_set(source) -> SourceBundleKCSet:
    other_source_id = "sha256:" + "e" * 16
    return SourceBundleKCSet.model_validate(
        {
            "source_ref": {
                "schema_version": "source-bundle.v1",
                "source_bundle_sha256": BUNDLE_SHA256,
                "authoring_context_sha256": None,
            },
            "source_summary": "A shared KC set over two independent PDF sources.",
            "page_audit": [
                {
                    "source_id": source.source_id,
                    "page": 1,
                    "classification": "learning_content",
                    "summary": "First source concept.",
                    "kc_ids": ["KC-001"],
                    "source_block_ids": ["b1"],
                    "warning_codes": [],
                },
                {
                    "source_id": other_source_id,
                    "page": 1,
                    "classification": "learning_content",
                    "summary": "Second source principle.",
                    "kc_ids": ["KC-002"],
                    "source_block_ids": ["other-b1"],
                    "warning_codes": [],
                },
            ],
            "kc_groups": [
                {
                    "group_id": "KCG-001",
                    "name": "Shared group",
                    "description": "Knowledge supported across the source bundle.",
                    "leaf_kc_ids": ["KC-001", "KC-002"],
                }
            ],
            "leaf_kcs": [
                {
                    "kc_id": "KC-001",
                    "group_id": "KCG-001",
                    "name": "First KC",
                    "semantic_form": "concept",
                    "knowledge_description": "Knowledge one.",
                    "observable_claim": "Learner distinguishes the first concept.",
                    "assessment_boundary": {"included": ["first"], "excluded": []},
                    "source_evidence": [
                        {
                            "evidence_id": "EVD-001",
                            "source_id": source.source_id,
                            "page": 1,
                            "block_ids": ["b1"],
                            "description": "First source evidence.",
                            "supports": "The first observable claim.",
                        }
                    ],
                    "context_evidence": [],
                    "warning_codes": [],
                    "status": "PROPOSED",
                },
                {
                    "kc_id": "KC-002",
                    "group_id": "KCG-001",
                    "name": "Second KC",
                    "semantic_form": "principle",
                    "knowledge_description": "Knowledge two.",
                    "observable_claim": "Learner applies the second principle.",
                    "assessment_boundary": {"included": ["second"], "excluded": []},
                    "source_evidence": [
                        {
                            "evidence_id": "EVD-002",
                            "source_id": other_source_id,
                            "page": 1,
                            "block_ids": ["other-b1"],
                            "description": "Second source evidence.",
                            "supports": "The second observable claim.",
                        }
                    ],
                    "context_evidence": [],
                    "warning_codes": [],
                    "status": "PROPOSED",
                },
            ],
            "uncovered_content": [],
            "generation_warnings": [],
            "context_audit": [],
        }
    )


def _bundle_quiz(source) -> dict:
    kcs = _bundle_kc_set(source)
    raw = adaptive_output(source, (("KC-001", 1), ("KC-002", 1)))
    raw["source_ref"] = {
        "extraction_source_id": None,
        "extraction_source_sha256": None,
        "source_bundle_sha256": BUNDLE_SHA256,
        "kc_set_sha256": KC_SET_SHA256,
        "authoring_context_sha256": None,
    }
    evidence_by_kc = {
        kc.kc_id: kc.source_evidence[0].model_dump(mode="json") for kc in kcs.leaf_kcs
    }
    for question in raw["questions"]:
        evidence = evidence_by_kc[question["kc_id"]]
        question["evidence_refs"] = [
            {
                "source_id": evidence["source_id"],
                "page": evidence["page"],
                "block_ids": evidence["block_ids"],
            }
        ]
    return raw


def _semantic_source_ref(quiz: dict) -> dict:
    return {
        "quiz_sha256": hashlib.sha256(
            json.dumps(quiz, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "kc_set_sha256": KC_SET_SHA256,
        "source_sha256": None,
        "source_bundle_sha256": BUNDLE_SHA256,
        "authoring_context_sha256": None,
        "review_input_sha256": "f" * 64,
    }


def _semantic_report(quiz: dict) -> dict:
    return {
        "schema_version": "quiz-semantic-audit.v1",
        "source_ref": _semantic_source_ref(quiz),
        "reviewer": {"mode": "independent", "label": "test-reviewer", "model": None},
        "scope": {
            "source_coverage": "complete",
            "checked_source_pages": sorted(
                [
                    {"source_id": ref["source_id"], "page": ref["page"]}
                    for question in quiz["questions"]
                    for ref in question["evidence_refs"]
                ],
                key=lambda item: (item["source_id"], item["page"]),
            ),
            "checked_context_ids": [],
            "limitations": [],
        },
        "questions": [
            {
                "question_id": question["question_id"],
                "kc_id": question["kc_id"],
                "slot_id": question["slot_id"],
                "independent_answer": "A bounded synthetic answer.",
                **{
                    criterion: {
                        "verdict": "PASS",
                        "rationale": f"Synthetic {criterion} result for contract testing.",
                        "issues": [],
                    }
                    for criterion in CRITERIA
                },
            }
            for question in quiz["questions"]
        ],
    }


def test_bundle_quiz_input_and_output_preserve_source_qualified_lineage(source) -> None:
    kcs = _bundle_kc_set(source)
    payload = build_quiz_input(
        kcs,
        kc_set_sha256=KC_SET_SHA256,
        config=QuizConfig(include_all_kcs=True),
    )
    assert payload["source_ref"] == {
        "source_bundle_sha256": BUNDLE_SHA256,
        "kc_set_sha256": KC_SET_SHA256,
        "authoring_context_sha256": None,
    }
    assert {
        evidence["source_id"] for kc in payload["leaf_kcs"] for evidence in kc["source_evidence"]
    } == {source.source_id, "sha256:" + "e" * 16}
    batch = QuizBatchV3.model_validate(_bundle_quiz(source))
    batch.validate_against_input(payload)


@pytest.mark.parametrize("mutation", ["missing_source", "wrong_source", "stale_bundle"])
def test_bundle_quiz_lineage_fails_closed(source, mutation) -> None:
    payload = build_quiz_input(
        _bundle_kc_set(source),
        kc_set_sha256=KC_SET_SHA256,
        config=QuizConfig(include_all_kcs=True),
    )
    raw = _bundle_quiz(source)
    if mutation == "missing_source":
        raw["questions"][0]["evidence_refs"][0].pop("source_id")
        with pytest.raises(ValidationError, match="requires source_id"):
            QuizBatchV3.model_validate(raw)
        return
    if mutation == "wrong_source":
        raw["questions"][0]["evidence_refs"][0]["source_id"] = "sha256:" + "e" * 16
    else:
        raw["source_ref"]["source_bundle_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source_ref|outside its KC"):
        QuizBatchV3.model_validate(raw).validate_against_input(payload)


def test_legacy_quiz_source_ref_roundtrips_without_bundle_field(source) -> None:
    raw = {
        "extraction_source_id": source.source_id,
        "extraction_source_sha256": source.sha256,
        "kc_set_sha256": KC_SET_SHA256,
    }
    assert QuizSourceRef.model_validate(raw).model_dump(mode="json") == raw


def test_bundle_context_page_mapping_is_source_qualified_and_preserved(source) -> None:
    raw = _bundle_kc_set(source).model_dump(mode="json")
    raw["source_ref"]["authoring_context_sha256"] = "c" * 64
    raw["leaf_kcs"][0]["context_evidence"] = [
        {
            "context_id": "CTX-001",
            "excerpt": "An instructor note.",
            "description": None,
            "supports": "A bounded supplementary claim.",
            "source_id": source.source_id,
            "pages": [1],
            "mapping_method": "explicit_page_reference",
            "mapping_confidence": "high",
        }
    ]
    payload = build_quiz_input(
        SourceBundleKCSet.model_validate(raw),
        kc_set_sha256=KC_SET_SHA256,
        config=QuizConfig(include_all_kcs=True),
    )
    assert payload["leaf_kcs"][0]["context_evidence"][0]["source_id"] == (source.source_id)

    quiz = _bundle_quiz(source)
    quiz["source_ref"]["authoring_context_sha256"] = "c" * 64
    quiz["questions"][0]["context_evidence_refs"] = [
        {
            "context_id": "CTX-001",
            "excerpt": "An instructor note.",
            "description": None,
            "source_id": source.source_id,
            "pages": [1],
        }
    ]
    QuizBatchV3.model_validate(quiz).validate_against_input(payload)


@pytest.mark.parametrize("mutation", ["missing_source", "wrong_source", "document_source"])
def test_bundle_context_mapping_fails_closed(source, mutation) -> None:
    raw = _bundle_kc_set(source).model_dump(mode="json")
    raw["source_ref"]["authoring_context_sha256"] = "c" * 64
    raw["leaf_kcs"][0]["context_evidence"] = [
        {
            "context_id": "CTX-001",
            "excerpt": "An instructor note.",
            "description": None,
            "supports": "A bounded supplementary claim.",
            "source_id": source.source_id,
            "pages": [1],
            "mapping_method": "explicit_page_reference",
            "mapping_confidence": "high",
        }
    ]
    if mutation == "missing_source":
        raw["leaf_kcs"][0]["context_evidence"][0]["source_id"] = None
        with pytest.raises(ValueError, match="requires source_id"):
            build_quiz_input(
                SourceBundleKCSet.model_validate(raw),
                kc_set_sha256=KC_SET_SHA256,
                config=QuizConfig(include_all_kcs=True),
            )
        return
    if mutation == "wrong_source":
        raw["leaf_kcs"][0]["context_evidence"][0]["source_id"] = "unknown-source"
        with pytest.raises(ValueError, match="unknown source page"):
            build_quiz_input(
                SourceBundleKCSet.model_validate(raw),
                kc_set_sha256=KC_SET_SHA256,
                config=QuizConfig(include_all_kcs=True),
            )
        return
    raw["leaf_kcs"][0]["context_evidence"][0].update(
        source_id=source.source_id,
        pages=[],
        mapping_method="document_level",
    )
    with pytest.raises(ValidationError, match="must not name a source"):
        SourceBundleKCSet.model_validate(raw)


def test_bundle_quiz_input_preserves_current_context_and_source_bundle(tmp_path) -> None:
    prepared = [_source_run(tmp_path, name) for name in ("concepts", "exceptions")]
    bundle = prepare_source_bundle(tmp_path, [run for run, _ in prepared])
    context = prepare_bundle_authoring_context(
        tmp_path, bundle, context_texts=("A separate lecturer qualification.",)
    )
    assert context is not None
    extractions = load_bundle_extractions(tmp_path, bundle)
    raw = _bundled_kc(bundle, extractions)
    raw["source_ref"] = bundle_kc_source_ref(
        bundle, authoring_context_sha256=context.sha256
    ).model_dump(mode="json")
    raw["leaf_kcs"][0]["context_evidence"] = [
        {
            "context_id": "CTX-001",
            "excerpt": "A separate lecturer qualification.",
            "description": None,
            "supports": "A bounded supplementary qualification.",
            "source_id": bundle.sources[0].source.source_id,
            "pages": [1],
            "mapping_method": "explicit_page_reference",
            "mapping_confidence": "high",
        }
    ]
    raw["context_audit"] = [
        {
            "context_id": "CTX-001",
            "excerpt": "A separate lecturer qualification.",
            "description": None,
            "claim": "Retain the qualification.",
            "disposition": "represented",
            "kc_ids": ["KC-001"],
            "reason": "It bounds the shared concept.",
        }
    ]
    quiz_input = build_quiz_input(
        SourceBundleKCSet.model_validate(raw),
        raw_kc_set=raw,
        kc_set_sha256=KC_SET_SHA256,
        config=QuizConfig(include_all_kcs=True),
    )
    assert quiz_input["source_ref"] == {
        "source_bundle_sha256": bundle.bundle_sha256,
        "kc_set_sha256": KC_SET_SHA256,
        "authoring_context_sha256": context.sha256,
    }
    context_evidence = quiz_input["leaf_kcs"][0]["context_evidence"][0]
    assert context_evidence["source_id"] == bundle.sources[0].source.source_id
    assert context_evidence["pages"] == [1]


def test_bundle_semantic_review_uses_source_qualified_scope_and_snapshots(source) -> None:
    quiz = _bundle_quiz(source)
    report = _semantic_report(quiz)
    snapshots = {
        "extraction": {
            source.source_id: {"pages": [{"page_number": 1, "blocks": []}]},
            "sha256:" + "e" * 16: {"pages": [{"page_number": 1, "blocks": []}]},
        }
    }
    parsed = validate_semantic_audit(
        report,
        quiz=quiz,
        expected_source_ref=_semantic_source_ref(quiz),
        artifacts=snapshots,
    )
    assert (
        semantic_audit_summary(parsed, quiz=quiz, expected_source_ref=_semantic_source_ref(quiz))[
            "status"
        ]
        == "PASS"
    )


@pytest.mark.parametrize(
    "mutation", ["ambiguous_scope", "stale_bundle", "ambiguous_issue", "unknown_source"]
)
def test_bundle_semantic_lineage_fails_closed(source, mutation) -> None:
    quiz = _bundle_quiz(source)
    report = _semantic_report(quiz)
    snapshots = {
        "extraction": {
            source.source_id: {
                "pages": [
                    {
                        "page_number": 1,
                        "blocks": [{"content": "A bounded source statement."}],
                    }
                ]
            },
            "sha256:" + "e" * 16: {"pages": [{"page_number": 1, "blocks": []}]},
        }
    }
    if mutation == "ambiguous_scope":
        report["scope"]["checked_source_pages"] = [1]
        with pytest.raises(ValidationError, match="require source_id"):
            QuizSemanticAudit.model_validate(report)
        return
    if mutation == "stale_bundle":
        expected = _semantic_source_ref(quiz)
        expected["source_bundle_sha256"] = "0" * 64
        with pytest.raises(ValueError, match="frozen review input"):
            validate_semantic_audit(report, quiz=quiz, expected_source_ref=expected)
        return

    criterion = report["questions"][0]["grounding"]
    criterion.update(
        verdict="REVIEW",
        issues=[
            {
                "stage": "extraction",
                "observation": "The statement needs source inspection.",
                "locators": [
                    {
                        "artifact": "extraction",
                        "source_id": (
                            None if mutation == "ambiguous_issue" else "sha256:" + "9" * 16
                        ),
                        "pointer": "/pages/0/blocks/0/content",
                        "quote": None,
                    }
                ],
            }
        ],
    )
    if mutation == "ambiguous_issue":
        with pytest.raises(ValidationError, match="require source_id"):
            QuizSemanticAudit.model_validate(report)
    else:
        with pytest.raises(ValueError, match="unknown bundle source"):
            validate_semantic_audit(
                report,
                quiz=quiz,
                expected_source_ref=_semantic_source_ref(quiz),
                artifacts=snapshots,
            )


def test_bundle_semantic_extraction_locator_resolves_inside_named_source(source) -> None:
    quiz = _bundle_quiz(source)
    report = _semantic_report(quiz)
    report["questions"][0]["grounding"] = {
        "verdict": "REVIEW",
        "rationale": "The exact wording needs review.",
        "issues": [
            {
                "stage": "extraction",
                "observation": "The source wording is bounded.",
                "locators": [
                    {
                        "artifact": "extraction",
                        "source_id": source.source_id,
                        "pointer": "/pages/0/blocks/0/content",
                        "quote": "bounded source",
                    }
                ],
            }
        ],
    }
    snapshots = {
        "extraction": {
            source.source_id: {
                "pages": [
                    {
                        "page_number": 1,
                        "blocks": [{"content": "A bounded source statement."}],
                    }
                ]
            },
            "sha256:" + "e" * 16: {"pages": [{"page_number": 1, "blocks": []}]},
        }
    }
    validate_semantic_audit(
        report,
        quiz=copy.deepcopy(quiz),
        expected_source_ref=_semantic_source_ref(quiz),
        artifacts=snapshots,
    )
