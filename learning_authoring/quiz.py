"""Single active Quiz flow: reviewed KC JSON -> unapproved Quiz batch."""

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
    write_text,
)
from learning_authoring.audit import reported_cost, response_usage
from learning_authoring.gateway import execute_response
from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.provider import build_client, normalized_model
from learning_authoring.quiz_contracts import QuizBatch, QuizSourceRef
from learning_authoring.quiz_quality import build_quiz_form_audit
from learning_authoring.requests import build_quiz_request

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT_DIR = PACKAGE_DIR / "prompts" / "quiz-v1"
STAGE_VERSION = "quiz.v1.experimental"
PROMPT_COMPONENTS = ("foundation", "rulebook", "task")
ALLOWED_INTERACTIONS = (
    "single_select",
    "multi_select",
    "matching",
    "ordering",
    "short_text",
)


@dataclass(frozen=True)
class QuizPromptPackage:
    instructions: str
    output_schema: dict[str, Any]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class QuizConfig:
    model: str = "gpt-5.6-sol"
    reasoning_effort: str = "high"
    response_mode: str = "background"
    max_output_tokens: int | None = 64000
    prompt_dir: Path = DEFAULT_PROMPT_DIR
    selected_kc_ids: tuple[str, ...] = ()
    include_all_kcs: bool = False
    variants_per_kc: int = 2
    allowed_interactions: tuple[str, ...] = ALLOWED_INTERACTIONS
    language: str = "source"
    poll_interval_seconds: float = 5.0
    timeout_seconds: float = 3600.0
    api_key: str | None = None
    base_url: str | None = None

    def validate(self) -> None:
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if self.reasoning_effort not in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
        }:
            raise ValueError(f"unsupported reasoning effort: {self.reasoning_effort}")
        if self.response_mode not in {"background", "sync"}:
            raise ValueError("response_mode must be background or sync")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.poll_interval_seconds <= 0 or self.timeout_seconds <= 0:
            raise ValueError("poll interval and timeout must be positive")
        if self.include_all_kcs and self.selected_kc_ids:
            raise ValueError("include_all_kcs cannot be combined with selected KC IDs")
        if not self.include_all_kcs and not self.selected_kc_ids:
            raise ValueError("select at least one KC or enable include_all_kcs")
        if len(self.selected_kc_ids) != len(set(self.selected_kc_ids)):
            raise ValueError("selected KC IDs must be unique")
        if self.variants_per_kc < 1:
            raise ValueError("variants_per_kc must be at least 1")
        if not self.allowed_interactions or set(self.allowed_interactions) - set(
            ALLOWED_INTERACTIONS
        ):
            raise ValueError("allowed_interactions contains an unsupported value")
        for component in PROMPT_COMPONENTS:
            path = self.prompt_dir / f"{component}.md"
            if not path.is_file():
                raise ValueError(f"Quiz prompt component does not exist: {path}")


@dataclass(frozen=True)
class QuizGenerationResult:
    proposed: QuizBatch
    proposed_path: Path
    request_preview_path: Path
    cached: bool
    resumed: bool
    metrics: dict[str, Any]


def load_quiz_prompt_package(prompt_dir: Path = DEFAULT_PROMPT_DIR) -> QuizPromptPackage:
    texts = {
        component: (prompt_dir / f"{component}.md").read_text(encoding="utf-8")
        for component in PROMPT_COMPONENTS
    }
    output_schema = QuizBatch.model_json_schema()
    output_schema.pop("$schema", None)
    instructions = "\n\n".join(texts[component] for component in PROMPT_COMPONENTS)
    components: dict[str, Any] = {
        component: {
            "filename": f"{component}.md",
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "content": content,
        }
        for component, content in texts.items()
    }
    schema_bytes = json.dumps(output_schema, ensure_ascii=False, sort_keys=True).encode()
    components["output_schema"] = {
        "source": "learning_authoring.quiz_contracts.QuizBatch",
        "sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "content": output_schema,
    }
    package_bytes = json.dumps(components, ensure_ascii=False, sort_keys=True).encode()
    return QuizPromptPackage(
        instructions=instructions,
        output_schema=output_schema,
        manifest={
            "package_version": STAGE_VERSION,
            "instruction_order": list(PROMPT_COMPONENTS),
            "structured_output_component": "output_schema",
            "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
            "components": components,
        },
    )


