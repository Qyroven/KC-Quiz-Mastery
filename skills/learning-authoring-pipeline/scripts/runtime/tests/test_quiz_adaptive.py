from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.quiz import (
    QuizConfig,
    build_quiz_input,
)
from learning_authoring.quiz_contracts import (
    AssessmentSlot,
    QuizBatch,
    QuizBatchV2,
    quiz_output_schema,
)
from tests.test_quiz import KC_SHA256, kc_set, quiz_output

CONTEXT_SHA256 = "c" * 64


def test_difficulty_can_be_unknown_but_is_never_defaulted() -> None:
    row = {
        "slot_id": "slot-uncalibrated",
        "kc_id": "KC-001",
        "evidence_intent": "Interpret supplied evidence under its stated assumptions.",
        "cognitive_operation": "analyze",
        "intended_difficulty": "unknown",
        "variant_count": 1,
        "justification": "Target is defined; intended audience knowledge is unspecified.",
    }
    assert AssessmentSlot.model_validate(row).intended_difficulty == "unknown"
    del row["intended_difficulty"]
    with pytest.raises(ValidationError):
        AssessmentSlot.model_validate(row)


def adaptive_output(
    source,
    slots: tuple[tuple[str, int], ...] = (("KC-001", 2), ("KC-001", 1), ("KC-002", 1)),
    *,
    version: str = "quiz-batch.v3",
) -> dict:
    raw = quiz_output(source)
    template = raw["questions"][0]
    raw.update(schema_version=version, assessment_slots=[], questions=[])
    for index, (kc_id, variant_count) in enumerate(slots, 1):
        slot_id = f"slot-{index}"
        raw["assessment_slots"].append(
            {
                "slot_id": slot_id,
                "kc_id": kc_id,
                "evidence_intent": f"Bounded evidence intent {index}",
                "cognitive_operation": "understand" if index == 1 else "apply",
                "intended_difficulty": "medium",
                "variant_count": variant_count,
                "justification": "This supported intent has useful alternate concrete instances.",
            }
        )
        for variant_index in range(1, variant_count + 1):
            question = copy.deepcopy(template)
            page = 1 if kc_id == "KC-001" else 2
            question.update(
                question_id=f"Q-{len(raw['questions']) + 1:03d}",
                slot_id=slot_id,
                kc_id=kc_id,
                variant_index=variant_index,
                evidence_refs=[{"page": page, "block_ids": [f"b{page}"]}],
            )
            if version == "quiz-batch.v3":
                question.update(
                    hints=[
                        {
                            "hint_id": "start-with-the-condition",
                            "kind": "cue",
                            "text": "Which condition in the situation is relevant to the rule?",
                        }
                    ],
                    hint_absence_reason=None,
                )
            raw["questions"].append(question)
    return raw


def contextual_kc_set(source, *, attachment: bool = False) -> ProposedKCSet:
    payload = kc_set(source).model_dump(mode="json")
    payload["source_ref"]["authoring_context_sha256"] = CONTEXT_SHA256
    payload["leaf_kcs"][0].update(
        source_evidence=[],
        context_evidence=[
            {
                "context_id": "CTX-001",
                "excerpt": None if attachment else "Lecturer note supports this claim.",
                "description": "An inspected diagram supports this claim." if attachment else None,
                "supports": "The observable claim is supported by the lecturer context.",
                "pages": [],
                "mapping_method": "unmapped",
                "mapping_confidence": "unmapped",
            }
        ],
    )
    return ProposedKCSet.model_validate(payload)


def context_output(source, *, attachment: bool = False, legacy: bool = False) -> dict:
    raw = quiz_output(source) if legacy else adaptive_output(source, (("KC-001", 1),))
    raw["source_ref"]["authoring_context_sha256"] = CONTEXT_SHA256
    evidence = contextual_kc_set(source, attachment=attachment).leaf_kcs[0].context_evidence[0]
    raw["questions"][0]["evidence_refs"] = []
    raw["questions"][0]["context_evidence_refs"] = [
        {
            key: value
            for key, value in evidence.model_dump(mode="json").items()
            if key in {"context_id", "excerpt", "description", "pages"}
        }
    ]
    return raw


