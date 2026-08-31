"""Offline regressions for complete, source-bound learner stimuli."""

from __future__ import annotations

import base64
import struct
import zlib
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from learning_authoring.artifacts import read_json, write_json
from learning_authoring.quiz import (
    DEFAULT_EXAMPLES_DIR,
    QuizConfig,
    build_quiz_input,
    load_quiz_prompt_package,
)
from learning_authoring.quiz_contracts import QuizBatch, QuizImageCrop, QuizStimulus
from learning_authoring.quiz_media import build_media_catalog, render_quiz_images
from learning_authoring.quiz_review import _TEMPLATE
from learning_authoring.source import prepare_or_reuse_source
from tests.conftest import write_text_pdf
from tests.test_quiz import KC_SHA256, kc_set, quiz_output
from tests.test_review_compatibility import inline_script, quiz_review_data, run_js


@pytest.fixture
def media_case(tmp_path):
    pdf = tmp_path / "source.pdf"
    write_text_pdf(pdf)
    root = tmp_path / "run"
    source, _, _ = prepare_or_reuse_source(pdf, root, render_dpi=72)
    payload = build_quiz_input(
        kc_set(source),
        kc_set_sha256=KC_SHA256,
        config=QuizConfig(selected_kc_ids=("KC-001",), variants_per_kc=1),
    )
    payload["media_assets"] = build_media_catalog(root, payload)
    raw = quiz_output(source)
    raw["questions"][0]["stimulus"] = {
        "kind": "composite",
        "text": "",
        "formula": "",
        "table_columns": [],
        "table_rows": [],
        "blocks": [
            {"kind": "text", "text": "Use the diagram and supplied convention."},
            {
                "kind": "image",
                "asset_id": payload["media_assets"][0]["asset_id"],
                "alt": "Source diagram",
                "crop": None,
            },
            {
                "kind": "table",
                "table_columns": ["Input", "Value"],
                "table_rows": [["a", "3"], ["b", "5"]],
            },
            {"kind": "formula", "formula": "f(a,b) = a + b"},
        ],
    }
    return root, payload, raw


def test_catalog_and_portable_image_preserve_raw_candidate(media_case):
    root, payload, raw = media_case
    before = deepcopy(raw)
    images = render_quiz_images(root, payload, QuizBatch.model_validate(raw))
    assert len(payload["media_assets"]) == len(images) == 1
    assert images[0]["asset_id"] == payload["media_assets"][0]["asset_id"]
    data = base64.b64decode(images[0]["data_url"].split(",", 1)[1])
    assert data == (root / payload["media_assets"][0]["image_ref"]).read_bytes()
    assert raw == before


@pytest.mark.parametrize(
    "crop",
    [
        {"x": 0.15, "y": 0.1, "w": 0.8, "h": 0.5},
        {"x": 0, "y": 0, "w": 1, "h": 1},
    ],
)
def test_crop_renders_expected_pixels_and_top_left_orientation(media_case, crop):
    root, payload, raw = media_case
    raw["questions"][0]["stimulus"]["blocks"][1]["crop"] = crop
    image = render_quiz_images(root, payload, QuizBatch.model_validate(raw))[0]
    data = base64.b64decode(image["data_url"].split(",", 1)[1])
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (round(200 * crop["w"]), round(200 * crop["h"]))
    # Native page text is near the top: the cropped top portion must retain ink.
    offset, rows = 8, b""
    while offset < len(data):
        size = int.from_bytes(data[offset : offset + 4], "big")
        if data[offset + 4 : offset + 8] == b"IDAT":
            rows += data[offset + 8 : offset + 8 + size]
        offset += size + 12
    pixels = zlib.decompress(rows)
    assert any(
        value < 128
        for row in range(height)
        for value in pixels[row * (width * 3 + 1) + 1 : (row + 1) * (width * 3 + 1)]
    )


