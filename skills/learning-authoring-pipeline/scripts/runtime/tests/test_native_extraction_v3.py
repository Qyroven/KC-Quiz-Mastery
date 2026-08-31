from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from learning_authoring.agent_session import agent_init, prepare_agent_task
from learning_authoring.artifacts import read_json
from learning_authoring.contracts import ExtractedSource
from learning_authoring.native_extraction import _Glyph, _layout_text, _page_from_text_layer
from tests.conftest import write_blank_pdf, write_text_pdf


def test_agent_init_creates_deterministic_native_text_extraction(tmp_path) -> None:
    pdf = tmp_path / "native.pdf"
    run = tmp_path / "run"
    write_text_pdf(pdf)

    initialized = agent_init(pdf, run)
    extracted = ExtractedSource.model_validate(read_json(run / "extracted-source.proposed.json"))

    assert initialized["extraction"]["method"] == "native-text-geometry.v2"
    assert [block.content for block in extracted.pages[0].blocks] == [
        "Native title",
        "Second source line",
    ]
    assert all(block.region.localization_status == "located" for block in extracted.pages[0].blocks)
    assert (
        len(
            {
                tuple(block.region.geometry[name] for name in ("x", "y", "w", "h"))
                for block in extracted.pages[0].blocks
            }
        )
        == 2
    )
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


def test_layout_keeps_indentation_columns_and_split_punctuation_without_invented_words():
    def text(value, x, y):
        return [
            _Glyph(char, (x + i * 6, y, x + (i + 1) * 6, y + 10)) for i, char in enumerate(value)
        ]

    glyphs = text("value", 10, 100)
    glyphs += [_Glyph("\n", None), _Glyph("_", (40, 98, 46, 99)), _Glyph("\n", None)]
    glyphs += text("count", 46, 100)
    glyphs += [_Glyph("\n", None)] + text("next()", 34, 80)
    glyphs += [_Glyph("\n", None)] + text("left", 10, 60) + text("right", 70, 60)
    result = _layout_text(glyphs)
    assert result == "value_count\n    next()\nleft      right"
    assert _layout_text([_Glyph("unlocated", None)]) == "unlocated"


def test_layout_retains_narrow_explicit_spaces_and_falls_back_for_unlocated_content():
    glyphs = [_Glyph("W", (0, 0, 12, 10)), _Glyph(" ", None), _Glyph("i", (14, 0, 16, 10))]
    assert _layout_text(glyphs) == "W i"
    glyphs.append(_Glyph("?", None))
    assert _layout_text(glyphs) == "W i?"


def test_graphics_with_valid_native_text_still_receive_visual_triage():
    text_page = SimpleNamespace(
        count_chars=lambda: 1,
        get_text_range=lambda *args: "A",
        get_charbox=lambda index: (10, 20, 16, 30),
        close=lambda: None,
    )
    page = SimpleNamespace(
        get_size=lambda: (200, 200),
        get_textpage=lambda: text_page,
        get_objects=lambda **kwargs: iter([object()]),
    )
    result = _page_from_text_layer(page, 1)
    assert result.blocks[0].content == "A"
    assert result.blocks[0].region.localization_status == "located"
    assert [warning.code for warning in result.warnings] == ["GRAPHICS_PRESENT"]
    assert result.layout_text == "A"
