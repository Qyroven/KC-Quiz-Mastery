"""Item labels serialize author decisions; these tests do not judge their pedagogy."""

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from learning_authoring.quiz import QuizConfig, build_quiz_input
from learning_authoring.quiz_contracts import QuizBatch, QuizItemAssessment
from tests.test_quiz import KC_SHA256, kc_set
from tests.test_quiz_adaptive import adaptive_output


def assessment(bloom="apply", difficulty="medium"):
    return {
        "cognitive_operation": bloom,
        "intended_difficulty": difficulty,
        "rationale": "Apply the supplied rule; coordinate conditions taught to these learners.",
    }


@pytest.mark.parametrize(
    "bloom", ["remember", "understand", "apply", "analyze", "evaluate", "create"]
)
@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_bloom_does_not_determine_item_difficulty(source, bloom, difficulty):
    raw = adaptive_output(source, (("KC-001", 1),))
    raw["questions"][0]["assessment"] = assessment(bloom, difficulty)
    before = deepcopy(raw)
    batch = QuizBatch.model_validate(raw)
    batch.validate_against_input(
        build_quiz_input(
            kc_set(source),
            kc_set_sha256=KC_SHA256,
            config=QuizConfig(selected_kc_ids=("KC-001",)),
        )
    )
    assert batch.model_dump(mode="json") == before == raw


@pytest.mark.parametrize("missing", ["cognitive_operation", "intended_difficulty", "rationale"])
def test_item_assessment_has_no_implicit_labels_or_rationale(missing):
    raw = assessment()
    raw.pop(missing)
    with pytest.raises(ValidationError):
        QuizItemAssessment.model_validate(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rationale", " \n"),
        ("rationale", True),
        ("cognitive_operation", "unknown"),
        ("cognitive_operation", "Apply"),
        ("intended_difficulty", "very hard"),
        ("intended_difficulty", 3),
    ],
)
def test_invalid_item_labels_are_not_coerced(field, value):
    raw = assessment()
    raw[field] = value
    with pytest.raises(ValidationError):
        QuizItemAssessment.model_validate(raw)


def test_unknown_is_explicit_estimation_state_not_an_inferred_medium():
    raw = assessment(difficulty="unknown")
    raw["rationale"] = "The task is clear but intended learners' prior knowledge is unspecified."
    assert QuizItemAssessment.model_validate(raw).model_dump() == raw


def test_sibling_variants_keep_individual_difficulty_and_legacy_data_is_unchanged(source):
    raw = adaptive_output(source, (("KC-001", 3),))
    original = deepcopy(raw)
    assert QuizBatch.model_validate(raw).model_dump(mode="json") == original
    assert all("assessment" not in q for q in raw["questions"])
    for question, difficulty in zip(raw["questions"], ("easy", "medium", "hard"), strict=True):
        question["assessment"] = assessment(difficulty=difficulty)
    batch = QuizBatch.model_validate(raw)
    assert [q.assessment.intended_difficulty for q in batch.questions] == ["easy", "medium", "hard"]
    assert batch.assessment_slots[0].intended_difficulty == "medium"
    assert batch.model_dump(mode="json") == raw
    raw["questions"][0]["assessment"] = None
    assert QuizBatch.model_validate(raw).model_dump(mode="json") == raw


def test_numeric_item_assessment_survives_import_review_and_raw_revision(tmp_path):
    from learning_authoring.agent_session import agent_import, prepare_agent_task
    from learning_authoring.artifacts import sha256_file
    from learning_authoring.product.showcase import build_showcase
    from learning_authoring.quiz_review_state import material_digest, quiz_review_material
    from tests.test_agent_context_slots import _adaptive_candidate, _import_kcs, _init
    from tests.test_agent_session import _write_raw
    from tests.test_quiz_numeric import set_numeric

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
    for question, difficulty in zip(raw["questions"], ("easy", "medium", "hard"), strict=True):
        question["assessment"] = assessment(difficulty=difficulty)
    candidate = tmp_path / "item-levels.json"
    unchanged = _write_raw(candidate, raw)
    imported = agent_import("quiz", run, candidate, task_package=Path(task["task_package"]))
    assert Path(imported["proposed"]).read_bytes() == unchanged
    original_raw_path = Path(imported["raw_candidate"])
    material = quiz_review_material(run)
    assert "assessment" not in material["learner_questions"][0]
    assert (
        material["answer_material"]["questions"][0]["assessment"]
        == raw["questions"][0]["assessment"]
    )
    assert material["artifacts"]["quiz"] == raw

    raw["questions"][0]["assessment"] = assessment(difficulty="hard")
    revision = tmp_path / "revised-item-levels.json"
    revised_bytes = _write_raw(revision, raw)
    revised = agent_import("quiz", run, revision, include_all_kcs=True)
    assert Path(revised["proposed"]).read_bytes() == revised_bytes
    assert original_raw_path.read_bytes() == unchanged
    assert candidate.read_bytes() == unchanged
    current = quiz_review_material(run)
    assert current["source_ref"]["quiz_sha256"] != material["source_ref"]["quiz_sha256"]
    assert material_digest(current) != material_digest(material)
    assert current["artifacts"]["quiz"] == raw
    output = tmp_path / "portal"
    build_showcase(run, output)
    assert (output / "quiz-review.html").is_file()
    assert all(sha256_file(run / name) == digest for name, digest in upstream.items())
