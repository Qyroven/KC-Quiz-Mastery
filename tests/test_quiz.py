from __future__ import annotations

import hashlib
import json

from learning_authoring.artifacts import RunArtifacts, read_json, write_json
from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.quiz import (
    QuizConfig,
    build_quiz_input,
    load_quiz_prompt_package,
    prepare_quiz_request,
    run_quiz_generation,
)
from learning_authoring.quiz_contracts import QuizBatch
from learning_authoring.requests import build_quiz_request
from tests.conftest import FakeResponse, fake_client

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


def test_quiz_prompt_package_is_one_small_canonical_package() -> None:
    package = load_quiz_prompt_package()

    assert package.manifest["package_version"] == "quiz.v3.experimental"
    assert package.manifest["instruction_order"] == ["foundation", "rulebook", "task"]
    assert set(package.manifest["components"]) == {
        "foundation",
        "rulebook",
        "task",
        "output_schema",
    }
    assert "assessment planner" not in package.instructions.lower()
    assert "candidate generator" not in package.instructions.lower()
    assert "passed review" in package.instructions


def test_quiz_schema_is_strict() -> None:
    schema = load_quiz_prompt_package().output_schema

    def assert_strict(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            for value in node.values():
                assert_strict(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict(value)

    assert_strict(schema)


def test_quiz_input_contains_only_selected_kcs_groups_and_runtime(source) -> None:
    payload = build_quiz_input(
        kc_set(source),
        kc_set_sha256=KC_SHA256,
        config=QuizConfig(selected_kc_ids=("KC-001",), variants_per_kc=2),
    )

    assert payload["runtime"]["selected_kc_ids"] == ["KC-001"]
    assert payload["runtime"]["variants_per_kc"] == 2
    assert payload["runtime"]["expected_question_count"] == 2
    assert [kc["kc_id"] for kc in payload["leaf_kcs"]] == ["KC-001"]
    assert [group["group_id"] for group in payload["kc_groups"]] == ["KCG-001"]
    serialized = json.dumps(payload)
    for forbidden in ("assessment_needs", "quiz_blueprint", "input_image", "source_asset"):
        assert forbidden not in serialized


def test_quiz_request_has_one_json_text_item_and_no_media(source) -> None:
    payload = build_quiz_input(
        kc_set(source),
        kc_set_sha256=KC_SHA256,
        config=QuizConfig(selected_kc_ids=("KC-001",), variants_per_kc=1),
    )
    package = load_quiz_prompt_package(schema_version=payload["runtime"]["expected_schema_version"])
    request = build_quiz_request(
        quiz_input_payload=payload,
        instructions=package.instructions,
        output_schema=package.output_schema,
        model="test-model",
        reasoning_effort="high",
        response_mode="sync",
        max_output_tokens=1000,
    )

    assert json.loads(request["input"][0]["content"][0]["text"]) == payload
    encoded = json.dumps(request["input"])
    assert "input_file" not in encoded
    assert "input_image" not in encoded


def test_preview_writes_canonical_artifacts_without_generation(tmp_path, source) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    kc_path = run_dir / "kc-proposed.json"
    write_json(kc_path, kc_set(source).model_dump(mode="json"))

    request, metadata = prepare_quiz_request(
        run_dir,
        config=QuizConfig(
            model="test-model",
            response_mode="sync",
            selected_kc_ids=("KC-001",),
            variants_per_kc=1,
        ),
    )

    artifacts = RunArtifacts(run_dir / "quiz")
    assert metadata["quality_status"] == "experimental_unapproved"
    assert artifacts.quiz_input.is_file()
    assert artifacts.quiz_prompt_package.is_file()
    assert artifacts.quiz_request_preview.is_file()
    assert json.loads(request["input"][0]["content"][0]["text"]) == read_json(artifacts.quiz_input)
    assert not artifacts.quiz_api_response.exists()


def test_generation_preserves_raw_output_and_writes_form_audit(tmp_path, source) -> None:
    run_dir = tmp_path / "run"
    output_dir = run_dir / "quiz"
    run_dir.mkdir()
    kc_path = run_dir / "kc-proposed.json"
    write_json(kc_path, kc_set(source).model_dump(mode="json"))
    raw = quiz_output(source)
    raw["source_ref"]["kc_set_sha256"] = hashlib.sha256(kc_path.read_bytes()).hexdigest()
    client = fake_client(created=[FakeResponse(raw, response_id="resp_quiz")])

    result = run_quiz_generation(
        run_dir,
        config=QuizConfig(
            model="test-model",
            response_mode="sync",
            selected_kc_ids=("KC-001",),
            variants_per_kc=1,
        ),
        client=client,
        progress=None,
    )

    artifacts = RunArtifacts(output_dir)
    assert result.metrics["quality_status"] == "experimental_unapproved"
    assert json.loads(artifacts.quiz_raw_output.read_text()) == raw
    assert QuizBatch.model_validate(read_json(artifacts.quiz_proposed))
    assert read_json(artifacts.quiz_form_audit)["scope"].startswith("surface-form heuristics")
