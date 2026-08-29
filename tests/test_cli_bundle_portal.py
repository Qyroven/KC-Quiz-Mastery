from __future__ import annotations

import json
from pathlib import Path

from learning_authoring.cli import NATIVE_COMMANDS, _parser, main
from tests.test_bundle_portal import _bundle


def test_bundle_portal_cli_builds_fresh_connected_review(tmp_path: Path, capsys) -> None:
    bundle, _, _ = _bundle(tmp_path)
    output = tmp_path / "connected-review"

    exit_code = main(
        [
            "bundle-portal-build",
            str(tmp_path),
            "--output-dir",
            str(output),
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert result["built"] is True
    assert result["deployment_performed"] is False
    assert result["output_dir"] == str(output.resolve())
    assert result["manifest"]["source_bundle_sha256"] == bundle.bundle_sha256
    assert (output / "index.html").is_file()


def test_bundle_portal_cli_accepts_explicit_bound_artifact_paths() -> None:
    args = _parser().parse_args(
        [
            "bundle-portal-build",
            "bundle",
            "--output-dir",
            "review",
            "--kc",
            "bundle/shared-kcs.json",
            "--quiz-dir",
            "bundle/questions",
        ]
    )

    assert args.command == "bundle-portal-build"
    assert args.bundle_root == Path("bundle")
    assert args.output_dir == Path("review")
    assert args.kc == Path("bundle/shared-kcs.json")
    assert args.quiz_dir == Path("bundle/questions")
    assert args.command in NATIVE_COMMANDS


def test_bundle_portal_cli_reports_fail_closed_error(tmp_path: Path, capsys) -> None:
    _bundle(tmp_path)
    output = tmp_path / "already-exists"
    output.mkdir()

    exit_code = main(
        [
            "bundle-portal-build",
            str(tmp_path),
            "--output-dir",
            str(output),
        ]
    )
    captured = capsys.readouterr()
    result = json.loads(captured.err)

    assert exit_code == 1
    assert captured.out == ""
    assert result["built"] is False
    assert result["output_dir"] == str(output.resolve())
    assert "fresh path" in result["error"]
