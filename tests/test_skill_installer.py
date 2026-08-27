from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (
    REPOSITORY_ROOT
    / "skills"
    / "learning-authoring-pipeline"
    / "scripts"
    / "install_skill.py"
)


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
