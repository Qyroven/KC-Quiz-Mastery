"""Offline registration/hash checks; synthetic fixtures, not pedagogical evidence."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

import pytest

import learning_authoring.product.review_registration as registration_module
from learning_authoring.agent_session import agent_import, prepare_agent_task
from learning_authoring.artifacts import read_json, sha256_file
from learning_authoring.product.review_registration import (
    RegistrationSafetyError,
    export_review_registration,
    prepare_review_registration,
    registration_sql,
    renderer_payload_sha256,
)
from learning_authoring.product.showcase import (
    DEFAULT_TEMPLATE_DIR,
    PublishSafetyError,
    ReviewFiles,
    _json_assignment,
)
from tests.test_agent_context_slots import _adaptive_candidate, _import_kcs, _init
from tests.test_agent_session import _forbid_provider_use, _write_raw
from tests.test_publish_showcase import _fake_run


@pytest.fixture
def node() -> str:
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js is required for exact browser baseline registration")
    return executable


def _embedded(path: Path, marker: str) -> dict:
    return _json_assignment(path.read_text(encoding="utf-8"), marker, "fixture")


def _replace_embedded(path: Path, marker: str, value: dict) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index(marker) + len(marker)
    _, length = json.JSONDecoder().raw_decode(text[start:])
    raw = json.dumps(value, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(text[:start] + raw + text[start + length :], encoding="utf-8")


def _generated_run(tmp_path: Path, *, context: bool = False) -> Path:
    run, source = _init(tmp_path, notes=context)
    _import_kcs(run, source, notes=context)
    task = prepare_agent_task("quiz", run, include_all_kcs=True)
    candidate = _adaptive_candidate(run, source, task, notes=context)
    path = tmp_path / "quiz-candidate.json"
    _write_raw(path, candidate)
    agent_import("quiz", run, path, task_package=Path(task["task_package"]))
    return run


def _inventory(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256_file(path) for path in root.rglob("*") if path.is_file()
    }


def _actual_renderer_targets(run: Path, node: str) -> list[dict]:
    runtime = (DEFAULT_TEMPLATE_DIR / "review-runtime.js").read_text(encoding="utf-8")
    # Only these actual pure renderer functions run; never its auth/network/UI setup.
    functions = "\n".join(
        re.search(
            rf"  (?:async )?function {name}\([^\n]*\) \{{.*?\n  \}}",
            runtime,
            re.S,
        )[0]
        for name in ("canonical", "payloadSha256", "deepCopy", "detectAdapter")
    )
    data = {
        "extraction": _embedded(run / "extraction-review.html", "const source="),
        "kc": _embedded(run / "kc-recall.html", "const DATA=")["candidate"]["proposed"],
        "quiz": _embedded(
            run / "quiz-review.html", '<script id="payload" type="application/json">'
        )["quiz"],
    }
    script = (
        functions
        + """
