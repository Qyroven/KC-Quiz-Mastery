#!/usr/bin/env python3
"""Copy the portable learning-authoring skill into user-level agent skill folders."""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

SKILL_NAME = "learning-authoring-pipeline"
AGENT_DESTINATIONS = {
    "codex": Path(".agents") / "skills" / SKILL_NAME,
    "claude": Path(".claude") / "skills" / SKILL_NAME,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the Learning Authoring Pipeline Agent Skill by copying it."
    )
    parser.add_argument(
        "agent",
        choices=("codex", "claude", "both"),
        help="user-level agent skill folder(s) to install",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home(),
        help="home directory to install under (default: current user's home)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="replace an existing installation after moving it to a timestamped backup",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print destinations without changing files",
    )
    return parser


def _skill_source() -> Path:
    source = Path(__file__).resolve().parent.parent
    if not (source / "SKILL.md").is_file():
        raise RuntimeError(f"canonical skill is incomplete: {source}")
    return source


def _selected_agents(value: str) -> tuple[str, ...]:
    if value == "both":
        return ("codex", "claude")
    return (value,)


def _backup_root(destination: Path) -> Path:
    """Keep replaced skills outside folders scanned for skill discovery."""

    if destination.parent.name == "skills":
        return destination.parent.parent / "skill-backups"
    return destination.parent / ".skill-backups"


def _timestamped_backup(destination: Path, label: str) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_root = _backup_root(destination)
    candidate = backup_root / f"{destination.name}.{label}-{stamp}"
    counter = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = backup_root / f"{destination.name}.{label}-{stamp}-{counter}"
        counter += 1
    return candidate


def _copy_ignore(source: Path):
    """Copy maintained package roots, excluding private/generated development artifacts."""

    source = source.resolve()
    runtime = (source / "scripts" / "runtime").resolve()
    allowed_children = {
        source: {"SKILL.md", "agents", "references", "scripts"},
        source / "scripts": {"install_skill.py", "runtime"},
        runtime: {"pyproject.toml", "uv.lock", "learning_authoring", "supabase"},
    }

    def ignore(directory: str, names: list[str]) -> set[str]:
        current = Path(directory).resolve()
        ignored = {
            name
            for name in names
            if name.startswith(".")
            or name in {
                "__pycache__",
                "build",
                "dist",
                "runs",
                "slides",
                "output",
                "outputs",
                "tmp",
                "node_modules",
            }
            or name.endswith((".pyc", ".pyo", ".egg-info", ".log", ".zip", ".bak", ".tmp", "~"))
            or (Path(directory) / name).is_symlink()
        }
        if current in allowed_children:
            ignored.update(set(names) - allowed_children[current])
        if current == runtime / "learning_authoring":
            ignored.update({"legacy_api"} & set(names))
        return ignored

    return ignore


def _install(source: Path, destination: Path, *, replace: bool, dry_run: bool) -> str:
    # Replace the discovery entry, never the target of an existing symlink.
    destination = destination.expanduser().absolute()
    if not destination.is_symlink() and destination.resolve() == source.resolve():
        raise RuntimeError("refusing to replace the canonical skill directory with itself")
    existing = destination.exists() or destination.is_symlink()
    if existing and not replace:
        raise FileExistsError(
            f"destination already exists: {destination}; rerun with --replace to back it up"
        )

    backup = _timestamped_backup(destination, "backup") if existing else None
    if dry_run:
        suffix = f" (backup existing to {backup})" if backup else ""
        return f"would copy {source} -> {destination}{suffix}"

    destination.parent.mkdir(parents=True, exist_ok=True)
    if backup is not None:
        backup.parent.mkdir(parents=True, exist_ok=True)
        destination.rename(backup)
    try:
        shutil.copytree(
            source,
            destination,
            symlinks=False,
            ignore=_copy_ignore(source),
        )
    except Exception as exc:
        incomplete = None
        if destination.exists() or destination.is_symlink():
            incomplete = _timestamped_backup(destination, "incomplete")
            incomplete.parent.mkdir(parents=True, exist_ok=True)
            destination.rename(incomplete)
        if backup is not None:
            backup.rename(destination)
        detail = f"; incomplete copy preserved at {incomplete}" if incomplete else ""
        raise RuntimeError(f"failed to copy skill to {destination}{detail}") from exc
    suffix = f"; previous installation backed up at {backup}" if backup else ""
    return f"installed {destination}{suffix}"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        source = _skill_source()
        home = args.home.expanduser().resolve()
        messages = [
            _install(
                source,
                home / AGENT_DESTINATIONS[agent],
                replace=args.replace,
                dry_run=args.dry_run,
            )
            for agent in _selected_agents(args.agent)
        ]
    except (FileExistsError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("\n".join(messages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