@pytest.mark.parametrize(
    "change,match",
    [
        ("unknown", "unknown image"),
        ("wrong_page", "outside its cited evidence"),
        ("wrong_source", "outside its cited evidence"),
        ("changed_png", "missing or changed"),
        ("missing_png", "missing or changed"),
        ("escape", "inside its source run"),
        ("changed_pdf", "missing or changed"),
    ],
)
def test_invalid_media_is_not_silently_accepted(media_case, change, match):
    root, payload, raw = media_case
    asset = payload["media_assets"][0]
    block = raw["questions"][0]["stimulus"]["blocks"][1]
    if change == "unknown":
        block["asset_id"] = "unavailable"
    elif change == "wrong_page":
        asset["page"] = 2
    elif change == "wrong_source":
        asset["source_id"] = "different-source"
    elif change == "changed_png":
        (root / asset["image_ref"]).write_bytes(b"changed")
    elif change == "missing_png":
        (root / asset["image_ref"]).unlink()
    elif change == "escape":
        asset["image_ref"] = "../source.pdf"
    elif change == "changed_pdf":
        block["crop"] = {"x": 0, "y": 0, "w": 0.5, "h": 0.5}
        (root / asset["pdf_ref"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match=match):
        render_quiz_images(root, payload, QuizBatch.model_validate(raw))


@pytest.mark.parametrize(
    "crop",
    [
        {"x": -0.1, "y": 0, "w": 1, "h": 1},
        {"x": 0.5, "y": 0, "w": 0.6, "h": 1},
        {"x": 0, "y": 0, "w": 0, "h": 1},
        {"x": 0, "y": 0, "w": float("nan"), "h": 1},
    ],
)
def test_invalid_crop_is_a_contract_error(crop):
    with pytest.raises(ValidationError):
        QuizImageCrop.model_validate(crop)


def test_legacy_stimulus_is_not_rewritten(source):
    old = quiz_output(source)["questions"][0]["stimulus"]
    assert QuizStimulus.model_validate(old).model_dump(mode="json") == old


@pytest.mark.parametrize("count", [1, 3, 5])
def test_catalog_keeps_same_page_numbers_in_different_sources_distinct(tmp_path, count):
    from learning_authoring.agent_session import agent_init
    from learning_authoring.source_bundle import prepare_source_bundle

    root = tmp_path / "bundle"
    runs = []
    for index in range(count):
        pdf = tmp_path / f"source-{index}.pdf"
        write_text_pdf(pdf)
        pdf.write_bytes(pdf.read_bytes() + f"\n% source {index}\n".encode())
        run = root / "sources" / str(index)
        agent_init(pdf, run)
        runs.append(run)
    bundle = prepare_source_bundle(root, runs)
    selected_sources = [entry.source.source_id for entry in bundle.sources]
    payload = {
        "source_ref": {"source_bundle_sha256": bundle.bundle_sha256},
        "leaf_kcs": [
            {
                "source_evidence": [
                    {"source_id": identity, "page": 1} for identity in selected_sources
                ]
            }
        ],
    }
    catalog = build_media_catalog(root, payload)
    assert len({item["asset_id"] for item in catalog}) == count
    assert [item["source_id"] for item in catalog] == selected_sources
    assert {item["page"] for item in catalog} == {1}
    payload["leaf_kcs"][0]["source_evidence"] = [{"source_id": selected_sources[-1], "page": 1}]
    selected_catalog = build_media_catalog(root, payload)
    assert [item["source_id"] for item in selected_catalog] == selected_sources[-1:]


def test_actual_learner_renderer_shows_all_blocks_without_key(source):
    data = quiz_review_data(source, adaptive=True)
    data["stimulus_images"] = [
        {"asset_id": "figure", "crop": None, "data_url": "data:image/png;base64,iVBORw0KGgo="}
    ]
    data["quiz"]["questions"][0]["stimulus"] = {
        "kind": "composite",
        "blocks": [
            {"kind": "text", "text": "Inputs <shown>"},
            {"kind": "table", "table_columns": ["Branch", "Value"], "table_rows": [["X", "4"]]},
            {"kind": "formula", "formula": "s = 2 * X"},
            {"kind": "image", "asset_id": "figure", "alt": "Chart <labels>", "crop": None},
        ],
    }
    run_js(
        inline_script(_TEMPLATE),
        """
      const html=stimulusHTML(questions[0].stimulus);
      const parts=['Inputs &lt;shown&gt;','<table','Branch','s = 2 * X',
                   '<img','Chart &lt;labels&gt;'];
      for(const part of parts)assert.ok(html.includes(part),part);
      assert.doesNotMatch(html,/answer_explanation|correct_answer/);
      questions[0].stimulus.blocks[3].crop={x:0,y:0,w:.5,h:.5};
      assert.match(stimulusHTML(questions[0].stimulus),/role="alert"/);
      assert.doesNotMatch(stimulusHTML(questions[0].stimulus),/<img/);
    """,
        data=data,
    )


def test_few_shot_application_has_visible_rule_and_contract():
    package = load_quiz_prompt_package()
    for example in package.worked_examples:
        # Worked-example properties use the same JSON contract as runtime output.
        raw = read_json(DEFAULT_EXAMPLES_DIR / f"{example.example_id}.json")
        QuizBatch.model_validate(raw["output"]).validate_against_input(raw["input"])
    raw = read_json(DEFAULT_EXAMPLES_DIR / "parallel-near-miss-distractors.json")
    q = QuizBatch.model_validate(raw["output"]).questions[0]
    visible = " ".join(part.text for part in q.stimulus.parts())
    assert "exactly one fails" in visible and "seal state cannot be determined" in visible
    assert "one-failure" not in q.title


def test_frozen_task_import_and_portal_embed_real_source_image(tmp_path, monkeypatch):
    from learning_authoring.agent_session import agent_import, prepare_agent_task
    from learning_authoring.product.showcase import build_showcase
    from tests.test_agent_context_slots import _adaptive_candidate, _import_kcs, _init
    from tests.test_agent_session import _forbid_provider_use

    _forbid_provider_use(monkeypatch)
    root, source = _init(tmp_path)
    _import_kcs(root, source)
    task = prepare_agent_task("quiz", root, include_all_kcs=True)
    frozen = read_json(Path(task["task_package"]))["input_boundary"]["payload"]
    raw = _adaptive_candidate(root, source, task)
    question = raw["questions"][0]
    question["stimulus"] = {
        "kind": "composite",
        "text": "",
        "formula": "",
        "table_columns": [],
        "table_rows": [],
        "blocks": [
            {"kind": "text", "text": "Inspect the source figure."},
            {
                "kind": "image",
                "asset_id": frozen["media_assets"][0]["asset_id"],
                "alt": "Source figure",
                "crop": {"x": 0, "y": 0, "w": 1, "h": 0.5},
            },
        ],
    }
    path = root / "candidate.json"
    write_json(path, raw)
    before = path.read_bytes()
    result = agent_import("quiz", root, path, task_package=Path(task["task_package"]))
    assert Path(result["raw_candidate"]).read_bytes() == before
    assert (root / "quiz/quiz-proposed.json").read_bytes() == before
    assert "data:image/png;base64," in (root / "quiz-review.html").read_text()
    output = tmp_path / "portal"
    build_showcase(root, output)
    assert "data:image/png;base64," in (output / "quiz-review.html").read_text()
    assert not (output / "source.pdf").exists()
