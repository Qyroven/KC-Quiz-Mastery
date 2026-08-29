"""Historical provider-backed PDF extraction stage."""

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

from learning_authoring.artifacts import RunArtifacts, read_json, write_json
from learning_authoring.audit import (
    build_audit,
    reported_cost,
    response_usage,
    validate_extraction_geometry,
)
from learning_authoring.contracts import ExtractedSource, ExtractedSourcePayload
from learning_authoring.legacy_api.gateway import execute_response
from learning_authoring.legacy_api.provider import build_client, normalized_model
from learning_authoring.legacy_api.repair import RepairPolicy, run_repairs
from learning_authoring.legacy_api.requests import (
    build_extraction_request,
    extraction_descriptor,
)
from learning_authoring.prompt_packages import (
    WorkedExample,
    load_worked_example_suite,
    worked_examples_component,
)
from learning_authoring.source import DEFAULT_RENDER_DPI, prepare_or_reuse_source

STAGE_VERSION = "pdf-semantic-extractor.v3"
PACKAGE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT_PATH = PACKAGE_DIR / "prompts" / "extractor-v2.md"
DEFAULT_EXAMPLES_DIR = PACKAGE_DIR / "prompts" / "extractor-v2" / "examples-v1"


@dataclass(frozen=True)
class ExtractionConfig:
    model: str = "gpt-5.6-sol"
    reasoning_effort: str = "high"
    response_mode: str = "background"
    render_dpi: int = DEFAULT_RENDER_DPI
    pdf_detail: str = "high"
    max_output_tokens: int | None = None
    targeted_repair: bool = True
    repair_max_attempts: int = 2
    repair_max_candidate_pages: int | None = 12
    repair_systemic_guard_min_candidate_pages: int = 4
    repair_systemic_guard_max_page_fraction: float = 0.5
    poll_interval_seconds: float = 5.0
    timeout_seconds: float = 3600.0
    prompt_path: Path = DEFAULT_PROMPT_PATH
    repair_prompt_path: Path = PACKAGE_DIR / "prompts" / "repair-v1.md"
    api_key: str | None = None
    base_url: str | None = None

    def validate(self) -> None:
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if self.reasoning_effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            raise ValueError(f"unsupported reasoning effort: {self.reasoning_effort}")
        if self.response_mode not in {"background", "sync"}:
            raise ValueError("response_mode must be background or sync")
        if self.pdf_detail not in {"auto", "low", "high"}:
            raise ValueError(f"unsupported PDF detail: {self.pdf_detail}")
        if self.render_dpi <= 0:
            raise ValueError("render_dpi must be positive")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.repair_max_attempts < 1:
            raise ValueError("repair_max_attempts must be at least 1")
        if self.repair_max_candidate_pages is not None and self.repair_max_candidate_pages < 1:
            raise ValueError("repair_max_candidate_pages must be at least 1 or None")
        if self.repair_systemic_guard_min_candidate_pages < 1:
            raise ValueError("repair_systemic_guard_min_candidate_pages must be at least 1")
        if not 0 < self.repair_systemic_guard_max_page_fraction <= 1:
            raise ValueError("repair_systemic_guard_max_page_fraction must be in (0, 1]")
        if self.poll_interval_seconds <= 0 or self.timeout_seconds <= 0:
            raise ValueError("poll interval and timeout must be positive")
        for path in (self.prompt_path, self.repair_prompt_path):
            if not path.is_file():
                raise ValueError(f"prompt does not exist: {path}")


@dataclass(frozen=True)
class ExtractionResult:
    extracted: ExtractedSource
    run_dir: Path
    proposed_path: Path
    cached: bool
    resumed: bool
    metrics: dict[str, Any]


@dataclass(frozen=True)
class ExtractionPromptPackage:
    instructions: str
    output_schema: dict[str, Any]
    worked_examples: tuple[WorkedExample, ...]
    manifest: dict[str, Any]

    @property
    def lineage(self) -> dict[str, Any]:
        return self.manifest


