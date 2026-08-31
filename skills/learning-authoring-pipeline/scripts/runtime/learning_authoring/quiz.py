"""Quiz prompt package and deterministic KC-to-Quiz input compiler.

The active coding-agent session authors Quiz JSON. This module has no provider
client, request builder, environment credential, or generation command.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.prompt_packages import (
    WorkedExample,
    load_worked_example_suite,
    worked_examples_component,
)
from learning_authoring.quiz_contracts import (
    CURRENT_QUIZ_INPUT_VERSION,
    CURRENT_QUIZ_SCHEMA_VERSION,
    QuizSchemaVersion,
    QuizSourceRef,
    quiz_output_schema,
)
from learning_authoring.source_bundle import SourceBundleKCSet

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT_DIR = PACKAGE_DIR / "prompts" / "quiz-v1"
DEFAULT_EXAMPLES_DIR = DEFAULT_PROMPT_DIR / "examples-v3"
BUNDLE_EXAMPLES_DIR = DEFAULT_PROMPT_DIR / "examples-bundle-v1"
STAGE_VERSION = "quiz-agent-session.v5"
PROMPT_COMPONENTS = ("foundation", "rulebook", "task")
ALLOWED_INTERACTIONS = (
    "single_select",
    "multi_select",
    "matching",
    "ordering",
    "short_text",
)


@dataclass(frozen=True)
class QuizPromptPackage:
    instructions: str
    output_schema: dict[str, Any]
    worked_examples: tuple[WorkedExample, ...]
    manifest: dict[str, Any]

    @property
    def lineage(self) -> dict[str, Any]:
        return self.manifest


@dataclass(frozen=True)
class QuizConfig:
    selected_kc_ids: tuple[str, ...] = ()
    include_all_kcs: bool = False
    variants_per_kc: int | None = None
    min_slots_per_kc: int = 1
    max_slots_per_kc: int | None = None
    variants_per_slot: int | None = None
    max_variants_per_slot: int | None = None
    total_question_budget: int | None = None
    allowed_interactions: tuple[str, ...] = ALLOWED_INTERACTIONS
    language: str = "source"

    def validate(self) -> None:
        if self.min_slots_per_kc is None:
            raise ValueError("min_slots_per_kc must be at least 1")
        if self.include_all_kcs and self.selected_kc_ids:
            raise ValueError("include_all_kcs cannot be combined with selected KC IDs")
        if not self.include_all_kcs and not self.selected_kc_ids:
            raise ValueError("select at least one KC or enable include_all_kcs")
        if len(self.selected_kc_ids) != len(set(self.selected_kc_ids)):
            raise ValueError("selected KC IDs must be unique")
        for name in (
            "variants_per_kc",
            "min_slots_per_kc",
            "max_slots_per_kc",
            "variants_per_slot",
            "max_variants_per_slot",
            "total_question_budget",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise ValueError(f"{name} must be a positive integer when supplied")
        if self.max_slots_per_kc is not None and self.max_slots_per_kc < self.min_slots_per_kc:
            raise ValueError("max_slots_per_kc must be at least min_slots_per_kc")
        if (
            self.variants_per_slot is not None
            and self.max_variants_per_slot is not None
            and self.variants_per_slot > self.max_variants_per_slot
        ):
            raise ValueError("variants_per_slot exceeds max_variants_per_slot")
        if self.variants_per_kc is not None and (
            self.min_slots_per_kc != 1
            or self.max_slots_per_kc is not None
            or self.variants_per_slot is not None
            or self.max_variants_per_slot is not None
        ):
            raise ValueError(
                "legacy variants_per_kc cannot be combined with assessment-slot limits"
            )
        if not self.allowed_interactions or set(self.allowed_interactions) - set(
            ALLOWED_INTERACTIONS
        ):
            raise ValueError("allowed_interactions contains an unsupported value")


def load_quiz_prompt_package(
    prompt_dir: Path = DEFAULT_PROMPT_DIR,
    *,
    schema_version: QuizSchemaVersion = CURRENT_QUIZ_SCHEMA_VERSION,
    examples_dir: Path = DEFAULT_EXAMPLES_DIR,
) -> QuizPromptPackage:
    texts = {
        component: (prompt_dir / f"{component}.md").read_text(encoding="utf-8")
        for component in PROMPT_COMPONENTS
    }
    output_schema = quiz_output_schema(schema_version)
    instructions = "\n\n".join(texts[component] for component in PROMPT_COMPONENTS)
    components: dict[str, Any] = {
        component: {
            "filename": f"{component}.md",
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "content": content,
        }
        for component, content in texts.items()
    }
    schema_bytes = json.dumps(output_schema, ensure_ascii=False, sort_keys=True).encode()
    components["output_schema"] = {
        "source": "learning_authoring.quiz_contracts.quiz_output_schema",
        "schema_version": schema_version,
        "sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "content": output_schema,
    }
    suite = None
    if schema_version == "quiz-batch.v3":
        suite = load_worked_example_suite(
            examples_dir,
            expected_stage="quiz",
            expected_contract_version="quiz-batch.v3",
        )
        components["worked_examples"] = worked_examples_component(
            suite,
            filename=f"{examples_dir.name}/manifest.json",
        )
    package_bytes = json.dumps(components, ensure_ascii=False, sort_keys=True).encode()
    return QuizPromptPackage(
        instructions=instructions,
        output_schema=output_schema,
        worked_examples=suite.examples if suite is not None else (),
        manifest={
            "package_version": STAGE_VERSION,
            "instruction_order": list(PROMPT_COMPONENTS),
            "structured_output_component": "output_schema",
            "worked_examples_component": "worked_examples" if suite is not None else None,
            "worked_example_order": list(suite.example_order) if suite is not None else [],
            "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
            "components": components,
        },
    )


def build_quiz_input(
    kc_set: ProposedKCSet | SourceBundleKCSet,
    *,
    kc_set_sha256: str,
    config: QuizConfig,
    raw_kc_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile selected original KCs and code-owned assessment constraints."""

    config.validate()
    if raw_kc_set is None:
        original = kc_set.model_dump(mode="json")
    else:
        contract = SourceBundleKCSet if isinstance(kc_set, SourceBundleKCSet) else ProposedKCSet
        if contract.model_validate(raw_kc_set) != kc_set:
            raise ValueError("raw KC set does not match the validated KC set")
        original = raw_kc_set
    kc_by_id = {kc.kc_id: kc for kc in kc_set.leaf_kcs}
    original_kc_by_id = {kc["kc_id"]: kc for kc in original["leaf_kcs"]}
    selected_kc_ids = (
        tuple(kc.kc_id for kc in kc_set.leaf_kcs)
        if config.include_all_kcs
        else config.selected_kc_ids
    )
    unknown = set(selected_kc_ids) - set(kc_by_id)
    if unknown:
        raise ValueError(f"selected unknown KC IDs: {sorted(unknown)}")
    if not selected_kc_ids:
        raise ValueError("Quiz requires at least one selected Leaf KC")

    adaptive = config.variants_per_kc is None
    expected_question_count = None if adaptive else len(selected_kc_ids) * config.variants_per_kc
    minimum_question_count = (
        len(selected_kc_ids) * config.min_slots_per_kc * (config.variants_per_slot or 1)
        if adaptive
        else expected_question_count
    )
    if config.total_question_budget is not None and (
        minimum_question_count > config.total_question_budget
    ):
        raise ValueError(
            f"infeasible total_question_budget {config.total_question_budget}: "
            f"covering all {len(selected_kc_ids)} selected KCs with the configured minimum "
            f"requires at least {minimum_question_count} questions; no KCs will be truncated"
        )

    selected_kcs = [kc_by_id[kc_id] for kc_id in selected_kc_ids]
    selected_group_ids = {kc.group_id for kc in selected_kcs}
    selected_groups = [
        deepcopy(group)
        for group in original["kc_groups"]
        if group["group_id"] in selected_group_ids
    ]
    if isinstance(kc_set, SourceBundleKCSet):
        source_ref = QuizSourceRef(
            source_bundle_sha256=kc_set.source_ref.source_bundle_sha256,
            kc_set_sha256=kc_set_sha256,
            authoring_context_sha256=kc_set.source_ref.authoring_context_sha256,
        )
    else:
        source_ref = QuizSourceRef(
            extraction_source_id=kc_set.source_ref.source_id,
            extraction_source_sha256=kc_set.source_ref.source_sha256,
            kc_set_sha256=kc_set_sha256,
            authoring_context_sha256=getattr(kc_set.source_ref, "authoring_context_sha256", None),
        )
    if isinstance(kc_set, SourceBundleKCSet):
        known_source_pages = {(audit.source_id, audit.page) for audit in kc_set.page_audit}
        for kc in selected_kcs:
            for evidence in kc.context_evidence:
                if evidence.pages and evidence.source_id is None:
                    raise ValueError("bundle page-mapped context evidence requires source_id")
                if evidence.source_id is not None and any(
                    (evidence.source_id, page) not in known_source_pages for page in evidence.pages
                ):
                    raise ValueError("bundle context evidence references an unknown source page")
    elif any(
        evidence.source_id is not None for kc in selected_kcs for evidence in kc.context_evidence
    ):
        raise ValueError("single-source context evidence must not add source_id")
    if any(getattr(kc, "context_evidence", []) for kc in selected_kcs) and (
        not source_ref.authoring_context_sha256
    ):
        raise ValueError("selected KC context evidence requires a bound authoring context hash")
    return {
        "input_version": CURRENT_QUIZ_INPUT_VERSION if adaptive else "quiz-input.v1",
        "source_ref": source_ref.model_dump(mode="json"),
        "runtime": {
            "selected_kc_ids": list(selected_kc_ids),
            "assessment_mode": "adaptive_slots" if adaptive else "legacy_per_kc",
            "expected_schema_version": CURRENT_QUIZ_SCHEMA_VERSION if adaptive else "quiz-batch.v1",
            "variants_per_kc": config.variants_per_kc,
            "min_slots_per_kc": config.min_slots_per_kc,
            "max_slots_per_kc": config.max_slots_per_kc,
            "variants_per_slot": config.variants_per_slot,
            "max_variants_per_slot": config.max_variants_per_slot,
            "total_question_budget": config.total_question_budget,
            "expected_question_count": expected_question_count,
            "minimum_question_count": minimum_question_count,
            "allowed_interactions": list(config.allowed_interactions),
            "language": config.language,
        },
        "kc_groups": selected_groups,
        "leaf_kcs": [deepcopy(original_kc_by_id[kc_id]) for kc_id in selected_kc_ids],
        "authoring_batches": [
            {
                "batch_id": f"quiz-group-{index:03d}",
                "group_id": group["group_id"],
                "kc_ids": [
                    kc_id for kc_id in group["leaf_kc_ids"] if kc_id in selected_kc_ids
                ],
            }
            for index, group in enumerate(selected_groups, start=1)
        ],
    }
