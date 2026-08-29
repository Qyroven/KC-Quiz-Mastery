"""Legacy Responses API execution with durable background checkpoints."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from learning_authoring.artifacts import read_json, write_json


def _notify(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _raw(response: Any) -> dict[str, Any]:
    return response.model_dump(mode="json")


def execute_response(
    client: Any,
    request: dict[str, Any],
    *,
    response_mode: str,
    checkpoint_path: Path,
    request_fingerprint: str,
    poll_interval_seconds: float,
    timeout_seconds: float,
    progress: Callable[[str], None] | None = None,
) -> tuple[Any, dict[str, Any], float, bool]:
    """Create or resume exactly one response without issuing a duplicate request."""

    started = time.perf_counter()
    resumed = False
    if checkpoint_path.is_file():
        checkpoint = read_json(checkpoint_path)
        if checkpoint.get("request_fingerprint") != request_fingerprint:
            raise RuntimeError(f"checkpoint belongs to a different request: {checkpoint_path}")
        response_id = checkpoint.get("response_id")
        if not isinstance(response_id, str) or not response_id:
            raise RuntimeError(f"checkpoint has no response id: {checkpoint_path}")
        _notify(progress, f"[model] RESUME response {response_id}")
        response = client.responses.retrieve(response_id)
        resumed = True
    else:
        response = client.responses.create(**request)
        response_id = getattr(response, "id", None)
        if not isinstance(response_id, str) or not response_id:
            raise RuntimeError("provider response has no id; cannot checkpoint safely")
        write_json(
            checkpoint_path,
            {
                "checkpoint_version": "responses-checkpoint.v1",
                "request_fingerprint": request_fingerprint,
                "response_id": response_id,
                "status": getattr(response, "status", None),
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    if response_mode == "background":
        while response.status in {"queued", "in_progress"}:
            elapsed = time.perf_counter() - started
            if elapsed > timeout_seconds:
                raise TimeoutError(f"background response exceeded {timeout_seconds:.0f}s")
            _notify(progress, f"[model] status={response.status} elapsed={elapsed:.0f}s")
            time.sleep(poll_interval_seconds)
            response = client.responses.retrieve(response.id)
            checkpoint = read_json(checkpoint_path)
            checkpoint["status"] = response.status
            checkpoint["updated_at"] = datetime.now(UTC).isoformat()
            write_json(checkpoint_path, checkpoint)
    if response.status != "completed":
        raise RuntimeError(f"provider response ended with status={response.status}")

    raw = _raw(response)
    checkpoint = read_json(checkpoint_path)
    checkpoint["status"] = "completed"
    checkpoint["completed_at"] = datetime.now(UTC).isoformat()
    write_json(checkpoint_path, checkpoint)
    return response, raw, time.perf_counter() - started, resumed
