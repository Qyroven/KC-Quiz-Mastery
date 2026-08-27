"""Offline protocol tests; synthetic verdicts do not measure teaching quality."""

from __future__ import annotations

from pathlib import Path

import pytest

from learning_authoring.agent_session import agent_import, prepare_agent_task
from learning_authoring.artifacts import read_json, sha256_file, write_json
from learning_authoring.cli import main
from learning_authoring.quiz_review import build_quiz_review
from learning_authoring.quiz_review_state import load_quiz_semantic_state, quiz_review_material
from learning_authoring.showcase import PublishSafetyError, build_showcase
from tests.test_agent_context_slots import _adaptive_candidate, _import_kcs, _init
from tests.test_agent_session import _forbid_provider_use, _write_raw
from tests.test_quiz_semantics import _flag, _report


def _quiz_run(tmp_path: Path, *, notes: bool = False) -> Path:
    run, source = _init(tmp_path, notes=notes)
    _import_kcs(run, source, notes=notes)
    task = prepare_agent_task("quiz", run, include_all_kcs=True)
    candidate = _adaptive_candidate(run, source, task, notes=notes)
    path = run / "candidate-quiz.json"
    _write_raw(path, candidate)
    agent_import("quiz", run, path, task_package=Path(task["task_package"]))
    return run


def _review_task(run: Path, *, mode: str = "independent") -> tuple[dict, dict]:
    task = prepare_agent_task("quiz-review", run, reviewer_mode=mode)
    package = read_json(Path(task["task_package"]))
    report = _report(read_json(run / "quiz" / "quiz-proposed.json"))
    report["source_ref"] = package["input_boundary"]["expected_source_ref"]
    report["reviewer"]["mode"] = mode
    return task, report


def _import_report(run: Path, task: dict, report: dict) -> tuple[dict, bytes]:
    path = run / "review-candidate.json"
    raw = _write_raw(path, report)
    result = agent_import("quiz-review", run, path, task_package=Path(task["task_package"]))
    return result, raw


def test_review_keeps_key_out_of_initial_packet_and_binds_source(tmp_path, monkeypatch) -> None:
    _forbid_provider_use(monkeypatch)
    run = _quiz_run(tmp_path)
    task, _ = _review_task(run)
    boundary = read_json(Path(task["task_package"]))["input_boundary"]
    assert boundary["reviewer_mode"] == "independent"
    for question in boundary["learner_questions"]:
        assert not {"hints", "correct_answer", "rubric", "answer_explanation"} & question.keys()
    companion_path = Path(boundary["answer_material"]["path"])
    companion = read_json(companion_path)
    assert sha256_file(companion_path) == boundary["answer_material"]["sha256"]
    assert all("hints" in question for question in companion["questions"])
    assert all("correct_answer" in question for question in companion["questions"])
    assert boundary["source_locators"]["page_images"]
    assert boundary["limits"]["human_approval_created"] is False
    assert task["next_command"]["argv"][1:3] == ["agent-import", "quiz-review"]


@pytest.mark.parametrize("notes", [False, True])
def test_initial_report_and_quiz_bytes_preserved_no_api_or_approval(
    tmp_path, monkeypatch, notes,
) -> None:
    _forbid_provider_use(monkeypatch)
    run = _quiz_run(tmp_path, notes=notes)
    before = {
        relative: sha256_file(run / relative)
        for relative in (
            "source.pdf", "extracted-source.proposed.json", "kc-proposed.json",
            "quiz/quiz-proposed.json", "quiz/quiz-input.json",
        )
    }
    task, report = _review_task(run)
    result, raw = _import_report(run, task, report)
    assert result["status"] == "PASS"
    assert result["summary"]["counts"]["PASS"] == 3
    assert result["original_quiz_modified"] is False
    assert result["provider_api_calls"] == 0
    assert result["approval_status"] == "EXPERIMENTAL_UNAPPROVED"
    assert Path(result["report"]).read_bytes() == raw
    assert Path(result["raw_candidate"]).read_bytes() == raw
    assert all(sha256_file(run / name) == digest for name, digest in before.items())
    state = load_quiz_semantic_state(run)
    assert state["status"] == "PASS"
    assert state["report"] == report
    assert state["approved"] is False
    assert str(run) not in str(state)
    if notes:
        material = quiz_review_material(run)
        assert material["source_locators"]["page_images"] == []
        assert material["source_locators"]["context_attachments"][0]["context_id"] == "CTX-001"
    assert not (run / "extraction-approval.json").exists()


def test_reject_findings_are_retained_and_do_not_rewrite_or_drop_items(tmp_path) -> None:
    run = _quiz_run(tmp_path)
    original = (run / "quiz/quiz-proposed.json").read_bytes()
    task, report = _review_task(run)
    _flag(report, "hints", "REJECT", index=1)
    result, _ = _import_report(run, task, report)
    assert result["status"] == "REJECT"
    assert result["summary"]["counts"]["REJECT"] == 1
    assert result["summary"]["counts"]["PASS"] == 2
    assert (run / "quiz/quiz-proposed.json").read_bytes() == original
    assert len(load_quiz_semantic_state(run)["questions"]) == 3


