from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.publish_showcase import (
    DEFAULT_REVIEW_FILES,
    MANIFEST_NAME,
    PublishSafetyError,
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
    source_sha256 = "1" * 64
    (run_dir / "source-manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": "source-package.v2",
                "source": {
                    "source_id": "sha256:synthetic",
                    "filename": filename,
                    "media_type": "application/pdf",
                    "page_count": page_count,
                    "sha256": source_sha256,
                },
            }
        ),
        encoding="utf-8",
    )
    extraction_text = (
        json.dumps(
            {
                "schema_version": "extracted-source.v2",
                "source": {"filename": filename},
                "pages": [],
            }
        )
        + "\n"
    )
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
    elif extraction_status != "proposed":
        raise ValueError(f"Unsupported synthetic extraction status: {extraction_status}")
    for index, review_name in enumerate(_review_names(review_files)):
        content = (
            secret
            if index == 0 and secret
            else (
                "<!doctype html><title>Safe review</title><body>"
                '<span data-source="/Users/example/work/demo.json">Review</span></body>'
            )
        )
        (run_dir / review_name).write_text(content, encoding="utf-8")
    for page in range(1, page_count + 1):
        (pages_dir / f"page-{page:04d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return run_dir


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
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file()
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
    assert manifest["entrypoints"]["quiz_experiment"] == "quiz-pilot-selected.html"
    assert manifest["stage_status"] == {
        "extractor": "PROPOSED",
        "kc": "PROPOSED",
        "quiz": "EXPERIMENTAL_UNAPPROVED",
        "mastery": "NOT_IMPLEMENTED",
    }
    stored = json.loads((output_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert stored["source_run"] == "demo"
    assert len(stored["files"]) == 3 + len(_review_names(review_files)) + 7
    portal = (output_dir / "index.html").read_text(encoding="utf-8")
    assert "unseen-seven-page-deck.pdf" in portal
    assert "7 ảnh trang" in portal
    assert "PROPOSED · REVIEW NEEDED" in portal
    assert 'href="quiz-pilot-selected.html"' in portal
    assert "45 ảnh trang" not in portal
    combined = b"".join(path.read_bytes() for path in output_dir.rglob("*") if path.is_file())
    assert b"/Users/" not in combined
    assert b"sk-" not in combined
    quiz_page = (output_dir / review_files.quiz).read_text(encoding="utf-8")
    assert "Quiz experimental / unapproved" in quiz_page
    assert "không chứng minh semantic validity" in quiz_page


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
    assert "reviewer, note và approval metadata không được đóng gói" in portal
    published = {
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file()
    }
    assert "extraction-approval.json" not in published
    assert "extracted-source.approved.json" not in published
    combined = b"".join(path.read_bytes() for path in output_dir.rglob("*") if path.is_file())
    assert b"CONFIDENTIAL_REVIEWER_SHOULD_NOT_PUBLISH" not in combined
    assert b"CONFIDENTIAL_APPROVAL_NOTE_SHOULD_NOT_PUBLISH" not in combined


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


def test_build_showcase_rejects_path_like_review_selection(tmp_path: Path) -> None:
    run_dir = _fake_run(tmp_path, 1)

    with pytest.raises(PublishSafetyError, match="run-local HTML filename"):
        build_showcase(
            run_dir,
            tmp_path / "showcase-dist",
            template_dir=REPOSITORY_ROOT / "showcase",
            review_files=ReviewFiles(quiz="../unrelated-review.html"),
        )
