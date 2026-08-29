from __future__ import annotations

from learning_authoring.artifacts import read_json, write_json
from learning_authoring.legacy_api.batch import create_batch_plan, preflight_batch
from tests.conftest import write_blank_pdf


def make_phase(source_dir) -> None:
    source_dir.mkdir()
    for day in range(1, 16):
        write_blank_pdf(source_dir / f"day{day:02d}.pdf")


def test_batch_plan_auto_selects_only_unambiguous_days(tmp_path) -> None:
    source_dir = tmp_path / "slides"
    make_phase(source_dir)
    write_blank_pdf(source_dir / "day04_v2.pdf")
    manifest = tmp_path / "batch.json"
    plan = create_batch_plan(source_dir, manifest, runs_dir=tmp_path / "runs")
    day4 = next(row for row in plan["days"] if row["day"] == 4)
    assert plan["selection_status"] == "incomplete"
    assert day4["selected_pdf"] is None
    assert len(day4["candidates"]) == 2


def test_batch_preflight_requires_one_selection_per_day(tmp_path) -> None:
    source_dir = tmp_path / "slides"
    make_phase(source_dir)
    manifest = tmp_path / "batch.json"
    create_batch_plan(source_dir, manifest, runs_dir=tmp_path / "runs")
    payload = read_json(manifest)
    payload["days"][0]["selected_pdf"] = None
    write_json(manifest, payload)
    result = preflight_batch(manifest)
    assert result["ready"] is False
    assert "day 01 has no selected_pdf" in result["errors"]


def test_batch_preflight_accepts_exactly_15_valid_pdfs(tmp_path) -> None:
    source_dir = tmp_path / "slides"
    make_phase(source_dir)
    manifest = tmp_path / "batch.json"
    create_batch_plan(source_dir, manifest, runs_dir=tmp_path / "runs")
    result = preflight_batch(manifest)
    assert result["ready"] is True
    assert result["document_count"] == 15
    assert result["total_pages"] == 15
