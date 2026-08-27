"""Local commands for subscription-native authoring and explicit legacy compatibility."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from learning_authoring.agent_session import (
    agent_context,
    agent_import,
    agent_init,
    agent_schema,
    prepare_agent_task,
)
from learning_authoring.approval import approve_extraction
from learning_authoring.artifacts import RunArtifacts, read_json, sha256_file, write_json
from learning_authoring.kc_review import build_kc_demo
from learning_authoring.quiz_review import build_quiz_review
from learning_authoring.review import build_review
from learning_authoring.showcase import (
    LEGACY_MANAGED_BY,
    MANAGED_BY,
    MANIFEST_NAME,
    PublishSafetyError,
    ReviewBackendConfig,
    ReviewFiles,
    build_showcase,
)
from learning_authoring.source import DEFAULT_RENDER_DPI, preflight_source

if TYPE_CHECKING:
    from learning_authoring.extractor import ExtractionConfig
    from learning_authoring.kc import KCConfig
    from learning_authoring.quiz import QuizConfig

PORTAL_BUILD_RECORD = "portal-build-record.json"
NATIVE_COMMANDS = (
    "source-preflight", "agent-init", "agent-context", "agent-schema", "agent-task",
    "agent-import", "batch-plan", "batch-preflight", "review", "approve", "kc-review",
    "quiz-review", "portal-build", "learning-register", "status",
)
LEGACY_API_COMMANDS = frozenset({
    "doctor", "extract", "batch-extract", "kc-preview", "kc-generate",
    "quiz-preview", "quiz-generate",
})


def _legacy_command(
    subcommands: Any, name: str, description: str,
) -> argparse.ArgumentParser:
    # Omitting ``help`` keeps compatibility aliases out of root help.  The explicit
    # native metavar below also keeps them out of the automatically generated usage.
    return subcommands.add_parser(
        name,
        description=(
            f"Legacy model-provider API adapter: {description}. "
            "This is not subscription-native authoring; live calls require the optional "
            "legacy-api extra and provider credentials. Use agent-init, agent-task, "
            "and agent-import for the coding-agent subscription workflow."
        ),
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


def _add_extraction_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default=os.getenv("LEARNING_AUTHORING_MODEL", "gpt-5.6-sol"),
    )
    parser.add_argument(
        "--reasoning-effort",
        default=os.getenv("LEARNING_AUTHORING_REASONING_EFFORT", "high"),
    )
    parser.add_argument("--response-mode", choices=("background", "sync"), default="background")
    parser.add_argument("--render-dpi", type=int, default=DEFAULT_RENDER_DPI)
    parser.add_argument("--pdf-detail", choices=("auto", "low", "high"), default="high")
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--no-targeted-repair", action="store_true")
    parser.add_argument("--repair-max-attempts", type=int, default=2)
    parser.add_argument(
        "--repair-max-candidate-pages",
        type=_optional_positive_int,
        default=12,
        metavar="N|none",
        help="absolute automatic-repair page budget; use 'none' to disable",
    )
    parser.add_argument(
        "--repair-systemic-guard-min-candidate-pages",
        type=int,
        default=4,
        metavar="N",
        help="minimum candidates before the proportional systemic guard applies",
    )
    parser.add_argument(
        "--repair-systemic-guard-max-page-fraction",
        type=float,
        default=0.5,
        metavar="FRACTION",
        help="block automatic repair when candidate coverage exceeds this fraction",
    )
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--prompt", type=_path)
    parser.add_argument("--repair-prompt", type=_path)


def _add_kc_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default=os.getenv("LEARNING_AUTHORING_MODEL", "gpt-5.6-sol"),
    )
    parser.add_argument(
        "--reasoning-effort",
        default=os.getenv("LEARNING_AUTHORING_REASONING_EFFORT", "high"),
    )
    parser.add_argument("--response-mode", choices=("background", "sync"), default="background")
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--prompt-dir", type=_path)


def _add_quiz_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default=os.getenv("LEARNING_AUTHORING_MODEL", "gpt-5.6-sol"),
    )
    parser.add_argument(
        "--reasoning-effort",
        default=os.getenv("LEARNING_AUTHORING_REASONING_EFFORT", "high"),
    )
    parser.add_argument("--response-mode", choices=("background", "sync"), default="background")
    parser.add_argument("--max-output-tokens", type=int, default=64000)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--prompt-dir", type=_path)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--include-kc",
        action="append",
        help="explicit KC ID to include; repeat for more KCs",
    )
    selection.add_argument(
        "--include-all-kcs",
        action="store_true",
        help="include every Leaf KC in source order",
    )
    _add_assessment_policy_options(parser)
    parser.add_argument(
        "--language",
        default="source",
        help="learner-facing language; 'source' follows the selected KC set",
    )


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
        "--context-file", type=_path, action="append", default=[],
        help="supplementary lecturer material, any format; repeat for multiple files",
    )
    parser.add_argument(
        "--context-text", action="append", default=[],
        help="free-form supplementary teaching context from the user's message; repeatable",
    )


def _add_agent_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--reviewer-mode", choices=("independent", "self_review"),
        help=("quiz-review task only: use a separate agent context by default; "
              "declare self_review if independent review is unavailable"),
    )
    parser.add_argument(
        "--allow-proposed-extraction-demo",
        action="store_true",
        help=(
            "KC only: use a proposed extraction for a visibly marked demo; "
            "does not create approval"
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
    parser.add_argument("--env-file", type=_path, help=argparse.SUPPRESS)
    subcommands = parser.add_subparsers(
        dest="command", required=True, metavar="{" + ",".join(NATIVE_COMMANDS) + "}",
    )

    doctor = _legacy_command(
        subcommands, "doctor", "verify API authentication and model visibility without generation",
    )
    doctor.add_argument(
        "--model",
        default=os.getenv("LEARNING_AUTHORING_MODEL", "gpt-5.6-sol"),
    )
    doctor.add_argument("--timeout", type=float, default=20.0)

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
        "--legacy-quiz", action="store_true",
        help="emit the legacy per-KC Quiz schema rather than adaptive slots",
    )

    agent_task_parser = subcommands.add_parser(
        "agent-task",
        help="write a self-contained instructions/schema/input package for the host coding agent",
    )
    agent_task_parser.add_argument("stage", choices=("extraction", "kc", "quiz", "quiz-review"))
    agent_task_parser.add_argument("run_dir", type=_path)
    _add_agent_runtime_options(agent_task_parser)

    agent_import_parser = subcommands.add_parser(
        "agent-import",
        help="preserve and validate candidate JSON produced in the subscription session",
    )
    agent_import_parser.add_argument("stage", choices=("extraction", "kc", "quiz", "quiz-review"))
    agent_import_parser.add_argument("run_dir", type=_path)
    agent_import_parser.add_argument("candidate_json", type=_path)
    agent_import_parser.add_argument(
        "--task-package", type=_path,
        help="validate against the exact frozen agent-task; do not repeat runtime options",
    )
    _add_agent_runtime_options(agent_import_parser)

    extract = _legacy_command(subcommands, "extract", "extract a PDF to a proposed artifact")
    extract.add_argument("pdf", type=_path)
    extract.add_argument("run_dir", type=_path)
    _add_extraction_options(extract)

    batch_plan = subcommands.add_parser(
        "batch-plan", help="inventory PDFs and create an explicit day-selection manifest"
    )
    batch_plan.add_argument("source_dir", type=_path)
    batch_plan.add_argument("manifest", type=_path)
    batch_plan.add_argument("--runs-dir", type=_path, required=True)

    batch_preflight = subcommands.add_parser(
        "batch-preflight", help="validate every selected batch PDF without model calls"
    )
    batch_preflight.add_argument("manifest", type=_path)

    batch_extract = _legacy_command(
        subcommands, "batch-extract", "run a preflighted batch with isolated checkpoints",
    )
    batch_extract.add_argument("manifest", type=_path)
    batch_extract.add_argument("--continue-on-error", action="store_true")
    _add_extraction_options(batch_extract)

    review = subcommands.add_parser("review", help="build the local extraction review page")
    review.add_argument("run_dir", type=_path)

    approve = subcommands.add_parser("approve", help="approve a reviewed extraction")
    approve.add_argument("run_dir", type=_path)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--note")
    approve.add_argument("--acknowledge-warnings", action="store_true")

    kc_preview = _legacy_command(
        subcommands, "kc-preview", "write the exact KC request without calling the API",
    )
    kc_preview.add_argument("run_dir", type=_path)
    kc_preview.add_argument("--output-dir", type=_path)
    _add_kc_options(kc_preview)

    kc_generate = _legacy_command(
        subcommands, "kc-generate", "generate proposed KCs from approved extraction JSON only",
    )
    kc_generate.add_argument("run_dir", type=_path)
    kc_generate.add_argument("--output-dir", type=_path)
    _add_kc_options(kc_generate)

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

    quiz_preview = _legacy_command(
        subcommands, "quiz-preview", "freeze the KC-to-Quiz request without an API call",
    )
    quiz_preview.add_argument("run_dir", type=_path)
    quiz_preview.add_argument("--kc", type=_path)
    quiz_preview.add_argument("--output-dir", type=_path)
    _add_quiz_options(quiz_preview)

    quiz_generate = _legacy_command(
        subcommands, "quiz-generate", "generate one unapproved Quiz batch without repair",
    )
    quiz_generate.add_argument("run_dir", type=_path)
    quiz_generate.add_argument("--kc", type=_path)
    quiz_generate.add_argument("--output-dir", type=_path)
    _add_quiz_options(quiz_generate)

    quiz_review = subcommands.add_parser(
        "quiz-review", help="build a local review page from one canonical Quiz batch"
    )
    quiz_review.add_argument("run_dir", type=_path)
    quiz_review.add_argument("--candidate", type=_path)
    quiz_review.add_argument("--output-name", default="quiz-review.html")

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
        "--with-learning", action="store_true",
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

    learning_register = subcommands.add_parser(
        "learning-register",
        help="export insert-only learning snapshot SQL offline; never connects to a database",
    )
    learning_register.add_argument("run_dir", type=_path)
    learning_register.add_argument("output_sql", type=_path)

    status = subcommands.add_parser("status", help="show canonical run artifacts")
    status.add_argument("run_dir", type=_path)
    return parser


def _load_env_before_parser(argv: list[str]) -> None:
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--env-file", type=_path)
    bootstrap.add_argument("command", nargs="?")
    known, _ = bootstrap.parse_known_args(argv)
    if known.env_file is None:
        return
    if known.command not in LEGACY_API_COMMANDS:
        bootstrap.error(
            "--env-file is only for historical model-provider API commands; "
            "native agent and local review commands need no .env file"
        )
    if not known.env_file.is_file():
        bootstrap.error(f"env file does not exist: {known.env_file}")
    try:
        from dotenv import dotenv_values, load_dotenv
    except ImportError:
        bootstrap.error(
            "explicit legacy --env-file loading requires the optional 'legacy-api' extra; "
            "subscription-native commands do not use dotenv"
        )
    values = dotenv_values(known.env_file)
    load_dotenv(known.env_file, override=True)
    if not (values.get("OPENAI_BASE_URL") or "").strip():
        os.environ.pop("OPENAI_BASE_URL", None)


def _config(args: argparse.Namespace) -> ExtractionConfig:
    from learning_authoring.extractor import ExtractionConfig
    from learning_authoring.provider import normalized_base_url

    defaults = ExtractionConfig()
    return ExtractionConfig(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        response_mode=args.response_mode,
        render_dpi=args.render_dpi,
        pdf_detail=args.pdf_detail,
        max_output_tokens=args.max_output_tokens,
        targeted_repair=not args.no_targeted_repair,
        repair_max_attempts=args.repair_max_attempts,
        repair_max_candidate_pages=args.repair_max_candidate_pages,
        repair_systemic_guard_min_candidate_pages=(args.repair_systemic_guard_min_candidate_pages),
        repair_systemic_guard_max_page_fraction=(args.repair_systemic_guard_max_page_fraction),
        poll_interval_seconds=args.poll_interval,
        timeout_seconds=args.timeout,
        prompt_path=args.prompt or defaults.prompt_path,
        repair_prompt_path=args.repair_prompt or defaults.repair_prompt_path,
        api_key=os.getenv("OPENAI_API_KEY") or None,
        base_url=normalized_base_url(os.getenv("OPENAI_BASE_URL")),
    )


def _kc_config(args: argparse.Namespace) -> KCConfig:
    from learning_authoring.kc import KCConfig
    from learning_authoring.provider import normalized_base_url

    defaults = KCConfig()
    return KCConfig(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        response_mode=args.response_mode,
        max_output_tokens=args.max_output_tokens,
        poll_interval_seconds=args.poll_interval,
        timeout_seconds=args.timeout,
        prompt_dir=args.prompt_dir or defaults.prompt_dir,
        api_key=os.getenv("OPENAI_API_KEY") or None,
        base_url=normalized_base_url(os.getenv("OPENAI_BASE_URL")),
    )


def _quiz_config(args: argparse.Namespace) -> QuizConfig:
    from learning_authoring.provider import normalized_base_url
    from learning_authoring.quiz import QuizConfig

    defaults = QuizConfig()
    return QuizConfig(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        response_mode=args.response_mode,
        max_output_tokens=args.max_output_tokens,
        prompt_dir=args.prompt_dir or defaults.prompt_dir,
        selected_kc_ids=tuple(args.include_kc or ()),
        include_all_kcs=args.include_all_kcs,
        **_assessment_policy(args),
        language=args.language,
        poll_interval_seconds=args.poll_interval,
        timeout_seconds=args.timeout,
        api_key=os.getenv("OPENAI_API_KEY") or None,
        base_url=normalized_base_url(os.getenv("OPENAI_BASE_URL")),
    )


def _validate_agent_runtime_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace, argv: list[str],
) -> None:
    has_task = bool(getattr(args, "task_package", None))
    has_policy = any(
        value is not None and (name != "min_slots_per_kc" or value != 1)
        for name, value in _assessment_policy(args).items()
    )
    if has_task:
        stage_flags = {
            "--allow-proposed-extraction-demo", "--kc", "--include-all-kcs", "--include-kc",
            "--variants-per-kc", "--min-slots-per-kc", "--max-slots-per-kc",
            "--variants-per-slot", "--max-variants-per-slot", "--total-question-budget",
            "--language", "--reviewer-mode",
        }
        # Even an explicitly supplied default (e.g. --language source) is an override.
        if any(token.split("=", 1)[0] in stage_flags for token in argv) or (
            args.allow_proposed_extraction_demo or args.kc or args.include_all_kcs
            or args.include_kc or has_policy or args.language != "source"
        ):
            parser.error("--task-package freezes source and runtime; do not override stage options")
        return
    if args.reviewer_mode is not None and args.stage != "quiz-review":
        parser.error("--reviewer-mode is valid only for a quiz-review task")
    if args.command == "agent-import" and args.stage == "quiz-review":
        parser.error("quiz-review import requires --task-package")
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
        manifest.get("schema_version") not in {
            "learning-authoring-showcase.v1", "learning-authoring-showcase.v2",
            "learning-authoring-showcase.v3", "learning-authoring-showcase.v4",
        }
        or manifest.get("managed_by") not in {MANAGED_BY, LEGACY_MANAGED_BY}
        or manifest.get("source_run") != root.name
        or manifest.get("source") != {
            name: source[name] for name in ("filename", "source_id", "page_count")
        }
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
        write_json(root / PORTAL_BUILD_RECORD, {
            "schema_version": "portal-build-record.v1",
            "run_dir": str(root),
            "output_dir": str(output_dir),
            "manifest_sha256": sha256_file(output_dir / MANIFEST_NAME),
            "source_manifest_sha256": sha256_file(RunArtifacts(root).source_manifest),
        })
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
    _load_env_before_parser(raw_argv)
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
            args.pdf, args.run_dir, render_dpi=args.render_dpi,
            context_files=tuple(args.context_file), context_texts=tuple(args.context_text),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "agent-context":
        result = agent_context(
            args.run_dir, context_files=tuple(args.context_file),
            context_texts=tuple(args.context_text),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "agent-schema":
        if args.legacy_quiz and args.stage != "quiz":
            parser.error("--legacy-quiz is valid only for the Quiz stage")
        print(json.dumps(
            agent_schema(args.stage, legacy_quiz=args.legacy_quiz),
            ensure_ascii=False, indent=2,
        ))
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
    if args.command == "doctor":
        from learning_authoring.provider import check_provider

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            parser.error("OPENAI_API_KEY is missing")
        try:
            check = check_provider(
                api_key=api_key,
                model=args.model,
                base_url=os.getenv("OPENAI_BASE_URL"),
                timeout_seconds=args.timeout,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "authenticated": False,
                        "error_type": type(exc).__name__,
                        "status_code": getattr(exc, "status_code", None),
                        "error_code": getattr(exc, "code", None),
                        "generation_performed": False,
                    },
                    indent=2,
                )
            )
            return 1
        print(json.dumps(check.as_dict(), ensure_ascii=False, indent=2))
        return 0 if check.model_visible else 1
    if args.command == "extract":
        from learning_authoring.extractor import run_extraction

        result = run_extraction(args.pdf, args.run_dir, config=_config(args))
        print(json.dumps({"proposed": str(result.proposed_path), **result.metrics}, indent=2))
        return 0
    if args.command == "batch-plan":
        from learning_authoring.batch import create_batch_plan

        plan = create_batch_plan(args.source_dir, args.manifest, runs_dir=args.runs_dir)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if args.command == "batch-preflight":
        from learning_authoring.batch import preflight_batch

        result = preflight_batch(args.manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 1
    if args.command == "batch-extract":
        from learning_authoring.batch import run_batch

        result = run_batch(
            args.manifest,
            config=_config(args),
            continue_on_error=args.continue_on_error,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "proposed" else 1
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
    if args.command == "kc-preview":
        from learning_authoring.kc import prepare_kc_request

        _, metadata = prepare_kc_request(
            args.run_dir,
            config=_kc_config(args),
            output_dir=args.output_dir,
        )
        artifact_dir = args.output_dir or args.run_dir
        artifacts = RunArtifacts(artifact_dir.expanduser().resolve())
        print(
            json.dumps(
                {
                    "generation_performed": False,
                    "request_preview": str(artifacts.kc_request_preview),
                    "prompt_package": str(artifacts.kc_prompt_package),
                    "metadata": metadata,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "kc-generate":
        from learning_authoring.kc import run_kc_generation

        result = run_kc_generation(
            args.run_dir,
            config=_kc_config(args),
            output_dir=args.output_dir,
        )
        print(json.dumps({"proposed": str(result.proposed_path), **result.metrics}, indent=2))
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
    if args.command == "quiz-preview":
        from learning_authoring.quiz import prepare_quiz_request

        _, metadata = prepare_quiz_request(
            args.run_dir,
            kc_path=args.kc,
            output_dir=args.output_dir,
            config=_quiz_config(args),
        )
        destination = (args.output_dir or (args.run_dir / "quiz")).expanduser().resolve()
        artifacts = RunArtifacts(destination)
        print(
            json.dumps(
                {
                    "generation_performed": False,
                    "quiz_input": str(artifacts.quiz_input),
                    "prompt_package": str(artifacts.quiz_prompt_package),
                    "request_preview": str(artifacts.quiz_request_preview),
                    "metadata": metadata,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "quiz-generate":
        from learning_authoring.quiz import run_quiz_generation

        result = run_quiz_generation(
            args.run_dir,
            kc_path=args.kc,
            output_dir=args.output_dir,
            config=_quiz_config(args),
        )
        print(json.dumps({"proposed": str(result.proposed_path), **result.metrics}, indent=2))
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
    if args.command == "learning-register":
        from learning_authoring.learning import export_learning_registration

        try:
            result = export_learning_registration(args.run_dir, args.output_sql)
        except (PublishSafetyError, ValueError, OSError) as exc:
            print(json.dumps({"exported": False, "error": str(exc)}), file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(_status(args.run_dir), ensure_ascii=False, indent=2))
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
