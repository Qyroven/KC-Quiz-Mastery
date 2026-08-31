from __future__ import annotations

import json

from learning_authoring.cli import _parser, main
from learning_authoring.product.showcase import build_showcase
from tests.conftest import write_blank_pdf
from tests.test_publish_showcase import _fake_run


def test_source_preflight_cli_returns_nonzero_for_conflict(tmp_path, capsys) -> None:
    pdf = tmp_path / "lesson.pdf"
    write_blank_pdf(pdf)
    run_dir = tmp_path / "occupied"
    run_dir.mkdir()
    (run_dir / "unrelated.txt").write_text("occupied", encoding="utf-8")

    exit_code = main(["source-preflight", str(pdf), str(run_dir)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["run_dir_state"] == "conflict"
    assert output["ready"] is False


def test_source_preflight_cli_succeeds_without_creating_run(tmp_path, capsys) -> None:
    pdf = tmp_path / "lesson.pdf"
    write_blank_pdf(pdf)
    run_dir = tmp_path / "new-run"

    exit_code = main(["source-preflight", str(pdf), str(run_dir)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["run_dir_state"] == "fresh"
    assert output["page_count"] == 1
    assert not run_dir.exists()


def test_source_preflight_cli_returns_nonzero_for_invalid_pdf(tmp_path, capsys) -> None:
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"not a PDF")

    exit_code = main(["source-preflight", str(pdf), str(tmp_path / "run")])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert "invalid PDF" in json.loads(captured.err)["error"]


def test_quiz_agent_task_exposes_one_canonical_flow() -> None:
    parser = _parser()
    args = parser.parse_args(
        ["agent-task", "quiz", "run", "--include-kc", "KC-001", "--variants-per-kc", "2"]
    )

    assert args.command == "agent-task"
    assert args.stage == "quiz"
    assert args.include_kc == ["KC-001"]
    assert args.variants_per_kc == 2


def test_experimental_quiz_commands_are_not_public() -> None:
    help_text = _parser().format_help()

    for removed in (
        "assessment-plan",
        "quiz-blueprint",
        "quiz-candidate",
        "quiz-direct",
        "kc-to-quiz",
    ):
        assert removed not in help_text


def test_status_reports_current_artifacts_without_legacy_provider_state(tmp_path, capsys) -> None:
    historical = {
        "background-checkpoint.json": '{"response_id":"old-response","status":"queued"}',
        "kc-request-preview.json": "{}",
        "quiz/quiz-request-preview.json": "{}",
    }
    for relative, content in historical.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    assert main(["status", str(tmp_path)]) == 0
    status = json.loads(capsys.readouterr().out)

    assert "extraction_proposed" in status["artifacts"]
    assert "quiz_proposed" in status["artifacts"]
    assert "kc_request_preview" not in status["artifacts"]
    assert "quiz_request_preview" not in status["artifacts"]
    assert "response_id" not in status and "response_status" not in status
    assert all((tmp_path / path).read_text() == data for path, data in historical.items())


def test_portal_build_defaults_to_run_local_connected_portal(
    tmp_path, monkeypatch, capsys
) -> None:
    run_dir = _fake_run(tmp_path, page_count=1)
    captured: dict[str, object] = {}

    def fake_build(run, output, *, review_files, review_backend=None, include_learning=False):
        captured.update(
            run=run,
            output=output,
            review_files=review_files,
            review_backend=review_backend,
            include_learning=include_learning,
        )
        return build_showcase(
            run, output, review_files=review_files, review_backend=review_backend,
            include_learning=include_learning,
        )

    monkeypatch.setattr("learning_authoring.cli.build_showcase", fake_build)

    exit_code = main(["portal-build", str(run_dir)])
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["run"] == run_dir
    assert captured["output"] == run_dir / "connected-portal"
    assert captured["review_backend"] is None
    assert result["built"] is True
    assert result["deployment_performed"] is False


def test_portal_build_accepts_explicit_review_filenames() -> None:
    args = _parser().parse_args(
        [
            "portal-build",
            "run",
            "--extractor-review",
            "extractor-a.html",
            "--kc-recall-review",
            "kc-a.html",
            "--kc-scroll-review",
            "kc-b.html",
            "--quiz-review",
            "quiz-a.html",
        ]
    )

    assert args.command == "portal-build"
    assert args.extractor_review == "extractor-a.html"
    assert args.kc_recall_review == "kc-a.html"
    assert args.kc_scroll_review == "kc-b.html"
    assert args.quiz_review == "quiz-a.html"
