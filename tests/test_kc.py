from __future__ import annotations

import json

import pytest

from learning_authoring.artifacts import read_json, write_json
from learning_authoring.kc import (
    KCConfig,
    load_prompt_package,
    prepare_kc_request,
    run_kc_generation,
)
from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.requests import build_kc_request
from tests.conftest import FakeResponse, fake_client, file_sha256, payload


def approved_source(source):
    return payload().with_source(source)


def make_approved_run(run_dir, source) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    approved = approved_source(source)
    write_json(run_dir / "extracted-source.approved.json", approved.model_dump(mode="json"))
    write_json(
        run_dir / "extraction-approval.json",
        {
            "approval_version": "extraction-approval.v1",
            "status": "approved",
            "reviewer": "Reviewer",
            "approved_at": "2026-01-01T00:00:00+00:00",
            "schema_version": approved.schema_version,
            "source_sha256": approved.source.sha256,
            "approved_sha256": file_sha256(run_dir / "extracted-source.approved.json"),
        },
    )


def test_kc_request_has_only_complete_approved_json_as_model_input(source) -> None:
    approved = approved_source(source)
    package = load_prompt_package()
    request = build_kc_request(
        approved=approved,
        instructions=package.instructions,
        output_schema=package.output_schema,
        model="test-model",
        reasoning_effort="high",
        response_mode="sync",
        max_output_tokens=None,
    )

    assert len(request["input"]) == 1
    content = request["input"][0]["content"]
    assert [item["type"] for item in content] == ["input_text"]
    assert json.loads(content[0]["text"]) == approved.model_dump(mode="json")
    assert "input_file" not in json.dumps(request["input"])
    assert "input_image" not in json.dumps(request["input"])


def test_kc_prompt_package_is_exactly_foundation_rulebook_task_and_schema() -> None:
    package = load_prompt_package()
    components = package.manifest["components"]

    assert list(package.manifest["instruction_order"]) == ["foundation", "rulebook", "task"]
    assert set(components) == {"foundation", "rulebook", "task", "output_schema"}
    assert package.instructions == "\n\n".join(
        components[name]["content"] for name in ("foundation", "rulebook", "task")
    )
    assert package.output_schema == components["output_schema"]["content"]

    def assert_const_types(node) -> None:
        if isinstance(node, dict):
            if "const" in node:
                assert "type" in node
            for value in node.values():
                assert_const_types(value)
        elif isinstance(node, list):
            for value in node:
                assert_const_types(value)

    assert_const_types(package.output_schema)


def test_kc_preview_is_non_generating_and_records_exact_boundary(tmp_path, source) -> None:
    make_approved_run(tmp_path, source)
    request, metadata = prepare_kc_request(
        tmp_path,
        config=KCConfig(model="test-model", response_mode="sync"),
    )

    assert metadata["source_delivery"] == "approved_extraction_json_only"
    assert metadata["model_input_items"] == ["extracted-source.approved.json"]
    assert json.loads(request["input"][0]["content"][0]["text"]) == read_json(
        tmp_path / "extracted-source.approved.json"
    )
    assert not (tmp_path / "kc-background-checkpoint.json").exists()
    assert not (tmp_path / "kc-api-response.json").exists()
    assert not (tmp_path / "kc-proposed.json").exists()


def test_kc_preview_can_write_isolated_candidate_artifacts(tmp_path, source) -> None:
    make_approved_run(tmp_path, source)
    candidate = tmp_path / "kc-candidates" / "test-model"
    request, metadata = prepare_kc_request(
        tmp_path,
        output_dir=candidate,
        config=KCConfig(model="test-model", response_mode="sync"),
    )

    assert (candidate / "kc-request-preview.json").is_file()
    assert metadata["approved_extraction"]["path"] == str(
        tmp_path / "extracted-source.approved.json"
    )
    assert json.loads(request["input"][0]["content"][0]["text"]) == read_json(
        tmp_path / "extracted-source.approved.json"
    )
    assert not (candidate / "extracted-source.approved.json").exists()


def test_kc_gate_rejects_approved_file_hash_mismatch(tmp_path, source) -> None:
    make_approved_run(tmp_path, source)
    approval = read_json(tmp_path / "extraction-approval.json")
    approval["approved_sha256"] = "0" * 64
    write_json(tmp_path / "extraction-approval.json", approval)

    with pytest.raises(RuntimeError, match="hash"):
        prepare_kc_request(tmp_path, config=KCConfig(model="test-model", response_mode="sync"))


def test_kc_generation_uses_previewed_json_only_request(tmp_path, source) -> None:
    make_approved_run(tmp_path, source)
    output = {
        "source_ref": {
            "schema_version": "extracted-source.v2",
            "source_id": source.source_id,
            "source_sha256": source.sha256,
        },
        "source_summary": "Summary",
        "page_audit": [
            {
                "page": 1,
                "classification": "context",
                "summary": "One",
                "kc_ids": [],
                "source_block_ids": ["b1"],
                "warning_codes": [],
            },
            {
                "page": 2,
                "classification": "context",
                "summary": "Two",
                "kc_ids": [],
                "source_block_ids": ["b2"],
                "warning_codes": [],
            },
        ],
        "kc_groups": [],
        "leaf_kcs": [],
        "uncovered_content": [],
        "generation_warnings": [],
    }
    client = fake_client(created=[FakeResponse(output, response_id="resp_kc")])

    result = run_kc_generation(
        tmp_path,
        config=KCConfig(model="test-model", response_mode="sync"),
        client=client,
        progress=None,
    )

    assert result.metrics["contract_valid"] is True
    assert len(client.responses.create_calls) == 1
    sent = client.responses.create_calls[0]
    assert sent == read_json(tmp_path / "kc-request-preview.json")
    assert [item["type"] for item in sent["input"][0]["content"]] == ["input_text"]


def test_kc_contract_rejects_invented_evidence_block(source) -> None:
    proposed = ProposedKCSet.model_validate(
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
                    "classification": "context",
                    "summary": "Two",
                    "kc_ids": [],
                    "source_block_ids": ["b2"],
                    "warning_codes": [],
                },
            ],
            "kc_groups": [
                {
                    "group_id": "KCG-001",
                    "name": "Group",
                    "description": "Description",
                    "leaf_kc_ids": ["KC-001"],
                }
            ],
            "leaf_kcs": [
                {
                    "kc_id": "KC-001",
                    "group_id": "KCG-001",
                    "name": "KC",
                    "semantic_form": "concept",
                    "knowledge_description": "Knowledge",
                    "observable_claim": "Learner can explain it",
                    "assessment_boundary": {"included": [], "excluded": []},
                    "source_evidence": [
                        {
                            "evidence_id": "EVD-001",
                            "page": 1,
                            "block_ids": ["invented"],
                            "description": "Evidence",
                            "supports": "Claim",
                        }
                    ],
                    "warning_codes": [],
                    "status": "PROPOSED",
                }
            ],
            "uncovered_content": [],
            "generation_warnings": [],
        }
    )

    with pytest.raises(ValueError, match="outside page 1"):
        proposed.validate_against_source(approved_source(source))
