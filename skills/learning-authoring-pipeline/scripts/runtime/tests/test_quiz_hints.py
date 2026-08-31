from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from learning_authoring.quiz import QuizConfig, build_quiz_input
from learning_authoring.quiz_contracts import (
    CURRENT_QUIZ_INPUT_VERSION,
    CURRENT_QUIZ_SCHEMA_VERSION,
    QuizBatch,
    QuizBatchV2,
    QuizBatchV3,
    QuizHint,
    quiz_output_schema,
)
from tests.test_quiz import KC_SHA256, kc_set, quiz_output
from tests.test_quiz_adaptive import adaptive_output


def hinted_output(source, *, interaction: str = "single_select", hint_count: int = 1) -> dict:
    raw = adaptive_output(source, (("KC-001", 1),))
    question = raw["questions"][0]
    question["interaction"] = interaction
    question["hints"] = [
        {"hint_id": f"support-{index}", "kind": "strategy", "text": f"Consider step {index}."}
        for index in range(1, hint_count + 1)
    ]
    question["hint_absence_reason"] = (
        None if hint_count else "Any additional cue would disclose the required term."
    )
    answer = question["correct_answer"]
    if interaction == "multi_select":
        answer["selection_ids"] = ["A", "C"]
    elif interaction != "single_select":
        question["choice_options"] = []
        answer["selection_ids"] = []
        if interaction == "matching":
            question["matching_left"] = [
                {"option_id": f"L-{i}", "text": f"Case {i}"} for i in range(1, 4)
            ]
            question["matching_right"] = [
                {"option_id": f"R-{i}", "text": f"Category {i}"} for i in (3, 1, 2)
            ]
            answer["mappings"] = [{"left": f"L-{i}", "right": f"R-{i}"} for i in range(1, 4)]
        elif interaction == "ordering":
            question["ordering_options"] = [
                {"option_id": f"step-{i}", "text": f"Step {i}"} for i in (2, 3, 1)
            ]
            answer["ordering"] = ["step-1", "step-2", "step-3"]
        elif interaction == "short_text":
            answer["text"] = "A bounded reference response."
            question["rubric"] = [{"criterion": "Gives the relevant distinction.", "points": 1}]
        else:
            raise AssertionError(f"unsupported fixture interaction: {interaction}")
    return raw


@pytest.mark.parametrize(
    "interaction", ["single_select", "multi_select", "matching", "ordering", "short_text"]
)
@pytest.mark.parametrize("hint_count", [0, 1, 2, 7])
def test_each_supported_interaction_has_an_adaptive_hint_decision(
    source, interaction, hint_count
) -> None:
    raw = hinted_output(source, interaction=interaction, hint_count=hint_count)
    batch = QuizBatchV3.model_validate_json(json.dumps(raw), strict=True)
    batch.validate_against_input(
        build_quiz_input(
            kc_set(source),
            kc_set_sha256=KC_SHA256,
            config=QuizConfig(selected_kc_ids=("KC-001",)),
        )
    )
    assert len(batch.questions[0].hints) == hint_count
    assert batch.model_dump(mode="json") == raw


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hint_id", ""),
        ("hint_id", " \t"),
        ("hint_id", " padded "),
        ("hint_id", 1),
        ("hint_id", True),
        ("hint_id", b"bytes-are-not-json-text"),
        ("kind", "answer"),
        ("kind", 1),
        ("text", ""),
        ("text", " \n\t"),
        ("text", 42),
        ("text", False),
    ],
)
def test_hint_fields_are_typed_and_nonblank(field, value) -> None:
    hint = {"hint_id": "focus", "kind": "cue", "text": "Look for the relevant condition."}
    hint[field] = value
    with pytest.raises(ValidationError):
        QuizHint.model_validate(hint)


@pytest.mark.parametrize("kind", ["cue", "strategy", "step"])
def test_hint_kind_is_a_description_not_a_required_ladder(source, kind) -> None:
    raw = hinted_output(source, hint_count=2)
    for hint in raw["questions"][0]["hints"]:
        hint["kind"] = kind
    assert len(QuizBatchV3.model_validate(raw).questions[0].hints) == 2


