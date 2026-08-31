from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from learning_authoring.agent_session import (
    EXECUTION_MODE,
    agent_import,
    agent_init,
    agent_schema,
    prepare_agent_task,
)
from learning_authoring.artifacts import RunArtifacts, read_json, sha256_file
from learning_authoring.cli import _parser
from learning_authoring.contracts import ExtractedSource
from tests.conftest import write_blank_pdf


def _write_raw(path: Path, payload: dict) -> bytes:
    raw = (json.dumps(payload, ensure_ascii=False, indent=3) + "  \n").encode()
    path.write_bytes(raw)
    return raw


def _extraction_candidate() -> dict:
    return {
        "schema_version": "extracted-source.v2",
        "pages": [
            {
                "page_number": 1,
                "role": "lesson",
                "blocks": [
                    {
                        "block_id": "b1",
                        "kind": "text",
                        "content": "Source content",
                        "region": {
                            "page": 1,
                            "coordinate_system": "normalized_top_left",
                            "localization_status": "located",
                            "geometry": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.2},
                        },
                        "asset_refs": ["pages/page-0001.png"],
                        "relations": [],
                        "attributes": {},
                        "uncertainties": [],
                    }
                ],
                "reading_order": ["b1"],
                "page_note": {
                    "summary": "One source page",
                    "explanation": None,
                    "key_takeaways": [],
                    "evidence_block_ids": ["b1"],
                    "uncertainties": [],
                },
                "warnings": [],
            }
        ],
        "cross_page_relations": [],
        "warnings": [],
    }


def _kc_candidate(source: ExtractedSource) -> dict:
    return {
        "source_ref": {
            "schema_version": source.schema_version,
            "source_id": source.source.source_id,
            "source_sha256": source.source.sha256,
        },
        "source_summary": "One-page lesson",
        "page_audit": [
            {
                "page": 1,
                "classification": "learning_content",
                "summary": "One concept",
                "kc_ids": ["KC-001"],
                "source_block_ids": ["b1"],
                "warning_codes": [],
            }
        ],
        "kc_groups": [
            {
                "group_id": "KCG-001",
                "name": "Concepts",
                "description": "Concept group",
                "leaf_kc_ids": ["KC-001"],
            }
        ],
        "leaf_kcs": [
            {
                "kc_id": "KC-001",
                "group_id": "KCG-001",
                "name": "Source concept",
                "semantic_form": "concept",
                "knowledge_description": "The concept shown in the source.",
                "observable_claim": "Given the source, the learner can explain the concept.",
                "assessment_boundary": {"included": ["Meaning"], "excluded": []},
                "source_evidence": [
                    {
                        "evidence_id": "EVD-001",
                        "page": 1,
                        "block_ids": ["b1"],
                        "description": "Visible source content",
                        "supports": "Concept meaning",
                    }
                ],
                "warning_codes": [],
                "status": "PROPOSED",
            }
        ],
        "uncovered_content": [],
        "generation_warnings": [],
    }


def _quiz_candidate(source: ExtractedSource, kc_sha256: str, *, variants: int) -> dict:
    questions = []
    for index in range(1, variants + 1):
        questions.append(
            {
                "question_id": f"Q-{index:03d}",
                "variant_index": index,
                "kc_id": "KC-001",
                "group_id": "KCG-001",
                "title": f"Question {index}",
                "interaction": "single_select",
                "stimulus": {
                    "kind": "text",
                    "text": "A bounded situation.",
                    "table_columns": [],
                    "table_rows": [],
                    "formula": "",
                },
                "prompt": "Choose the best answer.",
                "choice_options": [
                    {"option_id": "A", "text": "First"},
                    {"option_id": "B", "text": "Second"},
                    {"option_id": "C", "text": "Third"},
                    {"option_id": "D", "text": "Fourth"},
                ],
                "matching_left": [],
                "matching_right": [],
                "ordering_options": [],
                "correct_answer": {
                    "selection_ids": ["B"],
                    "ordering": [],
                    "mappings": [],
                    "text": "",
                },
                "rubric": [],
                "answer_explanation": "The second option follows from the KC.",
                "evidence_refs": [{"page": 1, "block_ids": ["b1"]}],
            }
        )
    return {
        "schema_version": "quiz-batch.v1",
        "source_ref": {
            "extraction_source_id": source.source.source_id,
            "extraction_source_sha256": source.source.sha256,
            "kc_set_sha256": kc_sha256,
        },
        "questions": questions,
    }


def _forbid_provider_use(monkeypatch) -> None:
    del monkeypatch
    legacy = Path(__file__).parents[1] / "learning_authoring" / "legacy_api"
    assert not list(legacy.glob("*.py"))
    for module in ("learning_authoring.kc", "learning_authoring.quiz"):
        namespace = __import__(module, fromlist=["*"])
        assert not hasattr(namespace, "build_client")
        assert not hasattr(namespace, "execute_response")


def test_agent_schema_defaults_to_slots_but_still_emits_legacy_contract() -> None:
    assert agent_schema("extraction")["title"] == "ExtractedSourcePayload"
    assert agent_schema("kc")["title"] == "ProposedKCSet"
    assert agent_schema("quiz")["title"] == "QuizBatchV3"
    assert agent_schema("quiz", legacy_quiz=True)["properties"]["schema_version"]["const"] == (
        "quiz-batch.v1"
    )


