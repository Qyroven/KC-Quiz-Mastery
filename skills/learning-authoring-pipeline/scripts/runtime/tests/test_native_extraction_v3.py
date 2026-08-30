from __future__ import annotations

from pathlib import Path

from learning_authoring.agent_session import agent_init, prepare_agent_task
from learning_authoring.artifacts import read_json
from learning_authoring.contracts import ExtractedSource
from tests.conftest import write_blank_pdf, write_text_pdf


def test_agent_init_creates_deterministic_native_text_extraction(tmp_path) -> None:
    pdf = tmp_path / "native.pdf"
    run = tmp_path / "run"
    write_text_pdf(pdf)

    initialized = agent_init(pdf, run)
    extracted = ExtractedSource.model_validate(read_json(run / "extracted-source.proposed.json"))

    assert initialized["extraction"]["method"] == "native-text-geometry.v1"
    assert [block.content for block in extracted.pages[0].blocks] == [
        "Native title",
        "Second source line",
    ]
    assert all(
        block.region.localization_status == "located" for block in extracted.pages[0].blocks
    )
    assert len(
        {
            tuple(block.region.geometry[name] for name in ("x", "y", "w", "h"))
            for block in extracted.pages[0].blocks
        }
    ) == 2
    assert read_json(run / "extraction-metadata.json")["semantic_generation_performed"] is False
    assert (run / "extraction-review.html").is_file()


def test_blank_native_page_is_explicit_visual_review_not_invented_text(tmp_path) -> None:
    pdf = tmp_path / "blank.pdf"
    run = tmp_path / "run"
    write_blank_pdf(pdf)

    agent_init(pdf, run)
    extracted = ExtractedSource.model_validate(read_json(run / "extracted-source.proposed.json"))

    assert extracted.pages[0].blocks == []
    assert [warning.code for warning in extracted.pages[0].warnings] == ["NO_NATIVE_TEXT"]
    assert read_json(run / "run-metrics.json")["targeted_visual_review_page_count"] == 1


def test_legacy_extraction_task_is_labeled_and_not_part_of_v3_default(tmp_path) -> None:
    pdf = tmp_path / "blank.pdf"
    run = tmp_path / "run"
    write_blank_pdf(pdf)
    agent_init(pdf, run)

    task = prepare_agent_task("extraction", run)
    package = read_json(Path(task["task_package"]))

    assert package["instructions"].startswith("LEGACY ARTIFACT COMPATIBILITY ONLY")
    assert (run / "extracted-source.proposed.json").is_file()
