"""Single active Quiz flow: reviewed KC JSON -> unapproved Quiz batch."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from copy import deepcopy
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
from learning_authoring.authoring_context import (
    CONTEXT_MANIFEST,
    load_authoring_context,
    load_bundle_authoring_context,
)
from learning_authoring.contracts import SourceDescriptor
from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.prompt_packages import (
    WorkedExample,
    load_worked_example_suite,
    worked_examples_component,
)
from learning_authoring.quiz_contracts import (
    CURRENT_QUIZ_INPUT_VERSION,
    CURRENT_QUIZ_SCHEMA_VERSION,
    QuizBatch,
    QuizBatchV2,
    QuizBatchV3,
    QuizSchemaVersion,
    QuizSourceRef,
    quiz_output_schema,
)
from learning_authoring.quiz_quality import build_quiz_form_audit
from learning_authoring.source_bundle import (
    SOURCE_BUNDLE_MANIFEST,
    SourceBundleKCSet,
    load_bundle_extractions,
    load_source_bundle,
    validate_kc_set_against_bundle,
)

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT_DIR = PACKAGE_DIR / "prompts" / "quiz-v1"
DEFAULT_EXAMPLES_DIR = DEFAULT_PROMPT_DIR / "examples-v3"
BUNDLE_EXAMPLES_DIR = DEFAULT_PROMPT_DIR / "examples-bundle-v1"
STAGE_VERSION = "quiz.v3.experimental"
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
    worked_examples: tuple[WorkedExample, ...]
    manifest: dict[str, Any]

    @property
    def lineage(self) -> dict[str, Any]:
        return self.manifest


@dataclass(frozen=True)
class QuizConfig:
    model: str = "gpt-5.6-sol"
    reasoning_effort: str = "high"
    response_mode: str = "background"
    max_output_tokens: int | None = 64000
    prompt_dir: Path = DEFAULT_PROMPT_DIR
    selected_kc_ids: tuple[str, ...] = ()
    include_all_kcs: bool = False
    variants_per_kc: int | None = None
    min_slots_per_kc: int = 1
    max_slots_per_kc: int | None = None
    variants_per_slot: int | None = None
    max_variants_per_slot: int | None = None
    total_question_budget: int | None = None
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
        if self.min_slots_per_kc is None:
            raise ValueError("min_slots_per_kc must be at least 1")
        for name in (
            "variants_per_kc",
            "min_slots_per_kc",
            "max_slots_per_kc",
            "variants_per_slot",
            "max_variants_per_slot",
            "total_question_budget",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise ValueError(f"{name} must be a positive integer when supplied")
        if self.max_slots_per_kc is not None and self.max_slots_per_kc < self.min_slots_per_kc:
            raise ValueError("max_slots_per_kc must be at least min_slots_per_kc")
        if (
            self.variants_per_slot is not None
            and self.max_variants_per_slot is not None
            and (self.variants_per_slot > self.max_variants_per_slot)
        ):
            raise ValueError("variants_per_slot exceeds max_variants_per_slot")
        if self.variants_per_kc is not None and (
            self.min_slots_per_kc != 1
            or self.max_slots_per_kc is not None
            or self.variants_per_slot is not None
            or self.max_variants_per_slot is not None
        ):
            raise ValueError(
                "legacy variants_per_kc cannot be combined with assessment-slot limits"
            )
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


def load_quiz_prompt_package(
    prompt_dir: Path = DEFAULT_PROMPT_DIR,
    *,
    schema_version: QuizSchemaVersion = CURRENT_QUIZ_SCHEMA_VERSION,
    examples_dir: Path = DEFAULT_EXAMPLES_DIR,
) -> QuizPromptPackage:
    texts = {
        component: (prompt_dir / f"{component}.md").read_text(encoding="utf-8")
        for component in PROMPT_COMPONENTS
    }
    output_schema = quiz_output_schema(schema_version)
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
        "source": "learning_authoring.quiz_contracts.quiz_output_schema",
        "schema_version": schema_version,
        "sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "content": output_schema,
    }
    suite = None
    if schema_version == "quiz-batch.v3":
        suite = load_worked_example_suite(
            examples_dir,
            expected_stage="quiz",
            expected_contract_version="quiz-batch.v3",
        )
        components["worked_examples"] = worked_examples_component(
            suite,
            filename=f"{examples_dir.name}/manifest.json",
        )
    package_bytes = json.dumps(components, ensure_ascii=False, sort_keys=True).encode()
    return QuizPromptPackage(
        instructions=instructions,
        output_schema=output_schema,
        worked_examples=suite.examples if suite is not None else (),
        manifest={
            "package_version": STAGE_VERSION,
            "instruction_order": list(PROMPT_COMPONENTS),
            "structured_output_component": "output_schema",
            "worked_examples_component": "worked_examples" if suite is not None else None,
            "worked_example_order": list(suite.example_order) if suite is not None else [],
            "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
            "components": components,
        },
    )


def build_quiz_input(
    kc_set: ProposedKCSet | SourceBundleKCSet,
    *,
    kc_set_sha256: str,
    config: QuizConfig,
    raw_kc_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile selected original KC/group records and code-owned runtime policy.

    File-backed callers supply the parsed raw JSON as well as its validated model.
    Validation defaults must not be injected into the downstream copy: absent and
    explicit-empty fields have different browser review baseline hashes.  Model-only
    callers keep their historical normalized representation.
    """

    config.validate()
    if raw_kc_set is None:
        original = kc_set.model_dump(mode="json")
    else:
        contract = SourceBundleKCSet if isinstance(kc_set, SourceBundleKCSet) else ProposedKCSet
        if contract.model_validate(raw_kc_set) != kc_set:
            raise ValueError("raw KC set does not match the validated KC set")
        original = raw_kc_set
    kc_by_id = {kc.kc_id: kc for kc in kc_set.leaf_kcs}
    original_kc_by_id = {kc["kc_id"]: kc for kc in original["leaf_kcs"]}
    selected_kc_ids = (
        tuple(kc.kc_id for kc in kc_set.leaf_kcs)
        if config.include_all_kcs
        else config.selected_kc_ids
    )
    unknown = set(selected_kc_ids) - set(kc_by_id)
    if unknown:
        raise ValueError(f"selected unknown KC IDs: {sorted(unknown)}")
    if not selected_kc_ids:
        raise ValueError("Quiz requires at least one selected Leaf KC")

    adaptive = config.variants_per_kc is None
    expected_question_count = None if adaptive else len(selected_kc_ids) * config.variants_per_kc
    minimum_question_count = (
        len(selected_kc_ids) * config.min_slots_per_kc * (config.variants_per_slot or 1)
        if adaptive
        else expected_question_count
    )
    if config.total_question_budget is not None and (
        minimum_question_count > config.total_question_budget
    ):
        raise ValueError(
            f"infeasible total_question_budget {config.total_question_budget}: "
            f"covering all {len(selected_kc_ids)} selected KCs with the configured minimum "
            f"requires at least {minimum_question_count} questions; no KCs will be truncated"
        )

    selected_kcs = [kc_by_id[kc_id] for kc_id in selected_kc_ids]
    selected_group_ids = {kc.group_id for kc in selected_kcs}
    selected_groups = [
        deepcopy(group)
        for group in original["kc_groups"]
        if group["group_id"] in selected_group_ids
    ]
    if isinstance(kc_set, SourceBundleKCSet):
        source_ref = QuizSourceRef(
            source_bundle_sha256=kc_set.source_ref.source_bundle_sha256,
            kc_set_sha256=kc_set_sha256,
            authoring_context_sha256=kc_set.source_ref.authoring_context_sha256,
        )
    else:
        source_ref = QuizSourceRef(
            extraction_source_id=kc_set.source_ref.source_id,
            extraction_source_sha256=kc_set.source_ref.source_sha256,
            kc_set_sha256=kc_set_sha256,
            authoring_context_sha256=getattr(kc_set.source_ref, "authoring_context_sha256", None),
        )
    if isinstance(kc_set, SourceBundleKCSet):
        known_source_pages = {(audit.source_id, audit.page) for audit in kc_set.page_audit}
        for kc in selected_kcs:
            for evidence in kc.context_evidence:
                if evidence.pages and evidence.source_id is None:
                    raise ValueError("bundle page-mapped context evidence requires source_id")
                if evidence.source_id is not None and any(
                    (evidence.source_id, page) not in known_source_pages for page in evidence.pages
                ):
                    raise ValueError("bundle context evidence references an unknown source page")
    elif any(
        evidence.source_id is not None for kc in selected_kcs for evidence in kc.context_evidence
    ):
        raise ValueError("single-source context evidence must not add source_id")
    if any(getattr(kc, "context_evidence", []) for kc in selected_kcs) and (
        not source_ref.authoring_context_sha256
    ):
        raise ValueError("selected KC context evidence requires a bound authoring context hash")
    return {
        "input_version": CURRENT_QUIZ_INPUT_VERSION if adaptive else "quiz-input.v1",
        "source_ref": source_ref.model_dump(mode="json"),
        "runtime": {
            "selected_kc_ids": list(selected_kc_ids),
            "assessment_mode": "adaptive_slots" if adaptive else "legacy_per_kc",
            "expected_schema_version": (
                CURRENT_QUIZ_SCHEMA_VERSION if adaptive else "quiz-batch.v1"
            ),
            "variants_per_kc": config.variants_per_kc,
            "min_slots_per_kc": config.min_slots_per_kc,
            "max_slots_per_kc": config.max_slots_per_kc,
            "variants_per_slot": config.variants_per_slot,
            "max_variants_per_slot": config.max_variants_per_slot,
            "total_question_budget": config.total_question_budget,
            "expected_question_count": expected_question_count,
            "minimum_question_count": minimum_question_count,
            "allowed_interactions": list(config.allowed_interactions),
            "language": config.language,
        },
        "kc_groups": selected_groups,
        "leaf_kcs": [deepcopy(original_kc_by_id[kc_id]) for kc_id in selected_kc_ids],
    }


