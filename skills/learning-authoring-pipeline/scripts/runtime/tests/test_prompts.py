from __future__ import annotations

import hashlib
from pathlib import Path

PROMPTS = Path(__file__).parents[1] / "learning_authoring" / "prompts"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_main_prompt_source_fidelity_revision() -> None:
    prompt = " ".join((PROMPTS / "extractor-v2.md").read_text().split())
    assert "The agent reads the entire PDF, including informative visuals" in prompt
    assert "No fixed batch size or image count is required" in prompt
    assert "actual edge endpoints/directions" in prompt
    assert "An image reference alone does not extract the information it contains" in prompt
    assert "Do not silently correct the source" in prompt
    assert "Use unresolved and empty geometry" in prompt
    assert "Geometry uncertainty does not make semantic content unusable" in prompt
    assert "Revisions are allowed" in prompt
    assert "native text only" not in prompt.casefold()


def test_repair_prompt_matches_pm_feedback_baseline() -> None:
    assert sha256(PROMPTS / "repair-v1.md") == (
        "9f3c4131a26c2596c2c5a125688b5945f12fe165fe462e334473805ed6225e25"
    )
