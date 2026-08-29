from __future__ import annotations

from learning_authoring.legacy_api.requests import (
    build_extraction_request,
    build_repair_request,
    extraction_descriptor,
)
from tests.conftest import payload


def test_main_extraction_sends_pdf_without_rendered_page_images(tmp_path) -> None:
    (tmp_path / "source.pdf").write_bytes(b"pdf")
    request = build_extraction_request(
        run_dir=tmp_path,
        filename="source.pdf",
        page_count=2,
        prompt="extract",
        model="test-model",
        reasoning_effort="low",
        response_mode="sync",
        max_output_tokens=None,
    )

    content = request["input"][0]["content"]
    assert [item["type"] for item in content] == ["input_file", "input_text"]
    assert all(item["type"] != "input_image" for item in content)


def test_targeted_repair_sends_exactly_one_page_image_without_pdf(tmp_path) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page-0001.png").write_bytes(b"png")
    request = build_repair_request(
        run_dir=tmp_path,
        page=payload().pages[0],
        page_count=2,
        filename="source.pdf",
        prompt="repair",
        model="test-model",
        reasoning_effort="low",
        response_mode="sync",
        pdf_detail="high",
        max_output_tokens=None,
        attempt_number=1,
        attempt_limit=2,
    )

    content = request["input"][0]["content"]
    assert [item["type"] for item in content] == ["input_text", "input_image"]
    assert all(item["type"] != "input_file" for item in content)


def test_repair_guard_policy_is_fingerprinted_and_recorded() -> None:
    base = {
        "stage_version": "test-stage",
        "source_sha256": "a" * 64,
        "model": "test-model",
        "reasoning_effort": "low",
        "response_mode": "sync",
        "render_dpi": 160,
        "pdf_detail": "high",
        "max_output_tokens": None,
        "targeted_repair": True,
        "repair_max_attempts": 2,
        "repair_max_candidate_pages": 12,
        "repair_systemic_guard_min_candidate_pages": 4,
        "repair_systemic_guard_max_page_fraction": 0.5,
        "prompt": "extract",
        "repair_prompt": "repair",
    }
    fingerprint, descriptor = extraction_descriptor(**base)

    assert descriptor["repair_max_candidate_pages"] == 12
    assert descriptor["repair_systemic_guard_min_candidate_pages"] == 4
    assert descriptor["repair_systemic_guard_max_page_fraction"] == 0.5
    for field, changed in (
        ("repair_max_candidate_pages", None),
        ("repair_systemic_guard_min_candidate_pages", 6),
        ("repair_systemic_guard_max_page_fraction", 0.75),
    ):
        changed_fingerprint, _ = extraction_descriptor(**{**base, field: changed})
        assert changed_fingerprint != fingerprint
