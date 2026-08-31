"""Synthetic contract cases, not a claim that authored questions are pedagogically valid."""

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from learning_authoring.quiz import QuizConfig, build_quiz_input
from learning_authoring.quiz_contracts import QuizBatch
from tests.test_quiz import KC_SHA256, kc_set
from tests.test_quiz_adaptive import adaptive_output


def integrated_output(source):
    raw = adaptive_output(source, (("KC-001", 1), ("KC-002", 1)))
    first, second = raw["questions"]
    first.update(
        interaction="short_text",
        additional_slot_ids=[second["slot_id"]],
        prompt="Explain the acceptance decision and the required recovery action.",
        choice_options=[],
        correct_answer={
            "selection_ids": [],
            "mappings": [],
            "ordering": [],
            "text": "The decision and recovery action each follow their source rule.",
        },
        evidence_refs=first["evidence_refs"] + second["evidence_refs"],
        rubric=[
            {
                "slot_id": first["slot_id"],
                "criterion": "Apply the acceptance condition.",
                "points": 1,
            },
            {
                "slot_id": second["slot_id"],
                "criterion": "Apply the recovery condition.",
                "points": 1,
            },
        ],
    )
    raw["questions"] = [first]
    return raw


def test_one_integrated_item_covers_two_slots_without_duplicating_the_question(source):
    raw = integrated_output(source)
    before = deepcopy(raw)
    batch = QuizBatch.model_validate(raw)
    payload = build_quiz_input(
        kc_set(source),
        kc_set_sha256=KC_SHA256,
        config=QuizConfig(include_all_kcs=True, total_question_budget=1),
    )
    batch.validate_against_input(payload)
    assert raw == before and batch.model_dump(mode="json") == before
    assert len(batch.questions) == 1 and len(batch.assessment_slots) == 2
    assert batch.question_kc_ids(batch.questions[0]) == ["KC-001", "KC-002"]


@pytest.mark.parametrize("defect", ["unbound", "missing", "unknown", "duplicate", "objective"])
def test_integrated_items_require_real_separately_bound_rubric_components(source, defect):
    raw = integrated_output(source)
    question = raw["questions"][0]
    if defect == "unbound":
        question["rubric"][1].pop("slot_id")
    elif defect == "missing":
        question["rubric"].pop()
    elif defect == "unknown":
        question["additional_slot_ids"] = ["nonexistent"]
        question["rubric"][1]["slot_id"] = "nonexistent"
    elif defect == "duplicate":
        question["additional_slot_ids"].append(question["slot_id"])
    else:
        question["interaction"] = "single_select"
    with pytest.raises(ValidationError):
        QuizBatch.model_validate(raw)


def test_integrated_item_cannot_add_a_kc_without_its_bound_source_evidence(source):
    raw = integrated_output(source)
    raw["questions"][0]["evidence_refs"].pop()
    batch = QuizBatch.model_validate(raw)
    payload = build_quiz_input(
        kc_set(source), kc_set_sha256=KC_SHA256, config=QuizConfig(include_all_kcs=True)
    )
    with pytest.raises(ValueError, match="lacks evidence for KC-002"):
        batch.validate_against_input(payload)


def test_objective_only_budget_cannot_rely_on_integrated_constructed_responses(source):
    config = QuizConfig(include_all_kcs=True, allowed_interactions=("single_select",))
    payload = build_quiz_input(kc_set(source), kc_set_sha256=KC_SHA256, config=config)
    assert payload["runtime"]["minimum_question_count"] == 2
    assert payload["runtime"]["integrated_constructed_responses"] is False
    with pytest.raises(ValueError, match="infeasible total_question_budget"):
        build_quiz_input(
            kc_set(source),
            kc_set_sha256=KC_SHA256,
            config=QuizConfig(
                include_all_kcs=True,
                allowed_interactions=("single_select",),
                total_question_budget=1,
            ),
        )


@pytest.mark.parametrize("count", [2, 3, 5, 7])
def test_single_select_accepts_supported_option_counts_without_padding(source, count):
    raw = adaptive_output(source, (("KC-001", 1),))
    question = raw["questions"][0]
    question["choice_options"] = [
        {"option_id": str(i), "text": f"Choice {i}"} for i in range(count)
    ]
    question["correct_answer"]["selection_ids"] = ["0"]
    assert len(QuizBatch.model_validate(raw).questions[0].choice_options) == count
    question["choice_options"] = question["choice_options"][:1]
    with pytest.raises(ValidationError):
        QuizBatch.model_validate(raw)


