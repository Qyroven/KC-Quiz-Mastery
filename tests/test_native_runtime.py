"""Fresh-process isolation checks, not model or teaching-quality evaluations."""

from __future__ import annotations

import builtins
import importlib.abc
import json
import os
import subprocess
import sys
import tomllib
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROVIDER_MODULES = {
    "openai", "dotenv", "learning_authoring.provider", "learning_authoring.gateway",
    "learning_authoring.extractor", "learning_authoring.repair", "learning_authoring.requests",
}


def test_base_install_has_no_provider_or_dotenv_dependency() -> None:
    project = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())
    assert project["project"]["version"] == "0.5.0"
    assert project["project"]["dependencies"] == ["pydantic>=2.10,<3", "pypdfium2"]
    assert project["project"]["optional-dependencies"]["legacy-api"] == [
        "openai>=2,<3", "python-dotenv",
    ]

    # Check the locked transitive base graph, not merely its direct requirements.
    packages = {
        package["name"]: package
        for package in tomllib.loads((REPOSITORY_ROOT / "uv.lock").read_text())["package"]
    }
    pending = ["learning-authoring-tool"]
    reached = set()
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending.extend(item["name"] for item in packages[name].get("dependencies", []))
    assert not reached & {"openai", "python-dotenv", "httpx", "httpcore"}


def test_native_help_has_no_legacy_generation_or_environment_setup(capsys) -> None:
    from learning_authoring.cli import LEGACY_API_COMMANDS, NATIVE_COMMANDS, _parser, main

    help_text = _parser().format_help()
    assert "--env-file" not in help_text
    assert "--model" not in help_text
    assert "--version" in help_text
    assert set(NATIVE_COMMANDS) <= set(
        item.strip("{},") for item in help_text.replace(",", " ").split()
    )
    for legacy in LEGACY_API_COMMANDS:
        assert legacy not in help_text.split()
        with pytest.raises(SystemExit) as exc:
            main([legacy, "--help"])
        assert exc.value.code == 0
        assert "Legacy model-provider API adapter" in capsys.readouterr().out


def test_version_uses_installed_distribution_metadata(monkeypatch, capsys) -> None:
    from learning_authoring.cli import main

    monkeypatch.setattr("importlib.metadata.version", lambda name: "0.5.0")
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "learning-authoring 0.5.0"


def test_native_command_rejects_explicit_env_before_reading_file(tmp_path, capsys) -> None:
    from learning_authoring.cli import main

    # A nonexistent path shows rejection happens before any dotenv/file requirement.
    with pytest.raises(SystemExit) as exc:
        main(["--env-file", str(tmp_path / "does-not-exist"), "agent-schema", "quiz"])
    assert exc.value.code == 2
    assert "only for historical model-provider API commands" in capsys.readouterr().err


def test_missing_legacy_dependencies_have_explicit_non_native_guidance(
    tmp_path, monkeypatch, capsys,
) -> None:
    from learning_authoring.cli import _load_env_before_parser
    from learning_authoring.provider import build_client, normalized_base_url

    real_import = builtins.__import__

    def without_legacy_modules(name, *args, **kwargs):
        if name in {"openai", "dotenv"}:
            raise ModuleNotFoundError(f"Optional module intentionally unavailable: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_legacy_modules)
    assert normalized_base_url(" ") is None
    with pytest.raises(RuntimeError, match="optional 'legacy-api' extra"):
        build_client(api_key="unused-test-value", base_url=None)
    env_file = tmp_path / "explicit-legacy.env"
    env_file.write_text("LEARNING_AUTHORING_MODEL=unused-test-model\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _load_env_before_parser(["--env-file", str(env_file), "doctor"])
    assert exc.value.code == 2
    assert "subscription-native commands do not use dotenv" in capsys.readouterr().err


@pytest.mark.parametrize("with_context", [False, True])
def test_native_cli_journey_without_provider_imports_environment_or_network(
    tmp_path, with_context,
) -> None:
    # Run a completely fresh interpreter: a pre-imported SDK must not mask a leak.
    (tmp_path / ".env").write_text("NATIVE_DOTENV_MUST_STAY_UNLOADED=yes\n", encoding="utf-8")
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("OPENAI_", "LEARNING_AUTHORING_"))
    }
    env["PYTHONPATH"] = str(REPOSITORY_ROOT)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path), str(int(with_context))],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "native_journey": "complete", "provider_imports": [], "network_calls": 0,
        "context": with_context, "shared_review_configured_offline": True,
    }


class _NoProviders(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in PROVIDER_MODULES):
            raise AssertionError(f"Native authoring imported a provider module: {fullname}")
        return None


