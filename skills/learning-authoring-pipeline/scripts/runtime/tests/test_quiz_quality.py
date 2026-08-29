import json

from learning_authoring.quiz_contracts import QuizBatch
from learning_authoring.quiz_quality import build_quiz_form_audit


def test_form_audit_flags_uniquely_long_correct_option() -> None:
    batch = QuizBatch.model_validate(
        {
            "schema_version": "quiz-batch.v1",
            "source_ref": {
                "extraction_source_id": "source",
                "extraction_source_sha256": "a" * 64,
                "kc_set_sha256": "b" * 64,
            },
            "questions": [
                {
                    "question_id": "Q-001",
                    "variant_index": 1,
                    "kc_id": "KC-001",
                    "group_id": "KCG-001",
                    "title": "Test",
                    "interaction": "single_select",
                    "stimulus": {
                        "kind": "text",
                        "text": "Một tình huống cần quyết định.",
                        "table_columns": [],
                        "table_rows": [],
                        "formula": "",
                    },
                    "prompt": "Chọn phương án phù hợp.",
                    "choice_options": [
                        {"option_id": "A", "text": "Giữ nguyên"},
                        {"option_id": "B", "text": "Điều chỉnh"},
                        {
                            "option_id": "C",
                            "text": (
                                "Điều chỉnh toàn bộ cơ chế sau khi phân tích "
                                "đầy đủ mọi điều kiện liên quan"
                            ),
                        },
                        {"option_id": "D", "text": "Loại bỏ"},
                    ],
                    "matching_left": [],
                    "matching_right": [],
                    "ordering_options": [],
                    "correct_answer": {
                        "selection_ids": ["C"],
                        "ordering": [],
                        "mappings": [],
                        "text": "",
                    },
                    "rubric": [],
                    "answer_explanation": "Rationale",
                    "evidence_refs": [{"page": 1, "block_ids": ["p1-b1"]}],
                }
            ],
        }
    )

    audit = build_quiz_form_audit(batch)

    assert audit["summary"]["status"] == "HAS_FORM_FLAGS"
    assert audit["questions"][0]["status"] == "FORM_REVIEW"
    assert any(
        issue["code"] == "CORRECT_OPTION_LENGTH_CUE"
        for issue in audit["questions"][0]["issues"]
    )


def test_no_form_flag_is_not_labeled_as_pass() -> None:
    batch = QuizBatch.model_validate(
        {
            "schema_version": "quiz-batch.v1",
            "source_ref": {
                "extraction_source_id": "source",
                "extraction_source_sha256": "a" * 64,
                "kc_set_sha256": "b" * 64,
            },
            "questions": [
                {
                    "question_id": "Q-001",
                    "variant_index": 1,
                    "kc_id": "KC-001",
                    "group_id": "KCG-001",
                    "title": "Test",
                    "interaction": "short_text",
                    "stimulus": {
                        "kind": "none",
                        "text": "",
                        "table_columns": [],
                        "table_rows": [],
                        "formula": "",
                    },
                    "prompt": "Explain the distinction.",
                    "choice_options": [],
                    "matching_left": [],
                    "matching_right": [],
                    "ordering_options": [],
                    "correct_answer": {
                        "selection_ids": [],
                        "ordering": [],
                        "mappings": [],
                        "text": "A bounded explanation.",
                    },
                    "rubric": [{"criterion": "Names the distinction", "points": 1}],
                    "answer_explanation": "Rationale",
                    "evidence_refs": [{"page": 1, "block_ids": ["p1-b1"]}],
                }
            ],
        }
    )

    audit = build_quiz_form_audit(batch)

    assert audit["questions"][0]["status"] == "NO_FORM_FLAG"
    assert "PASS" not in json_text(audit)


def json_text(value) -> str:
    return json.dumps(value)


def _batch(*questions: dict) -> QuizBatch:
    return QuizBatch.model_validate(
        {
            "schema_version": "quiz-batch.v1",
            "source_ref": {
                "extraction_source_id": "source",
                "extraction_source_sha256": "a" * 64,
                "kc_set_sha256": "b" * 64,
            },
            "questions": list(questions),
        }
    )


def _choice_question(
    question_id: str,
    *,
    options: list[str],
    keys: list[str],
    explanation: str,
    interaction: str = "single_select",
    stimulus: str = "A bounded case.",
    prompt: str = "Choose the supported answer.",
    hints: list[dict] | None = None,
    kc_id: str = "KC-001",
) -> dict:
    option_ids = [chr(ord("A") + index) for index in range(len(options))]
    question = {
        "question_id": question_id,
        "variant_index": 1,
        "kc_id": kc_id,
        "group_id": "KCG-001",
        "title": "Diagnostic fixture",
        "interaction": interaction,
        "stimulus": {
            "kind": "text",
            "text": stimulus,
            "table_columns": [],
            "table_rows": [],
            "formula": "",
        },
        "prompt": prompt,
        "choice_options": [
            {"option_id": option_id, "text": text}
            for option_id, text in zip(option_ids, options, strict=True)
        ],
        "matching_left": [],
        "matching_right": [],
        "ordering_options": [],
        "correct_answer": {
            "selection_ids": keys,
            "ordering": [],
            "mappings": [],
            "text": "",
        },
        "rubric": [],
        "answer_explanation": explanation,
        "evidence_refs": [{"page": 1, "block_ids": ["p1-b1"]}],
    }
    if hints is not None:
        question["hints"] = hints
        question["hint_absence_reason"] = None
    return question


