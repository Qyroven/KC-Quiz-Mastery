from __future__ import annotations

import hashlib
from pathlib import Path

PROMPTS = Path(__file__).parents[1] / "learning_authoring" / "prompts"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_main_prompt_matches_pm_feedback_baseline() -> None:
    assert sha256(PROMPTS / "extractor-v2.md") == (
        "e0dad8f24afb4f044dc22fd42013c4f405551d17fd81fd5c44399826a47a3d29"
    )


def test_repair_prompt_matches_pm_feedback_baseline() -> None:
    assert sha256(PROMPTS / "repair-v1.md") == (
        "9f3c4131a26c2596c2c5a125688b5945f12fe165fe462e334473805ed6225e25"
    )
