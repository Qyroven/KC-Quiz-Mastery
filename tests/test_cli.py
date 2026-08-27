from __future__ import annotations

import json
import os

import pytest

from learning_authoring.cli import _config, _load_env_before_parser, _parser, main
from learning_authoring.showcase import build_showcase
from tests.conftest import write_blank_pdf
from tests.test_publish_showcase import _fake_run


def test_env_file_is_loaded_before_argument_defaults(tmp_path, monkeypatch) -> None:
    pytest.importorskip("dotenv", reason="only the optional legacy-api extra loads .env")
    env_file = tmp_path / ".env"
    env_file.write_text("LEARNING_AUTHORING_MODEL=env-model\n", encoding="utf-8")
    monkeypatch.setenv("LEARNING_AUTHORING_MODEL", "inherited-model")
    argv = ["--env-file", str(env_file), "extract", "source.pdf", "run"]
    _load_env_before_parser(argv)
    args = _parser().parse_args(argv)
    assert args.model == "env-model"


def test_explicit_env_file_clears_inherited_optional_base_url(tmp_path, monkeypatch) -> None:
    pytest.importorskip("dotenv", reason="only the optional legacy-api extra loads .env")
    env_file = tmp_path / ".env"
    env_file.write_text("LEARNING_AUTHORING_MODEL=gpt-test\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_BASE_URL", "invalid-inherited-endpoint")
    _load_env_before_parser(["--env-file", str(env_file), "doctor"])
    assert "OPENAI_BASE_URL" not in os.environ


def test_kc_preview_command_does_not_require_api_options() -> None:
    args = _parser().parse_args(["kc-preview", "run"])
    assert args.command == "kc-preview"
    assert args.run_dir.name == "run"


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


def test_quiz_cli_exposes_one_canonical_flow() -> None:
    parser = _parser()
    args = parser.parse_args(
        ["quiz-preview", "run", "--include-kc", "KC-001", "--variants-per-kc", "2"]
    )

    assert args.command == "quiz-preview"
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


def test_portal_build_defaults_to_run_local_connected_portal(
    tmp_path, monkeypatch, capsys
) -> None:
    run_dir = _fake_run(tmp_path, page_count=1)
    captured: dict[str, object] = {}

    def fake_build(run, output, *, review_files, review_backend=None):
        captured.update(
            run=run,
            output=output,
            review_files=review_files,
            review_backend=review_backend,
        )
        return build_showcase(run, output, review_files=review_files, review_backend=review_backend)

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


def test_extraction_cli_exposes_repair_guard_defaults_and_overrides() -> None:
    parser = _parser()
    defaults = _config(parser.parse_args(["extract", "source.pdf", "run"]))

    assert defaults.repair_max_candidate_pages == 12
    assert defaults.repair_systemic_guard_min_candidate_pages == 4
    assert defaults.repair_systemic_guard_max_page_fraction == 0.5

    custom = _config(
        parser.parse_args(
            [
                "extract",
                "source.pdf",
                "run",
                "--repair-max-candidate-pages",
                "none",
                "--repair-systemic-guard-min-candidate-pages",
                "7",
                "--repair-systemic-guard-max-page-fraction",
                "0.75",
            ]
        )
    )

    assert custom.repair_max_candidate_pages is None
    assert custom.repair_systemic_guard_min_candidate_pages == 7
    assert custom.repair_systemic_guard_max_page_fraction == 0.75
