from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from learning_authoring.agent_session import (
    _fingerprint,
    _load_task_package,
    agent_bundle,
    agent_context,
    agent_import,
    prepare_agent_task,
)
from learning_authoring.artifacts import read_json, write_json
from learning_authoring.authoring_context import AuthoringContext
from learning_authoring.cli import _parser
from learning_authoring.contracts import ExtractedSource
from learning_authoring.quiz_contracts import QuizBatchV3
from learning_authoring.quiz_review_state import quiz_review_material
from learning_authoring.quiz_semantics import CRITERIA, validate_semantic_audit
from learning_authoring.source_bundle import (
    SourceBundle,
    SourceBundleKCSet,
    bundle_kc_source_ref,
    load_bundle_extractions,
)
from tests.test_agent_context_slots import _adaptive_candidate
from tests.test_agent_session import _write_raw
from tests.test_source_bundle import _bundled_kc, _source_run


def _prepared_bundle(tmp_path: Path, *, context: str = "Lecturer qualification."):
    root = tmp_path / "bundle"
    prepared = [_source_run(root, name) for name in ("concepts", "exceptions")]
    note = tmp_path / "notes.md"
    note.write_text(context, encoding="utf-8")
    result = agent_bundle(
        root,
        tuple(run for run, _ in prepared),
        context_files=(note,),
    )
    bundle = read_json(root / "source-bundle.json")
    return root, prepared, result, bundle


def _candidate_with_context(root: Path, bundle_json: dict) -> dict:
    from learning_authoring.source_bundle import SourceBundle

    bundle = SourceBundle.model_validate(bundle_json)
    extractions = load_bundle_extractions(root, bundle)
    context = read_json(root / "authoring-context.json")
    candidate = _bundled_kc(bundle, extractions)
    candidate["source_ref"] = bundle_kc_source_ref(
        bundle,
        authoring_context_sha256=context["sha256"],
    ).model_dump(mode="json")
    candidate["leaf_kcs"][0]["context_evidence"] = [
        {
            "context_id": "CTX-001",
            "excerpt": "Lecturer qualification.",
            "description": None,
            "supports": "A separate lecturer constraint.",
            "pages": [],
            "mapping_method": "document_level",
            "mapping_confidence": "high",
        }
    ]
    candidate["context_audit"] = [
        {
            "context_id": "CTX-001",
            "excerpt": "Lecturer qualification.",
            "description": None,
            "claim": "Retain the lecturer qualification.",
            "disposition": "represented",
            "kc_ids": ["KC-001"],
            "reason": "It bounds the shared concept.",
        }
    ]
    return candidate


def _bundle_quiz_candidate(root: Path, source, task: dict) -> dict:
    frozen = read_json(Path(task["task_package"]))["input_boundary"]["payload"]
    candidate = _adaptive_candidate(root, source, task)
    candidate["source_ref"] = frozen["source_ref"]
    kc_evidence = frozen["leaf_kcs"][0]["source_evidence"]
    for index, question in enumerate(candidate["questions"]):
        evidence = kc_evidence[index % len(kc_evidence)]
        question["evidence_refs"] = [
            {
                "source_id": evidence["source_id"],
                "page": evidence["page"],
                "block_ids": evidence["block_ids"],
            }
        ]
    return candidate


def _bundle_semantic_report(quiz: dict, source_ref: dict) -> dict:
    pages = {
        (reference["source_id"], reference["page"])
        for question in quiz["questions"]
        for reference in question["evidence_refs"]
    }
    return {
        "schema_version": "quiz-semantic-audit.v1",
        "source_ref": source_ref,
        "reviewer": {"mode": "independent", "label": "bundle-test", "model": None},
        "scope": {
            "source_coverage": "complete",
            "checked_source_pages": [
                {"source_id": source_id, "page": page} for source_id, page in sorted(pages)
            ],
            "checked_context_ids": [],
            "limitations": [],
        },
        "questions": [
            {
                "question_id": question["question_id"],
                "kc_id": question["kc_id"],
                "slot_id": question["slot_id"],
                "independent_answer": "A bounded synthetic answer.",
                **{
                    criterion: {
                        "verdict": "PASS",
                        "rationale": f"Synthetic {criterion} contract check.",
                        "issues": [],
                    }
                    for criterion in CRITERIA
                },
            }
            for question in quiz["questions"]
        ],
    }