def test_adaptive_defaults_have_no_per_kc_multiplier_or_implicit_budget(source) -> None:
    payload = build_quiz_input(
        kc_set(source), kc_set_sha256=KC_SHA256, config=QuizConfig(include_all_kcs=True)
    )
    runtime = payload["runtime"]
    assert payload["input_version"] == "quiz-input.v3"
    assert runtime["assessment_mode"] == "adaptive_slots"
    assert runtime["expected_schema_version"] == "quiz-batch.v3"
    assert runtime["selected_kc_ids"] == ["KC-001", "KC-002"]
    assert runtime["min_slots_per_kc"] == 1
    assert runtime["minimum_question_count"] == 1
    for name in (
        "variants_per_kc",
        "max_slots_per_kc",
        "variants_per_slot",
        "max_variants_per_slot",
        "total_question_budget",
        "expected_question_count",
    ):
        assert runtime[name] is None
    batch = QuizBatch.model_validate(adaptive_output(source))
    batch.validate_against_input(payload)
    assert len(batch.questions) == 4 and len(batch.assessment_slots) == 3
    # Repeated variant index 1 is correct across different slots within the same KC.
    assert [q.variant_index for q in batch.questions if q.kc_id == "KC-001"] == [1, 2, 1]


@pytest.mark.parametrize("version", ["quiz-batch.v1", "quiz-batch.v2", "quiz-batch.v3"])
def test_selected_generation_schema_is_strict_and_context_capable(version) -> None:
    schema = quiz_output_schema(version)
    assert schema["properties"]["schema_version"]["const"] == version
    question_name = "QuizQuestion" if version.endswith("v1") else f"QuizQuestionV{version[-1]}"
    question = schema["$defs"][question_name]
    assert "context_evidence_refs" in question["required"]
    assert "authoring_context_sha256" in schema["$defs"]["QuizSourceRef"]["required"]
    assert ("assessment_slots" in schema["required"]) != version.endswith("v1")
    assert ("slot_id" in question["required"]) != version.endswith("v1")
    assert ("hints" in question["required"]) == version.endswith("v3")
    assert ("hint_absence_reason" in question["required"]) == version.endswith("v3")

    def check(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)


def test_legacy_batch_roundtrips_without_added_optional_fields(source) -> None:
    raw = quiz_output(source, variants=3)
    batch = QuizBatch.model_validate(raw)
    assert batch.model_dump(mode="json") == raw
    payload = build_quiz_input(
        kc_set(source),
        kc_set_sha256=KC_SHA256,
        config=QuizConfig(selected_kc_ids=("KC-001",), variants_per_kc=3),
    )
    batch.validate_against_input(payload)
    assert payload["runtime"]["assessment_mode"] == "legacy_per_kc"
    assert payload["runtime"]["expected_question_count"] == 3


@pytest.mark.parametrize("version", ["quiz-batch.v1", "quiz-batch.v2", "quiz-batch.v3"])
def test_native_schema_retains_optional_context_fields_and_selected_slot_contract(version) -> None:
    schema = quiz_output_schema(version, strict_output=False)
    question_name = "QuizQuestion" if version.endswith("v1") else f"QuizQuestionV{version[-1]}"
    question = schema["$defs"][question_name]
    source_ref = schema["$defs"]["QuizSourceRef"]
    assert "context_evidence_refs" in question["properties"]
    assert "context_evidence_refs" not in question["required"]
    assert "authoring_context_sha256" in source_ref["properties"]
    assert "authoring_context_sha256" not in source_ref["required"]
    if not version.endswith("v1"):
        assert "assessment_slots" in schema["required"]
        assert "slot_id" in question["required"]
    else:
        assert "assessment_slots" not in schema["properties"]
        assert "slot_id" not in question["properties"]