def test_agent_cli_exposes_portable_task_and_import_runtime_options() -> None:
    task = _parser().parse_args(
        [
            "agent-task",
            "quiz",
            "run",
            "--include-kc",
            "KC-001",
            "--variants-per-kc",
            "3",
        ]
    )
    imported = _parser().parse_args(
        [
            "agent-import",
            "kc",
            "run",
            "candidate.json",
            "--allow-proposed-extraction-demo",
        ]
    )

    assert task.command == "agent-task"
    assert task.include_kc == ["KC-001"] and task.variants_per_kc == 3
    assert task.language == "source"
    assert imported.command == "agent-import"
    assert imported.allow_proposed_extraction_demo is True


def test_agent_extraction_uses_no_provider_and_preserves_exact_candidate_bytes(
    tmp_path, monkeypatch
) -> None:
    _forbid_provider_use(monkeypatch)
    pdf = tmp_path / "lesson.pdf"
    run_dir = tmp_path / "run"
    candidate = tmp_path / "candidate.json"
    write_blank_pdf(pdf)

    initialized = agent_init(pdf, run_dir)
    task = prepare_agent_task("extraction", run_dir)
    raw = _write_raw(candidate, _extraction_candidate())
    imported = agent_import(
        "extraction",
        run_dir,
        candidate,
        task_package=Path(task["task_package"]),
    )

    artifacts = RunArtifacts(run_dir)
    assert initialized["provider_api_calls"] == 0
    task_package = read_json(Path(task["task_package"]))
    assert task_package["instructions"]
    assert task["read_before_authoring"] is True
    rendered = Path(task["agent_readable_task"]).read_text(encoding="utf-8")
    assert task_package["prompt_delivery_sha256"] in rendered
    assert task_package["worked_examples"][0]["example_id"] in rendered
    assert task_package["output_policy"] == {
        "format": "JSON only",
        "human_review_required": True,
        "approval_created_by_import": False,
        "preserve_candidate_bytes": True,
        "semantic_candidate_authorship": "direct_host_agent_reasoning",
        "agent_written_scripts_allowed": True,
        "counts_follow_content_unless_user_constrained": True,
        "source_claims_must_be_grounded": True,
        "revisions_allowed_after_contract_validity": True,
    }
    assert task_package["input_boundary"]["delivery"] == "native_pdf_primary"
    assert task_package["input_boundary"]["page_image_policy"]["batching"] == "agent_selected"
    assert Path(imported["raw_candidate"]).read_bytes() == raw
    assert read_json(artifacts.proposed)["source"] == read_json(artifacts.source_manifest)["source"]
    metrics = read_json(artifacts.metrics)
    assert metrics["execution_mode"] == EXECUTION_MODE
    assert metrics["provider_api_calls"] == 0
    assert metrics["usage"] is None and metrics["usage_available"] is False
    assert metrics["cost_available"] is False
    assert "gateway_reported_cost_usd" not in metrics
    assert metrics["human_review_required"] is True
    assert metrics["approved"] is False
    assert artifacts.review_html.is_file()
    assert "usage unavailable" in artifacts.review_html.read_text(encoding="utf-8")
    assert not (run_dir / "api-response.json").exists()
    assert not (run_dir / "background-checkpoint.json").exists()
    assert not artifacts.approved.exists()
    assert not artifacts.approval.exists()