def _import_bundle_kc_and_quiz(tmp_path: Path):
    root, prepared, _, bundle_json = _prepared_bundle(tmp_path)
    kc_task = prepare_agent_task("kc", root, allow_proposed_extraction_demo=True)
    kc_candidate = tmp_path / "bundle-kc.json"
    _write_raw(kc_candidate, _candidate_with_context(root, bundle_json))
    agent_import("kc", root, kc_candidate, task_package=Path(kc_task["task_package"]))

    quiz_task = prepare_agent_task("quiz", root, include_all_kcs=True)
    quiz_candidate = _bundle_quiz_candidate(root, prepared[0][1], quiz_task)
    quiz_path = tmp_path / "bundle-quiz.json"
    quiz_raw = _write_raw(quiz_path, quiz_candidate)
    quiz_result = agent_import(
        "quiz",
        root,
        quiz_path,
        task_package=Path(quiz_task["task_package"]),
    )
    return root, bundle_json, quiz_task, quiz_candidate, quiz_raw, quiz_result


def test_bundle_kc_task_and_import_bind_every_source_and_context(tmp_path) -> None:
    root, prepared, result, bundle_json = _prepared_bundle(tmp_path)
    assert result["source_count"] == len(prepared)
    task_result = prepare_agent_task(
        "kc",
        root,
        allow_proposed_extraction_demo=True,
    )
    task_path = Path(task_result["task_package"])
    task = read_json(task_path)

    assert task["task_package_version"] == "agent-task.v3"
    assert task["candidate_contract"]["title"] == "SourceBundleKCSet"
    assert task["worked_examples"]
    assert task["prompt_lineage"]["package_version"] == "kc-source-bundle.v2"
    assert task["worked_examples"][0]["example_id"] == "qualified-independent-capabilities"
    assert "supplies one canonical `extracted-source.v2` JSON document" not in task["instructions"]
    assert "one complete canonical Extraction" in task["instructions"]
    assert "per bundle source" in task["instructions"]
    assert {
        component["filename"]
        for name, component in task["prompt_lineage"]["components"].items()
        if name in {"foundation", "rulebook", "task"}
    } == {
        "bundle-v1/foundation.md",
        "bundle-v1/rulebook.md",
        "bundle-v1/task.md",
    }
    assert task["prompt_lineage"]["package_sha256"]
    assert task["prompt_delivery_sha256"]
    boundary = task["input_boundary"]
    assert boundary["source_bundle"] == bundle_json
    assert len(boundary["payload"]) == len(prepared)
    assert (
        boundary["source_qualification_policy"][
            "context_page_ordinals_may_not_be_projected_across_sources"
        ]
        is True
    )
    assert "Lecturer qualification." in str(boundary["authoring_context"])

    candidate_path = tmp_path / "candidate.json"
    candidate = _candidate_with_context(root, bundle_json)
    raw = _write_raw(candidate_path, candidate)
    imported = agent_import("kc", root, candidate_path, task_package=task_path)

    assert imported["status"] == "PROPOSED"
    assert imported["review"] is None
    assert (root / "kc-proposed.json").read_bytes() == raw
    record = read_json(Path(imported["import_record"]))
    assert record["prompt_delivery_sha256"] == task["prompt_delivery_sha256"]
    assert record["prompt_package_sha256"] == task["prompt_lineage"]["package_sha256"]


