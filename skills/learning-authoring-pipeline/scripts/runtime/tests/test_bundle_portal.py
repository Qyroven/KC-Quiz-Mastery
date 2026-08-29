from __future__ import annotations

import copy
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from learning_authoring.artifacts import read_json, sha256_bytes, sha256_file, write_json
from learning_authoring.authoring_context import prepare_bundle_authoring_context
from learning_authoring.product.bundle_portal import BundlePortalError, build_bundle_portal
from learning_authoring.quiz import QuizConfig, build_quiz_input
from learning_authoring.quiz_contracts import QuizBatch
from learning_authoring.source_bundle import (
    SourceBundleKCSet,
    bundle_kc_source_ref,
    load_bundle_extractions,
    prepare_source_bundle,
)
from tests.test_quiz_adaptive import adaptive_output
from tests.test_source_bundle import _bundled_kc, _source_run


def _bundle(root: Path):
    prepared = [_source_run(root, name) for name in ("concepts", "exceptions")]
    # One manifest-bound image is enough to prove that the builder copies only
    # verified review assets. Missing page images remain an honest UI state.
    first_run = prepared[0][0]
    image = b"\x89PNG\r\n\x1a\nsynthetic-image"
    image_path = first_run / "pages" / "page-0001.png"
    image_path.parent.mkdir()
    image_path.write_bytes(image)
    manifest = read_json(first_run / "source-manifest.json")
    manifest["page_records"] = [
        {
            "page": 1,
            "image_ref": "pages/page-0001.png",
            "image_sha256": sha256_bytes(image),
        }
    ]
    write_json(first_run / "source-manifest.json", manifest)

    bundle = prepare_source_bundle(root, [run for run, _ in prepared])
    extractions = load_bundle_extractions(root, bundle)
    raw_kc = _bundled_kc(bundle, extractions)
    write_json(root / "kc-proposed.json", raw_kc)
    return bundle, extractions, raw_kc


def _write_quiz(root: Path, bundle, extractions, raw_kc) -> dict:
    kc_path = root / "kc-proposed.json"
    kc = SourceBundleKCSet.model_validate(raw_kc)
    quiz_input = build_quiz_input(
        kc,
        kc_set_sha256=sha256_file(kc_path),
        config=QuizConfig(include_all_kcs=True),
        raw_kc_set=raw_kc,
    )
    raw_quiz = adaptive_output(
        extractions[bundle.sources[0].source.source_id].source,
        (("KC-001", 1),),
    )
    raw_quiz["source_ref"] = quiz_input["source_ref"]
    evidence = raw_kc["leaf_kcs"][0]["source_evidence"][0]
    raw_quiz["questions"][0]["evidence_refs"] = [
        {
            "source_id": evidence["source_id"],
            "page": evidence["page"],
            "block_ids": evidence["block_ids"],
        }
    ]
    QuizBatch.model_validate(raw_quiz).validate_against_input(quiz_input)
    quiz_dir = root / "quiz"
    write_json(quiz_dir / "quiz-input.json", quiz_input)
    write_json(quiz_dir / "quiz-proposed.json", raw_quiz)
    candidate_sha = sha256_file(quiz_dir / "quiz-proposed.json")
    write_json(
        quiz_dir / "quiz-generation-metadata.json",
        {
            "stage": "quiz",
            "quality_status": "experimental_unapproved",
            "approval_status": "EXPERIMENTAL_UNAPPROVED",
            "candidate_raw_sha256": candidate_sha,
            "kc_set": {"path": str(kc_path), "sha256": sha256_file(kc_path)},
            "selected_kc_ids": quiz_input["runtime"]["selected_kc_ids"],
        },
    )
    return raw_quiz


def _bind_context(
    root: Path,
    bundle,
    raw_kc: dict,
    *,
    source_id: str | None = None,
    pages: list[int] | None = None,
):
    context = prepare_bundle_authoring_context(
        root,
        bundle,
        context_texts=("Lecturer qualification.",),
    )
    assert context is not None
    candidate = copy.deepcopy(raw_kc)
    candidate["source_ref"] = bundle_kc_source_ref(
        bundle,
        authoring_context_sha256=context.sha256,
    ).model_dump(mode="json")
    mapped_pages = pages or []
    evidence = {
        "context_id": "CTX-001",
        "excerpt": "Lecturer qualification.",
        "description": None,
        "supports": "A separate lecturer constraint.",
        "pages": mapped_pages,
        "mapping_method": "semantic_alignment" if mapped_pages else "document_level",
        "mapping_confidence": "high",
    }
    if source_id is not None:
        evidence["source_id"] = source_id
    candidate["leaf_kcs"][0]["context_evidence"] = [evidence]
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
    write_json(root / "kc-proposed.json", candidate)
    return context, candidate


