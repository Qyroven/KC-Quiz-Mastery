"""Portable contracts with lazy compatibility exports for historical API stages.

Importing a local validator or agent helper must not load provider adapters.  Old
``from learning_authoring import run_extraction`` callers still resolve on demand.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from learning_authoring.contracts import ExtractedSource, ExtractedSourcePayload

if TYPE_CHECKING:
    from learning_authoring.extractor import ExtractionConfig, ExtractionResult, run_extraction
    from learning_authoring.kc import KCConfig, KCGenerationResult, run_kc_generation
    from learning_authoring.quiz import QuizConfig, QuizGenerationResult, run_quiz_generation

_LEGACY_EXPORT_MODULES = {
    "ExtractionConfig": "extractor",
    "ExtractionResult": "extractor",
    "run_extraction": "extractor",
    "KCConfig": "kc",
    "KCGenerationResult": "kc",
    "run_kc_generation": "kc",
    "QuizConfig": "quiz",
    "QuizGenerationResult": "quiz",
    "run_quiz_generation": "quiz",
}


def __getattr__(name: str) -> Any:
    module_name = _LEGACY_EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value

__all__ = [
    "ExtractedSource",
    "ExtractedSourcePayload",
    "ExtractionConfig",
    "ExtractionResult",
    "KCConfig",
    "KCGenerationResult",
    "QuizConfig",
    "QuizGenerationResult",
    "run_extraction",
    "run_kc_generation",
    "run_quiz_generation",
]