def build_quiz_input(
    kc_set: ProposedKCSet,
    *,
    kc_set_sha256: str,
    config: QuizConfig,
) -> dict[str, Any]:
    """Compile only selected Leaf KCs, referenced groups, and runtime policy."""

    config.validate()
    kc_by_id = {kc.kc_id: kc for kc in kc_set.leaf_kcs}
    selected_kc_ids = (
        tuple(kc.kc_id for kc in kc_set.leaf_kcs)
        if config.include_all_kcs
        else config.selected_kc_ids
    )
    unknown = set(selected_kc_ids) - set(kc_by_id)
    if unknown:
        raise ValueError(f"selected unknown KC IDs: {sorted(unknown)}")

    selected_kcs = [kc_by_id[kc_id] for kc_id in selected_kc_ids]
    selected_group_ids = {kc.group_id for kc in selected_kcs}
    selected_groups = [
        group.model_dump(mode="json")
        for group in kc_set.kc_groups
        if group.group_id in selected_group_ids
    ]
    source_ref = QuizSourceRef(
        extraction_source_id=kc_set.source_ref.source_id,
        extraction_source_sha256=kc_set.source_ref.source_sha256,
        kc_set_sha256=kc_set_sha256,
    )
    return {
        "input_version": "quiz-input.v1",
        "source_ref": source_ref.model_dump(mode="json"),
        "runtime": {
            "selected_kc_ids": list(selected_kc_ids),
            "variants_per_kc": config.variants_per_kc,
            "expected_question_count": len(selected_kc_ids) * config.variants_per_kc,
            "allowed_interactions": list(config.allowed_interactions),
            "language": config.language,
        },
        "kc_groups": selected_groups,
        "leaf_kcs": [kc.model_dump(mode="json") for kc in selected_kcs],
    }


