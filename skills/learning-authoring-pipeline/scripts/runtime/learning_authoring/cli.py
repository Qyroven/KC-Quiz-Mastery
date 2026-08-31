"""Subscription-native authoring commands.

Provider adapters and Teacher/Student product exports live in separate modules so installing the
Agent Skill never implies an API runtime or a deployed learning product.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Any

from learning_authoring.agent_session import (
    agent_bundle,
    agent_context,
    agent_import,
    agent_init,
    agent_schema,
    prepare_agent_task,
    read_agent_task_input,
)
from learning_authoring.approval import approve_extraction
from learning_authoring.artifacts import RunArtifacts, read_json, sha256_file, write_json
from learning_authoring.kc_review import build_kc_demo
from learning_authoring.product.bundle_portal import build_bundle_portal
from learning_authoring.product.showcase import (
    LEGACY_MANAGED_BY,
    MANAGED_BY,
    MANIFEST_NAME,
    PublishSafetyError,
    ReviewBackendConfig,
    ReviewFiles,
    build_showcase,
)
from learning_authoring.quiz_review import build_quiz_review
from learning_authoring.review import build_review
from learning_authoring.source import DEFAULT_RENDER_DPI, preflight_source

PORTAL_BUILD_RECORD = "portal-build-record.json"
NATIVE_COMMANDS = (
    "source-preflight",
    "agent-init",
    "agent-bundle",
    "agent-context",
    "agent-schema",
    "agent-task",
    "agent-read",
    "agent-import",
    "review",
    "approve",
    "kc-review",
    "quiz-review",
    "bundle-portal-build",
    "portal-build",
    "status",
)


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _optional_positive_int(value: str) -> int | None:
    if value.strip().casefold() in {"none", "off", "unlimited"}:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer or 'none'") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("expected a positive integer or 'none'")
    return parsed


def _add_assessment_policy_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--variants-per-kc",
        type=int,
        default=None,
        help="explicit legacy override only; default plans evidence-based assessment slots",
    )
    parser.add_argument("--min-slots-per-kc", type=int, default=1)
    parser.add_argument("--max-slots-per-kc", type=_optional_positive_int, default=None)
    parser.add_argument(
        "--variants-per-slot",
        type=_optional_positive_int,
        default=None,
        help="optional exact bank depth per slot; otherwise the agent justifies each count",
    )
    parser.add_argument("--max-variants-per-slot", type=_optional_positive_int, default=None)
    parser.add_argument(
        "--total-question-budget",
        type=_optional_positive_int,
        default=None,
        help="optional explicit upper bound; no default cap and no silent KC omissions",
    )


def _assessment_policy(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "variants_per_kc": args.variants_per_kc,
        "min_slots_per_kc": args.min_slots_per_kc,
        "max_slots_per_kc": args.max_slots_per_kc,
        "variants_per_slot": args.variants_per_slot,
        "max_variants_per_slot": args.max_variants_per_slot,
        "total_question_budget": args.total_question_budget,
    }


def _add_context_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--context-file",
        type=_path,
        action="append",
        default=[],
        help="supplementary lecturer material, any format; repeat for multiple files",
    )
    parser.add_argument(
        "--context-text",
        action="append",
        default=[],
        help="free-form supplementary teaching context from the user's message; repeatable",
    )


def _add_agent_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--reviewer-mode",
        choices=("independent", "self_review"),
        help=(
            "quiz-review task only: use a separate agent context by default; "
            "declare self_review if independent review is unavailable"
        ),
    )
    parser.add_argument(
        "--allow-proposed-extraction-demo",
        action="store_true",
        help=(
            "KC only: use a proposed extraction for a visibly marked demo; does not create approval"
        ),
    )
    parser.add_argument("--kc", type=_path, help="Quiz only: KC candidate JSON path")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--include-kc",
        action="append",
        help="Quiz only: explicit KC ID; repeat for more KCs",
    )
    selection.add_argument(
        "--include-all-kcs",
        action="store_true",
        help="Quiz only: include every Leaf KC in source order",
    )
    _add_assessment_policy_options(parser)
    parser.add_argument(
        "--language",
        default="source",
        help="learner-facing language; 'source' follows the selected KC set",
    )


def _parser() -> argparse.ArgumentParser:
    try:
        version = importlib.metadata.version("learning-authoring-tool")
    except importlib.metadata.PackageNotFoundError:
        version = "uninstalled-source"
    parser = argparse.ArgumentParser(
        prog="learning-authoring",
        description=(
            "Subscription-native Extract -> KC -> Quiz drafts and connected local review. "
            "The host coding agent authors candidates; these commands make no model-provider "
            "API calls and need no API key or .env setup."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version}")
    subcommands = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{" + ",".join(NATIVE_COMMANDS) + "}",
    )

    source_preflight = subcommands.add_parser(
        "source-preflight",
        help="inspect one PDF and intended run directory without writing or calling a model",
    )
    source_preflight.add_argument("pdf", type=_path)
    source_preflight.add_argument("run_dir", type=_path)
    source_preflight.add_argument("--render-dpi", type=int, default=DEFAULT_RENDER_DPI)

    agent_init_parser = subcommands.add_parser(
        "agent-init",
        help="prepare and render a source for a zero-provider-API agent session",
    )
    agent_init_parser.add_argument("pdf", type=_path)
    agent_init_parser.add_argument("run_dir", type=_path)
    agent_init_parser.add_argument("--render-dpi", type=int, default=DEFAULT_RENDER_DPI)
    _add_context_options(agent_init_parser)

    bundle_parser = subcommands.add_parser(
        "agent-bundle",
        help="freeze ordered one-PDF Extraction subruns for shared KC authoring",
    )
    bundle_parser.add_argument("run_dir", type=_path)
    bundle_parser.add_argument(
        "source_run",
        type=_path,
        nargs="+",
        help="prepared one-PDF subrun; order is preserved and one or more are required",
    )
    _add_context_options(bundle_parser)

    context_parser = subcommands.add_parser(
        "agent-context",
        help="freeze optional context for a prepared PDF without modifying Extraction",
    )
    context_parser.add_argument("run_dir", type=_path)
    _add_context_options(context_parser)

    agent_schema_parser = subcommands.add_parser(
        "agent-schema",
        help="emit the strict JSON candidate schema for an agent-native stage",
    )
    agent_schema_parser.add_argument("stage", choices=("extraction", "kc", "quiz", "quiz-review"))
    agent_schema_parser.add_argument(
        "--legacy-quiz",
        action="store_true",
        help="emit the legacy per-KC Quiz schema rather than adaptive slots",
    )

    agent_task_parser = subcommands.add_parser(
        "agent-task",
        help="write a self-contained instructions/schema/input package for the host coding agent",
    )
    agent_task_parser.add_argument("stage", choices=("extraction", "kc", "quiz", "quiz-review"))
    agent_task_parser.add_argument("run_dir", type=_path)
    _add_agent_runtime_options(agent_task_parser)

    agent_read_parser = subcommands.add_parser(
        "agent-read", help="read the frozen input index or selected batches without creating files"
    )
    agent_read_parser.add_argument("task_package", type=_path)
    agent_read_parser.add_argument("--batch", action="append", default=[])
    agent_read_parser.add_argument("--context-id", action="append", default=[])

    agent_import_parser = subcommands.add_parser(
        "agent-import",
        help="preserve and validate candidate JSON produced in the subscription session",
    )
    agent_import_parser.add_argument("stage", choices=("extraction", "kc", "quiz", "quiz-review"))
    agent_import_parser.add_argument("run_dir", type=_path)
    agent_import_parser.add_argument("candidate_json", type=_path)
    agent_import_parser.add_argument(
        "--task-package",
        type=_path,
        help="validate against the exact frozen agent-task; do not repeat runtime options",
    )
    _add_agent_runtime_options(agent_import_parser)

    review = subcommands.add_parser("review", help="build the local extraction review page")
    review.add_argument("run_dir", type=_path)

    approve = subcommands.add_parser("approve", help="approve a reviewed extraction")
    approve.add_argument("run_dir", type=_path)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--note")
    approve.add_argument("--acknowledge-warnings", action="store_true")

    kc_review = subcommands.add_parser(
        "kc-review", help="build the local source-first KC review pages"
    )
    kc_review.add_argument("run_dir", type=_path)
    kc_review.add_argument("--candidate", type=_path, action="append")
    kc_review.add_argument(
        "--allow-proposed-extraction-demo",
        action="store_true",
        help="build a visibly marked demo from proposed extraction; never implies approval",
    )

    quiz_review = subcommands.add_parser(
        "quiz-review", help="build a local review page from one canonical Quiz batch"
    )
    quiz_review.add_argument("run_dir", type=_path)
    quiz_review.add_argument("--candidate", type=_path)
    quiz_review.add_argument("--output-name", default="quiz-review.html")

    bundle_portal_build = subcommands.add_parser(
        "bundle-portal-build",
        help="build a connected, read-only review portal from an exact source bundle",
    )
    bundle_portal_build.add_argument("bundle_root", type=_path)
    bundle_portal_build.add_argument("--output-dir", type=_path, required=True)
    bundle_portal_build.add_argument(
        "--kc",
        type=_path,
        help="shared KC candidate (default: <bundle-root>/kc-proposed.json)",
    )
    bundle_portal_build.add_argument(
        "--quiz-dir",
        type=_path,
        help="Quiz artifact directory (default: <bundle-root>/quiz)",
    )

    portal_build = subcommands.add_parser(
        "portal-build",
        help="build one connected, allowlisted review portal from an exact run",
    )
    portal_build.add_argument("run_dir", type=_path)
    portal_build.add_argument(
        "--output-dir",
        type=_path,
        help="destination directory (default: <run-dir>/connected-portal)",
    )
    portal_build.add_argument("--extractor-review", default="extraction-review.html")
    portal_build.add_argument("--kc-recall-review", default="kc-recall.html")
    portal_build.add_argument("--kc-scroll-review", default="kc-scroll.html")
    portal_build.add_argument("--quiz-review", default="quiz-review.html")
    portal_build.add_argument(
        "--with-learning",
        action="store_true",
        help="include local practice, evidence and provisional mastery (requires local Node.js)",
    )
    portal_build.add_argument(
        "--review-supabase-url",
        help="exact public Supabase project URL for shared review",
    )
    portal_build.add_argument(
        "--review-supabase-publishable-key",
        help="public Supabase publishable/legacy anon browser key (never service-role)",
    )

    status = subcommands.add_parser("status", help="show canonical run artifacts")
    status.add_argument("run_dir", type=_path)
    return parser


def _validate_agent_runtime_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    argv: list[str],
) -> None:
    has_task = bool(getattr(args, "task_package", None))
    has_policy = any(
        value is not None and (name != "min_slots_per_kc" or value != 1)
        for name, value in _assessment_policy(args).items()
    )
    if has_task:
        stage_flags = {
            "--allow-proposed-extraction-demo",
            "--kc",
            "--include-all-kcs",
            "--include-kc",
            "--variants-per-kc",
            "--min-slots-per-kc",
            "--max-slots-per-kc",
            "--variants-per-slot",
            "--max-variants-per-slot",
            "--total-question-budget",
            "--language",
            "--reviewer-mode",
        }
        # Even an explicitly supplied default (e.g. --language source) is an override.
        if any(token.split("=", 1)[0] in stage_flags for token in argv) or (
            args.allow_proposed_extraction_demo
            or args.kc
            or args.include_all_kcs
            or args.include_kc
            or has_policy
            or args.language != "source"
        ):
            parser.error("--task-package freezes source and runtime; do not override stage options")
        return
    if args.command == "agent-import":
        # Imports preserve candidate bytes before binding current inputs or reporting errors.
        return
    if args.reviewer_mode is not None and args.stage != "quiz-review":
        parser.error("--reviewer-mode is valid only for a quiz-review task")
    if args.stage != "kc" and args.allow_proposed_extraction_demo:
        parser.error("--allow-proposed-extraction-demo is valid only for the KC stage")
    has_quiz_selection = bool(args.include_all_kcs or args.include_kc)
    if args.stage == "quiz" and not has_quiz_selection:
        parser.error("Quiz agent stages require --include-kc or --include-all-kcs")
    if args.stage != "quiz" and (has_quiz_selection or args.kc is not None):
        parser.error("--kc and KC selection options are valid only for the Quiz stage")
    if args.stage != "quiz" and has_policy:
        parser.error("assessment slot and variant options are valid only for the Quiz stage")


def _portal_manifest_for_run(root: Path, output_dir: Path) -> dict[str, Any]:
    """Read only a real portal manifest whose source identity belongs to this run."""

    manifest_path = output_dir / MANIFEST_NAME
    if output_dir.is_symlink() or manifest_path.is_symlink():
        raise ValueError("portal output or manifest must not be a symlink")
    manifest = read_json(manifest_path)
    source = read_json(RunArtifacts(root).source_manifest)["source"]
    if (
        manifest.get("schema_version")
        not in {
            "learning-authoring-showcase.v1",
            "learning-authoring-showcase.v2",
            "learning-authoring-showcase.v3",
            "learning-authoring-showcase.v4",
        }
        or manifest.get("managed_by") not in {MANAGED_BY, LEGACY_MANAGED_BY}
        or manifest.get("source_run") != root.name
        or manifest.get("source")
        != {name: source[name] for name in ("filename", "source_id", "page_count")}
        or not (output_dir / "index.html").is_file()
    ):
        raise ValueError("portal manifest does not identify a completed build for this run")
    return manifest


def _record_portal_build(root: Path, output_dir: Path, manifest: dict[str, Any]) -> None:
    """Record a completed local build without changing any generation artifact."""

    root = root.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    try:
        if _portal_manifest_for_run(root, output_dir) != manifest:
            raise ValueError("Built portal manifest differs from the builder's result")
        write_json(
            root / PORTAL_BUILD_RECORD,
            {
                "schema_version": "portal-build-record.v1",
                "run_dir": str(root),
                "output_dir": str(output_dir),
                "manifest_sha256": sha256_file(output_dir / MANIFEST_NAME),
                "source_manifest_sha256": sha256_file(RunArtifacts(root).source_manifest),
            },
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise PublishSafetyError(f"Cannot verify and record completed portal build: {exc}") from exc


def _connected_portal_status(root: Path) -> dict[str, Any] | None:
    record_path = root / PORTAL_BUILD_RECORD
    try:
        has_record = record_path.exists() or record_path.is_symlink()
        if has_record:
            if record_path.is_symlink():
                return None
            record = read_json(record_path)
            if record.get("schema_version") != "portal-build-record.v1" or (
                record.get("run_dir") != str(root)
            ):
                return None
            output_dir = Path(record["output_dir"])
            if not output_dir.is_absolute() or output_dir.resolve() != output_dir:
                return None
            if record["source_manifest_sha256"] != sha256_file(
                RunArtifacts(root).source_manifest
            ) or record["manifest_sha256"] != sha256_file(output_dir / MANIFEST_NAME):
                return None
        else:
            # Older builds have no pointer record but used this fixed local destination.
            output_dir = root / "connected-portal"
        _portal_manifest_for_run(root, output_dir)
        return {
            "output_dir": str(output_dir),
            "manifest": str(output_dir / MANIFEST_NAME),
            "recorded_build": has_record,
        }
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _status(run_dir: Path) -> dict[str, Any]:
    root = run_dir.expanduser().resolve()
    artifacts = RunArtifacts(root)
    quiz_artifacts = RunArtifacts(root / "quiz")
    paths = {
        "source_ready": artifacts.source_manifest,
        "authoring_context": root / "authoring-context.json",
        "extraction_proposed": artifacts.proposed,
        "extraction_review_built": artifacts.review_html,
        "extraction_approved": artifacts.approved,
        "kc_request_preview": artifacts.kc_request_preview,
        "kc_proposed": artifacts.kc_proposed,
        "quiz_request_preview": quiz_artifacts.quiz_request_preview,
        "quiz_proposed": quiz_artifacts.quiz_proposed,
        "quiz_review_built": artifacts.quiz_review_html,
        "quiz_semantic_report": root / "quiz" / "quiz-semantic-audit.json",
    }
    result: dict[str, Any] = {
        "run_dir": str(root),
        "artifacts": {name: path.is_file() for name, path in paths.items()},
    }
    portal = _connected_portal_status(root)
    result["artifacts"]["connected_portal_built"] = portal is not None
    if portal is not None:
        result["connected_portal"] = portal
    if artifacts.checkpoint.is_file():
        checkpoint = read_json(artifacts.checkpoint)
        result["response_id"] = checkpoint.get("response_id")
        result["response_status"] = checkpoint.get("status")
    if artifacts.metrics.is_file():
        result["extraction_metrics"] = read_json(artifacts.metrics)
    if artifacts.kc_metrics.is_file():
        result["kc_metrics"] = read_json(artifacts.kc_metrics)
    if quiz_artifacts.quiz_metrics.is_file():
        result["quiz_metrics"] = read_json(quiz_artifacts.quiz_metrics)
    if quiz_artifacts.quiz_proposed.is_file():
        from learning_authoring.quiz_review_state import load_quiz_semantic_state

        state = load_quiz_semantic_state(root)
        result["quiz_initial_check"] = {
            key: state[key] for key in ("status", "counts", "reason", "reasons") if key in state
        }
    return result


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = _parser()
    args = parser.parse_args(raw_argv)

    if args.command in {"agent-task", "agent-import"}:
        _validate_agent_runtime_args(parser, args, raw_argv)

    if args.command == "source-preflight":
        try:
            result = preflight_source(args.pdf, args.run_dir, render_dpi=args.render_dpi)
        except (OSError, ValueError) as exc:
            print(json.dumps({"ready": False, "error": str(exc)}, indent=2), file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 1
    if args.command == "agent-init":
        result = agent_init(
            args.pdf,
            args.run_dir,
            render_dpi=args.render_dpi,
            context_files=tuple(args.context_file),
            context_texts=tuple(args.context_text),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "agent-bundle":
        result = agent_bundle(
            args.run_dir,
            tuple(args.source_run),
            context_files=tuple(args.context_file),
            context_texts=tuple(args.context_text),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "agent-context":
        result = agent_context(
            args.run_dir,
            context_files=tuple(args.context_file),
            context_texts=tuple(args.context_text),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "agent-schema":
        if args.legacy_quiz and args.stage != "quiz":
            parser.error("--legacy-quiz is valid only for the Quiz stage")
        print(
            json.dumps(
                agent_schema(args.stage, legacy_quiz=args.legacy_quiz),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "agent-read":
        result = read_agent_task_input(
            args.task_package, batch_ids=tuple(args.batch), context_ids=tuple(args.context_id)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "agent-task":
        result = prepare_agent_task(
            args.stage,
            args.run_dir,
            allow_proposed_extraction_demo=args.allow_proposed_extraction_demo,
            kc_path=args.kc,
            selected_kc_ids=tuple(args.include_kc or ()),
            include_all_kcs=args.include_all_kcs,
            **_assessment_policy(args),
            language=args.language,
            reviewer_mode=args.reviewer_mode or "independent",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "agent-import":
        result = agent_import(
            args.stage,
            args.run_dir,
            args.candidate_json,
            allow_proposed_extraction_demo=args.allow_proposed_extraction_demo,
            kc_path=args.kc,
            selected_kc_ids=tuple(args.include_kc or ()),
            include_all_kcs=args.include_all_kcs,
            **_assessment_policy(args),
            language=args.language,
            task_package=args.task_package,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "review":
        print(build_review(args.run_dir))
        return 0
    if args.command == "approve":
        record = approve_extraction(
            args.run_dir,
            reviewer=args.reviewer,
            note=args.note,
            acknowledge_warnings=args.acknowledge_warnings,
        )
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    if args.command == "kc-review":
        candidate_dirs = args.candidate
        if candidate_dirs is None and RunArtifacts(args.run_dir).kc_proposed.is_file():
            candidate_dirs = [args.run_dir]
        output = build_kc_demo(
            args.run_dir,
            candidate_dirs,
            allow_proposed_extraction_demo=args.allow_proposed_extraction_demo,
        )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    if args.command == "quiz-review":
        candidate = args.candidate or (args.run_dir / "quiz")
        output = build_quiz_review(
            args.run_dir,
            candidate_dir=candidate,
            output_name=args.output_name,
        )
        print(output)
        return 0
    if args.command == "bundle-portal-build":
        try:
            manifest = build_bundle_portal(
                args.bundle_root,
                args.output_dir,
                kc_path=args.kc,
                quiz_dir=args.quiz_dir,
            )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print(
                json.dumps(
                    {
                        "built": False,
                        "output_dir": str(args.output_dir.expanduser().resolve()),
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1
        print(
            json.dumps(
                {
                    "built": True,
                    "output_dir": str(args.output_dir.expanduser().resolve()),
                    "manifest": manifest,
                    "deployment_performed": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "portal-build":
        output_dir = args.output_dir or (args.run_dir / "connected-portal")
        if bool(args.review_supabase_url) != bool(args.review_supabase_publishable_key):
            parser.error(
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
        try:
            manifest = build_showcase(
                args.run_dir,
                output_dir,
                review_files=ReviewFiles(
                    extractor=args.extractor_review,
                    kc_recall=args.kc_recall_review,
                    kc_scroll=args.kc_scroll_review,
                    quiz=args.quiz_review,
                ),
                review_backend=review_backend,
                include_learning=args.with_learning,
            )
            _record_portal_build(args.run_dir, output_dir, manifest)
        except PublishSafetyError as exc:
            print(
                json.dumps(
                    {
                        "built": False,
                        "output_dir": str(output_dir.expanduser().resolve()),
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1
        print(
            json.dumps(
                {
                    "built": True,
                    "output_dir": str(output_dir.expanduser().resolve()),
                    "manifest": manifest,
                    "deployment_performed": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "status":
        print(json.dumps(_status(args.run_dir), ensure_ascii=False, indent=2))
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
