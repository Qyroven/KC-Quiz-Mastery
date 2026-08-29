from __future__ import annotations

from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.kc_diagnostics import kc_review_diagnostics


def _candidate() -> ProposedKCSet:
    return ProposedKCSet.model_validate(
        {
            "source_ref": {
                "schema_version": "extracted-source.v2",
                "source_id": "sha256:test",
                "source_sha256": "a" * 64,
            },
            "source_summary": "Neutral diagnostic fixture.",
            "page_audit": [
                {
                    "page": 1,
                    "classification": "learning_content",
                    "summary": "One",
                    "kc_ids": ["KC-001"],
                    "source_block_ids": ["b1"],
                    "warning_codes": [],
                },
                {
                    "page": 2,
                    "classification": "learning_content",
                    "summary": "Two",
                    "kc_ids": [],
                    "source_block_ids": ["b2"],
                    "warning_codes": [],
                },
                {
                    "page": 3,
                    "classification": "learning_content",
                    "summary": "Three",
                    "kc_ids": [],
                    "source_block_ids": ["b3"],
                    "warning_codes": [],
                },
            ],
            "kc_groups": [
                {
                    "group_id": "KCG-001",
                    "name": "Neutral",
                    "description": "Neutral group.",
                    "leaf_kc_ids": ["KC-001"],
                }
            ],
            "leaf_kcs": [
                {
                    "kc_id": "KC-001",
                    "group_id": "KCG-001",
                    "name": "Apply one rule",
                    "semantic_form": "decision_rule",
                    "knowledge_description": "Apply one supplied rule.",
                    "observable_claim": "Given a case, choose the valid result.",
                    "assessment_boundary": {
                        "included": ["Apply the rule"],
                        "excluded": ["Invent a new rule"],
                    },
                    "source_evidence": [
                        {
                            "evidence_id": "EVD-001",
                            "page": 1,
                            "block_ids": ["b1"],
                            "description": "First statement.",
                            "supports": "The same precise claim.",
                        },
                        {
                            "evidence_id": "EVD-002",
                            "page": 2,
                            "block_ids": ["b2"],
                            "description": "Second statement.",
                            "supports": "  THE same precise claim. ",
                        },
                    ],
                    "context_evidence": [],
                    "warning_codes": [],
                    "status": "PROPOSED",
                }
            ],
            "uncovered_content": [
                {
                    "page": 2,
                    "block_ids": ["b2"],
                    "description": "Claim two.",
                    "reason": "Not represented yet.",
                },
                {
                    "page": 3,
                    "block_ids": ["b3"],
                    "description": "Claim three.",
                    "reason": " not represented yet. ",
                },
            ],
            "generation_warnings": [],
            "context_audit": [],
        }
    )


def test_kc_diagnostics_expose_review_signals_without_semantic_verdicts() -> None:
    diagnostics = kc_review_diagnostics(_candidate())

    assert diagnostics["learning_content_page_count"] == 3
    assert diagnostics["learning_content_pages_without_kc_count"] == 2
    assert diagnostics["learning_content_pages_without_kc"] == [{"page": 2}, {"page": 3}]
    assert diagnostics["distinct_uncovered_reason_count"] == 1
    assert diagnostics["repeated_uncovered_reason_groups"][0]["occurrences"] == 2
    assert diagnostics["repeated_evidence_support_groups"][0]["kc_id"] == "KC-001"
    assert diagnostics["human_semantic_review_required"] is True
    serialized_keys = " ".join(diagnostics).casefold()
    assert "pass" not in serialized_keys
    assert "fail" not in serialized_keys
    assert "target" not in serialized_keys
