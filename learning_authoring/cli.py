"""Command-line interface for the standalone authoring tool."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, load_dotenv

from learning_authoring.agent_session import (
    agent_import,
    agent_init,
    agent_schema,
    prepare_agent_task,
)
from learning_authoring.approval import approve_extraction
from learning_authoring.artifacts import RunArtifacts, read_json
from learning_authoring.batch import create_batch_plan, preflight_batch, run_batch
from learning_authoring.extractor import ExtractionConfig, run_extraction
from learning_authoring.kc import KCConfig, prepare_kc_request, run_kc_generation
from learning_authoring.kc_review import build_kc_demo
from learning_authoring.provider import check_provider, normalized_base_url
from learning_authoring.quiz import QuizConfig, prepare_quiz_request, run_quiz_generation
from learning_authoring.quiz_review import build_quiz_review
from learning_authoring.review import build_review
from learning_authoring.source import DEFAULT_RENDER_DPI, preflight_source


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
    parser.add_argument(
        "--variants-per-kc",
        type=int,
        default=2,
        help="runtime bank depth; this is configuration, not a content rule",
    )
    parser.add_argument("--language", default="vi")


def _add_agent_runtime_options(parser: argparse.ArgumentParser) -> None:
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
    parser.add_argument("--variants-per-kc", type=int, default=2)
    parser.add_argument("--language", default="vi")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="learning-authoring",
        description="Review-gated Extract -> KC -> experimental Quiz authoring pipeline",
    )
    parser.add_argument("--env-file", type=_path, help="optional dotenv file")
    subcommands = parser.add_subparsers(dest="command", required=True)

    doctor = subcommands.add_parser(
        "doctor", help="verify API authentication and model visibility without generation"
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

    agent_schema_parser = subcommands.add_parser(
        "agent-schema",
        help="emit the strict JSON candidate schema for an agent-native stage",
    )
    agent_schema_parser.add_argument("stage", choices=("extraction", "kc", "quiz"))

    agent_task_parser = subcommands.add_parser(
        "agent-task",
        help="write a self-contained instructions/schema/input package for the host coding agent",
    )
    agent_task_parser.add_argument("stage", choices=("extraction", "kc", "quiz"))
    agent_task_parser.add_argument("run_dir", type=_path)
    _add_agent_runtime_options(agent_task_parser)

    agent_import_parser = subcommands.add_parser(
        "agent-import",
        help="preserve and validate candidate JSON produced in the subscription session",
    )
    agent_import_parser.add_argument("stage", choices=("extraction", "kc", "quiz"))
    agent_import_parser.add_argument("run_dir", type=_path)
    agent_import_parser.add_argument("candidate_json", type=_path)
    _add_agent_runtime_options(agent_import_parser)

    extract = subcommands.add_parser("extract", help="extract a PDF to a proposed artifact")
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

    batch_extract = subcommands.add_parser(
        "batch-extract", help="run a preflighted batch sequentially with isolated checkpoints"
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

    kc_preview = subcommands.add_parser(
        "kc-preview", help="write the exact KC request without calling the API"
    )
    kc_preview.add_argument("run_dir", type=_path)
    kc_preview.add_argument("--output-dir", type=_path)
    _add_kc_options(kc_preview)

    kc_generate = subcommands.add_parser(
        "kc-generate", help="generate proposed KCs from approved extraction JSON only"
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

    quiz_preview = subcommands.add_parser(
        "quiz-preview", help="freeze the experimental KC-to-Quiz request without an API call"
    )
    quiz_preview.add_argument("run_dir", type=_path)
    quiz_preview.add_argument("--kc", type=_path)
    quiz_preview.add_argument("--output-dir", type=_path)
    _add_quiz_options(quiz_preview)

    quiz_generate = subcommands.add_parser(
        "quiz-generate", help="generate one unapproved Quiz batch without repair"
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

    status = subcommands.add_parser("status", help="show canonical run artifacts")
    status.add_argument("run_dir", type=_path)
    return parser


def _load_env_before_parser(argv: list[str]) -> None:
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--env-file", type=_path)
    known, _ = bootstrap.parse_known_args(argv)
    if known.env_file is None:
        return
    if not known.env_file.is_file():
        bootstrap.error(f"env file does not exist: {known.env_file}")
    values = dotenv_values(known.env_file)
    load_dotenv(known.env_file, override=True)
    if not (values.get("OPENAI_BASE_URL") or "").strip():
        os.environ.pop("OPENAI_BASE_URL", None)


def _config(args: argparse.Namespace) -> ExtractionConfig:
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
    defaults = QuizConfig()
    return QuizConfig(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        response_mode=args.response_mode,
        max_output_tokens=args.max_output_tokens,
        prompt_dir=args.prompt_dir or defaults.prompt_dir,
        selected_kc_ids=tuple(args.include_kc or ()),
        include_all_kcs=args.include_all_kcs,
        variants_per_kc=args.variants_per_kc,
        language=args.language,
        poll_interval_seconds=args.poll_interval,
        timeout_seconds=args.timeout,
        api_key=os.getenv("OPENAI_API_KEY") or None,
        base_url=normalized_base_url(os.getenv("OPENAI_BASE_URL")),
    )


def _validate_agent_runtime_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.stage != "kc" and args.allow_proposed_extraction_demo:
        parser.error("--allow-proposed-extraction-demo is valid only for the KC stage")
    has_quiz_selection = bool(args.include_all_kcs or args.include_kc)
    if args.stage == "quiz" and not has_quiz_selection:
        parser.error("Quiz agent stages require --include-kc or --include-all-kcs")
    if args.stage != "quiz" and (has_quiz_selection or args.kc is not None):
        parser.error("--kc and KC selection options are valid only for the Quiz stage")


def _status(run_dir: Path) -> dict[str, Any]:
    root = run_dir.expanduser().resolve()
    artifacts = RunArtifacts(root)
    quiz_artifacts = RunArtifacts(root / "quiz")
    paths = {
        "source_ready": artifacts.source_manifest,
        "extraction_proposed": artifacts.proposed,
        "extraction_review_built": artifacts.review_html,
        "extraction_approved": artifacts.approved,
        "kc_request_preview": artifacts.kc_request_preview,
        "kc_proposed": artifacts.kc_proposed,
        "quiz_request_preview": quiz_artifacts.quiz_request_preview,
        "quiz_proposed": quiz_artifacts.quiz_proposed,
        "quiz_review_built": artifacts.quiz_review_html,
    }
    result: dict[str, Any] = {
        "run_dir": str(root),
        "artifacts": {name: path.is_file() for name, path in paths.items()},
    }
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
    return result


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    _load_env_before_parser(raw_argv)
    parser = _parser()
    args = parser.parse_args(raw_argv)

    if args.command in {"agent-task", "agent-import"}:
        _validate_agent_runtime_args(parser, args)

    if args.command == "source-preflight":
        try:
            result = preflight_source(args.pdf, args.run_dir, render_dpi=args.render_dpi)
        except (OSError, ValueError) as exc:
            print(json.dumps({"ready": False, "error": str(exc)}, indent=2), file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 1
    if args.command == "agent-init":
        result = agent_init(args.pdf, args.run_dir, render_dpi=args.render_dpi)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "agent-schema":
        print(json.dumps(agent_schema(args.stage), ensure_ascii=False, indent=2))
        return 0
    if args.command == "agent-task":
        result = prepare_agent_task(
            args.stage,
            args.run_dir,
            allow_proposed_extraction_demo=args.allow_proposed_extraction_demo,
            kc_path=args.kc,
            selected_kc_ids=tuple(args.include_kc or ()),
            include_all_kcs=args.include_all_kcs,
            variants_per_kc=args.variants_per_kc,
            language=args.language,
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
            variants_per_kc=args.variants_per_kc,
            language=args.language,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "doctor":
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
        result = run_extraction(args.pdf, args.run_dir, config=_config(args))
        print(json.dumps({"proposed": str(result.proposed_path), **result.metrics}, indent=2))
        return 0
    if args.command == "batch-plan":
        plan = create_batch_plan(args.source_dir, args.manifest, runs_dir=args.runs_dir)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if args.command == "batch-preflight":
        result = preflight_batch(args.manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 1
    if args.command == "batch-extract":
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
        result = run_kc_generation(
            args.run_dir,
            config=_kc_config(args),
            output_dir=args.output_dir,
        )
        print(json.dumps({"proposed": str(result.proposed_path), **result.metrics}, indent=2))
        return 0
    if args.command == "kc-review":
        output = build_kc_demo(
            args.run_dir,
            args.candidate,
            allow_proposed_extraction_demo=args.allow_proposed_extraction_demo,
        )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    if args.command == "quiz-preview":
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
    if args.command == "status":
        print(json.dumps(_status(args.run_dir), ensure_ascii=False, indent=2))
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
