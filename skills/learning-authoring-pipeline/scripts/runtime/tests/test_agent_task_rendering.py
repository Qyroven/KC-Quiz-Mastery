from __future__ import annotations

import json
from pathlib import Path

import pytest

from learning_authoring.agent_session import (
    EXECUTION_MODE,
    _load_task_package,
    _write_task_package,
    prepare_agent_task,
    read_agent_task_input,
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
    assert ("learning-authoring agent-read" in rendered) == (stage in {"kc", "quiz"})
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
    assert _load_task_package(stage, run, package_path, require_official_prompt=False) == task

    repeated = _write_task_package(stage, run, payload)
    assert repeated["task_package"] == result["task_package"]
    assert repeated["agent_readable_task"] == result["agent_readable_task"]
    assert repeated["agent_readable_task_sha256"] == result["agent_readable_task_sha256"]


def test_frozen_reader_indexes_then_reads_exact_source_and_context_without_writes(tmp_path):
    from tests.test_agent_context_slots import _init

    run, _ = _init(tmp_path, notes=True)
    task = prepare_agent_task("kc", run, allow_proposed_extraction_demo=True)
    path = Path(task["task_package"])
    frozen = read_json(path)["input_boundary"]
    before = {p: sha256_file(p) for p in run.rglob("*") if p.is_file()}
    index = read_agent_task_input(path)
    assert "payload" not in index and "text" not in index["context_items"][0]
    selected = read_agent_task_input(path, batch_ids=(index["batches"][0]["read_id"],))
    assert selected["sources"][0] == frozen["payload"]
    assert "context_items" not in selected
    context = read_agent_task_input(path, context_ids=(index["context_items"][0]["context_id"],))
    assert context["context_items"] == frozen["authoring_context"]["items"]
    assert before == {p: sha256_file(p) for p in run.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="unknown batch/context"):
        read_agent_task_input(path, batch_ids=("missing",))
    path.write_text(path.read_text().replace('"stage": "kc"', '"stage": "quiz"'))
    with pytest.raises(ValueError):
        read_agent_task_input(path)


def test_bundle_reader_keeps_same_page_numbers_source_qualified(tmp_path):
    from tests.test_agent_bundle_session import _prepared_bundle

    root, _, _, _ = _prepared_bundle(tmp_path)
    task = prepare_agent_task("kc", root, allow_proposed_extraction_demo=True)
    path = Path(task["task_package"])
    index = read_agent_task_input(path)
    ids = [batch["read_id"] for batch in index["batches"]]
    assert len(ids) == len(set(ids)) == 2
    reads = [read_agent_task_input(path, batch_ids=(key,)) for key in ids]
    assert all(len(read["sources"]) == 1 for read in reads)
    sources = [read["sources"][0] for read in reads]
    assert sources == read_json(path)["input_boundary"]["payload"]
    assert sources[0]["source"]["source_id"] != sources[1]["source"]["source_id"]
    assert sources[0]["pages"][0]["page_number"] == sources[1]["pages"][0]["page_number"]


def test_quiz_reader_can_combine_batches_and_retains_the_full_bank_policy(tmp_path):
    from tests.test_agent_context_slots import _import_kcs, _init

    run, source = _init(tmp_path)
    _import_kcs(run, source)
    task = prepare_agent_task("quiz", run, include_all_kcs=True)
    path = Path(task["task_package"])
    frozen = read_json(path)["input_boundary"]["payload"]
    index = read_agent_task_input(path)
    assert "leaf_kcs" not in index
    ids = tuple(batch["read_id"] for batch in index["batches"])
    selected = read_agent_task_input(path, batch_ids=ids)["payload"]
    assert selected == frozen


@pytest.mark.parametrize("max_pages,max_blocks", [(1, 160), (7, 160), (12, 5)])
def test_long_input_batches_recombine_without_lost_or_duplicated_pages(
    tmp_path, max_pages, max_blocks, capsys
):
    from learning_authoring.agent_session import _inspection_batches
    from learning_authoring.cli import main
    from learning_authoring.contracts import ExtractedSource
    from tests.conftest import page
    from tests.test_agent_context_slots import _init

    run, _ = _init(tmp_path)
    prepared = prepare_agent_task("kc", run, allow_proposed_extraction_demo=True)
    task = read_json(Path(prepared["task_package"]))
    task.pop("task_fingerprint")
    boundary = task["input_boundary"]
    original = boundary["payload"]
    original["source"]["page_count"] = 27
    original["pages"] = [page(i, f"block-{i}").model_dump(mode="json") for i in range(1, 28)]
    boundary["inspection_batches"] = _inspection_batches(
        run, ExtractedSource.model_validate(original), max_pages=max_pages, max_blocks=max_blocks
    )
    frozen = Path(_write_task_package("kc", run, task)["task_package"])
    assert main(["agent-read", str(frozen)]) == 0
    index = json.loads(capsys.readouterr().out)
    assert len(index["batches"]) > 1
    collected = []
    for batch in index["batches"]:
        selected = read_agent_task_input(frozen, batch_ids=(batch["read_id"],))
        collected.extend(selected["sources"][0]["pages"])
    assert collected == original["pages"]
