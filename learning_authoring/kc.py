"""KC generation from one human-approved extraction JSON artifact."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from learning_authoring.artifacts import (
    RunArtifacts,
    read_json,
    sha256_file,
    write_json,
)
from learning_authoring.audit import reported_cost, response_usage
from learning_authoring.contracts import ExtractedSource
from learning_authoring.kc_contracts import ProposedKCSet

STAGE_VERSION = "approved-extraction-to-kc.v1"
PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT_DIR = PACKAGE_DIR / "prompts" / "kc-v1"


@dataclass(frozen=True)
class KCConfig:
    model: str = "gpt-5.6-sol"
    reasoning_effort: str = "high"
    response_mode: str = "background"
    max_output_tokens: int | None = None
    poll_interval_seconds: float = 5.0
    timeout_seconds: float = 3600.0
    prompt_dir: Path = DEFAULT_PROMPT_DIR
    api_key: str | None = None
    base_url: str | None = None

    def validate(self) -> None:
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if self.reasoning_effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            raise ValueError(f"unsupported reasoning effort: {self.reasoning_effort}")
        if self.response_mode not in {"background", "sync"}:
            raise ValueError("response_mode must be background or sync")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.poll_interval_seconds <= 0 or self.timeout_seconds <= 0:
            raise ValueError("poll interval and timeout must be positive")
        for name in ("foundation.md", "rulebook.md", "task.md", "output.schema.json"):
            if not (self.prompt_dir / name).is_file():
                raise ValueError(f"KC prompt component does not exist: {self.prompt_dir / name}")


@dataclass(frozen=True)
class KCPromptPackage:
    instructions: str
    output_schema: dict[str, Any]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class KCGenerationResult:
    proposed: ProposedKCSet
    proposed_path: Path
    request_preview_path: Path
    cached: bool
    resumed: bool
    metrics: dict[str, Any]


def load_prompt_package(prompt_dir: Path = DEFAULT_PROMPT_DIR) -> KCPromptPackage:
    """Load exactly Foundation, Rulebook, Task, and the structured output schema."""

    texts = {
        "foundation": (prompt_dir / "foundation.md").read_text(encoding="utf-8"),
        "rulebook": (prompt_dir / "rulebook.md").read_text(encoding="utf-8"),
        "task": (prompt_dir / "task.md").read_text(encoding="utf-8"),
    }
    schema_text = (prompt_dir / "output.schema.json").read_text(encoding="utf-8")
    output_schema = json.loads(schema_text)
    instructions = "\n\n".join((texts["foundation"], texts["rulebook"], texts["task"]))
    components: dict[str, Any] = {
        name: {
            "filename": f"{name}.md",
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "content": content,
        }
        for name, content in texts.items()
    }
    components["output_schema"] = {
        "filename": "output.schema.json",
        "sha256": hashlib.sha256(schema_text.encode()).hexdigest(),
        "content": output_schema,
    }
    package_bytes = json.dumps(components, ensure_ascii=False, sort_keys=True).encode()
    return KCPromptPackage(
        instructions=instructions,
        output_schema=output_schema,
        manifest={
            "package_version": "kc-approved-extraction.v1",
            "instruction_order": ["foundation", "rulebook", "task"],
            "structured_output_component": "output_schema",
            "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
            "components": components,
        },
    )


def load_approved_extraction(run_dir: Path) -> tuple[ExtractedSource, dict[str, Any], str]:
    """Enforce explicit approval and bind it to the exact extraction bytes."""

    artifacts = RunArtifacts(run_dir.expanduser().resolve())
    for path in (artifacts.approved, artifacts.approval):
        if not path.is_file():
            raise RuntimeError(f"KC generation requires extraction approval artifact: {path}")
    approval = read_json(artifacts.approval)
    if approval.get("status") != "approved":
        raise RuntimeError("extraction approval status is not approved")
    approved_sha256 = sha256_file(artifacts.approved)
    if approval.get("approved_sha256") != approved_sha256:
        raise RuntimeError("approved extraction hash does not match extraction-approval.json")
    approved = ExtractedSource.model_validate(read_json(artifacts.approved))
    if approval.get("schema_version") != approved.schema_version:
        raise RuntimeError("approved extraction schema does not match approval record")
    if approval.get("source_sha256") != approved.source.sha256:
        raise RuntimeError("approved extraction source hash does not match approval record")
    return approved, approval, approved_sha256


def _fingerprint(
    *,
    approved_sha256: str,
    package_sha256: str,
    model: str,
    reasoning_effort: str,
    response_mode: str,
    max_output_tokens: int | None,
) -> str:
    descriptor = {
        "stage_version": STAGE_VERSION,
        "approved_extraction_sha256": approved_sha256,
        "prompt_package_sha256": package_sha256,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "response_mode": response_mode,
        "max_output_tokens": max_output_tokens,
        "source_delivery": "approved_extraction_json_only",
    }
    encoded = json.dumps(descriptor, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def prepare_kc_request(
    run_dir: Path,
    *,
    config: KCConfig | None = None,
    output_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the gate and write an exact, non-generating request preview."""

    from learning_authoring.provider import normalized_model
    from learning_authoring.requests import build_kc_request

    settings = config or KCConfig()
    settings.validate()
    source_artifacts = RunArtifacts(run_dir.expanduser().resolve())
    if any(
        path.exists() or path.is_symlink()
        for path in (
            source_artifacts.run_dir / "authoring-context.json",
            source_artifacts.run_dir / "authoring-context",
        )
    ):
        raise RuntimeError(
            "supplementary authoring context requires the subscription-native agent-task KC "
            "workflow; the legacy API adapter must not silently drop it"
        )
    artifacts = RunArtifacts(
        output_dir.expanduser().resolve() if output_dir is not None else source_artifacts.run_dir
    )
    approved, approval, approved_sha256 = load_approved_extraction(source_artifacts.run_dir)
    package = load_prompt_package(settings.prompt_dir)
    effective_model = normalized_model(settings.model, settings.base_url)
    request = build_kc_request(
        approved=approved,
        instructions=package.instructions,
        output_schema=package.output_schema,
        model=effective_model,
        reasoning_effort=settings.reasoning_effort,
        response_mode=settings.response_mode,
        max_output_tokens=settings.max_output_tokens,
    )
    fingerprint = _fingerprint(
        approved_sha256=approved_sha256,
        package_sha256=package.manifest["package_sha256"],
        model=effective_model,
        reasoning_effort=settings.reasoning_effort,
        response_mode=settings.response_mode,
        max_output_tokens=settings.max_output_tokens,
    )
    metadata = {
        "stage": "kc",
        "stage_version": STAGE_VERSION,
        "request_fingerprint": fingerprint,
        "source_delivery": "approved_extraction_json_only",
        "approved_extraction": {
            "path": str(source_artifacts.approved),
            "sha256": approved_sha256,
            "schema_version": approved.schema_version,
            "source_id": approved.source.source_id,
            "source_sha256": approved.source.sha256,
            "page_count": approved.source.page_count,
        },
        "approval_record_sha256": sha256_file(source_artifacts.approval),
        "approval_status": approval["status"],
        "prompt_package_sha256": package.manifest["package_sha256"],
        "model": effective_model,
        "reasoning_effort": settings.reasoning_effort,
        "response_mode": settings.response_mode,
        "max_output_tokens": settings.max_output_tokens,
        "model_input_items": ["extracted-source.approved.json"],
        "excluded_model_inputs": [
            "PDF",
            "page PNGs",
            "source manifest",
            "text audit",
            "raw extractor API response",
            "proposed extraction",
            "approval reviewer metadata",
        ],
        "created_at": datetime.now(UTC).isoformat(),
    }
    if artifacts.kc_metadata.is_file() and (
        artifacts.kc_checkpoint.is_file() or artifacts.kc_proposed.is_file()
    ):
        existing = read_json(artifacts.kc_metadata)
        if existing.get("request_fingerprint") != fingerprint:
            raise RuntimeError("existing KC state belongs to a different generation request")
    write_json(artifacts.kc_prompt_package, package.manifest)
    write_json(artifacts.kc_request_preview, request)
    write_json(artifacts.kc_metadata, metadata)
    return request, metadata