def _short_text_question(
    question_id: str,
    *,
    stimulus: str,
    prompt: str,
    answer: str,
    rubric: list[str],
    explanation: str = "The exemplar follows the stated task.",
) -> dict:
    return {
        "question_id": question_id,
        "variant_index": 1,
        "kc_id": "KC-001",
        "group_id": "KCG-001",
        "title": "Diagnostic fixture",
        "interaction": "short_text",
        "stimulus": {
            "kind": "text",
            "text": stimulus,
            "table_columns": [],
            "table_rows": [],
            "formula": "",
        },
        "prompt": prompt,
        "choice_options": [],
        "matching_left": [],
        "matching_right": [],
        "ordering_options": [],
        "correct_answer": {
            "selection_ids": [],
            "ordering": [],
            "mappings": [],
            "text": answer,
        },
        "rubric": [
            {"criterion": criterion, "points": 1} for criterion in rubric
        ],
        "answer_explanation": explanation,
        "evidence_refs": [{"page": 1, "block_ids": ["p1-b1"]}],
    }


def _question_codes(audit: dict, index: int = 0) -> set[str]:
    return {issue["code"] for issue in audit["questions"][index]["issues"]}


def _portfolio_codes(audit: dict) -> set[str]:
    return {issue["code"] for issue in audit["portfolio"]["issues"]}


def test_keyed_option_and_explanation_opposite_polarity_is_flagged() -> None:
    batch = _batch(
        _choice_question(
            "Q-001",
            interaction="multi_select",
            options=[
                "History summarization is no longer necessary",
                "Retrieve only relevant context",
                "Limit output length",
                "Add unrelated context",
            ],
            keys=["A", "B", "C"],
            explanation=(
                "Summarize history, retrieve relevant context, and limit output length."
            ),
        )
    )

    audit = build_quiz_form_audit(batch)

    assert "KEY_EXPLANATION_POLARITY_MISMATCH" in _question_codes(audit)


def test_dimension_and_normalization_contradictions_are_flagged() -> None:
    batch = _batch(
        _short_text_question(
            "Q-001",
            stimulus=(
                "Let d_k=1; K1=[1,0], K2=[0,1]. "
                "Attention weights=[0.62,0.54,0.08]."
            ),
            prompt="Return the weighted result.",
            answer="[1.0, 0.0]",
            rubric=["Returns the weighted result."],
        )
    )

    audit = build_quiz_form_audit(batch)

    assert {
        "DECLARED_DIMENSION_MISMATCH",
        "NORMALIZATION_SUM_MISMATCH",
    } <= _question_codes(audit)


def test_normalized_numeric_table_column_is_checked() -> None:
    question = _short_text_question(
        "Q-001",
        stimulus="Placeholder replaced below.",
        prompt="Identify the largest contributor.",
        answer="alpha",
        rubric=["Names the largest contributor."],
    )
    question["stimulus"] = {
        "kind": "table",
        "text": "",
        "table_columns": ["Item", "Attention weight"],
        "table_rows": [
            ["alpha", "0.90"],
            ["beta", "0.20"],
            ["gamma", "0.08"],
            ["delta", "0.06"],
        ],
        "formula": "",
    }
    batch = _batch(question)

    audit = build_quiz_form_audit(batch)

    assert "NORMALIZATION_SUM_MISMATCH" in _question_codes(audit)


def test_hidden_rubric_response_form_is_flagged() -> None:
    batch = _batch(
        _short_text_question(
            "Q-001",
            stimulus="Use the supplied values.",
            prompt="Return the numeric result.",
            answer="42",
            rubric=["Show a derivation and explain every intermediate step."],
        )
    )

    audit = build_quiz_form_audit(batch)

    assert "RUBRIC_HIDDEN_REQUIREMENT" in _question_codes(audit)


def test_any_n_of_m_exemplar_cannot_have_mostly_fixed_rubric() -> None:
    batch = _batch(
        _short_text_question(
            "Q-001",
            stimulus="The case has five possible improvements.",
            prompt="Name four improvements.",
            answer="A; B; C; D; E. Accept any four of five.",
            rubric=[
                "Names improvement A.",
                "Names improvement B.",
                "Names improvement C.",
                "Names improvement D or improvement E.",
            ],
        )
    )

    audit = build_quiz_form_audit(batch)

    assert "RUBRIC_CHOICE_SET_MISMATCH" in _question_codes(audit)