def test_native_v2_parser_accepts_omitted_context_without_rewriting_it(source) -> None:
    raw = adaptive_output(source, (("KC-001", 1),), version="quiz-batch.v2")
    batch = QuizBatchV2.model_validate_json(json.dumps(raw), strict=True)
    payload = build_quiz_input(
        kc_set(source),
        kc_set_sha256=KC_SHA256,
        config=QuizConfig(selected_kc_ids=("KC-001",)),
    )
    # An existing v2 task remains readable against its unchanged historical policy.
    payload["input_version"] = "quiz-input.v2"
    payload["runtime"]["expected_schema_version"] = "quiz-batch.v2"
    batch.validate_against_input(payload)
    assert batch.model_dump(mode="json") == raw


@pytest.mark.parametrize("field", ["variant_index", "page", "rubric_points"])
@pytest.mark.parametrize("value", [True, "1"])
def test_native_v2_parser_rejects_coercions_before_raw_json_is_accepted(
    source, field, value
) -> None:
    raw = adaptive_output(source, (("KC-001", 1),), version="quiz-batch.v2")
    question = raw["questions"][0]
    if field == "variant_index":
        question["variant_index"] = value
    elif field == "page":
        question["evidence_refs"][0]["page"] = value
    else:
        question.update(interaction="short_text", choice_options=[])
        question["correct_answer"].update(selection_ids=[], text="A bounded exemplar answer.")
        question["rubric"] = [{"criterion": "An observable criterion.", "points": value}]
    with pytest.raises(ValidationError):
        QuizBatchV2.model_validate_json(json.dumps(raw), strict=True)


def test_legacy_reader_does_not_retroactively_add_new_quality_constraints(source) -> None:
    raw = quiz_output(source)
    raw["questions"][0]["interaction"] = "multi_select"
    raw["questions"][0]["correct_answer"]["selection_ids"] = ["A", "B", "C", "D"]
    # Historical v1 allowed all choices as keys. Adaptive v2 rejects that shape,
    # but read-only handling of old candidates must not silently change validity.
    assert QuizBatch.model_validate(raw).model_dump(mode="json") == raw


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_slots_per_kc": 0},
        {"min_slots_per_kc": None},
        {"max_slots_per_kc": 0},
        {"variants_per_slot": 0},
        {"max_variants_per_slot": -1},
        {"total_question_budget": 0},
        {"variants_per_kc": 0},
        {"variants_per_slot": True},
        {"min_slots_per_kc": 1.5},
        {"min_slots_per_kc": 3, "max_slots_per_kc": 2},
        {"variants_per_slot": 3, "max_variants_per_slot": 2},
        {"variants_per_kc": 2, "variants_per_slot": 1},
        {"variants_per_kc": 2, "min_slots_per_kc": 2},
    ],
)
def test_invalid_count_policy_fails_before_generation(overrides) -> None:
    with pytest.raises(ValueError):
        QuizConfig(include_all_kcs=True, **overrides).validate()


@pytest.mark.parametrize(
    "overrides",
    [
        {"variants_per_slot": 2, "total_question_budget": 1},
        {"variants_per_kc": 3, "total_question_budget": 5},
    ],
)
def test_infeasible_caps_fail_before_candidate_authoring(source, overrides) -> None:
    with pytest.raises(ValueError, match="infeasible.*no KCs will be truncated"):
        build_quiz_input(
            kc_set(source),
            kc_set_sha256=KC_SHA256,
            config=QuizConfig(include_all_kcs=True, **overrides),
        )


def test_all_selected_kcs_can_exceed_one_hundred_questions_without_a_cap(source) -> None:
    payload = build_quiz_input(
        kc_set(source), kc_set_sha256=KC_SHA256, config=QuizConfig(include_all_kcs=True)
    )
    batch = QuizBatch.model_validate(adaptive_output(source, (("KC-001", 70), ("KC-002", 45))))
    batch.validate_against_input(payload)
    assert len(batch.questions) == 115


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"min_slots_per_kc": 2}, "requires at least 2 assessment slots"),
        ({"max_slots_per_kc": 1}, "exceeds max_slots_per_kc"),
        ({"variants_per_slot": 2}, "must have exactly 2 variants"),
        ({"max_variants_per_slot": 1}, "exceeds max_variants_per_slot"),
        ({"total_question_budget": 3}, "exceeds total_question_budget"),
    ],
)
def test_slot_plan_respects_every_supplied_bound(source, overrides, error) -> None:
    payload = build_quiz_input(
        kc_set(source),
        kc_set_sha256=KC_SHA256,
        config=QuizConfig(include_all_kcs=True, **overrides),
    )
    with pytest.raises(ValueError, match=error):
        QuizBatch.model_validate(adaptive_output(source)).validate_against_input(payload)