@pytest.mark.parametrize("limited", ["self_review", "partial", "explicit_limitation"])
def test_limited_review_is_not_presented_as_pass(tmp_path, limited) -> None:
    run = _quiz_run(tmp_path)
    task, report = _review_task(
        run, mode="self_review" if limited == "self_review" else "independent",
    )
    if limited != "self_review":
        report["scope"]["limitations"] = ["Original source could not be fully inspected."]
        if limited == "partial":
            report["scope"]["source_coverage"] = "partial"
            report["scope"]["checked_source_pages"] = []
    result, _ = _import_report(run, task, report)
    assert result["status"] == "REVIEW"
    assert load_quiz_semantic_state(run)["counts"]["PASS"] == 0


@pytest.mark.parametrize("change", ["quiz", "kc", "extraction", "pdf", "page", "context"])
def test_stale_source_or_quiz_cannot_keep_old_green_report(tmp_path, change) -> None:
    run = _quiz_run(tmp_path, notes=change == "context")
    task, report = _review_task(run)
    _import_report(run, task, report)
    material = quiz_review_material(run)
    if change in {"quiz", "kc", "extraction"}:
        path = Path(material["bindings"][change]["path"])
    elif change == "pdf":
        path = run / "source.pdf"
    elif change == "page":
        path = Path(material["source_locators"]["page_images"][0]["path"])
    else:
        path = Path(material["source_locators"]["context_attachments"][0]["path"])
    path.write_bytes(path.read_bytes() + b" \n")
    state = load_quiz_semantic_state(run)
    assert state["status"] == "STALE"
    assert state["report"] is None
    assert str(run) not in str(state)
    with pytest.raises((ValueError, RuntimeError)):
        _import_report(run, task, report)


@pytest.mark.parametrize("change", ["key_companion", "task", "report", "record", "mode"])
def test_review_integrity_and_execution_provenance_cannot_be_silently_changed(
    tmp_path, change,
) -> None:
    run = _quiz_run(tmp_path)
    task, report = _review_task(run)
    _import_report(run, task, report)
    if change == "key_companion":
        package = read_json(Path(task["task_package"]))
        path = Path(package["input_boundary"]["answer_material"]["path"])
        path.write_bytes(path.read_bytes() + b" \n")
        with pytest.raises(ValueError, match="changed after review"):
            _import_report(run, task, report)
        return
    if change == "task":
        path = Path(task["task_package"])
        payload = read_json(path)
        payload["input_boundary"]["reviewer_mode"] = "self_review"
        write_json(path, payload)
    elif change == "report":
        path = run / "quiz/quiz-semantic-audit.json"
        path.write_bytes(path.read_bytes() + b" \n")
    else:
        path = run / "quiz/quiz-semantic-metadata.json"
        payload = read_json(path)
        if change == "record":
            payload.pop("reviewer_mode")
        else:
            payload["reviewer_mode"] = "self_review"
        write_json(path, payload)
    assert load_quiz_semantic_state(run)["status"] == "STALE"


def test_invalid_report_is_archived_without_replacing_previous_valid_report(tmp_path) -> None:
    run = _quiz_run(tmp_path)
    task, report = _review_task(run)
    good, original = _import_report(run, task, report)
    report["questions"][0]["kc_id"] = "KC-999"
    bad_path = run / "bad-report.json"
    raw = _write_raw(bad_path, report)
    with pytest.raises(ValueError, match="changed KC"):
        agent_import("quiz-review", run, bad_path, task_package=Path(task["task_package"]))
    assert Path(good["report"]).read_bytes() == original
    assert load_quiz_semantic_state(run)["status"] == "PASS"
    assert any(
        path.read_bytes() == raw
        for path in (run / "agent-session/candidates").glob("quiz-review-*.json")
    )


def test_missing_review_is_gray_and_cli_requires_frozen_review_mode(tmp_path) -> None:
    run = _quiz_run(tmp_path)
    assert load_quiz_semantic_state(run)["status"] == "NOT_REVIEWED"
    with pytest.raises(SystemExit):
        main(["agent-task", "kc", str(run), "--reviewer-mode", "self_review"])
    with pytest.raises(SystemExit):
        main(["agent-import", "quiz-review", str(run), str(run / "missing.json")])
    task, report = _review_task(run)
    candidate = run / "candidate-review.json"
    _write_raw(candidate, report)
    with pytest.raises(SystemExit):
        main(["agent-import", "quiz-review", str(run), str(candidate),
              "--task-package", task["task_package"], "--reviewer-mode", "independent"])


def test_portal_binds_semantic_payload_and_does_not_publish_private_review_material(tmp_path):
    run = _quiz_run(tmp_path)
    task, report = _review_task(run)
    _import_report(run, task, report)
    portal = tmp_path / "portal"
    manifest = build_showcase(run, portal)
    assert manifest["quiz_initial_check"]["status"] == "PASS"
    assert manifest["stage_status"]["quiz"] == "EXPERIMENTAL_UNAPPROVED"
    assert not list(portal.rglob("*semantic-metadata*"))
    assert not list(portal.rglob("*review-materials*"))
    raw = read_json(run / "quiz/quiz-semantic-audit.json")
    raw["scope"]["limitations"] = ["Changed after import."]
    write_json(run / "quiz/quiz-semantic-audit.json", raw)
    with pytest.raises(PublishSafetyError, match="semantic status"):
        build_showcase(run, tmp_path / "stale-portal")
    build_quiz_review(run, candidate_dir=run / "quiz")
    manifest = build_showcase(run, tmp_path / "rebuilt-portal")
    assert manifest["quiz_initial_check"]["status"] == "STALE"
