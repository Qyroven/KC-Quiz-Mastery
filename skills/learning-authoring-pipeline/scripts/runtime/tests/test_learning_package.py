"""Offline export tests over disposable synthetic authoring runs, never real courses."""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest

from learning_authoring.agent_session import agent_import, prepare_agent_task
from learning_authoring.artifacts import read_json, sha256_file
from learning_authoring.product.learning import (
    POLICY_VERSION,
    build_learning_package,
    export_learning_registration,
    render_learning_data,
    render_learning_registration_sql,
    write_learning_data,
)
from learning_authoring.product.review_registration import (
    RegistrationSafetyError,
    prepare_review_registration,
    renderer_payload_sha256,
)
from learning_authoring.product.showcase import (
    DEFAULT_TEMPLATE_DIR,
    PublishSafetyError,
    ReviewFiles,
    _json_assignment,
)
from learning_authoring.quiz_review import build_quiz_review
from tests.test_agent_context_slots import _import_kcs, _init
from tests.test_agent_quiz_review import _import_report, _quiz_run, _review_task
from tests.test_agent_session import _forbid_provider_use, _quiz_candidate, _write_raw
from tests.test_quiz_semantics import _flag


@pytest.fixture
def node():
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js is required for the exact browser renderer hash contract")
    return executable


