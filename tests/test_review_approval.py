from __future__ import annotations

import pytest

from learning_authoring.approval import approve_extraction
from learning_authoring.artifacts import RunArtifacts, read_json, sha256_file, write_json
from learning_authoring.contracts import WarningRecord
from learning_authoring.review import _warning_scope, build_review
from tests.conftest import make_run_dir, payload


def prepare_proposed(tmp_path, source, *, warning: bool = False) -> RunArtifacts:
    make_run_dir(tmp_path, source)
    artifacts = RunArtifacts(tmp_path)
    extracted = payload(warning_page=2 if warning else None).with_source(source)
    write_json(artifacts.proposed, extracted.model_dump(mode="json"))
    write_json(artifacts.audit, {"human_review_required": True})
    write_json(artifacts.metrics, {"approved": False})
    return artifacts


def test_review_contains_all_pages_and_local_images(tmp_path, source) -> None:
    artifacts = prepare_proposed(tmp_path, source)
    output = build_review(tmp_path)
    html = output.read_text(encoding="utf-8")
    assert output == artifacts.review_html
    assert "Page ${page.page_number}" in html
    assert "pages/page-${String(page.page_number).padStart(4,'0')}.png" in html
    assert "extracted-source.v2" in html
    assert 'id="prev"' in html and 'id="next"' in html
    assert 'id="search"' in html
    assert 'data-copy-block="${index}"' not in html
    assert 'aria-label="Copy block JSON"' not in html
    assert 'id="copy-page"' in html
    assert 'aria-label="Copy page output JSON"' in html
    assert "Page output JSON" in html
    assert "Semantic blocks" not in html
    assert "Raw block JSON" not in html
    assert "Raw page JSON" not in html
    assert 'id="left-handle"' in html and 'id="right-handle"' in html
    assert 'id="left-toggle"' in html and 'id="right-toggle"' in html
    assert "w.page==null" not in html
    assert "const warningScope=" in html


def test_review_links_forward_to_same_kc_slide_when_recall_view_exists(
    tmp_path, source
) -> None:
    prepare_proposed(tmp_path, source)
    (tmp_path / "kc-recall.html").write_text("recall", encoding="utf-8")

    html = build_review(tmp_path).read_text(encoding="utf-8")

    assert 'id="next-stage"' in html
    assert 'href="kc-recall.html#1"' in html
    assert "kc-recall.html#${page.page_number}" in html
    assert "new URLSearchParams(location.search)" in html
    assert "← Back to KC" in html


def test_review_scopes_document_warnings_from_all_supported_references(source) -> None:
    extracted = payload(warning_page=1).with_source(source)
    extracted = extracted.model_copy(
        update={
            "warnings": [
                WarningRecord(code="EXPLICIT", message="Page field", page=1),
                WarningRecord(code="DETAILS", message="Details pages", details={"pages": [2]}),
                WarningRecord(code="BLOCK", message="Block reference", block_ids=["b2"]),
                WarningRecord(code="DOCUMENT", message="Unscoped document warning"),
            ]
        }
    )

    scope = _warning_scope(extracted)

    assert [warning["code"] for warning in scope["by_page"]["1"]] == ["EXPLICIT"]
    assert [warning["code"] for warning in scope["by_page"]["2"]] == [
        "DETAILS",
        "BLOCK",
    ]
    assert [warning["code"] for warning in scope["document"]] == ["DOCUMENT"]
    assert scope["record_count"] == 5


def test_review_does_not_depend_on_block_id_format(source) -> None:
    extracted = payload().with_source(source)
    extracted = extracted.model_copy(
        update={
            "warnings": [
                WarningRecord(
                    code="OPAQUE_BLOCK",
                    message="Opaque block reference",
                    block_ids=["b2"],
                )
            ]
        }
    )

    scope = _warning_scope(extracted)

    assert scope["by_page"]["1"] == []
    assert [warning["code"] for warning in scope["by_page"]["2"]] == ["OPAQUE_BLOCK"]


def test_approval_requires_review_page(tmp_path, source) -> None:
    prepare_proposed(tmp_path, source)
    with pytest.raises(RuntimeError, match="build and inspect"):
        approve_extraction(tmp_path, reviewer="Reviewer")


def test_approval_blocks_unacknowledged_warnings(tmp_path, source) -> None:
    prepare_proposed(tmp_path, source, warning=True)
    build_review(tmp_path)
    with pytest.raises(RuntimeError, match="warning"):
        approve_extraction(tmp_path, reviewer="Reviewer")


def test_approval_records_hashes_and_acknowledgement(tmp_path, source) -> None:
    artifacts = prepare_proposed(tmp_path, source, warning=True)
    build_review(tmp_path)
    record = approve_extraction(
        tmp_path,
        reviewer="Reviewer",
        note="Reviewed page by page",
        acknowledge_warnings=True,
    )
    assert record["warnings_acknowledged"] is True
    assert record["approved_sha256"] == sha256_file(artifacts.approved)
    assert sha256_file(artifacts.approved) == sha256_file(artifacts.proposed)
    assert read_json(artifacts.metrics)["approved"] is True
    with pytest.raises(FileExistsError):
        approve_extraction(tmp_path, reviewer="Reviewer", acknowledge_warnings=True)
