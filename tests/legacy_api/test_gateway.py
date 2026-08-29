from __future__ import annotations

import pytest

from learning_authoring.artifacts import read_json, write_json
from learning_authoring.legacy_api.gateway import execute_response
from tests.conftest import FakeResponse, fake_client


def test_new_response_is_checkpointed(tmp_path) -> None:
    client = fake_client(created=[FakeResponse({"ok": True})])
    response, _, _, resumed = execute_response(
        client,
        {"model": "test"},
        response_mode="sync",
        checkpoint_path=tmp_path / "checkpoint.json",
        request_fingerprint="fingerprint",
        poll_interval_seconds=0.001,
        timeout_seconds=1,
    )
    assert response.id == "resp_test"
    assert resumed is False
    assert read_json(tmp_path / "checkpoint.json")["status"] == "completed"


def test_existing_checkpoint_resumes_without_create(tmp_path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    write_json(
        checkpoint,
        {"request_fingerprint": "fingerprint", "response_id": "resp_existing"},
    )
    client = fake_client(retrieved=[FakeResponse({}, response_id="resp_existing")])
    _, _, _, resumed = execute_response(
        client,
        {"model": "test"},
        response_mode="sync",
        checkpoint_path=checkpoint,
        request_fingerprint="fingerprint",
        poll_interval_seconds=0.001,
        timeout_seconds=1,
    )
    assert resumed is True
    assert client.responses.create_calls == []
    assert client.responses.retrieve_calls == ["resp_existing"]


def test_checkpoint_rejects_changed_request(tmp_path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    write_json(checkpoint, {"request_fingerprint": "old", "response_id": "resp_existing"})
    with pytest.raises(RuntimeError, match="different request"):
        execute_response(
            fake_client(),
            {},
            response_mode="sync",
            checkpoint_path=checkpoint,
            request_fingerprint="new",
            poll_interval_seconds=0.001,
            timeout_seconds=1,
        )
