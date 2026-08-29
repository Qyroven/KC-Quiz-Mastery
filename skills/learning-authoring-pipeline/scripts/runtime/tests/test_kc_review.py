from __future__ import annotations

from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.kc_review import _recall_html, _selected_candidate
from tests.conftest import payload


def candidate(model: str, source) -> dict:
    proposal = ProposedKCSet.model_validate(
        {
            "source_ref": {
                "schema_version": "extracted-source.v2",
                "source_id": source.source_id,
                "source_sha256": source.sha256,
            },
            "source_summary": "A two-page lesson",
            "page_audit": [
                {
                    "page": 1,
                    "classification": "learning_content",
                    "summary": "A taught concept",
                    "kc_ids": ["KC-001"],
                    "source_block_ids": ["b1"],
                    "warning_codes": [],
                },
                {
                    "page": 2,
                    "classification": "context",
                    "summary": "Context only",
                    "kc_ids": [],
                    "source_block_ids": ["b2"],
                    "warning_codes": [],
                },
            ],
            "kc_groups": [
                {
                    "group_id": "KCG-001",
                    "name": "Concepts",
                    "description": "Taught concepts",
                    "leaf_kc_ids": ["KC-001"],
                }
            ],
            "leaf_kcs": [
                {
                    "kc_id": "KC-001",
                    "group_id": "KCG-001",
                    "name": "Explain the concept",
                    "semantic_form": "concept",
                    "knowledge_description": "The concept taught on page one.",
                    "observable_claim": "Given an example, the learner can explain it.",
                    "assessment_boundary": {"included": ["Meaning"], "excluded": []},
                    "source_evidence": [
                        {
                            "evidence_id": "EVD-001",
                            "page": 1,
                            "block_ids": ["b1"],
                            "description": "The source definition.",
                            "supports": "The concept meaning.",
                        }
                    ],
                    "warning_codes": [],
                    "status": "PROPOSED",
                }
            ],
            "uncovered_content": [],
            "generation_warnings": [],
        }
    )
    return {"id": model, "model": model, "proposed": proposal.model_dump(mode="json")}


def test_recall_view_is_source_first_and_uses_dynamic_source_pages(source) -> None:
    approved = payload().with_source(source)
    html = _recall_html(approved, candidate("gpt-5.6-sol", source))

    assert "KC Recall Review" in html
    assert "Check against extraction" in html
    assert "Extracted blocks" in html
    assert 'id="leftResize"' in html and 'id="rightResize"' in html
    assert 'id="sidebarToggle"' in html and 'id="inspectorToggle"' in html
    assert 'id="sidebarCollapse"' in html
    assert 'aria-label="Collapse slides panel"' in html
    assert "Collapse slides</span>" not in html
    assert ".sidebar{grid-column:1}" in html
    assert ".canvas{grid-column:3}" in html
    assert ".inspector{grid-column:5}" in html
    assert "source.source.page_count" in html
    assert "source.pages.map" in html
    assert "pages/page-${String(row.page).padStart(4,'0')}.png" in html
    assert 'id="slideDeck"' in html
    assert 'data-slide-page="${row.page}"' in html
    assert "syncPageFromScroll" in html
    assert "slideScroll.addEventListener('scroll'" in html
    assert "gpt-5.6-sol" in html
    assert "Explain the concept" in html
    assert '>Overview</a>' not in html
    assert '>Compare</a>' not in html
    assert 'id="extractionLink"' in html
    assert "extraction-review.html?from=kc#" in html
    assert "No KC generated" in html
    assert "audit.summary" in html
    assert "fitSlideWidth()" in html
    assert "zoomOut.disabled" in html
    assert 'aria-label="Filter slides by KC group"' in html
    assert "renderGroupFilters" in html
    assert "row.groupIds.includes(filter)" in html
    assert "All groups" in html
    assert "Has KC</button>" not in html
    assert "No KC</button>" not in html
    assert "KCG-001" in html and "Concepts" in html
    assert "Leaf KCs" in html
    assert "One or more extracted blocks are not linked to a KC" in html
    assert "but no KC uses it as evidence" not in html


def test_scroll_view_is_separate_and_uses_floating_kc_cards(source) -> None:
    approved = payload().with_source(source)
    html = _recall_html(
        approved,
        candidate("gpt-5.6-sol", source),
        scroll_mode=True,
    )

    assert '<body class="scroll-mode">' in html
    assert "KC Scroll Review" in html
    assert 'id="kcFloat"' in html
    assert 'id="scrollKcResize"' in html
    assert "installScrollKcResize" in html
    assert "renderFloatingKcs" in html
    assert "float-kc" in html
    assert "float-kc-button" in html
    assert "float-kc-detail" in html
    assert "float-kc-group" in html
    assert 'href="kc-recall.html#1"' in html


def test_recall_candidate_follows_evaluation_recommendation(source) -> None:
    sol = candidate("gpt-5.6-sol", source)
    luna = candidate("gpt-5.6-luna", source)

    selected = _selected_candidate(
        [luna, sol], {"verdict": {"recommended_candidate": "gpt-5.6-sol"}}
    )

    assert selected is sol
