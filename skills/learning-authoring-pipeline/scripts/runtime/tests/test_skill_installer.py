from __future__ import annotations

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
        (tmp_path / ".agents" / "skill-backups").glob(
            "learning-authoring-pipeline.backup-*"
        )
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
