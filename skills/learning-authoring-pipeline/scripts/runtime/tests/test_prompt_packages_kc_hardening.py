"""Focused prompt-package checks for source-local visuals and evidence locality."""

from __future__ import annotations

from learning_authoring.agent_session import _bundle_kc_prompt_fields
from learning_authoring.extraction_prompt import load_extraction_prompt_package
from learning_authoring.kc import load_prompt_package
from learning_authoring.source_bundle import SourceBundleKCSet


def _compact(value: str) -> str:
    return " ".join(value.split())


def test_extraction_prompt_keeps_unresolved_visuals_source_local() -> None:
    instructions = _compact(load_extraction_prompt_package().instructions)

    assert "actual edge endpoints/directions" in instructions
    assert "another source does not recover a missing detail in this PDF" in instructions
    assert "Warnings identify the affected page/block and remaining gap" in instructions


def test_kc_prompts_require_minimal_evidence_spans() -> None:
    singular = _compact(load_prompt_package().instructions)
    bundle, _ = _bundle_kc_prompt_fields(SourceBundleKCSet.model_json_schema())
    bundle = _compact(bundle)

    assert "cite the smallest set of blocks" in singular
    assert "Do not copy every page block by default" in singular
    assert "selecting the smallest set" in bundle
    assert "do not copy every block from the page by default" in bundle


def test_singular_kc_prompt_requires_claim_level_evidence_closure() -> None:
    instructions = _compact(load_prompt_package().instructions)

    assert "Apply claim-level closure" in instructions
    assert "evidence record cited on that same KC" in instructions
    assert "claim-to-reference pass" in instructions
    assert "never import a useful conclusion from an uncited summary page" in instructions


def test_bundle_kc_prompt_keeps_visual_gaps_independent_per_source() -> None:
    instructions, _ = _bundle_kc_prompt_fields(SourceBundleKCSet.model_json_schema())
    instructions = _compact(instructions)

    assert "independently for each source" in instructions
    assert "another source may support the shared KC" in instructions
    assert "cannot clear a missing edge" in instructions
    assert "day01" not in instructions.casefold()
