from __future__ import annotations

import pytest
from pydantic import ValidationError

from learning_authoring.contracts import (
    CrossPageRelation,
    ExtractedPage,
    PageNote,
    SourceRegion,
    normalized_geometry_bbox,
    source_region_geometry_state,
)
from tests.conftest import block, page, payload


def test_page_note_is_required() -> None:
    data = page(1, "b1").model_dump(mode="json")
    del data["page_note"]
    with pytest.raises(ValidationError, match="page_note"):
        ExtractedPage.model_validate(data)


def test_schema_version_is_exact() -> None:
    data = payload().model_dump(mode="json")
    data["schema_version"] = "extracted-source.v1"
    with pytest.raises(ValidationError, match="extracted-source.v2"):
        type(payload()).model_validate(data)


def test_reading_order_must_include_every_block() -> None:
    data = page(1, "b1").model_dump(mode="json")
    data["blocks"].append(block("b2", 1).model_dump(mode="json"))
    with pytest.raises(ValidationError, match="every block"):
        ExtractedPage.model_validate(data)


def test_page_note_evidence_must_exist() -> None:
    data = page(1, "b1").model_dump(mode="json")
    data["page_note"] = PageNote(summary="summary", evidence_block_ids=["missing"]).model_dump(
        mode="json"
    )
    with pytest.raises(ValidationError, match="unknown blocks"):
        ExtractedPage.model_validate(data)


def test_document_requires_complete_ordered_pages(source) -> None:
    data = payload().model_dump(mode="json")
    data["pages"].reverse()
    with pytest.raises(ValidationError, match="ordered"):
        type(payload()).model_validate(data).with_source(source)


def test_cross_page_relation_targets_must_exist(source) -> None:
    candidate = payload().model_copy(
        update={
            "cross_page_relations": [
                CrossPageRelation(
                    relation_type="continues",
                    source_block_id="b1",
                    target_block_id="missing",
                )
            ]
        }
    )
    with pytest.raises(ValidationError, match="unknown blocks"):
        candidate.with_source(source)


@pytest.mark.parametrize(
    ("geometry", "expected"),
    [
        ({"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}, (0.1, 0.2, 0.3, 0.4)),
        ({"bbox": [0.1, 0.2, 0.3, 0.4]}, (0.1, 0.2, 0.3, 0.4)),
        ({}, None),
        ({"bbox": [0, 0, 2, 1]}, None),
        ({"polygon": [[0, 0], [1, 1]]}, None),
    ],
)
def test_geometry_must_resolve_to_normalized_block_bounds(geometry, expected) -> None:
    assert normalized_geometry_bbox(geometry) == expected


@pytest.mark.parametrize(
    ("geometry", "expected_status", "expected_state"),
    [
        ({"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}, "located", "located"),
        ({}, "unresolved", "unresolved"),
        ({"bbox": [0, 0, 2, 1]}, "unresolved", "invalid"),
    ],
)
def test_historical_regions_infer_explicit_localization_status(
    geometry, expected_status, expected_state
) -> None:
    historical = SourceRegion.model_validate({"page": 1, "geometry": geometry})

    assert historical.localization_status == expected_status
    assert source_region_geometry_state(historical) == expected_state


def test_explicit_unresolved_status_routes_even_with_numeric_bounds() -> None:
    region = SourceRegion(
        page=1,
        localization_status="unresolved",
        geometry={"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4},
    )

    assert source_region_geometry_state(region) == "unresolved"