def _fingerprint(*, input_sha256: str, prompt_sha256: str, model: str, config: QuizConfig) -> str:
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

    from learning_authoring.legacy_api.provider import normalized_model
    from learning_authoring.legacy_api.requests import build_quiz_request

    config.validate()
    root = run_dir.expanduser().resolve()
    resolved_kc = (kc_path or (root / "kc-proposed.json")).expanduser().resolve()
    destination = (output_dir or (root / "quiz")).expanduser().resolve()
    if not resolved_kc.is_file():
        raise RuntimeError(f"KC set is missing: {resolved_kc}")

    raw_kc_set = read_json(resolved_kc)
    raw_source_ref = raw_kc_set.get("source_ref")
    bundle_mode = isinstance(raw_source_ref, dict) and (
        raw_source_ref.get("schema_version") == "source-bundle.v1"
    )
    kc_set: ProposedKCSet | SourceBundleKCSet
    if bundle_mode:
        kc_set = SourceBundleKCSet.model_validate(raw_kc_set)
        bundle = load_source_bundle(root)
        extractions = load_bundle_extractions(root, bundle)
        context = load_bundle_authoring_context(root, bundle)
        validated = validate_kc_set_against_bundle(
            kc_set,
            bundle,
            extractions,
            authoring_context=context,
        )
        if not isinstance(validated, SourceBundleKCSet):
            raise ValueError("multi-source Quiz requires source-qualified KC lineage")
        kc_set = validated
        context_sha256 = kc_set.source_ref.authoring_context_sha256
        if context_sha256 != (context.sha256 if context else None):
            raise ValueError(
                "KC authoring context SHA-256 does not match the current bundle context"
            )
    else:
        kc_set = ProposedKCSet.model_validate(raw_kc_set)
    context_sha256 = getattr(kc_set.source_ref, "authoring_context_sha256", None)
    if not bundle_mode and (
        context_sha256
        or (root / CONTEXT_MANIFEST).exists()
        or (root / CONTEXT_MANIFEST).is_symlink()
        or (root / "authoring-context").exists()
    ):
        manifest_path = root / "source-manifest.json"
        if not manifest_path.is_file():
            raise ValueError("source manifest is required to verify Quiz authoring-context binding")
        source = SourceDescriptor.model_validate(read_json(manifest_path)["source"])
        context = load_authoring_context(root, source)
        if context_sha256 != (context.sha256 if context else None):
            raise ValueError("KC authoring context SHA-256 does not match the current run context")
        if kc_set.source_ref.source_sha256 != source.sha256 or (
            kc_set.source_ref.source_id != source.source_id
        ):
            raise ValueError("KC source identity does not match the Quiz run")
        if context is not None:
            for kc in kc_set.leaf_kcs:
                for evidence in kc.context_evidence:
                    evidence.validate_against_context(context)
    if bundle_mode and not (root / SOURCE_BUNDLE_MANIFEST).is_file():
        raise ValueError("source bundle manifest is required for multi-source Quiz lineage")
    kc_sha256 = sha256_file(resolved_kc)
    quiz_input = build_quiz_input(
        kc_set,
        kc_set_sha256=kc_sha256,
        config=config,
        raw_kc_set=raw_kc_set,
    )
    prompt_package = load_quiz_prompt_package(
        config.prompt_dir,
        schema_version=quiz_input["runtime"]["expected_schema_version"],
    )
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
        "assessment_mode": quiz_input["runtime"]["assessment_mode"],
        "expected_schema_version": quiz_input["runtime"]["expected_schema_version"],
        "variants_per_kc": config.variants_per_kc,
        "min_slots_per_kc": config.min_slots_per_kc,
        "max_slots_per_kc": config.max_slots_per_kc,
        "variants_per_slot": config.variants_per_slot,
        "max_variants_per_slot": config.max_variants_per_slot,
        "total_question_budget": config.total_question_budget,
        "minimum_question_count": quiz_input["runtime"]["minimum_question_count"],
        "expected_question_count": quiz_input["runtime"]["expected_question_count"],
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

    from learning_authoring.legacy_api.gateway import execute_response
    from learning_authoring.legacy_api.provider import build_client

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
        expected_schema = quiz_input["runtime"]["expected_schema_version"]
        if expected_schema == "quiz-batch.v3":
            proposed = QuizBatchV3.model_validate_json(raw_output, strict=True)
        elif expected_schema == "quiz-batch.v2":
            proposed = QuizBatchV2.model_validate_json(raw_output, strict=True)
        else:
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
                    json.loads(exc.json(include_url=False, include_context=False))
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
        "assessment_slot_count": len(proposed.assessment_slots),
        "hint_count": sum(len(question.hints) for question in proposed.questions),
        "schema_version": proposed.schema_version,
        "assessment_mode": quiz_input["runtime"]["assessment_mode"],
        "interaction_counts": {
            interaction: sum(question.interaction == interaction for question in proposed.questions)
            for interaction in config.allowed_interactions
        },
        "model_elapsed_seconds": round(provider_elapsed, 6),
        "local_resume_and_validation_seconds": round(time.perf_counter() - started, 6),
        "usage": usage,
        "gateway_reported_cost_usd": reported_cost(raw),
        "resumed": resumed,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    write_text(artifacts.quiz_proposed, raw_output)
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
