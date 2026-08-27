from __future__ import annotations

import shutil

import pytest

from learning_authoring.artifacts import RunArtifacts, read_json, write_json
from learning_authoring.source import preflight_source, prepare_or_reuse_source
from tests.conftest import file_sha256, write_blank_pdf


def test_partial_source_preparation_resumes(tmp_path) -> None:
    pdf = tmp_path / "lesson.pdf"
    write_blank_pdf(pdf)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    artifacts = RunArtifacts(run_dir)
    shutil.copy2(pdf, artifacts.source_pdf)
    write_json(
        artifacts.source_preparation,
        {
            "preparation_version": "source-preparation.v1",
            "source_sha256": file_sha256(pdf),
            "source_filename": pdf.name,
            "render_dpi": 72,
        },
    )
    source, manifest, resumed = prepare_or_reuse_source(pdf, run_dir, render_dpi=72)
    assert source.page_count == 1
    assert manifest["rendered_page_count"] == 1
    assert resumed is True
    assert artifacts.source_manifest.is_file()
    assert not artifacts.source_preparation.exists()


def test_completed_source_is_reused(tmp_path) -> None:
    pdf = tmp_path / "lesson.pdf"
    write_blank_pdf(pdf)
    run_dir = tmp_path / "run"
    prepare_or_reuse_source(pdf, run_dir, render_dpi=72)
    _, _, reused = prepare_or_reuse_source(pdf, run_dir, render_dpi=72)
    assert reused is True


def test_source_preflight_reports_identity_without_creating_fresh_run(tmp_path) -> None:
    pdf = tmp_path / "lesson.pdf"
    write_blank_pdf(pdf)
    run_dir = tmp_path / "new-run"
    source_bytes = pdf.read_bytes()

    result = preflight_source(pdf, run_dir)

    source_keys = ("filename", "absolute_path", "size_bytes", "sha256", "page_count")
    assert {key: result[key] for key in source_keys} == {
        "filename": "lesson.pdf",
        "absolute_path": str(pdf.resolve()),
        "size_bytes": len(source_bytes),
        "sha256": file_sha256(pdf),
        "page_count": 1,
    }
    assert result["run_dir_state"] == "fresh"
    assert result["ready"] is True
    assert not run_dir.exists()
    assert pdf.read_bytes() == source_bytes


def test_source_preflight_distinguishes_empty_and_reusable_run_dirs(tmp_path) -> None:
    pdf = tmp_path / "lesson.pdf"
    write_blank_pdf(pdf)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert preflight_source(pdf, empty)["run_dir_state"] == "empty"

    reusable = tmp_path / "reusable"
    reusable.mkdir()
    write_json(
        RunArtifacts(reusable).source_preparation,
        {"source_sha256": file_sha256(pdf), "render_dpi": 160},
    )
    before = RunArtifacts(reusable).source_preparation.read_bytes()
    result = preflight_source(pdf, reusable)

    assert result["run_dir_state"] == "reusable-same-source"
    assert result["ready"] is True
    assert RunArtifacts(reusable).source_preparation.read_bytes() == before


def test_source_preflight_validates_completed_run_integrity(tmp_path) -> None:
    pdf = tmp_path / "lesson.pdf"
    write_blank_pdf(pdf)
    run_dir = tmp_path / "run"
    prepare_or_reuse_source(pdf, run_dir, render_dpi=72)

    result = preflight_source(pdf, run_dir, render_dpi=72)
    assert result["run_dir_state"] == "reusable-same-source"

    (run_dir / "pages" / "page-0001.png").write_bytes(b"broken")
    result = preflight_source(pdf, run_dir, render_dpi=72)
    assert result["run_dir_state"] == "conflict"
    assert "rendered page image" in result["run_dir_detail"]


def test_source_preflight_rejects_dpi_page_count_and_stored_pdf_drift(tmp_path) -> None:
    pdf = tmp_path / "lesson.pdf"
    write_blank_pdf(pdf)
    run_dir = tmp_path / "run"
    prepare_or_reuse_source(pdf, run_dir, render_dpi=72)

    assert preflight_source(pdf, run_dir)["run_dir_state"] == "conflict"

    manifest = read_json(RunArtifacts(run_dir).source_manifest)
    manifest["source"]["page_count"] = 2
    write_json(RunArtifacts(run_dir).source_manifest, manifest)
    result = preflight_source(pdf, run_dir, render_dpi=72)
    assert result["run_dir_state"] == "conflict"
    assert "page count" in result["run_dir_detail"]

    manifest["source"]["page_count"] = 1
    write_json(RunArtifacts(run_dir).source_manifest, manifest)
    RunArtifacts(run_dir).source_pdf.write_bytes(b"different source")
    result = preflight_source(pdf, run_dir, render_dpi=72)
    assert result["run_dir_state"] == "conflict"
    assert "stored source PDF" in result["run_dir_detail"]


@pytest.mark.parametrize("identity", [None, "different", "invalid"])
def test_source_preflight_reports_nonempty_run_conflicts(tmp_path, identity) -> None:
    pdf = tmp_path / "lesson.pdf"
    write_blank_pdf(pdf)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    artifacts = RunArtifacts(run_dir)
    if identity is None:
        (run_dir / "unrelated.txt").write_text("occupied", encoding="utf-8")
    elif identity == "different":
        write_json(artifacts.source_preparation, {"source_sha256": "0" * 64})
    else:
        artifacts.source_preparation.write_text("not json", encoding="utf-8")

    result = preflight_source(pdf, run_dir)

    assert result["run_dir_state"] == "conflict"
    assert result["ready"] is False


def test_source_preflight_rejects_invalid_pdf_without_creating_run(tmp_path) -> None:
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"not a PDF")
    run_dir = tmp_path / "run"

    with pytest.raises(ValueError, match="invalid PDF"):
        preflight_source(pdf, run_dir)

    assert not run_dir.exists()


def test_extraction_source_gate_rejects_invalid_pdf_before_writing(tmp_path) -> None:
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"not a PDF")
    run_dir = tmp_path / "run"

    with pytest.raises(ValueError, match="invalid PDF"):
        prepare_or_reuse_source(pdf, run_dir, render_dpi=72)

    assert not run_dir.exists()
