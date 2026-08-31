from __future__ import annotations

import copy
import hashlib
import json

import pytest
from pydantic import ValidationError

from learning_authoring.quiz_contracts import QuizBatch
from learning_authoring.quiz_semantics import (
    CRITERIA,
    QuizSemanticAudit,
    load_semantic_review_prompt_package,
    semantic_audit_summary,
    semantic_review_schema,
    validate_semantic_audit,
)
from tests.test_quiz import quiz_output as legacy_quiz_output


def quiz_output(source, *, variants: int = 1) -> dict:
    payload = legacy_quiz_output(source, variants=variants)
    for question in payload["questions"]:
        question.update(
            hints=[{"hint_id": "next-step", "kind": "cue", "text": "Recall the relevant rule."}],
            hint_absence_reason=None,
        )
    return payload


def _source_ref(quiz: dict) -> dict:
    return {
        "quiz_sha256": hashlib.sha256(json.dumps(quiz).encode()).hexdigest(),
        "kc_set_sha256": quiz["source_ref"]["kc_set_sha256"],
        "source_sha256": quiz["source_ref"]["extraction_source_sha256"],
        "authoring_context_sha256": quiz["source_ref"].get("authoring_context_sha256"),
        "review_input_sha256": "f" * 64,
    }


def _report(quiz: dict) -> dict:
    """Synthetic judgments test mechanics; they do not certify fixture quality."""
    return {
        "schema_version": "quiz-semantic-audit.v1",
        "source_ref": _source_ref(quiz),
        "reviewer": {"mode": "independent", "label": "test-reviewer", "model": None},
        "scope": {
            "source_coverage": "complete",
            "checked_source_pages": sorted(
                {ref["page"] for q in quiz["questions"] for ref in q.get("evidence_refs", [])}
            ),
            "checked_context_ids": sorted(
                {
                    ref["context_id"]
                    for q in quiz["questions"]
                    for ref in q.get("context_evidence_refs", [])
                }
            ),
            "limitations": [],
        },
        "questions": [
            {
                "question_id": q["question_id"],
                "kc_id": q["kc_id"],
                "slot_id": q.get("slot_id"),
                "independent_answer": "Option B: bounded synthetic answer.",
                **{
                    criterion: {
                        "verdict": "PASS",
                        "rationale": f"Synthetic {criterion} check; not a semantic test result.",
                        "issues": [],
                    }
                    for criterion in CRITERIA
                },
            }
            for q in quiz["questions"]
        ],
    }


def _issue(*, artifact: str = "quiz", pointer: str = "/questions/0/prompt") -> dict:
    return {
        "stage": "quiz",
        "observation": "The named response cannot be resolved from the stated premise.",
        "locators": [{"artifact": artifact, "pointer": pointer, "quote": None}],
    }


def _flag(report: dict, criterion: str, verdict: str, *, index: int = 0) -> None:
    report["questions"][index][criterion] = {
        "verdict": verdict,
        "rationale": "A concrete source-dependent concern requires resolution.",
        "issues": [_issue(pointer=f"/questions/{index}/prompt")],
    }


