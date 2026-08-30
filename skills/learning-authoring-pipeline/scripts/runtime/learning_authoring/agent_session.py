"""Import artifacts produced inside a coding-agent subscription session.

This module intentionally contains no provider client or response-gateway dependency.  The
coding agent produces candidate JSON in its own subscription session; this code preserves those
bytes, validates them, binds code-owned inputs, and writes the same proposed artifacts consumed by
the existing review surfaces.
"""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from learning_authoring.artifacts import (
    RunArtifacts,
    read_json,
    sha256_bytes,
    sha256_file,
    write_bytes,
    write_json,
    write_text,
)
from learning_authoring.audit import build_audit, validate_extraction_geometry
from learning_authoring.authoring_context import (
    AuthoringContext,
    load_authoring_context,
    load_bundle_authoring_context,
    prepare_authoring_context,
    prepare_bundle_authoring_context,
)
from learning_authoring.contracts import ExtractedSource, ExtractedSourcePayload, SourceDescriptor
from learning_authoring.kc import load_approved_extraction, load_prompt_package
from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.kc_diagnostics import kc_review_diagnostics
from learning_authoring.kc_review import build_kc_demo
from learning_authoring.prompt_packages import (
    load_worked_example_suite,
    worked_examples_component,
)
from learning_authoring.quiz import (
    BUNDLE_EXAMPLES_DIR as BUNDLE_QUIZ_EXAMPLES_DIR,
)
from learning_authoring.quiz import (
    DEFAULT_EXAMPLES_DIR as DEFAULT_QUIZ_EXAMPLES_DIR,
)
from learning_authoring.quiz import (
    QuizConfig,
    build_quiz_input,
    load_quiz_prompt_package,
)
from learning_authoring.quiz_contracts import (
    CURRENT_QUIZ_SCHEMA_VERSION,
    QuizBatch,
    QuizBatchV3,
    quiz_output_schema,
)
from learning_authoring.quiz_quality import build_quiz_form_audit
from learning_authoring.quiz_review import build_quiz_review
from learning_authoring.quiz_review_state import (
    AUDIT_FILENAME,
    AUDIT_METADATA_FILENAME,
    material_digest,
    quiz_review_material,
)
from learning_authoring.quiz_semantics import (
    BUNDLE_EXAMPLES_DIR as BUNDLE_REVIEW_EXAMPLES_DIR,
)
from learning_authoring.quiz_semantics import (
    DEFAULT_EXAMPLES_DIR as DEFAULT_REVIEW_EXAMPLES_DIR,
)
from learning_authoring.quiz_semantics import (
    QuizSemanticAudit,
    load_semantic_review_prompt_package,
    semantic_audit_summary,
    semantic_review_schema,
    validate_semantic_audit,
)
from learning_authoring.review import build_review
from learning_authoring.source import DEFAULT_RENDER_DPI, prepare_or_reuse_source
from learning_authoring.source_bundle import (
    SOURCE_BUNDLE_MANIFEST,
    SourceBundle,
    SourceBundleKCSet,
    load_bundle_extractions,
    load_source_bundle,
    prepare_source_bundle,
    validate_kc_set_against_bundle,
)

AgentStage = Literal["extraction", "kc", "quiz", "quiz-review"]
EXECUTION_MODE = "agent_subscription_session"
IMPORT_VERSION = "agent-session-import.v1"
PACKAGE_DIR = Path(__file__).resolve().parent
AGENT_OUTPUT_POLICY = {
    "format": "JSON only",
    "human_review_required": True,
    "approval_created_by_import": False,
    "preserve_candidate_bytes": True,
    "semantic_candidate_authorship": "direct_host_agent_reasoning",
    "course_specific_executable_generator_forbidden": True,
    "fixed_semantic_counts_forbidden_unless_runtime_supplies_them": True,
    "all_run_specific_values_must_be_derived_from_the_frozen_input": True,
}