def test_ordered_hints_are_checked_cumulatively_for_multi_select_key_leakage() -> None:
    batch = _batch(
        _choice_question(
            "Q-001",
            interaction="multi_select",
            options=[
                "Summarize growing history",
                "Retrieve only relevant context",
                "Limit output length",
                "Add unrelated data",
                "Ignore usage records",
            ],
            keys=["A", "B", "C"],
            explanation=(
                "History summarization, relevant retrieval, and bounded output reduce tokens."
            ),
            hints=[
                {
                    "hint_id": "first",
                    "kind": "strategy",
                    "text": "Consider growing history and retrieval of relevant context.",
                },
                {
                    "hint_id": "second",
                    "kind": "step",
                    "text": "Then consider the output limit.",
                },
            ],
        )
    )

    audit = build_quiz_form_audit(batch)
    issue = next(
        issue
        for issue in audit["questions"][0]["issues"]
        if issue["code"] == "CUMULATIVE_HINT_KEY_LEAK"
    )

    assert issue["metrics"]["hint_prefix_length"] == 2
    assert issue["metrics"]["covered_key_ids"] == ["A", "B", "C"]


def test_bank_level_answer_position_and_correct_length_patterns_are_flagged() -> None:
    questions = []
    key_positions = [0, 1, 2] * 4
    for index, key_position in enumerate(key_positions, start=1):
        options = ["Short alpha", "Short beta", "Short gamma", "Short delta"]
        options[key_position] = "The uniquely longest supported answer for this bounded case"
        questions.append(
            _choice_question(
                f"Q-{index:03d}",
                options=options,
                keys=[chr(ord("A") + key_position)],
                explanation="This is the supported answer for the bounded case.",
                stimulus=f"Bounded case {index}.",
                kc_id=f"KC-{index:03d}",
            )
        )
    batch = _batch(*questions)

    audit = build_quiz_form_audit(batch)

    assert {
        "ANSWER_POSITION_IMBALANCE",
        "CORRECT_OPTION_LENGTH_PATTERN",
    } <= _portfolio_codes(audit)
    assert audit["fresh_candidate_guidance"] == {
        "recommended": True,
        "trigger_codes": [
            "ANSWER_POSITION_IMBALANCE",
            "CORRECT_OPTION_LENGTH_PATTERN",
        ],
        "question_ids": [],
        "max_fresh_candidate_revisions": 1,
        "automatic_repair_performed": False,
        "semantic_quality_proven": False,
        "next_action": "AUTHOR_ONE_FRESH_CANDIDATE_FROM_THE_SAME_FROZEN_TASK",
    }


def test_repeated_multi_select_key_shape_across_kcs_requests_fresh_candidate() -> None:
    questions = [
        _choice_question(
            f"Q-{index:03d}",
            interaction="multi_select",
            options=["Alpha case", "Beta case", "Gamma case", "Delta case", "Epsilon case"],
            keys=["A", "B", "D"],
            explanation="The bounded case supports alpha, beta, and delta.",
            stimulus=f"Independent bounded case {index}.",
            kc_id=f"KC-{index:03d}",
        )
        for index in range(1, 5)
    ]

    audit = build_quiz_form_audit(_batch(*questions))

    issue = next(
        issue
        for issue in audit["portfolio"]["issues"]
        if issue["code"] == "MULTI_SELECT_KEY_PATTERN"
    )
    assert issue["metrics"]["keyed_positions"] == [1, 2, 4]
    assert issue["metrics"]["dominant_share"] == 1.0
    assert "MULTI_SELECT_KEY_PATTERN" in audit["fresh_candidate_guidance"][
        "trigger_codes"
    ]


def test_consistent_dimension_normalization_and_polarity_are_not_flagged() -> None:
    batch = _batch(
        _choice_question(
            "Q-001",
                options=[
                    "History summarization remains necessary",
                    "History can be discarded immediately",
                    "All context should be duplicated",
                    "Usage observation can be deferred",
                ],
            keys=["A"],
            explanation="History summarization remains necessary for the stated case.",
            stimulus="Let d_k=2; K=[1,0]. Attention weights=[0.6,0.4].",
        )
    )

    audit = build_quiz_form_audit(batch)

    assert not {
        "KEY_EXPLANATION_POLARITY_MISMATCH",
        "DECLARED_DIMENSION_MISMATCH",
        "NORMALIZATION_SUM_MISMATCH",
    } & _question_codes(audit)
    assert audit["fresh_candidate_guidance"]["recommended"] is False
    assert (
        audit["fresh_candidate_guidance"]["next_action"]
        == "PROCEED_TO_INDEPENDENT_SEMANTIC_REVIEW"
    )