def load_extraction_prompt_package(
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    *,
    examples_dir: Path = DEFAULT_EXAMPLES_DIR,
) -> ExtractionPromptPackage:
    """Load the extraction instructions, contract, and neutral worked examples."""

    instructions = prompt_path.read_text(encoding="utf-8")
    output_schema = ExtractedSourcePayload.model_json_schema()
    suite = load_worked_example_suite(
        examples_dir,
        expected_stage="extraction",
        expected_contract_version="extracted-source.v2",
    )
    schema_bytes = json.dumps(output_schema, ensure_ascii=False, sort_keys=True).encode()
    components: dict[str, Any] = {
        "instructions": {
            "filename": prompt_path.name,
            "sha256": hashlib.sha256(instructions.encode()).hexdigest(),
            "content": instructions,
        },
        "output_schema": {
            "source": "learning_authoring.contracts.ExtractedSourcePayload",
            "schema_version": "extracted-source.v2",
            "sha256": hashlib.sha256(schema_bytes).hexdigest(),
            "content": output_schema,
        },
        "worked_examples": worked_examples_component(
            suite,
            filename="extractor-v2/examples-v1/manifest.json",
        ),
    }
    package_bytes = json.dumps(components, ensure_ascii=False, sort_keys=True).encode()
    return ExtractionPromptPackage(
        instructions=instructions,
        output_schema=output_schema,
        worked_examples=suite.examples,
        manifest={
            "package_version": "extraction-prompt.v1",
            "instruction_order": ["instructions"],
            "structured_output_component": "output_schema",
            "worked_examples_component": "worked_examples",
            "worked_example_order": list(suite.example_order),
            "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
            "components": components,
        },
    )


def _output_text(response: Any) -> str:
    value = getattr(response, "output_text", None)
    if isinstance(value, str) and value.strip():
        return value
    raise RuntimeError("extraction response contains no structured output text")


def _sum_usage(rows: list[dict[str, int]]) -> dict[str, int]:
    keys = {
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    }
    return {key: sum(row.get(key, 0) for row in rows) for key in sorted(keys)}


def _write_contract_error(artifacts: RunArtifacts, exc: Exception) -> None:
    errors = exc.errors(include_url=False) if isinstance(exc, ValidationError) else None
    write_json(
        artifacts.contract_errors,
        {
            "contract_valid": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "errors": errors,
            "created_at": datetime.now(UTC).isoformat(),
        },
    )


def _completed_result(
    artifacts: RunArtifacts,
    *,
    source_sha256: str,
    extraction_fingerprint: str,
) -> ExtractionResult | None:
    if not all(path.is_file() for path in (artifacts.proposed, artifacts.audit, artifacts.metrics)):
        return None
    metadata = read_json(artifacts.metadata)
    if metadata.get("extraction_fingerprint") != extraction_fingerprint:
        raise RuntimeError("existing proposed output belongs to a different extraction request")
    extracted = ExtractedSource.model_validate(read_json(artifacts.proposed))
    if extracted.source.sha256 != source_sha256:
        raise RuntimeError("existing proposed output belongs to a different source")
    metrics = read_json(artifacts.metrics)
    return ExtractionResult(
        extracted=extracted,
        run_dir=artifacts.run_dir,
        proposed_path=artifacts.proposed,
        cached=True,
        resumed=bool(metrics.get("resumed")),
        metrics=metrics,
    )