class CandidateAttemptPolicyError(ValueError):
    """A preserved candidate cannot enter the canonical stage under the retry policy."""

    def __init__(
        self,
        message: str,
        *,
        status: str,
        attempt_number: int,
        distinct_candidate_count_before: int,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.attempt_number = attempt_number
        self.distinct_candidate_count_before = distinct_candidate_count_before


class TaskPackageRequiredError(ValueError):
    """The candidate was preserved, but no frozen task was supplied."""


def agent_schema(
    stage: AgentStage,
    *,
    legacy_quiz: bool = False,
    source_bundle: bool = False,
) -> dict[str, Any]:
    """Return the exact candidate contract for one agent-native stage."""

    if source_bundle and stage != "kc":
        raise ValueError("source-bundle candidate schema is currently defined for KC only")
    if source_bundle:
        return SourceBundleKCSet.model_json_schema()
    if stage == "quiz":
        return quiz_output_schema(
            "quiz-batch.v1" if legacy_quiz else CURRENT_QUIZ_SCHEMA_VERSION,
            strict_output=False,
        )
    if stage == "quiz-review":
        return semantic_review_schema()
    contracts: dict[AgentStage, type[BaseModel]] = {
        "extraction": ExtractedSourcePayload,
        "kc": ProposedKCSet,
    }
    try:
        contract = contracts[stage]
    except KeyError as exc:  # pragma: no cover - argparse constrains public calls
        raise ValueError(f"unsupported agent stage: {stage}") from exc
    return contract.model_json_schema()


def _session_dir(run_dir: Path) -> Path:
    return run_dir / "agent-session"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _worked_examples_payload(prompt: Any) -> list[dict[str, Any]]:
    return [example.as_payload() for example in prompt.worked_examples]


def _prompt_task_fields(prompt: Any) -> dict[str, Any]:
    return {
        "worked_examples": _worked_examples_payload(prompt),
        "prompt_lineage": prompt.lineage,
    }


def _native_extraction_prompt_fields() -> tuple[str, dict[str, Any]]:
    """Load Extraction prompt assets without importing the legacy execution adapter."""

    prompt_path = PACKAGE_DIR / "prompts" / "extractor-v2.md"
    instructions = prompt_path.read_text(encoding="utf-8")
    output_schema = ExtractedSourcePayload.model_json_schema()
    suite = load_worked_example_suite(
        PACKAGE_DIR / "prompts" / "extractor-v2" / "examples-v1",
        expected_stage="extraction",
        expected_contract_version="extracted-source.v2",
    )
    schema_bytes = json.dumps(output_schema, ensure_ascii=False, sort_keys=True).encode()
    components = {
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
    return instructions, {
        "worked_examples": suite.as_payload(),
        "prompt_lineage": {
            "package_version": "extraction-prompt.v1",
            "instruction_order": ["instructions"],
            "structured_output_component": "output_schema",
            "worked_examples_component": "worked_examples",
            "worked_example_order": list(suite.example_order),
            "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
            "components": components,
        },
    }


def _extraction_instructions(prompt_instructions: str) -> str:
    return (
        "HOST DELIVERY POLICY:\n"
        "- Treat the native PDF as the primary input.\n"
        "- Do not load or attach all rendered page PNGs.\n"
        "- Page-image paths are locators only. Inspect at most the specific page image "
        "needed for targeted visual or geometry clarification.\n"
        "- Never use local text-audit files as semantic model input.\n\n" + prompt_instructions
    )


def _bundle_kc_prompt_fields(schema: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Load the source-bundle KC prompt without singular one-PDF instructions."""

    prompt_dir = PACKAGE_DIR / "prompts" / "kc-v1"
    bundle_prompt_dir = prompt_dir / "bundle-v1"
    instruction_order = ("foundation", "rulebook", "task")
    texts = {
        name: (bundle_prompt_dir / f"{name}.md").read_text(encoding="utf-8")
        for name in instruction_order
    }
    instructions = "\n\n".join(texts[name] for name in instruction_order)

    suite = load_worked_example_suite(
        prompt_dir / "examples-bundle-v1",
        expected_stage="kc-source-bundle",
        expected_contract_version="source-bundle-kc-set.v1",
    )
    components: dict[str, Any] = {
        name: {
            "filename": f"bundle-v1/{name}.md",
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "content": content,
        }
        for name, content in texts.items()
    }
    schema_bytes = json.dumps(schema, ensure_ascii=False, sort_keys=True).encode()
    components["output_schema"] = {
        "source": "learning_authoring.source_bundle.SourceBundleKCSet",
        "schema_version": "source-bundle-kc-set.v1",
        "sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "content": schema,
    }
    components["worked_examples"] = worked_examples_component(
        suite,
        filename="examples-bundle-v1/manifest.json",
    )
    package_bytes = json.dumps(components, ensure_ascii=False, sort_keys=True).encode()
    return instructions, {
        "worked_examples": suite.as_payload(),
        "prompt_lineage": {
            "package_version": "kc-source-bundle.v2",
            "instruction_order": list(instruction_order),
            "structured_output_component": "output_schema",
            "worked_examples_component": "worked_examples",
            "worked_example_order": list(suite.example_order),
            "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
            "components": components,
        },
    }


def _prompt_delivery_sha256(task: dict[str, Any]) -> str:
    """Hash only model-visible prompt material embedded in an agent-task.v3 package."""

    return _fingerprint(
        {
            "instructions": task["instructions"],
            "candidate_schema": task["candidate_contract"]["schema"],
            "worked_examples": task["worked_examples"],
        }
    )


def _task_lineage(lineage: dict[str, Any]) -> dict[str, Any]:
    """Keep provenance metadata without embedding worked examples a second time."""

    task_lineage = deepcopy(lineage)
    component_name = task_lineage.get("worked_examples_component")
    components = task_lineage.get("components")
    if isinstance(component_name, str) and isinstance(components, dict):
        component = components.get(component_name)
        if isinstance(component, dict) and "content" in component:
            component.pop("content")
            component["content_ref"] = "/worked_examples"
    return task_lineage


def _markdown_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    fence = "```"
    while fence in encoded:
        fence += "`"
    return f"{fence}json\n{encoded}\n{fence}"


def _render_agent_task(task: dict[str, Any], package_path: Path) -> str:
    """Render the prompt-sized material a host agent must read before authoring."""

    next_command = deepcopy(task["next_command"])
    next_command["argv"] = [
        str(package_path) if value == "<task-package-json>" else value
        for value in next_command["argv"]
    ]
    input_keys = ", ".join(f"`{key}`" for key in task["input_boundary"])
    return (
        f"# Subscription-native {task['stage']} task\n\n"
        "Read this artifact before authoring the candidate. Worked-example values are "
        "illustrative; follow their contract and teaching points without copying their "
        "content.\n\n"
        f"- Frozen task package: `{package_path}`\n"
        f"- Task fingerprint: `{task['task_fingerprint']}`\n"
        f"- Prompt delivery SHA-256: `{task['prompt_delivery_sha256']}`\n\n"
        "## Instructions\n\n"
        f"{task['instructions'].rstrip()}\n\n"
        "## Selected worked examples\n\n"
        f"{_markdown_json(task['worked_examples'])}\n\n"
        "## Candidate JSON schema\n\n"
        f"{_markdown_json(task['candidate_contract']['schema'])}\n\n"
        "## Frozen input boundary\n\n"
        f"Read JSON pointer `/input_boundary` from `{package_path}`. Its top-level fields "
        f"are: {input_keys}. The input is not repeated here so large source payloads remain "
        "available for targeted inspection without diluting this prompt.\n\n"
        "## Output policy\n\n"
        f"{_markdown_json(task['output_policy'])}\n\n"
        "## Import command\n\n"
        f"{_markdown_json(next_command)}\n"
    )


def _task_audit_fields(task: dict[str, Any] | None) -> dict[str, Any]:
    if task is None:
        return {
            "prompt_delivery_sha256": None,
            "prompt_package_sha256": None,
        }
    lineage = task.get("prompt_lineage") or {}
    return {
        "prompt_delivery_sha256": task.get("prompt_delivery_sha256"),
        "prompt_package_sha256": lineage.get("package_sha256"),
    }


def _preserve_candidate(
    stage: AgentStage,
    run_dir: Path,
    candidate_path: Path,
) -> tuple[bytes, str, Path]:
    source = candidate_path.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"candidate JSON does not exist: {source}")
    raw = source.read_bytes()
    digest = sha256_bytes(raw)
    destination = _session_dir(run_dir) / "candidates" / f"{stage}-{digest}.json"
    if destination.is_file():
        if destination.read_bytes() != raw:
            raise RuntimeError("candidate SHA-256 collision in agent-session archive")
    else:
        write_bytes(destination, raw)
    return raw, digest, destination


def _candidate_attempt_records(
    stage: AgentStage,
    run_dir: Path,
    task_fingerprint: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    imports_dir = _session_dir(run_dir) / "imports"
    if not imports_dir.is_dir():
        return records
    for path in imports_dir.glob(f"{stage}-*.json"):
        try:
            record = read_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"agent-session import record is unreadable: {path}") from exc
        if record.get("stage") == stage and record.get("task_fingerprint") == task_fingerprint:
            records.append(record)
    return records


def _record_allows_fresh_retry(
    stage: AgentStage,
    run_dir: Path,
    record: dict[str, Any],
) -> bool:
    explicit = record.get("fresh_retry_authorized")
    if isinstance(explicit, bool):
        return explicit
    if record.get("status") == "CONTRACT_INVALID":
        return True
    if stage == "quiz":
        metadata_path = RunArtifacts(run_dir / "quiz").quiz_metadata
        if metadata_path.is_file():
            metadata = read_json(metadata_path)
            return bool(
                metadata.get("candidate_raw_sha256") == record.get("candidate_raw_sha256")
                and metadata.get("quality_revision_recommended") is True
            )
    return False


def _start_candidate_attempt(
    stage: AgentStage,
    run_dir: Path,
    raw_sha256: str,
    task: dict[str, Any],
) -> dict[str, Any]:
    """Authorize one initial candidate plus at most one justified fresh retry."""

    task_fingerprint = task["task_fingerprint"]
    records = _candidate_attempt_records(stage, run_dir, task_fingerprint)
    by_hash = {record["candidate_raw_sha256"]: record for record in records}
    prior = by_hash.get(raw_sha256)
    if prior is not None:
        if prior.get("status") in {"RETRY_NOT_AUTHORIZED", "RETRY_LIMIT_EXCEEDED"}:
            raise CandidateAttemptPolicyError(
                "this candidate was already archived and rejected by the retry policy",
                status=str(prior["status"]),
                attempt_number=int(prior.get("candidate_attempt_number") or len(by_hash)),
                distinct_candidate_count_before=len(by_hash),
            )
        return {
            "candidate_attempt_number": int(prior.get("candidate_attempt_number") or 1),
            "distinct_candidate_count_before": len(by_hash),
            "identical_reimport": True,
            "prior_fresh_retry_authorized": bool(prior.get("fresh_retry_authorized")),
            "prior_fresh_retry_reason": prior.get("fresh_retry_reason"),
        }

    distinct_count = len(by_hash)
    if distinct_count >= 2:
        raise CandidateAttemptPolicyError(
            "at most two distinct candidates are permitted for this stage and frozen task",
            status="RETRY_LIMIT_EXCEEDED",
            attempt_number=distinct_count + 1,
            distinct_candidate_count_before=distinct_count,
        )
    if distinct_count == 1:
        first = next(iter(by_hash.values()))
        if not _record_allows_fresh_retry(stage, run_dir, first):
            raise CandidateAttemptPolicyError(
                "a fresh retry is not authorized after the first candidate outcome",
                status="RETRY_NOT_AUTHORIZED",
                attempt_number=2,
                distinct_candidate_count_before=1,
            )
    return {
        "candidate_attempt_number": distinct_count + 1,
        "distinct_candidate_count_before": distinct_count,
        "identical_reimport": False,
    }


def _load_required_import_task(
    stage: AgentStage,
    run_dir: Path,
    task_package: Path | None,
) -> dict[str, Any]:
    if task_package is None:
        raise TaskPackageRequiredError(
            f"{stage} import requires the exact frozen --task-package from agent-task"
        )
    task = _load_task_package(stage, run_dir, task_package)
    if task.get("task_package_version") != "agent-task.v3":
        raise ValueError("current agent imports require an official agent-task.v3 package")
    return task


def _import_error_status(error: Exception) -> str:
    if isinstance(error, CandidateAttemptPolicyError):
        return error.status
    if isinstance(error, TaskPackageRequiredError):
        return "TASK_PACKAGE_REQUIRED"
    return "CONTRACT_INVALID"


def _error_attempt(
    error: Exception,
    attempt: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(error, CandidateAttemptPolicyError):
        return attempt
    return {
        "candidate_attempt_number": error.attempt_number,
        "distinct_candidate_count_before": error.distinct_candidate_count_before,
        "identical_reimport": False,
    }


def _contract_failure_retry_authorized(
    task: dict[str, Any] | None,
    attempt: dict[str, Any] | None,
    error: Exception,
) -> bool:
    if attempt and attempt.get("prior_fresh_retry_authorized") is True:
        return True
    return bool(
        task
        and attempt
        and attempt["candidate_attempt_number"] == 1
        and not attempt["identical_reimport"]
        and _import_error_status(error) == "CONTRACT_INVALID"
    )


def _contract_failure_retry_reason(
    task: dict[str, Any] | None,
    attempt: dict[str, Any] | None,
    error: Exception,
) -> str | None:
    if attempt and attempt.get("prior_fresh_retry_authorized") is True:
        return attempt.get("prior_fresh_retry_reason") or "initial_candidate_contract_failure"
    if _contract_failure_retry_authorized(task, attempt, error):
        return "initial_candidate_contract_failure"
    return None


def _unavailable_metrics() -> dict[str, Any]:
    return {
        "execution_mode": EXECUTION_MODE,
        "provider_api_calls": 0,
        "usage": None,
        "usage_available": False,
        "usage_status": "unavailable_in_subscription_session",
        "gateway_reported_cost_usd": None,
        "cost_available": False,
        "cost_status": "unavailable_in_subscription_session",
        "human_review_required": True,
        "approved": False,
    }


def _write_import_record(
    *,
    stage: AgentStage,
    run_dir: Path,
    raw_path: Path,
    raw_sha256: str,
    status: str,
    canonical_path: Path | None = None,
    error: Exception | None = None,
    task_fingerprint: str | None = None,
    task: dict[str, Any] | None = None,
    attempt: dict[str, Any] | None = None,
    fresh_retry_authorized: bool = False,
    fresh_retry_reason: str | None = None,
    canonical_write_performed: bool = False,
) -> Path:
    task_key = task_fingerprint or "unbound"
    record_path = _session_dir(run_dir) / "imports" / f"{stage}-{task_key}-{raw_sha256}.json"
    errors = None
    if isinstance(error, ValidationError):
        errors = error.errors(include_url=False, include_context=False)
    write_json(
        record_path,
        {
            "import_version": IMPORT_VERSION,
            "stage": stage,
            "status": status,
            "execution_mode": EXECUTION_MODE,
            "provider_api_calls": 0,
            "candidate_raw_path": str(raw_path),
            "candidate_raw_sha256": raw_sha256,
            "candidate_bytes_preserved_exactly": True,
            "task_fingerprint": task_fingerprint,
            **_task_audit_fields(task),
            "candidate_attempt_number": (
                attempt.get("candidate_attempt_number") if attempt else None
            ),
            "distinct_candidate_count_before": (
                attempt.get("distinct_candidate_count_before") if attempt else None
            ),
            "identical_reimport": attempt.get("identical_reimport") if attempt else False,
            "fresh_retry_authorized": fresh_retry_authorized,
            "fresh_retry_reason": fresh_retry_reason,
            "canonical_proposed_path": str(canonical_path) if canonical_path else None,
            "canonical_write_performed": canonical_write_performed,
            "human_review_required": True,
            "approval_created": False,
            "error_type": type(error).__name__ if error else None,
            "error": str(error) if error else None,
            "errors": errors,
            "recorded_at": _now(),
        },
    )
    return record_path


def agent_init(
    pdf_path: Path,
    run_dir: Path,
    *,
    render_dpi: int = DEFAULT_RENDER_DPI,
    context_files: tuple[Path, ...] = (),
    context_texts: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Prepare source identity, local text audit, and rendered pages without generation."""

    output = run_dir.expanduser().resolve()
    source, manifest, reused = prepare_or_reuse_source(
        pdf_path,
        output,
        render_dpi=render_dpi,
        progress=None,
    )
    context = prepare_authoring_context(output, source, context_files, context_texts)
    context_ref = _context_ref(output, context)
    session_manifest = _session_dir(output) / "session.json"
    write_json(
        session_manifest,
        {
            "session_version": "agent-subscription-session.v1",
            "execution_mode": EXECUTION_MODE,
            "provider_api_calls": 0,
            "generation_performed": False,
            "source": source.model_dump(mode="json"),
            "source_manifest": str(RunArtifacts(output).source_manifest),
            "source_reused": reused,
            "render_dpi": render_dpi,
            "authoring_context": context_ref,
            "status": "SOURCE_READY",
            "human_review_required": True,
            "created_at": _now(),
        },
    )
    return {
        "run_dir": str(output),
        "source": source.model_dump(mode="json"),
        "source_manifest_version": manifest.get("manifest_version"),
        "rendered_page_count": manifest.get("rendered_page_count"),
        "source_reused": reused,
        "execution_mode": EXECUTION_MODE,
        "provider_api_calls": 0,
        "generation_performed": False,
        "session_manifest": str(session_manifest),
        "authoring_context": context_ref,
    }


def _context_ref(run_dir: Path, context: AuthoringContext | None) -> dict[str, Any] | None:
    if context is None:
        return None
    return {
        "path": str(run_dir / "authoring-context.json"),
        "sha256": context.sha256,
        "item_count": len(context.items),
        "role": "supplementary_lecturer_context_not_slide_extraction",
    }


def agent_bundle(
    run_dir: Path,
    source_runs: tuple[Path, ...],
    *,
    context_files: tuple[Path, ...] = (),
    context_texts: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Freeze an ordered 1..N collection after each PDF has its own Extraction."""

    root = run_dir.expanduser().resolve()
    bundle = prepare_source_bundle(root, source_runs)
    context = prepare_bundle_authoring_context(
        root,
        bundle,
        context_files=context_files,
        context_texts=context_texts,
    )
    manifest = _session_dir(root) / "session.json"
    write_json(
        manifest,
        {
            "session_version": "agent-subscription-bundle-session.v1",
            "execution_mode": EXECUTION_MODE,
            "generation_performed": False,
            "source_bundle": bundle.model_dump(mode="json"),
            "authoring_context": _context_ref(root, context),
            "status": "SOURCE_BUNDLE_READY",
            "human_review_required": True,
            "created_at": _now(),
        },
    )
    return {
        "run_dir": str(root),
        "source_count": len(bundle.sources),
        "source_bundle": str(root / SOURCE_BUNDLE_MANIFEST),
        "source_bundle_sha256": bundle.bundle_sha256,
        "ordered_sources": [entry.source.model_dump(mode="json") for entry in bundle.sources],
        "authoring_context": _context_ref(root, context),
        "generation_performed": False,
        "session_manifest": str(manifest),
    }


def agent_context(
    run_dir: Path,
    *,
    context_files: tuple[Path, ...] = (),
    context_texts: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Prepare context independently; existing PDF/extraction artifacts remain untouched."""

    root = run_dir.expanduser().resolve()
    if (root / SOURCE_BUNDLE_MANIFEST).is_file():
        bundle = load_source_bundle(root)
        context = prepare_bundle_authoring_context(
            root,
            bundle,
            context_files,
            context_texts,
        )
    else:
        source = _manifest_source(root)
        context = prepare_authoring_context(root, source, context_files, context_texts)
    return {
        "run_dir": str(root),
        "authoring_context": _context_ref(root, context),
        "extraction_modified": False,
        "generation_performed": False,
        "execution_mode": EXECUTION_MODE,
        "provider_api_calls": 0,
    }


def _write_task_package(
    stage: AgentStage,
    run_dir: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    # Import the frozen package rather than reconstructing configuration from repeated flags.
    payload = {
        **payload,
        "next_command": {
            "argv": [
                "learning-authoring",
                "agent-import",
                stage,
                str(run_dir),
                "<candidate-json>",
                "--task-package",
                "<task-package-json>",
            ]
        },
    }
    if payload.get("task_package_version") == "agent-task.v3":
        payload["prompt_lineage"] = _task_lineage(payload["prompt_lineage"])
        payload["prompt_delivery_sha256"] = _prompt_delivery_sha256(payload)
    fingerprint = _fingerprint(payload)
    path = _session_dir(run_dir) / "tasks" / f"{stage}-{fingerprint}.json"
    frozen_task = {**payload, "task_fingerprint": fingerprint}
    write_json(path, frozen_task)
    rendered_path = path.with_suffix(".md")
    write_text(rendered_path, _render_agent_task(frozen_task, path))
    return {
        "stage": stage,
        "status": "TASK_READY",
        "execution_mode": EXECUTION_MODE,
        "provider_api_calls": 0,
        "task_package": str(path),
        "task_package_sha256": sha256_file(path),
        "agent_readable_task": str(rendered_path),
        "agent_readable_task_sha256": sha256_file(rendered_path),
        "read_before_authoring": True,
        "task_fingerprint": fingerprint,
        "candidate_contract": payload["candidate_contract"]["title"],
        "next_command": {
            "argv": [
                str(path) if arg == "<task-package-json>" else arg
                for arg in payload["next_command"]["argv"]
            ]
        },
    }


def _load_task_package(
    stage: AgentStage,
    root: Path,
    path: Path,
    *,
    require_official_prompt: bool = True,
) -> dict[str, Any]:
    """Detect stale/cross-run packages; this is an integrity check, not approval."""

    resolved = path.expanduser().resolve()
    if resolved.parent != (_session_dir(root) / "tasks").resolve():
        raise ValueError("task package must belong to this run's agent-session/tasks directory")
    task = read_json(resolved)
    version = task.get("task_package_version")
    if (
        version not in {"agent-task.v2", "agent-task.v3"}
        or task.get("stage") != stage
        or task.get("run_dir") != str(root)
        or task.get("execution_mode") != EXECUTION_MODE
        or task.get("provider_api_calls") != 0
        or task.get("host_generation") != "coding_agent_subscription_session"
    ):
        raise ValueError("task package stage, run identity, or version does not match")
    fingerprint = task.get("task_fingerprint")
    original = {key: value for key, value in task.items() if key != "task_fingerprint"}
    if fingerprint != _fingerprint(original):
        raise ValueError("task package fingerprint does not match its frozen contents")
    if resolved.name != f"{stage}-{fingerprint}.json":
        raise ValueError("task package filename does not match its frozen fingerprint")
    if version == "agent-task.v3":
        if not isinstance(task.get("worked_examples"), list) or not isinstance(
            task.get("prompt_lineage"), dict
        ):
            raise ValueError("agent-task.v3 requires embedded worked examples and prompt lineage")
        if task.get("prompt_delivery_sha256") != _prompt_delivery_sha256(task):
            raise ValueError("task prompt delivery hash does not match embedded prompt material")
        if require_official_prompt:
            _validate_official_task_prompt_material(stage, root, task)
    return task


def _validate_official_task_prompt_material(
    stage: AgentStage,
    root: Path,
    task: dict[str, Any],
) -> None:
    """Bind a v3 task to this runtime's stage-owned prompt package and schema.

    A task fingerprint and prompt-delivery hash prove only that a file is
    self-consistent.  They do not prove that the embedded instructions or
    examples came from the installed authoring package.  Reconstructing the
    official material closes that self-rehash gap without trusting candidate
    output or a model-provider service.
    """

    boundary = task.get("input_boundary")
    if not isinstance(boundary, dict):
        raise ValueError("agent-task.v3 requires a frozen input boundary")

    if stage == "extraction":
        instructions, fields = _native_extraction_prompt_fields()
        expected_instructions = _extraction_instructions(instructions)
        schema = agent_schema("extraction")
    elif stage == "kc":
        bundle_mode = (root / SOURCE_BUNDLE_MANIFEST).is_file()
        schema = agent_schema("kc", source_bundle=bundle_mode)
        if bundle_mode:
            expected_instructions, fields = _bundle_kc_prompt_fields(schema)
        else:
            prompt = load_prompt_package()
            expected_instructions = prompt.instructions
            fields = _prompt_task_fields(prompt)
    elif stage == "quiz":
        try:
            runtime = boundary["payload"]["runtime"]
            schema_version = runtime["expected_schema_version"]
            source_ref = boundary["payload"]["source_ref"]
        except (KeyError, TypeError) as exc:
            raise ValueError("Quiz task is missing its frozen runtime prompt mode") from exc
        legacy = schema_version == "quiz-batch.v1"
        schema = agent_schema("quiz", legacy_quiz=legacy)
        prompt = load_quiz_prompt_package(
            schema_version=schema_version,
            examples_dir=(
                BUNDLE_QUIZ_EXAMPLES_DIR
                if source_ref.get("source_bundle_sha256") is not None
                else DEFAULT_QUIZ_EXAMPLES_DIR
            ),
        )
        expected_instructions = prompt.instructions
        fields = _prompt_task_fields(prompt)
    elif stage == "quiz-review":
        try:
            source_ref = boundary["expected_source_ref"]
        except KeyError as exc:
            raise ValueError("Quiz-review task is missing its frozen source mode") from exc
        schema = agent_schema("quiz-review")
        prompt = load_semantic_review_prompt_package(
            examples_dir=(
                BUNDLE_REVIEW_EXAMPLES_DIR
                if source_ref.get("source_bundle_sha256") is not None
                else DEFAULT_REVIEW_EXAMPLES_DIR
            )
        )
        expected_instructions = prompt.instructions
        fields = _prompt_task_fields(prompt)
    else:  # pragma: no cover - AgentStage and the public parser constrain calls
        raise ValueError(f"unsupported agent stage: {stage}")

    expected = {
        "instructions": expected_instructions,
        "worked_examples": fields["worked_examples"],
        "prompt_lineage": _task_lineage(fields["prompt_lineage"]),
        "candidate_contract": {
            "title": schema.get("title"),
            "schema": schema,
        },
        "output_policy": AGENT_OUTPUT_POLICY,
    }
    mismatches = [name for name, value in expected.items() if task.get(name) != value]
    if mismatches:
        raise ValueError(
            "task prompt material does not match the official runtime package: "
            + ", ".join(mismatches)
        )


def _kc_input_boundary(
    root: Path,
    extracted: ExtractedSource,
    upstream: dict[str, Any],
) -> dict[str, Any]:
    context = load_authoring_context(root, extracted.source)
    return {
        "delivery": (
            "complete_extracted_source_json_and_supplementary_context"
            if context
            else "complete_extracted_source_json"
        ),
        "upstream_extraction": upstream,
        "payload": extracted.model_dump(mode="json"),
        "authoring_context": context.model_dump(mode="json") if context else None,
        "context_base_dir": str(root) if context else None,
        "context_accountability": "claim_to_kc_audit_required" if context else None,
        "expected_source_ref": {
            "schema_version": extracted.schema_version,
            "source_id": extracted.source.source_id,
            "source_sha256": extracted.source.sha256,
            "authoring_context_sha256": context.sha256 if context else None,
        },
    }


def _bundle_kc_state(
    root: Path,
    *,
    allow_proposed_extraction_demo: bool,
) -> tuple[SourceBundle, dict[str, ExtractedSource], AuthoringContext | None, dict[str, Any]]:
    bundle = load_source_bundle(root)
    extractions = load_bundle_extractions(root, bundle)
    proposed = [
        entry.source.source_id for entry in bundle.sources if entry.extraction_status == "PROPOSED"
    ]
    if proposed and not allow_proposed_extraction_demo:
        raise RuntimeError(
            "bundle KC import requires approved Extractions; for a non-production review demo "
            "only, pass --allow-proposed-extraction-demo"
        )
    context = load_bundle_authoring_context(root, bundle)
    upstream = {
        "status": "PROPOSED_DEMO_ONLY" if proposed else "HUMAN_APPROVED",
        "demo_only": bool(proposed),
        "source_bundle_sha256": bundle.bundle_sha256,
        "sources": [
            {
                "source_id": entry.source.source_id,
                "status": entry.extraction_status,
                "path": str(root / entry.extraction_ref),
                "sha256": entry.extraction_sha256,
            }
            for entry in bundle.sources
        ],
    }
    return bundle, extractions, context, upstream


def _bundle_kc_input_boundary(
    root: Path,
    bundle: SourceBundle,
    extractions: dict[str, ExtractedSource],
    context: AuthoringContext | None,
    upstream: dict[str, Any],
) -> dict[str, Any]:
    return {
        "delivery": (
            "ordered_complete_extracted_sources_and_supplementary_context"
            if context
            else "ordered_complete_extracted_sources"
        ),
        "source_bundle": bundle.model_dump(mode="json"),
        "upstream_extraction": upstream,
        "payload": [
            extractions[entry.source.source_id].model_dump(mode="json") for entry in bundle.sources
        ],
        "authoring_context": context.model_dump(mode="json") if context else None,
        "context_base_dir": str(root) if context else None,
        "context_accountability": "claim_to_kc_audit_required" if context else None,
        "source_qualification_policy": {
            "every_pdf_page_or_block_reference_requires_source_id": True,
            "context_page_ordinals_may_not_be_projected_across_sources": True,
            "same_topic_claims_may_merge_only_with_source_qualified_evidence": True,
            "conflicting_source_claims_must_remain_explicit": True,
        },
        "expected_source_ref": {
            "schema_version": bundle.schema_version,
            "source_bundle_sha256": bundle.bundle_sha256,
            "authoring_context_sha256": context.sha256 if context else None,
        },
    }


QuizKCSet = ProposedKCSet | SourceBundleKCSet


def _load_quiz_kc_set(root: Path, raw_kc_set: dict[str, Any]) -> QuizKCSet:
    """Parse and verify the exact KC lineage used by an agent-native Quiz task.

    Bundle identity is detected from the artifact itself, then checked against the
    current bundle, every Extraction, and the separately bound authoring context.
    A bundle root may not silently downgrade to an ambiguous one-PDF KC contract.
    """

    source_ref = raw_kc_set.get("source_ref")
    bundle_artifact = isinstance(source_ref, dict) and (
        source_ref.get("schema_version") == "source-bundle.v1"
    )
    bundle_manifest = root / SOURCE_BUNDLE_MANIFEST
    if bundle_artifact:
        if not bundle_manifest.is_file():
            raise ValueError("source-qualified KC requires the current source bundle manifest")
        bundle = load_source_bundle(root)
        extractions = load_bundle_extractions(root, bundle)
        context = load_bundle_authoring_context(root, bundle)
        parsed = SourceBundleKCSet.model_validate(raw_kc_set)
        validated = validate_kc_set_against_bundle(
            parsed,
            bundle,
            extractions,
            authoring_context=context,
        )
        if not isinstance(validated, SourceBundleKCSet):
            raise ValueError("multi-source Quiz requires source-qualified KC lineage")
        return validated
    if bundle_manifest.exists() or bundle_manifest.is_symlink():
        raise ValueError("source bundle Quiz cannot use an unqualified one-PDF KC set")
    parsed = ProposedKCSet.model_validate(raw_kc_set)
    extracted, _ = _load_kc_source(root, allow_proposed_extraction_demo=True)
    context = load_authoring_context(root, extracted.source)
    parsed.validate_against_source(extracted, authoring_context=context)
    return parsed


def _semantic_input_boundary(
    root: Path,
    material: dict[str, Any],
    reviewer_mode: str,
    *,
    prepare_companion: bool,
) -> dict[str, Any]:
    """Keep the answer key out of the initial learner view, without claiming enforced blindness."""

    if reviewer_mode not in {"independent", "self_review"}:
        raise ValueError("reviewer mode must be independent or self_review")
    companion = material["answer_material"]
    companion_path = _session_dir(root) / "review-materials" / f"{material_digest(companion)}.json"
    if prepare_companion and not companion_path.is_file():
        write_json(companion_path, companion)
    if not companion_path.is_file() or read_json(companion_path) != companion:
        raise ValueError("review answer-material companion is missing or changed")
    return {
        "delivery": "independent_semantic_review_of_bound_quiz_and_relevant_sources",
        "reviewer_mode": reviewer_mode,
        "expected_source_ref": material["source_ref"],
        "bindings": material["bindings"],
        "learner_questions": material["learner_questions"],
        "assessment_slots": material["assessment_slots"],
        "kc": material["artifacts"]["kc"],
        "extraction": material["artifacts"]["extraction"],
        "context": material["artifacts"]["context"],
        "source_locators": material["source_locators"],
        "answer_material": {
            "path": str(companion_path),
            "sha256": sha256_file(companion_path),
            "read_after": "recording an independent answer from each learner-facing question",
        },
        "limits": {
            "independence_is_a_host_protocol_not_a_verified_identity": True,
            "scope_is_selected_quiz_and_its_cited_sources_not_whole_course_certification": True,
            "human_approval_created": False,
            "original_quiz_may_not_be_modified": True,
        },
    }


def prepare_agent_task(
    stage: AgentStage,
    run_dir: Path,
    *,
    allow_proposed_extraction_demo: bool = False,
    kc_path: Path | None = None,
    selected_kc_ids: tuple[str, ...] = (),
    include_all_kcs: bool = False,
    variants_per_kc: int | None = None,
    min_slots_per_kc: int = 1,
    max_slots_per_kc: int | None = None,
    variants_per_slot: int | None = None,
    max_variants_per_slot: int | None = None,
    total_question_budget: int | None = None,
    language: str = "source",
    reviewer_mode: Literal["independent", "self_review"] = "independent",
) -> dict[str, Any]:
    """Write a prompt/schema/input package a portable skill can hand to its host agent."""

    root = run_dir.expanduser().resolve()
    bundle_mode = stage == "kc" and (root / SOURCE_BUNDLE_MANIFEST).is_file()
    schema = agent_schema(
        stage,
        legacy_quiz=variants_per_kc is not None,
        source_bundle=bundle_mode,
    )
    common: dict[str, Any] = {
        "task_package_version": "agent-task.v3",
        "run_dir": str(root),
        "stage": stage,
        "execution_mode": EXECUTION_MODE,
        "provider_api_calls": 0,
        "host_generation": "coding_agent_subscription_session",
        "candidate_contract": {
            "title": schema.get("title"),
            "schema": schema,
        },
        "output_policy": dict(AGENT_OUTPUT_POLICY),
    }
    if stage == "quiz-review":
        material = quiz_review_material(root)
        prompt = load_semantic_review_prompt_package(
            examples_dir=(
                BUNDLE_REVIEW_EXAMPLES_DIR
                if material["source_ref"].get("source_bundle_sha256") is not None
                else DEFAULT_REVIEW_EXAMPLES_DIR
            )
        )
        return _write_task_package(
            stage,
            root,
            {
                **common,
                "instructions": prompt.instructions,
                **_prompt_task_fields(prompt),
                "input_boundary": _semantic_input_boundary(
                    root,
                    material,
                    reviewer_mode,
                    prepare_companion=True,
                ),
            },
        )
    if stage == "extraction":
        prompt_instructions, prompt_fields = _native_extraction_prompt_fields()
        source = _manifest_source(root)
        artifacts = RunArtifacts(root)
        manifest = read_json(artifacts.source_manifest)
        page_refs = [
            {
                "page": record["page"],
                "image_ref": record["image_ref"],
                "image_sha256": record["image_sha256"],
                "absolute_image_path": str(root / record["image_ref"]),
            }
            for record in manifest.get("page_records", [])
        ]
        task = {
            **common,
            "instructions": _extraction_instructions(prompt_instructions),
            **prompt_fields,
            "input_boundary": {
                "delivery": "native_pdf_primary",
                "source": source.model_dump(mode="json"),
                "expected_page_count": source.page_count,
                "source_pdf": str(artifacts.source_pdf),
                "source_pdf_sha256": sha256_file(artifacts.source_pdf),
                "page_images": page_refs,
                "page_image_policy": {
                    "role": "targeted_single_page_fallback_locator_only",
                    "bulk_load_forbidden": True,
                },
                "text_audit_role": "diagnostic_only_not_agent_input",
            },
        }
        return _write_task_package(stage, root, task)
    if stage == "kc":
        if bundle_mode:
            bundle, extractions, context, upstream = _bundle_kc_state(
                root,
                allow_proposed_extraction_demo=allow_proposed_extraction_demo,
            )
            boundary = _bundle_kc_input_boundary(
                root,
                bundle,
                extractions,
                context,
                upstream,
            )
            instructions, prompt_fields = _bundle_kc_prompt_fields(schema)
        else:
            prompt = load_prompt_package()
            extracted, upstream = _load_kc_source(
                root,
                allow_proposed_extraction_demo=allow_proposed_extraction_demo,
            )
            boundary = _kc_input_boundary(root, extracted, upstream)
            instructions = prompt.instructions
            prompt_fields = _prompt_task_fields(prompt)
        task = {
            **common,
            "instructions": instructions,
            **prompt_fields,
            "input_boundary": boundary,
        }
        return _write_task_package(stage, root, task)
    if stage == "quiz":
        resolved_kc = (kc_path or (root / "kc-proposed.json")).expanduser().resolve()
        if not resolved_kc.is_file():
            raise RuntimeError(f"KC set is missing: {resolved_kc}")
        raw_kc_set = read_json(resolved_kc)
        kc_set = _load_quiz_kc_set(root, raw_kc_set)
        config = QuizConfig(
            selected_kc_ids=selected_kc_ids,
            include_all_kcs=include_all_kcs,
            variants_per_kc=variants_per_kc,
            min_slots_per_kc=min_slots_per_kc,
            max_slots_per_kc=max_slots_per_kc,
            variants_per_slot=variants_per_slot,
            max_variants_per_slot=max_variants_per_slot,
            total_question_budget=total_question_budget,
            language=language,
        )
        quiz_input = build_quiz_input(
            kc_set,
            kc_set_sha256=sha256_file(resolved_kc),
            config=config,
            raw_kc_set=raw_kc_set,
        )
        prompt = load_quiz_prompt_package(
            schema_version=quiz_input["runtime"]["expected_schema_version"],
            examples_dir=(
                BUNDLE_QUIZ_EXAMPLES_DIR
                if isinstance(kc_set, SourceBundleKCSet)
                else DEFAULT_QUIZ_EXAMPLES_DIR
            ),
        )
        task = {
            **common,
            "instructions": prompt.instructions,
            **_prompt_task_fields(prompt),
            "input_boundary": {
                "delivery": "selected_leaf_kcs_groups_and_runtime_only",
                "kc_set": {"path": str(resolved_kc), "sha256": sha256_file(resolved_kc)},
                "payload": quiz_input,
            },
        }
        return _write_task_package(stage, root, task)
    raise ValueError(f"unsupported agent stage: {stage}")


def _manifest_source(run_dir: Path) -> SourceDescriptor:
    artifacts = RunArtifacts(run_dir)
    if not artifacts.source_manifest.is_file():
        raise RuntimeError(
            f"run has not been prepared with agent-init: {artifacts.source_manifest}"
        )
    return SourceDescriptor.model_validate(read_json(artifacts.source_manifest)["source"])


def _validate_source_binding(extracted: ExtractedSource, source: SourceDescriptor) -> None:
    if extracted.source != source:
        raise RuntimeError("canonical extraction source does not match code-owned source manifest")


def _contract_error(path: Path, exc: Exception, raw_path: Path) -> None:
    errors = (
        exc.errors(include_url=False, include_context=False)
        if isinstance(exc, ValidationError)
        else None
    )
    write_json(
        path,
        {
            "contract_valid": False,
            "execution_mode": EXECUTION_MODE,
            "provider_api_calls": 0,
            "candidate_raw_path": str(raw_path),
            "error_type": type(exc).__name__,
            "message": str(exc),
            "errors": errors,
            "created_at": _now(),
        },
    )


def _guard_extraction_approval(artifacts: RunArtifacts) -> None:
    if artifacts.approved.exists() or artifacts.approval.exists():
        raise FileExistsError(
            "agent extraction import will not replace a run containing approval artifacts"
        )


def import_extraction(
    run_dir: Path,
    candidate_path: Path,
    *,
    task_package: Path | None = None,
) -> dict[str, Any]:
    """Validate an extraction payload and bind the source descriptor owned by code."""

    started = time.perf_counter()
    root = run_dir.expanduser().resolve()
    artifacts = RunArtifacts(root)
    raw, raw_sha256, raw_path = _preserve_candidate("extraction", root, candidate_path)
    task: dict[str, Any] | None = None
    attempt: dict[str, Any] | None = None
    try:
        task = _load_required_import_task("extraction", root, task_package)
        attempt = _start_candidate_attempt("extraction", root, raw_sha256, task)
        source = _manifest_source(root)
        frozen = task["input_boundary"]
        if frozen["source"] != source.model_dump(mode="json") or frozen[
            "source_pdf_sha256"
        ] != sha256_file(artifacts.source_pdf):
            raise ValueError("PDF changed after the frozen Extraction task")
        payload = ExtractedSourcePayload.model_validate_json(raw)
        extracted = payload.with_source(source)
        validate_extraction_geometry(extracted)
        _guard_extraction_approval(artifacts)
        audit = build_audit(extracted, root)
    except (KeyError, OSError, ValidationError, ValueError, RuntimeError) as exc:
        _contract_error(artifacts.contract_errors, exc, raw_path)
        _write_import_record(
            stage="extraction",
            run_dir=root,
            raw_path=raw_path,
            raw_sha256=raw_sha256,
            status=_import_error_status(exc),
            error=exc,
            task_fingerprint=task["task_fingerprint"] if task else None,
            task=task,
            attempt=_error_attempt(exc, attempt),
            fresh_retry_authorized=_contract_failure_retry_authorized(task, attempt, exc),
            fresh_retry_reason=_contract_failure_retry_reason(task, attempt, exc),
        )
        raise

    fingerprint = _fingerprint(
        {
            "stage": "extraction",
            "candidate_raw_sha256": raw_sha256,
            "source_sha256": source.sha256,
            "task_fingerprint": task["task_fingerprint"] if task else None,
            "execution_mode": EXECUTION_MODE,
        }
    )
    guidance = audit["fresh_candidate_guidance"]
    promoted = not guidance["recommended"]
    attempt_number = int(attempt["candidate_attempt_number"]) if attempt else 1
    status = "PROPOSED" if promoted else ("RETRY_REQUIRED" if attempt_number == 1 else "REVIEW")
    metadata = {
        "stage": "extract",
        "stage_version": IMPORT_VERSION,
        "execution_mode": EXECUTION_MODE,
        "provider_api_calls": 0,
        "generation_performed_by_importer": False,
        "candidate_raw_path": str(raw_path),
        "candidate_raw_sha256": raw_sha256,
        "candidate_bytes_preserved_exactly": True,
        "request_fingerprint": fingerprint,
        "task_fingerprint": task["task_fingerprint"] if task else None,
        **_task_audit_fields(task),
        "source": source.model_dump(mode="json"),
        "output_schema_version": extracted.schema_version,
        "human_review_required": True,
        "approval_status": status,
        "promotion_gate_passed": promoted,
        "promotion_trigger_codes": guidance["trigger_codes"],
        "created_at": _now(),
    }
    metrics = {
        "metrics_version": "agent-extraction-run-metrics.v1",
        "stage_version": IMPORT_VERSION,
        "request_fingerprint": fingerprint,
        "contract_valid": True,
        "schema_version": extracted.schema_version,
        "source_page_count": source.page_count,
        "extracted_page_count": len(extracted.pages),
        "page_note_count": len(extracted.pages),
        "raw_candidate_sha256": raw_sha256,
        "raw_candidate_bytes_preserved": True,
        "model_elapsed_seconds": None,
        "total_elapsed_seconds": round(time.perf_counter() - started, 6),
        "reconstruction_ready": audit["reconstruction_ready"],
        "missing_geometry_block_count": audit["missing_geometry_block_count"],
        "promotion_gate_passed": promoted,
        "promotion_trigger_codes": guidance["trigger_codes"],
        "completed_at": _now(),
        **_unavailable_metrics(),
    }
    write_json(artifacts.metadata, metadata)
    if promoted:
        write_json(artifacts.proposed, extracted.model_dump(mode="json"))
    elif attempt_number >= 2:
        write_bytes(root / "extracted-source.review.json", raw)
    write_json(artifacts.audit, audit)
    write_json(artifacts.metrics, metrics)
    artifacts.contract_errors.unlink(missing_ok=True)
    review_path = build_review(root) if promoted else None
    import_record = _write_import_record(
        stage="extraction",
        run_dir=root,
        raw_path=raw_path,
        raw_sha256=raw_sha256,
        status=status,
        canonical_path=artifacts.proposed if promoted else None,
        task_fingerprint=task["task_fingerprint"],
        task=task,
        attempt=attempt,
        fresh_retry_authorized=bool(not promoted and attempt_number == 1),
        fresh_retry_reason=(
            "deterministic_extraction_promotion_gate"
            if not promoted and attempt_number == 1
            else None
        ),
        canonical_write_performed=promoted,
    )
    return {
        "stage": "extraction",
        "status": status,
        "proposed": str(artifacts.proposed) if promoted else None,
        "review_candidate": (
            str(root / "extracted-source.review.json")
            if not promoted and attempt_number >= 2
            else None
        ),
        "review": str(review_path) if review_path else None,
        "raw_candidate": str(raw_path),
        "raw_candidate_sha256": raw_sha256,
        "import_record": str(import_record),
        "promotion_gate_passed": promoted,
        "promotion_trigger_codes": guidance["trigger_codes"],
        "next_quality_action": guidance["next_action"],
        **_unavailable_metrics(),
    }


def _load_kc_source(
    run_dir: Path,
    *,
    allow_proposed_extraction_demo: bool,
) -> tuple[ExtractedSource, dict[str, Any]]:
    artifacts = RunArtifacts(run_dir)
    approval_exists = artifacts.approval.is_file()
    approved_exists = artifacts.approved.is_file()
    if approval_exists or approved_exists:
        if not (approval_exists and approved_exists):
            raise RuntimeError("extraction approval boundary is incomplete")
        approved, _, approved_sha256 = load_approved_extraction(run_dir)
        _validate_source_binding(approved, _manifest_source(run_dir))
        return approved, {
            "status": "HUMAN_APPROVED",
            "demo_only": False,
            "path": str(artifacts.approved),
            "sha256": approved_sha256,
        }
    if not allow_proposed_extraction_demo:
        raise RuntimeError(
            "KC import requires approved extraction; for a non-production review demo only, "
            "pass --allow-proposed-extraction-demo"
        )
    if not artifacts.proposed.is_file():
        raise RuntimeError(f"proposed extraction is missing: {artifacts.proposed}")
    if artifacts.metadata.is_file():
        metadata = read_json(artifacts.metadata)
        if metadata.get("promotion_gate_passed") is False:
            raise RuntimeError("proposed extraction is blocked by its deterministic promotion gate")
    proposed = ExtractedSource.model_validate(read_json(artifacts.proposed))
    _validate_source_binding(proposed, _manifest_source(run_dir))
    return proposed, {
        "status": "PROPOSED_DEMO_ONLY",
        "demo_only": True,
        "path": str(artifacts.proposed),
        "sha256": sha256_file(artifacts.proposed),
    }


def import_kc(
    run_dir: Path,
    candidate_path: Path,
    *,
    allow_proposed_extraction_demo: bool = False,
    task_package: Path | None = None,
) -> dict[str, Any]:
    """Validate proposed KCs against an approved or explicitly demo-only extraction."""

    started = time.perf_counter()
    root = run_dir.expanduser().resolve()
    artifacts = RunArtifacts(root)
    raw, raw_sha256, raw_path = _preserve_candidate("kc", root, candidate_path)
    task: dict[str, Any] | None = None
    attempt: dict[str, Any] | None = None
    bundle_mode = (root / SOURCE_BUNDLE_MANIFEST).is_file()
    try:
        task = _load_required_import_task("kc", root, task_package)
        attempt = _start_candidate_attempt("kc", root, raw_sha256, task)
        allow_proposed_extraction_demo = task["input_boundary"]["upstream_extraction"]["demo_only"]
        if bundle_mode:
            bundle, extractions, context, upstream = _bundle_kc_state(
                root,
                allow_proposed_extraction_demo=allow_proposed_extraction_demo,
            )
            boundary = _bundle_kc_input_boundary(
                root,
                bundle,
                extractions,
                context,
                upstream,
            )
            if task["input_boundary"] != boundary:
                raise ValueError(
                    "source bundle, Extraction, or context changed after the frozen KC task"
                )
            proposed = SourceBundleKCSet.model_validate_json(raw)
            validated = validate_kc_set_against_bundle(
                proposed,
                bundle,
                extractions,
                authoring_context=context,
            )
            if not isinstance(validated, SourceBundleKCSet):
                raise ValueError("multi-source bundle requires source-qualified KC output")
            source_page_count = sum(entry.source.page_count for entry in bundle.sources)
        else:
            extracted, upstream = _load_kc_source(
                root,
                allow_proposed_extraction_demo=allow_proposed_extraction_demo,
            )
            context = load_authoring_context(root, extracted.source)
            if task["input_boundary"] != _kc_input_boundary(
                root,
                extracted,
                upstream,
            ):
                raise ValueError("Extraction or authoring context changed after the frozen KC task")
            proposed = ProposedKCSet.model_validate_json(raw)
            proposed.validate_against_source(
                extracted,
                authoring_context=context,
                require_context_audit=context is not None,
            )
            source_page_count = extracted.source.page_count
    except (KeyError, OSError, ValidationError, ValueError, RuntimeError) as exc:
        _contract_error(artifacts.kc_contract_errors, exc, raw_path)
        _write_import_record(
            stage="kc",
            run_dir=root,
            raw_path=raw_path,
            raw_sha256=raw_sha256,
            status=_import_error_status(exc),
            error=exc,
            task_fingerprint=task["task_fingerprint"] if task else None,
            task=task,
            attempt=_error_attempt(exc, attempt),
            fresh_retry_authorized=_contract_failure_retry_authorized(task, attempt, exc),
            fresh_retry_reason=_contract_failure_retry_reason(task, attempt, exc),
        )
        raise

    fingerprint = _fingerprint(
        {
            "stage": "kc",
            "candidate_raw_sha256": raw_sha256,
            "upstream_extraction": upstream,
            "authoring_context_sha256": context.sha256 if context else None,
            "task_fingerprint": task["task_fingerprint"] if task else None,
            "execution_mode": EXECUTION_MODE,
        }
    )
    metadata = {
        "stage": "kc",
        "stage_version": IMPORT_VERSION,
        "request_fingerprint": fingerprint,
        "task_fingerprint": task["task_fingerprint"] if task else None,
        **_task_audit_fields(task),
        "execution_mode": EXECUTION_MODE,
        "provider_api_calls": 0,
        "generation_performed_by_importer": False,
        "model": "coding-agent subscription session",
        "candidate_raw_path": str(raw_path),
        "candidate_raw_sha256": raw_sha256,
        "candidate_bytes_preserved_exactly": True,
        "upstream_extraction": upstream,
        "authoring_context": _context_ref(root, context),
        "human_review_required": True,
        "approval_status": "PROPOSED",
        "created_at": _now(),
    }
    metrics = {
        "metrics_version": "agent-kc-run-metrics.v1",
        "stage_version": IMPORT_VERSION,
        "request_fingerprint": fingerprint,
        "contract_valid": True,
        "source_page_count": source_page_count,
        "page_audit_count": len(proposed.page_audit),
        "leaf_kc_count": len(proposed.leaf_kcs),
        "kc_group_count": len(proposed.kc_groups),
        "granularity_diagnostics": kc_review_diagnostics(proposed),
        "upstream_extraction_status": upstream["status"],
        "raw_candidate_sha256": raw_sha256,
        "raw_candidate_bytes_preserved": True,
        "model_elapsed_seconds": None,
        "total_elapsed_seconds": round(time.perf_counter() - started, 6),
        "completed_at": _now(),
        **_unavailable_metrics(),
    }
    write_json(artifacts.kc_metadata, metadata)
    write_bytes(artifacts.kc_proposed, raw)
    write_json(artifacts.kc_metrics, metrics)
    artifacts.kc_contract_errors.unlink(missing_ok=True)
    review = (
        None
        if bundle_mode
        else build_kc_demo(
            root,
            [root],
            allow_proposed_extraction_demo=upstream["demo_only"],
        )
    )
    import_record = _write_import_record(
        stage="kc",
        run_dir=root,
        raw_path=raw_path,
        raw_sha256=raw_sha256,
        status="PROPOSED",
        canonical_path=artifacts.kc_proposed,
        task_fingerprint=task["task_fingerprint"],
        task=task,
        attempt=attempt,
        canonical_write_performed=True,
    )
    return {
        "stage": "kc",
        "status": "PROPOSED",
        "upstream_extraction_status": upstream["status"],
        "demo_only": upstream["demo_only"],
        "proposed": str(artifacts.kc_proposed),
        "review": review,
        "raw_candidate": str(raw_path),
        "raw_candidate_sha256": raw_sha256,
        "import_record": str(import_record),
        **_unavailable_metrics(),
    }


def import_quiz(
    run_dir: Path,
    candidate_path: Path,
    *,
    kc_path: Path | None = None,
    selected_kc_ids: tuple[str, ...] = (),
    include_all_kcs: bool = False,
    variants_per_kc: int | None = None,
    min_slots_per_kc: int = 1,
    max_slots_per_kc: int | None = None,
    variants_per_slot: int | None = None,
    max_variants_per_slot: int | None = None,
    total_question_budget: int | None = None,
    language: str = "source",
    task_package: Path | None = None,
) -> dict[str, Any]:
    """Freeze KC/runtime input and validate one experimental Quiz candidate against it."""

    started = time.perf_counter()
    root = run_dir.expanduser().resolve()
    destination = root / "quiz"
    artifacts = RunArtifacts(destination)
    raw, raw_sha256, raw_path = _preserve_candidate("quiz", root, candidate_path)
    resolved_kc = (kc_path or (root / "kc-proposed.json")).expanduser().resolve()
    task: dict[str, Any] | None = None
    attempt: dict[str, Any] | None = None
    try:
        task = _load_required_import_task("quiz", root, task_package)
        attempt = _start_candidate_attempt("quiz", root, raw_sha256, task)
        boundary = task["input_boundary"]
        resolved_kc = Path(boundary["kc_set"]["path"]).expanduser().resolve()
        runtime = boundary["payload"]["runtime"]
        config = QuizConfig(
            selected_kc_ids=tuple(runtime["selected_kc_ids"]),
            variants_per_kc=runtime["variants_per_kc"],
            min_slots_per_kc=runtime["min_slots_per_kc"],
            max_slots_per_kc=runtime["max_slots_per_kc"],
            variants_per_slot=runtime["variants_per_slot"],
            max_variants_per_slot=runtime["max_variants_per_slot"],
            total_question_budget=runtime["total_question_budget"],
            allowed_interactions=tuple(runtime["allowed_interactions"]),
            language=runtime["language"],
        )
        if not resolved_kc.is_file():
            raise RuntimeError(f"KC set is missing: {resolved_kc}")
        raw_kc_set = read_json(resolved_kc)
        kc_set = _load_quiz_kc_set(root, raw_kc_set)
        kc_sha256 = sha256_file(resolved_kc)
        quiz_input = build_quiz_input(
            kc_set,
            kc_set_sha256=kc_sha256,
            config=config,
            raw_kc_set=raw_kc_set,
        )
        if (
            task["input_boundary"]["kc_set"]["sha256"] != kc_sha256
            or task["input_boundary"]["payload"] != quiz_input
        ):
            raise ValueError("KC or runtime differs from the frozen Quiz task")
        proposed = (
            QuizBatchV3.model_validate_json(raw, strict=True)
            if config.variants_per_kc is None
            else QuizBatch.model_validate_json(raw)
        )
        proposed.validate_against_input(quiz_input)
    except (KeyError, OSError, ValidationError, ValueError, RuntimeError) as exc:
        _contract_error(artifacts.quiz_contract_errors, exc, raw_path)
        _write_import_record(
            stage="quiz",
            run_dir=root,
            raw_path=raw_path,
            raw_sha256=raw_sha256,
            status=_import_error_status(exc),
            error=exc,
            task_fingerprint=task["task_fingerprint"] if task else None,
            task=task,
            attempt=_error_attempt(exc, attempt),
            fresh_retry_authorized=_contract_failure_retry_authorized(task, attempt, exc),
            fresh_retry_reason=_contract_failure_retry_reason(task, attempt, exc),
        )
        raise

    fingerprint = _fingerprint(
        {
            "stage": "quiz",
            "candidate_raw_sha256": raw_sha256,
            "kc_set_sha256": kc_sha256,
            "runtime": quiz_input["runtime"],
            "task_fingerprint": task["task_fingerprint"] if task else None,
            "execution_mode": EXECUTION_MODE,
        }
    )
    kc_metadata_path = resolved_kc.parent / "kc-generation-metadata.json"
    kc_metadata = read_json(kc_metadata_path) if kc_metadata_path.is_file() else {}
    metadata = {
        "stage": "quiz",
        "stage_version": IMPORT_VERSION,
        "quality_status": "experimental_unapproved",
        "request_fingerprint": fingerprint,
        "task_fingerprint": task["task_fingerprint"] if task else None,
        **_task_audit_fields(task),
        "execution_mode": EXECUTION_MODE,
        "provider_api_calls": 0,
        "generation_performed_by_importer": False,
        "model": "coding-agent subscription session",
        "candidate_raw_path": str(raw_path),
        "candidate_raw_sha256": raw_sha256,
        "candidate_bytes_preserved_exactly": True,
        "kc_set": {"path": str(resolved_kc), "sha256": kc_sha256},
        "upstream_extraction_status": (
            (kc_metadata.get("upstream_extraction") or {}).get("status")
        ),
        "selected_kc_ids": quiz_input["runtime"]["selected_kc_ids"],
        "variants_per_kc": config.variants_per_kc,
        "assessment_policy": quiz_input["runtime"],
        "assessment_slot_count": len(proposed.assessment_slots),
        "human_review_required": True,
        "approval_status": "EXPERIMENTAL_UNAPPROVED",
        "created_at": _now(),
    }
    metrics = {
        "metrics_version": "agent-quiz-run-metrics.v1",
        "stage_version": IMPORT_VERSION,
        "quality_status": "experimental_unapproved",
        "request_fingerprint": fingerprint,
        "contract_valid": True,
        "raw_output_unedited": True,
        "repair_calls": 0,
        "selected_kc_count": len(quiz_input["runtime"]["selected_kc_ids"]),
        "question_count": len(proposed.questions),
        "assessment_slot_count": len(proposed.assessment_slots),
        "question_counts_by_kc": {
            kc_id: sum(question.kc_id == kc_id for question in proposed.questions)
            for kc_id in quiz_input["runtime"]["selected_kc_ids"]
        },
        "interaction_counts": {
            interaction: sum(question.interaction == interaction for question in proposed.questions)
            for interaction in config.allowed_interactions
        },
        "raw_candidate_sha256": raw_sha256,
        "raw_candidate_bytes_preserved": True,
        "model_elapsed_seconds": None,
        "local_import_and_validation_seconds": round(time.perf_counter() - started, 6),
        "completed_at": _now(),
        **_unavailable_metrics(),
    }
    form_audit = build_quiz_form_audit(proposed)
    fresh_candidate_guidance = form_audit["fresh_candidate_guidance"]
    metadata["quality_revision_recommended"] = fresh_candidate_guidance["recommended"]
    metadata["quality_revision_trigger_codes"] = fresh_candidate_guidance["trigger_codes"]
    metrics["quality_revision_recommended"] = fresh_candidate_guidance["recommended"]
    metrics["quality_revision_trigger_codes"] = fresh_candidate_guidance["trigger_codes"]
    promoted = not fresh_candidate_guidance["recommended"]
    attempt_number = int(attempt["candidate_attempt_number"]) if attempt else 1
    status = (
        "EXPERIMENTAL_UNAPPROVED"
        if promoted
        else ("RETRY_REQUIRED" if attempt_number == 1 else "REVIEW")
    )
    metadata["approval_status"] = status
    metadata["promotion_gate_passed"] = promoted
    metrics["promotion_gate_passed"] = promoted
    write_json(artifacts.quiz_input, quiz_input)
    write_json(artifacts.quiz_metadata, metadata)
    write_bytes(artifacts.quiz_raw_output, raw)
    if promoted:
        write_bytes(artifacts.quiz_proposed, raw)
    elif attempt_number >= 2:
        write_bytes(destination / "quiz-review-required.json", raw)
    write_json(artifacts.quiz_form_audit, form_audit)
    write_json(artifacts.quiz_metrics, metrics)
    artifacts.quiz_contract_errors.unlink(missing_ok=True)
    review_path = build_quiz_review(root, candidate_dir=destination) if promoted else None
    import_record = _write_import_record(
        stage="quiz",
        run_dir=root,
        raw_path=raw_path,
        raw_sha256=raw_sha256,
        status=status,
        canonical_path=artifacts.quiz_proposed if promoted else None,
        task_fingerprint=task["task_fingerprint"],
        task=task,
        attempt=attempt,
        fresh_retry_authorized=bool(
            fresh_candidate_guidance["recommended"]
            and attempt
            and attempt["candidate_attempt_number"] == 1
        ),
        fresh_retry_reason=(
            "deterministic_quiz_form_guidance"
            if fresh_candidate_guidance["recommended"]
            and attempt
            and attempt["candidate_attempt_number"] == 1
            else None
        ),
        canonical_write_performed=promoted,
    )
    return {
        "stage": "quiz",
        "status": status,
        "proposed": str(artifacts.quiz_proposed) if promoted else None,
        "review_candidate": (
            str(destination / "quiz-review-required.json")
            if not promoted and attempt_number >= 2
            else None
        ),
        "review": str(review_path) if review_path else None,
        "raw_candidate": str(raw_path),
        "raw_candidate_sha256": raw_sha256,
        "import_record": str(import_record),
        "form_audit": str(artifacts.quiz_form_audit),
        "quality_revision_recommended": fresh_candidate_guidance["recommended"],
        "quality_revision_trigger_codes": fresh_candidate_guidance["trigger_codes"],
        "next_quality_action": fresh_candidate_guidance["next_action"],
        "promotion_gate_passed": promoted,
        **_unavailable_metrics(),
    }


def import_quiz_semantic(
    run_dir: Path,
    candidate_path: Path,
    *,
    task_package: Path | None,
) -> dict[str, Any]:
    """Record an independent initial check without editing or approving any teaching output."""

    root = run_dir.expanduser().resolve()
    destination = root / "quiz"
    raw, raw_sha256, raw_path = _preserve_candidate("quiz-review", root, candidate_path)
    task: dict[str, Any] | None = None
    attempt: dict[str, Any] | None = None
    error_path = destination / "quiz-semantic-contract-errors.json"
    try:
        task = _load_required_import_task("quiz-review", root, task_package)
        attempt = _start_candidate_attempt("quiz-review", root, raw_sha256, task)
        material = quiz_review_material(root)
        reviewer_mode = task["input_boundary"]["reviewer_mode"]
        current_boundary = _semantic_input_boundary(
            root,
            material,
            reviewer_mode,
            prepare_companion=False,
        )
        if task["input_boundary"] != current_boundary:
            raise ValueError("Quiz, KC, source, context, or reviewer protocol changed after review")
        quiz = material["artifacts"]["quiz"]
        report = QuizSemanticAudit.model_validate_json(raw, strict=True)
        validate_semantic_audit(
            report,
            quiz=quiz,
            expected_source_ref=material["source_ref"],
            artifacts=material["artifacts"],
            expected_reviewer=reviewer_mode,
        )
        summary = semantic_audit_summary(
            report,
            quiz=quiz,
            expected_source_ref=material["source_ref"],
        )
    except (KeyError, OSError, ValidationError, ValueError, RuntimeError) as exc:
        _contract_error(error_path, exc, raw_path)
        _write_import_record(
            stage="quiz-review",
            run_dir=root,
            raw_path=raw_path,
            raw_sha256=raw_sha256,
            status=_import_error_status(exc),
            error=exc,
            task_fingerprint=task["task_fingerprint"] if task else None,
            task=task,
            attempt=_error_attempt(exc, attempt),
            fresh_retry_authorized=_contract_failure_retry_authorized(task, attempt, exc),
            fresh_retry_reason=_contract_failure_retry_reason(task, attempt, exc),
        )
        raise

    report_path = destination / AUDIT_FILENAME
    write_bytes(report_path, raw)
    write_json(
        destination / AUDIT_METADATA_FILENAME,
        {
            "stage": "quiz-review",
            "stage_version": "agent-quiz-semantic-review.v1",
            "execution_mode": EXECUTION_MODE,
            "provider_api_calls": 0,
            "candidate_raw_path": str(raw_path),
            "candidate_raw_sha256": raw_sha256,
            "candidate_bytes_preserved_exactly": True,
            "task_fingerprint": task["task_fingerprint"],
            **_task_audit_fields(task),
            "source_ref": material["source_ref"],
            "reviewer_mode": reviewer_mode,
            "reviewer_identity_is_self_reported": True,
            "initial_check_status": summary["status"],
            "original_quiz_modified": False,
            "approval_status": "EXPERIMENTAL_UNAPPROVED",
            "human_review_required": True,
            "created_at": _now(),
        },
    )
    error_path.unlink(missing_ok=True)
    review_path = build_quiz_review(root, candidate_dir=destination)
    import_record = _write_import_record(
        stage="quiz-review",
        run_dir=root,
        raw_path=raw_path,
        raw_sha256=raw_sha256,
        status=summary["status"],
        canonical_path=report_path,
        task_fingerprint=task["task_fingerprint"],
        task=task,
        attempt=attempt,
        canonical_write_performed=True,
    )
    return {
        "stage": "quiz-review",
        "status": summary["status"],
        "summary": summary,
        "report": str(report_path),
        "review": str(review_path),
        "raw_candidate": str(raw_path),
        "raw_candidate_sha256": raw_sha256,
        "original_quiz_modified": False,
        "import_record": str(import_record),
        "approval_status": "EXPERIMENTAL_UNAPPROVED",
        **_unavailable_metrics(),
    }


def agent_import(
    stage: AgentStage,
    run_dir: Path,
    candidate_path: Path,
    *,
    allow_proposed_extraction_demo: bool = False,
    kc_path: Path | None = None,
    selected_kc_ids: tuple[str, ...] = (),
    include_all_kcs: bool = False,
    variants_per_kc: int | None = None,
    min_slots_per_kc: int = 1,
    max_slots_per_kc: int | None = None,
    variants_per_slot: int | None = None,
    max_variants_per_slot: int | None = None,
    total_question_budget: int | None = None,
    language: str = "source",
    task_package: Path | None = None,
) -> dict[str, Any]:
    """Dispatch one bounded agent-session candidate import."""

    if stage == "extraction":
        return import_extraction(run_dir, candidate_path, task_package=task_package)
    if stage == "kc":
        return import_kc(
            run_dir,
            candidate_path,
            allow_proposed_extraction_demo=allow_proposed_extraction_demo,
            task_package=task_package,
        )
    if stage == "quiz":
        return import_quiz(
            run_dir,
            candidate_path,
            kc_path=kc_path,
            selected_kc_ids=selected_kc_ids,
            include_all_kcs=include_all_kcs,
            variants_per_kc=variants_per_kc,
            min_slots_per_kc=min_slots_per_kc,
            max_slots_per_kc=max_slots_per_kc,
            variants_per_slot=variants_per_slot,
            max_variants_per_slot=max_variants_per_slot,
            total_question_budget=total_question_budget,
            language=language,
            task_package=task_package,
        )
    if stage == "quiz-review":
        return import_quiz_semantic(run_dir, candidate_path, task_package=task_package)
    raise ValueError(f"unsupported agent stage: {stage}")