def test_agent_kc_and_quiz_demo_stay_unapproved_and_preserve_raw_bytes(
    tmp_path, monkeypatch
) -> None:
    _forbid_provider_use(monkeypatch)
    pdf = tmp_path / "lesson.pdf"
    run_dir = tmp_path / "run"
    extraction_path = tmp_path / "extraction.json"
    kc_path = tmp_path / "kc.json"
    quiz_path = tmp_path / "quiz.json"
    write_blank_pdf(pdf)
    agent_init(pdf, run_dir)
    _write_raw(extraction_path, _extraction_candidate())
    extraction_task = prepare_agent_task("extraction", run_dir)
    agent_import(
        "extraction",
        run_dir,
        extraction_path,
        task_package=Path(extraction_task["task_package"]),
    )
    source = ExtractedSource.model_validate(read_json(run_dir / "extracted-source.proposed.json"))

    with pytest.raises(RuntimeError, match="approved extraction"):
        prepare_agent_task("kc", run_dir)
    kc_task = prepare_agent_task(
        "kc",
        run_dir,
        allow_proposed_extraction_demo=True,
    )
    kc_raw = _write_raw(kc_path, _kc_candidate(source))
    kc_import = agent_import(
        "kc",
        run_dir,
        kc_path,
        task_package=Path(kc_task["task_package"]),
    )

    assert Path(kc_import["raw_candidate"]).read_bytes() == kc_raw
    assert kc_import["upstream_extraction_status"] == "PROPOSED_DEMO_ONLY"
    assert (
        read_json(Path(kc_task["task_package"]))["input_boundary"]["upstream_extraction"]["status"]
        == "PROPOSED_DEMO_ONLY"
    )
    assert "PROPOSED DEMO ONLY" in (run_dir / "index.html").read_text(encoding="utf-8")
    assert not (run_dir / "extracted-source.approved.json").exists()
    assert not (run_dir / "extraction-approval.json").exists()

    canonical_kc = run_dir / "kc-proposed.json"
    quiz_task = prepare_agent_task(
        "quiz",
        run_dir,
        selected_kc_ids=("KC-001",),
        variants_per_kc=2,
    )
    quiz_raw = _write_raw(
        quiz_path,
        _quiz_candidate(source, sha256_file(canonical_kc), variants=2),
    )
    quiz_import = agent_import(
        "quiz",
        run_dir,
        quiz_path,
        task_package=Path(quiz_task["task_package"]),
    )

    quiz_artifacts = RunArtifacts(run_dir / "quiz")
    assert (
        read_json(Path(quiz_task["task_package"]))["input_boundary"]["payload"]["runtime"][
            "variants_per_kc"
        ]
        == 2
    )
    assert Path(quiz_import["raw_candidate"]).read_bytes() == quiz_raw
    assert quiz_artifacts.quiz_raw_output.read_bytes() == quiz_raw
    form_audit = read_json(quiz_artifacts.quiz_form_audit)
    assert quiz_import["form_audit"] == str(quiz_artifacts.quiz_form_audit)
    assert (
        quiz_import["quality_revision_recommended"]
        == form_audit["fresh_candidate_guidance"]["recommended"]
    )
    assert (
        quiz_import["quality_revision_trigger_codes"]
        == form_audit["fresh_candidate_guidance"]["trigger_codes"]
    )
    assert (
        quiz_import["next_quality_action"] == form_audit["fresh_candidate_guidance"]["next_action"]
    )
    assert read_json(quiz_artifacts.quiz_metrics)["provider_api_calls"] == 0
    assert read_json(quiz_artifacts.quiz_metrics)["usage"] is None
    assert (run_dir / "quiz-review.html").is_file()
    assert not (run_dir / "quiz/quiz-api-response.json").exists()
    assert not (run_dir / "quiz/quiz-background-checkpoint.json").exists()


def test_invalid_agent_candidate_is_archived_before_contract_rejection(
    tmp_path, monkeypatch
) -> None:
    _forbid_provider_use(monkeypatch)
    pdf = tmp_path / "lesson.pdf"
    run_dir = tmp_path / "run"
    candidate = tmp_path / "invalid.json"
    write_blank_pdf(pdf)
    agent_init(pdf, run_dir)
    task = prepare_agent_task("extraction", run_dir)
    raw = b'{"schema_version":"extracted-source.v2","pages":[]}\n'
    candidate.write_bytes(raw)

    with pytest.raises(ValidationError):
        agent_import(
            "extraction",
            run_dir,
            candidate,
            task_package=Path(task["task_package"]),
        )

    archived = list((run_dir / "agent-session" / "candidates").glob("extraction-*.json"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == raw
    assert read_json(run_dir / "contract-errors.json")["provider_api_calls"] == 0


def test_quiz_form_findings_remain_visible_without_discarding_draft(tmp_path) -> None:
    pdf = tmp_path / "lesson.pdf"
    run_dir = tmp_path / "run"
    write_blank_pdf(pdf)
    agent_init(pdf, run_dir)

    extraction_path = tmp_path / "extraction.json"
    _write_raw(extraction_path, _extraction_candidate())
    extraction_task = prepare_agent_task("extraction", run_dir)
    agent_import(
        "extraction",
        run_dir,
        extraction_path,
        task_package=Path(extraction_task["task_package"]),
    )
    source = ExtractedSource.model_validate(read_json(run_dir / "extracted-source.proposed.json"))

    kc_path = tmp_path / "kc.json"
    _write_raw(kc_path, _kc_candidate(source))
    kc_task = prepare_agent_task("kc", run_dir, allow_proposed_extraction_demo=True)
    agent_import("kc", run_dir, kc_path, task_package=Path(kc_task["task_package"]))

    canonical_kc = run_dir / "kc-proposed.json"
    candidate = _quiz_candidate(source, sha256_file(canonical_kc), variants=1)
    candidate["questions"][0]["choice_options"][1]["text"] = (
        "The uniquely detailed correct option contains far more words than every alternative"
    )
    quiz_path = tmp_path / "quiz.json"
    raw = _write_raw(quiz_path, candidate)
    quiz_task = prepare_agent_task("quiz", run_dir, selected_kc_ids=("KC-001",), variants_per_kc=1)

    result = agent_import(
        "quiz",
        run_dir,
        quiz_path,
        task_package=Path(quiz_task["task_package"]),
    )

    assert result["status"] == "REVIEW"
    assert result["promotion_gate_passed"] is True
    assert "CORRECT_OPTION_LENGTH_CUE" in result["quality_revision_trigger_codes"]
    assert Path(result["proposed"]).read_bytes() == raw
    assert (run_dir / "quiz" / "quiz-output.raw.json").read_bytes() == raw