def run_extraction(
    pdf_path: Path,
    run_dir: Path,
    *,
    config: ExtractionConfig | None = None,
    client: Any | None = None,
    progress: Callable[[str], None] | None = print,
) -> ExtractionResult:
    """Extract one PDF to a reviewable v2 artifact; never approve it automatically."""

    settings = config or ExtractionConfig()
    settings.validate()
    effective_model = normalized_model(settings.model, settings.base_url)
    output = run_dir.expanduser().resolve()
    artifacts = RunArtifacts(output)
    source, manifest, source_reused = prepare_or_reuse_source(
        pdf_path,
        output,
        render_dpi=settings.render_dpi,
        progress=progress,
    )
    prompt = settings.prompt_path.read_text(encoding="utf-8")
    repair_prompt = settings.repair_prompt_path.read_text(encoding="utf-8")
    fingerprint, descriptor = extraction_descriptor(
        stage_version=STAGE_VERSION,
        source_sha256=source.sha256,
        model=effective_model,
        reasoning_effort=settings.reasoning_effort,
        response_mode=settings.response_mode,
        render_dpi=settings.render_dpi,
        pdf_detail=settings.pdf_detail,
        max_output_tokens=settings.max_output_tokens,
        targeted_repair=settings.targeted_repair,
        repair_max_attempts=settings.repair_max_attempts,
        repair_max_candidate_pages=settings.repair_max_candidate_pages,
        repair_systemic_guard_min_candidate_pages=(
            settings.repair_systemic_guard_min_candidate_pages
        ),
        repair_systemic_guard_max_page_fraction=(settings.repair_systemic_guard_max_page_fraction),
        prompt=prompt,
        repair_prompt=repair_prompt,
    )
    metadata = {
        "stage": "extract",
        "stage_version": STAGE_VERSION,
        "extraction_fingerprint": fingerprint,
        "request_descriptor": descriptor,
        "source": source.model_dump(mode="json"),
        "source_manifest_version": manifest.get("manifest_version"),
        "output_schema_version": "extracted-source.v2",
        "human_review_required": True,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if artifacts.metadata.is_file():
        existing = read_json(artifacts.metadata)
        if existing.get("extraction_fingerprint") != fingerprint:
            raise RuntimeError("run directory belongs to a different extraction request")
    else:
        write_json(artifacts.metadata, metadata)

    completed = _completed_result(
        artifacts,
        source_sha256=source.sha256,
        extraction_fingerprint=fingerprint,
    )
    if completed is not None:
        if progress:
            progress("[extract] REUSED completed proposed output; no model request")
        return completed

    request = build_extraction_request(
        run_dir=output,
        filename=source.filename,
        page_count=source.page_count,
        prompt=prompt,
        model=effective_model,
        reasoning_effort=settings.reasoning_effort,
        response_mode=settings.response_mode,
        max_output_tokens=settings.max_output_tokens,
    )
    if client is None:
        api_key = settings.api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required to start or resume extraction")
        client = build_client(api_key=api_key, base_url=settings.base_url)

    started = time.perf_counter()
    response, raw, model_elapsed, resumed = execute_response(
        client,
        request,
        response_mode=settings.response_mode,
        checkpoint_path=artifacts.checkpoint,
        request_fingerprint=fingerprint,
        poll_interval_seconds=settings.poll_interval_seconds,
        timeout_seconds=settings.timeout_seconds,
        progress=progress,
    )
    write_json(artifacts.api_response, raw)
    try:
        payload = ExtractedSourcePayload.model_validate_json(_output_text(response))
        payload.with_source(source)
    except (ValidationError, ValueError, RuntimeError) as exc:
        _write_contract_error(artifacts, exc)
        raise

    repaired_payload, repair_summary, repair_usage, repair_costs = run_repairs(
        payload,
        source=source,
        artifacts=artifacts,
        policy=RepairPolicy(
            enabled=settings.targeted_repair,
            max_attempts=settings.repair_max_attempts,
            model=effective_model,
            reasoning_effort=settings.reasoning_effort,
            response_mode=settings.response_mode,
            pdf_detail=settings.pdf_detail,
            max_output_tokens=settings.max_output_tokens,
            poll_interval_seconds=settings.poll_interval_seconds,
            timeout_seconds=settings.timeout_seconds,
            prompt=repair_prompt,
            extraction_fingerprint=fingerprint,
            max_candidate_pages=settings.repair_max_candidate_pages,
            systemic_guard_min_candidate_pages=(settings.repair_systemic_guard_min_candidate_pages),
            systemic_guard_max_page_fraction=(settings.repair_systemic_guard_max_page_fraction),
        ),
        client=client,
        progress=progress,
    )
    try:
        extracted = repaired_payload.with_source(source)
        validate_extraction_geometry(extracted)
    except (ValidationError, ValueError) as exc:
        _write_contract_error(artifacts, exc)
        raise

    audit = build_audit(extracted, output)
    usage_rows = [response_usage(raw), *repair_usage]
    costs = [reported_cost(raw), *repair_costs]
    known_costs = [cost for cost in costs if cost is not None]
    metrics = {
        "metrics_version": "extraction-run-metrics.v2",
        "stage_version": STAGE_VERSION,
        "extraction_fingerprint": fingerprint,
        "contract_valid": True,
        "schema_version": extracted.schema_version,
        "source_page_count": source.page_count,
        "extracted_page_count": len(extracted.pages),
        "page_note_count": len(extracted.pages),
        "human_review_required": True,
        "approved": False,
        "source_reused": source_reused,
        "resumed": resumed,
        "model_elapsed_seconds": round(model_elapsed, 6),
        "total_elapsed_seconds": round(time.perf_counter() - started, 6),
        "usage": _sum_usage(usage_rows),
        "gateway_reported_cost_usd": round(sum(known_costs), 8) if known_costs else None,
        "repair": repair_summary,
        "reconstruction_ready": audit["reconstruction_ready"],
        "missing_geometry_block_count": audit["missing_geometry_block_count"],
        "completed_at": datetime.now(UTC).isoformat(),
    }
    write_json(artifacts.proposed, extracted.model_dump(mode="json"))
    write_json(artifacts.audit, audit)
    write_json(artifacts.metrics, metrics)
    if artifacts.contract_errors.exists():
        artifacts.contract_errors.unlink()
    if progress:
        progress(f"[extract] PROPOSED: {artifacts.proposed}")
    return ExtractionResult(
        extracted=extracted,
        run_dir=output,
        proposed_path=artifacts.proposed,
        cached=False,
        resumed=resumed,
        metrics=metrics,
    )
