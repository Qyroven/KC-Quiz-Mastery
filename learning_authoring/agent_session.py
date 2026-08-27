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
)
from learning_authoring.audit import build_audit
from learning_authoring.authoring_context import (
    AuthoringContext,
    load_authoring_context,
    prepare_authoring_context,
)
from learning_authoring.contracts import ExtractedSource, ExtractedSourcePayload, SourceDescriptor
from learning_authoring.kc import load_approved_extraction, load_prompt_package
from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.kc_review import build_kc_demo
from learning_authoring.quiz import QuizConfig, build_quiz_input, load_quiz_prompt_package
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
    QuizSemanticAudit,
    load_semantic_review_prompt,
    semantic_audit_summary,
    semantic_review_schema,
    validate_semantic_audit,
)
from learning_authoring.review import build_review
from learning_authoring.source import DEFAULT_RENDER_DPI, prepare_or_reuse_source

AgentStage = Literal["extraction", "kc", "quiz", "quiz-review"]
EXECUTION_MODE = "agent_subscription_session"
IMPORT_VERSION = "agent-session-import.v1"
PACKAGE_DIR = Path(__file__).resolve().parent


def agent_schema(stage: AgentStage, *, legacy_quiz: bool = False) -> dict[str, Any]:
    """Return the exact candidate contract for one agent-native stage."""

    if stage == "quiz":
        return quiz_output_schema(
            "quiz-batch.v1" if legacy_quiz else CURRENT_QUIZ_SCHEMA_VERSION, strict_output=False,
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
) -> Path:
    record_path = _session_dir(run_dir) / "imports" / f"{stage}-{raw_sha256}.json"
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
            "canonical_proposed_path": str(canonical_path) if canonical_path else None,
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


