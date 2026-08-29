"""Offline packaging/security tests; synthetic data, never production authorship."""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from learning_authoring.product.learning import build_learning_package
from learning_authoring.product.role_apps import build_role_apps, export_authoring_registration
from learning_authoring.product.showcase import (
    PublishSafetyError,
    ReviewBackendConfig,
    _json_assignment,
)
from learning_authoring.product_cli import main as product_main
from tests.test_agent_quiz_review import _import_report, _quiz_run, _review_task
from tests.test_agent_session import _forbid_provider_use
from tests.test_review_registration import _inventory

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js payload hashing")


def _run(tmp_path):
    run = _quiz_run(tmp_path)
    task, report = _review_task(run)
    _import_report(run, task, report)
    return run


@pytest.mark.parametrize("preview", [False, True])
def test_separate_app_allowlists_preserve_raw_and_exclude_student_authoring(
    tmp_path, monkeypatch, preview,
):
    _forbid_provider_use(monkeypatch)
    run = _run(tmp_path)
    before = _inventory(run)
    output = tmp_path / "apps"
    backend = None if preview else ReviewBackendConfig(
        "https://abcdefghijklmnopqrst.supabase.co", "sb_publishable_fixture_public_key",
    )
    manifest = build_role_apps(run, output, review_backend=backend, local_preview=preview)
    assert _inventory(run) == before
    assert manifest["model_provider_calls"] == manifest["backend_writes"] == 0
    assert manifest["teacher_grants_created"] == manifest["publications_created"] == 0
    assert manifest["deploy_allowed"] is not preview
    for role in ("teacher", "student"):
        app = output / role
        document = json.loads((app / "showcase-manifest.json").read_text())
        recorded = {row["path"] for row in document["files"]}
        assert set(_inventory(app)) == recorded | {"showcase-manifest.json"}
        for row in document["files"]:
            content = (app / row["path"]).read_bytes()
            assert row["sha256"] == hashlib.sha256(content).hexdigest()
            assert row["bytes"] == len(content)
        assert document["deploy_allowed"] is not preview
        assert document["private_learner_data_bundled"] is False
    student = output / "student"
    student_files = set(_inventory(student))
    assert not student_files & {
        "learning-data.js", "learning.html", "learning-runtime.js", "review-runtime.js",
        "extraction-review.html", "kc-recall.html", "kc-scroll.html", "quiz-review.html",
        "teacher.html", "teacher-runtime.js",
    }
    assert not any(name.endswith((".sql", ".pdf")) for name in student_files)
    page = (student / "index.html").read_text()
    assert ("student-preview.js" in page) is preview
    assert "learning-data.js" not in page
    config = _json_assignment((student / "student-config.js").read_text(),
                              "window.STUDENT_CONFIG=", "test")
    assert config["courseId"] == run.name
    assert config["mode"] == ("local_preview" if preview else "shared")
    if preview:
        data = _json_assignment((student / "student-preview.js").read_text(),
                                "window.STUDENT_PREVIEW_DATA=", "test")
        assert data["questions"] == json.loads((run / "quiz/quiz-proposed.json").read_text())[
            "questions"
        ]
    else:
        assert "student-preview.js" not in student_files
        assert config["supabaseUrl"] == backend.supabase_url
    teacher = output / "teacher"
    review_config = _json_assignment((teacher / "review-config.js").read_text(),
                                     "window.LEARNING_AUTHORING_REVIEW=", "test")
    assert review_config["requiresTeacherRole"] is True
    assert review_config["reviewViews"]["quiz"] == "quiz-review.html"
    assert not (teacher / "learning.html").exists()


def test_shared_build_needs_backend_and_refuses_overwrite_or_symlink(tmp_path):
    run = _run(tmp_path)
    with pytest.raises(PublishSafetyError, match="require Supabase"):
        build_role_apps(run, tmp_path / "missing-config")
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("user data")
    with pytest.raises(PublishSafetyError, match="already exists"):
        build_role_apps(run, occupied, local_preview=True)
    assert (occupied / "keep.txt").read_text() == "user data"
    link = tmp_path / "link"
    link.symlink_to(occupied, target_is_directory=True)
    with pytest.raises(PublishSafetyError, match="[Ss]ymlink"):
        build_role_apps(run, link / "new", local_preview=True)


def test_registration_is_insert_only_private_and_does_not_grant_or_publish(tmp_path, monkeypatch):
    _forbid_provider_use(monkeypatch)
    run = _run(tmp_path)
    before = _inventory(run)
    output = tmp_path / "private-registration.sql"
    result = export_authoring_registration(run, output)
    sql = output.read_text()
    assert "INSERT INTO public.learning_authoring_packages" in sql
    assert "UPDATE public." not in sql and "ON CONFLICT" not in sql
    assert "INSERT INTO public.learning_course_teachers" not in sql
    assert "publish_reviewed_release(" not in sql
    assert result["backend_writes"] == result["publications_created"] == 0
    package = build_learning_package(run)
    canonical = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                           allow_nan=False)
    assert result["package_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert _inventory(run) == before
    with pytest.raises(Exception, match="new SQL path"):
        export_authoring_registration(run, output)
    with pytest.raises(Exception, match="outside the immutable"):
        export_authoring_registration(run, run / "private.sql")


def test_native_cli_cannot_silently_use_api_or_register_database(tmp_path, monkeypatch, capsys):
    _forbid_provider_use(monkeypatch)
    run = _run(tmp_path)
    assert product_main(
        ["export-authoring-registration", str(run), str(tmp_path / "operator.sql")]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["backend_writes"] == result["model_provider_calls"] == 0
    assert product_main(["build-role-apps", str(run), str(tmp_path / "no-config")]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["built"] is False
