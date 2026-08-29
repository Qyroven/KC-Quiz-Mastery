"""Deterministic, stage-owned worked-example package loading.

Worked examples are trusted package assets shown to the authoring agent.  They are
not evaluation holdouts and never contain run-specific paths or content.  The
loader follows an explicit manifest rather than discovering files with a glob so
that order and lineage remain stable across installations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a JSON value deterministically for semantic content hashes."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True)
class WorkedExample:
    example_id: str
    filename: str
    teaching_points: tuple[str, ...]
    illustrative_values_only: bool
    input: dict[str, Any]
    output: dict[str, Any]
    sha256: str

    def as_payload(self) -> dict[str, Any]:
        """Return the exact semantic content delivered to a host agent."""

        return {
            "example_version": "worked-example.v1",
            "example_id": self.example_id,
            "teaching_points": list(self.teaching_points),
            "illustrative_values_only": self.illustrative_values_only,
            "input": self.input,
            "output": self.output,
        }


@dataclass(frozen=True)
class WorkedExampleSuite:
    suite_version: str
    stage: str
    contract_version: str
    manifest_filename: str
    examples: tuple[WorkedExample, ...]
    sha256: str

    @property
    def example_order(self) -> tuple[str, ...]:
        return tuple(example.example_id for example in self.examples)

    def as_payload(self) -> list[dict[str, Any]]:
        return [example.as_payload() for example in self.examples]

    def lineage(self) -> dict[str, Any]:
        """Return content hashes without repeating the worked-example payloads."""

        return {
            "suite_version": self.suite_version,
            "stage": self.stage,
            "contract_version": self.contract_version,
            "manifest_filename": self.manifest_filename,
            "example_order": list(self.example_order),
            "examples": [
                {
                    "example_id": example.example_id,
                    "filename": example.filename,
                    "sha256": example.sha256,
                }
                for example in self.examples
            ],
            "suite_sha256": self.sha256,
        }


def _require_exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} keys must be exactly {sorted(expected)}; got {sorted(value)}")


def _relative_json_path(root: Path, value: Any) -> tuple[str, Path]:
    if not isinstance(value, str) or not value:
        raise ValueError("worked-example filename must be a nonempty relative path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "\\" in value
        or relative.suffix != ".json"
        or str(relative) != value
    ):
        raise ValueError(f"invalid worked-example filename: {value}")
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"worked-example path escapes its suite: {value}")
    if not path.is_file():
        raise ValueError(f"worked-example file does not exist: {path}")
    return value, path


def load_worked_example_suite(
    directory: Path,
    *,
    expected_stage: str,
    expected_contract_version: str,
) -> WorkedExampleSuite:
    """Load one explicit, ordered example suite and derive semantic lineage."""

    root = directory.expanduser().resolve()
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError(f"worked-example manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("worked-example manifest must be a JSON object")
    _require_exact_keys(
        manifest,
        {"suite_version", "stage", "contract_version", "example_order", "examples"},
        label="worked-example manifest",
    )
    if manifest["suite_version"] != "worked-example-suite.v1":
        raise ValueError("unsupported worked-example suite version")
    if manifest["stage"] != expected_stage:
        raise ValueError("worked-example stage does not match its prompt package")
    if manifest["contract_version"] != expected_contract_version:
        raise ValueError("worked-example contract does not match its prompt package")
    order = manifest["example_order"]
    files = manifest["examples"]
    if (
        not isinstance(order, list)
        or not order
        or not all(isinstance(value, str) and value.strip() for value in order)
        or len(order) != len(set(order))
    ):
        raise ValueError("worked-example order must contain unique nonblank IDs")
    if not isinstance(files, dict) or set(files) != set(order):
        raise ValueError("worked-example file mapping must exactly match example_order")

    examples: list[WorkedExample] = []
    for example_id in order:
        filename, example_path = _relative_json_path(root, files[example_id])
        payload = json.loads(example_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"worked example {example_id} must be a JSON object")
        _require_exact_keys(
            payload,
            {
                "example_version",
                "example_id",
                "teaching_points",
                "illustrative_values_only",
                "input",
                "output",
            },
            label=f"worked example {example_id}",
        )
        if payload["example_version"] != "worked-example.v1":
            raise ValueError(f"worked example {example_id} has an unsupported version")
        if payload["example_id"] != example_id:
            raise ValueError(f"worked example {example_id} identity does not match its manifest")
        teaching_points = payload["teaching_points"]
        if (
            not isinstance(teaching_points, list)
            or not teaching_points
            or not all(isinstance(value, str) and value.strip() for value in teaching_points)
            or len(teaching_points) != len(set(teaching_points))
        ):
            raise ValueError(f"worked example {example_id} needs unique teaching points")
        if payload["illustrative_values_only"] is not True:
            raise ValueError(f"worked example {example_id} must mark its values illustrative")
        if not isinstance(payload["input"], dict) or not isinstance(payload["output"], dict):
            raise ValueError(f"worked example {example_id} input/output must be objects")
        examples.append(
            WorkedExample(
                example_id=example_id,
                filename=filename,
                teaching_points=tuple(teaching_points),
                illustrative_values_only=True,
                input=payload["input"],
                output=payload["output"],
                sha256=canonical_json_sha256(payload),
            )
        )

    semantic_suite = {
        "suite_version": manifest["suite_version"],
        "stage": manifest["stage"],
        "contract_version": manifest["contract_version"],
        "example_order": order,
        "examples": [example.as_payload() for example in examples],
    }
    return WorkedExampleSuite(
        suite_version=manifest["suite_version"],
        stage=manifest["stage"],
        contract_version=manifest["contract_version"],
        manifest_filename="manifest.json",
        examples=tuple(examples),
        sha256=canonical_json_sha256(semantic_suite),
    )


def worked_examples_component(
    suite: WorkedExampleSuite,
    *,
    filename: str,
) -> dict[str, Any]:
    """Build the content-bearing component used in existing prompt manifests."""

    return {
        "filename": filename,
        "sha256": suite.sha256,
        "content": suite.as_payload(),
        "lineage": suite.lineage(),
    }
