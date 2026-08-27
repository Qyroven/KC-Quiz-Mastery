from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.publish_showcase import (
    DEFAULT_REVIEW_FILES,
    MANIFEST_NAME,
    PublishSafetyError,
    ReviewBackendConfig,
    ReviewFiles,
    build_showcase,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _review_names(review_files: ReviewFiles) -> tuple[str, ...]:
    return (
        review_files.extractor,
        review_files.kc_recall,
        review_files.kc_scroll,
        review_files.quiz,
    )


def _fake_run(
    root: Path,
    page_count: int,
    *,
    filename: str = "synthetic-course.pdf",
    review_files: ReviewFiles = DEFAULT_REVIEW_FILES,
    extraction_status: str = "proposed",
    secret: str | None = None,
) -> Path:
    run_dir = root / "runs" / "demo"
    pages_dir = run_dir / "pages"
    pages_dir.mkdir(parents=True)
    page_bytes = b"\x89PNG\r\n\x1a\n"
    page_sha256 = hashlib.sha256(page_bytes).hexdigest()
    source_sha256 = "1" * 64
    source = {
        "source_id": "sha256:synthetic",
        "filename": filename,
        "media_type": "application/pdf",
        "page_count": page_count,
        "sha256": source_sha256,
    }
    source_manifest = {
        "manifest_version": "source-package.v2",
        "source": source,
        "page_records": [
            {
                "page": page,
                "image_ref": f"pages/page-{page:04d}.png",
                "image_sha256": page_sha256,
            }
            for page in range(1, page_count + 1)
        ],
    }
    (run_dir / "source-manifest.json").write_text(
        json.dumps(source_manifest),
        encoding="utf-8",
    )
    extraction = {
        "schema_version": "extracted-source.v2",
        "source": source,
        "pages": [{"page_number": page} for page in range(1, page_count + 1)],
    }
    extraction_text = json.dumps(extraction) + "\n"
    (run_dir / "extracted-source.proposed.json").write_text(
        extraction_text,
        encoding="utf-8",
    )
    if extraction_status == "approved":
        approved_path = run_dir / "extracted-source.approved.json"
        approved_path.write_text(extraction_text, encoding="utf-8")
        approved_sha256 = hashlib.sha256(approved_path.read_bytes()).hexdigest()
        (run_dir / "extraction-approval.json").write_text(
            json.dumps(
                {
                    "approval_version": "extraction-approval.v1",
                    "status": "approved",
                    "schema_version": "extracted-source.v2",
                    "source_sha256": source_sha256,
                    "approved_sha256": approved_sha256,
                    "reviewer": "CONFIDENTIAL_REVIEWER_SHOULD_NOT_PUBLISH",
                    "note": "CONFIDENTIAL_APPROVAL_NOTE_SHOULD_NOT_PUBLISH",
                }
            ),
            encoding="utf-8",
        )
        upstream_status = "HUMAN_APPROVED"
        upstream_path = approved_path
    elif extraction_status != "proposed":
        raise ValueError(f"Unsupported synthetic extraction status: {extraction_status}")
    else:
        upstream_status = "PROPOSED_DEMO_ONLY"
        upstream_path = run_dir / "extracted-source.proposed.json"
    upstream = {
        "status": upstream_status,
        "demo_only": upstream_status == "PROPOSED_DEMO_ONLY",
        "path": "/private/tmp/agent/extraction.json",
        "sha256": hashlib.sha256(upstream_path.read_bytes()).hexdigest(),
    }

    extraction_html = (
        "<!doctype html><title>Extraction</title><body>"
        f"<script>const source={json.dumps(extraction)};"
        f"const metrics={json.dumps({'source_page_count': page_count})};"
        f"const sourceManifest={json.dumps(source_manifest)};</script>"
        f"<span>{secret or ''}</span></body>"
    )
    (run_dir / review_files.extractor).write_text(extraction_html, encoding="utf-8")

    kc_candidate = {
        "schema_version": "proposed-kc-set.v1",
        "source_ref": {
            "schema_version": "extracted-source.v2",
            "source_id": source["source_id"],
            "source_sha256": source_sha256,
        },
        "source_summary": "Synthetic connected KC set",
        "page_audit": [],
        "kc_groups": [
            {"group_id": "KCG-001", "name": "Group", "leaf_kc_ids": ["KC-001", "KC-002"]}
        ],
        "leaf_kcs": [
            {"kc_id": "KC-001", "group_id": "KCG-001", "name": "First KC"},
            {"kc_id": "KC-002", "group_id": "KCG-001", "name": "Second KC"},
        ],
        "uncovered_content": [],
        "generation_warnings": [],
    }
    kc_path = run_dir / "kc-proposed.json"
    kc_path.write_text(json.dumps(kc_candidate) + "\n", encoding="utf-8")
    kc_metadata = {
        "stage": "kc",
        "request_fingerprint": "kc-request",
        "candidate_raw_path": "/Users/example/run/kc-candidate.json",
        "candidate_raw_sha256": "2" * 64,
        "upstream_extraction": upstream,
        "human_review_required": True,
        "approval_status": "PROPOSED",
    }
    kc_metrics = {
        "leaf_kc_count": 2,
        "kc_group_count": 1,
        "upstream_extraction_status": upstream_status,
    }
    (run_dir / "kc-generation-metadata.json").write_text(json.dumps(kc_metadata), encoding="utf-8")
    (run_dir / "kc-run-metrics.json").write_text(json.dumps(kc_metrics), encoding="utf-8")
    for name, scroll_mode in (
        (review_files.kc_recall, False),
        (review_files.kc_scroll, True),
    ):
        kc_payload = {
            "source": extraction,
            "candidate": {
                "proposed": kc_candidate,
                "metrics": {"leaf_kcs": 2, "groups": 1},
                "raw_metrics": kc_metrics,
                "metadata": kc_metadata,
            },
            "scroll_mode": scroll_mode,
        }
        (run_dir / name).write_text(
            "<!doctype html><title>KC</title><body>"
            f"<script>const DATA={json.dumps(kc_payload)};const ready=true;</script></body>",
            encoding="utf-8",
        )

    kc_sha256 = hashlib.sha256(kc_path.read_bytes()).hexdigest()
    quiz_ref = {
        "extraction_source_id": source["source_id"],
        "extraction_source_sha256": source_sha256,
        "kc_set_sha256": kc_sha256,
    }
    quiz = {
        "schema_version": "quiz-batch.v1",
        "source_ref": quiz_ref,
        "questions": [
            {"question_id": "Q-001", "kc_id": "KC-001", "title": "Question 1"},
            {"question_id": "Q-002", "kc_id": "KC-001", "title": "Question 2"},
            {"question_id": "Q-003", "kc_id": "KC-002", "title": "Question 3"},
        ],
    }
    quiz_input = {
        "source_ref": quiz_ref,
        "runtime": {"selected_kc_ids": ["KC-001", "KC-002"]},
        "leaf_kcs": kc_candidate["leaf_kcs"],
    }
    quiz_metrics = {
        "quality_status": "experimental_unapproved",
        "selected_kc_count": 2,
        "question_count": 3,
    }
    quiz_metadata = {
        "stage": "quiz",
        "quality_status": "experimental_unapproved",
        "candidate_raw_path": "/Users/example/run/quiz-candidate.json",
        "kc_set": {"path": "/private/tmp/agent/kc-proposed.json", "sha256": kc_sha256},
        "upstream_extraction_status": upstream_status,
        "selected_kc_ids": ["KC-001", "KC-002"],
        "approval_status": "EXPERIMENTAL_UNAPPROVED",
    }
    quiz_audit = {"summary": {"question_count": 3, "status": "NO_FORM_FLAGS"}}
    quiz_dir = run_dir / "quiz"
    quiz_dir.mkdir()
    canonical_quiz = {
        "quiz-proposed.json": quiz,
        "quiz-input.json": quiz_input,
        "quiz-run-metrics.json": quiz_metrics,
        "quiz-generation-metadata.json": quiz_metadata,
        "quiz-form-audit.json": quiz_audit,
    }
    for name, payload in canonical_quiz.items():
        (quiz_dir / name).write_text(json.dumps(payload), encoding="utf-8")
    quiz_payload = {
        "quiz": quiz,
        "input": quiz_input,
        "metrics": quiz_metrics,
        "metadata": quiz_metadata,
        "form_audit": quiz_audit,
    }
    (run_dir / review_files.quiz).write_text(
        "<!doctype html><title>Quiz</title><body>"
        f'<script id="payload" type="application/json">{json.dumps(quiz_payload)}</script>'
        "</body>",
        encoding="utf-8",
    )
    for page in range(1, page_count + 1):
        (pages_dir / f"page-{page:04d}.png").write_bytes(page_bytes)
    return run_dir


def _source_manifest(run_dir: Path) -> dict[str, object]:
    return json.loads((run_dir / "source-manifest.json").read_text(encoding="utf-8"))


def _write_source_manifest(run_dir: Path, payload: dict[str, object]) -> None:
    (run_dir / "source-manifest.json").write_text(json.dumps(payload), encoding="utf-8")


def _approve_existing_extraction(run_dir: Path) -> None:
    proposed_path = run_dir / "extracted-source.proposed.json"
    approved_path = run_dir / "extracted-source.approved.json"
    approved_path.write_bytes(proposed_path.read_bytes())
    source = _source_manifest(run_dir)["source"]
    assert isinstance(source, dict)
    (run_dir / "extraction-approval.json").write_text(
        json.dumps(
            {
                "approval_version": "extraction-approval.v1",
                "status": "approved",
                "schema_version": "extracted-source.v2",
                "source_sha256": source["sha256"],
                "approved_sha256": hashlib.sha256(approved_path.read_bytes()).hexdigest(),
                "reviewer": "LATER_REVIEWER_NOT_PUBLISHED",
            }
        ),
        encoding="utf-8",
    )


def test_build_showcase_derives_non_45_metadata_and_uses_explicit_reviews(
    tmp_path: Path,
) -> None:
    review_files = ReviewFiles(
        extractor="extractor-audit.html",
        kc_recall="kc-cards.html",
        kc_scroll="kc-continuous.html",
        quiz="quiz-pilot-selected.html",
    )
    run_dir = _fake_run(
        tmp_path,
        7,
        filename="unseen-seven-page-deck.pdf",
        review_files=review_files,
    )
    (run_dir / "api-response.json").write_text('{"secret": "not published"}')
    (run_dir / ".env").write_text("OPENAI_API_KEY=not-published")
    output_dir = tmp_path / "showcase-dist"

    manifest = build_showcase(
        run_dir,
        output_dir,
        template_dir=REPOSITORY_ROOT / "showcase",
        review_files=review_files,
    )

    published = {
        path.relative_to(output_dir).as_posix() for path in output_dir.rglob("*") if path.is_file()
    }
    assert "api-response.json" not in published
    assert ".env" not in published
    assert MANIFEST_NAME in published
    assert set(_review_names(review_files)) <= published
    assert manifest["source"] == {
        "filename": "unseen-seven-page-deck.pdf",
        "source_id": "sha256:synthetic",
        "page_count": 7,
    }
    assert manifest["page_count"] == 7
    assert manifest["counts"] == {
        "pages": 7,
        "review_views": 4,
        "leaf_kcs": 2,
        "kc_groups": 1,
        "selected_kcs": 2,
        "quiz_questions": 3,
    }
    assert manifest["lineage"] == {
        "extraction_to_kc": "VERIFIED",
        "kc_upstream_extraction_status": "PROPOSED_DEMO_ONLY",
        "kc_to_quiz": "VERIFIED",
    }
    assert manifest["entrypoints"]["quiz_experiment"] == "quiz-pilot-selected.html"
    assert manifest["stage_status"] == {
        "extractor": "PROPOSED",
        "kc": "PROPOSED",
        "quiz": "EXPERIMENTAL_UNAPPROVED",
        "mastery": "NOT_IMPLEMENTED",
    }
    stored = json.loads((output_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert stored["source_run"] == "demo"
    assert len(stored["files"]) == 5 + len(_review_names(review_files)) + 7
    assert stored["shared_review"] == {
        "enabled": False,
        "provider": None,
        "identity": None,
        "raw_artifacts_mutable": False,
    }
    portal = (output_dir / "index.html").read_text(encoding="utf-8")
    assert "unseen-seven-page-deck.pdf" in portal
    assert "7 ảnh trang" in portal
    assert "PROPOSED · REVIEW NEEDED" in portal
    assert 'data-src="quiz-pilot-selected.html"' in portal
    assert 'id="stage-frame"' in portal
    assert 'id="previous-stage"' in portal
    assert 'id="next-stage"' in portal
    assert "3 câu hỏi" in portal
    assert "2 Leaf KC" in portal
    assert 'data-route="kc"' in portal
    assert 'data-kc-recall-src="kc-cards.html#1"' in portal
    assert 'data-kc-scroll-src="kc-continuous.html#1"' in portal
    assert 'class="stage-position">1 / 3' in portal
    assert "4 review views" in portal
    assert "Mastery · Roadmap" in portal
    assert "ROADMAP · NOT IMPLEMENTED" in portal
    assert "day16" not in portal.lower()
    assert "pilot gần nhất" not in portal
    assert "45 ảnh trang" not in portal
    combined = b"".join(path.read_bytes() for path in output_dir.rglob("*") if path.is_file())
    assert b"/Users/" not in combined
    assert b"/private/tmp/" not in combined
    assert b"sk-" not in combined
    quiz_page = (output_dir / review_files.quiz).read_text(encoding="utf-8")
    assert "showcase-quiz-status" not in quiz_page
    assert "Quiz experimental / unapproved" not in quiz_page
    assert '<script src="review-config.js"></script>' in quiz_page
    assert '<script src="review-runtime.js"></script>' in quiz_page
    review_config = (output_dir / "review-config.js").read_text(encoding="utf-8")
    assert '"enabled":false' in review_config
    vercel = (output_dir / "vercel.json").read_text(encoding="utf-8")
    assert "connect-src 'none'" in vercel


def test_build_showcase_enables_shared_supabase_review(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 2)
    output_dir = tmp_path / "showcase-dist"
    backend = ReviewBackendConfig(
        supabase_url="https://abcdefghij.supabase.co/",
        supabase_publishable_key="sb_publishable_" + "a" * 32,
    )

    manifest = build_showcase(run_dir, output_dir, review_backend=backend)

    assert manifest["shared_review"] == {
        "enabled": True,
        "provider": "supabase",
        "identity": "anonymous_auth_with_display_name",
        "raw_artifacts_mutable": False,
    }
    config = (output_dir / "review-config.js").read_text(encoding="utf-8")
    assert '"enabled":true' in config
    assert '"runId":"demo"' in config
    assert "https://abcdefghij.supabase.co" in config
    assert "sb_publishable_" in config
    vercel = (output_dir / "vercel.json").read_text(encoding="utf-8")
    assert "connect-src https://abcdefghij.supabase.co" in vercel
    assert "connect-src 'none'" not in vercel
    runtime = (output_dir / "review-runtime.js").read_text(encoding="utf-8")
    assert "/rest/v1/rpc/append_review_event" in runtime
    assert "/rest/v1/rpc/get_review_target_events" in runtime
    assert 'request("/rest/v1/review_events' not in runtime
    assert "revisionMatchesAdapter" in runtime
    assert "function openKcEditor" in runtime
    assert "function openQuizEditor" in runtime
    assert "Chỉ sửa nội dung KC" in runtime
    assert "Bạn không cần đọc hoặc chỉnh JSON" in runtime
    assert "data-la-option-text" in runtime
    assert "data-la-map-left" in runtime
    assert "data-la-order-id" in runtime
    assert "data-la-rubric-row" in runtime
    assert "function upstreamStaleMessage" in runtime
    assert "function setupTableControls" in runtime
    assert "data-la-table-cell" in runtime
    assert "function setupMatchingControls" in runtime


def test_build_showcase_rejects_supabase_service_role_key(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 1)
    header = "eyJhbGciOiJub25lIn0"
    payload = "eyJyb2xlIjoic2VydmljZV9yb2xlIn0"

    with pytest.raises(PublishSafetyError, match="service-role"):
        build_showcase(
            run_dir,
            tmp_path / "showcase-dist",
            review_backend=ReviewBackendConfig(
                supabase_url="https://abcdefghij.supabase.co",
                supabase_publishable_key=f"{header}.{payload}.signature",
            ),
        )


def test_build_showcase_verifies_human_approval_without_publishing_reviewer(
    tmp_path: Path,
) -> None:
    run_dir = _fake_run(tmp_path, 2, extraction_status="approved")
    output_dir = tmp_path / "showcase-dist"

    manifest = build_showcase(
        run_dir,
        output_dir,
        template_dir=REPOSITORY_ROOT / "showcase",
    )

    assert manifest["stage_status"]["extractor"] == "HUMAN_APPROVED"
    portal = (output_dir / "index.html").read_text(encoding="utf-8")
    assert "HUMAN APPROVED" in portal
    assert "Reviewer, note và approval metadata không nằm trong portal" in portal
    assert manifest["lineage"]["kc_upstream_extraction_status"] == "HUMAN_APPROVED"
    published = {
        path.relative_to(output_dir).as_posix() for path in output_dir.rglob("*") if path.is_file()
    }
    assert "extraction-approval.json" not in published
    assert "extracted-source.approved.json" not in published
    combined = b"".join(path.read_bytes() for path in output_dir.rglob("*") if path.is_file())
    assert b"CONFIDENTIAL_REVIEWER_SHOULD_NOT_PUBLISH" not in combined
    assert b"CONFIDENTIAL_APPROVAL_NOTE_SHOULD_NOT_PUBLISH" not in combined


def test_rebuild_after_later_approval_preserves_historical_kc_provenance(
    tmp_path: Path,
) -> None:
    run_dir = _fake_run(tmp_path, 2)
    output_dir = tmp_path / "showcase-dist"

    before = build_showcase(run_dir, output_dir)
    assert before["stage_status"]["extractor"] == "PROPOSED"
    assert before["lineage"]["kc_upstream_extraction_status"] == "PROPOSED_DEMO_ONLY"

    _approve_existing_extraction(run_dir)
    after = build_showcase(run_dir, output_dir)

    assert after["stage_status"]["extractor"] == "HUMAN_APPROVED"
    assert after["lineage"]["kc_upstream_extraction_status"] == "PROPOSED_DEMO_ONLY"
    portal = (output_dir / "index.html").read_text(encoding="utf-8")
    assert "HUMAN APPROVED" in portal
    assert "PROPOSED_DEMO_ONLY" in portal
    combined = b"".join(path.read_bytes() for path in output_dir.rglob("*") if path.is_file())
    assert b"LATER_REVIEWER_NOT_PUBLISHED" not in combined


def test_build_showcase_rejects_quiz_upstream_status_not_matching_kc(
    tmp_path: Path,
) -> None:
    run_dir = _fake_run(tmp_path, 1)
    metadata_path = run_dir / "quiz" / "quiz-generation-metadata.json"
    quiz_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    quiz_metadata["upstream_extraction_status"] = "HUMAN_APPROVED"
    metadata_path.write_text(json.dumps(quiz_metadata), encoding="utf-8")
    review_path = run_dir / DEFAULT_REVIEW_FILES.quiz
    review_path.write_text(
        review_path.read_text(encoding="utf-8").replace(
            '"upstream_extraction_status": "PROPOSED_DEMO_ONLY"',
            '"upstream_extraction_status": "HUMAN_APPROVED"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(PublishSafetyError, match="Quiz upstream extraction status"):
        build_showcase(run_dir, tmp_path / "showcase-dist")


def test_build_showcase_rejects_secret_in_review_html(tmp_path: Path) -> None:
    secret = "sk-" + "A" * 24
    run_dir = _fake_run(tmp_path, 1, secret=secret)
    output_dir = tmp_path / "showcase-dist"

    with pytest.raises(PublishSafetyError, match="secret"):
        build_showcase(
            run_dir,
            output_dir,
            template_dir=REPOSITORY_ROOT / "showcase",
        )

    assert not output_dir.exists()


def test_build_showcase_refuses_to_replace_modified_output(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 1)
    output_dir = tmp_path / "showcase-dist"
    kwargs = {"template_dir": REPOSITORY_ROOT / "showcase"}
    build_showcase(run_dir, output_dir, **kwargs)
    (output_dir / "manual-note.txt").write_text("keep me", encoding="utf-8")

    with pytest.raises(PublishSafetyError, match="modified showcase output"):
        build_showcase(run_dir, output_dir, **kwargs)

    assert (output_dir / "manual-note.txt").read_text(encoding="utf-8") == "keep me"


def test_build_showcase_refuses_changed_managed_file(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 1)
    output_dir = tmp_path / "showcase-dist"
    kwargs = {"template_dir": REPOSITORY_ROOT / "showcase"}
    build_showcase(run_dir, output_dir, **kwargs)
    (output_dir / "index.html").write_text("modified", encoding="utf-8")

    with pytest.raises(PublishSafetyError, match="modified showcase output"):
        build_showcase(run_dir, output_dir, **kwargs)

    assert (output_dir / "index.html").read_text(encoding="utf-8") == "modified"


def test_build_showcase_rejects_path_like_review_selection(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 1)

    with pytest.raises(PublishSafetyError, match="run-local HTML filename"):
        build_showcase(
            run_dir,
            tmp_path / "showcase-dist",
            template_dir=REPOSITORY_ROOT / "showcase",
            review_files=ReviewFiles(quiz="../unrelated-review.html"),
        )


def test_build_showcase_uses_packaged_connected_template(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 1)
    output_dir = tmp_path / "showcase-dist"

    build_showcase(run_dir, output_dir)

    portal = (output_dir / "index.html").read_text(encoding="utf-8")
    assert 'aria-label="Connected authoring journey"' in portal
    assert 'data-route="extraction"' in portal
    assert 'data-route="kc"' in portal
    assert 'data-route="quiz"' in portal
    assert 'data-kc-view="recall"' in portal
    assert 'data-kc-view="scroll"' in portal
    assert portal.count('class="route"') == 3
    assert 'class="stage-position">1 / 3' in portal
    assert "{{WORKFLOW_STAGE_COUNT}}" not in portal
    assert "PROPOSED_DEMO_ONLY" in portal


def test_build_showcase_rejects_unconnected_extraction_review(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 2)
    review = run_dir / DEFAULT_REVIEW_FILES.extractor
    review.write_text(
        review.read_text(encoding="utf-8").replace('"page_number": 1', '"page_number": 99'),
        encoding="utf-8",
    )

    with pytest.raises(PublishSafetyError, match="does not match the connected extraction"):
        build_showcase(run_dir, tmp_path / "showcase-dist")


def test_build_showcase_rejects_unconnected_kc_review(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 1)
    review = run_dir / DEFAULT_REVIEW_FILES.kc_recall
    upstream_sha256 = hashlib.sha256(
        (run_dir / "extracted-source.proposed.json").read_bytes()
    ).hexdigest()
    review.write_text(
        review.read_text(encoding="utf-8").replace(upstream_sha256, "9" * 64),
        encoding="utf-8",
    )

    with pytest.raises(PublishSafetyError, match="not connected to this extraction"):
        build_showcase(run_dir, tmp_path / "showcase-dist")


def test_build_showcase_rejects_quiz_review_not_matching_run(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 1)
    review = run_dir / DEFAULT_REVIEW_FILES.quiz
    review.write_text(
        review.read_text(encoding="utf-8").replace("Question 1", "Foreign question"),
        encoding="utf-8",
    )

    with pytest.raises(PublishSafetyError, match="quiz-proposed.json"):
        build_showcase(run_dir, tmp_path / "showcase-dist")


def test_build_showcase_rejects_missing_page_record(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 2)
    manifest = _source_manifest(run_dir)
    records = manifest["page_records"]
    assert isinstance(records, list)
    records.pop()
    _write_source_manifest(run_dir, manifest)

    with pytest.raises(PublishSafetyError, match="complete ordered inventory"):
        build_showcase(run_dir, tmp_path / "showcase-dist")


def test_build_showcase_rejects_duplicate_page_record(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 2)
    manifest = _source_manifest(run_dir)
    records = manifest["page_records"]
    assert isinstance(records, list)
    records[1] = dict(records[0])
    _write_source_manifest(run_dir, manifest)

    with pytest.raises(PublishSafetyError, match="unique pages in order"):
        build_showcase(run_dir, tmp_path / "showcase-dist")


def test_build_showcase_rejects_out_of_order_page_records(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 2)
    manifest = _source_manifest(run_dir)
    records = manifest["page_records"]
    assert isinstance(records, list)
    records.reverse()
    _write_source_manifest(run_dir, manifest)

    with pytest.raises(PublishSafetyError, match="unique pages in order"):
        build_showcase(run_dir, tmp_path / "showcase-dist")


def test_build_showcase_rejects_tampered_page_image(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 1)
    (run_dir / "pages" / "page-0001.png").write_bytes(b"tampered image")

    with pytest.raises(PublishSafetyError, match="Page image hash mismatch"):
        build_showcase(run_dir, tmp_path / "showcase-dist")


def test_build_showcase_rejects_page_inventory_escape(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 1)
    manifest = _source_manifest(run_dir)
    records = manifest["page_records"]
    assert isinstance(records, list) and isinstance(records[0], dict)
    records[0]["image_ref"] = "pages/../outside.png"
    _write_source_manifest(run_dir, manifest)

    with pytest.raises(PublishSafetyError, match=r"run-local pages/\*\.png"):
        build_showcase(run_dir, tmp_path / "showcase-dist")


def test_build_showcase_rejects_symlinked_pages_directory(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 1)
    outside_pages = tmp_path / "outside-pages"
    (run_dir / "pages").rename(outside_pages)
    (run_dir / "pages").symlink_to(outside_pages, target_is_directory=True)

    with pytest.raises(PublishSafetyError, match="Symlink path components"):
        build_showcase(run_dir, tmp_path / "showcase-dist")


def test_build_showcase_rejects_symlinked_quiz_directory(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 1)
    outside_quiz = tmp_path / "outside-quiz"
    (run_dir / "quiz").rename(outside_quiz)
    (run_dir / "quiz").symlink_to(outside_quiz, target_is_directory=True)

    with pytest.raises(PublishSafetyError, match="Symlink path components"):
        build_showcase(run_dir, tmp_path / "showcase-dist")
