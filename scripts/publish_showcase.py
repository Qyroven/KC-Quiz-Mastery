#!/usr/bin/env python3
"""Backward-compatible wrapper for the packaged static portal builder."""

from __future__ import annotations

import argparse
from pathlib import Path

from learning_authoring.showcase import (
    DEFAULT_REVIEW_FILES,
    DEFAULT_TEMPLATE_DIR,
    MANAGED_BY,
    MANIFEST_NAME,
    SOURCE_MANIFEST_NAME,
    PublishSafetyError,
    ReviewArtifact,
    ReviewBackendConfig,
    ReviewFiles,
    SourceMetadata,
    StageStatus,
    build_showcase,
)

__all__ = [
    "DEFAULT_REVIEW_FILES",
    "DEFAULT_TEMPLATE_DIR",
    "MANAGED_BY",
    "MANIFEST_NAME",
    "SOURCE_MANIFEST_NAME",
    "PublishSafetyError",
    "ReviewArtifact",
    "ReviewBackendConfig",
    "ReviewFiles",
    "SourceMetadata",
    "StageStatus",
    "build_showcase",
]

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "showcase-dist"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="Authoring run directory to publish",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Generated static package (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--extractor-review",
        default=DEFAULT_REVIEW_FILES.extractor,
        help="Exact run-local Extractor review HTML filename",
    )
    parser.add_argument(
        "--kc-recall-review",
        default=DEFAULT_REVIEW_FILES.kc_recall,
        help="Exact run-local KC recall review HTML filename",
    )
    parser.add_argument(
        "--kc-scroll-review",
        default=DEFAULT_REVIEW_FILES.kc_scroll,
        help="Exact run-local KC scroll review HTML filename",
    )
    parser.add_argument(
        "--quiz-review",
        default=DEFAULT_REVIEW_FILES.quiz,
        help="Exact run-local experimental Quiz review HTML filename",
    )
    parser.add_argument("--review-supabase-url")
    parser.add_argument("--review-supabase-publishable-key")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if bool(args.review_supabase_url) != bool(args.review_supabase_publishable_key):
        raise SystemExit(
            "--review-supabase-url and --review-supabase-publishable-key "
            "must be supplied together"
        )
    review_backend = (
        ReviewBackendConfig(
            supabase_url=args.review_supabase_url,
            supabase_publishable_key=args.review_supabase_publishable_key,
        )
        if args.review_supabase_url
        else None
    )
    manifest = build_showcase(
        args.run_dir,
        args.output_dir,
        template_dir=DEFAULT_TEMPLATE_DIR,
        review_files=ReviewFiles(
            extractor=args.extractor_review,
            kc_recall=args.kc_recall_review,
            kc_scroll=args.kc_scroll_review,
            quiz=args.quiz_review,
        ),
        review_backend=review_backend,
    )
    total_bytes = sum(int(entry["bytes"]) for entry in manifest["files"])
    print(f"Built {args.output_dir.resolve()}")
    print(f"Files: {len(manifest['files']) + 1}; bytes: {total_bytes}")
    print("Lineage: verified (Extraction -> KC -> Quiz)")
    print("Security audit: passed (allowlist, names, symlinks, credential patterns)")
    print("Deployment: not performed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