def _bytes_by_path(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_bundle_portal_is_connected_deterministic_and_honest(tmp_path: Path) -> None:
    bundle, _, _ = _bundle(tmp_path)
    first = tmp_path / "portal-a"
    second = tmp_path / "portal-b"

    manifest = build_bundle_portal(tmp_path, first)
    build_bundle_portal(tmp_path, second)

    assert manifest["schema_version"] == "source-bundle-review-portal.v1"
    assert manifest["source_bundle_sha256"] == bundle.bundle_sha256
    assert manifest["counts"] == {
        "sources": 2,
        "source_pages": sum(entry.source.page_count for entry in bundle.sources),
        "kc_groups": 1,
        "leaf_kcs": 1,
        "assessment_slots": 0,
        "quiz_questions": 0,
    }
    assert manifest["statuses"]["extraction"]["label"] == ("PROPOSED · REVIEW REQUIRED")
    assert manifest["statuses"]["kc"]["human_approved"] is False
    assert manifest["statuses"]["quiz"]["code"] == "NOT_GENERATED"
    assert manifest["quiz_initial_check"] == {
        "status": "NOT_REVIEWED",
        "counts": {},
        "reasons": [],
        "reviewer_mode": None,
        "source_coverage": None,
        "initial_check_only": True,
        "human_approved": False,
    }
    assert manifest["candidate_content_modified"] is False
    assert manifest["deployment_performed"] is False
    assert [source["filename"] for source in manifest["sources"]] == [
        entry.source.filename for entry in bundle.sources
    ]
    assert manifest["sources"][0]["manifest_bound_image_count"] == 1
    assert manifest["sources"][1]["manifest_bound_image_count"] == 0
    assert (first / "assets/source-001/page-0001.png").is_file()
    assert "source-001" in (first / "index.html").read_text()
    assert "source-002" in (first / "kc.html").read_text()
    assert "No Quiz candidate" in (first / "quiz.html").read_text()
    assert _bytes_by_path(first) == _bytes_by_path(second)


def test_bundle_portal_builds_with_bound_authoring_context(tmp_path: Path) -> None:
    bundle, _, raw_kc = _bundle(tmp_path)
    context, _ = _bind_context(tmp_path, bundle, raw_kc)

    manifest = build_bundle_portal(tmp_path, tmp_path / "context-portal")

    assert manifest["authoring_context_sha256"] == context.sha256
    kc_page = (tmp_path / "context-portal/kc.html").read_text()
    assert "Lecturer qualification." in kc_page


def test_bundle_portal_accepts_only_qualified_context_page_mappings(
    tmp_path: Path,
) -> None:
    bundle, _, raw_kc = _bundle(tmp_path)
    source_id = bundle.sources[1].source.source_id
    context, candidate = _bind_context(
        tmp_path,
        bundle,
        raw_kc,
        source_id=source_id,
        pages=[1],
    )

    manifest = build_bundle_portal(tmp_path, tmp_path / "qualified-context-portal")

    assert manifest["authoring_context_sha256"] == context.sha256
    evidence = candidate["leaf_kcs"][0]["context_evidence"][0]
    assert evidence["source_id"] == source_id
    assert evidence["pages"] == [1]

    unqualified = copy.deepcopy(candidate)
    unqualified["leaf_kcs"][0]["context_evidence"][0].pop("source_id")
    write_json(tmp_path / "kc-proposed.json", unqualified)
    with pytest.raises(ValueError, match="requires a source_id"):
        build_bundle_portal(tmp_path, tmp_path / "unqualified-context-portal")
    assert not (tmp_path / "unqualified-context-portal").exists()

    unknown_source = copy.deepcopy(candidate)
    unknown_source["leaf_kcs"][0]["context_evidence"][0]["source_id"] = "sha256:unknown"
    write_json(tmp_path / "kc-proposed.json", unknown_source)
    with pytest.raises(ValueError, match="unknown source"):
        build_bundle_portal(tmp_path, tmp_path / "unknown-context-source-portal")
    assert not (tmp_path / "unknown-context-source-portal").exists()

    unknown_page = copy.deepcopy(candidate)
    unknown_page["leaf_kcs"][0]["context_evidence"][0]["pages"] = [
        bundle.sources[1].source.page_count + 1
    ]
    write_json(tmp_path / "kc-proposed.json", unknown_page)
    with pytest.raises(ValueError, match="unknown page"):
        build_bundle_portal(tmp_path, tmp_path / "unknown-context-page-portal")
    assert not (tmp_path / "unknown-context-page-portal").exists()


def test_bundle_portal_validates_quiz_and_never_edits_candidate(tmp_path: Path) -> None:
    bundle, extractions, raw_kc = _bundle(tmp_path)
    raw_quiz = _write_quiz(tmp_path, bundle, extractions, raw_kc)
    candidate = tmp_path / "quiz/quiz-proposed.json"
    before = candidate.read_bytes()

    manifest = build_bundle_portal(tmp_path, tmp_path / "portal")

    assert candidate.read_bytes() == before
    assert manifest["counts"]["assessment_slots"] == 1
    assert manifest["counts"]["quiz_questions"] == 1
    assert manifest["statuses"]["quiz"] == {
        "code": "EXPERIMENTAL_UNAPPROVED",
        "label": "Experimental · human review required",
        "human_approved": False,
        "selected_kc_count": 1,
        "question_count": 1,
        "assessment_slot_count": 1,
        "candidate_sha256": sha256_file(candidate),
    }
    assert manifest["quiz_initial_check"]["status"] == "NOT_REVIEWED"
    quiz_page = (tmp_path / "portal/quiz.html").read_text()
    assert raw_quiz["questions"][0]["prompt"] in quiz_page
    assert "Generated candidate is unchanged" in quiz_page
    assert "Initial semantic check · NOT_REVIEWED" in quiz_page


def test_bundle_portal_fails_closed_on_stale_bundle_or_kc_quiz_lineage(
    tmp_path: Path,
) -> None:
    bundle, extractions, raw_kc = _bundle(tmp_path)
    _write_quiz(tmp_path, bundle, extractions, raw_kc)
    changed = copy.deepcopy(raw_kc)
    changed["source_summary"] = "Changed after Quiz generation."
    write_json(tmp_path / "kc-proposed.json", changed)

    with pytest.raises(BundlePortalError, match="Quiz KC lineage is stale"):
        build_bundle_portal(tmp_path, tmp_path / "stale-kc-portal")
    assert not (tmp_path / "stale-kc-portal").exists()

    write_json(tmp_path / "kc-proposed.json", raw_kc)
    extraction = tmp_path / bundle.sources[0].extraction_ref
    extraction.write_bytes(extraction.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Extraction changed"):
        build_bundle_portal(tmp_path, tmp_path / "stale-source-portal")
    assert not (tmp_path / "stale-source-portal").exists()


def test_bundle_portal_requires_a_fresh_destination(tmp_path: Path) -> None:
    _bundle(tmp_path)
    output = tmp_path / "portal"
    output.mkdir()
    with pytest.raises(BundlePortalError, match="fresh path"):
        build_bundle_portal(tmp_path, output)


def test_bundle_portal_inline_javascript_is_syntactically_valid(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    _bundle(tmp_path)
    output = tmp_path / "portal"
    build_bundle_portal(tmp_path, output)

    for page in ("index.html", "kc.html", "quiz.html", "sources/source-001/index.html"):
        content = (output / page).read_text(encoding="utf-8")
        scripts = re.findall(r"<script(?: [^>]*)?>(.*?)</script>", content, re.DOTALL)
        executable = [script for script in scripts if not script.lstrip().startswith("{")]
        assert executable, page
        for script in executable:
            checked = subprocess.run(
                [node, "--check", "-"],
                input=script,
                text=True,
                capture_output=True,
                check=False,
            )
            assert checked.returncode == 0, f"{page}: {checked.stderr}"
