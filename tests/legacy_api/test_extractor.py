from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from learning_authoring.artifacts import RunArtifacts, read_json
from learning_authoring.legacy_api.extractor import ExtractionConfig, run_extraction
from tests.conftest import FakeResponse, fake_client, make_run_dir, payload


def fake_source_prep(source):
    def prepare(pdf_path, run_dir, *, render_dpi, progress=None):
        if not run_dir.exists():
            make_run_dir(run_dir, source)
        return source, {"manifest_version": "source-package.v2"}, False

    return prepare


def config() -> ExtractionConfig:
    return ExtractionConfig(
        model="test-model",
        reasoning_effort="low",
        response_mode="sync",
        targeted_repair=False,
        poll_interval_seconds=0.001,
        timeout_seconds=1,
    )


def test_extractor_writes_proposed_not_approved(tmp_path, source, monkeypatch) -> None:
    monkeypatch.setattr(
        "learning_authoring.legacy_api.extractor.prepare_or_reuse_source", fake_source_prep(source)
    )
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"ignored by fake source gate")
    run_dir = tmp_path / "run"
    client = fake_client(created=[FakeResponse(payload().model_dump(mode="json"))])
    result = run_extraction(pdf, run_dir, config=config(), client=client, progress=None)
    artifacts = RunArtifacts(run_dir)
    assert result.extracted.schema_version == "extracted-source.v2"
    assert artifacts.proposed.is_file()
    assert not artifacts.approved.exists()
    assert result.metrics["page_note_count"] == 2


def test_completed_extraction_is_reused_without_model_call(tmp_path, source, monkeypatch) -> None:
    monkeypatch.setattr(
        "learning_authoring.legacy_api.extractor.prepare_or_reuse_source", fake_source_prep(source)
    )
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"ignored")
    run_dir = tmp_path / "run"
    first_client = fake_client(created=[FakeResponse(payload().model_dump(mode="json"))])
    run_extraction(pdf, run_dir, config=config(), client=first_client, progress=None)
    second_client = fake_client()
    result = run_extraction(pdf, run_dir, config=config(), client=second_client, progress=None)
    assert result.cached is True
    assert second_client.responses.create_calls == []


def test_changed_repair_guard_cannot_reuse_old_extraction_request(
    tmp_path, source, monkeypatch
) -> None:
    monkeypatch.setattr(
        "learning_authoring.legacy_api.extractor.prepare_or_reuse_source", fake_source_prep(source)
    )
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"ignored")
    run_dir = tmp_path / "run"
    run_extraction(
        pdf,
        run_dir,
        config=config(),
        client=fake_client(created=[FakeResponse(payload().model_dump(mode="json"))]),
        progress=None,
    )

    changed = replace(config(), repair_systemic_guard_max_page_fraction=0.75)
    with pytest.raises(RuntimeError, match="different extraction request"):
        run_extraction(
            pdf,
            run_dir,
            config=changed,
            client=fake_client(),
            progress=None,
        )


def test_invalid_model_payload_writes_contract_errors(tmp_path, source, monkeypatch) -> None:
    monkeypatch.setattr(
        "learning_authoring.legacy_api.extractor.prepare_or_reuse_source", fake_source_prep(source)
    )
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"ignored")
    run_dir = tmp_path / "run"
    invalid = payload().model_dump(mode="json")
    del invalid["pages"][0]["page_note"]
    client = fake_client(created=[FakeResponse(invalid)])
    with pytest.raises(ValidationError):
        run_extraction(pdf, run_dir, config=config(), client=client, progress=None)
    assert RunArtifacts(run_dir).contract_errors.is_file()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"repair_max_candidate_pages": 0}, "repair_max_candidate_pages"),
        (
            {"repair_systemic_guard_min_candidate_pages": 0},
            "repair_systemic_guard_min_candidate_pages",
        ),
        (
            {"repair_systemic_guard_max_page_fraction": 0},
            "repair_systemic_guard_max_page_fraction",
        ),
        (
            {"repair_systemic_guard_max_page_fraction": 1.1},
            "repair_systemic_guard_max_page_fraction",
        ),
    ],
)
def test_repair_guard_config_validation(changes, message) -> None:
    with pytest.raises(ValueError, match=message):
        replace(config(), **changes).validate()


def test_repair_guard_config_reaches_policy_fingerprint_and_metadata(
    tmp_path, source, monkeypatch
) -> None:
    monkeypatch.setattr(
        "learning_authoring.legacy_api.extractor.prepare_or_reuse_source", fake_source_prep(source)
    )
    captured = {}

    def capture_repairs(payload_value, *, policy, **kwargs):
        captured["policy"] = policy
        return payload_value, {"enabled": policy.enabled}, [], []

    monkeypatch.setattr("learning_authoring.legacy_api.extractor.run_repairs", capture_repairs)
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"ignored")
    run_dir = tmp_path / "run"
    settings = replace(
        config(),
        targeted_repair=True,
        repair_max_candidate_pages=None,
        repair_systemic_guard_min_candidate_pages=7,
        repair_systemic_guard_max_page_fraction=0.75,
    )
    client = fake_client(created=[FakeResponse(payload().model_dump(mode="json"))])

    run_extraction(pdf, run_dir, config=settings, client=client, progress=None)

    policy = captured["policy"]
    assert policy.max_candidate_pages is None
    assert policy.systemic_guard_min_candidate_pages == 7
    assert policy.systemic_guard_max_page_fraction == 0.75
    descriptor = read_json(RunArtifacts(run_dir).metadata)["request_descriptor"]
    assert descriptor["repair_max_candidate_pages"] is None
    assert descriptor["repair_systemic_guard_min_candidate_pages"] == 7
    assert descriptor["repair_systemic_guard_max_page_fraction"] == 0.75