def test_explicit_slot_bounds_and_variant_override_are_feasible(source) -> None:
    payload = build_quiz_input(
        kc_set(source),
        kc_set_sha256=KC_SHA256,
        config=QuizConfig(
            include_all_kcs=True,
            min_slots_per_kc=2,
            max_slots_per_kc=2,
            variants_per_slot=2,
            max_variants_per_slot=2,
            total_question_budget=8,
        ),
    )
    slots = (("KC-001", 2), ("KC-001", 2), ("KC-002", 2), ("KC-002", 2))
    QuizBatch.model_validate(adaptive_output(source, slots)).validate_against_input(payload)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("missing_plan", "requires assessment_slots"),
        ("duplicate_slot", "duplicate assessment slot IDs"),
        ("unknown_slot", "known assessment slot_id"),
        ("missing_slot", "known assessment slot_id"),
        ("slot_kc_mismatch", "KC does not match its assessment slot"),
        ("wrong_total", "assessment slots require"),
        ("wrong_slot_count", "must have exactly"),
        ("index_gap", "variant indexes are not contiguous"),
        ("duplicate_index", "variant indexes are not contiguous"),
        ("duplicate_question", "duplicate question IDs"),
    ],
)
def test_adaptive_internal_references_are_exact(source, mutation, error) -> None:
    raw = adaptive_output(source)
    if mutation == "missing_plan":
        raw.pop("assessment_slots")
    elif mutation == "duplicate_slot":
        raw["assessment_slots"][1]["slot_id"] = raw["assessment_slots"][0]["slot_id"]
    elif mutation == "unknown_slot":
        raw["questions"][0]["slot_id"] = "unknown"
    elif mutation == "missing_slot":
        raw["questions"][0].pop("slot_id")
    elif mutation == "slot_kc_mismatch":
        raw["questions"][0]["kc_id"] = "KC-002"
    elif mutation == "wrong_total":
        raw["questions"].pop()
    elif mutation == "wrong_slot_count":
        raw["questions"][1]["slot_id"] = raw["assessment_slots"][1]["slot_id"]
    elif mutation == "index_gap":
        raw["questions"][1]["variant_index"] = 3
    elif mutation == "duplicate_index":
        raw["questions"][1]["variant_index"] = 1
    elif mutation == "duplicate_question":
        raw["questions"][1]["question_id"] = raw["questions"][0]["question_id"]
    with pytest.raises(ValidationError, match=error):
        QuizBatch.model_validate(raw)


@pytest.mark.parametrize("mode", ["missing_selected", "extra_selected", "legacy_output"])
def test_selected_coverage_and_adaptive_schema_cannot_be_bypassed(source, mode) -> None:
    config = QuizConfig(
        include_all_kcs=mode != "extra_selected",
        selected_kc_ids=("KC-001",) if mode == "extra_selected" else (),
    )
    payload = build_quiz_input(kc_set(source), kc_set_sha256=KC_SHA256, config=config)
    raw = adaptive_output(source)
    if mode == "missing_selected":
        raw = adaptive_output(source, (("KC-001", 1),))
    elif mode == "legacy_output":
        raw = quiz_output(source)
    with pytest.raises(ValueError, match="selected KCs|requires quiz-batch.v3"):
        QuizBatch.model_validate(raw).validate_against_input(payload)