def test_bundle_kc_worked_example_matches_real_contracts_and_runtime_boundary(
    tmp_path,
) -> None:
    root, _, _, _ = _prepared_bundle(tmp_path)
    task_result = prepare_agent_task("kc", root, allow_proposed_extraction_demo=True)
    task = read_json(Path(task_result["task_package"]))
    boundary = task["input_boundary"]
    example = next(
        item
        for item in task["worked_examples"]
        if item["example_id"] == "qualified-merge-with-context"
    )
    example_input = example["input"]

    # The example uses the exact semantic input names present at the real task
    # boundary; it omits only run-specific status and filesystem diagnostics.
    semantic_keys = {"source_bundle", "payload", "authoring_context"}
    assert set(example_input) == semantic_keys
    assert semantic_keys < set(boundary)

    runtime_bundle = SourceBundle.model_validate(boundary["source_bundle"])
    runtime_extractions = [ExtractedSource.model_validate(item) for item in boundary["payload"]]
    runtime_context = AuthoringContext.model_validate(boundary["authoring_context"])
    runtime_context.validate_against_bundle(runtime_bundle)
    assert [item.source.source_id for item in runtime_extractions] == [
        entry.source.source_id for entry in runtime_bundle.sources
    ]

    example_bundle = SourceBundle.model_validate(example_input["source_bundle"])
    example_extractions = [
        ExtractedSource.model_validate(item) for item in example_input["payload"]
    ]
    example_context = AuthoringContext.model_validate(example_input["authoring_context"])
    example_context.validate_against_bundle(example_bundle)
    assert [item.source.source_id for item in example_extractions] == [
        entry.source.source_id for entry in example_bundle.sources
    ]

    output = SourceBundleKCSet.model_validate(example["output"])
    output.validate_against_bundle(
        example_bundle,
        {item.source.source_id: item for item in example_extractions},
        example_context,
    )


def test_bundle_quiz_and_semantic_review_complete_agent_native_round_trip(tmp_path) -> None:
    root, bundle_json, quiz_task, quiz, raw, result = _import_bundle_kc_and_quiz(tmp_path)

    quiz_package = read_json(Path(quiz_task["task_package"]))
    frozen = quiz_package["input_boundary"]["payload"]
    assert frozen["source_ref"]["source_bundle_sha256"] == bundle_json["bundle_sha256"]
    quiz_example = quiz_package["worked_examples"][0]
    assert quiz_example["example_id"] == "source-qualified-bundle-slot"
    assert quiz_example["input"]["source_ref"]["source_bundle_sha256"]
    assert "extraction_source_id" not in quiz_example["input"]["source_ref"]
    assert "extraction_source_sha256" not in quiz_example["input"]["source_ref"]
    assert all(
        reference["source_id"]
        for question in quiz_example["output"]["questions"]
        for reference in question["evidence_refs"]
    )
    delivered_quiz_example = QuizBatchV3.model_validate(quiz_example["output"], strict=True)
    delivered_quiz_example.validate_against_input(quiz_example["input"])
    assert Path(result["proposed"]).read_bytes() == raw
    assert all(
        reference["source_id"]
        for question in quiz["questions"]
        for reference in question["evidence_refs"]
    )

    material = quiz_review_material(root)
    assert set(material["artifacts"]["extraction"]) == {
        entry["source"]["source_id"] for entry in bundle_json["sources"]
    }
    review_task = prepare_agent_task("quiz-review", root)
    review_package = read_json(Path(review_task["task_package"]))
    boundary = review_package["input_boundary"]
    review_example = review_package["worked_examples"][0]
    assert review_example["example_id"] == "source-qualified-bundle-review"
    assert review_example["input"]["expected_source_ref"]["source_sha256"] is None
    assert review_example["input"]["expected_source_ref"]["source_bundle_sha256"]
    assert all(
        page["source_id"] for page in review_example["output"]["scope"]["checked_source_pages"]
    )
    delivered_review_quiz = QuizBatchV3.model_validate(
        review_example["input"]["artifacts"]["quiz"], strict=True
    )
    validate_semantic_audit(
        review_example["output"],
        quiz=delivered_review_quiz,
        expected_source_ref=review_example["input"]["expected_source_ref"],
        artifacts=review_example["input"]["artifacts"],
        expected_reviewer=review_example["input"]["reviewer_mode"],
    )
    assert boundary["expected_source_ref"]["source_sha256"] is None
    assert boundary["expected_source_ref"]["source_bundle_sha256"] == (bundle_json["bundle_sha256"])
    report = _bundle_semantic_report(quiz, boundary["expected_source_ref"])
    report_path = tmp_path / "bundle-review.json"
    report_raw = _write_raw(report_path, report)
    reviewed = agent_import(
        "quiz-review",
        root,
        report_path,
        task_package=Path(review_task["task_package"]),
    )
    assert reviewed["status"] == "PASS"
    assert Path(reviewed["report"]).read_bytes() == report_raw


