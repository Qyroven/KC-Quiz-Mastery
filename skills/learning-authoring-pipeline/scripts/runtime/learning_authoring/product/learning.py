"""Frozen, offline practice packages and insert-only learning-item registration.

This is a practice surface, not a secure exam: the package deliberately includes
the same answer material already exposed by the reviewer site. No authoring file
is changed, no approval is invented, and no database/provider call is made here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from learning_authoring.product.review_registration import (
    HASH_ALGORITHM,
    RegistrationSafetyError,
    _check_inline_json,
    _sql_text,
    prepare_review_registration,
)
from learning_authoring.product.showcase import (
    DEFAULT_REVIEW_FILES,
    ReviewFiles,
    _absolute_without_resolving,
    _read_json_object,
    _reject_symlink_components,
    _sha256,
)
from learning_authoring.quiz_review_state import (
    AUDIT_FILENAME,
    AUDIT_METADATA_FILENAME,
    load_quiz_semantic_state,
    quiz_review_material,
)

SCHEMA_VERSION = "learning-package.v1"
POLICY_VERSION = "evidence-rules.v1"
INITIAL_STATUSES = {"PASS", "REVIEW", "REJECT", "UNCHECKED", "STALE"}


def _optional_hash(path: Path) -> str | None:
    _reject_symlink_components(path, "initial check")
    return _sha256(path) if path.is_file() else None


def build_learning_package(
    run_dir: Path,
    *,
    review_files: ReviewFiles = DEFAULT_REVIEW_FILES,
    node_executable: str | Path | None = None,
    runtime_path: Path | None = None,
) -> dict[str, Any]:
    """Read a verified run and copy complete content without normalizing its arrays.

    Node.js is a build-time prerequisite for the existing renderer's exact hash
    contract, not a browser/runtime dependency. Legacy quizzes without slots are
    still practiceable, but cannot claim assessment-slot coverage.
    """
    root = _absolute_without_resolving(run_dir)
    registration = prepare_review_registration(
        root,
        review_files=review_files,
        node_executable=node_executable,
        runtime_path=runtime_path,
    )
    material = quiz_review_material(root)
    quiz = material["artifacts"]["quiz"]  # The original parsed candidate, not model_dump.
    if any(question.get("additional_slot_ids") for question in quiz["questions"]):
        raise RegistrationSafetyError(
            "Integrated items are authoring/review only until Learning supports per-slot rubric "
            "evidence; refusing to copy a whole-question result to multiple KCs."
        )
    if any(question["interaction"] == "numeric_input" for question in quiz["questions"]):
        raise RegistrationSafetyError(
            "numeric_input is authoring/review only until Learning supports numeric scoring; "
            "refusing to export an unsupported learner interaction."
        )
    kc_set = _read_json_object(root / "kc-proposed.json", "KC")
    quiz_input = _read_json_object(root / "quiz" / "quiz-input.json", "Quiz input")
    selected = quiz_input["runtime"]["selected_kc_ids"]
    kc_by_id = {kc["kc_id"]: kc for kc in kc_set["leaf_kcs"]}
    kcs = [kc_by_id[kc_id] for kc_id in selected]
    group_ids = {kc["group_id"] for kc in kcs}
    groups = [group for group in kc_set["kc_groups"] if group["group_id"] in group_ids]

    check_paths = [root / "quiz" / name for name in (AUDIT_FILENAME, AUDIT_METADATA_FILENAME)]
    check_hashes = {path: _optional_hash(path) for path in check_paths}
    semantic = load_quiz_semantic_state(root)
    statuses = {item["question_id"]: item["status"] for item in semantic.get("questions", [])}
    fallback = "STALE" if semantic["status"] == "STALE" else "UNCHECKED"
    versions = {
        "quiz_sha256": material["bindings"]["quiz"]["sha256"],
        "kc_sha256": material["bindings"]["kc"]["sha256"],
        "extraction_sha256": material["bindings"]["extraction"]["sha256"],
        "context_sha256": material["bindings"]["authoring_context_sha256"],
        "policy_version": POLICY_VERSION,
    }
    targets = {
        (target.stage, target.item_type, target.item_key): {
            "stage": target.stage,
            "item_type": target.item_type,
            "item_key": target.item_key,
            "base_artifact_sha256": target.base_artifact_sha256,
        }
        for target in registration.targets
    }
    question_meta = {}
    for question in quiz["questions"]:
        question_id, kc_id = question["question_id"], question["kc_id"]
        pages = sorted({
            row["page"]
            for row in [*kc_by_id[kc_id].get("source_evidence", []),
                        *question.get("evidence_refs", [])]
        })
        # Context's optional page alignment is not PDF evidence. Do not fabricate
        # extraction targets for document-level or context-only KCs.
        refs = [targets[("extraction", "page", f"page:{page:04d}")] for page in pages]
        refs.extend([targets[("kc", "leaf_kc", kc_id)],
                     targets[("quiz", "question", question_id)]])
        status = statuses.get(question_id, fallback)
        if status not in INITIAL_STATUSES:
            status = fallback
        if semantic.get("reviewer", {}).get("mode") == "self_review" and status == "PASS":
            status = "UNCHECKED"
        question_meta[question_id] = {
            "question_sha256": targets[("quiz", "question", question_id)][
                "base_artifact_sha256"
            ],
            "initial_check_status": status,
            "lineage": {
                "source_sha256": registration.source_sha256,
                "kc_set_sha256": versions["kc_sha256"],
                "quiz_sha256": versions["quiz_sha256"],
                "extraction_sha256": versions["extraction_sha256"],
                "authoring_context_sha256": versions["context_sha256"],
                "policy_version": POLICY_VERSION,
                "review_targets": refs,
            },
        }

    # Detect a changed candidate/initial check instead of mixing two snapshots.
    for name, digest in registration.artifact_sha256:
        if _sha256(root / name) != digest:
            raise RegistrationSafetyError("Learning inputs changed during export; use a frozen run")
    for binding in material["bindings"].values():
        if isinstance(binding, dict) and _sha256(Path(binding["path"])) != binding["sha256"]:
            raise RegistrationSafetyError("Learning source changed during export; use a frozen run")
    if check_hashes != {path: _optional_hash(path) for path in check_paths}:
        raise RegistrationSafetyError("Initial check changed during Learning export; retry")

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": registration.run_id,
        "source": {
            "filename": registration.source_filename,
            "source_id": registration.source_id,
            "source_sha256": registration.source_sha256,
        },
        "versions": versions,
        "kcs": kcs,
        "groups": groups,
        "slots": quiz.get("assessment_slots", []),
        "questions": quiz["questions"],
        "question_meta": question_meta,
        "practice_only": True,
        "secure_exam": False,
        "evidence_label": "Provisional observed evidence; not calibrated mastery.",
        "baseline_hash_algorithm": HASH_ALGORITHM,
    }


def render_learning_data(package: dict[str, Any]) -> str:
    """Render safe external-script data, also safe if an integrator embeds it."""
    if package.get("schema_version") != SCHEMA_VERSION:
        raise RegistrationSafetyError("Unsupported Learning package schema")
    _check_inline_json(package)
    try:
        raw = json.dumps(package, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RegistrationSafetyError("Learning package is not finite JSON") from exc
    raw = raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return "// Frozen offline practice data: answer keys are intentionally included.\n" + (
        "window.LEARNING_DATA=" + raw + ";\n"
    )


def write_learning_data(package: dict[str, Any], destination: Path) -> Path:
    """Create a NEW data script; the caller owns the generated portal directory."""
    output = _absolute_without_resolving(destination)
    _reject_symlink_components(output, "Learning data output")
    content = render_learning_data(package)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    return output


def render_learning_registration_sql(package: dict[str, Any]) -> str:
    """Render an atomic, INSERT-only package registration on existing baselines.

    The backend's foreign keys and insertion gate require the already registered
    run, question and upstream review targets. Existing item identities abort the
    transaction: no rebase, overwrite or automatic conflict resolution is used.
    """
    if package.get("schema_version") != SCHEMA_VERSION:
        raise RegistrationSafetyError("Unsupported Learning package schema")
    if package.get("versions", {}).get("policy_version") != POLICY_VERSION:
        raise RegistrationSafetyError("Unsupported Learning evidence policy")
    values = []
    identities = set()
    for question in package["questions"]:
        question_id = question["question_id"]
        if question_id in identities:
            raise RegistrationSafetyError("Duplicate Learning question identity")
        identities.add(question_id)
        meta = package["question_meta"][question_id]
        if meta["initial_check_status"] not in INITIAL_STATUSES:
            raise RegistrationSafetyError("Unknown initial-check status")
        fields = [package["run_id"], question_id, meta["question_sha256"], question["kc_id"]]
        slot = "NULL" if question.get("slot_id") is None else _sql_text(question["slot_id"])
        json_fields = [json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                       for value in (question, meta["lineage"])]
        values.append("  (" + ", ".join([
            *map(_sql_text, fields), slot, _sql_text(meta["initial_check_status"]),
            *(_sql_text(value) + "::jsonb" for value in json_fields),
        ]) + ")")
    if not values:
        raise RegistrationSafetyError("Cannot register an empty Learning package")
    return (
        "-- Offline practice registration; apply only after the immutable review baseline.\n"
        "-- Contains answer keys. Keep this SQL outside the published portal.\n"
        "-- Duplicate item identities abort; existing authoring/review history is untouched.\n"
        "BEGIN;\n"
        "INSERT INTO public.learning_items\n"
        "  (run_id, question_id, question_sha256, kc_id, slot_id, initial_check_status, "
        "question_payload, lineage)\n"
        "VALUES\n" + ",\n".join(values) + ";\nCOMMIT;\n"
    )


def export_learning_registration(
    run_dir: Path,
    output_path: Path,
    *,
    review_files: ReviewFiles = DEFAULT_REVIEW_FILES,
    node_executable: str | Path | None = None,
    runtime_path: Path | None = None,
) -> dict[str, Any]:
    """Write new external SQL only; never connect, register, or alter a run."""
    output = _absolute_without_resolving(output_path)
    _reject_symlink_components(output, "Learning registration output")
    if output.is_relative_to(_absolute_without_resolving(run_dir)):
        raise RegistrationSafetyError("Learning registration SQL must be outside the immutable run")
    if any((parent / "showcase-manifest.json").is_file() for parent in output.parents):
        raise RegistrationSafetyError("Learning registration SQL must not be in a static portal")
    if output.exists():
        raise RegistrationSafetyError("Learning registration output already exists")
    package = build_learning_package(
        run_dir, review_files=review_files, node_executable=node_executable,
        runtime_path=runtime_path,
    )
    sql = render_learning_registration_sql(package)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(sql)
    return {
        "run_id": package["run_id"],
        "item_count": len(package["questions"]),
        "policy_version": POLICY_VERSION,
        "sql_sha256": _sha256(output),
        "backend_writes": 0,
    }
