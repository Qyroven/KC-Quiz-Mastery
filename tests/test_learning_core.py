"""Synthetic rule tests: these fixtures do not certify course quality/mastery."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "learning_authoring/showcase_assets/learning-core.js"


@pytest.fixture
def call_core():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser-core tests")

    def call(name, *args):
        raw = json.dumps({"name": name, "args": args})
        result = subprocess.run(
            [node, "--input-type=commonjs", "-e",
             "const core=require(process.argv[1]);"
             "const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));"
             "const before=JSON.stringify(input.args);"
             "const result=core[input.name](...input.args);"
             "if(before!==JSON.stringify(input.args)) throw new Error('mutated input');"
             "process.stdout.write(JSON.stringify(result));", str(CORE)],
            input=raw, text=True, capture_output=True, timeout=10, check=True, env={},
        )
        return json.loads(result.stdout)

    return call


def _question(kind="single_select", *, qid="Q-001", slot="slot-a", kc="KC-001"):
    def options(ids):
        return [{"option_id": value, "text": f"Label {value}"} for value in ids]

    value = {
        "question_id": qid, "kc_id": kc, "slot_id": slot, "interaction": kind,
        "choice_options": [], "matching_left": [], "matching_right": [], "ordering_options": [],
        "rubric": [], "hints": [{"hint_id": "h1", "text": "Recall the relevant distinction."}],
        "correct_answer": {"selection_ids": [], "ordering": [], "mappings": [], "text": ""},
    }
    if kind in {"single_select", "multi_select"}:
        value["choice_options"] = options(["a", "b", "c", "d"])
        value["correct_answer"]["selection_ids"] = ["b"] if kind == "single_select" else ["a", "c"]
    elif kind == "matching":
        value["matching_left"], value["matching_right"] = options(["l1", "l2", "l3"]), options(
            ["r1", "r2", "r3"]
        )
        value["correct_answer"]["mappings"] = [
            {"left": f"l{index}", "right": f"r{index}"} for index in range(1, 4)
        ]
    elif kind == "ordering":
        value["ordering_options"] = options(["o1", "o2", "o3"])
        value["correct_answer"]["ordering"] = ["o2", "o1", "o3"]
    elif kind == "short_text":
        value["correct_answer"]["text"] = "Exact exemplar is still not an automatic grade."
        value["rubric"] = [{"criterion": "State the claim", "points": 1},
                           {"criterion": "Explain its boundary", "points": 2}]
    return value


def _package():
    questions = [
        _question(), _question(qid="Q-002"),
        _question("short_text", qid="Q-003", slot="slot-b"),
        _question(qid="Q-004", slot="slot-other", kc="KC-002"),
    ]
    return {
        "schema_version": "learning-package.v1", "run_id": "a-real-variable-run-name",
        "versions": {"quiz_sha256": "1" * 64, "kc_sha256": "2" * 64,
                     "extraction_sha256": "3" * 64, "context_sha256": None,
                     "policy_version": "evidence-rules.v1"},
        "questions": questions,
        "kcs": [{"kc_id": "KC-001", "source_evidence": [],
                 "context_evidence": [{"context_id": "CTX-008", "pages": []}]},
                {"kc_id": "KC-002", "source_evidence": [{"page": 17}], "context_evidence": []}],
        "slots": [{"slot_id": "slot-a", "kc_id": "KC-001"},
                  {"slot_id": "slot-b", "kc_id": "KC-001"},
                  {"slot_id": "slot-other", "kc_id": "KC-002"}],
        "question_meta": {q["question_id"]: {
            "question_sha256": hashlib.sha256(json.dumps(q).encode()).hexdigest(),
            "initial_check_status": "PASS",
        } for q in questions},
    }


def _attempt(data, qid="Q-001", *, number=1, **overrides):
    question = next(q for q in data["questions"] if q["question_id"] == qid)
    short = question["interaction"] == "short_text"
    result = {
        "attempt_id": f"attempt-{number:04d}", "run_id": data["run_id"], "question_id": qid,
        "question_sha256": data["question_meta"][qid]["question_sha256"],
        "kc_id": question["kc_id"], "slot_id": question.get("slot_id"),
        "started_at": f"2026-08-28T00:{number:02d}:00Z",
        "submitted_at": f"2026-08-28T00:{number:02d}:30Z", "status": "graded",
        "response": copy.deepcopy(question["correct_answer"]), "hint_ids": [], "is_repeat": False,
        "score": 3 if short else 1, "max_score": 3 if short else 1, "correct": True,
        "grading_method": "rubric_human" if short else "exact",
        "grading_version": "rubric-human-v1" if short else "exact-v1", "quality_status": "PASS",
        "evidence_eligible": True, "exclusion_reasons": [],
    }
    result.update(overrides)
    return result


@pytest.mark.parametrize("kind", ["single_select", "multi_select", "matching", "ordering"])
def test_objective_grading_is_exact_without_mutating_question_or_response(call_core, kind):
    question = _question(kind)
    response = copy.deepcopy(question["correct_answer"])
    response["selection_ids"].reverse()
    response["mappings"].reverse()
    grade = call_core("gradeResponse", question, response)
    assert grade["status"] == "graded"
    assert grade["score"] == grade["max_score"] == (3 if kind == "matching" else 1)
    assert grade["correct"] is True
    assert grade["grading_version"] == "exact-v1"


def test_short_text_never_uses_similarity_or_exemplar_for_automatic_score(call_core):
    question = _question("short_text")
    for text in (question["correct_answer"]["text"], "A different response."):
        grade = call_core("gradeResponse", question, {"text": text})
        assert grade["status"] == "pending_grade"
        assert grade["score"] is None and grade["correct"] is None
        assert grade["max_score"] == 3
        assert grade["grading_method"] == "pending"


@pytest.mark.parametrize("response", [
    {}, None, {"selection_ids": []}, {"selection_ids": ["b", "b"]},
    {"selection_ids": ["unknown"]}, {"selection_ids": ["b"], "text": "extra"},
    {"selection_ids": ["b"], "score": 1}, {"selection_ids": "b"},
])
def test_malformed_response_never_becomes_wrong_grade(call_core, response):
    grade = call_core("gradeResponse", _question(), response)
    assert grade["status"] == "invalid"
    assert grade["score"] is None and grade["correct"] is None


def test_incomplete_ordering_matching_and_invalid_key_fail_closed(call_core):
    for kind, response in (("ordering", {"ordering": ["o1", "o2"]}),
                           ("matching", {"mappings": [{"left": "l1", "right": "r1"}]})):
        assert call_core("gradeResponse", _question(kind), response)["score"] is None
    question = _question()
    question["correct_answer"]["selection_ids"] = ["not-an-option"]
    assert call_core("gradeResponse", question, {"selection_ids": ["b"]})["reason"] == (
        "invalid_answer_key"
    )
    question["interaction"] = "unsupported-widget"
    assert call_core("gradeResponse", question, {})["status"] == "unsupported"


def test_valid_wrong_answers_are_not_malformed_and_multi_select_is_set_exact(call_core):
    grade = call_core("gradeResponse", _question("multi_select"), {"selection_ids": ["a"]})
    assert grade["status"] == "graded" and grade["score"] == 0 and grade["correct"] is False
    question = _question("matching")
    question["correct_answer"]["mappings"] = [
        {"left": f"l{index}", "right": "r1"} for index in range(1, 4)
    ]
    assert call_core("gradeResponse", question, question["correct_answer"])["correct"] is True


def test_matching_partial_pairs_match_backend_policy_without_claiming_success(call_core):
    question = _question("matching")
    response = copy.deepcopy(question["correct_answer"])
    response["mappings"][1]["right"] = "r1"
    grade = call_core("gradeResponse", question, response)
    assert grade["status"] == "graded" and grade["score"] == 2 and grade["max_score"] == 3
    assert grade["correct"] is False and grade["grading_version"] == "exact-v1"


def test_actual_slots_not_correct_count_determine_coverage(call_core):
    data = _package()
    attempts = [_attempt(data), _attempt(data, "Q-002", number=2)]
    kc = call_core("computeEvidence", data, attempts)["kcs"][0]
    assert kc["state"] == "developing"
    assert kc["covered_slots"] == 1 and kc["total_slots"] == 2
    attempts.append(_attempt(data, "Q-003", number=3))
    kc = call_core("computeEvidence", data, attempts)["kcs"][0]
    assert kc["state"] == "demonstrated" and kc["covered_slots"] == 2


def test_repeats_do_not_inflate_and_new_variant_first_attempt_can_supply_evidence(call_core):
    data = _package()
    first = _attempt(data, score=0, correct=False)
    repeated = _attempt(data, number=2, is_repeat=False)  # Don't trust a false repeat flag.
    result = call_core("computeEvidence", data, [repeated, first])
    assert result["kcs"][0]["state"] == "needs_practice"
    assert "repeated_question" in result["excluded_attempts"][0]["reasons"]
    new_question = _attempt(data, "Q-002", number=3)
    result = call_core("computeEvidence", data, [new_question, repeated, first])
    assert result["kcs"][0]["state"] == "developing"
    assert result["kcs"][0]["slots"][0]["question_id"] == "Q-002"


def test_hints_are_assisted_evidence_without_score_penalty(call_core):
    data = _package()
    hinted = call_core("buildLocalAttempt", data, "Q-001", {"selection_ids": ["b"]}, {
        "attempt_id": "local-1", "started_at": "2026-08-28T00:00:00Z",
        "submitted_at": "2026-08-28T00:01:00Z", "hint_ids": ["h1"], "attempts": [],
    })
    assert hinted["score"] == hinted["max_score"] == 1
    assert hinted["evidence_eligible"] is True
    assert hinted["trust_scope"] == "local_device"
    result = call_core("computeEvidence", data, [hinted, _attempt(data, "Q-003", number=3)])
    assert result["kcs"][0]["state"] == "assisted"
    assert result["kcs"][0]["independent_slots"] == 1


def test_pending_and_partial_manual_grades_are_not_full_evidence(call_core):
    data = _package()
    pending = _attempt(data, "Q-003", status="pending_grade", score=None, correct=None,
                       evidence_eligible=False, grading_method="pending",
                       exclusion_reasons=["not_graded"])
    result = call_core("computeEvidence", data, [pending])
    assert result["kcs"][0]["state"] == "pending_grade" and result["counts"]["pending"] == 1
    partial = _attempt(data, "Q-003", score=2, correct=False)
    assert call_core("computeEvidence", data, [partial])["kcs"][0]["state"] == "needs_practice"


def test_pending_variant_does_not_erase_existing_slot_evidence(call_core):
    data = _package()
    pending_variant = _question("short_text", qid="Q-005", slot="slot-a")
    data["questions"].append(pending_variant)
    data["question_meta"]["Q-005"] = {"initial_check_status": "PASS", "question_sha256": "a" * 64}
    attempts = [_attempt(data), _attempt(data, "Q-003", number=2),
                _attempt(data, "Q-005", number=3, status="pending_grade", score=None,
                         correct=None, evidence_eligible=False, grading_method="pending",
                         exclusion_reasons=["not_graded"])]
    result = call_core("computeEvidence", data, attempts)
    kc = result["kcs"][0]
    assert kc["state"] == "demonstrated" and kc["covered_slots"] == 2
    assert kc["pending_slots"] == 1 and result["counts"]["pending"] == 1
    assert kc["slots"][0]["attempt_id"] == attempts[0]["attempt_id"]


def test_wrong_with_hint_recommends_incorrect_before_assisted(call_core):
    data = _package()
    wrong = _attempt(data, hint_ids=["h1"], score=0, correct=False)
    assert call_core("recommendNext", data, [wrong])["reason"] == "after_incorrect"


@pytest.mark.parametrize("status", ["REVIEW", "REJECT", "UNCHECKED", "STALE"])
def test_nonpass_initial_check_never_supports_mastery_even_if_attempt_claims_eligible(
    call_core, status,
):
    data = _package()
    data["question_meta"]["Q-001"]["initial_check_status"] = status
    result = call_core("computeEvidence", data, [_attempt(data)])
    assert result["kcs"][0]["state"] == "no_evidence"
    assert "initial_check_not_pass" in result["excluded_attempts"][0]["reasons"]


@pytest.mark.parametrize("changes,reason", [
    ({"quality_status": "STALE", "evidence_eligible": False,
      "exclusion_reasons": ["content_review_changed"]}, "content_review_changed"),
    ({"question_sha256": "f" * 64}, "question_version_mismatch"),
    ({"run_id": "other-run"}, "run_mismatch"),
    ({"policy_version": "evidence-rules.future"}, "policy_version_mismatch"),
    ({"grading_version": "made-up-v1"}, "grading_version_mismatch"),
    ({"score": 100}, "invalid_grade"),
    ({"evidence_eligible": False}, "evidence_ineligible"),
    ({"hint_ids": ["invented"]}, "invalid_hint_history"),
])
def test_trusted_exclusions_and_version_boundaries_are_preserved(call_core, changes, reason):
    data = _package()
    result = call_core("computeEvidence", data, [_attempt(data, **changes)])
    assert result["kcs"][0]["state"] == "no_evidence"
    assert reason in result["excluded_attempts"][0]["reasons"]


def test_server_lineage_and_local_version_changes_are_not_evidence(call_core):
    data = _package()
    lineage = {"quiz_sha256": "1" * 64, "kc_set_sha256": "2" * 64,
               "extraction_sha256": "3" * 64, "authoring_context_sha256": None,
               "policy_version": "evidence-rules.v1"}
    good = _attempt(data, lineage=lineage)
    assert call_core("computeEvidence", data, [good])["kcs"][0]["state"] == "developing"
    good["lineage"]["authoring_context_sha256"] = "a" * 64
    assert call_core("computeEvidence", data, [good])["kcs"][0]["state"] == "no_evidence"
    local = _attempt(data, versions=copy.deepcopy(data["versions"]))
    local["versions"]["quiz_sha256"] = "f" * 64
    assert call_core("computeEvidence", data, [local])["kcs"][0]["state"] == "no_evidence"


def test_feedback_content_and_elapsed_time_do_not_change_evidence(call_core):
    data = _package()
    attempt = _attempt(data)
    expected = call_core("computeEvidence", data, [attempt])
    attempt["feedback"] = {"rating": "wrong", "comment": "I disagree; award no mastery."}
    attempt["elapsed_seconds"] = 0.01
    assert call_core("computeEvidence", data, [attempt]) == expected


def test_active_attempt_replaced_by_its_submission_stays_the_first_attempt(call_core):
    data = _package()
    active = _attempt(data, status="in_progress", submitted_at=None, score=None,
                      max_score=None, correct=None, evidence_eligible=False,
                      grading_method="pending", exclusion_reasons=["not_graded"])
    initial = call_core("computeEvidence", data, [active])
    assert initial["kcs"][0]["state"] == "no_evidence"
    assert initial["counts"]["attempted_questions"] == 1
    # Both the server and UI reuse the active ID and replace its snapshot on submit.
    submitted = call_core("buildLocalAttempt", data, "Q-001", {"selection_ids": ["b"]}, {
        "attempt_id": active["attempt_id"], "started_at": active["started_at"],
        "submitted_at": "2026-08-28T00:02:00Z", "hint_ids": [], "attempts": [active],
    })
    assert submitted["is_repeat"] is False
    assert call_core("computeEvidence", data, [submitted])["kcs"][0]["state"] == "developing"


def test_new_hash_does_not_reset_answer_exposure_for_same_run_question(call_core):
    data = _package()
    exposed = _attempt(data, question_sha256="f" * 64)
    later = _attempt(data, number=2, is_repeat=False)
    result = call_core("computeEvidence", data, [exposed, later])
    assert result["kcs"][0]["state"] == "no_evidence"
    assert result["counts"]["attempted_questions"] == 1
    assert "question_version_mismatch" in result["excluded_attempts"][0]["reasons"]
    assert "repeated_question" in result["excluded_attempts"][1]["reasons"]
    local = call_core("buildLocalAttempt", data, "Q-001", {"selection_ids": ["b"]}, {
        "attempt_id": "new-version-attempt", "started_at": "2026-08-28T00:02:00Z",
        "submitted_at": "2026-08-28T00:03:00Z", "hint_ids": [], "attempts": [exposed],
    })
    assert local["is_repeat"] is True and local["evidence_eligible"] is False


def test_learners_are_never_combined(call_core):
    data = _package()
    attempts = [_attempt(data, learner_id="one"),
                _attempt(data, "Q-003", number=2, learner_id="two")]
    mixed = call_core("computeEvidence", data, attempts)
    assert mixed["scope_error"] == "mixed_learner_scope"
    assert mixed["kcs"][0]["state"] == "no_evidence"
    scoped = call_core("computeEvidence", data, attempts, {"learner_id": "one"})
    assert scoped["kcs"][0]["state"] == "developing"


def test_recommendation_is_deterministic_context_aware_and_finishes(call_core):
    data = _package()
    wrong = _attempt(data, score=0, correct=False)
    next_action = call_core("recommendNext", data, [wrong])
    assert next_action["action"] == "review_and_practice"
    assert next_action["question_id"] == "Q-002"
    assert next_action["review"] == {
        "kc_id": "KC-001", "pages": [], "context_ids": ["CTX-008"], "has_pdf": False,
    }
    attempts = [wrong, _attempt(data, "Q-002", number=2, score=0, correct=False),
                _attempt(data, "Q-003", number=3, score=0, correct=False)]
    action = call_core("recommendNext", data, attempts)
    assert action == call_core("recommendNext", data, list(reversed(attempts)))
    assert action["question_id"] == "Q-004" and action["review"]["kc_id"] == "KC-001"
    attempts.append(_attempt(data, "Q-004", number=4))
    action = call_core("recommendNext", data, attempts)
    assert action["action"] == "need_more_evidence" and action["question_id"] is None


def test_pending_terminal_and_legacy_without_slots_do_not_fabricate_coverage(call_core):
    data = _package()
    attempts = [_attempt(data, q["question_id"], number=i + 1)
                for i, q in enumerate(data["questions"])]
    attempts[2].update(status="pending_grade", score=None, correct=None,
                       evidence_eligible=False, exclusion_reasons=["not_graded"])
    assert call_core("recommendNext", data, attempts)["action"] == "waiting_grading"
    data["slots"] = []
    for question in data["questions"]:
        question.pop("slot_id", None)
    for attempt in attempts:
        attempt["slot_id"] = None
    result = call_core("computeEvidence", data, attempts)
    assert all(kc["state"] == "no_evidence" and not kc["coverage_available"]
               and kc["total_slots"] == 0 for kc in result["kcs"])