def test_bundle_quiz_import_rejects_cross_source_candidate_lineage(tmp_path) -> None:
    root, prepared, _, bundle_json = _prepared_bundle(tmp_path)
    kc_task = prepare_agent_task("kc", root, allow_proposed_extraction_demo=True)
    kc_path = tmp_path / "bundle-kc.json"
    _write_raw(kc_path, _candidate_with_context(root, bundle_json))
    agent_import("kc", root, kc_path, task_package=Path(kc_task["task_package"]))
    quiz_task = prepare_agent_task("quiz", root, include_all_kcs=True)
    quiz = _bundle_quiz_candidate(root, prepared[0][1], quiz_task)
    quiz["questions"][0]["evidence_refs"][0]["source_id"] = "sha256:foreign-source"
    path = tmp_path / "cross-source-quiz.json"
    _write_raw(path, quiz)
    with pytest.raises(ValueError, match="outside its KC"):
        agent_import("quiz", root, path, task_package=Path(quiz_task["task_package"]))


def test_bundle_quiz_task_rejects_stale_extraction_lineage(tmp_path) -> None:
    root, _, _, bundle_json = _prepared_bundle(tmp_path)
    kc_task = prepare_agent_task("kc", root, allow_proposed_extraction_demo=True)
    kc_path = tmp_path / "bundle-kc.json"
    _write_raw(kc_path, _candidate_with_context(root, bundle_json))
    agent_import("kc", root, kc_path, task_package=Path(kc_task["task_package"]))

    source_entry = bundle_json["sources"][0]
    extraction_path = root / source_entry["extraction_ref"]
    extraction_path.write_bytes(extraction_path.read_bytes() + b" \n")
    with pytest.raises(ValueError, match="Extraction changed"):
        prepare_agent_task("quiz", root, include_all_kcs=True)


def test_bundle_semantic_import_rejects_stale_and_foreign_source_lineage(tmp_path) -> None:
    root, _, _, quiz, _, _ = _import_bundle_kc_and_quiz(tmp_path)
    review_task = prepare_agent_task("quiz-review", root)
    boundary = read_json(Path(review_task["task_package"]))["input_boundary"]
    report = _bundle_semantic_report(quiz, boundary["expected_source_ref"])
    report["questions"][0]["grounding"] = {
        "verdict": "REVIEW",
        "rationale": "The cited source must be inspected.",
        "issues": [
            {
                "stage": "extraction",
                "observation": "A foreign source was cited.",
                "locators": [
                    {
                        "artifact": "extraction",
                        "source_id": "sha256:foreign-source",
                        "pointer": "/pages/0/blocks/0/content",
                        "quote": None,
                    }
                ],
            }
        ],
    }
    bad_path = tmp_path / "foreign-review.json"
    _write_raw(bad_path, report)
    with pytest.raises(ValueError, match="unknown bundle source"):
        agent_import(
            "quiz-review",
            root,
            bad_path,
            task_package=Path(review_task["task_package"]),
        )

    source_entry = read_json(root / "source-bundle.json")["sources"][0]
    extraction_path = root / source_entry["extraction_ref"]
    extraction_path.write_bytes(extraction_path.read_bytes() + b" \n")
    valid_report = _bundle_semantic_report(quiz, boundary["expected_source_ref"])
    stale_path = tmp_path / "stale-review.json"
    _write_raw(stale_path, valid_report)
    with pytest.raises(ValueError, match="Extraction changed"):
        agent_import(
            "quiz-review",
            root,
            stale_path,
            task_package=Path(review_task["task_package"]),
        )