def agent_context(
    run_dir: Path,
    *,
    context_files: tuple[Path, ...] = (),
    context_texts: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Prepare context independently; existing PDF/extraction artifacts remain untouched."""

    root = run_dir.expanduser().resolve()
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
                "learning-authoring", "agent-import", stage, str(run_dir),
                "<candidate-json>", "--task-package", "<task-package-json>",
            ]
        },
    }
    fingerprint = _fingerprint(payload)
    path = _session_dir(run_dir) / "tasks" / f"{stage}-{fingerprint}.json"
    write_json(path, {**payload, "task_fingerprint": fingerprint})
    return {
        "stage": stage,
        "status": "TASK_READY",
        "execution_mode": EXECUTION_MODE,
        "provider_api_calls": 0,
        "task_package": str(path),
        "task_package_sha256": sha256_file(path),
        "task_fingerprint": fingerprint,
        "candidate_contract": payload["candidate_contract"]["title"],
        "next_command": {
            "argv": [
                str(path) if arg == "<task-package-json>" else arg
                for arg in payload["next_command"]["argv"]
            ]
        },
    }


def _load_task_package(stage: AgentStage, root: Path, path: Path) -> dict[str, Any]:
    """Detect stale/cross-run packages; this is an integrity check, not approval."""

    resolved = path.expanduser().resolve()
    if resolved.parent != (_session_dir(root) / "tasks").resolve():
        raise ValueError("task package must belong to this run's agent-session/tasks directory")
    task = read_json(resolved)
    if (
        task.get("task_package_version") != "agent-task.v2"
        or task.get("stage") != stage
        or task.get("run_dir") != str(root)
        or task.get("execution_mode") != EXECUTION_MODE
    ):
        raise ValueError("task package stage, run identity, or version does not match")
    fingerprint = task.get("task_fingerprint")
    original = {key: value for key, value in task.items() if key != "task_fingerprint"}
    if fingerprint != _fingerprint(original):
        raise ValueError("task package fingerprint does not match its frozen contents")
    if resolved.name != f"{stage}-{fingerprint}.json":
        raise ValueError("task package filename does not match its frozen fingerprint")
    legacy_quiz = (
        stage == "quiz"
        and task["input_boundary"]["payload"]["runtime"].get("variants_per_kc") is not None
    )
    if task["candidate_contract"]["schema"] != agent_schema(stage, legacy_quiz=legacy_quiz):
        raise ValueError("runtime contract changed after this task; prepare a fresh agent-task")
    return task


def _kc_input_boundary(
    root: Path, extracted: ExtractedSource, upstream: dict[str, Any],
) -> dict[str, Any]:
    context = load_authoring_context(root, extracted.source)
    return {
        "delivery": (
            "complete_extracted_source_json_and_supplementary_context"
            if context else "complete_extracted_source_json"
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


def _validate_quiz_lineage(root: Path, kc_set: ProposedKCSet) -> None:
    extracted, _ = _load_kc_source(root, allow_proposed_extraction_demo=True)
    context = load_authoring_context(root, extracted.source)
    kc_set.validate_against_source(extracted, authoring_context=context)


def _semantic_input_boundary(
    root: Path, material: dict[str, Any], reviewer_mode: str, *, prepare_companion: bool,
) -> dict[str, Any]:
    """Keep the answer key out of the initial learner view, without claiming enforced blindness."""

    if reviewer_mode not in {"independent", "self_review"}:
        raise ValueError("reviewer mode must be independent or self_review")
    companion = material["answer_material"]
    companion_path = (
        _session_dir(root) / "review-materials" / f"{material_digest(companion)}.json"
    )
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
    schema = agent_schema(stage, legacy_quiz=variants_per_kc is not None)
    common: dict[str, Any] = {
        "task_package_version": "agent-task.v2",
        "run_dir": str(root),
        "stage": stage,
        "execution_mode": EXECUTION_MODE,
        "provider_api_calls": 0,
        "host_generation": "coding_agent_subscription_session",
        "candidate_contract": {
            "title": schema.get("title"),
            "schema": schema,
        },
        "output_policy": {
            "format": "JSON only",
            "human_review_required": True,
            "approval_created_by_import": False,
            "preserve_candidate_bytes": True,
        },
    }
    if stage == "quiz-review":
        material = quiz_review_material(root)
        prompt = load_semantic_review_prompt()
        return _write_task_package(stage, root, {
            **common,
            "instructions": "\n\n".join(prompt.values()),
            "input_boundary": _semantic_input_boundary(
                root, material, reviewer_mode, prepare_companion=True,
            ),
        })
    if stage == "extraction":
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
            "instructions": (
                "HOST DELIVERY POLICY:\n"
                "- Treat the native PDF as the primary input.\n"
                "- Do not load or attach all rendered page PNGs.\n"
                "- Page-image paths are locators only. Inspect at most the specific page image "
                "needed for targeted visual or geometry clarification.\n"
                "- Never use local text-audit files as semantic model input.\n\n"
                + (PACKAGE_DIR / "prompts" / "extractor-v2.md").read_text(encoding="utf-8")
            ),
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
        extracted, upstream = _load_kc_source(
            root,
            allow_proposed_extraction_demo=allow_proposed_extraction_demo,
        )
        prompt = load_prompt_package()
        task = {
            **common,
            "instructions": prompt.instructions,
            "input_boundary": _kc_input_boundary(root, extracted, upstream),
        }
        return _write_task_package(stage, root, task)
    if stage == "quiz":
        resolved_kc = (kc_path or (root / "kc-proposed.json")).expanduser().resolve()
        if not resolved_kc.is_file():
            raise RuntimeError(f"KC set is missing: {resolved_kc}")
        raw_kc_set = read_json(resolved_kc)
        kc_set = ProposedKCSet.model_validate(raw_kc_set)
        _validate_quiz_lineage(root, kc_set)
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
        )
        task = {
            **common,
            "instructions": prompt.instructions,
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
        if isinstance(exc, ValidationError) else None
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
    run_dir: Path, candidate_path: Path, *, task_package: Path | None = None,
) -> dict[str, Any]:
    """Validate an extraction payload and bind the source descriptor owned by code."""

    started = time.perf_counter()
    root = run_dir.expanduser().resolve()
    artifacts = RunArtifacts(root)
    raw, raw_sha256, raw_path = _preserve_candidate("extraction", root, candidate_path)
    task: dict[str, Any] | None = None
    try:
        source = _manifest_source(root)
        if task_package is not None:
            task = _load_task_package("extraction", root, task_package)
            frozen = task["input_boundary"]
            if (
                frozen["source"] != source.model_dump(mode="json")
                or frozen["source_pdf_sha256"] != sha256_file(artifacts.source_pdf)
            ):
                raise ValueError("PDF changed after the frozen Extraction task")
        payload = ExtractedSourcePayload.model_validate_json(raw)
        extracted = payload.with_source(source)
        _guard_extraction_approval(artifacts)
        audit = build_audit(extracted, root)
    except (KeyError, OSError, ValidationError, ValueError, RuntimeError) as exc:
        _contract_error(artifacts.contract_errors, exc, raw_path)
        _write_import_record(
            stage="extraction",
            run_dir=root,
            raw_path=raw_path,
            raw_sha256=raw_sha256,
            status="CONTRACT_INVALID",
            error=exc,
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
        "source": source.model_dump(mode="json"),
        "output_schema_version": extracted.schema_version,
        "human_review_required": True,
        "approval_status": "PROPOSED",
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
        "completed_at": _now(),
        **_unavailable_metrics(),
    }
    write_json(artifacts.metadata, metadata)
    write_json(artifacts.proposed, extracted.model_dump(mode="json"))
    write_json(artifacts.audit, audit)
    write_json(artifacts.metrics, metrics)
    artifacts.contract_errors.unlink(missing_ok=True)
    review_path = build_review(root)
    import_record = _write_import_record(
        stage="extraction",
        run_dir=root,
        raw_path=raw_path,
        raw_sha256=raw_sha256,
        status="PROPOSED",
        canonical_path=artifacts.proposed,
        task_fingerprint=task["task_fingerprint"] if task else None,
    )
    return {
        "stage": "extraction",
        "status": "PROPOSED",
        "proposed": str(artifacts.proposed),
        "review": str(review_path),
        "raw_candidate": str(raw_path),
        "raw_candidate_sha256": raw_sha256,
        "import_record": str(import_record),
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
    try:
        if task_package is not None:
            task = _load_task_package("kc", root, task_package)
            allow_proposed_extraction_demo = task["input_boundary"][
                "upstream_extraction"
            ]["demo_only"]
        extracted, upstream = _load_kc_source(
            root,
            allow_proposed_extraction_demo=allow_proposed_extraction_demo,
        )
        context = load_authoring_context(root, extracted.source)
        if context is not None and task is None:
            raise ValueError("KC with supplementary context requires its frozen --task-package")
        if task is not None and task["input_boundary"] != _kc_input_boundary(
            root, extracted, upstream,
        ):
            raise ValueError("Extraction or authoring context changed after the frozen KC task")
        proposed = ProposedKCSet.model_validate_json(raw)
        proposed.validate_against_source(
            extracted, authoring_context=context, require_context_audit=context is not None,
        )
    except (KeyError, OSError, ValidationError, ValueError, RuntimeError) as exc:
        _contract_error(artifacts.kc_contract_errors, exc, raw_path)
        _write_import_record(
            stage="kc",
            run_dir=root,
            raw_path=raw_path,
            raw_sha256=raw_sha256,
            status="CONTRACT_INVALID",
            error=exc,
        )
        raise

    fingerprint = _fingerprint(
        {
            "stage": "kc",
            "candidate_raw_sha256": raw_sha256,
            "upstream_extraction_sha256": upstream["sha256"],
            "upstream_extraction_status": upstream["status"],
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
        "source_page_count": extracted.source.page_count,
        "page_audit_count": len(proposed.page_audit),
        "leaf_kc_count": len(proposed.leaf_kcs),
        "kc_group_count": len(proposed.kc_groups),
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
    review = build_kc_demo(
        root,
        [root],
        allow_proposed_extraction_demo=upstream["demo_only"],
    )
    import_record = _write_import_record(
        stage="kc",
        run_dir=root,
        raw_path=raw_path,
        raw_sha256=raw_sha256,
        status="PROPOSED",
        canonical_path=artifacts.kc_proposed,
        task_fingerprint=task["task_fingerprint"] if task else None,
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
    try:
        if task_package is not None:
            task = _load_task_package("quiz", root, task_package)
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
        else:
            if variants_per_kc is None:
                raise ValueError("adaptive Quiz import requires --task-package from agent-task")
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
        if not resolved_kc.is_file():
            raise RuntimeError(f"KC set is missing: {resolved_kc}")
        raw_kc_set = read_json(resolved_kc)
        kc_set = ProposedKCSet.model_validate(raw_kc_set)
        _validate_quiz_lineage(root, kc_set)
        kc_sha256 = sha256_file(resolved_kc)
        quiz_input = build_quiz_input(
            kc_set, kc_set_sha256=kc_sha256, config=config,
            raw_kc_set=raw_kc_set,
        )
        if task is not None and (
            task["input_boundary"]["kc_set"]["sha256"] != kc_sha256
            or task["input_boundary"]["payload"] != quiz_input
        ):
            raise ValueError("KC or runtime differs from the frozen Quiz task")
        proposed = (
            QuizBatchV3.model_validate_json(raw, strict=True)
            if config.variants_per_kc is None else QuizBatch.model_validate_json(raw)
        )
        proposed.validate_against_input(quiz_input)
    except (KeyError, OSError, ValidationError, ValueError, RuntimeError) as exc:
        _contract_error(artifacts.quiz_contract_errors, exc, raw_path)
        _write_import_record(
            stage="quiz",
            run_dir=root,
            raw_path=raw_path,
            raw_sha256=raw_sha256,
            status="CONTRACT_INVALID",
            error=exc,
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
            interaction: sum(
                question.interaction == interaction for question in proposed.questions
            )
            for interaction in config.allowed_interactions
        },
        "raw_candidate_sha256": raw_sha256,
        "raw_candidate_bytes_preserved": True,
        "model_elapsed_seconds": None,
        "local_import_and_validation_seconds": round(time.perf_counter() - started, 6),
        "completed_at": _now(),
        **_unavailable_metrics(),
    }
    write_json(artifacts.quiz_input, quiz_input)
    write_json(artifacts.quiz_metadata, metadata)
    write_bytes(artifacts.quiz_raw_output, raw)
    write_bytes(artifacts.quiz_proposed, raw)
    write_json(artifacts.quiz_form_audit, build_quiz_form_audit(proposed))
    write_json(artifacts.quiz_metrics, metrics)
    artifacts.quiz_contract_errors.unlink(missing_ok=True)
    review_path = build_quiz_review(root, candidate_dir=destination)
    import_record = _write_import_record(
        stage="quiz",
        run_dir=root,
        raw_path=raw_path,
        raw_sha256=raw_sha256,
        status="EXPERIMENTAL_UNAPPROVED",
        canonical_path=artifacts.quiz_proposed,
        task_fingerprint=task["task_fingerprint"] if task else None,
    )
    return {
        "stage": "quiz",
        "status": "EXPERIMENTAL_UNAPPROVED",
        "proposed": str(artifacts.quiz_proposed),
        "review": str(review_path),
        "raw_candidate": str(raw_path),
        "raw_candidate_sha256": raw_sha256,
        "import_record": str(import_record),
        **_unavailable_metrics(),
    }


def import_quiz_semantic(
    run_dir: Path, candidate_path: Path, *, task_package: Path | None,
) -> dict[str, Any]:
    """Record an independent initial check without editing or approving any teaching output."""

    root = run_dir.expanduser().resolve()
    destination = root / "quiz"
    raw, raw_sha256, raw_path = _preserve_candidate("quiz-review", root, candidate_path)
    task: dict[str, Any] | None = None
    error_path = destination / "quiz-semantic-contract-errors.json"
    try:
        if task_package is None:
            raise ValueError("Quiz semantic import requires --task-package from agent-task")
        task = _load_task_package("quiz-review", root, task_package)
        material = quiz_review_material(root)
        reviewer_mode = task["input_boundary"]["reviewer_mode"]
        current_boundary = _semantic_input_boundary(
            root, material, reviewer_mode, prepare_companion=False,
        )
        if task["input_boundary"] != current_boundary:
            raise ValueError("Quiz, KC, source, context, or reviewer protocol changed after review")
        quiz = material["artifacts"]["quiz"]
        report = QuizSemanticAudit.model_validate_json(raw, strict=True)
        validate_semantic_audit(
            report, quiz=quiz, expected_source_ref=material["source_ref"],
            artifacts=material["artifacts"], expected_reviewer=reviewer_mode,
        )
        summary = semantic_audit_summary(
            report, quiz=quiz, expected_source_ref=material["source_ref"],
        )
    except (KeyError, OSError, ValidationError, ValueError, RuntimeError) as exc:
        _contract_error(error_path, exc, raw_path)
        _write_import_record(
            stage="quiz-review", run_dir=root, raw_path=raw_path, raw_sha256=raw_sha256,
            status="CONTRACT_INVALID", error=exc,
            task_fingerprint=task["task_fingerprint"] if task else None,
        )
        raise

    report_path = destination / AUDIT_FILENAME
    write_bytes(report_path, raw)
    write_json(destination / AUDIT_METADATA_FILENAME, {
        "stage": "quiz-review",
        "stage_version": "agent-quiz-semantic-review.v1",
        "execution_mode": EXECUTION_MODE,
        "provider_api_calls": 0,
        "candidate_raw_path": str(raw_path),
        "candidate_raw_sha256": raw_sha256,
        "candidate_bytes_preserved_exactly": True,
        "task_fingerprint": task["task_fingerprint"],
        "source_ref": material["source_ref"],
        "reviewer_mode": reviewer_mode,
        "reviewer_identity_is_self_reported": True,
        "initial_check_status": summary["status"],
        "original_quiz_modified": False,
        "approval_status": "EXPERIMENTAL_UNAPPROVED",
        "human_review_required": True,
        "created_at": _now(),
    })
    error_path.unlink(missing_ok=True)
    review_path = build_quiz_review(root, candidate_dir=destination)
    import_record = _write_import_record(
        stage="quiz-review", run_dir=root, raw_path=raw_path, raw_sha256=raw_sha256,
        status=summary["status"], canonical_path=report_path,
        task_fingerprint=task["task_fingerprint"],
    )
    return {
        "stage": "quiz-review", "status": summary["status"],
        "summary": summary, "report": str(report_path), "review": str(review_path),
        "raw_candidate": str(raw_path), "raw_candidate_sha256": raw_sha256,
        "original_quiz_modified": False, "import_record": str(import_record),
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
