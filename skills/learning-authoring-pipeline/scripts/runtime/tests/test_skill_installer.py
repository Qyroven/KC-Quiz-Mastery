from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = RUNTIME_ROOT.parents[1]
INSTALLER = SKILL_ROOT / "scripts" / "install_skill.py"


def _install(home: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(INSTALLER), "codex", "--home", str(home), *extra],
        check=True,
        capture_output=True,
        text=True,
    )


def test_replacing_personal_skill_keeps_backup_outside_discovery(tmp_path: Path) -> None:
    _install(tmp_path)
    destination = tmp_path / ".agents" / "skills" / "learning-authoring-pipeline"
    marker = destination / "local-marker.txt"
    marker.write_text("old installation", encoding="utf-8")

    result = _install(tmp_path, "--replace")

    assert destination.joinpath("SKILL.md").is_file()
    assert destination.joinpath("scripts/runtime/pyproject.toml").is_file()
    assert not destination.joinpath("scripts/runtime/tests").exists()
    assert not destination.joinpath("scripts/runtime/scripts").exists()
    assert not destination.joinpath("scripts/runtime/showcase").exists()
    assert not destination.joinpath("scripts/runtime/learning_authoring/legacy_api").exists()
    assert not marker.exists()
    discovered = list((tmp_path / ".agents" / "skills").glob("*/SKILL.md"))
    assert discovered == [destination / "SKILL.md"]
    backups = list(
        (tmp_path / ".agents" / "skill-backups").glob("learning-authoring-pipeline.backup-*")
    )
    assert len(backups) == 1
    assert backups[0].joinpath("local-marker.txt").read_text(encoding="utf-8") == (
        "old installation"
    )
    assert "previous installation backed up" in result.stdout


def test_replace_symlink_backs_up_discovery_entry_not_its_target(tmp_path: Path) -> None:
    destination = tmp_path / ".agents/skills/learning-authoring-pipeline"
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    (canonical / "SKILL.md").write_text("keep this source", encoding="utf-8")
    destination.parent.mkdir(parents=True)
    destination.symlink_to(canonical, target_is_directory=True)

    _install(tmp_path, "--replace")

    assert not destination.is_symlink()
    assert "keep this source" == (canonical / "SKILL.md").read_text()
    backups = list((tmp_path / ".agents/skill-backups").glob("*.backup-*"))
    assert len(backups) == 1 and backups[0].is_symlink()
    assert backups[0].resolve() == canonical


def test_full_package_installs_identically_for_both_hosts(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, str(INSTALLER), "both", "--home", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    codex = tmp_path / ".agents/skills/learning-authoring-pipeline"
    claude = tmp_path / ".claude/skills/learning-authoring-pipeline"
    codex_files = {p.relative_to(codex): p.read_bytes() for p in codex.rglob("*") if p.is_file()}
    claude_files = {p.relative_to(claude): p.read_bytes() for p in claude.rglob("*") if p.is_file()}

    assert codex_files == claude_files
    for path, content in codex_files.items():
        assert content == (SKILL_ROOT / path).read_bytes()
    assert not any("tests" in path.parts or ".venv" in path.parts for path in codex_files)


def test_instructions_only_package_installs_without_runtime(tmp_path: Path) -> None:
    source = tmp_path / "portable-skill"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text("A portable instruction-only fixture.\n")
    installer = source / "scripts/install_skill.py"
    shutil.copyfile(INSTALLER, installer)
    home = tmp_path / "recipient"
    subprocess.run(
        [sys.executable, str(installer), "both", "--home", str(home)],
        check=True,
        capture_output=True,
        text=True,
    )
    for host in (".agents", ".claude"):
        installed = home / host / "skills/learning-authoring-pipeline"
        assert (installed / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()
        assert not (installed / "scripts/runtime").exists()


def test_installer_excludes_private_generated_and_unrelated_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    scripts = source / "scripts"
    scripts.mkdir(parents=True)
    (source / "SKILL.md").write_text("Portable packaging fixture.\n")
    installer = scripts / "install_skill.py"
    shutil.copyfile(INSTALLER, installer)
    kept = {
        "agents/openai.yaml",
        "references/workflow.md",
        "scripts/runtime/pyproject.toml",
        "scripts/runtime/learning_authoring/prompts/task.md",
        "scripts/runtime/learning_authoring/showcase_assets/robots.txt",
        "scripts/runtime/supabase/migrations/example.sql",
    }
    excluded = {
        ".env",
        ".git/config",
        "scratch.md",
        "output/quiz.json",
        "scripts/old_generator.py",
        "scripts/runtime/.env.local",
        "scripts/runtime/tests/test_fixture.py",
        "scripts/runtime/learning_authoring/legacy_api/client.py",
        "scripts/runtime/learning_authoring/prompts/.env",
        "scripts/runtime/learning_authoring/prompts/previous.zip",
        "scripts/runtime/learning_authoring/prompts/debug.log",
        "scripts/runtime/learning_authoring/prompts/task.md.bak",
        "scripts/runtime/learning_authoring/prompts/__pycache__/old.pyc",
        "scripts/runtime/learning_authoring/prompts/runs/quiz.json",
    }
    for relative in kept | excluded:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic fixture\n")
    outside = tmp_path / "private.md"
    outside.write_text("must stay outside the package\n")
    (source / "references/linked.md").symlink_to(outside)
    home = tmp_path / "recipient"

    subprocess.run(
        [sys.executable, str(installer), "codex", "--home", str(home)],
        check=True, capture_output=True, text=True,
    )

    installed = home / ".agents/skills/learning-authoring-pipeline"
    actual = {str(path.relative_to(installed)) for path in installed.rglob("*") if path.is_file()}
    assert actual == kept | {"SKILL.md", "scripts/install_skill.py"}
    assert outside.read_text() == "must stay outside the package\n"


def test_installed_reference_links_resolve_without_repository_files(tmp_path: Path) -> None:
    _install(tmp_path)
    installed = tmp_path / ".agents/skills/learning-authoring-pipeline"
    documents = [installed / "SKILL.md", *(installed / "references").glob("*.md")]
    for document in documents:
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text()):
            if target.startswith(("https://", "http://", "#")):
                continue
            resolved = (document.parent / target.split("#", 1)[0]).resolve()
            assert resolved.is_relative_to(installed), (document, target)
            assert resolved.is_file(), (document, target)