def test_existing_learning_export_refuses_to_misattribute_integrated_evidence(
    source, tmp_path, monkeypatch
):
    from learning_authoring.product import learning
    from learning_authoring.product.review_registration import RegistrationSafetyError

    monkeypatch.setattr(learning, "prepare_review_registration", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        learning,
        "quiz_review_material",
        lambda *args: {"artifacts": {"quiz": integrated_output(source)}},
    )
    with pytest.raises(RegistrationSafetyError, match="per-slot rubric evidence"):
        learning.build_learning_package(tmp_path)


def test_integrated_import_and_portal_preserve_raw_and_count_unique_items(tmp_path):
    from learning_authoring.agent_session import agent_import, prepare_agent_task
    from learning_authoring.artifacts import read_json, sha256_file
    from learning_authoring.product.showcase import build_showcase
    from tests.test_agent_context_slots import _adaptive_candidate, _import_kcs, _init
    from tests.test_agent_session import _write_raw

    run, source = _init(tmp_path)
    _import_kcs(run, source)
    upstream = {
        name: sha256_file(run / name)
        for name in ("extracted-source.proposed.json", "kc-proposed.json")
    }
    task = prepare_agent_task("quiz", run, include_all_kcs=True)
    raw = _adaptive_candidate(run, source, task)
    raw["assessment_slots"][1]["variant_count"] = 1
    raw["questions"] = raw["questions"][:1]
    question = raw["questions"][0]
    question.update(
        interaction="short_text",
        additional_slot_ids=["S-apply"],
        choice_options=[],
        correct_answer={"selection_ids": [], "mappings": [], "ordering": [], "text": "A result."},
        rubric=[
            {"slot_id": "S-explain", "criterion": "Explain the condition.", "points": 1},
            {"slot_id": "S-apply", "criterion": "Apply the condition.", "points": 1},
        ],
    )
    candidate = run / "integrated-candidate.json"
    unchanged = _write_raw(candidate, raw)
    imported = agent_import("quiz", run, candidate, task_package=Path(task["task_package"]))
    assert Path(imported["raw_candidate"]).read_bytes() == unchanged
    assert Path(imported["proposed"]).read_bytes() == unchanged
    metrics = read_json(run / "quiz/quiz-run-metrics.json")
    assert metrics["question_count"] == 1 and metrics["assessment_slot_count"] == 2
    assert metrics["question_counts_by_kc"] == {"KC-001": 1}
    output = tmp_path / "portal"
    build_showcase(run, output)
    assert (output / "quiz-review.html").is_file()
    assert (output / "index.html").is_file()
    assert all(sha256_file(run / name) == value for name, value in upstream.items())
    assert candidate.read_bytes() == unchanged


def test_integrated_review_renders_every_slot_without_duplicating_questions(source):
    from learning_authoring.quiz_review import _TEMPLATE
    from tests.test_review_compatibility import inline_script, quiz_review_data, run_js

    data = quiz_review_data(source, adaptive=True)
    data["quiz"] = integrated_output(source)
    data["input"] = build_quiz_input(
        kc_set(source), kc_set_sha256=KC_SHA256, config=QuizConfig(include_all_kcs=True)
    )
    run_js(
        inline_script(_TEMPLATE),
        """
        const original=JSON.stringify(DATA.quiz), q=questions[0];
        assert.deepEqual(questionKCIds(q), ['KC-001', 'KC-002']);
        assert.match(view('metrics').innerHTML, /1 Câu hỏi/);
        assert.match(view('metrics').innerHTML, /2 Leaf KC/);
        const markup=slotHTML(q);
        for (const slot of DATA.quiz.assessment_slots) {
          assert.ok(markup.includes(slot.slot_id));
          assert.ok(markup.includes(slot.evidence_intent));
        }
        view('reviewMode').onclick();
        assert.ok(view('content').innerHTML.includes('[slot-1]'));
        assert.ok(view('content').innerHTML.includes('[slot-2]'));
        assert.equal(JSON.stringify(DATA.quiz), original);
    """,
        data=data,
    )
