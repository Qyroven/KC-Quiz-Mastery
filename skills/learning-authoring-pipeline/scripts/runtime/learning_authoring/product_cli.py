"""Optional Teacher/Student product export commands.

This module is deliberately separate from the subscription-native authoring CLI.  It builds
review products from existing run artifacts; it does not generate Extraction, KC, or Quiz data.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path

from learning_authoring.product.learning import export_learning_registration
from learning_authoring.product.role_apps import build_role_apps, export_authoring_registration
from learning_authoring.product.showcase import (
    PublishSafetyError,
    ReviewBackendConfig,
    ReviewFiles,
)


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _parser() -> argparse.ArgumentParser:
    try:
        version = importlib.metadata.version("learning-authoring-tool")
    except importlib.metadata.PackageNotFoundError:
        version = "uninstalled-source"
    parser = argparse.ArgumentParser(
        prog="learning-authoring-product",
        description=(
            "Build or register optional Teacher/Student product surfaces from existing "
            "authoring artifacts. These commands never generate learning content."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    role_apps = subcommands.add_parser(
        "build-role-apps",
        help="build separate Teacher and Student apps offline; never deploy",
    )
    role_apps.add_argument("run_dir", type=_path)
    role_apps.add_argument("output_dir", type=_path)
    role_apps.add_argument(
        "--local-preview",
        action="store_true",
        help="explicit local, unapproved preview; not deployable",
    )
    role_apps.add_argument("--review-supabase-url")
    role_apps.add_argument("--review-supabase-publishable-key")
    role_apps.add_argument("--extractor-review", default="extraction-review.html")
    role_apps.add_argument("--kc-recall-review", default="kc-recall.html")
    role_apps.add_argument("--kc-scroll-review", default="kc-scroll.html")
    role_apps.add_argument("--quiz-review", default="quiz-review.html")

    authoring = subcommands.add_parser(
        "export-authoring-registration",
        help="export immutable authoring package SQL offline; no grants",
    )
    authoring.add_argument("run_dir", type=_path)
    authoring.add_argument("output_sql", type=_path)

    learning = subcommands.add_parser(
        "export-learning-registration",
        help="export insert-only learning snapshot SQL offline",
    )
    learning.add_argument("run_dir", type=_path)
    learning.add_argument("output_sql", type=_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.command == "build-role-apps":
        if bool(args.review_supabase_url) != bool(args.review_supabase_publishable_key):
            parser.error("Supply both the Supabase project URL and its public browser key")
        backend = (
            ReviewBackendConfig(
                args.review_supabase_url,
                args.review_supabase_publishable_key,
            )
            if args.review_supabase_url
            else None
        )
        try:
            result = build_role_apps(
                args.run_dir,
                args.output_dir,
                review_backend=backend,
                local_preview=args.local_preview,
                review_files=ReviewFiles(
                    args.extractor_review,
                    args.kc_recall_review,
                    args.kc_scroll_review,
                    args.quiz_review,
                ),
            )
        except (PublishSafetyError, ValueError, OSError) as exc:
            print(json.dumps({"built": False, "error": str(exc)}), file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "built": True,
                    "output_dir": str(args.output_dir),
                    "deployment_performed": False,
                    "manifest": result,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    exporter = (
        export_authoring_registration
        if args.command == "export-authoring-registration"
        else export_learning_registration
    )
    try:
        result = exporter(args.run_dir, args.output_sql)
    except (PublishSafetyError, ValueError, OSError) as exc:
        print(json.dumps({"exported": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