@pytest.mark.parametrize("parser", [QuizBatch, QuizBatchV3])
@pytest.mark.parametrize(
    "mutation",
    [
        "missing_hints",
        "missing_reason",
        "missing_both",
        "duplicate_ids",
        "empty_no_reason",
        "empty_blank_reason",
        "empty_nontext_reason",
        "hints_with_reason",
    ],
)
def test_v3_hint_decision_cannot_be_omitted_or_contradictory(source, parser, mutation) -> None:
    raw = hinted_output(source, hint_count=2)
    question = raw["questions"][0]
    if mutation in {"missing_hints", "missing_both"}:
        question.pop("hints")
    if mutation in {"missing_reason", "missing_both"}:
        question.pop("hint_absence_reason")
    if mutation == "duplicate_ids":
        question["hints"][1]["hint_id"] = question["hints"][0]["hint_id"]
    elif mutation.startswith("empty_"):
        question["hints"] = []
        question["hint_absence_reason"] = {
            "empty_no_reason": None,
            "empty_blank_reason": " \n ",
            "empty_nontext_reason": False,
        }[mutation]
    elif mutation == "hints_with_reason":
        question["hint_absence_reason"] = "Conflicting absence explanation."
    with pytest.raises(ValidationError):
        parser.model_validate_json(json.dumps(raw), strict=True)


def test_hint_ids_are_stable_local_ids_not_globally_counted(source) -> None:
    raw = adaptive_output(source)
    # The event identity is question + hint, so another question may reuse an ID.
    assert len({q["hints"][0]["hint_id"] for q in raw["questions"]}) == 1
    assert QuizBatchV3.model_validate(raw).model_dump(mode="json") == raw


@pytest.mark.parametrize("version", ["quiz-batch.v1", "quiz-batch.v2"])
def test_old_quiz_artifacts_do_not_acquire_synthetic_hint_fields(source, version) -> None:
    raw = (
        quiz_output(source) if version.endswith("v1") else adaptive_output(source, version=version)
    )
    batch = QuizBatch.model_validate(raw)
    assert batch.model_dump(mode="json") == raw
    assert all("hints" not in question for question in batch.model_dump()["questions"])
    assert all("hint_absence_reason" not in q for q in batch.model_dump()["questions"])
    schema = quiz_output_schema(version, strict_output=False)
    question_name = "QuizQuestion" if version.endswith("v1") else "QuizQuestionV2"
    assert "hints" not in schema["$defs"][question_name]["properties"]
    assert "QuizHint" not in schema["$defs"]


def test_v3_native_schema_requires_hint_decision_without_fixed_count_or_string_defaults() -> None:
    schema = quiz_output_schema(strict_output=False)
    assert schema["properties"]["schema_version"]["const"] == CURRENT_QUIZ_SCHEMA_VERSION
    question = schema["$defs"]["QuizQuestionV3"]
    assert {"hints", "hint_absence_reason"} <= set(question["required"])
    assert "default" not in question["properties"]["hints"]
    assert "minItems" not in question["properties"]["hints"]
    assert "maxItems" not in question["properties"]["hints"]
    assert "default" not in question["properties"]["hint_absence_reason"]
    assert schema["$defs"]["QuizHint"]["required"] == ["hint_id", "kind", "text"]


@pytest.mark.parametrize("field", ["variant_index", "variant_count", "page", "rubric_points"])
@pytest.mark.parametrize("value", [True, "1", 1.0])
def test_v3_native_parser_rejects_numeric_coercion(source, field, value) -> None:
    raw = hinted_output(source, interaction="short_text")
    question = raw["questions"][0]
    if field == "variant_index":
        question[field] = value
    elif field == "variant_count":
        raw["assessment_slots"][0][field] = value
    elif field == "page":
        question["evidence_refs"][0][field] = value
    else:
        question["rubric"][0]["points"] = value
    with pytest.raises(ValidationError):
        QuizBatchV3.model_validate_json(json.dumps(raw), strict=True)


def test_v3_frozen_policy_rejects_downgrade_but_old_v2_task_remains_readable(source) -> None:
    payload = build_quiz_input(
        kc_set(source),
        kc_set_sha256=KC_SHA256,
        config=QuizConfig(selected_kc_ids=("KC-001",)),
    )
    assert payload["input_version"] == CURRENT_QUIZ_INPUT_VERSION
    historical = adaptive_output(source, (("KC-001", 1),), version="quiz-batch.v2")
    batch = QuizBatchV2.model_validate_json(json.dumps(historical), strict=True)
    with pytest.raises(ValueError, match="requires quiz-batch.v3"):
        batch.validate_against_input(payload)
    old_payload = copy.deepcopy(payload)
    old_payload["input_version"] = "quiz-input.v2"
    old_payload["runtime"]["expected_schema_version"] = "quiz-batch.v2"
    batch.validate_against_input(old_payload)
    with pytest.raises(ValueError, match="requires quiz-batch.v2"):
        QuizBatchV3.model_validate(hinted_output(source)).validate_against_input(old_payload)


def test_form_contract_does_not_masquerade_as_semantic_hint_validation(source) -> None:
    raw = hinted_output(source)
    raw["questions"][0]["hints"][0]["text"] = "Select B; it is the correct answer."
    # This deliberately bad hint is structurally valid. Independent semantic review
    # must reject leakage; keyword/length heuristics cannot establish its quality.
    QuizBatchV3.model_validate(raw)
