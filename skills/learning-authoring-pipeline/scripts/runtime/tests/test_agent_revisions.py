"""Synthetic lifecycle checks, not a course run or a semantic-quality evaluation."""

from pathlib import Path

import pytest

from learning_authoring.agent_session import (
    agent_bundle,
    agent_context,
    agent_import,
    agent_init,
    prepare_agent_task,
)
from learning_authoring.artifacts import read_json, sha256_file, write_json
from learning_authoring.product.showcase import build_showcase
from tests.test_agent_bundle_session import _prepared_bundle
from tests.test_agent_quiz_review import _quiz_run, _review_task
from tests.test_agent_session import _extraction_candidate, _write_raw


def test_upstream_revision_requires_reconciliation_without_discarding_downstream(tmp_path):
    run = _quiz_run(tmp_path)
    unchanged = {
        name: (run / name).read_bytes()
        for name in (
            "source.pdf",
            "native-source.raw.json",
            "kc-proposed.json",
            "quiz/quiz-proposed.json",
        )
    }
    candidate = _extraction_candidate()
    candidate["pages"][0]["blocks"][0]["content"] = "A corrected source transcription."
    path = run / "revised-extraction.json"
    _write_raw(path, candidate)
    agent_import("extraction", run, path)
    state = read_json(run / "revision-state.json")["stages"]
    assert state["extraction"]["status"] == "CURRENT_DRAFT"
    assert state["kc"]["status"] == state["quiz"]["status"] == "NEEDS_RECHECK"
    assert all((run / name).read_bytes() == data for name, data in unchanged.items())
    with pytest.raises(ValueError, match="needs recheck"):
        prepare_agent_task("quiz", run, include_all_kcs=True)
    with pytest.raises(ValueError, match="needs recheck"):
        build_showcase(run, tmp_path / "stale-portal")
    assert not (tmp_path / "stale-portal").exists()

    # Explicitly revalidated identical KC bytes are allowed; Quiz still needs rechecking.
    agent_import("kc", run, run / "kc-proposed.json", allow_proposed_extraction_demo=True)
    state = read_json(run / "revision-state.json")["stages"]
    assert state["kc"]["status"] == "CURRENT_DRAFT"
    assert state["quiz"]["status"] == "NEEDS_RECHECK"
    agent_import("quiz", run, run / "quiz/quiz-proposed.json", include_all_kcs=True)
    assert read_json(run / "revision-state.json")["stages"]["quiz"]["status"] == "CURRENT_DRAFT"
    build_showcase(run, tmp_path / "current-portal")
    assert (tmp_path / "current-portal/index.html").is_file()


def test_quiz_semantic_edits_are_allowed_after_a_valid_contract_and_keep_history(tmp_path):
    run = _quiz_run(tmp_path)
    path = run / "quiz/quiz-proposed.json"
    task = prepare_agent_task("quiz", run, include_all_kcs=True)
    for revision in range(4):
        before, digest = path.read_bytes(), sha256_file(path)
        candidate = read_json(path)
        candidate["questions"][0]["title"] = f"Revised wording {revision}"
        next_path = run / "revised-quiz.json"
        raw = _write_raw(next_path, candidate)
        result = agent_import("quiz", run, next_path, task_package=Path(task["task_package"]))
        archive = run / "agent-session/revisions/quiz" / digest / path.name
        assert archive.read_bytes() == before
        assert path.read_bytes() == raw
        assert read_json(Path(result["import_record"]))["revisions_allowed"] is True
        assert result["approved"] is False


@pytest.mark.parametrize("entrypoint", ["agent_context", "agent_init"])
def test_new_notes_invalidate_kc_and_quiz_but_never_replace_extraction(tmp_path, entrypoint):
    run = _quiz_run(tmp_path)
    before = (run / "extracted-source.proposed.json").read_bytes()
    raw = (run / "native-source.raw.json").read_bytes()
    if entrypoint == "agent_context":
        agent_context(run, context_texts=("New lecturer qualification.",))
    else:
        agent_init(tmp_path / "lesson.pdf", run, context_texts=("New lecturer qualification.",))
    state = read_json(run / "revision-state.json")["stages"]
    assert state["kc"]["status"] == state["quiz"]["status"] == "NEEDS_RECHECK"
    assert (run / "extracted-source.proposed.json").read_bytes() == before
    assert (run / "native-source.raw.json").read_bytes() == raw


def test_delivery_time_review_never_claims_independent_judging(tmp_path):
    run = _quiz_run(tmp_path)
    _, report = _review_task(run, mode="self_review")
    candidate = run / "self-review.json"
    _write_raw(candidate, report)
    result = agent_import("quiz-review", run, candidate)
    assert result["status"] == "REVIEW"
    record = read_json(Path(result["import_record"]))
    assert record["task_binding_mode"] == "delivery_time"
    assert record["prompt_delivery_sha256"] is None


def test_revised_bundle_keeps_history_and_explicitly_rebinds_notes(tmp_path):
    root, prepared, _, _ = _prepared_bundle(tmp_path)
    previous = (root / "source-bundle.json").read_bytes()
    digest = sha256_file(root / "source-bundle.json")
    old_context = read_json(root / "authoring-context.json")
    write_json(root / "kc-proposed.json", {"fixture": "previous KC"})
    write_json(root / "quiz/quiz-proposed.json", {"fixture": "previous Quiz"})
    source_run = prepared[0][0]
    path = source_run / "extracted-source.proposed.json"
    source = read_json(path)
    source["pages"][0]["blocks"][0]["content"] = "Corrected synthetic content."
    write_json(path, source)

    result = agent_bundle(
        root, tuple(run for run, _ in prepared), context_files=(tmp_path / "notes.md",)
    )
    assert (root / "source-bundle-revisions" / f"{digest}.json").read_bytes() == previous
    context = read_json(root / "authoring-context.json")
    assert context["source_ref"]["source_bundle_sha256"] == result["source_bundle_sha256"]
    assert context["items"] == old_context["items"]
    assert (root / "authoring-context/manifests" / f"{old_context['sha256']}.json").is_file()
    state = read_json(root / "revision-state.json")["stages"]
    assert state["kc"]["status"] == state["quiz"]["status"] == "NEEDS_RECHECK"