def test_schema_requires_all_six_checks_without_overall_grade() -> None:
    schema = semantic_review_schema()
    question = schema["$defs"]["SemanticQuestionReview"]
    assert set(CRITERIA) <= set(question["required"])
    assert "independent_answer" in question["required"]
    assert "overall_verdict" not in schema["properties"]
    assert "score" not in schema["properties"]

    def strict_objects(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                strict_objects(child)
        elif isinstance(node, list):
            for child in node:
                strict_objects(child)

    strict_objects(schema)


def test_review_dynamic_coverage_and_order_do_not_depend_on_kc_or_question_count(source) -> None:
    quiz = quiz_output(source, variants=115)
    report = _report(quiz)
    report["questions"].reverse()
    raw = json.dumps(report, indent=3).encode() + b" \n"
    raw_before, quiz_before, report_before = raw[:], copy.deepcopy(quiz), copy.deepcopy(report)
    validated = validate_semantic_audit(
        raw,
        quiz=QuizBatch.model_validate(quiz),
        expected_source_ref=_source_ref(quiz),
        expected_reviewer=report["reviewer"],
    )
    summary = semantic_audit_summary(validated, quiz=quiz, expected_source_ref=_source_ref(quiz))
    assert summary["status"] == "PASS"
    assert summary["counts"]["PASS"] == 115
    assert summary["questions"][0]["question_id"] == "Q-001"
    assert validated.questions[0].question_id == "Q-115"  # Artifact order was not rewritten.
    assert summary["initial_check_only"] and summary["human_approved"] is False
    assert raw == raw_before and quiz == quiz_before and report == report_before


@pytest.mark.parametrize("missing", ["grounding", "hints", "independent_answer", "slot_id"])
def test_cannot_skip_a_check_or_solution(source, missing) -> None:
    quiz = quiz_output(source)
    report = _report(quiz)
    report["questions"][0].pop(missing)
    with pytest.raises(ValidationError):
        validate_semantic_audit(report, quiz=quiz, expected_source_ref=_source_ref(quiz))
    assert semantic_audit_summary(report, quiz=quiz)["status"] == "NOT_REVIEWED"


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "wrong_kc", "wrong_slot"])
def test_exact_item_identity_is_bound(source, change) -> None:
    quiz = quiz_output(source, variants=2)
    report = _report(quiz)
    if change == "missing":
        report["questions"].pop()
    elif change == "extra":
        extra = copy.deepcopy(report["questions"][0])
        extra["question_id"] = "Q-999"
        report["questions"].append(extra)
    elif change == "duplicate":
        report["questions"].append(copy.deepcopy(report["questions"][0]))
    elif change == "wrong_kc":
        report["questions"][0]["kc_id"] = "KC-999"
    else:
        report["questions"][0]["slot_id"] = "invented-slot"
    with pytest.raises(ValueError):
        validate_semantic_audit(report, quiz=quiz, expected_source_ref=_source_ref(quiz))
    summary = semantic_audit_summary(report, quiz=quiz, expected_source_ref=_source_ref(quiz))
    assert summary["status"] in {"NOT_REVIEWED", "STALE"}
    assert summary["counts"]["PASS"] == 0


@pytest.mark.parametrize(
    "hash_field",
    list(
        _source_ref(
            {
                "source_ref": {
                    "kc_set_sha256": "a" * 64,
                    "extraction_source_sha256": "b" * 64,
                }
            }
        )
    ),
)
def test_every_hash_change_invalidates_audit(source, hash_field) -> None:
    quiz = quiz_output(source)
    report = _report(quiz)
    changed = _source_ref(quiz)
    changed[hash_field] = "0" * 64
    with pytest.raises(ValueError, match="frozen review input"):
        validate_semantic_audit(report, quiz=quiz, expected_source_ref=changed)
    summary = semantic_audit_summary(report, quiz=quiz, expected_source_ref=changed)
    assert summary["status"] == "STALE"
    assert summary["questions"][0] == {
        "question_id": "Q-001",
        "kc_id": "KC-001",
        "slot_id": None,
        "status": "STALE",
    }


