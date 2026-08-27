from __future__ import annotations

import json
from pathlib import Path

import pytest

from learning_authoring.artifacts import read_json, sha256_file, write_json
from learning_authoring.cli import PORTAL_BUILD_RECORD, _status, main
from learning_authoring.showcase import MANIFEST_NAME, PublishSafetyError, build_showcase
from tests.test_publish_showcase import _fake_run


def _build_custom(tmp_path: Path, capsys) -> tuple[Path, Path]:
    run_dir = _fake_run(tmp_path, page_count=2)
    output_dir = tmp_path / "custom-portal"
    originals = [
        run_dir / "extracted-source.proposed.json",
        run_dir / "kc-proposed.json",
        run_dir / "quiz" / "quiz-proposed.json",
    ]
    hashes = {path: sha256_file(path) for path in originals}
    assert main(["portal-build", str(run_dir), "--output-dir", str(output_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["built"] is True
    assert {path: sha256_file(path) for path in originals} == hashes
    return run_dir, output_dir


def test_status_recognizes_and_binds_successful_custom_build(tmp_path, capsys) -> None:
    run_dir, output_dir = _build_custom(tmp_path, capsys)
    record = read_json(run_dir / PORTAL_BUILD_RECORD)
    assert record == {
        "schema_version": "portal-build-record.v1",
        "run_dir": str(run_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "manifest_sha256": sha256_file(output_dir / MANIFEST_NAME),
        "source_manifest_sha256": sha256_file(run_dir / "source-manifest.json"),
    }
    assert main(["status", str(run_dir)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["artifacts"]["connected_portal_built"] is True
    assert status["connected_portal"] == {
        "output_dir": str(output_dir.resolve()),
        "manifest": str(output_dir.resolve() / MANIFEST_NAME),
        "recorded_build": True,
    }


def test_status_preserves_legacy_default_portal_discovery(tmp_path) -> None:
    run_dir = _fake_run(tmp_path, page_count=1)
    output_dir = run_dir / "connected-portal"
    build_showcase(run_dir, output_dir)
    assert not (run_dir / PORTAL_BUILD_RECORD).exists()
    assert _status(run_dir)["artifacts"]["connected_portal_built"] is True
    assert _status(run_dir)["connected_portal"]["recorded_build"] is False


@pytest.mark.parametrize("damage", ["missing", "corrupt", "tampered", "index", "source"])
def test_status_fails_closed_when_custom_build_is_no_longer_valid(
    tmp_path, capsys, damage
) -> None:
    run_dir, output_dir = _build_custom(tmp_path, capsys)
    manifest = output_dir / MANIFEST_NAME
    if damage == "missing":
        manifest.unlink()
    elif damage == "corrupt":
        manifest.write_text("not JSON", encoding="utf-8")
    elif damage == "tampered":
        # Even an otherwise valid JSON re-serialization must match the recorded bytes.
        manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    elif damage == "index":
        (output_dir / "index.html").unlink()
    else:
        source = run_dir / "source-manifest.json"
        source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    status = _status(run_dir)
    assert status["artifacts"]["connected_portal_built"] is False
    assert "connected_portal" not in status


@pytest.mark.parametrize("field", ["source_run", "source"])
def test_manifest_must_belong_to_run_even_if_recorded_digest_matches(
    tmp_path, capsys, field
) -> None:
    run_dir, output_dir = _build_custom(tmp_path, capsys)
    manifest_path = output_dir / MANIFEST_NAME
    manifest = read_json(manifest_path)
    manifest[field] = "other-run" if field == "source_run" else {"filename": "other.pdf"}
    write_json(manifest_path, manifest)
    record_path = run_dir / PORTAL_BUILD_RECORD
    record = read_json(record_path)
    record["manifest_sha256"] = sha256_file(manifest_path)
    write_json(record_path, record)
    assert _status(run_dir)["artifacts"]["connected_portal_built"] is False


def test_invalid_record_does_not_silently_fall_back_to_default_portal(tmp_path, capsys) -> None:
    run_dir, _ = _build_custom(tmp_path, capsys)
    build_showcase(run_dir, run_dir / "connected-portal")
    (run_dir / PORTAL_BUILD_RECORD).write_text("{}", encoding="utf-8")
    assert _status(run_dir)["artifacts"]["connected_portal_built"] is False


def test_record_cannot_be_reused_by_another_run_with_same_name(tmp_path, capsys) -> None:
    run_dir, _ = _build_custom(tmp_path, capsys)
    other_run = _fake_run(tmp_path / "other", page_count=2)
    assert other_run.name == run_dir.name
    (other_run / PORTAL_BUILD_RECORD).write_bytes((run_dir / PORTAL_BUILD_RECORD).read_bytes())
    assert _status(other_run)["artifacts"]["connected_portal_built"] is False


def test_failed_build_does_not_record_a_success(tmp_path, monkeypatch, capsys) -> None:
    run_dir = _fake_run(tmp_path, page_count=1)

    def fail(*args, **kwargs):
        raise PublishSafetyError("synthetic build failure")

    monkeypatch.setattr("learning_authoring.cli.build_showcase", fail)
    assert main(["portal-build", str(run_dir)]) == 1
    assert json.loads(capsys.readouterr().err)["built"] is False
    assert not (run_dir / PORTAL_BUILD_RECORD).exists()
    assert _status(run_dir)["artifacts"]["connected_portal_built"] is False


def test_unverified_builder_result_does_not_record_a_success(
    tmp_path, monkeypatch, capsys
) -> None:
    run_dir = _fake_run(tmp_path, page_count=1)
    monkeypatch.setattr("learning_authoring.cli.build_showcase", lambda *args, **kwargs: {})
    assert main(["portal-build", str(run_dir)]) == 1
    assert json.loads(capsys.readouterr().err)["built"] is False
    assert not (run_dir / PORTAL_BUILD_RECORD).exists()
    assert _status(run_dir)["artifacts"]["connected_portal_built"] is False


def test_failed_rebuild_keeps_previous_verified_build_record(tmp_path, monkeypatch, capsys) -> None:
    run_dir, _ = _build_custom(tmp_path, capsys)
    previous = (run_dir / PORTAL_BUILD_RECORD).read_bytes()

    def fail(*args, **kwargs):
        raise PublishSafetyError("synthetic rebuild failure")

    monkeypatch.setattr("learning_authoring.cli.build_showcase", fail)
    assert main(["portal-build", str(run_dir), "--output-dir", str(tmp_path / "failed")]) == 1
    assert json.loads(capsys.readouterr().err)["built"] is False
    assert (run_dir / PORTAL_BUILD_RECORD).read_bytes() == previous
    assert _status(run_dir)["artifacts"]["connected_portal_built"] is True
