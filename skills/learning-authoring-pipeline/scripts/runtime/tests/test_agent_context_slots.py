"""Offline integration fixtures, never model-generated teaching content."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from learning_authoring.agent_session import (
    agent_context,
    agent_import,
    agent_init,
    prepare_agent_task,
)
from learning_authoring.artifacts import read_json, sha256_file, write_json
from learning_authoring.cli import _parser, main
from learning_authoring.contracts import ExtractedSource
from tests.conftest import write_blank_pdf
from tests.test_agent_session import (
    _extraction_candidate,
    _forbid_provider_use,
    _kc_candidate,
    _quiz_candidate,
    _write_raw,
)


def _init(tmp_path: Path, *, notes: bool = False) -> tuple[Path, ExtractedSource]:
    pdf = tmp_path / "lesson.pdf"
    run = tmp_path / "run"
    write_blank_pdf(pdf)
    kwargs = {"context_texts": ("Lecturer-only nuance: compare the assumptions.",)} if notes else {}
    agent_init(pdf, run, **kwargs)
    candidate = tmp_path / "extraction.json"
    _write_raw(candidate, _extraction_candidate())
    task = prepare_agent_task("extraction", run)
    agent_import("extraction", run, candidate, task_package=Path(task["task_package"]))
    return run, ExtractedSource.model_validate(read_json(run / "extracted-source.proposed.json"))


def _import_kcs(run: Path, source: ExtractedSource, *, notes: bool = False) -> dict:
    task = prepare_agent_task("kc", run, allow_proposed_extraction_demo=True)
    package = read_json(Path(task["task_package"]))
    candidate = _kc_candidate(source)
    if notes:
        candidate["source_ref"] = package["input_boundary"]["expected_source_ref"]
        candidate["page_audit"][0]["kc_ids"] = []
        candidate["leaf_kcs"][0]["source_evidence"] = []
        candidate["leaf_kcs"][0]["context_evidence"] = [
            {
                "context_id": "CTX-001",
                "excerpt": "Lecturer-only nuance: compare the assumptions.",
                "description": None,
                "supports": "A supplementary distinction, not a statement visible on the PDF.",
                "pages": [],
                "mapping_method": "document_level",
                "mapping_confidence": "high",
            }
        ]
        candidate["context_audit"] = [
            {
                "context_id": "CTX-001",
                "excerpt": "Lecturer-only nuance: compare the assumptions.",
                "claim": "Compare the assumptions.",
                "disposition": "represented",
                "kc_ids": ["KC-001"],
                "reason": "The contextual distinction is retained in the proposed KC.",
            }
        ]
    path = run / "candidate-kc.json"
    raw = _write_raw(path, candidate)
    imported = agent_import("kc", run, path, task_package=Path(task["task_package"]))
    assert Path(imported["raw_candidate"]).read_bytes() == raw
    assert (run / "kc-proposed.json").read_bytes() == raw
    return candidate


def _adaptive_candidate(run: Path, source: ExtractedSource, task: dict, *, notes=False) -> dict:
    frozen = read_json(Path(task["task_package"]))["input_boundary"]["payload"]
    candidate = _quiz_candidate(source, sha256_file(run / "kc-proposed.json"), variants=3)
    candidate["schema_version"] = frozen["runtime"]["expected_schema_version"]
    candidate["source_ref"] = frozen["source_ref"]
    candidate["assessment_slots"] = [
        {
            "slot_id": "S-explain",
            "kc_id": "KC-001",
            "evidence_intent": "Explain the distinguishing assumption.",
            "cognitive_operation": "understand",
            "intended_difficulty": "easy",
            "variant_count": 1,
            "justification": "One explanation covers this evidence intent.",
        },
        {
            "slot_id": "S-apply",
            "kc_id": "KC-001",
            "evidence_intent": "Apply the assumption to a bounded new case.",
            "cognitive_operation": "apply",
            "intended_difficulty": "medium",
            "variant_count": 2,
            "justification": "Two independent contexts test the same application requirement.",
        },
    ]
    for index, question in enumerate(candidate["questions"]):
        question["slot_id"] = "S-explain" if index == 0 else "S-apply"
        question["variant_index"] = 1 if index == 0 else index
        question["hints"] = [
            {
                "hint_id": "consider-assumptions",
                "kind": "strategy",
                "text": "Identify the assumption the given comparison depends on.",
            }
        ]
        question["hint_absence_reason"] = None
        if notes:
            question["evidence_refs"] = []
            question["context_evidence_refs"] = [
                {
                    "context_id": "CTX-001",
                    "excerpt": "Lecturer-only nuance: compare the assumptions.",
                    "description": None,
                    "pages": [],
                }
            ]
    return candidate


def test_context_is_separate_and_never_enters_extraction_task(tmp_path, monkeypatch) -> None:
    _forbid_provider_use(monkeypatch)
    run, source = _init(tmp_path, notes=True)
    before = sha256_file(run / "extracted-source.proposed.json")
    task = prepare_agent_task("extraction", run)
    assert "Lecturer-only nuance" not in Path(task["task_package"]).read_text()
    note = tmp_path / "arbitrary-annotation.anything"
    note.write_text("A free paragraph; not a required Slide N template.\n", encoding="utf-8")
    result = agent_context(run, context_files=(note,), context_texts=("Additional emphasis.",))
    context = read_json(run / "authoring-context.json")
    assert result["extraction_modified"] is False
    assert result["authoring_context"]["item_count"] == 2
    assert sha256_file(run / "extracted-source.proposed.json") == before
    assert (run / context["items"][0]["raw_path"]).read_bytes() == note.read_bytes()
    assert context["source_ref"]["source_sha256"] == source.source.sha256
    assert len(list((run / "authoring-context/manifests").glob("*.json"))) == 2


def test_context_only_kc_and_slot_quiz_complete_offline_without_changing_raw(
    tmp_path,
    monkeypatch,
) -> None:
    _forbid_provider_use(monkeypatch)
    run, source = _init(tmp_path, notes=True)
    extraction_hash = sha256_file(run / "extracted-source.proposed.json")
    _import_kcs(run, source, notes=True)
    task = prepare_agent_task("quiz", run, include_all_kcs=True)
    task_package = read_json(Path(task["task_package"]))
    payload = task_package["input_boundary"]["payload"]
    assert task_package["worked_examples"][0]["example_id"] == ("adaptive-slot-with-hint")
    assert payload["runtime"]["variants_per_kc"] is None
    assert payload["runtime"]["expected_question_count"] is None
    assert payload["runtime"]["total_question_budget"] is None
    # Only citations in complete KCs, not raw attachments.
    assert "authoring_context" not in payload
    assert task["next_command"]["argv"][-2:] == ["--task-package", task["task_package"]]
    candidate = _adaptive_candidate(run, source, task, notes=True)
    path = run / "candidate-quiz.json"
    raw = _write_raw(path, candidate)
    result = agent_import("quiz", run, path, task_package=Path(task["task_package"]))
    assert result["status"] == "EXPERIMENTAL_UNAPPROVED"
    assert result["provider_api_calls"] == 0
    assert Path(result["proposed"]).read_bytes() == raw
    assert Path(result["raw_candidate"]).read_bytes() == raw
    assert sha256_file(run / "extracted-source.proposed.json") == extraction_hash
    metrics = read_json(run / "quiz/quiz-run-metrics.json")
    assert metrics["assessment_slot_count"] == 2
    assert metrics["question_count"] == 3
    assert not (run / "extraction-approval.json").exists()


def test_changed_context_rejects_stale_kc_task_and_existing_kc_for_quiz(tmp_path) -> None:
    run, source = _init(tmp_path, notes=True)
    _import_kcs(run, source, notes=True)
    old_task = prepare_agent_task("kc", run, allow_proposed_extraction_demo=True)
    old_raw = (run / "kc-proposed.json").read_bytes()
    agent_context(run, context_texts=("Different lecturer guidance.",))
    with pytest.raises(ValueError, match="changed after the frozen KC task"):
        agent_import(
            "kc",
            run,
            run / "candidate-kc.json",
            task_package=Path(old_task["task_package"]),
        )
    assert (run / "kc-proposed.json").read_bytes() == old_raw
    with pytest.raises(ValueError, match="authoring context SHA-256"):
        prepare_agent_task("quiz", run, include_all_kcs=True)


def test_quiz_slot_counts_are_validated_without_repair_or_overwrite(tmp_path) -> None:
    run, source = _init(tmp_path)
    _import_kcs(run, source)
    task = prepare_agent_task("quiz", run, include_all_kcs=True)
    candidate = _adaptive_candidate(run, source, task)
    candidate["questions"].pop()
    path = run / "invalid-quiz.json"
    raw = _write_raw(path, candidate)
    with pytest.raises(ValidationError, match="require 3 questions"):
        agent_import("quiz", run, path, task_package=Path(task["task_package"]))
    assert not (run / "quiz/quiz-proposed.json").exists()
    archives = list((run / "agent-session/candidates").glob("quiz-*.json"))
    assert len(archives) == 1 and archives[0].read_bytes() == raw


@pytest.mark.parametrize("mistype", ["variant_index", "variant_count", "evidence_page"])
def test_adaptive_import_does_not_coerce_raw_contract_types(tmp_path, mistype) -> None:
    run, source = _init(tmp_path)
    _import_kcs(run, source)
    task = prepare_agent_task("quiz", run, include_all_kcs=True)
    candidate = _adaptive_candidate(run, source, task)
    if mistype == "variant_index":
        candidate["questions"][0]["variant_index"] = True
    elif mistype == "variant_count":
        candidate["assessment_slots"][0]["variant_count"] = "1"
    else:
        candidate["questions"][0]["evidence_refs"][0]["page"] = "1"
    path = run / "mistyped-quiz.json"
    raw = _write_raw(path, candidate)
    with pytest.raises(ValidationError, match="valid integer"):
        agent_import("quiz", run, path, task_package=Path(task["task_package"]))
    assert not (run / "quiz/quiz-proposed.json").exists()
    archived = list((run / "agent-session/candidates").glob("quiz-*.json"))
    assert len(archived) == 1 and archived[0].read_bytes() == raw


def test_quiz_task_detects_changed_kc_and_tampered_policy(tmp_path) -> None:
    run, source = _init(tmp_path)
    kc = _import_kcs(run, source)
    task = prepare_agent_task("quiz", run, include_all_kcs=True)
    candidate = run / "quiz-candidate.json"
    _write_raw(candidate, _adaptive_candidate(run, source, task))
    old_kc_raw = (run / "kc-proposed.json").read_bytes()
    changed = deepcopy(kc)
    changed["leaf_kcs"][0]["name"] = "Changed KC name"
    write_json(run / "kc-proposed.json", changed)
    with pytest.raises(ValueError, match="differs from the frozen Quiz task"):
        agent_import("quiz", run, candidate, task_package=Path(task["task_package"]))
    (run / "kc-proposed.json").write_bytes(old_kc_raw)
    package_path = Path(task["task_package"])
    package = read_json(package_path)
    package["input_boundary"]["payload"]["runtime"]["total_question_budget"] = 100
    write_json(package_path, package)
    with pytest.raises(ValueError, match="fingerprint"):
        agent_import("quiz", run, candidate, task_package=package_path)


def test_explicit_infeasible_budget_is_not_silently_truncated(tmp_path) -> None:
    run, source = _init(tmp_path)
    _import_kcs(run, source)
    with pytest.raises(ValueError, match="budget"):
        prepare_agent_task(
            "quiz",
            run,
            include_all_kcs=True,
            min_slots_per_kc=2,
            variants_per_slot=2,
            total_question_budget=3,
        )
    assert not list((run / "agent-session/tasks").glob("quiz-*.json"))


def test_cli_context_and_adaptive_policy_defaults_are_explicit() -> None:
    args = _parser().parse_args(
        [
            "agent-init",
            "source.pdf",
            "run",
            "--context-file",
            "loose.md",
            "--context-file",
            "diagram.png",
            "--context-text",
            "A lecturer clarification.",
        ]
    )
    assert args.context_file == [Path("loose.md"), Path("diagram.png")]
    assert args.context_text == ["A lecturer clarification."]
    quiz = _parser().parse_args(["agent-task", "quiz", "run", "--include-all-kcs"])
    assert quiz.variants_per_kc is None and quiz.total_question_budget is None
    assert quiz.variants_per_slot is None and quiz.max_slots_per_kc is None


@pytest.mark.parametrize(
    "override",
    [
        ["--variants-per-kc", "2"],
        ["--language", "source"],
        ["--min-slots-per-kc", "1"],
        ["--variants-per-slot", "none"],
        ["--total-question-budget=none"],
    ],
)
def test_cli_rejects_override_of_frozen_import_before_any_write(override) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "agent-import",
                "quiz",
                "absent-run",
                "absent.json",
                "--task-package",
                "task.json",
                *override,
            ]
        )
