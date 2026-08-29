from __future__ import annotations

import json
from pathlib import Path

import pytest

from learning_authoring.agent_session import (
    EXECUTION_MODE,
    _load_task_package,
    _write_task_package,
)
from learning_authoring.artifacts import read_json, sha256_file


@pytest.mark.parametrize("stage", ("extraction", "kc", "quiz", "quiz-review"))
def test_agent_task_renders_selected_examples_for_every_native_stage(
    tmp_path: Path,
    stage: str,
) -> None:
    run = (tmp_path / stage).resolve()
    example = {
        "example_version": "worked-example.v1",
        "example_id": f"{stage}-selected-example",
        "teaching_points": ["Use the selected contract."],
        "illustrative_values_only": True,
        "input": {"example_input": stage},
        "output": {"example_output": stage},
    }
    lineage_example_component = {
        "filename": f"{stage}/manifest.json",
        "sha256": "e" * 64,
        "content": [example],
        "lineage": {"example_order": [example["example_id"]]},
    }
    payload = {
        "task_package_version": "agent-task.v3",
        "run_dir": str(run),
        "stage": stage,
        "execution_mode": EXECUTION_MODE,
        "provider_api_calls": 0,
        "host_generation": "coding_agent_subscription_session",
        "instructions": f"Follow the {stage} instructions.",
        "candidate_contract": {
            "title": "RenderedCandidate",
            "schema": {"type": "object", "required": ["answer"]},
        },
        "output_policy": {"format": "JSON only"},
        "worked_examples": [example],
        "prompt_lineage": {
            "package_version": f"{stage}-prompt.v1",
            "worked_examples_component": "worked_examples",
            "package_sha256": "p" * 64,
            "components": {"worked_examples": lineage_example_component},
        },
        "input_boundary": {
            "delivery": "targeted_fixture",
            "large_source_payload": "input-boundary-content-is-not-rendered-twice",
        },
    }

    result = _write_task_package(stage, run, payload)
    package_path = Path(result["task_package"])
    rendered_path = Path(result["agent_readable_task"])
    task = read_json(package_path)
    rendered = rendered_path.read_text(encoding="utf-8")

    assert result["read_before_authoring"] is True
    assert rendered_path.stem == package_path.stem
    assert result["agent_readable_task_sha256"] == sha256_file(rendered_path)
    assert task["worked_examples"] == [example]
    assert json.dumps([example], ensure_ascii=False, indent=2) in rendered
    assert json.dumps(task["candidate_contract"]["schema"], indent=2) in rendered
    assert task["instructions"] in rendered
    assert str(package_path) in rendered
    assert "input-boundary-content-is-not-rendered-twice" not in rendered
    assert task["prompt_lineage"]["components"]["worked_examples"] == {
        "filename": f"{stage}/manifest.json",
        "sha256": "e" * 64,
        "lineage": {"example_order": [example["example_id"]]},
        "content_ref": "/worked_examples",
    }
    assert lineage_example_component["content"] == [example]
    # This unit fixture exercises rendering with deliberately synthetic prompt
    # material. Runtime imports use the default official-package check.
    assert (
        _load_task_package(stage, run, package_path, require_official_prompt=False) == task
    )

    repeated = _write_task_package(stage, run, payload)
    assert repeated["task_package"] == result["task_package"]
    assert repeated["agent_readable_task"] == result["agent_readable_task"]
    assert repeated["agent_readable_task_sha256"] == result["agent_readable_task_sha256"]
