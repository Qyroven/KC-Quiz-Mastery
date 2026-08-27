from __future__ import annotations

import hashlib
from pathlib import Path

PROMPTS = Path(__file__).parents[1] / "learning_authoring" / "prompts"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_main_prompt_source_fidelity_revision() -> None:
    # Intentional v1.4 extension: retain the PM contract and explicitly check
    # image-only content, endpoint tracing and non-placeholder geometry.
    assert sha256(PROMPTS / "extractor-v2.md") == (
        "cfddc6bdd85b329c96babffc1cbd472a1a53d4a61908f6ac3bd22b3538ceef98"
    )
    prompt = (PROMPTS / "extractor-v2.md").read_text()
    assert "Trace the\nline to its endpoint" in prompt
    assert "not evidence that all content" in prompt
    assert "full-page box is valid only" in prompt
    assert "Do not normalize source" in prompt
    assert "Never request repair merely because the page is visually complex" in prompt


def test_repair_prompt_matches_pm_feedback_baseline() -> None:
    assert sha256(PROMPTS / "repair-v1.md") == (
        "9f3c4131a26c2596c2c5a125688b5945f12fe165fe462e334473805ed6225e25"
    )
