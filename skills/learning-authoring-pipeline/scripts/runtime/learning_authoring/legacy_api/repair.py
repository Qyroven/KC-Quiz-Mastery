"""Isolated page repair with document-valid fallback semantics."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from learning_authoring.artifacts import RunArtifacts, write_json
from learning_authoring.audit import reported_cost, response_usage
from learning_authoring.contracts import (
    ExtractedPage,
    ExtractedSourcePayload,
    SourceDescriptor,
    WarningRecord,
    source_region_geometry_state,
)
from learning_authoring.legacy_api.gateway import execute_response
from learning_authoring.legacy_api.requests import build_repair_request


@dataclass(frozen=True)
class RepairPolicy:
    enabled: bool
    max_attempts: int
    model: str
    reasoning_effort: str
    response_mode: str
    pdf_detail: str
    max_output_tokens: int | None
    poll_interval_seconds: float
    timeout_seconds: float
    prompt: str
    extraction_fingerprint: str
    max_candidate_pages: int | None = 12
    systemic_guard_min_candidate_pages: int = 4
    systemic_guard_max_page_fraction: float = 0.5


_HUMAN_ONLY_ISSUE_CLASSES = {
    "source_ambiguity",
    "human_semantic_decision",
    "semantic_review",
}
_HUMAN_ONLY_REPAIR_ROUTES = {"human_review", "human_only", "none", "not_recoverable"}


def _output_text(response: Any) -> str:
    value = getattr(response, "output_text", None)
    if isinstance(value, str) and value.strip():
        return value
    raise RuntimeError("repair response contains no structured output text")


def _warning_pages(warning: WarningRecord) -> set[int]:
    pages = {warning.page} if warning.page is not None else set()
    values = warning.details.get("pages")
    if isinstance(values, list):
        pages.update(
            value
            for value in values
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1
        )
    return pages


def _detail_token(warning: WarningRecord, key: str) -> str | None:
    value = warning.details.get(key)
    return value.strip().casefold() if isinstance(value, str) and value.strip() else None


def _warning_requires_repair(warning: WarningRecord) -> bool:
    """Honor recoverable legacy warnings while blocking explicitly human-only issues."""

    if warning.details.get("repair_recommended") is not True:
        return False
    if _detail_token(warning, "issue_class") in _HUMAN_ONLY_ISSUE_CLASSES:
        return False
    if _detail_token(warning, "repair_route") in _HUMAN_ONLY_REPAIR_ROUTES:
        return False
    return True


def _geometry_repair_ids(page: ExtractedPage) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"unresolved": [], "invalid": []}
    for block in page.blocks:
        state = source_region_geometry_state(block.region)
        if state != "located":
            result[state].append(block.block_id)
    return result


def _page_repair_reasons(page: ExtractedPage) -> dict[str, list[str]]:
    geometry = _geometry_repair_ids(page)
    return {
        "unresolved_geometry_block_ids": geometry["unresolved"],
        "invalid_geometry_block_ids": geometry["invalid"],
        "repair_warning_codes": [
            warning.code for warning in page.warnings if _warning_requires_repair(warning)
        ],
        "document_repair_warning_codes": [],
    }


def repair_candidates(payload: ExtractedSourcePayload) -> list[int]:
    candidates = {page.page_number for page in payload.pages if _page_requires_repair(page)}
    for warning in payload.warnings:
        if _warning_requires_repair(warning):
            candidates.update(_warning_pages(warning))
    known = {page.page_number for page in payload.pages}
    return sorted(candidates & known)


def _page_requires_repair(page: ExtractedPage) -> bool:
    reasons = _page_repair_reasons(page)
    return any(reasons.values())


def _repair_guard(
    candidates: list[int],
    *,
    page_count: int,
    policy: RepairPolicy,
) -> dict[str, Any]:
    fraction = len(candidates) / page_count if page_count else 0.0
    reasons: list[str] = []
    if policy.max_candidate_pages is not None and len(candidates) > policy.max_candidate_pages:
        reasons.append("candidate_page_budget_exceeded")
    if (
        len(candidates) >= policy.systemic_guard_min_candidate_pages
        and fraction > policy.systemic_guard_max_page_fraction
    ):
        reasons.append("systemic_candidate_spread")
    return {
        "triggered": bool(reasons),
        "reasons": reasons,
        "candidate_page_count": len(candidates),
        "source_page_count": page_count,
        "candidate_page_fraction": round(fraction, 4),
        "limits": {
            "max_candidate_pages": policy.max_candidate_pages,
            "systemic_guard_min_candidate_pages": policy.systemic_guard_min_candidate_pages,
            "systemic_guard_max_page_fraction": policy.systemic_guard_max_page_fraction,
        },
    }


def _guarded_payload(
    payload: ExtractedSourcePayload,
    *,
    candidates: list[int],
    guard: dict[str, Any],
) -> ExtractedSourcePayload:
    warning = WarningRecord(
        code="TARGETED_REPAIR_SYSTEMIC_GUARD",
        message=(
            "Automatic targeted repair was not started because the candidate set looks "
            "systemic or exceeds the configured page budget. Human review is required."
        ),
        details={
            "issue_class": "systemic_repair_guard",
            "repair_route": "human_review",
            "review_disposition": "review",
            "repair_recommended": False,
            "candidate_pages": candidates,
            **guard,
        },
    )
    warnings = [item for item in payload.warnings if item.code != warning.code]
    return payload.model_copy(update={"warnings": [*warnings, warning]})


def _resolved_document_warnings(
    warnings: list[WarningRecord],
    repaired_pages: set[int],
) -> list[WarningRecord]:
    return [
        warning
        for warning in warnings
        if not (
            _warning_requires_repair(warning)
            and _warning_pages(warning)
            and _warning_pages(warning) <= repaired_pages
        )
    ]


def _failed_document_warnings(
    warnings: list[WarningRecord],
    failed_page: int,
) -> list[WarningRecord]:
    updated: list[WarningRecord] = []
    for warning in warnings:
        if _warning_requires_repair(warning) and failed_page in _warning_pages(warning):
            details = {
                **warning.details,
                "repair_recommended": False,
                "automatic_repair_exhausted": True,
            }
            updated.append(warning.model_copy(update={"details": details}))
        else:
            updated.append(warning)
    return updated


def _exhausted_page(
    page: ExtractedPage,
    *,
    attempt_limit: int,
    attempts: list[dict[str, Any]],
) -> ExtractedPage:
    warnings: list[WarningRecord] = []
    for warning in page.warnings:
        if _warning_requires_repair(warning):
            details = {
                **warning.details,
                "repair_recommended": False,
                "automatic_repair_exhausted": True,
            }
            warnings.append(warning.model_copy(update={"details": details}))
        else:
            warnings.append(warning)
    geometry = _geometry_repair_ids(page)
    missing_geometry_ids = [*geometry["unresolved"], *geometry["invalid"]]
    warnings.append(
        WarningRecord(
            code="TARGETED_REPAIR_EXHAUSTED",
            message=(
                f"Automatic targeted repair did not resolve page {page.page_number} "
                f"after {attempt_limit} attempts. Human review is required."
            ),
            page=page.page_number,
            block_ids=missing_geometry_ids,
            details={
                "review_disposition": "review",
                "repair_recommended": False,
                "automatic_repair_exhausted": True,
                "attempt_count": attempt_limit,
                "missing_geometry_block_ids": missing_geometry_ids,
                "unresolved_geometry_block_ids": geometry["unresolved"],
                "invalid_geometry_block_ids": geometry["invalid"],
                "attempt_results": [
                    {
                        "attempt": attempt.get("attempt"),
                        "status": attempt.get("status"),
                        "error_type": attempt.get("error_type"),
                    }
                    for attempt in attempts
                ],
            },
        )
    )
    return page.model_copy(update={"warnings": warnings})


def _replace_page(
    payload: ExtractedSourcePayload,
    page_number: int,
    page: ExtractedPage,
    *,
    warnings: list[WarningRecord],
) -> ExtractedSourcePayload:
    return ExtractedSourcePayload(
        schema_version=payload.schema_version,
        pages=[page if item.page_number == page_number else item for item in payload.pages],
        cross_page_relations=payload.cross_page_relations,
        warnings=warnings,
    )


def run_repairs(
    payload: ExtractedSourcePayload,
    *,
    source: SourceDescriptor,
    artifacts: RunArtifacts,
    policy: RepairPolicy,
    client: Any,
    progress: Callable[[str], None] | None = None,
) -> tuple[ExtractedSourcePayload, dict[str, Any], list[dict[str, int]], list[float | None]]:
    candidates = repair_candidates(payload) if policy.enabled else []
    guard = _repair_guard(candidates, page_count=len(payload.pages), policy=policy)
    reasons_by_page = {
        str(page.page_number): _page_repair_reasons(page)
        for page in payload.pages
        if page.page_number in candidates
    }
    for warning in payload.warnings:
        if not _warning_requires_repair(warning):
            continue
        for page_number in _warning_pages(warning) & set(candidates):
            reasons_by_page[str(page_number)]["document_repair_warning_codes"].append(warning.code)
    summary: dict[str, Any] = {
        "enabled": policy.enabled,
        "max_attempts_per_page": policy.max_attempts,
        "candidate_pages": candidates,
        "candidate_reasons_by_page": reasons_by_page,
        "guard": guard,
        "attempted_pages": [],
        "applied_pages": [],
        "failed_pages": [],
        "attempts": [],
    }
    write_json(artifacts.repair_summary, summary)
    if not candidates:
        return payload, summary, [], []
    if guard["triggered"]:
        guarded = _guarded_payload(payload, candidates=candidates, guard=guard)
        return guarded, summary, [], []

    artifacts.repair_dir.mkdir(parents=True, exist_ok=True)
    current = payload
    repaired_pages: set[int] = set()
    usage_rows: list[dict[str, int]] = []
    costs: list[float | None] = []

    for page_number in candidates:
        summary["attempted_pages"].append(page_number)
        last_document_valid_page = next(
            page for page in current.pages if page.page_number == page_number
        )
        page_attempts: list[dict[str, Any]] = []
        resolved = False
        for attempt_number in range(1, policy.max_attempts + 1):
            request = build_repair_request(
                run_dir=artifacts.run_dir,
                page=last_document_valid_page,
                page_count=source.page_count,
                filename=source.filename,
                prompt=policy.prompt,
                model=policy.model,
                reasoning_effort=policy.reasoning_effort,
                response_mode=policy.response_mode,
                pdf_detail=policy.pdf_detail,
                max_output_tokens=policy.max_output_tokens,
                attempt_number=attempt_number,
                attempt_limit=policy.max_attempts,
            )
            page_json = json.dumps(
                last_document_valid_page.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            )
            fingerprint = hashlib.sha256(
                (
                    f"{policy.extraction_fingerprint}:repair:{page_number}:"
                    f"{attempt_number}:{page_json}"
                ).encode()
            ).hexdigest()
            prefix = f"page-{page_number:04d}-attempt-{attempt_number:02d}"
            attempt: dict[str, Any] = {
                "page": page_number,
                "attempt": attempt_number,
                "status": "failed",
            }
            # Gateway failures deliberately escape this function. The checkpoint
            # must be resumed on the next invocation rather than silently moving
            # to another paid repair attempt.
            response, raw, elapsed, resumed = execute_response(
                client,
                request,
                response_mode=policy.response_mode,
                checkpoint_path=artifacts.repair_dir / f"{prefix}-checkpoint.json",
                request_fingerprint=fingerprint,
                poll_interval_seconds=policy.poll_interval_seconds,
                timeout_seconds=policy.timeout_seconds,
                progress=progress,
            )
            write_json(artifacts.repair_dir / f"{prefix}-api-response.json", raw)
            usage = response_usage(raw)
            cost = reported_cost(raw)
            usage_rows.append(usage)
            costs.append(cost)
            attempt.update(
                {
                    "elapsed_seconds": round(elapsed, 6),
                    "usage": usage,
                    "gateway_reported_cost_usd": cost,
                    "resumed": resumed,
                }
            )
            try:
                repaired = ExtractedPage.model_validate_json(_output_text(response))
                if repaired.page_number != page_number:
                    raise ValueError(
                        f"repair returned page {repaired.page_number}; expected {page_number}"
                    )

                candidate = _replace_page(
                    current,
                    page_number,
                    repaired,
                    warnings=current.warnings,
                )
                candidate.with_source(source)
                last_document_valid_page = repaired
                write_json(
                    artifacts.repair_dir / f"{prefix}-proposed.json",
                    repaired.model_dump(mode="json"),
                )
                attempt.update(
                    {
                        "status": "unresolved" if _page_requires_repair(repaired) else "applied",
                    }
                )
                if not _page_requires_repair(repaired):
                    repaired_pages.add(page_number)
                    current = _replace_page(
                        current,
                        page_number,
                        repaired,
                        warnings=_resolved_document_warnings(
                            current.warnings,
                            repaired_pages,
                        ),
                    )
                    current.with_source(source)
                    summary["applied_pages"].append(page_number)
                    resolved = True
            except (RuntimeError, TimeoutError, ValidationError, ValueError) as exc:
                attempt["error_type"] = type(exc).__name__
                attempt["message"] = str(exc)

            page_attempts.append(attempt)
            summary["attempts"].append(attempt)
            write_json(artifacts.repair_summary, summary)
            if resolved:
                break

        if resolved:
            continue

        page_attempts[-1]["final_outcome"] = "exhausted"
        fallback = _exhausted_page(
            last_document_valid_page,
            attempt_limit=policy.max_attempts,
            attempts=page_attempts,
        )
        current = _replace_page(
            current,
            page_number,
            fallback,
            warnings=_failed_document_warnings(current.warnings, page_number),
        )
        current.with_source(source)
        summary["failed_pages"].append(page_number)
        write_json(
            artifacts.repair_dir / f"page-{page_number:04d}-exhausted.json",
            fallback.model_dump(mode="json"),
        )
        write_json(artifacts.repair_summary, summary)

    return current, summary, usage_rows, costs
