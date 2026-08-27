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
