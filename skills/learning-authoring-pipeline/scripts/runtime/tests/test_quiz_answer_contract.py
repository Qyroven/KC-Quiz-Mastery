"""The authored schema explains existing interaction-specific answer restrictions."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from learning_authoring.quiz_contracts import (
    QuizBatchV1,
    QuizBatchV2,
    QuizBatchV3,
    quiz_output_schema,
)
from tests.test_quiz_hints import hinted_output

CONTRACTS = {
    "quiz-batch.v1": QuizBatchV1,
    "quiz-batch.v2": QuizBatchV2,
    "quiz-batch.v3": QuizBatchV3,
}
OBJECTIVE_INTERACTIONS = ("single_select", "multi_select", "matching", "ordering")


def answer_fixture(source, version: str, interaction: str) -> dict:
    raw = hinted_output(source, interaction=interaction)
    raw["schema_version"] = version
    if version != "quiz-batch.v3":
        for question in raw["questions"]:
            question.pop("hints")
            question.pop("hint_absence_reason")
    if version == "quiz-batch.v1":
        raw.pop("assessment_slots")
        for question in raw["questions"]:
            question.pop("slot_id")
    return raw


@pytest.mark.parametrize("version", CONTRACTS)
@pytest.mark.parametrize("interaction", (*OBJECTIVE_INTERACTIONS, "short_text"))
def test_answer_field_guidance_does_not_change_valid_payloads(source, version, interaction) -> None:
    raw = answer_fixture(source, version, interaction)
    parsed = CONTRACTS[version].model_validate_json(json.dumps(raw), strict=True)
    assert parsed.model_dump(mode="json") == raw


@pytest.mark.parametrize("version", CONTRACTS)
@pytest.mark.parametrize("interaction", OBJECTIVE_INTERACTIONS)
@pytest.mark.parametrize("extra_field", ["rubric", "text"])
def test_objective_answers_still_reject_short_text_only_fields(
    source, version, interaction, extra_field
) -> None:
    raw = answer_fixture(source, version, interaction)
    if extra_field == "rubric":
        raw["questions"][0]["rubric"] = [{"criterion": "Chooses the keyed answer", "points": 1}]
    else:
        raw["questions"][0]["correct_answer"]["text"] = "An explanation is not a text key."
    with pytest.raises(ValidationError, match=f"{interaction} must not contain text-answer fields"):
        CONTRACTS[version].model_validate_json(json.dumps(raw), strict=True)


@pytest.mark.parametrize("version", CONTRACTS)
@pytest.mark.parametrize("missing_field", ["rubric", "text"])
def test_short_text_still_requires_both_exemplar_and_rubric(source, version, missing_field) -> None:
    raw = answer_fixture(source, version, "short_text")
    if missing_field == "rubric":
        raw["questions"][0]["rubric"] = []
    else:
        raw["questions"][0]["correct_answer"]["text"] = ""
    with pytest.raises(ValidationError, match="short_text requires an exemplar answer and rubric"):
        CONTRACTS[version].model_validate_json(json.dumps(raw), strict=True)


@pytest.mark.parametrize("version", CONTRACTS)
@pytest.mark.parametrize("strict_output", [False, True])
def test_native_and_strict_schema_describe_answer_field_ownership(version, strict_output) -> None:
    schema = quiz_output_schema(version, strict_output=strict_output)
    answer = schema["$defs"]["QuizAnswer"]["properties"]
    question_name = "QuizQuestion" if version.endswith("v1") else f"QuizQuestionV{version[-1]}"
    question = schema["$defs"][question_name]["properties"]
    assert "short_text only" in answer["text"]["description"]
    assert 'empty string ("")' in answer["text"]["description"]
    assert "short_text only" in question["rubric"]["description"]
    for interaction in OBJECTIVE_INTERACTIONS:
        assert interaction in answer["text"]["description"]
        assert interaction in question["rubric"]["description"]
    assert "single_select or multi_select only" in answer["selection_ids"]["description"]
    assert "for matching only" in answer["mappings"]["description"]
    assert "for ordering only" in answer["ordering"]["description"]
    assert "required for every interaction" in question["answer_explanation"]["description"]
    for field in (
        *(value for key, value in answer.items() if key != "numeric"),
        question["rubric"],
        question["answer_explanation"],
    ):
        assert "default" not in field
    assert "Required only for numeric_input" in answer["numeric"]["description"]
    if strict_output:
        assert "default" not in answer["numeric"]
    else:
        assert answer["numeric"]["default"] is None
