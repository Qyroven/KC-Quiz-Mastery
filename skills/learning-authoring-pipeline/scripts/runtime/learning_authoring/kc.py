"""KC prompt package and approved-Extraction boundary.

Semantic KC authorship belongs to the active coding-agent session. This module
contains no model client, provider request, or generation command.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from learning_authoring.artifacts import RunArtifacts, read_json, sha256_file
from learning_authoring.contracts import ExtractedSource
from learning_authoring.prompt_packages import (
    WorkedExample,
    load_worked_example_suite,
    worked_examples_component,
)

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT_DIR = PACKAGE_DIR / "prompts" / "kc-v1"
DEFAULT_EXAMPLES_DIR = DEFAULT_PROMPT_DIR / "examples-v1"


@dataclass(frozen=True)
class KCPromptPackage:
    instructions: str
    output_schema: dict[str, Any]
    worked_examples: tuple[WorkedExample, ...]
    manifest: dict[str, Any]

    @property
    def lineage(self) -> dict[str, Any]:
        return self.manifest


def load_prompt_package(
    prompt_dir: Path = DEFAULT_PROMPT_DIR,
    *,
    examples_dir: Path = DEFAULT_EXAMPLES_DIR,
) -> KCPromptPackage:
    """Load the ordered KC instructions, schema, and neutral examples."""

    texts = {
        name: (prompt_dir / f"{name}.md").read_text(encoding="utf-8")
        for name in ("foundation", "rulebook", "task")
    }
    schema_text = (prompt_dir / "output.schema.json").read_text(encoding="utf-8")
    output_schema = json.loads(schema_text)
    instructions = "\n\n".join(texts.values())
    components: dict[str, Any] = {
        name: {
            "filename": f"{name}.md",
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "content": content,
        }
        for name, content in texts.items()
    }
    components["output_schema"] = {
        "filename": "output.schema.json",
        "sha256": hashlib.sha256(schema_text.encode()).hexdigest(),
        "content": output_schema,
    }
    suite = load_worked_example_suite(
        examples_dir,
        expected_stage="kc",
        expected_contract_version="proposed-kc-set.v1",
    )
    components["worked_examples"] = worked_examples_component(
        suite,
        filename="examples-v1/manifest.json",
    )
    package_bytes = json.dumps(components, ensure_ascii=False, sort_keys=True).encode()
    return KCPromptPackage(
        instructions=instructions,
        output_schema=output_schema,
        worked_examples=suite.examples,
        manifest={
            "package_version": "kc-agent-session.v2",
            "instruction_order": ["foundation", "rulebook", "task"],
            "structured_output_component": "output_schema",
            "worked_examples_component": "worked_examples",
            "worked_example_order": list(suite.example_order),
            "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
            "components": components,
        },
    )


def load_approved_extraction(run_dir: Path) -> tuple[ExtractedSource, dict[str, Any], str]:
    """Load an Extraction only when the explicit approval hash still matches."""

    artifacts = RunArtifacts(run_dir.expanduser().resolve())
    for path in (artifacts.approved, artifacts.approval):
        if not path.is_file():
            raise RuntimeError(f"KC requires extraction approval artifact: {path}")
    approval = read_json(artifacts.approval)
    if approval.get("status") != "approved":
        raise RuntimeError("extraction approval status is not approved")
    approved_sha256 = sha256_file(artifacts.approved)
    if approval.get("approved_sha256") != approved_sha256:
        raise RuntimeError("approved extraction hash does not match extraction-approval.json")
    approved = ExtractedSource.model_validate(read_json(artifacts.approved))
    if approval.get("schema_version") != approved.schema_version:
        raise RuntimeError("approved extraction schema does not match approval record")
    if approval.get("source_sha256") != approved.source.sha256:
        raise RuntimeError("approved extraction source hash does not match approval record")
    return approved, approval, approved_sha256
