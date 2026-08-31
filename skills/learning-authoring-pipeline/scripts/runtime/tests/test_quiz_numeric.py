"""Synthetic numeric adapter checks, not evaluation of an authored lesson."""

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from learning_authoring.quiz import QuizConfig, build_quiz_input
from learning_authoring.quiz_contracts import QuizBatch, QuizNumericAnswer
from tests.test_quiz import KC_SHA256, kc_set
from tests.test_quiz_hints import hinted_output


def set_numeric(question):
    question.update(
        interaction="numeric_input",
        prompt="What is the total duration in ms?",
        choice_options=[],
        rubric=[],
        correct_answer={
            "selection_ids": [],
            "mappings": [],
            "ordering": [],
            "text": "",
            "numeric": {"value": 127.5, "absolute_tolerance": 0.05, "unit": "ms"},
        },
        answer_explanation="PRIVATE EXPLANATION: add the two durations.",
    )


def numeric_output(source):
    raw = hinted_output(source)
    set_numeric(raw["questions"][0])
    return raw


def test_numeric_roundtrip_does_not_change_authored_content(source):
    raw = numeric_output(source)
    before = deepcopy(raw)
    batch = QuizBatch.model_validate(raw)
    batch.validate_against_input(
        build_quiz_input(
            kc_set(source),
            kc_set_sha256=KC_SHA256,
            config=QuizConfig(selected_kc_ids=("KC-001",)),
        )
    )
    assert batch.model_dump(mode="json") == raw == before


@pytest.mark.parametrize("field", ["value", "absolute_tolerance", "unit"])
def test_numeric_key_has_no_implicit_value_unit_or_tolerance(field):
    raw = {"value": 3, "absolute_tolerance": 0, "unit": ""}
    raw.pop(field)
    with pytest.raises(ValidationError):
        QuizNumericAnswer.model_validate(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("value", True),
        ("value", "3"),
        ("value", float("nan")),
        ("value", float("inf")),
        ("absolute_tolerance", -1),
        ("absolute_tolerance", True),
        ("absolute_tolerance", "0.1"),
        ("absolute_tolerance", float("inf")),
        ("unit", 1),
    ],
)
def test_numeric_key_rejects_ambiguous_or_nonfinite_values(field, value):
    raw = {"value": -3, "absolute_tolerance": 0, "unit": ""}
    raw[field] = value
    with pytest.raises(ValidationError):
        QuizNumericAnswer.model_validate(raw)


@pytest.mark.parametrize("defect", ["missing", "null", "text", "rubric", "options"])
def test_numeric_response_cannot_mix_other_answer_contracts(source, defect):
    raw = numeric_output(source)
    q = raw["questions"][0]
    if defect == "missing":
        q["correct_answer"].pop("numeric")
    elif defect == "null":
        q["correct_answer"]["numeric"] = None
    elif defect == "text":
        q["correct_answer"]["text"] = "An unrelated explanation."
    elif defect == "rubric":
        q["rubric"] = [{"criterion": "Explanation", "points": 1}]
    else:
        q["choice_options"] = [{"option_id": "A", "text": "Choice"}]
    with pytest.raises(ValidationError):
        QuizBatch.model_validate(raw)


def test_other_interactions_cannot_smuggle_a_numeric_key(source):
    raw = hinted_output(source)
    raw["questions"][0]["correct_answer"]["numeric"] = {
        "value": 3,
        "absolute_tolerance": 0,
        "unit": "",
    }
    with pytest.raises(ValidationError, match="numeric key is only valid"):
        QuizBatch.model_validate(raw)


def test_scalar_numeric_budget_cannot_cover_multiple_independent_kcs(source):
    with pytest.raises(ValueError, match="infeasible total_question_budget"):
        build_quiz_input(
            kc_set(source),
            kc_set_sha256=KC_SHA256,
            config=QuizConfig(
                include_all_kcs=True,
                allowed_interactions=("numeric_input",),
                total_question_budget=1,
            ),
        )


def test_numeric_preview_separates_unit_hint_and_answer_without_mutating_data(source):
    from learning_authoring.quiz_review import _TEMPLATE
    from tests.test_review_compatibility import inline_script, quiz_review_data, run_js

    data = quiz_review_data(source, adaptive=True)
    data["quiz"] = numeric_output(source)
    data["quiz"]["questions"][0]["correct_answer"]["numeric"]["unit"] = "ms <check>"
    run_js(
        inline_script(_TEMPLATE),
        r"""
        const original=JSON.stringify(DATA.quiz), q=questions[0];
        assert.match(responseHTML(q), /type="number"/);
        assert.match(renderStudent(q), /ms &lt;check&gt;/);
        assert.doesNotMatch(renderStudent(q), /127\.5|0\.05|PRIVATE EXPLANATION/);
        view('hintButton').onclick();
        assert.doesNotMatch(view('hintStack').innerHTML, /127\.5|PRIVATE EXPLANATION/);
        assert.equal(questionPreview(q).answer_seen, false);
        view('check').onclick();
        assert.match(view('feedback').innerHTML, /127\.5/);
        assert.match(view('feedback').innerHTML, /0\.05/);
        assert.match(view('feedback').innerHTML, /ms &lt;check&gt;/);
        view('reset').onclick();
        assert.equal(questionPreview(q).answer_seen, false);
        assert.equal(JSON.stringify(DATA.quiz), original);
    """,
        data=data,
    )


def test_numeric_import_and_review_keep_raw_source_and_learner_unit(tmp_path):
    from learning_authoring.agent_session import agent_import, prepare_agent_task
    from learning_authoring.artifacts import sha256_file
    from learning_authoring.product.showcase import build_showcase
    from learning_authoring.quiz_review_state import quiz_review_material
    from tests.test_agent_context_slots import _adaptive_candidate, _import_kcs, _init
    from tests.test_agent_session import _write_raw

    run, source = _init(tmp_path)
    _import_kcs(run, source)
    upstream = {
        name: sha256_file(run / name)
        for name in (
            "extracted-source.proposed.json",
            "kc-proposed.json",
        )
    }
    task = prepare_agent_task("quiz", run, include_all_kcs=True)
    raw = _adaptive_candidate(run, source, task)
    set_numeric(raw["questions"][0])
    candidate = tmp_path / "numeric.json"
    unchanged = _write_raw(candidate, raw)
    imported = agent_import("quiz", run, candidate, task_package=Path(task["task_package"]))
    assert Path(imported["proposed"]).read_bytes() == unchanged
    assert Path(imported["raw_candidate"]).read_bytes() == unchanged
    material = quiz_review_material(run)
    learner = material["learner_questions"][0]
    assert learner["response_unit"] == "ms"
    assert "correct_answer" not in learner and "rubric" not in learner and "hints" not in learner
    assert material["artifacts"]["quiz"] == raw
    output = tmp_path / "portal"
    build_showcase(run, output)
    assert (output / "quiz-review.html").is_file()
    assert candidate.read_bytes() == unchanged
    assert all(sha256_file(run / name) == digest for name, digest in upstream.items())


def test_learning_export_refuses_unsupported_numeric_scoring(source, tmp_path, monkeypatch):
    from learning_authoring.product import learning
    from learning_authoring.product.review_registration import RegistrationSafetyError

    monkeypatch.setattr(learning, "prepare_review_registration", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        learning,
        "quiz_review_material",
        lambda *args: {
            "artifacts": {"quiz": numeric_output(source)},
        },
    )
    with pytest.raises(RegistrationSafetyError, match="numeric_input is authoring/review only"):
        learning.build_learning_package(tmp_path)
