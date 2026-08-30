"""Extraction prompt package for subscription-native agent tasks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from learning_authoring.contracts import ExtractedSourcePayload
from learning_authoring.prompt_packages import (
    WorkedExample,
    load_worked_example_suite,
    worked_examples_component,
)

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT_PATH = PACKAGE_DIR / "prompts" / "extractor-v2.md"
DEFAULT_EXAMPLES_DIR = PACKAGE_DIR / "prompts" / "extractor-v2" / "examples-v1"


@dataclass(frozen=True)
class ExtractionPromptPackage:
    instructions: str
    output_schema: dict[str, Any]
    worked_examples: tuple[WorkedExample, ...]
    manifest: dict[str, Any]

    @property
    def lineage(self) -> dict[str, Any]:
        return self.manifest


def load_extraction_prompt_package(
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    *,
    examples_dir: Path = DEFAULT_EXAMPLES_DIR,
) -> ExtractionPromptPackage:
    """Load instructions, contract, and neutral examples without a provider adapter."""

    instructions = prompt_path.read_text(encoding="utf-8")
    output_schema = ExtractedSourcePayload.model_json_schema()
    suite = load_worked_example_suite(
        examples_dir,
        expected_stage="extraction",
        expected_contract_version="extracted-source.v2",
    )
    schema_bytes = json.dumps(output_schema, ensure_ascii=False, sort_keys=True).encode()
    components: dict[str, Any] = {
        "instructions": {
            "filename": prompt_path.name,
            "sha256": hashlib.sha256(instructions.encode()).hexdigest(),
            "content": instructions,
        },
        "output_schema": {
            "source": "learning_authoring.contracts.ExtractedSourcePayload",
            "schema_version": "extracted-source.v2",
            "sha256": hashlib.sha256(schema_bytes).hexdigest(),
            "content": output_schema,
        },
        "worked_examples": worked_examples_component(
            suite,
            filename="extractor-v2/examples-v1/manifest.json",
        ),
    }
    package_bytes = json.dumps(components, ensure_ascii=False, sort_keys=True).encode()
    return ExtractionPromptPackage(
        instructions=instructions,
        output_schema=output_schema,
        worked_examples=suite.examples,
        manifest={
            "package_version": "extraction-prompt.v2",
            "instruction_order": ["instructions"],
            "structured_output_component": "output_schema",
            "worked_examples_component": "worked_examples",
            "worked_example_order": list(suite.example_order),
            "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
            "components": components,
        },
    )