def _native_probe(root: Path, with_context: bool) -> None:
    assert not PROVIDER_MODULES & sys.modules.keys()
    sys.meta_path.insert(0, _NoProviders())

    def forbid_external_io(event, args):
        if event.startswith("socket.") or event == "http.client.connect":
            raise AssertionError(f"Native command attempted network access: {event}")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            if Path(os.fsdecode(args[0])).name == ".env":
                raise AssertionError("Native command attempted to access .env")

    sys.addaudithook(forbid_external_io)

    from learning_authoring.artifacts import read_json, sha256_file
    from learning_authoring.cli import main
    from learning_authoring.contracts import ExtractedSource
    from learning_authoring.quiz_semantics import CRITERIA
    from tests.conftest import write_blank_pdf
    from tests.test_agent_context_slots import _adaptive_candidate
    from tests.test_agent_session import _extraction_candidate, _kc_candidate, _write_raw

    def call(*args, structured=True):
        output = StringIO()
        with redirect_stdout(output):
            assert main([str(arg) for arg in args]) == 0
        return json.loads(output.getvalue()) if structured else output.getvalue()

    pdf = root / "lecture.pdf"
    run = root / "native-run"
    write_blank_pdf(pdf)
    assert call("source-preflight", pdf, run)["ready"] is True
    assert call("agent-init", pdf, run)["provider_api_calls"] == 0
    context_args = []
    if with_context:
        note = root / "unstructured-lecturer-input.any-format"
        note.write_text("Synthetic instructor comment, without page headings.", encoding="utf-8")
        context_args = ["--context-file", note, "--context-text", "Another free-form note."]
    call("agent-context", run, *context_args)

    for stage in ("extraction", "kc", "quiz", "quiz-review"):
        assert call("agent-schema", stage)["type"] == "object"

    extraction_task = call("agent-task", "extraction", run)
    extraction_candidate = root / "extraction.json"
    _write_raw(extraction_candidate, _extraction_candidate())
    call(
        "agent-import", "extraction", run, extraction_candidate,
        "--task-package", extraction_task["task_package"],
    )
    call("review", run, structured=False)
    source = ExtractedSource.model_validate(read_json(run / "extracted-source.proposed.json"))
    extraction_sha256 = sha256_file(run / "extracted-source.proposed.json")

    kc_task = call("agent-task", "kc", run, "--allow-proposed-extraction-demo")
    kc_package = read_json(Path(kc_task["task_package"]))
    kc = _kc_candidate(source)
    if with_context:
        kc["source_ref"] = kc_package["input_boundary"]["expected_source_ref"]
        kc["context_audit"] = [
            {
                "context_id": item["context_id"], "excerpt": item["text"],
                "description": None, "claim": "Synthetic non-assessed instructor comment.",
                "disposition": "not_assessed", "kc_ids": [],
                "reason": "Fixture covers runtime mechanics, not semantic authoring quality.",
            }
            for item in read_json(run / "authoring-context.json")["items"]
        ]
    kc_candidate = root / "kc.json"
    kc_raw = _write_raw(kc_candidate, kc)
    call("agent-import", "kc", run, kc_candidate, "--task-package", kc_task["task_package"])
    call("kc-review", run, "--allow-proposed-extraction-demo")

    quiz_task = call("agent-task", "quiz", run, "--include-all-kcs")
    quiz_boundary = read_json(Path(quiz_task["task_package"]))["input_boundary"]["payload"]
    assert quiz_boundary["leaf_kcs"] == kc["leaf_kcs"]
    assert quiz_boundary["kc_groups"] == kc["kc_groups"]
    quiz = _adaptive_candidate(run, source, quiz_task)
    quiz_candidate = root / "quiz.json"
    quiz_raw = _write_raw(quiz_candidate, quiz)
    call("agent-import", "quiz", run, quiz_candidate, "--task-package", quiz_task["task_package"])

    review_task = call("agent-task", "quiz-review", run)
    review_boundary = read_json(Path(review_task["task_package"]))["input_boundary"]
    review = {
        "schema_version": "quiz-semantic-audit.v1",
        "source_ref": review_boundary["expected_source_ref"],
        "reviewer": {"mode": "independent", "label": "offline fixture", "model": None},
        "scope": {
            "source_coverage": "complete", "checked_source_pages": [1],
            "checked_context_ids": [], "limitations": [],
        },
        "questions": [
            {
                "question_id": question["question_id"], "kc_id": question["kc_id"],
                "slot_id": question["slot_id"], "independent_answer": "Synthetic answer B.",
                **{
                    criterion: {
                        "verdict": "PASS", "rationale": "Offline mechanical test fixture.",
                        "issues": [],
                    }
                    for criterion in CRITERIA
                },
            }
            for question in quiz["questions"]
        ],
    }
    review_candidate = root / "semantic-review.json"
    _write_raw(review_candidate, review)
    call(
        "agent-import", "quiz-review", run, review_candidate,
        "--task-package", review_task["task_package"],
    )
    call("quiz-review", run, structured=False)
    local_portal = call("portal-build", run)
    assert local_portal["built"] is True and local_portal["deployment_performed"] is False

    # Supabase review is optional browser configuration, not a model-provider API.
    # It remains supported even though this entire build is network-forbidden.
    shared_portal = call(
        "portal-build", run, "--output-dir", root / "shared-review-portal",
        "--review-supabase-url", "https://abcdefghij.supabase.co",
        "--review-supabase-publishable-key", "sb_publishable_" + "a" * 32,
    )
    assert shared_portal["built"] is True and shared_portal["deployment_performed"] is False
    status = call("status", run)
    assert status["artifacts"]["connected_portal_built"] is True
    assert status["quiz_initial_check"]["status"] == "PASS"
    assert status["artifacts"]["extraction_approved"] is False
    assert (run / "kc-proposed.json").read_bytes() == kc_raw
    assert (run / "quiz/quiz-proposed.json").read_bytes() == quiz_raw
    assert sha256_file(run / "extracted-source.proposed.json") == extraction_sha256
    assert "NATIVE_DOTENV_MUST_STAY_UNLOADED" not in os.environ
    assert not PROVIDER_MODULES & sys.modules.keys()
    print(json.dumps({
        "native_journey": "complete", "provider_imports": [], "network_calls": 0,
        "context": with_context, "shared_review_configured_offline": True,
    }))


if __name__ == "__main__":
    _native_probe(Path(sys.argv[1]), bool(int(sys.argv[2])))