def _output_text(response: Any) -> str:
    value = getattr(response, "output_text", None)
    if isinstance(value, str) and value.strip():
        return value
    raise RuntimeError("KC response contains no structured output text")


def _write_contract_error(artifacts: RunArtifacts, exc: Exception) -> None:
    errors = (
        exc.errors(include_url=False, include_context=False)
        if isinstance(exc, ValidationError) else None
    )
    write_json(
        artifacts.kc_contract_errors,
        {
            "contract_valid": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "errors": errors,
            "created_at": datetime.now(UTC).isoformat(),
        },
    )


def run_kc_generation(
    run_dir: Path,
    *,
    config: KCConfig | None = None,
    output_dir: Path | None = None,
    client: Any | None = None,
    progress: Callable[[str], None] | None = print,
) -> KCGenerationResult:
    """Generate a proposed KC set; never approve it automatically."""

    from learning_authoring.gateway import execute_response
    from learning_authoring.provider import build_client

    settings = config or KCConfig()
    request, metadata = prepare_kc_request(run_dir, config=settings, output_dir=output_dir)
    source_run = run_dir.expanduser().resolve()
    artifacts = RunArtifacts(output_dir.expanduser().resolve() if output_dir else source_run)
    approved, _, _ = load_approved_extraction(source_run)
    fingerprint = metadata["request_fingerprint"]

    if artifacts.kc_proposed.is_file() and artifacts.kc_metrics.is_file():
        existing = read_json(artifacts.kc_metadata)
        if existing.get("request_fingerprint") != fingerprint:
            raise RuntimeError("existing KC output belongs to a different request")
        proposed = ProposedKCSet.model_validate(read_json(artifacts.kc_proposed))
        proposed.validate_against_source(approved)
        return KCGenerationResult(
            proposed=proposed,
            proposed_path=artifacts.kc_proposed,
            request_preview_path=artifacts.kc_request_preview,
            cached=True,
            resumed=bool(read_json(artifacts.kc_metrics).get("resumed")),
            metrics=read_json(artifacts.kc_metrics),
        )

    if client is None:
        api_key = settings.api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required to start or resume KC generation")
        client = build_client(api_key=api_key, base_url=settings.base_url)

    started = time.perf_counter()
    response, raw, model_elapsed, resumed = execute_response(
        client,
        request,
        response_mode=settings.response_mode,
        checkpoint_path=artifacts.kc_checkpoint,
        request_fingerprint=fingerprint,
        poll_interval_seconds=settings.poll_interval_seconds,
        timeout_seconds=settings.timeout_seconds,
        progress=progress,
    )
    write_json(artifacts.kc_api_response, raw)
    try:
        proposed = ProposedKCSet.model_validate_json(_output_text(response))
        proposed.validate_against_source(approved)
    except (ValidationError, ValueError, RuntimeError) as exc:
        _write_contract_error(artifacts, exc)
        raise

    usage = response_usage(raw)
    metrics = {
        "metrics_version": "kc-run-metrics.v1",
        "stage_version": STAGE_VERSION,
        "request_fingerprint": fingerprint,
        "contract_valid": True,
        "approved_extraction_sha256": metadata["approved_extraction"]["sha256"],
        "source_page_count": approved.source.page_count,
        "page_audit_count": len(proposed.page_audit),
        "leaf_kc_count": len(proposed.leaf_kcs),
        "kc_group_count": len(proposed.kc_groups),
        "human_review_required": True,
        "approved": False,
        "resumed": resumed,
        "model_elapsed_seconds": round(model_elapsed, 6),
        "total_elapsed_seconds": round(time.perf_counter() - started, 6),
        "usage": usage,
        "gateway_reported_cost_usd": reported_cost(raw),
        "completed_at": datetime.now(UTC).isoformat(),
    }
    write_json(artifacts.kc_proposed, proposed.model_dump(mode="json"))
    write_json(artifacts.kc_metrics, metrics)
    if artifacts.kc_contract_errors.exists():
        artifacts.kc_contract_errors.unlink()
    if progress:
        progress(f"[kc] PROPOSED: {artifacts.kc_proposed}")
    return KCGenerationResult(
        proposed=proposed,
        proposed_path=artifacts.kc_proposed,
        request_preview_path=artifacts.kc_request_preview,
        cached=False,
        resumed=resumed,
        metrics=metrics,
    )