@pytest.mark.parametrize(
    "operation", ["remember", "understand", "apply", "analyze", "evaluate", "create"]
)
def test_all_six_cognitive_operations_are_available_without_a_quota(source, operation) -> None:
    raw = adaptive_output(source, (("KC-001", 1),))
    raw["assessment_slots"][0]["cognitive_operation"] = operation
    AssessmentSlot.model_validate(raw["assessment_slots"][0])


@pytest.mark.parametrize("count", [0, -1, True, 1.5, "2"])
def test_slot_variant_count_is_a_positive_integer(source, count) -> None:
    slot = adaptive_output(source)["assessment_slots"][0]
    slot["variant_count"] = count
    with pytest.raises(ValidationError):
        AssessmentSlot.model_validate(slot)


@pytest.mark.parametrize(
    "mutation", ["source_hash", "context_hash", "pdf_evidence", "group", "interaction"]
)
def test_input_identity_evidence_and_interaction_are_still_enforced(source, mutation) -> None:
    raw = adaptive_output(source, (("KC-001", 1),))
    config = QuizConfig(selected_kc_ids=("KC-001",))
    if mutation == "source_hash":
        raw["source_ref"]["kc_set_sha256"] = "f" * 64
    elif mutation == "context_hash":
        raw["source_ref"]["authoring_context_sha256"] = CONTEXT_SHA256
    elif mutation == "pdf_evidence":
        raw["questions"][0]["evidence_refs"] = [{"page": 2, "block_ids": ["b2"]}]
    elif mutation == "group":
        raw["questions"][0]["group_id"] = "KCG-999"
    elif mutation == "interaction":
        config = QuizConfig(selected_kc_ids=("KC-001",), allowed_interactions=("short_text",))
    payload = build_quiz_input(kc_set(source), kc_set_sha256=KC_SHA256, config=config)
    with pytest.raises(ValueError):
        QuizBatch.model_validate(raw).validate_against_input(payload)


@pytest.mark.parametrize("attachment", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_note_only_kcs_have_real_context_provenance_without_fake_pdf_pages(
    source, attachment, legacy
) -> None:
    payload = build_quiz_input(
        contextual_kc_set(source, attachment=attachment),
        kc_set_sha256=KC_SHA256,
        config=QuizConfig(selected_kc_ids=("KC-001",), variants_per_kc=1 if legacy else None),
    )
    raw = context_output(source, attachment=attachment, legacy=legacy)
    QuizBatch.model_validate(raw).validate_against_input(payload)
    assert payload["source_ref"]["authoring_context_sha256"] == CONTEXT_SHA256
    assert raw["questions"][0]["evidence_refs"] == []
    assert raw["questions"][0]["context_evidence_refs"][0]["pages"] == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("context_id", "CTX-002"),
        ("excerpt", "Another excerpt from the same note."),
        ("description", "An observation outside this KC."),
        ("pages", [1]),
    ],
)
def test_context_citations_must_match_the_owning_kc_exactly(source, field, value) -> None:
    payload = build_quiz_input(
        contextual_kc_set(source),
        kc_set_sha256=KC_SHA256,
        config=QuizConfig(selected_kc_ids=("KC-001",)),
    )
    raw = context_output(source)
    raw["questions"][0]["context_evidence_refs"][0][field] = value
    with pytest.raises(ValueError, match="context evidence outside its KC"):
        QuizBatch.model_validate(raw).validate_against_input(payload)


def test_question_without_pdf_or_context_evidence_is_rejected(source) -> None:
    raw = adaptive_output(source, (("KC-001", 1),))
    raw["questions"][0]["evidence_refs"] = []
    with pytest.raises(ValidationError, match="requires PDF or authoring-context evidence"):
        QuizBatch.model_validate(raw)


@pytest.mark.parametrize("keys", [["A", "A"], ["A", "B", "C", "D"]])
def test_multi_select_keys_are_distinct_and_leave_a_distractor(source, keys) -> None:
    raw = adaptive_output(source, (("KC-001", 1),))
    raw["questions"][0]["interaction"] = "multi_select"
    raw["questions"][0]["correct_answer"]["selection_ids"] = keys
    with pytest.raises(ValidationError):
        QuizBatch.model_validate(raw)