def _inventory(root: Path):
    return {str(path.relative_to(root)): sha256_file(path)
            for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("notes", [False, True])
def test_package_preserves_complete_raw_content_and_exact_renderer_baselines(
    tmp_path, monkeypatch, node, notes,
):
    _forbid_provider_use(monkeypatch)
    run = _quiz_run(tmp_path, notes=notes)
    task, report = _review_task(run)
    _import_report(run, task, report)
    before = _inventory(run)
    package = build_learning_package(run, node_executable=node)
    assert _inventory(run) == before
    quiz = read_json(run / "quiz/quiz-proposed.json")
    kcs = read_json(run / "kc-proposed.json")
    assert package["questions"] == quiz["questions"]
    assert package["slots"] == quiz["assessment_slots"]
    assert package["kcs"] == kcs["leaf_kcs"]
    assert package["groups"] == kcs["kc_groups"]
    assert package["run_id"] == run.name
    assert package["versions"]["policy_version"] == POLICY_VERSION
    assert package["versions"]["quiz_sha256"] == before["quiz/quiz-proposed.json"]
    assert package["versions"]["kc_sha256"] == before["kc-proposed.json"]
    assert package["versions"]["extraction_sha256"] == before["extracted-source.proposed.json"]
    assert package["practice_only"] is True and package["secure_exam"] is False
    assert str(run) not in json.dumps(package)
    registration = prepare_review_registration(run, node_executable=node)
    baseline_refs = {(t.stage, t.item_type, t.item_key): t.base_artifact_sha256
                     for t in registration.targets}
    for question in package["questions"]:
        meta = package["question_meta"][question["question_id"]]
        assert meta["initial_check_status"] == "PASS"
        assert meta["question_sha256"] == renderer_payload_sha256(question, node_executable=node)
        lineage = meta["lineage"]
        assert lineage["source_sha256"] == package["source"]["source_sha256"]
        assert lineage["policy_version"] == POLICY_VERSION
        assert lineage["authoring_context_sha256"] == package["versions"]["context_sha256"]
        for ref in lineage["review_targets"]:
            assert ref["base_artifact_sha256"] == baseline_refs[
                ref["stage"], ref["item_type"], ref["item_key"]
            ]
        assert any(ref["stage"] == "quiz" for ref in lineage["review_targets"])
        assert any(ref["stage"] == "kc" for ref in lineage["review_targets"])
        assert any(ref["stage"] == "extraction" for ref in lineage["review_targets"]) is not notes
    if notes:
        assert package["kcs"][0]["source_evidence"] == []
        assert package["kcs"][0]["context_evidence"][0]["pages"] == []
        assert package["versions"]["context_sha256"] is not None


def test_initial_checks_remain_per_question_and_never_rewrite_questions(tmp_path, node):
    run = _quiz_run(tmp_path)
    task, report = _review_task(run)
    _flag(report, "hints", "REVIEW", index=1)
    _flag(report, "answerability", "REJECT", index=2)
    _import_report(run, task, report)
    before = _inventory(run)
    package = build_learning_package(run, node_executable=node)
    assert [meta["initial_check_status"] for meta in package["question_meta"].values()] == [
        "PASS", "REVIEW", "REJECT",
    ]
    assert _inventory(run) == before


def test_absent_self_review_and_stale_reports_never_produce_green_evidence(tmp_path, node):
    run = _quiz_run(tmp_path)
    package = build_learning_package(run, node_executable=node)
    assert {meta["initial_check_status"] for meta in package["question_meta"].values()} == {
        "UNCHECKED"
    }
    task, report = _review_task(run, mode="self_review")
    _import_report(run, task, report)
    package = build_learning_package(run, node_executable=node)
    assert all(meta["initial_check_status"] != "PASS" for meta in package["question_meta"].values())
    # Deliberately corrupt only this temporary synthetic report, not source content.
    path = run / "quiz/quiz-semantic-audit.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(PublishSafetyError, match="stale or unbound semantic status"):
        build_learning_package(run, node_executable=node)
    build_quiz_review(run, candidate_dir=run / "quiz")
    package = build_learning_package(run, node_executable=node)
    assert {meta["initial_check_status"] for meta in package["question_meta"].values()} == {"STALE"}


def test_legacy_quiz_keeps_missing_slots_without_fabricating_a_mastery_plan(tmp_path, node):
    run, source = _init(tmp_path)
    _import_kcs(run, source)
    task = prepare_agent_task("quiz", run, include_all_kcs=True, variants_per_kc=1)
    candidate = _quiz_candidate(source, sha256_file(run / "kc-proposed.json"), variants=1)
    path = tmp_path / "legacy-quiz.json"
    _write_raw(path, candidate)
    agent_import("quiz", run, path, task_package=Path(task["task_package"]))
    package = build_learning_package(run, node_executable=node)
    assert package["slots"] == []
    assert package["questions"] == candidate["questions"]
    assert "NULL" in render_learning_registration_sql(package)


def test_safe_data_render_roundtrips_without_executing_content_or_exposing_paths(tmp_path, node):
    run = _quiz_run(tmp_path)
    package = build_learning_package(run, node_executable=node)
    original = copy.deepcopy(package)
    package["questions"][0]["prompt"] = "</script><script>alert('x')</script>&\u2028Tiếng Việt"
    text = render_learning_data(package)
    assert "</script>" not in text and "<script>" not in text
    assert "\u2028" not in text and "\\u2028" in text
    assert _json_assignment(text, "window.LEARNING_DATA=", "test") == package
    data_file = tmp_path / "learning-data.js"
    assert write_learning_data(original, data_file) == data_file
    assert data_file.read_text() == render_learning_data(original)
    with pytest.raises(FileExistsError):
        write_learning_data(original, data_file)
    package["bad"] = {"__proto__": {"polluted": True}}
    with pytest.raises(RegistrationSafetyError, match="__proto__"):
        render_learning_data(package)


def test_registration_is_offline_insert_only_and_requires_existing_review_baseline(
    tmp_path, monkeypatch, node,
):
    _forbid_provider_use(monkeypatch)
    run = _quiz_run(tmp_path)
    before = _inventory(run)
    output = tmp_path / "learning-registration.sql"
    result = export_learning_registration(run, output, node_executable=node)
    assert _inventory(run) == before
    assert result["backend_writes"] == 0
    assert result["sql_sha256"] == sha256_file(output)
    assert result["item_count"] == 3
    sql = output.read_text()
    package = build_learning_package(run, node_executable=node)
    assert sql == render_learning_registration_sql(package)
    assert sql.count("INSERT INTO public.learning_items") == 1
    assert "INSERT INTO public.review_runs" not in sql
    assert not re.search(r"\b(?:UPDATE|DELETE|TRUNCATE|DROP|ON CONFLICT)\b", sql)
    assert "BEGIN;\n" in sql and sql.endswith("COMMIT;\n")
    assert "question_payload, lineage" in sql
    assert "answer_explanation" in sql  # Deliberate key-bearing, administrator-only export.
    assert str(run) not in sql


def test_registration_escapes_literals_and_refuses_existing_run_or_portal_paths(tmp_path, node):
    run = _quiz_run(tmp_path)
    package = build_learning_package(run, node_executable=node)
    package["run_id"] = "run'); SELECT 1; --\\name"
    assert "E'run''); SELECT 1; --\\\\name'" in render_learning_registration_sql(package)
    with pytest.raises(RegistrationSafetyError, match="outside the immutable run"):
        export_learning_registration(run, run / "learning.sql", node_executable=node)
    existing = tmp_path / "existing.sql"
    existing.write_text("unchanged prior SQL")
    with pytest.raises(RegistrationSafetyError, match="already exists"):
        export_learning_registration(run, existing, node_executable=node)
    assert existing.read_text() == "unchanged prior SQL"
    portal = tmp_path / "portal"
    portal.mkdir()
    (portal / "showcase-manifest.json").write_text("{}")
    with pytest.raises(RegistrationSafetyError, match="static portal"):
        export_learning_registration(run, portal / "learning.sql", node_executable=node)
    link = tmp_path / "output-link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(PublishSafetyError, match="Symlink"):
        export_learning_registration(run, link / "learning.sql", node_executable=node)


def test_missing_node_or_changed_renderer_fails_with_actionable_error(tmp_path, monkeypatch, node):
    run = _quiz_run(tmp_path)
    monkeypatch.setattr(
        "learning_authoring.product.review_registration.shutil.which", lambda _: None
    )
    with pytest.raises(RegistrationSafetyError, match="Node.js is required"):
        build_learning_package(run)
    runtime = tmp_path / "changed-runtime.js"
    runtime.write_text((DEFAULT_TEMPLATE_DIR / "review-runtime.js").read_text().replace(
        "function canonical(value)", "function changedCanonical(value)"
    ))
    with pytest.raises(RegistrationSafetyError, match="canonical hash changed"):
        build_learning_package(run, node_executable=node, runtime_path=runtime)


def test_custom_review_names_are_explicit_and_hashed(tmp_path, node):
    run = _quiz_run(tmp_path)
    before = build_learning_package(run, node_executable=node)
    custom = ReviewFiles("extract.html", "recall.html", "scroll.html", "quiz-custom.html")
    for old, new in zip(
        ("extraction-review.html", "kc-recall.html", "kc-scroll.html", "quiz-review.html"),
        (custom.extractor, custom.kc_recall, custom.kc_scroll, custom.quiz), strict=True,
    ):
        (run / old).rename(run / new)
    assert build_learning_package(run, node_executable=node, review_files=custom) == before
