"""Worked-example package mechanics; these examples are not quality holdouts."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from learning_authoring.agent_session import _bundle_kc_prompt_fields
from learning_authoring.authoring_context import AuthoringContext
from learning_authoring.contracts import ExtractedSource, ExtractedSourcePayload, SourceDescriptor
from learning_authoring.extraction_prompt import load_extraction_prompt_package
from learning_authoring.kc import load_prompt_package
from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.prompt_packages import (
    canonical_json_sha256,
    load_worked_example_suite,
)
from learning_authoring.quiz import (
    BUNDLE_EXAMPLES_DIR as BUNDLE_QUIZ_EXAMPLES_DIR,
)
from learning_authoring.quiz import (
    load_quiz_prompt_package,
)
from learning_authoring.quiz_contracts import QuizBatchV3
from learning_authoring.quiz_semantics import (
    BUNDLE_EXAMPLES_DIR as BUNDLE_REVIEW_EXAMPLES_DIR,
)
from learning_authoring.quiz_semantics import (
    load_semantic_review_prompt_package,
    semantic_audit_summary,
    validate_semantic_audit,
)
from learning_authoring.source_bundle import SourceBundle, SourceBundleKCSet


def _packages():
    return (
        load_extraction_prompt_package(),
        load_prompt_package(),
        load_quiz_prompt_package(),
        load_semantic_review_prompt_package(),
    )


def _bundle_packages():
    return (
        load_quiz_prompt_package(examples_dir=BUNDLE_QUIZ_EXAMPLES_DIR),
        load_semantic_review_prompt_package(examples_dir=BUNDLE_REVIEW_EXAMPLES_DIR),
    )


def _assert_package_lineage(package) -> None:
    assert package.lineage == package.manifest
    component = package.manifest["components"]["worked_examples"]
    assert package.manifest["worked_example_order"] == [
        example.example_id for example in package.worked_examples
    ]
    assert component["sha256"] == component["lineage"]["suite_sha256"]
    assert component["content"] == [example.as_payload() for example in package.worked_examples]
    encoded = json.dumps(
        package.manifest["components"], ensure_ascii=False, sort_keys=True
    ).encode()
    assert package.manifest["package_sha256"] == hashlib.sha256(encoded).hexdigest()


def test_stage_packages_expose_examples_and_content_bound_lineage() -> None:
    for package in (*_packages(), *_bundle_packages()):
        _assert_package_lineage(package)

    legacy_quiz = load_quiz_prompt_package(schema_version="quiz-batch.v1")
    assert legacy_quiz.worked_examples == ()
    assert legacy_quiz.manifest["worked_example_order"] == []
    assert "worked_examples" not in legacy_quiz.manifest["components"]


def test_every_shipped_prompt_asset_is_read_by_a_stage_loader(monkeypatch) -> None:
    prompt_root = Path(__file__).parents[1] / "learning_authoring/prompts"
    accessed: set[Path] = set()
    original_open = Path.open

    def record_open(path, *args, **kwargs):
        resolved = path.resolve()
        if resolved.is_relative_to(prompt_root.resolve()):
            accessed.add(resolved)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", record_open)
    _packages()
    _bundle_packages()
    _bundle_kc_prompt_fields(SourceBundleKCSet.model_json_schema())

    shipped = {path.resolve() for path in prompt_root.rglob("*") if path.is_file()}
    assert shipped == accessed, {
        "unused_assets": sorted(str(path.relative_to(prompt_root)) for path in shipped - accessed),
        "unshipped_assets": sorted(str(path) for path in accessed - shipped),
    }


def test_official_prompt_and_example_packages_are_neutral_to_day09_holdout() -> None:
    for package in (*_packages(), *_bundle_packages()):
        serialized = json.dumps(package.manifest, ensure_ascii=False).casefold()
        for source_specific_pattern in (
            r"(?<![a-z0-9])day\s*0?9(?![a-z0-9])",
            r"(?<![a-z0-9])mcp(?![a-z0-9])",
            r"(?<![a-z0-9])a2a(?![a-z0-9])",
        ):
            assert re.search(source_specific_pattern, serialized) is None


def test_canonical_hash_ignores_json_formatting_but_not_semantic_changes(
    tmp_path: Path,
) -> None:
    assert canonical_json_sha256({"b": 2, "a": [1]}) == canonical_json_sha256({"a": [1], "b": 2})
    assert canonical_json_sha256({"a": [1]}) != canonical_json_sha256({"a": [2]})

    source = Path(__file__).parents[1] / "learning_authoring/prompts/extractor-v2/examples-v1"
    copied = tmp_path / "examples"
    shutil.copytree(source, copied)
    first = load_worked_example_suite(
        copied,
        expected_stage="extraction",
        expected_contract_version="extracted-source.v2",
    )
    example_path = copied / "neutral-structure.json"
    payload = json.loads(example_path.read_text(encoding="utf-8"))
    example_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    reformatted = load_worked_example_suite(
        copied,
        expected_stage="extraction",
        expected_contract_version="extracted-source.v2",
    )
    assert reformatted.sha256 == first.sha256
    payload["input"]["visible_pages"][0]["visible_elements"][0] = "Marker Z"
    example_path.write_text(json.dumps(payload), encoding="utf-8")
    changed = load_worked_example_suite(
        copied,
        expected_stage="extraction",
        expected_contract_version="extracted-source.v2",
    )
    assert changed.sha256 != first.sha256


def test_manifest_order_is_authoritative_and_paths_cannot_escape(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "learning_authoring/prompts/kc-v1/examples-v1"
    copied = tmp_path / "examples"
    shutil.copytree(source, copied)
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["examples"]["primary-plus-context"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid worked-example filename"):
        load_worked_example_suite(
            copied,
            expected_stage="kc",
            expected_contract_version="proposed-kc-set.v1",
        )


def test_extraction_worked_example_is_contract_valid() -> None:
    example = load_extraction_prompt_package().worked_examples[0]
    source = SourceDescriptor.model_validate(example.input["source"])
    extracted = ExtractedSourcePayload.model_validate(example.output).with_source(source)
    assert extracted.source == source
    assert extracted.pages[0].blocks[0].relations[0].target_block_id == "marker-b"
    connector = next(block for block in extracted.pages[0].blocks if block.kind == "connector")
    assert connector.region.localization_status == "located"
    assert connector.region.geometry is not None
    assert connector.region.geometry["w"] > connector.region.geometry["h"] > 0
    connector_relations = {
        (relation.relation_type, relation.target_block_id) for relation in connector.relations
    }
    assert connector_relations == {
        ("starts_at", "marker-a"),
        ("ends_at", "marker-b"),
    }
    assert extracted.pages[0].warnings[0].block_ids == ["arrow-a-b"]


def test_kc_worked_example_validates_primary_and_context_lineage() -> None:
    example = next(
        example
        for example in load_prompt_package().worked_examples
        if example.example_id == "primary-plus-context"
    )
    extraction = ExtractedSource.model_validate(example.input["extraction"])
    context = AuthoringContext.model_validate(example.input["authoring_context"])
    proposed = ProposedKCSet.model_validate(example.output)
    proposed.validate_against_source(
        extraction,
        authoring_context=context,
        require_context_audit=True,
    )
    assert [item.context_id for item in context.items] == ["CTX-001", "CTX-002"]
    assert proposed.leaf_kcs[0].source_evidence
    assert [evidence.context_id for evidence in proposed.leaf_kcs[0].context_evidence] == [
        "CTX-001"
    ]
    assert {
        entry.context_id: (entry.disposition, entry.kc_ids) for entry in proposed.context_audit
    } == {
        "CTX-001": ("represented", ["KC-001"]),
        "CTX-002": ("not_assessed", []),
    }


def test_kc_worked_examples_balance_independent_split_and_qualified_merge() -> None:
    singular = load_prompt_package()
    singular_counts = {
        example.example_id: len(example.output["leaf_kcs"])
        for example in singular.worked_examples
    }
    assert singular_counts["independent-capabilities"] == 2
    assert singular_counts["primary-plus-context"] == 1

    split = next(
        example
        for example in singular.worked_examples
        if example.example_id == "independent-capabilities"
    )
    extraction = ExtractedSource.model_validate(split.input["extraction"])
    proposed = ProposedKCSet.model_validate(split.output)
    proposed.validate_against_source(extraction)

    instructions, fields = _bundle_kc_prompt_fields(SourceBundleKCSet.model_json_schema())
    bundle_counts = {
        example["example_id"]: len(example["output"]["leaf_kcs"])
        for example in fields["worked_examples"]
    }
    assert bundle_counts["qualified-independent-capabilities"] == 2
    assert bundle_counts["qualified-merge-with-context"] == 1
    assert "smallest source-supported capability" in instructions.casefold()

    bundle_split = next(
        example
        for example in fields["worked_examples"]
        if example["example_id"] == "qualified-independent-capabilities"
    )
    bundle = SourceBundle.model_validate(bundle_split["input"]["source_bundle"])
    extractions = {
        item["source"]["source_id"]: ExtractedSource.model_validate(item)
        for item in bundle_split["input"]["payload"]
    }
    bundle_proposed = SourceBundleKCSet.model_validate(bundle_split["output"])
    bundle_proposed.validate_against_bundle(bundle, extractions)

    assert "smallest source-supported capability" in singular.instructions.casefold()
    teaching_points = " ".join(
        point for example in singular.worked_examples for point in example.teaching_points
    ).casefold()
    assert "target kc count" not in teaching_points


def test_quiz_worked_example_validates_slots_hints_and_frozen_input() -> None:
    example = next(
        example
        for example in load_quiz_prompt_package().worked_examples
        if example.example_id == "adaptive-slot-with-hint"
    )
    quiz = QuizBatchV3.model_validate(example.output, strict=True)
    quiz.validate_against_input(example.input)
    assert quiz.assessment_slots[0].variant_count == len(quiz.questions) == 2
    assert {question.slot_id for question in quiz.questions} == {quiz.assessment_slots[0].slot_id}
    assert {question.interaction for question in quiz.questions} == {"matching"}
    assert {question.prompt for question in quiz.questions} == {
        "Match each variable to its measurement scale."
    }
    for question in quiz.questions:
        assert len(question.matching_left) == len(question.correct_answer.mappings) == 4
        assert [item.option_id for item in question.matching_right] != [
            mapping.right for mapping in question.correct_answer.mappings
        ]
    assert [hint.kind for hint in quiz.questions[0].hints] == ["strategy"]
    assert quiz.questions[0].hint_absence_reason is None
    assert [hint.kind for hint in quiz.questions[1].hints] == ["cue"]
    assert quiz.questions[1].hint_absence_reason is None


def test_quiz_near_miss_example_has_parallel_options_and_honest_calibration() -> None:
    example = next(
        example
        for example in load_quiz_prompt_package().worked_examples
        if example.example_id == "parallel-near-miss-distractors"
    )
    quiz = QuizBatchV3.model_validate(example.output, strict=True)
    quiz.validate_against_input(example.input)

    assert len(quiz.assessment_slots) == len(quiz.questions) == 1
    slot = quiz.assessment_slots[0]
    assert slot.cognitive_operation == "apply"
    assert slot.intended_difficulty == "medium"
    assert "rule is supplied" in slot.justification.casefold()

    question = quiz.questions[0]
    option_texts = [option.text for option in question.choice_options]
    option_lengths = [len(text) for text in option_texts]
    assert all(text.startswith("Assign ") and " because " in text for text in option_texts)
    assert max(option_lengths) - min(option_lengths) <= 8
    keyed_option = next(
        option
        for option in question.choice_options
        if option.option_id == question.correct_answer.selection_ids[0]
    )
    assert keyed_option.text == (
        "Assign Tier Amber because one of the three required checks fails."
    )
    assert all("near-miss" not in text.casefold() for text in option_texts)
    assert "near-miss" in example.teaching_points[0].casefold()


def test_quiz_constructed_example_measures_produced_work_without_domain_leakage() -> None:
    example = next(
        example
        for example in load_quiz_prompt_package().worked_examples
        if example.example_id == "constructed-evidence-not-recognition"
    )
    quiz = QuizBatchV3.model_validate(example.output, strict=True)
    quiz.validate_against_input(example.input)

    assert len(quiz.assessment_slots) == len(quiz.questions) == 1
    question = quiz.questions[0]
    assert question.interaction == "short_text"
    assert question.correct_answer.text
    assert len(question.rubric) == 3
    assert question.hints == []
    assert question.hint_absence_reason
    serialized = json.dumps(example.as_payload(), ensure_ascii=False).casefold()
    for leaked_term in ("worker", "idempot", "deduplic", "retry", "multi-agent"):
        assert leaked_term not in serialized


def test_bundle_quiz_worked_example_is_source_qualified_and_contract_valid() -> None:
    package = load_quiz_prompt_package(examples_dir=BUNDLE_QUIZ_EXAMPLES_DIR)
    example = next(
        example
        for example in package.worked_examples
        if example.example_id == "source-qualified-bundle-slot"
    )
    assert example.example_id == "source-qualified-bundle-slot"
    assert package.manifest["components"]["worked_examples"]["filename"] == (
        "examples-bundle-v1/manifest.json"
    )
    for source_ref in (example.input["source_ref"], example.output["source_ref"]):
        assert source_ref["source_bundle_sha256"]
        assert "extraction_source_id" not in source_ref
        assert "extraction_source_sha256" not in source_ref

    quiz = QuizBatchV3.model_validate(example.output, strict=True)
    quiz.validate_against_input(example.input)
    assert all(
        reference.source_id for question in quiz.questions for reference in question.evidence_refs
    )
    assert all(
        reference.source_id
        for question in quiz.questions
        for reference in question.context_evidence_refs
        if reference.pages
    )

    legacy = next(
        example
        for example in load_quiz_prompt_package().worked_examples
        if example.example_id == "adaptive-slot-with-hint"
    )
    assert legacy.example_id == "adaptive-slot-with-hint"
    assert legacy.input["source_ref"]["extraction_source_id"]
    assert "source_bundle_sha256" not in legacy.input["source_ref"]


def test_bundle_quiz_near_miss_example_preserves_lineage_and_calibration() -> None:
    package = load_quiz_prompt_package(examples_dir=BUNDLE_QUIZ_EXAMPLES_DIR)
    example = next(
        example
        for example in package.worked_examples
        if example.example_id == "source-qualified-near-miss-distractors"
    )
    input_source_ref = example.input["source_ref"]
    output_source_ref = example.output["source_ref"]
    assert input_source_ref["source_bundle_sha256"] == output_source_ref["source_bundle_sha256"]
    for source_ref in (input_source_ref, output_source_ref):
        assert source_ref["source_bundle_sha256"]
        assert "extraction_source_id" not in source_ref
        assert "extraction_source_sha256" not in source_ref

    quiz = QuizBatchV3.model_validate(example.output, strict=True)
    quiz.validate_against_input(example.input)
    assert len(quiz.assessment_slots) == len(quiz.questions) == 1
    slot = quiz.assessment_slots[0]
    assert slot.cognitive_operation == "apply"
    assert slot.intended_difficulty == "medium"
    assert "rule is supplied" in slot.justification.casefold()

    input_source_ids = {
        evidence["source_id"]
        for leaf_kc in example.input["leaf_kcs"]
        for evidence in leaf_kc["source_evidence"]
    }
    question = quiz.questions[0]
    question_source_ids = {reference.source_id for reference in question.evidence_refs}
    assert question_source_ids == input_source_ids
    assert None not in question_source_ids

    option_texts = [option.text for option in question.choice_options]
    option_lengths = [len(text) for text in option_texts]
    assert all(text.startswith("Assign ") and " because " in text for text in option_texts)
    assert max(option_lengths) - min(option_lengths) <= 8
    keyed_option = next(
        option
        for option in question.choice_options
        if option.option_id == question.correct_answer.selection_ids[0]
    )
    assert len(keyed_option.text) < max(option_lengths)
    assert "near-miss" in example.teaching_points[1].casefold()


def test_review_worked_example_validates_bound_snapshots_and_pointer() -> None:
    example = load_semantic_review_prompt_package().worked_examples[0]
    artifacts = example.input["artifacts"]
    quiz = QuizBatchV3.model_validate(artifacts["quiz"], strict=True)
    report = validate_semantic_audit(
        example.output,
        quiz=quiz,
        expected_source_ref=example.input["expected_source_ref"],
        artifacts=artifacts,
        expected_reviewer=example.input["reviewer_mode"],
    )
    summary = semantic_audit_summary(
        report,
        quiz=quiz,
        expected_source_ref=example.input["expected_source_ref"],
    )
    assert summary["status"] == "REVIEW"
    answerability_locators = {
        (locator.artifact, locator.pointer)
        for locator in report.questions[0].answerability.issues[0].locators
    }
    assert answerability_locators == {
        ("quiz", "/questions/0/stimulus/text"),
        ("quiz", "/questions/0/prompt"),
        ("extraction", "/pages/0/blocks/0/content"),
    }
    scoring_locators = {
        (locator.artifact, locator.pointer)
        for locator in report.questions[0].scoring.issues[0].locators
    }
    assert scoring_locators == {
        ("quiz", "/questions/0/correct_answer/selection_ids/0"),
        ("extraction", "/pages/0/blocks/0/content"),
    }
    assert report.questions[0].scoring.verdict == "REVIEW"


def test_bundle_review_worked_example_validates_qualified_pages_and_locators() -> None:
    package = load_semantic_review_prompt_package(examples_dir=BUNDLE_REVIEW_EXAMPLES_DIR)
    example = package.worked_examples[0]
    assert example.example_id == "source-qualified-bundle-review"
    assert package.manifest["components"]["worked_examples"]["filename"] == (
        "examples-bundle-v1/manifest.json"
    )
    artifacts = example.input["artifacts"]
    quiz = QuizBatchV3.model_validate(artifacts["quiz"], strict=True)
    report = validate_semantic_audit(
        example.output,
        quiz=quiz,
        expected_source_ref=example.input["expected_source_ref"],
        artifacts=artifacts,
        expected_reviewer=example.input["reviewer_mode"],
    )
    assert report.source_ref.source_sha256 is None
    assert report.source_ref.source_bundle_sha256
    assert all(page.source_id for page in report.scope.checked_source_pages)
    extraction_locators = [
        locator
        for question in report.questions
        for criterion in (
            question.grounding,
            question.answerability,
            question.alignment,
            question.scoring,
            question.cues_and_variants,
            question.hints,
        )
        for issue in criterion.issues
        for locator in issue.locators
        if locator.artifact == "extraction"
    ]
    assert extraction_locators
    assert all(locator.source_id for locator in extraction_locators)

    legacy = load_semantic_review_prompt_package().worked_examples[0]
    assert legacy.example_id == "source-bound-review"
    assert legacy.input["expected_source_ref"]["source_sha256"]
    assert "source_bundle_sha256" not in legacy.input["expected_source_ref"]


def test_worked_examples_are_compact_neutral_assets_not_hidden_holdouts() -> None:
    serialized = json.dumps(
        [
            example.as_payload()
            for package in (*_packages(), *_bundle_packages())
            for example in package.worked_examples
        ],
        ensure_ascii=False,
    ).lower()
    for forbidden in (
        "/users/",
        "day01",
        "openai",
        "anthropic",
        "http://",
        "https://",
        "sk-proj-",
        "runs/",
    ):
        assert forbidden not in serialized
    for package in (*_packages(), *_bundle_packages()):
        for example in package.worked_examples:
            assert example.illustrative_values_only is True
            assert "illustrative" in example.teaching_points[-1].casefold()
            assert len(json.dumps(example.as_payload(), ensure_ascii=False)) < 20_000


def test_exemplar_identifiers_and_content_are_not_stage_defaults() -> None:
    """Synthetic values are examples only, never hidden defaults in stage instructions."""

    sentinels = {
        "neutral-structure",
        "primary-plus-context",
        "adaptive-slot-with-hint",
        "parallel-near-miss-distractors",
        "source-qualified-near-miss-distractors",
        "source-bound-review",
        "neutral-source.pdf",
        "Marker A",
        "Signal Ember",
        "Indicator L",
        "Transition X",
        "Tray North",
        "Tier Amber",
    }
    for package in (*_packages(), *_bundle_packages()):
        instructions = package.instructions.casefold()
        for sentinel in sentinels:
            assert sentinel.casefold() not in instructions