const data = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const crypto = require('node:crypto').webcrypto;
const kcChoiceByPage = new Map(), results = new Map();
let source = data.extraction, selected, questions, index, proposal;
let evidenceByPage, contextSelected, kcById;
function selectedKcRows() {
  return contextSelected ? proposal.leaf_kcs.filter(k => (k.context_evidence || []).length)
    .map(kc => ({kc})) : evidenceByPage[selected] || [];
}
async function record() {
  const a = detectAdapter();
  if (!a) return;
  const row = {stage:a.stage,item_type:a.itemType,item_key:a.itemKey,
    identity_field:a.identityField,identity_value:a.identityValue,
    base_artifact_sha256:await payloadSha256(a.payload)};
  results.set([a.stage,a.itemType,a.itemKey].join(':'), row);
}
(async () => {
  for (selected = 0; selected < source.pages.length; selected++) await record();
  proposal = data.kc; evidenceByPage = {}; contextSelected = false;
  kcById = Object.fromEntries(proposal.leaf_kcs.map(k => [k.kc_id,k]));
  globalThis.auditByPage = Object.fromEntries(proposal.page_audit.map(a => [a.page,a]));
  for (const kc of proposal.leaf_kcs)
    for (const evidence of kc.source_evidence)
      (evidenceByPage[evidence.page] ??= []).push({kc,evidence});
  for (const page of source.pages) {selected = page.page_number; await record();}
  for (const kc of proposal.leaf_kcs) {
    contextSelected = !kc.source_evidence.length;
    selected = kc.source_evidence[0]?.page || 1;
    kcChoiceByPage.set(contextSelected ? 'context' : selected, kc.kc_id);
    await record();
  }
  questions = data.quiz.questions;
  for (index = 0; index < questions.length; index++) await record();
  process.stdout.write(JSON.stringify([...results.values()]));
})().catch(() => process.exit(1));
"""
    )
    result = subprocess.run(
        [node, "-e", script],
        input=json.dumps(data),
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
        env={},
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("context", [False, True])
def test_export_matches_actual_renderer_and_preserves_generated_run(
    tmp_path,
    monkeypatch,
    node,
    context,
) -> None:
    _forbid_provider_use(monkeypatch)
    run = _generated_run(tmp_path, context=context)
    before = _inventory(run)
    output = tmp_path / "registration.sql"
    result = export_review_registration(run, output, node_executable=node)
    assert _inventory(run) == before
    expected = _actual_renderer_targets(run, node)

    def key(row):
        return row["stage"], row["item_type"], row["item_key"]

    assert sorted(map(asdict, result.targets), key=key) == sorted(expected, key=key)
    assert result.counts == {
        "extraction/page": 1,
        "kc/leaf_kc": 1,
        **({"kc/page_audit": 1} if context else {}),
        "quiz/question": 3,
    }
    assert output.read_text(encoding="utf-8") == registration_sql(result)
    assert prepare_review_registration(run, node_executable=node) == result
    sql = output.read_text(encoding="utf-8")
    assert sql.count("INSERT INTO public.review_runs") == 1
    assert sql.count("INSERT INTO public.review_targets") == 1
    assert "BEGIN;" in sql and sql.endswith("COMMIT;\n")
    assert not re.search(r"\b(?:UPDATE|DELETE|TRUNCATE|DROP|ON CONFLICT)\b", sql)
    assert "false, false," in sql
    assert "supabase" not in sql.lower() and "reviewer" not in sql.lower()
    assert "Lecturer-only nuance" not in sql and "Choose the best answer" not in sql
    assert str(run) not in sql
    assert result.run_id == run.name
    assert all(len(t.base_artifact_sha256) == 64 for t in result.targets)


def test_hash_uses_js_numbers_utf16_key_sort_strings_and_array_order(node, monkeypatch) -> None:
    monkeypatch.setenv("NODE_OPTIONS", "--require=/must-not-be-loaded-by-exporter")
    payload = {
        "𐀀": "supplementary",
        "\ue000": "BMP",
        "é": "Tiếng Việt </script>\u2028",
        "numbers": [1.0, -0.0, 1e-7, 1e-6, 1e20, 1e21, 9007199254740993, 5e-324],
        "nested": {"10": True, "2": None, "a": "'\\\n\ud800"},
    }
    runtime = (DEFAULT_TEMPLATE_DIR / "review-runtime.js").read_text(encoding="utf-8")
    canonical = re.search(r"  function canonical\(value\) \{.*?\n  \}", runtime, re.S)[0]
    script = (
        canonical
        + """
const value = JSON.parse(require('node:fs').readFileSync(0,'utf8'));
process.stdout.write(require('node:crypto').createHash('sha256')
  .update(canonical(value), 'utf8').digest('hex'));
