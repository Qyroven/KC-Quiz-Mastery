"""Offline Teacher/Student app packaging and trusted-operator registration.

Generation and deployment are deliberately not performed here. Shared Student
contains no question bank: it receives an authorized, published learner packet
from the backend. Local previews are explicitly marked non-publishable.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from learning_authoring.product.learning import build_learning_package, render_learning_data
from learning_authoring.product.review_registration import RegistrationSafetyError, _sql_text
from learning_authoring.product.showcase import (
    DEFAULT_REVIEW_FILES,
    DEFAULT_TEMPLATE_DIR,
    MANIFEST_NAME,
    PublishSafetyError,
    ReviewBackendConfig,
    ReviewFiles,
    _absolute_without_resolving,
    _audit_names,
    _audit_secrets,
    _json_assignment,
    _record,
    _reject_symlink_components,
    _relative_files,
    _validated_review_backend,
    build_showcase,
)

MANAGED_BY = "learning-authoring role-apps-build"
ROLE_MANIFEST = "role-apps-manifest.json"
STUDENT_FILES = ("student-runtime.js", "student-style.css", "learning-core.js")
TEACHER_FILES = ("teacher-runtime.js", "teacher-style.css", "learning-core.js")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _script(name: str, value: Any) -> str:
    text = _json(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f"window.{name}={text};\n"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _finish_app(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    _audit_names(path)
    _audit_secrets(path)
    manifest["files"] = [_record(path / name, path) for name in sorted(
        _relative_files(path) - {MANIFEST_NAME},
    )]
    _write_json(path / MANIFEST_NAME, manifest)
    return manifest


def build_role_apps(
    run_dir: Path,
    output_dir: Path,
    *,
    review_backend: ReviewBackendConfig | None = None,
    local_preview: bool = False,
    review_files: ReviewFiles = DEFAULT_REVIEW_FILES,
) -> dict[str, Any]:
    """Create two NEW allowlisted app directories; never deploy or approve.

    Shared packages require an exact public backend config; the actual database
    migration, baseline registration and course teacher grants are separate,
    authorized operator actions. No account is assigned a role by this builder.
    """
    source = _absolute_without_resolving(run_dir)
    output = _absolute_without_resolving(output_dir)
    _reject_symlink_components(source, "source run")
    _reject_symlink_components(output, "role apps output")
    if output.exists():
        raise PublishSafetyError("Role apps output already exists; choose a fresh directory")
    if output == source or source.is_relative_to(output):
        raise PublishSafetyError("Role apps output cannot contain or replace the source run")
    backend = _validated_review_backend(review_backend)
    if local_preview and backend is not None:
        raise PublishSafetyError("Local preview cannot also configure shared persistence")
    if not local_preview and backend is None:
        raise PublishSafetyError("Shared role apps require Supabase; use --local-preview locally")
    package = build_learning_package(source, review_files=review_files)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".role-apps-", dir=output.parent))
    try:
        teacher, student = staging / "teacher", staging / "student"
        teacher_manifest = build_showcase(
            source, teacher, review_files=review_files, review_backend=backend,
        )
        for name in TEACHER_FILES:
            shutil.copyfile(DEFAULT_TEMPLATE_DIR / name, teacher / name)
        shutil.copyfile(DEFAULT_TEMPLATE_DIR / "teacher.html", teacher / "index.html")
        (teacher / "learning-data.js").write_text(render_learning_data(package), encoding="utf-8")
        config_path = teacher / "review-config.js"
        config = _json_assignment(config_path.read_text(encoding="utf-8"),
                                  "window.LEARNING_AUTHORING_REVIEW=", "review config")
        config.update({
            "role": "teacher", "requiresTeacherRole": True,
            "mode": "local_preview" if local_preview else "shared",
            "reviewViews": {"extraction": review_files.extractor, "kc": review_files.kc_recall,
                            "kc_scroll": review_files.kc_scroll, "quiz": review_files.quiz},
        })
        config_path.write_text(_script("LEARNING_AUTHORING_REVIEW", config), encoding="utf-8")
        teacher_manifest.update({
            "schema_version": "learning-authoring-role-app.v1", "managed_by": MANAGED_BY,
            "role": "teacher", "deploy_allowed": not local_preview,
            "backend_role_required": True,
            "private_learner_data_bundled": False,
            "static_authoring_preview_contains_answers": True,
        })
        teacher_manifest["shared_review"]["identity"] = (
            "operator_granted_course_teacher" if backend else None
        )
        teacher_manifest["entrypoints"]["teacher"] = "index.html"
        _finish_app(teacher, teacher_manifest)

        student.mkdir()
        for name in STUDENT_FILES:
            shutil.copyfile(DEFAULT_TEMPLATE_DIR / name, student / name)
        page = (DEFAULT_TEMPLATE_DIR / "student.html").read_text(encoding="utf-8")
        import re

        page = re.sub(r"<!-- LOCAL_PREVIEW -->(.*?)<!-- /LOCAL_PREVIEW -->",
                      r"\1" if local_preview else "", page, flags=re.S)
        page = page.replace("<!--STUDENT_PREVIEW_SCRIPT-->",
                            '<script defer src="student-preview.js"></script>'
                            if local_preview else "")
        (student / "index.html").write_text(page, encoding="utf-8")
        student_config = {
            "mode": "local_preview" if local_preview else "shared",
            "courseId": package["run_id"],
            "sourceTitle": package["source"]["filename"],
            "supabaseUrl": backend.supabase_url if backend else None,
            "supabasePublishableKey": backend.supabase_publishable_key if backend else None,
        }
        (student / "student-config.js").write_text(
            _script("STUDENT_CONFIG", student_config), encoding="utf-8",
        )
        if local_preview:
            (student / "student-preview.js").write_text(
                _script("STUDENT_PREVIEW_DATA", package), encoding="utf-8",
            )
        for name in ("robots.txt", "vercel.json"):
            shutil.copyfile(teacher / name, student / name)
        _finish_app(student, {
            "schema_version": "learning-authoring-role-app.v1", "managed_by": MANAGED_BY,
            "role": "student", "deploy_allowed": not local_preview,
            "mode": student_config["mode"], "source_run": package["run_id"],
            "source": package["source"], "entrypoints": {"student": "index.html"},
            "content_delivery": "local_unapproved_preview" if local_preview else
                                "published_version_from_backend",
            "authoring_controls_bundled": False,
            "answer_material_bundled": local_preview,
            "private_learner_data_bundled": False,
        })
        summary = {
            "schema_version": "teacher-student-apps.v1", "managed_by": MANAGED_BY,
            "source_run": package["run_id"], "source": package["source"],
            "apps": {"teacher": "teacher", "student": "student"},
            "mode": student_config["mode"], "deploy_allowed": not local_preview,
            "model_provider_calls": 0, "backend_writes": 0, "publications_created": 0,
            "teacher_grants_created": 0,
            "required_backend_migration": "202608280002_teacher_student.sql",
            "calibrated_mastery": False,
        }
        _write_json(staging / ROLE_MANIFEST, summary)
        _audit_names(staging)
        _audit_secrets(staging)
        os.replace(staging, output)
        return summary
    except Exception:
        shutil.rmtree(staging)
        raise


def export_authoring_registration(run_dir: Path, output_path: Path) -> dict[str, Any]:
    """Export trusted-operator INSERT after review and learning registration.

    Does not assign a teacher, modify a raw artifact, publish a release or connect
    to a backend. Duplicate registration deliberately fails, not upserts.
    """
    source = _absolute_without_resolving(run_dir)
    output = _absolute_without_resolving(output_path)
    _reject_symlink_components(output, "authoring registration output")
    if output.exists() or output.is_relative_to(source):
        raise RegistrationSafetyError("Choose a new SQL path outside the immutable source run")
    if any((parent / MANIFEST_NAME).is_file() or (parent / ROLE_MANIFEST).is_file()
           for parent in output.parents):
        raise RegistrationSafetyError("Registration SQL must never be inside an app bundle")
    package = build_learning_package(source)
    encoded = _json(package)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    content = (
        "-- Private operator input. Never publish this SQL; contains assessment keys.\n"
        "-- Requires immutable review targets and learning_items already registered.\n"
        "-- Does not grant teachers, create approvals or publish a release.\nBEGIN;\n"
        "INSERT INTO public.learning_authoring_packages(run_id, package, package_sha256)\n"
        f"VALUES ({_sql_text(package['run_id'])}, {_sql_text(encoded)}::jsonb, "
        f"{_sql_text(digest)});\nCOMMIT;\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(content)
    return {
        "run_id": package["run_id"], "package_sha256": digest,
        "sql_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "model_provider_calls": 0, "backend_writes": 0, "teacher_grants_created": 0,
        "publications_created": 0, "question_count": len(package["questions"]),
    }