def test_expected_refs_cannot_override_quiz_lineage(source) -> None:
    quiz = quiz_output(source)
    report = _report(quiz)
    report["source_ref"]["kc_set_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="does not match the frozen Quiz"):
        validate_semantic_audit(report, quiz=quiz, expected_source_ref=report["source_ref"])


@pytest.mark.parametrize(
    "expected",
    [
        "self_review",
        {
            "mode": "self_review",
            "label": "generator",
            "model": None,
        },
    ],
)
def test_host_mode_cannot_be_self_upgraded_to_independent(source, expected) -> None:
    quiz = quiz_output(source)
    with pytest.raises(ValueError, match="host-bound"):
        validate_semantic_audit(
            _report(quiz),
            quiz=quiz,
            expected_source_ref=_source_ref(quiz),
            expected_reviewer=expected,
        )


def test_mode_only_binding_does_not_authenticate_reviewer_label(source) -> None:
    quiz = quiz_output(source)
    report = _report(quiz)
    report["reviewer"]["label"] = "A host-recorded new review context"
    validated = validate_semantic_audit(
        report, quiz=quiz, expected_source_ref=_source_ref(quiz), expected_reviewer="independent"
    )
    assert validated.reviewer.model is None


@pytest.mark.parametrize("limited", ["self_review", "partial", "unknown", "limitation"])
def test_limitations_never_promote_initial_pass(source, limited) -> None:
    quiz = quiz_output(source, variants=2)
    report = _report(quiz)
    if limited == "self_review":
        report["reviewer"]["mode"] = "self_review"
    else:
        report["scope"]["limitations"] = ["The original chart was not fully legible."]
        if limited in {"partial", "unknown"}:
            report["scope"]["source_coverage"] = limited
    parsed = validate_semantic_audit(report, quiz=quiz, expected_source_ref=_source_ref(quiz))
    summary = semantic_audit_summary(parsed, quiz=quiz, expected_source_ref=_source_ref(quiz))
    assert summary["status"] == "REVIEW" and summary["counts"]["REVIEW"] == 2
    _flag(report, "scoring", "REJECT")
    summary = semantic_audit_summary(report, quiz=quiz, expected_source_ref=_source_ref(quiz))
    assert summary["status"] == "REJECT"
    assert summary["counts"]["REJECT"] == 1 and summary["counts"]["REVIEW"] == 1


def test_verdict_is_worst_criterion_not_vote_or_mean(source) -> None:
    quiz = quiz_output(source, variants=3)
    report = _report(quiz)
    _flag(report, "hints", "REVIEW")
    _flag(report, "answerability", "REJECT", index=1)
    parsed = validate_semantic_audit(report, quiz=quiz, expected_source_ref=_source_ref(quiz))
    summary = semantic_audit_summary(parsed, quiz=quiz, expected_source_ref=_source_ref(quiz))
    assert summary["status"] == "REJECT"
    assert [q["status"] for q in summary["questions"]] == ["REVIEW", "REJECT", "PASS"]


@pytest.mark.parametrize("verdict,issues", [("REVIEW", []), ("REJECT", []), ("PASS", [_issue()])])
def test_issue_required_for_nonpass_and_forbidden_for_pass(source, verdict, issues) -> None:
    quiz = quiz_output(source)
    report = _report(quiz)
    report["questions"][0]["scoring"].update(verdict=verdict, issues=issues)
    with pytest.raises(ValidationError):
        QuizSemanticAudit.model_validate(report)


@pytest.mark.parametrize("pointer", ["not/a/pointer", "/bad~2escape", "/bad~"])
def test_invalid_pointer_syntax_rejected(source, pointer) -> None:
    quiz = quiz_output(source)
    report = _report(quiz)
    _flag(report, "grounding", "REVIEW")
    report["questions"][0]["grounding"]["issues"][0]["locators"][0]["pointer"] = pointer
    with pytest.raises(ValidationError, match="RFC 6901"):
        QuizSemanticAudit.model_validate(report)


@pytest.mark.parametrize(
    "pointer",
    [
        "/questions/9/prompt",
        "/questions/-1/prompt",
        "/questions/00/prompt",
        "/questions/0/prompt/absent",
        "/questions/0/nonexistent",
    ],
)
def test_unresolvable_pointer_is_not_evidence(source, pointer) -> None:
    quiz = quiz_output(source)
    report = _report(quiz)
    _flag(report, "grounding", "REVIEW")
    report["questions"][0]["grounding"]["issues"][0]["locators"][0]["pointer"] = pointer
    with pytest.raises(ValueError, match="does not resolve"):
        validate_semantic_audit(report, quiz=quiz, expected_source_ref=_source_ref(quiz))


def test_evidence_quote_requires_exact_text_at_location(source) -> None:
    quiz = quiz_output(source)
    report = _report(quiz)
    _flag(report, "grounding", "REVIEW")
    locator = report["questions"][0]["grounding"]["issues"][0]["locators"][0]
    locator["quote"] = "best answer"
    validate_semantic_audit(report, quiz=quiz, expected_source_ref=_source_ref(quiz))
    locator["quote"] = "This fabricated quote never appeared."
    with pytest.raises(ValueError, match="quote is not present"):
        validate_semantic_audit(report, quiz=quiz, expected_source_ref=_source_ref(quiz))
    locator.update(quote="Q-001", pointer="/questions/0")
    with pytest.raises(ValueError, match="quote is not present"):
        validate_semantic_audit(report, quiz=quiz, expected_source_ref=_source_ref(quiz))


def test_snapshot_locators_support_source_and_context_without_arbitrary_file_access(source) -> None:
    quiz = quiz_output(source)
    report = _report(quiz)
    _flag(report, "grounding", "REVIEW")
    issue = report["questions"][0]["grounding"]["issues"][0]
    issue["stage"] = "extraction"
    issue["locators"] = [
        {"artifact": "extraction", "pointer": "/pages/0/blocks/0/content", "quote": "ambiguous"},
        {"artifact": "context", "pointer": "/citations/0/a~1b~0c", "quote": "instruction"},
        {"artifact": "quiz", "pointer": "/questions/0/prompt", "quote": None},
    ]
    snapshots = {
        "extraction": {
            "pages": [
                {
                    "page_number": 1,
                    "blocks": [{"content": "An ambiguous source statement."}],
                }
            ]
        },
        "context": {"citations": [{"context_id": "CTX-001", "a/b~c": "An instruction as data."}]},
    }
    validate_semantic_audit(
        report, quiz=quiz, expected_source_ref=_source_ref(quiz), artifacts=snapshots
    )
    with pytest.raises(ValueError, match="missing evidence artifact"):
        validate_semantic_audit(report, quiz=quiz, expected_source_ref=_source_ref(quiz))


def test_cannot_substitute_quiz_snapshot_to_make_false_quote_resolve(source) -> None:
    quiz = quiz_output(source)
    fake_quiz = copy.deepcopy(quiz)
    fake_quiz["questions"][0]["prompt"] = "False premise."
    report = _report(quiz)
    _flag(report, "grounding", "REVIEW")
    report["questions"][0]["grounding"]["issues"][0]["locators"][0]["quote"] = "False premise."
    with pytest.raises(ValueError, match="quote is not present"):
        validate_semantic_audit(
            report, quiz=quiz, expected_source_ref=_source_ref(quiz), artifacts={"quiz": fake_quiz}
        )


def test_source_coverage_is_dynamic_and_only_for_cited_material(source) -> None:
    quiz = quiz_output(source)
    quiz["source_ref"]["authoring_context_sha256"] = "c" * 64
    quiz["questions"][0]["context_evidence_refs"] = [
        {
            "context_id": "CTX-017",
            "excerpt": "Lecturer premise",
            "pages": [],
        }
    ]
    report = _report(quiz)
    source_ref = _source_ref(quiz)
    snapshots = {
        "extraction": {"pages": [{"page_number": 1}, {"page_number": 37}]},
        "context": {"citations": [{"context_id": "CTX-017", "excerpt": "Lecturer premise"}]},
    }
    # No demand to inspect unrelated page 37.
    validate_semantic_audit(report, quiz=quiz, expected_source_ref=source_ref, artifacts=snapshots)
    report["scope"]["checked_context_ids"] = []
    with pytest.raises(ValueError, match="complete scope"):
        validate_semantic_audit(report, quiz=quiz, expected_source_ref=source_ref)
    summary = semantic_audit_summary(report, quiz=quiz, expected_source_ref=source_ref)
    assert summary["status"] == "REVIEW"
    report["scope"].update(
        source_coverage="partial", limitations=["Lecturer annotation could not be inspected."]
    )
    validate_semantic_audit(report, quiz=quiz, expected_source_ref=source_ref)
    report["scope"]["checked_context_ids"] = ["CTX-999"]
    with pytest.raises(ValueError, match="unknown supplied lecturer"):
        validate_semantic_audit(
            report, quiz=quiz, expected_source_ref=source_ref, artifacts=snapshots
        )
    report["scope"]["checked_context_ids"] = []
    report["scope"]["checked_source_pages"] = [999]
    with pytest.raises(ValueError, match="outside the supplied source snapshot"):
        validate_semantic_audit(
            report, quiz=quiz, expected_source_ref=source_ref, artifacts=snapshots
        )


@pytest.mark.parametrize(
    "mutation", ["bool_page", "string_page", "blank", "duplicate_page", "extra"]
)
def test_schema_rejects_coercion_blank_claims_and_extra_fields(source, mutation) -> None:
    report = _report(quiz_output(source))
    if mutation == "bool_page":
        report["scope"]["checked_source_pages"] = [True]
    elif mutation == "string_page":
        report["scope"]["checked_source_pages"] = ["1"]
    elif mutation == "blank":
        report["questions"][0]["independent_answer"] = " \n"
    elif mutation == "duplicate_page":
        report["scope"]["checked_source_pages"] = [1, 1]
    else:
        report["approved"] = True
    with pytest.raises(ValidationError):
        QuizSemanticAudit.model_validate_json(json.dumps(report))


def test_no_audit_or_unverified_projection_never_becomes_pass(source) -> None:
    quiz = quiz_output(source)
    assert semantic_audit_summary(None, quiz=quiz)["status"] == "NOT_REVIEWED"
    assert semantic_audit_summary(None, quiz=quiz)["counts"]["NOT_REVIEWED"] == 1
    assert semantic_audit_summary(_report(quiz))["status"] == "REVIEW"
    assert semantic_audit_summary(_report(quiz), quiz=quiz)["status"] == "REVIEW"


def test_legacy_missing_hint_metadata_caps_only_affected_questions_without_rewriting(
    source,
) -> None:
    quiz = quiz_output(source, variants=3)
    quiz["questions"][0].pop("hints")
    quiz["questions"][0].pop("hint_absence_reason")
    quiz["questions"][2].update(hints=[], hint_absence_reason="No non-answer cue is available.")
    report = _report(quiz)
    before = copy.deepcopy(report)
    validate_semantic_audit(report, quiz=quiz, expected_source_ref=_source_ref(quiz))
    summary = semantic_audit_summary(report, quiz=quiz, expected_source_ref=_source_ref(quiz))
    assert summary["status"] == "REVIEW"
    assert [q["status"] for q in summary["questions"]] == ["REVIEW", "PASS", "PASS"]
    assert "hint metadata" in summary["questions"][0]["status_reasons"][0]
    assert "hints" not in quiz["questions"][0]
    assert report == before and report["questions"][0]["hints"]["verdict"] == "PASS"
    _flag(report, "scoring", "REJECT")
    summary = semantic_audit_summary(report, quiz=quiz, expected_source_ref=_source_ref(quiz))
    assert summary["questions"][0]["status"] == "REJECT"


@pytest.mark.parametrize(
    "fields",
    [
        {"hints": []},
        {"hint_absence_reason": "No useful hint"},
        {"hints": [], "hint_absence_reason": None},
        {"hints": [], "hint_absence_reason": " "},
    ],
)
def test_incomplete_explicit_hint_decision_never_promotes_pass(source, fields) -> None:
    quiz = legacy_quiz_output(source)
    quiz["questions"][0].update(fields)
    summary = semantic_audit_summary(
        _report(quiz), quiz=quiz, expected_source_ref=_source_ref(quiz)
    )
    assert summary["status"] == "REVIEW"


def test_mutating_already_parsed_report_does_not_bypass_validation(source) -> None:
    quiz = quiz_output(source)
    parsed = QuizSemanticAudit.model_validate(_report(quiz))
    parsed.questions[0].hints.rationale = ""
    with pytest.raises(ValueError):
        validate_semantic_audit(parsed, quiz=quiz, expected_source_ref=_source_ref(quiz))


def test_prompt_is_one_scoped_agent_review_not_provider_or_automatic_repair() -> None:
    prompts = load_semantic_review_prompt_package().components
    assert list(prompts) == ["foundation", "rulebook", "task"]
    joined = "\n".join(prompts.values())
    for required in (
        "Solve before",
        "independent answer",
        "six",
        "hint_absence_reason",
        "cumulative leakage",
        "longest option",
        "not an automatic REJECT",
        "never human approval",
        "not private",
        "exactly",
        "No provider API call",
    ):
        assert required in joined
    assert "Day 1" not in joined and "KC-011" not in joined and "16 candidate" not in joined