"""
    )
    expected = subprocess.run(
        [node, "-e", script],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
        env={},
    ).stdout
    assert renderer_payload_sha256(payload, node_executable=node) == expected
    assert renderer_payload_sha256(1.0, node_executable=node) == (
        renderer_payload_sha256(1, node_executable=node)
    )
    assert renderer_payload_sha256([1, 2], node_executable=node) != (
        renderer_payload_sha256([2, 1], node_executable=node)
    )


def test_no_node_nonfinite_or_changed_renderer_fail_closed(tmp_path, monkeypatch, node) -> None:
    monkeypatch.setattr(registration_module.shutil, "which", lambda name: None)
    with pytest.raises(RegistrationSafetyError, match="Node.js is required"):
        renderer_payload_sha256({"x": 1})
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(RegistrationSafetyError, match="non-finite"):
            renderer_payload_sha256(value, node_executable=node)
    runtime = tmp_path / "changed-runtime.js"
    runtime.write_text("function canonical(v) {return JSON.stringify(v)}", encoding="utf-8")
    with pytest.raises(RegistrationSafetyError, match="canonical hash changed"):
        renderer_payload_sha256({}, runtime_path=runtime, node_executable=node)
    text = (DEFAULT_TEMPLATE_DIR / "review-runtime.js").read_text(encoding="utf-8")
    runtime.write_text(
        text.replace('itemType: "question"', 'itemType: "new_question"'), encoding="utf-8"
    )
    with pytest.raises(RegistrationSafetyError, match="target adapters changed"):
        renderer_payload_sha256({}, runtime_path=runtime, node_executable=node)


def test_sql_escapes_names_and_only_opens_review_explicitly(tmp_path, node) -> None:
    run = _generated_run(tmp_path)
    result = prepare_review_registration(run, node_executable=node)
    quoted = replace(result, run_id="day1'); SELECT 1; --\\name", source_filename="L'été.pdf")
    sql = registration_sql(quoted, is_public=True, review_open=True)
    assert "E'day1''); SELECT 1; --\\\\name'" in sql
    assert "E'L''été.pdf'" in sql and "true, true," in sql
    with pytest.raises(RegistrationSafetyError, match="must also be public"):
        registration_sql(result, review_open=True)
    with pytest.raises(RegistrationSafetyError, match="booleans"):
        registration_sql(result, is_public="true")
    for unsafe in ("bad\0name", "bad\ud800name"):
        with pytest.raises(RegistrationSafetyError):
            registration_sql(replace(result, run_id=unsafe))


def test_export_refuses_overwrite_run_output_and_symlinks(tmp_path, node) -> None:
    run = _generated_run(tmp_path)
    before = _inventory(run)
    existing = tmp_path / "old-registration.sql"
    existing.write_text("old history", encoding="utf-8")
    with pytest.raises(RegistrationSafetyError, match="already exists"):
        export_review_registration(run, existing, node_executable=node)
    assert existing.read_text() == "old history"
    with pytest.raises(RegistrationSafetyError, match="outside"):
        export_review_registration(run, run / "new.sql", node_executable=node)
    link = tmp_path / "link.sql"
    link.symlink_to(existing)
    with pytest.raises(PublishSafetyError, match="Symlink"):
        export_review_registration(run, link, node_executable=node)
    portal = tmp_path / "portal"
    portal.mkdir()
    (portal / "showcase-manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RegistrationSafetyError, match="static portal"):
        export_review_registration(run, portal / "registration.sql", node_executable=node)
    assert _inventory(run) == before


def test_rejects_stale_reviews_and_invalid_review_paths(tmp_path, node) -> None:
    run = _generated_run(tmp_path)
    with pytest.raises(PublishSafetyError, match="run-local"):
        prepare_review_registration(run, review_files=ReviewFiles(quiz="../quiz.html"))
    html = run / "quiz-review.html"
    value = _embedded(html, '<script id="payload" type="application/json">')
    value["quiz"]["questions"][0]["hints"][0]["text"] = "A changed hint"
    _replace_embedded(html, '<script id="payload" type="application/json">', value)
    with pytest.raises(PublishSafetyError, match="does not match"):
        prepare_review_registration(run, node_executable=node)


def _mapped_fixture(tmp_path: Path) -> Path:
    """Three-page contract fixture: one multi-page KC and one context-only KC."""
    run = _fake_run(tmp_path, 3)
    proposal = read_json(run / "kc-proposed.json")
    proposal["leaf_kcs"][0]["source_evidence"] = [{"page": 1}, {"page": 2}]
    proposal["leaf_kcs"][1].update(source_evidence=[], context_evidence=[{"pages": [3]}])
    proposal["page_audit"] = [{"page": page, "kc_ids": ["KC-002"]} for page in (1, 2, 3)]
    _write_raw(run / "kc-proposed.json", proposal)
    kc_hash = sha256_file(run / "kc-proposed.json")
    for name in ("kc-recall.html", "kc-scroll.html"):
        value = _embedded(run / name, "const DATA=")
        value["candidate"]["proposed"] = proposal
        _replace_embedded(run / name, "const DATA=", value)
    quiz = _embedded(run / "quiz-review.html", '<script id="payload" type="application/json">')
    for key in ("quiz", "input"):
        quiz[key]["source_ref"]["kc_set_sha256"] = kc_hash
    quiz["input"]["leaf_kcs"] = proposal["leaf_kcs"]
    quiz["metadata"]["kc_set"]["sha256"] = kc_hash
    for key, filename in (
        ("quiz", "quiz-proposed.json"),
        ("input", "quiz-input.json"),
        ("metadata", "quiz-generation-metadata.json"),
    ):
        _write_raw(run / "quiz" / filename, quiz[key])
    _replace_embedded(
        run / "quiz-review.html", '<script id="payload" type="application/json">', quiz
    )
    return run


def test_audit_targets_follow_pdf_evidence_not_audit_kc_ids_or_context_pages(
    tmp_path, node
) -> None:
    run = _mapped_fixture(tmp_path)
    result = prepare_review_registration(run, node_executable=node)
    assert result.counts == {
        "extraction/page": 3,
        "kc/leaf_kc": 2,
        "kc/page_audit": 1,
        "quiz/question": 3,
    }
    assert [t.item_key for t in result.targets if t.item_type == "page_audit"] == ["page:0003"]
    expected = _actual_renderer_targets(run, node)
    assert {json.dumps(asdict(t), sort_keys=True) for t in result.targets} == {
        json.dumps(t, sort_keys=True) for t in expected
    }


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_question",
        "duplicate_page",
        "missing_audit",
        "unreachable_kc",
        "proto",
        "upstream_copy",
    ],
)
def test_rejects_ambiguous_or_unreachable_target_shapes(tmp_path, node, case) -> None:
    run = _mapped_fixture(tmp_path)
    # Mutate only newly-created synthetic test fixtures; never an authored run.
    if case == "duplicate_question":
        html = run / "quiz-review.html"
        marker = '<script id="payload" type="application/json">'
        value = _embedded(html, marker)
        value["quiz"]["questions"][1]["question_id"] = "Q-001"
        _write_raw(run / "quiz/quiz-proposed.json", value["quiz"])
    else:
        html, marker = run / "kc-recall.html", "const DATA="
        value = _embedded(html, marker)
        proposal = value["candidate"]["proposed"]
        if case == "duplicate_page":
            value["source"]["pages"][1]["page_number"] = 1
        elif case == "missing_audit":
            proposal["page_audit"].pop()
        elif case == "unreachable_kc":
            proposal["leaf_kcs"][1]["context_evidence"] = []
        elif case == "proto":
            value["source"]["pages"][0]["__proto__"] = {}
        elif case == "upstream_copy":
            value["source"]["pages"][0]["role"] = "changed upstream payload"
    _replace_embedded(html, marker, value)
    with pytest.raises(PublishSafetyError):
        prepare_review_registration(run, node_executable=node)


def test_custom_review_filenames_use_the_selected_artifacts(tmp_path, node) -> None:
    run = _mapped_fixture(tmp_path)
    original = prepare_review_registration(run, node_executable=node)
    reviews = ReviewFiles(
        "extract-custom.html", "recall-custom.html", "scroll-custom.html", "quiz-custom.html"
    )
    for old, new in zip(
        ("extraction-review.html", "kc-recall.html", "kc-scroll.html", "quiz-review.html"),
        (reviews.extractor, reviews.kc_recall, reviews.kc_scroll, reviews.quiz),
        strict=True,
    ):
        (run / old).rename(run / new)
    result = prepare_review_registration(run, review_files=reviews, node_executable=node)
    assert result.targets == original.targets and result.run_id == run.name
    assert "quiz-custom.html" in dict(result.artifact_sha256)
    assert "quiz-review.html" not in dict(result.artifact_sha256)


def test_rejects_missing_or_default_changed_quiz_upstream_copies(tmp_path, node) -> None:
    run = _mapped_fixture(tmp_path)
    marker = '<script id="payload" type="application/json">'
    value = _embedded(run / "quiz-review.html", marker)
    # An omitted optional field is still part of an immutable baseline's shape.
    value["input"]["leaf_kcs"][0]["context_evidence"] = []
    _write_raw(run / "quiz/quiz-input.json", value["input"])
    _replace_embedded(run / "quiz-review.html", marker, value)
    with pytest.raises(RegistrationSafetyError, match="upstream copy"):
        prepare_review_registration(run, node_executable=node)
    value["input"]["leaf_kcs"] = []
    _write_raw(run / "quiz/quiz-input.json", value["input"])
    _replace_embedded(run / "quiz-review.html", marker, value)
    with pytest.raises(RegistrationSafetyError, match="selected KC identities"):
        prepare_review_registration(run, node_executable=node)


def test_detects_input_change_while_hashing(tmp_path, node, monkeypatch) -> None:
    run = _generated_run(tmp_path)
    original = registration_module._payload_hashes

    def changed(values, executable):
        hashes = original(values, executable)
        with (run / "quiz-review.html").open("a", encoding="utf-8") as handle:
            handle.write("\n<!-- changed during export -->")
        return hashes

    monkeypatch.setattr(registration_module, "_payload_hashes", changed)
    with pytest.raises(RegistrationSafetyError, match="changed during export"):
        prepare_review_registration(run, node_executable=node)


def test_standalone_cli_exports_only_offline_sql(tmp_path, node, capsys) -> None:
    run = _mapped_fixture(tmp_path)
    output = tmp_path / "new-registration.sql"
    before = _inventory(run)
    result = registration_module.main(
        [
            str(run),
            str(output),
            "--node",
            node,
            "--public",
            "--review-open",
            "--runtime-path",
            str(DEFAULT_TEMPLATE_DIR / "review-runtime.js"),
        ]
    )
    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["run_id"] == run.name and summary["target_count"] == 9
    assert summary["backend_writes"] == 0 and summary["sql_sha256"] == sha256_file(output)
    assert "true, true," in output.read_text()
    assert _inventory(run) == before
    with pytest.raises(SystemExit) as error:
        registration_module.main([str(run), str(output), "--node", node])
    assert error.value.code == 2
    assert "already exists" in capsys.readouterr().err
