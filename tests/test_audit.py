from __future__ import annotations

import pytest

from learning_authoring.audit import build_audit, validate_extraction_geometry
from learning_authoring.contracts import SourceRegion
from tests.conftest import make_run_dir, payload


def test_audit_reports_reconstruction_geometry_coverage(tmp_path, source) -> None:
    make_run_dir(tmp_path, source)
    extracted = payload().with_source(source)
    incomplete_block = (
        extracted.pages[1]
        .blocks[0]
        .model_copy(update={"region": SourceRegion(page=2, geometry={})})
    )
    incomplete_page = extracted.pages[1].model_copy(update={"blocks": [incomplete_block]})
    extracted = extracted.model_copy(update={"pages": [extracted.pages[0], incomplete_page]})

    audit = build_audit(extracted, tmp_path)

    assert audit["reconstruction_ready"] is False
    assert audit["missing_geometry_block_count"] == 1
    assert audit["missing_geometry_blocks"] == [{"page": 2, "block_id": "b2", "kind": "text"}]
    assert audit["unresolved_geometry_block_count"] == 1
    assert audit["invalid_geometry_block_count"] == 0
    assert audit["pages"][1]["unresolved_geometry_block_ids"] == ["b2"]
    assert audit["pages"][1]["geometry_coverage"] == 0.0


def test_audit_distinguishes_invalid_from_unresolved_geometry(tmp_path, source) -> None:
    make_run_dir(tmp_path, source)
    extracted = payload().with_source(source)
    invalid_block = (
        extracted.pages[1]
        .blocks[0]
        .model_copy(update={"region": SourceRegion(page=2, geometry={"bbox": [0, 0, 2, 1]})})
    )
    invalid_page = extracted.pages[1].model_copy(update={"blocks": [invalid_block]})
    extracted = extracted.model_copy(update={"pages": [extracted.pages[0], invalid_page]})

    audit = build_audit(extracted, tmp_path)

    assert audit["reconstruction_ready"] is False
    assert audit["unresolved_geometry_block_count"] == 0
    assert audit["invalid_geometry_block_count"] == 1
    assert audit["invalid_geometry_blocks"] == [{"page": 2, "block_id": "b2", "kind": "text"}]
    assert audit["pages"][1]["invalid_geometry_block_ids"] == ["b2"]


def test_invalid_geometry_is_a_contract_error_but_unresolved_is_allowed(source) -> None:
    unresolved = payload().with_source(source)
    unresolved.pages[1].blocks[0].region = SourceRegion(
        page=2,
        localization_status="unresolved",
        geometry={},
    )
    validate_extraction_geometry(unresolved)

    invalid = payload().with_source(source)
    invalid.pages[1].blocks[0].region = SourceRegion(
        page=2,
        localization_status="located",
        geometry={"x": 0.8, "y": 0.2, "w": 0.3, "h": 0.4},
    )
    with pytest.raises(ValueError, match="invalid normalized geometry"):
        validate_extraction_geometry(invalid)