def test_bundle_context_never_projects_unqualified_page_ordinals(tmp_path) -> None:
    root, _, _, bundle_json = _prepared_bundle(tmp_path)
    task = prepare_agent_task("kc", root, allow_proposed_extraction_demo=True)
    candidate = _candidate_with_context(root, bundle_json)
    candidate["leaf_kcs"][0]["context_evidence"][0].update(
        pages=[1],
        mapping_method="semantic_alignment",
    )
    path = tmp_path / "bad-context-page.json"
    _write_raw(path, candidate)
    with pytest.raises(ValueError, match="requires a source_id"):
        agent_import("kc", root, path, task_package=Path(task["task_package"]))


@pytest.mark.parametrize("fault", [None, "source", "page"])
def test_bundle_context_page_mapping_is_source_qualified_and_bounded(
    tmp_path,
    fault,
) -> None:
    root, _, _, bundle_json = _prepared_bundle(tmp_path)
    task = prepare_agent_task("kc", root, allow_proposed_extraction_demo=True)
    candidate = _candidate_with_context(root, bundle_json)
    source = bundle_json["sources"][1]["source"]
    evidence = candidate["leaf_kcs"][0]["context_evidence"][0]
    evidence.update(
        source_id=source["source_id"],
        pages=[1],
        mapping_method="semantic_alignment",
    )
    if fault == "source":
        evidence["source_id"] = "sha256:unknown"
    elif fault == "page":
        evidence["pages"] = [source["page_count"] + 1]
    path = tmp_path / f"context-map-{fault}.json"
    _write_raw(path, candidate)
    if fault is None:
        assert (
            agent_import(
                "kc",
                root,
                path,
                task_package=Path(task["task_package"]),
            )["status"]
            == "PROPOSED"
        )
    else:
        with pytest.raises(ValueError, match="unknown source|unknown page"):
            agent_import("kc", root, path, task_package=Path(task["task_package"]))


def test_changed_bundle_context_rejects_frozen_kc_task(tmp_path) -> None:
    root, _, _, bundle_json = _prepared_bundle(tmp_path)
    task = prepare_agent_task("kc", root, allow_proposed_extraction_demo=True)
    candidate = tmp_path / "candidate.json"
    _write_raw(candidate, _candidate_with_context(root, bundle_json))
    agent_context(root, context_texts=("Different context.",))
    with pytest.raises(ValueError, match="changed after the frozen KC task"):
        agent_import("kc", root, candidate, task_package=Path(task["task_package"]))


def test_v2_task_remains_readable_and_v3_delivery_hash_is_checked(tmp_path) -> None:
    root, _, _, _ = _prepared_bundle(tmp_path)
    result = prepare_agent_task("kc", root, allow_proposed_extraction_demo=True)
    original_path = Path(result["task_package"])
    current = read_json(original_path)

    legacy = {
        key: value
        for key, value in current.items()
        if key
        not in {"task_fingerprint", "worked_examples", "prompt_lineage", "prompt_delivery_sha256"}
    }
    legacy["task_package_version"] = "agent-task.v2"
    legacy_fingerprint = _fingerprint(legacy)
    legacy_path = original_path.parent / f"kc-{legacy_fingerprint}.json"
    write_json(legacy_path, {**legacy, "task_fingerprint": legacy_fingerprint})
    assert _load_task_package("kc", root, legacy_path)["task_package_version"] == "agent-task.v2"

    tampered = deepcopy(current)
    tampered["instructions"] += "\nTampered delivery."
    tampered.pop("task_fingerprint")
    tampered_fingerprint = _fingerprint(tampered)
    tampered_path = original_path.parent / f"kc-{tampered_fingerprint}.json"
    write_json(tampered_path, {**tampered, "task_fingerprint": tampered_fingerprint})
    with pytest.raises(ValueError, match="prompt delivery hash"):
        _load_task_package("kc", root, tampered_path)


def test_cli_exposes_ordered_bundle_finalize_without_fixed_source_count() -> None:
    args = _parser().parse_args(
        [
            "agent-bundle",
            "run",
            "sources/a",
            "sources/b",
            "sources/c",
            "--context-file",
            "notes.md",
            "--context-text",
            "Lecturer message",
        ]
    )
    assert args.source_run == [
        Path("sources/a"),
        Path("sources/b"),
        Path("sources/c"),
    ]
    assert args.context_file == [Path("notes.md")]
    assert args.context_text == ["Lecturer message"]
