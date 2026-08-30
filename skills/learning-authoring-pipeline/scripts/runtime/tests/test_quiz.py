"""Shared Quiz fixtures and subscription-native package checks."""

from __future__ import annotations

from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.quiz import QuizConfig, build_quiz_input, load_quiz_prompt_package

KC_SHA256 = "b" * 64


def kc_set(source) -> ProposedKCSet:
    return ProposedKCSet.model_validate(
        {
            "source_ref": {
                "schema_version": "extracted-source.v2",
                "source_id": source.source_id,
                "source_sha256": source.sha256,
            },
            "source_summary": "Summary",
            "page_audit": [
                {
                    "page": 1,
                    "classification": "learning_content",
                    "summary": "One",
                    "kc_ids": ["KC-001"],
                    "source_block_ids": ["b1"],
                    "warning_codes": [],
                },
                {
                    "page": 2,
                    "classification": "learning_content",
                    "summary": "Two",
                    "kc_ids": ["KC-002"],
                    "source_block_ids": ["b2"],
                    "warning_codes": [],
                },
            ],
            "kc_groups": [
                {
                    "group_id": "KCG-001",
                    "name": "Group",
                    "description": "Description",
                    "leaf_kc_ids": ["KC-001", "KC-002"],
                }
            ],
            "leaf_kcs": [
                {
                    "kc_id": "KC-001",
                    "group_id": "KCG-001",
                    "name": "First KC",
                    "semantic_form": "concept",
                    "knowledge_description": "Knowledge one",
                    "observable_claim": "Learner distinguishes one",
                    "assessment_boundary": {"included": ["one"], "excluded": []},
                    "source_evidence": [
                        {
                            "evidence_id": "EVD-001",
                            "page": 1,
                            "block_ids": ["b1"],
                            "description": "Evidence one",
                            "supports": "Claim one",
                        }
                    ],
                    "warning_codes": [],
                    "status": "PROPOSED",
                },
                {
                    "kc_id": "KC-002",
                    "group_id": "KCG-001",
                    "name": "Second KC",
                    "semantic_form": "principle",
                    "knowledge_description": "Knowledge two",
                    "observable_claim": "Learner applies two",
                    "assessment_boundary": {"included": ["two"], "excluded": []},
                    "source_evidence": [
                        {
                            "evidence_id": "EVD-002",
                            "page": 2,
                            "block_ids": ["b2"],
                            "description": "Evidence two",
                            "supports": "Claim two",
                        }
                    ],
                    "warning_codes": [],
                    "status": "PROPOSED",
                },
            ],
            "uncovered_content": [],
            "generation_warnings": [],
        }
    )


def quiz_output(source, *, variants: int = 1) -> dict:
    questions = []
    for index in range(1, variants + 1):
        questions.append(
            {
                "question_id": f"Q-{index:03d}",
                "variant_index": index,
                "kc_id": "KC-001",
                "group_id": "KCG-001",
                "title": f"Question {index}",
                "interaction": "single_select",
                "stimulus": {
                    "kind": "text",
                    "text": "A bounded situation.",
                    "table_columns": [],
                    "table_rows": [],
                    "formula": "",
                },
                "prompt": "Choose the best answer.",
                "choice_options": [
                    {"option_id": "A", "text": "Option one"},
                    {"option_id": "B", "text": "Option two"},
                    {"option_id": "C", "text": "Option three"},
                    {"option_id": "D", "text": "Option four"},
                ],
                "matching_left": [],
                "matching_right": [],
                "ordering_options": [],
                "correct_answer": {
                    "selection_ids": ["B"],
                    "ordering": [],
                    "mappings": [],
                    "text": "",
                },
                "rubric": [],
                "answer_explanation": "Option two follows from the KC.",
                "evidence_refs": [{"page": 1, "block_ids": ["b1"]}],
            }
        )
    return {
        "schema_version": "quiz-batch.v1",
        "source_ref": {
            "extraction_source_id": source.source_id,
            "extraction_source_sha256": source.sha256,
            "kc_set_sha256": KC_SHA256,
        },
        "questions": questions,
    }


def test_quiz_prompt_package_is_subscription_native() -> None:
    package = load_quiz_prompt_package()
    assert package.manifest["package_version"] == "quiz-agent-session.v4"
    assert package.manifest["instruction_order"] == ["foundation", "rulebook", "task"]
    assert "portfolio ledger" in package.instructions


def test_quiz_input_contains_selected_kcs_and_runtime(source) -> None:
    payload = build_quiz_input(
        kc_set(source),
        kc_set_sha256=KC_SHA256,
        config=QuizConfig(selected_kc_ids=("KC-002",)),
    )
    assert payload["runtime"]["selected_kc_ids"] == ["KC-002"]
    assert [kc["kc_id"] for kc in payload["leaf_kcs"]] == ["KC-002"]
