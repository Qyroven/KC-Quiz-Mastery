"""Stable artifact names and atomic JSON helpers."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    """Atomically replace one JSON artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_bytes(path: Path, payload: bytes) -> None:
    """Atomically replace one binary artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def write_text(path: Path, payload: str) -> None:
    """Atomically replace one UTF-8 text artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def record_revision_state(root: Path, stage: str, before: str | None, path: Path) -> None:
    state_path = root / "revision-state.json"
    state = read_json(state_path) if state_path.is_file() else {"stages": {}}
    stages = state["stages"]
    stages[stage] = {"status": "CURRENT_DRAFT", "sha256": sha256_file(path)}
    downstream = {
        "extraction": ("kc", "quiz"),
        "context": ("kc", "quiz"),
        "bundle": ("kc", "quiz"),
        "kc": ("quiz",),
        "quiz": (),
    }[stage]
    paths = {"kc": root / "kc-proposed.json", "quiz": root / "quiz" / "quiz-proposed.json"}
    if before != stages[stage]["sha256"]:
        for dependent in downstream:
            if paths[dependent].is_file():
                stages[dependent] = {
                    "status": "NEEDS_RECHECK",
                    "changed_upstream": stage,
                    "sha256": sha256_file(paths[dependent]),
                }
    write_json(state_path, state)


def require_current_revision(root: Path, stage: str) -> None:
    """Do not present a stale draft as current after a recorded upstream revision."""

    path = root / "revision-state.json"
    if (
        path.is_file()
        and read_json(path).get("stages", {}).get(stage, {}).get("status") == "NEEDS_RECHECK"
    ):
        raise ValueError(f"{stage} needs recheck after its upstream source changed")


@dataclass(frozen=True)
class RunArtifacts:
    run_dir: Path

    @property
    def source_pdf(self) -> Path:
        return self.run_dir / "source.pdf"

    @property
    def source_manifest(self) -> Path:
        return self.run_dir / "source-manifest.json"

    @property
    def source_preparation(self) -> Path:
        return self.run_dir / "source-preparation.json"

    @property
    def metadata(self) -> Path:
        return self.run_dir / "extraction-metadata.json"

    @property
    def proposed(self) -> Path:
        return self.run_dir / "extracted-source.proposed.json"

    @property
    def approved(self) -> Path:
        return self.run_dir / "extracted-source.approved.json"

    @property
    def approval(self) -> Path:
        return self.run_dir / "extraction-approval.json"

    @property
    def audit(self) -> Path:
        return self.run_dir / "extraction-audit.json"

    @property
    def metrics(self) -> Path:
        return self.run_dir / "run-metrics.json"

    @property
    def contract_errors(self) -> Path:
        return self.run_dir / "contract-errors.json"

    @property
    def review_html(self) -> Path:
        return self.run_dir / "extraction-review.html"

    @property
    def kc_metadata(self) -> Path:
        return self.run_dir / "kc-generation-metadata.json"

    @property
    def kc_proposed(self) -> Path:
        return self.run_dir / "kc-proposed.json"

    @property
    def kc_contract_errors(self) -> Path:
        return self.run_dir / "kc-contract-errors.json"

    @property
    def kc_metrics(self) -> Path:
        return self.run_dir / "kc-run-metrics.json"

    @property
    def quiz_input(self) -> Path:
        return self.run_dir / "quiz-input.json"

    @property
    def quiz_metadata(self) -> Path:
        return self.run_dir / "quiz-generation-metadata.json"

    @property
    def quiz_raw_output(self) -> Path:
        return self.run_dir / "quiz-output.raw.json"

    @property
    def quiz_proposed(self) -> Path:
        return self.run_dir / "quiz-proposed.json"

    @property
    def quiz_contract_errors(self) -> Path:
        return self.run_dir / "quiz-contract-errors.json"

    @property
    def quiz_metrics(self) -> Path:
        return self.run_dir / "quiz-run-metrics.json"

    @property
    def quiz_form_audit(self) -> Path:
        return self.run_dir / "quiz-form-audit.json"

    @property
    def quiz_review_html(self) -> Path:
        return self.run_dir / "quiz-review.html"
