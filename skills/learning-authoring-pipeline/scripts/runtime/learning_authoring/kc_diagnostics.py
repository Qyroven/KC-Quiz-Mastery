"""Deterministic KC review signals without pretending to judge semantics.

These diagnostics expose patterns a human or semantic reviewer should inspect.
They deliberately contain no target KC count, coverage threshold, or automatic
PASS/FAIL decision.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _locator(item: Any) -> dict[str, Any]:
    locator: dict[str, Any] = {"page": item.page}
    source_id = getattr(item, "source_id", None)
    if source_id is not None:
        locator["source_id"] = source_id
    return locator


def kc_review_diagnostics(proposed: Any) -> dict[str, Any]:
    """Return exact, domain-neutral signals for semantic KC review."""

    learning_audits = [
        audit for audit in proposed.page_audit if audit.classification == "learning_content"
    ]
    unlinked_learning_pages = [_locator(audit) for audit in learning_audits if not audit.kc_ids]

    uncovered_by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    original_reasons: dict[str, str] = {}
    for item in proposed.uncovered_content:
        key = _normalized_text(item.reason)
        original_reasons.setdefault(key, item.reason)
        uncovered_by_reason[key].append(_locator(item))

    repeated_uncovered_reasons = [
        {
            "reason": original_reasons[key],
            "occurrences": len(locators),
            "locations": locators,
        }
        for key, locators in sorted(uncovered_by_reason.items())
        if len(locators) > 1
    ]

    repeated_supports: list[dict[str, Any]] = []
    evidence_pages_by_kc: dict[str, list[dict[str, Any]]] = {}
    boundary_items_by_kc: dict[str, dict[str, int]] = {}
    for kc in proposed.leaf_kcs:
        pages = {
            tuple(sorted(_locator(evidence).items())) for evidence in kc.source_evidence
        }
        evidence_pages_by_kc[kc.kc_id] = [dict(items) for items in sorted(pages)]
        boundary_items_by_kc[kc.kc_id] = {
            "included": len(kc.assessment_boundary.included),
            "excluded": len(kc.assessment_boundary.excluded),
        }
        supports: dict[str, list[Any]] = defaultdict(list)
        original_supports: dict[str, str] = {}
        for evidence in kc.source_evidence:
            key = _normalized_text(evidence.supports)
            original_supports.setdefault(key, evidence.supports)
            supports[key].append(evidence)
        for key, records in sorted(supports.items()):
            if len(records) > 1:
                repeated_supports.append(
                    {
                        "kc_id": kc.kc_id,
                        "supports": original_supports[key],
                        "occurrences": len(records),
                        "evidence_ids": [record.evidence_id for record in records],
                        "locations": [_locator(record) for record in records],
                    }
                )

    return {
        "diagnostic_version": "kc-review-diagnostics.v1",
        "interpretation": (
            "Review signals only; they are not KC-count targets, coverage scores, "
            "semantic approval, or evidence that a split or merge is correct."
        ),
        "human_semantic_review_required": True,
        "learning_content_page_count": len(learning_audits),
        "learning_content_pages_without_kc_count": len(unlinked_learning_pages),
        "learning_content_pages_without_kc": unlinked_learning_pages,
        "uncovered_item_count": len(proposed.uncovered_content),
        "distinct_uncovered_reason_count": len(uncovered_by_reason),
        "repeated_uncovered_reason_groups": repeated_uncovered_reasons,
        "repeated_evidence_support_groups": repeated_supports,
        "evidence_pages_by_kc": evidence_pages_by_kc,
        "assessment_boundary_item_counts_by_kc": boundary_items_by_kc,
    }
