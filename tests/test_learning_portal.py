"""Connected MVP packaging checks; synthetic content, not learner validation."""

import json
import shutil

import pytest

from learning_authoring.artifacts import sha256_file
from learning_authoring.product.showcase import (
    LEARNING_GENERATED_FILES,
    LEARNING_TEMPLATE_FILES,
    ReviewBackendConfig,
    _json_assignment,
    build_showcase,
)
from learning_authoring.product_cli import main as product_main
from tests.test_agent_quiz_review import _import_report, _quiz_run, _review_task
from tests.test_agent_session import _forbid_provider_use
from tests.test_review_registration import _inventory

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js baseline hashing")


@pytest.mark.parametrize("notes", [False, True])
@pytest.mark.parametrize("shared", [False, True])
def test_learning_is_connected_allowlisted_and_keeps_authored_outputs(
    tmp_path, monkeypatch, notes, shared,
):
    _forbid_provider_use(monkeypatch)
    run = _quiz_run(tmp_path, notes=notes)
    task, report = _review_task(run)
    _import_report(run, task, report)
    original = _inventory(run)
    output = tmp_path / "portal"
    backend = ReviewBackendConfig(
        "https://abcdefghijklmnopqrst.supabase.co", "sb_publishable_fixture_public_key",
    ) if shared else None

    manifest = build_showcase(run, output, include_learning=True, review_backend=backend)

    assert _inventory(run) == original
    assert manifest["schema_version"] == "learning-authoring-showcase.v4"
    assert manifest["entrypoints"]["learning"] == "learning.html"
    assert manifest["stage_status"]["mastery"] == "PROVISIONAL_EVIDENCE_MVP"
    assert manifest["learning"] == {
        "enabled": True,
        "mode": "shared" if shared else "local_only",
        "policy_version": "evidence-rules.v1",
        "model_provider_calls": 0,
        "calibrated_mastery": False,
        "feedback_changes_grades": False,
    }
    files = {record["path"]: record for record in manifest["files"]}
    assert set(LEARNING_TEMPLATE_FILES + LEARNING_GENERATED_FILES) <= files.keys()
    for filename in LEARNING_TEMPLATE_FILES + LEARNING_GENERATED_FILES:
        assert sha256_file(output / filename) == files[filename]["sha256"]
    page = (output / "index.html").read_text()
    assert 'data-route="learning"' in page
    assert "learning.html" in page
    assert "LEARNING_DISABLED" not in page and "{{" not in page
    assert "review-config.js" in (output / "learning.html").read_text()
    config = (output / "review-config.js").read_text()
    parsed_config = _json_assignment(config, "window.LEARNING_AUTHORING_REVIEW=", "test")
    assert parsed_config["enabled"] is shared
    data = _json_assignment(
        (output / "learning-data.js").read_text(), "window.LEARNING_DATA=", "test",
    )
    quiz = json.loads((run / "quiz/quiz-proposed.json").read_text())
    assert data["questions"] == quiz["questions"]
    assert data["slots"] == quiz["assessment_slots"]
    assert not {"attempts", "feedback", "learner_id"} & data.keys()
    assert not any(name.endswith((".sql", ".pdf")) for name in files)


def test_cli_exports_learning_registration_offline_without_changing_run(
    tmp_path, monkeypatch, capsys,
):
    _forbid_provider_use(monkeypatch)
    run = _quiz_run(tmp_path)
    before = _inventory(run)
    output = tmp_path / "registration.sql"
    assert product_main(["export-learning-registration", str(run), str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["backend_writes"] == 0
    assert result["item_count"] == 3
    assert result["sql_sha256"] == sha256_file(output)
    assert _inventory(run) == before
