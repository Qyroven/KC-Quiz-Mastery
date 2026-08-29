"""Read-only, source-bound material and state for the initial Quiz review.

This is an integrity boundary, not a semantic grader. The host coding agent reviews
the material; missing, stale, or malformed reports cannot become an initial PASS.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from learning_authoring.artifacts import RunArtifacts, read_json, sha256_file
from learning_authoring.authoring_context import (
    AuthoringContext,
    load_authoring_context,
    load_bundle_authoring_context,
)
from learning_authoring.contracts import ExtractedSource, SourceDescriptor
from learning_authoring.kc import load_approved_extraction
from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.quiz_contracts import QuizBatch
from learning_authoring.source_bundle import (
    SOURCE_BUNDLE_MANIFEST,
    SourceBundle,
    SourceBundleKCSet,
    load_bundle_extractions,
    load_source_bundle,
    validate_kc_set_against_bundle,
)

AUDIT_FILENAME = "quiz-semantic-audit.json"
AUDIT_METADATA_FILENAME = "quiz-semantic-metadata.json"


def material_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _binding(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": sha256_file(path)}


def _inside(root: Path, relative: str, *, label: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.is_symlink():
        raise ValueError(f"{label} escapes the source bundle or is a symlink")
    return path


def _context_locators(
    root: Path,
    context: AuthoringContext | None,
    context_ids: set[str],
) -> list[dict[str, Any]]:
    if context is None:
        return []
    return [
        {
            "context_id": item.context_id,
            "media_type": item.media_type,
            "path": str(root / item.raw_path),
            "sha256": item.sha256,
            "role": "verify_cited_lecturer_context_not_slide_content",
        }
        for item in context.items
        if item.context_id in context_ids
    ]


def quiz_review_material(
    run_dir: Path,
    *,
    candidate_dir: Path | None = None,
) -> dict[str, Any]:
    """Reconstruct current, verified material without writing or calling a provider."""
    root = run_dir.expanduser().resolve()
    candidate = (candidate_dir or root / "quiz").expanduser().resolve()
    artifacts = RunArtifacts(root)
    quiz_files = RunArtifacts(candidate)
    batch_raw = read_json(quiz_files.quiz_proposed)
    batch = QuizBatch.model_validate(batch_raw)
    quiz_input = read_json(quiz_files.quiz_input)
    batch.validate_against_input(quiz_input)
    metadata = read_json(quiz_files.quiz_metadata)
    if metadata.get("candidate_raw_sha256") and (
        sha256_file(quiz_files.quiz_proposed) != metadata["candidate_raw_sha256"]
    ):
        raise ValueError("Quiz bytes differ from the immutable imported candidate")
    kc_path = Path(metadata["kc_set"]["path"]).expanduser().resolve()
    kc_sha = sha256_file(kc_path)
    if kc_sha != batch.source_ref.kc_set_sha256 or kc_sha != metadata["kc_set"]["sha256"]:
        raise ValueError("Quiz review KC identity is stale")
    kc_raw = read_json(kc_path)
    raw_source_ref = kc_raw.get("source_ref")
    bundle_mode = isinstance(raw_source_ref, dict) and (
        raw_source_ref.get("schema_version") == "source-bundle.v1"
    )
    bundle: SourceBundle | None = None
    extractions: dict[str, ExtractedSource] = {}
    extraction_paths: dict[str, Path] = {}
    source_pdf_paths: dict[str, Path] = {}
    if bundle_mode:
        bundle = load_source_bundle(root)
        extractions = load_bundle_extractions(root, bundle)
        context = load_bundle_authoring_context(root, bundle)
        parsed = SourceBundleKCSet.model_validate(kc_raw)
        validated = validate_kc_set_against_bundle(
            parsed,
            bundle,
            extractions,
            authoring_context=context,
        )
        if not isinstance(validated, SourceBundleKCSet):
            raise ValueError("Quiz review requires source-qualified bundle KCs")
        kc_set: ProposedKCSet | SourceBundleKCSet = validated
        if batch.source_ref.source_bundle_sha256 != bundle.bundle_sha256:
            raise ValueError("Quiz source identity differs from the current source bundle")
        for entry in bundle.sources:
            source_id = entry.source.source_id
            extraction_paths[source_id] = _inside(
                root, entry.extraction_ref, label="bundle Extraction"
            )
            source_pdf_paths[source_id] = (
                _inside(root, entry.run_ref, label="bundle source run") / "source.pdf"
            )
    else:
        if (root / SOURCE_BUNDLE_MANIFEST).exists():
            raise ValueError("Quiz review bundle root cannot use an unqualified one-PDF KC set")
        kc_set = ProposedKCSet.model_validate(kc_raw)
        source = SourceDescriptor.model_validate(read_json(artifacts.source_manifest)["source"])
        if artifacts.approval.exists() or artifacts.approved.exists():
            extracted, _, _ = load_approved_extraction(root)
            extracted_path = artifacts.approved
        else:
            extracted_path = artifacts.proposed
            extracted = ExtractedSource.model_validate(read_json(extracted_path))
        if extracted.source != source or sha256_file(artifacts.source_pdf) != source.sha256:
            raise ValueError("Quiz review source PDF/Extraction does not match its source manifest")
        if (
            batch.source_ref.extraction_source_id != source.source_id
            or batch.source_ref.extraction_source_sha256 != source.sha256
        ):
            raise ValueError("Quiz source identity differs from the current PDF")
        context = load_authoring_context(root, source)
        kc_set.validate_against_source(extracted, authoring_context=context)
    current_context_sha = context.sha256 if context else None
    if batch.source_ref.authoring_context_sha256 != current_context_sha:
        raise ValueError("Quiz review authoring context is stale")

    selected = quiz_input["runtime"]["selected_kc_ids"]
    selected_set = set(selected)
    selected_kcs = [kc for kc in kc_set.leaf_kcs if kc.kc_id in selected_set]
    # Compare the complete raw records, including source qualification and absence
    # versus explicit-null fields, not merely matching KC IDs.
    raw_kcs = {item["kc_id"]: item for item in kc_raw["leaf_kcs"]}
    expected_kcs = {kc_id: raw_kcs[kc_id] for kc_id in selected if kc_id in raw_kcs}
    supplied_kcs = {item["kc_id"]: item for item in quiz_input["leaf_kcs"]}
    if set(expected_kcs) != selected_set or supplied_kcs != expected_kcs:
        raise ValueError("Quiz input differs from the current complete KC content")
    group_ids = {kc.group_id for kc in selected_kcs}
    groups = [item for item in kc_raw["kc_groups"] if item["group_id"] in group_ids]
    if quiz_input["kc_groups"] != groups:
        raise ValueError("Quiz input groups differ from the current KC set")

    source_pages = sorted(
        {
            (getattr(ref, "source_id", None), ref.page)
            for kc in selected_kcs
            for ref in kc.source_evidence
        },
        key=lambda value: (value[0] or "", value[1]),
    )
    cited_context = []
    for kc in selected_kcs:
        for reference in kc.context_evidence:
            value = reference.model_dump(mode="json")
            if value not in cited_context:
                cited_context.append(value)
    context_ids = {item["context_id"] for item in cited_context}
    page_locators = []
    if bundle_mode:
        assert bundle is not None
        bundle_entries = {entry.source.source_id: entry for entry in bundle.sources}
        for source_id, page in source_pages:
            if source_id is None or source_id not in bundle_entries:
                raise ValueError("Quiz review contains ambiguous bundle source evidence")
            entry = bundle_entries[source_id]
            manifest = read_json(
                _inside(root, entry.source_manifest_ref, label="bundle source manifest")
            )
            record = next(
                (item for item in manifest.get("page_records", []) if item["page"] == page),
                None,
            )
            if record is not None:
                image_path = (
                    _inside(root, entry.run_ref, label="bundle source run") / record["image_ref"]
                ).resolve()
                if (
                    not image_path.is_relative_to(root)
                    or image_path.is_symlink()
                    or sha256_file(image_path) != record["image_sha256"]
                ):
                    raise ValueError(
                        f"Source image identity is invalid for {source_id} page {page}"
                    )
                page_locators.append({"source_id": source_id, "page": page, **_binding(image_path)})
        selected_page_keys = set(source_pages)
        extraction_snapshot: dict[str, Any] = {
            source_id: {
                "source": extraction.source.model_dump(mode="json"),
                "pages": [
                    page.model_dump(mode="json")
                    for page in extraction.pages
                    if (source_id, page.page_number) in selected_page_keys
                ],
            }
            for source_id, extraction in extractions.items()
            if any(key[0] == source_id for key in selected_page_keys)
        }
        extraction_bindings: Any = {
            source_id: _binding(path) for source_id, path in extraction_paths.items()
        }
        source_pdf_bindings: Any = {
            source_id: _binding(path) for source_id, path in source_pdf_paths.items()
        }
        source_ref_fields = {
            "source_sha256": None,
            "source_bundle_sha256": bundle.bundle_sha256,
        }
    else:
        pages = {page for source_id, page in source_pages if source_id is None}
        manifest = read_json(artifacts.source_manifest)
        for page in sorted(pages):
            record = next(
                (item for item in manifest.get("page_records", []) if item["page"] == page),
                None,
            )
            if record is None:
                raise ValueError(f"Source image locator is missing for cited page {page}")
            image_path = (root / record["image_ref"]).resolve()
            if (
                not image_path.is_relative_to(root)
                or sha256_file(image_path) != record["image_sha256"]
            ):
                raise ValueError(f"Source image identity is invalid for cited page {page}")
            page_locators.append({"page": page, **_binding(image_path)})
        extraction_snapshot = {
            "source": source.model_dump(mode="json"),
            "pages": [
                page.model_dump(mode="json")
                for page in extracted.pages
                if page.page_number in pages
            ],
        }
        extraction_bindings = _binding(extracted_path)
        source_pdf_bindings = _binding(artifacts.source_pdf)
        source_ref_fields = {"source_sha256": source.sha256}

    review_artifacts = {
        "quiz": batch_raw,
        "kc": {"leaf_kcs": [expected_kcs[kc_id] for kc_id in selected], "kc_groups": groups},
        "extraction": extraction_snapshot,
        "context": {"citations": cited_context},
    }
    bindings = {
        "quiz": _binding(quiz_files.quiz_proposed),
        "quiz_input": _binding(quiz_files.quiz_input),
        "kc": _binding(kc_path),
        "extraction": extraction_bindings,
        "source_pdf": source_pdf_bindings,
        "authoring_context_sha256": current_context_sha,
    }
    locators = {
        "source_pdf": source_pdf_bindings,
        "page_images": page_locators,
        "context_attachments": _context_locators(root, context, context_ids),
        "policy": "Inspect cited source as needed, one page at a time; never bulk-load all PNGs.",
    }
    snapshot = {"artifacts": review_artifacts, "bindings": bindings, "source_locators": locators}
    source_ref = {
        "quiz_sha256": bindings["quiz"]["sha256"],
        "kc_set_sha256": kc_sha,
        **source_ref_fields,
        "authoring_context_sha256": current_context_sha,
        "review_input_sha256": material_digest(snapshot),
    }
    learner_fields = {
        "question_id",
        "kc_id",
        "group_id",
        "slot_id",
        "variant_index",
        "title",
        "interaction",
        "stimulus",
        "prompt",
        "choice_options",
        "matching_left",
        "matching_right",
        "ordering_options",
    }
    learner_questions = [
        {key: value for key, value in question.items() if key in learner_fields}
        for question in batch_raw["questions"]
    ]
    key_questions = [
        {
            key: value
            for key, value in question.items()
            if key not in learner_fields or key == "question_id"
        }
        for question in batch_raw["questions"]
    ]
    return {
        "source_ref": source_ref,
        **snapshot,
        "learner_questions": learner_questions,
        "answer_material": {"questions": key_questions},
        "assessment_slots": batch_raw.get("assessment_slots", []),
    }


def load_quiz_semantic_state(
    run_dir: Path,
    *,
    candidate_dir: Path | None = None,
) -> dict[str, Any]:
    """Safe UI projection: no private task paths, and never stale green badges."""
    from learning_authoring.quiz_semantics import (
        QuizSemanticAudit,
        semantic_audit_summary,
        validate_semantic_audit,
    )

    root = run_dir.expanduser().resolve()
    candidate = (candidate_dir or root / "quiz").expanduser().resolve()
    report_path = candidate / AUDIT_FILENAME
    record_path = candidate / AUDIT_METADATA_FILENAME
    absent = {"status": "NOT_REVIEWED", "report": None, "questions": [], "approved": False}
    if not report_path.exists() and not record_path.exists():
        return absent
    try:
        raw_report = read_json(report_path)
        record = read_json(record_path)
        current = quiz_review_material(root, candidate_dir=candidate)
        if (
            record["stage"] != "quiz-review"
            or record["reviewer_mode"] not in {"independent", "self_review"}
            or record["approval_status"] != "EXPERIMENTAL_UNAPPROVED"
        ):
            raise ValueError("Semantic import provenance is invalid")
        if sha256_file(report_path) != record["candidate_raw_sha256"]:
            raise ValueError("Semantic report bytes differ from its import record")
        if record["source_ref"] != current["source_ref"]:
            raise ValueError("Source or quiz changed since the initial semantic review")
        fingerprint = record["task_fingerprint"]
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in fingerprint)
        ):
            raise ValueError("Semantic task fingerprint is invalid")
        task = read_json(root / "agent-session" / "tasks" / f"quiz-review-{fingerprint}.json")
        if (
            task["task_fingerprint"] != fingerprint
            or material_digest(
                {key: value for key, value in task.items() if key != "task_fingerprint"}
            )
            != fingerprint
            or task["stage"] != "quiz-review"
            or task["input_boundary"]["expected_source_ref"] != current["source_ref"]
            or task["input_boundary"]["reviewer_mode"] != record["reviewer_mode"]
        ):
            raise ValueError("Semantic report no longer matches its host-issued task")
        report = QuizSemanticAudit.model_validate(raw_report)
        batch = current["artifacts"]["quiz"]
        validate_semantic_audit(
            report,
            quiz=batch,
            expected_source_ref=current["source_ref"],
            artifacts=current["artifacts"],
            expected_reviewer=record["reviewer_mode"],
        )
        summary = semantic_audit_summary(
            report,
            quiz=batch,
            expected_source_ref=current["source_ref"],
        )
        return {**summary, "report": raw_report, "approved": False}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        # This object is embedded in publishable HTML. Never expose local file paths.
        return {
            **absent,
            "status": "STALE",
            "reason": "The review or its bound Quiz/KC/source material is missing or changed.",
        }