def _fingerprint(
    *, input_sha256: str, prompt_sha256: str, model: str, config: QuizConfig
) -> str:
    descriptor = {
        "stage_version": STAGE_VERSION,
        "input_sha256": input_sha256,
        "prompt_package_sha256": prompt_sha256,
        "model": model,
        "reasoning_effort": config.reasoning_effort,
        "response_mode": config.response_mode,
        "max_output_tokens": config.max_output_tokens,
    }
    return hashlib.sha256(
        json.dumps(descriptor, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def prepare_quiz_request(
    run_dir: Path,
    *,
    kc_path: Path | None = None,
    output_dir: Path | None = None,
    config: QuizConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Freeze one exact Quiz request without calling the provider."""

    config.validate()
    root = run_dir.expanduser().resolve()
    resolved_kc = (kc_path or (root / "kc-proposed.json")).expanduser().resolve()
    destination = (output_dir or (root / "quiz")).expanduser().resolve()
    if not resolved_kc.is_file():
        raise RuntimeError(f"KC set is missing: {resolved_kc}")

    kc_set = ProposedKCSet.model_validate(read_json(resolved_kc))
    kc_sha256 = sha256_file(resolved_kc)
    quiz_input = build_quiz_input(kc_set, kc_set_sha256=kc_sha256, config=config)
    prompt_package = load_quiz_prompt_package(config.prompt_dir)
    model = normalized_model(config.model, config.base_url)
    request = build_quiz_request(
        quiz_input_payload=quiz_input,
        instructions=prompt_package.instructions,
        output_schema=prompt_package.output_schema,
        model=model,
        reasoning_effort=config.reasoning_effort,
        response_mode=config.response_mode,
        max_output_tokens=config.max_output_tokens,
    )

    input_bytes = json.dumps(quiz_input, ensure_ascii=False, sort_keys=True).encode()
    input_sha256 = hashlib.sha256(input_bytes).hexdigest()
    fingerprint = _fingerprint(
        input_sha256=input_sha256,
        prompt_sha256=prompt_package.manifest["package_sha256"],
        model=model,
        config=config,
    )
    metadata = {
        "stage": "quiz",
        "stage_version": STAGE_VERSION,
        "quality_status": "experimental_unapproved",
        "generation_performed": False,
        "request_fingerprint": fingerprint,
        "model": model,
        "reasoning_effort": config.reasoning_effort,
        "response_mode": config.response_mode,
        "max_output_tokens": config.max_output_tokens,
        "input_sha256": input_sha256,
        "prompt_package_sha256": prompt_package.manifest["package_sha256"],
        "kc_set": {"path": str(resolved_kc), "sha256": kc_sha256},
        "selected_kc_ids": quiz_input["runtime"]["selected_kc_ids"],
        "variants_per_kc": config.variants_per_kc,
        "model_input_items": [
            "selected original Leaf KCs",
            "their referenced KC Groups",
            "runtime generation policy",
        ],
        "excluded_model_inputs": [
            "extraction JSON",
            "PDF or page images",
            "planning and assessment-spec artifacts",
            "previous Quiz output",
            "validator decisions",
        ],
        "created_at": datetime.now(UTC).isoformat(),
    }

    artifacts = RunArtifacts(destination)
    if artifacts.quiz_metadata.is_file() and (
        artifacts.quiz_checkpoint.is_file() or artifacts.quiz_proposed.is_file()
    ):
        existing = read_json(artifacts.quiz_metadata)
        if existing.get("request_fingerprint") != fingerprint:
            raise RuntimeError("existing Quiz state belongs to a different request")
    write_json(artifacts.quiz_input, quiz_input)
    write_json(artifacts.quiz_prompt_package, prompt_package.manifest)
    write_json(artifacts.quiz_request_preview, request)
    write_json(artifacts.quiz_metadata, metadata)
    return request, metadata


def _output_text(response: Any) -> str:
    value = getattr(response, "output_text", None)
    if isinstance(value, str) and value.strip():
        return value
    raise RuntimeError("Quiz response contains no structured output text")


def run_quiz_generation(
    run_dir: Path,
    *,
    kc_path: Path | None = None,
    output_dir: Path | None = None,
    config: QuizConfig,
    client: Any | None = None,
    progress: Callable[[str], None] | None = print,
) -> QuizGenerationResult:
    """Generate raw Quiz questions once; never repair, rewrite, or approve them."""

    root = run_dir.expanduser().resolve()
    destination = (output_dir or (root / "quiz")).expanduser().resolve()
    request, metadata = prepare_quiz_request(
        root,
        kc_path=kc_path,
        output_dir=destination,
        config=config,
    )
    artifacts = RunArtifacts(destination)
    quiz_input = read_json(artifacts.quiz_input)
    fingerprint = metadata["request_fingerprint"]
    if artifacts.quiz_proposed.is_file() and artifacts.quiz_metrics.is_file():
        proposed = QuizBatch.model_validate(read_json(artifacts.quiz_proposed))
        proposed.validate_against_input(quiz_input)
        metrics = read_json(artifacts.quiz_metrics)
        return QuizGenerationResult(
            proposed=proposed,
            proposed_path=artifacts.quiz_proposed,
            request_preview_path=artifacts.quiz_request_preview,
            cached=True,
            resumed=bool(metrics.get("resumed")),
            metrics=metrics,
        )

    if client is None:
        api_key = config.api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required to generate Quiz questions")
        client = build_client(api_key=api_key, base_url=config.base_url)

    started = time.perf_counter()
    response, raw, model_elapsed, resumed = execute_response(
        client,
        request,
        response_mode=config.response_mode,
        checkpoint_path=artifacts.quiz_checkpoint,
        request_fingerprint=fingerprint,
        poll_interval_seconds=config.poll_interval_seconds,
        timeout_seconds=config.timeout_seconds,
        progress=progress,
    )
    write_json(artifacts.quiz_api_response, raw)
    raw_output = _output_text(response)
    write_text(artifacts.quiz_raw_output, raw_output)
    try:
        proposed = QuizBatch.model_validate_json(raw_output)
        proposed.validate_against_input(quiz_input)
    except (ValidationError, ValueError, RuntimeError) as exc:
        write_json(
            artifacts.quiz_contract_errors,
            {
                "contract_valid": False,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "errors": (
                    json.loads(exc.json(include_url=False))
                    if isinstance(exc, ValidationError)
                    else None
                ),
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        raise

    usage = response_usage(raw)
    provider_created = raw.get("created_at")
    provider_completed = raw.get("completed_at")
    provider_elapsed = (
        float(provider_completed) - float(provider_created)
        if isinstance(provider_created, (int, float))
        and isinstance(provider_completed, (int, float))
        else model_elapsed
    )
    metrics = {
        "metrics_version": "quiz-run-metrics.v1",
        "stage_version": STAGE_VERSION,
        "quality_status": "experimental_unapproved",
        "request_fingerprint": fingerprint,
        "contract_valid": True,
        "raw_output_unedited": True,
        "repair_calls": 0,
        "generation_calls": 1,
        "selected_kc_count": len(quiz_input["runtime"]["selected_kc_ids"]),
        "question_count": len(proposed.questions),
        "interaction_counts": {
            interaction: sum(
                question.interaction == interaction for question in proposed.questions
            )
            for interaction in config.allowed_interactions
        },
        "model_elapsed_seconds": round(provider_elapsed, 6),
        "local_resume_and_validation_seconds": round(time.perf_counter() - started, 6),
        "usage": usage,
        "gateway_reported_cost_usd": reported_cost(raw),
        "resumed": resumed,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    write_json(artifacts.quiz_proposed, proposed.model_dump(mode="json"))
    write_json(artifacts.quiz_form_audit, build_quiz_form_audit(proposed))
    write_json(artifacts.quiz_metrics, metrics)
    if artifacts.quiz_contract_errors.exists():
        artifacts.quiz_contract_errors.unlink()
    if progress:
        progress(f"[quiz] PROPOSED: {artifacts.quiz_proposed}")
    return QuizGenerationResult(
        proposed=proposed,
        proposed_path=artifacts.quiz_proposed,
        request_preview_path=artifacts.quiz_request_preview,
        cached=False,
        resumed=resumed,
        metrics=metrics,
    )
