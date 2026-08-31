from __future__ import annotations

import json
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from learning_authoring.agent_session import agent_context, agent_import, prepare_agent_task
from learning_authoring.artifacts import read_json, sha256_file, write_json
from learning_authoring.authoring_context import prepare_authoring_context
from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.kc_review import (
    _candidate_metrics,
    _comparison_html,
    _load_candidate,
    _recall_html,
)
from learning_authoring.product.showcase import (
    PublishSafetyError,
    SourceMetadata,
    _require_context_lineage,
    build_showcase,
)
from learning_authoring.quiz_review import _TEMPLATE, build_quiz_review
from learning_authoring.review import _REVIEW_TEMPLATE, build_review
from tests.conftest import payload
from tests.test_agent_context_slots import _adaptive_candidate, _import_kcs, _init
from tests.test_agent_session import _forbid_provider_use, _quiz_candidate, _write_raw
from tests.test_kc_review import candidate
from tests.test_quiz import quiz_output

RUNTIME = (
    Path(__file__).resolve().parents[1] / "learning_authoring/showcase_assets/review-runtime.js"
).read_text(encoding="utf-8")

# Run the actual inline scripts offline. The small DOM stub records generated
# markup and callbacks; fetch is forbidden so tests cannot touch shared reviews.
NODE_HARNESS = r"""
const fs = require('node:fs'), vm = require('node:vm');
const task = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = new Map();
function view(id) {
  if (!nodes.has(id)) nodes.set(id, {
    innerHTML: '', textContent: '', value: '', hidden: false, disabled: false,
    style: {setProperty() {}}, dataset: {}, clientWidth: 1000, scrollTop: 0,
    classList: {toggle() {}, contains() {return false}, add() {}, remove() {}},
    addEventListener() {}, setAttribute() {}, scrollIntoView() {}, scrollTo() {},
    querySelectorAll() {return []},
    querySelector(selector) {
      if (selector === '[data-slide-page]' && this.innerHTML.includes('data-slide-page='))
        return view('slide');
      return null;
    },
    getBoundingClientRect() {return {top: 0, bottom: 800}},
  });
  return nodes.get(id);
}
view('payload').textContent = JSON.stringify(task.payload || {});
const sandbox = {
  assert: require('node:assert/strict'), view,
  document: {
    getElementById: view,
    querySelector: s => view(s.startsWith('#') ? s.slice(1) : s),
    querySelectorAll: () => [],
    documentElement: view('html'), body: view('body'),
  },
  localStorage: {getItem() {return null}, setItem() {}, removeItem() {}},
  location: {hash: task.hash || ''},
  history: {replaceState(a, b, hash) {sandbox.location.hash = hash}},
  requestAnimationFrame() {return 0}, addEventListener() {}, removeEventListener() {},
  getComputedStyle() {return {getPropertyValue() {return '390px'}}},
  Promise: {all() {return {then() {}}}}, innerWidth: 1280,
  fetch() {throw new Error('network is forbidden in review compatibility tests')},
};
vm.createContext(sandbox);
vm.runInContext(task.script, sandbox, {timeout: 5000});
vm.runInContext(task.assertions, sandbox, {timeout: 5000});
"""


def run_js(script: str, assertions: str, *, data: dict | None = None) -> None:
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js is needed for offline review JavaScript checks")
    result = subprocess.run(
        [executable, "-e", NODE_HARNESS],
        input=json.dumps({"script": script, "assertions": assertions, "payload": data}),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def inline_script(html: str) -> str:
    return re.findall(r"<script>(.*?)</script>", html, re.DOTALL)[0]


def test_extraction_review_preserves_authored_fields_and_source_bytes(tmp_path, source) -> None:
    raw = payload().with_source(source).model_dump(mode="json", exclude_defaults=True)
    # Optional omitted fields must not silently appear in the displayed/copied raw JSON.
    raw["pages"][0]["page_note"]["summary"] = "Source <example> </script><script>not code</script>"
    # A non-teaching page may have no blocks; the navigation still works without adding fields.
    raw["pages"][1].pop("blocks", None)
    raw["pages"][1].pop("reading_order", None)
    raw["pages"][1]["page_note"] = {"summary": "Blank source page."}
    path = tmp_path / "extracted-source.proposed.json"
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=3), encoding="utf-8")
    before = path.read_bytes()

    html = build_review(tmp_path).read_text(encoding="utf-8")
    embedded, _ = json.JSONDecoder().raw_decode(html.split("const source=", 1)[1])

    assert embedded == raw
    assert "warnings" not in embedded["pages"][0]
    assert "explanation" not in embedded["pages"][0]["page_note"]
    assert "blocks" not in embedded["pages"][1]
    assert path.read_bytes() == before
    assert "</script><script>not code" not in html
    assert embedded["pages"][0]["page_note"]["summary"] == raw["pages"][0]["page_note"]["summary"]


def test_extraction_ui_handles_omitted_warnings_and_searches_page_note() -> None:
    warnings = _REVIEW_TEMPLATE.split("function warningRows", 1)[1].split(
        "function formatNumber", 1
    )[0]
    search = _REVIEW_TEMPLATE.split("function searchableText", 1)[1].split(
        "function renderPageList", 1
    )[0]
    run_js(
        "const warningScope={by_page:{'1':[{code:'SOURCE_GAP'}]}};\n"
        + "function warningRows" + warnings
        + "function searchableText" + search,
        """
        const page={page_number:1,role:'blank',page_note:{summary:'Uncertain edge direction'}};
        const before=JSON.stringify(page);
        assert.equal(warningRows(page)[0].code, 'SOURCE_GAP');
        assert.match(searchableText(page), /uncertain edge/);
        assert.equal(JSON.stringify(page), before);
        """,
    )


def test_extraction_stats_do_not_invent_usage_for_missing_metrics() -> None:
    stats = "function formatNumber" + _REVIEW_TEMPLATE.split("function formatNumber", 1)[1].split(
        "function searchableText", 1
    )[0]
    run_js(
        "const metrics={}, source={pages:[{}],source:{page_count:1}},audit={},"
        "warningScope={record_count:0},sourceManifest={},el=view,escapeHtml=String;\n" + stats,
        """
        renderStats();
        assert.match(view('stats').innerHTML, /usage unavailable/);
        assert.doesNotMatch(view('stats').innerHTML, />0 tokens<|>0s</);
        """,
    )


@pytest.mark.parametrize("native", [True, False])
def test_extraction_stats_do_not_present_native_import_time_as_model_time(native) -> None:
    stats_script = "function formatNumber" + _REVIEW_TEMPLATE.split(
        "function formatNumber", 1
    )[1].split("function searchableText", 1)[0]
    run_js(
        """
        const metrics = JSON.parse(view('payload').textContent);
        const source = {pages: [{}], source: {page_count: 1}};
        const audit = {}, warningScope = {record_count: 0};
        const sourceManifest = {elapsed_seconds: 1};
        const el = view, escapeHtml = String;
        """ + stats_script,
        """
        renderStats();
        const markup = view('stats').innerHTML;
        if (metrics.execution_mode === 'agent_subscription_session') {
          assert.match(markup, /model time unavailable/);
          assert.doesNotMatch(markup, />6s</);
        } else {
          assert.match(markup, />6s</);
          assert.doesNotMatch(markup, /model time unavailable/);
        }
        """,
        data={
            "execution_mode": "agent_subscription_session" if native else "legacy_api",
            "total_elapsed_seconds": 5,
            "usage_available": not native,
        },
    )


def context_candidate(source) -> dict:
    result = candidate("local-test", source)
    proposal = result["proposed"]
    context_kc = deepcopy(proposal["leaf_kcs"][0])
    context_kc.update(
        kc_id="KC-002",
        group_id="KCG-002",
        name="Lecturer-only concept",
        source_evidence=[],
        context_evidence=[
            {
                "context_id": "CTX-001",
                "excerpt": "Lecturer <example> & explanation.",
                "description": None,
                "supports": "The additional concept.",
                "pages": [],
                "mapping_method": "document_level",
                "mapping_confidence": "unmapped",
            },
            {
                "context_id": "CTX-002",
                "excerpt": None,
                "description": "An attachment illustrates the additional concept.",
                "supports": "Concept application.",
                "pages": [2],
                "mapping_method": "semantic_alignment",
                "mapping_confidence": "low",
            },
        ],
    )
    proposal["leaf_kcs"].append(context_kc)
    proposal["kc_groups"].append(
        {
            "group_id": "KCG-002",
            "name": "Lecturer additions",
            "description": "Additional learning content",
            "leaf_kc_ids": ["KC-002"],
        }
    )
    return result


def test_legacy_kc_script_renders_without_context_fields(source) -> None:
    legacy = candidate("local-test", source)
    legacy["proposed"]["source_ref"].pop("authoring_context_sha256", None)
    for kc in legacy["proposed"]["leaf_kcs"]:
        kc.pop("context_evidence", None)
    html = _recall_html(payload().with_source(source), legacy)
    run_js(
        inline_script(html),
        """
        assert.equal(pageRows.length, 2);
        assert.equal(contextKcs().length, 0);
        assert.equal(view('contextLink').hidden, true);
        assert.match(view('inspectorBody').innerHTML, /Explain the concept/);
        selectPage(2);
        assert.match(view('inspectorBody').innerHTML, /No KC generated/);
        assert.equal(view('position').textContent, 'Slide 2 of 2');
        """,
    )


@pytest.mark.parametrize("scroll_mode", [False, True])
@pytest.mark.parametrize("pages", [None, [], [2]])
def test_kc_warning_without_page_scope_does_not_crash(source, scroll_mode, pages) -> None:
    item = candidate("local-test", source)
    warning = {"code": "UNCERTAIN", "message": "Check source meaning"}
    if pages is not None:
        warning["pages"] = pages
    item["proposed"]["generation_warnings"] = [warning]
    html = _recall_html(payload().with_source(source), item, scroll_mode=scroll_mode)
    run_js(inline_script(html), f"""
      assert.equal(pageRows.length,2);
      assert.equal((warningsByPage[1]||[]).length,{0 if pages else 1});
      assert.equal(warningsByPage[2].length,1);
      selectPage(2);
      assert.equal(view('position').textContent,'Slide 2 of 2');
    """)


@pytest.mark.parametrize("scroll_mode", [False, True])
def test_context_only_kc_is_reviewable_without_inventing_a_slide(source, scroll_mode) -> None:
    html = _recall_html(
        payload().with_source(source), context_candidate(source), scroll_mode=scroll_mode
    )
    run_js(
        inline_script(html),
        """
        assert.equal(pageRows.length, 2);
        assert.equal(contextKcs().length, 1);
        assert.equal(evidenceByPage[2], undefined);
        assert.equal(pageRows[1].kcCount, 0);
        assert.equal(view('contextLink').hidden, false);
        assert.match(view('pageList').innerHTML, /data-context/);
        setGroupFilter('KCG-002');
        assert.equal(contextSelected, true);
        assert.equal(location.hash, '#context');
        assert.equal(selectedKcRows()[0].kc.kc_id, 'KC-002');
        assert.match(view('inspectorBody').innerHTML, /Lecturer-only concept/);
        assert.doesNotMatch(view('inspectorBody').innerHTML, /Extracted blocks/);
        assert.match(view('slideDeck').innerHTML, /Document-level context/);
        assert.match(view('slideDeck').innerHTML, /Related PDF pages: 2/);
        assert.match(view('slideDeck').innerHTML, /confidence: low/);
        assert.match(view('slideDeck').innerHTML, /Lecturer &lt;example&gt; &amp; explanation/);
        assert.doesNotMatch(view('slideDeck').innerHTML, /data-slide-page|<img/);
        if (scrollMode) assert.match(view('kcFloat').innerHTML, /Lecturer-only concept/);
        selectPage(2);
        assert.equal(contextSelected, false);
        assert.equal(filter, 'all');
        assert.equal(view('position').textContent, 'Slide 2 of 2');
        assert.match(view('slideDeck').innerHTML, /page-0002.png/);
        assert.equal(pageRows.length, 2);
        """,
    )


def test_context_metrics_do_not_count_context_as_pdf_evidence(source) -> None:
    data = context_candidate(source)["proposed"]
    data["page_audit"][1]["kc_ids"] = ["KC-002"]
    proposed = ProposedKCSet.model_validate(data)
    metrics = _candidate_metrics(proposed, payload().with_source(source), {}, {})
    assert metrics["leaf_kcs"] == 2
    assert metrics["evidence_records"] == 1
    assert metrics["context_evidence_records"] == 2
    assert metrics["context_only_kcs"] == 1
    assert metrics["pages_with_kcs"] == 1
    assert metrics["referenced_source_blocks"] == 1
    assert metrics["source_blocks"] == 2


def test_comparison_review_shows_context_separately(source) -> None:
    item = context_candidate(source)
    approved = payload().with_source(source)
    item["metrics"] = _candidate_metrics(
        ProposedKCSet.model_validate(item["proposed"]), approved, {}, {}
    )
    html = _comparison_html(approved, [item], None)
    run_js(
        "const grid=view('grid'),copyRaw=view('copyRaw');\n" + inline_script(html),
        """
        assert.match(view('grid').innerHTML, /Lecturer-only concept/);
        assert.match(view('grid').innerHTML, /Lecturer context · separate from extraction/);
        assert.match(view('grid').innerHTML, /No PDF evidence/);
        assert.match(view('grid').innerHTML, /Document-level context · no PDF page/);
        assert.match(view('metrics').innerHTML, /PDF evidence records/);
        """,
    )


def quiz_review_data(source, *, adaptive: bool) -> dict:
    quiz = quiz_output(source, variants=3 if adaptive else 1)
    if adaptive:
        quiz["schema_version"] = "quiz-batch.v2"
        quiz["assessment_slots"] = [
            {
                "slot_id": f"SLOT-{index}",
                "kc_id": "KC-001",
                "evidence_intent": f"Intent {index}",
                "cognitive_operation": "apply",
                "intended_difficulty": "medium",
                "variant_count": count,
                "justification": f"Distinct bounded intent {index}.",
            }
            for index, count in [(1, 2), (2, 1)]
        ]
        for index, question in enumerate(quiz["questions"]):
            question["slot_id"] = "SLOT-1" if index < 2 else "SLOT-2"
            question["variant_index"] = index + 1 if index < 2 else 1
        quiz["questions"][2].update(
            evidence_refs=[],
            context_evidence_refs=[
                {
                    "context_id": "CTX-001",
                    "excerpt": "Lecturer-only evidence.",
                    "description": None,
                    "pages": [],
                }
            ],
        )
    return {
        "quiz": quiz,
        "input": candidate("local-test", source)["proposed"],
        "metadata": {"request_fingerprint": "d" * 64, "model": "local-test"},
        "metrics": {},
        "form_audit": {"questions": []},
    }


@pytest.mark.parametrize("adaptive", [False, True])
def test_quiz_review_metadata_uses_slot_variants_and_preserves_legacy(source, adaptive) -> None:
    data = quiz_review_data(source, adaptive=adaptive)
    run_js(
        inline_script(_TEMPLATE),
        r"""
        if (DATA.quiz.schema_version === 'quiz-batch.v2') {
          assert.equal(assessmentSlots.size, 2);
          assert.match(view('metrics').innerHTML, /2 Assessment slots/);
          assert.match(variantLabel(questions[0]), /SLOT-1 · variant 1 \/ 2/);
          assert.match(variantLabel(questions[2]), /SLOT-2 · variant 1 \/ 1/);
          assert.match(slotHTML(questions[2]), /Distinct bounded intent 2/);
          const review = renderReviewer(questions[2]);
          assert.match(review, /Lecturer-only evidence/);
          assert.match(review, /không gán trang PDF/);
          assert.doesNotMatch(review, /Trang undefined/);
        } else {
          assert.equal(assessmentSlots.size, 0);
          assert.doesNotMatch(view('metrics').innerHTML, /Assessment slots/);
          assert.equal(variantLabel(questions[0]), 'variant 1 · legacy KC numbering');
          assert.equal(slotHTML(questions[0]), '');
          assert.match(renderReviewer(questions[0]), /Trang 1/);
        }
        """,
        data=data,
    )


def test_shared_review_rejects_changed_provenance_or_slot_identity(source) -> None:
    functions = RUNTIME[
        RUNTIME.index("  function isObject(") : RUNTIME.index("  async function payloadSha256(")
    ]
    cases = {
        "kc": context_candidate(source)["proposed"]["leaf_kcs"][1],
        "question": quiz_review_data(source, adaptive=True)["quiz"]["questions"][2],
    }
    run_js(
        "const cases=" + json.dumps(cases) + ";\n" + functions,
        """
        for (const [kind, baseline] of Object.entries(cases)) {
          const quiz = kind === 'question';
          const adapter = {
            stage: quiz ? 'quiz' : 'kc', itemType: quiz ? 'question' : 'leaf_kc',
            identityField: quiz ? 'question_id' : 'kc_id',
            identityValue: quiz ? baseline.question_id : baseline.kc_id, payload: baseline,
          };
          const edited = JSON.parse(JSON.stringify(baseline));
          if (quiz) edited.title = 'Human wording'; else edited.name = 'Human wording';
          assert.equal(revisionMatchesAdapter(adapter, edited), true);
          const fields = quiz
            ? ['kc_id', 'group_id', 'slot_id', 'variant_index', 'context_evidence_refs']
            : ['group_id', 'source_evidence', 'context_evidence'];
          for (const field of fields) {
            const changed = JSON.parse(JSON.stringify(edited));
            delete changed[field];
            assert.equal(revisionMatchesAdapter(adapter, changed), false, field);
          }
        }
        """,
    )


def test_kc_editor_preserves_context_and_pdf_evidence(source) -> None:
    helpers = RUNTIME[
        RUNTIME.index("  function escapeHtml(") : RUNTIME.index("  function isObject(")
    ]
    fields = RUNTIME[
        RUNTIME.index("  function requiredValue(") : RUNTIME.index("  function bindEditorSave(")
    ]
    editor = RUNTIME[
        RUNTIME.index("  function openKcEditor(") : RUNTIME.index("  function tableGridHtml(")
    ]
    baseline = context_candidate(source)["proposed"]["leaf_kcs"][1]
    run_js(
        "const baseline="
        + json.dumps(baseline)
        + ";\n"
        + "const byId=view,state={adapter:{itemType:'leaf_kc'}};let buildEdit;"
        + "function openModal(title,body){view('editor').innerHTML=body}"
        + "function bindEditorSave(builder){buildEdit=builder}"
        + helpers
        + fields
        + editor,
        """
        openKcEditor(baseline, null);
        view('la-kc-name').value = 'Edited concept';
        view('la-kc-semantic-form').value = 'concept';
        view('la-kc-description').value = 'Edited description';
        view('la-kc-observable').value = 'Edited observable claim';
        view('la-kc-included').value = 'One boundary';
        const edited = buildEdit();
        assert.equal(edited.name, 'Edited concept');
        assert.equal(edited.kc_id, baseline.kc_id);
        assert.deepEqual(edited.source_evidence, baseline.source_evidence);
        assert.deepEqual(edited.context_evidence, baseline.context_evidence);
        assert.match(view('editor').innerHTML, /Không có nguồn PDF/);
        assert.match(view('editor').innerHTML, /CTX-001, CTX-002/);
        """,
    )


def test_quiz_editor_preserves_slot_variant_and_context(source) -> None:
    data = quiz_review_data(source, adaptive=True)
    baseline = data["quiz"]["questions"][2]
    helpers = RUNTIME[
        RUNTIME.index("  function escapeHtml(") : RUNTIME.index("  function isObject(")
    ]
    fields = RUNTIME[
        RUNTIME.index("  function requiredValue(") : RUNTIME.index("  function bindEditorSave(")
    ]
    editor = RUNTIME[
        RUNTIME.index("  function collectQuizResponse(") : RUNTIME.index(
            "  async function openEditor("
        )
    ]
    run_js(
        "const DATA="
        + json.dumps(data)
        + ";const baseline="
        + json.dumps(baseline)
        + ";\n"
        + """
        const byId=view, CSS={escape: value=>value};let buildEdit;
        const modalBody={
          querySelector(){return {value:'Updated choice wording'}},
          querySelectorAll(){return [{dataset:{laCorrect:'B'}}]},
        };
        function openModal(title,body){view('editor').innerHTML=body}
        function bindEditorSave(builder){buildEdit=builder}
        function selectionEditor(){return ''}
        function stimulusEditor(){return ''}
        function setupTableControls(){}
        function setStimulusVisibility(){}
        function setupMatchingControls(){}
        function setupOrderingControls(){}
        function setupRubricControls(){}
        function collectStimulus(){return baseline.stimulus}
        """
        + helpers
        + fields
        + editor,
        r"""
        openQuizEditor(baseline, null);
        view('la-quiz-title').value = 'Edited question';
        view('la-quiz-prompt').value = 'Edited prompt';
        view('la-quiz-explanation').value = 'Edited explanation';
        const edited = buildEdit();
        assert.equal(edited.title, 'Edited question');
        assert.equal(edited.slot_id, baseline.slot_id);
        assert.equal(edited.variant_index, baseline.variant_index);
        assert.equal(edited.question_id, baseline.question_id);
        assert.deepEqual(edited.context_evidence_refs, baseline.context_evidence_refs);
        assert.deepEqual(edited.evidence_refs, baseline.evidence_refs);
        assert.equal(edited.correct_answer.selection_ids[0], 'B');
        assert.match(view('editor').innerHTML, /SLOT-2/);
        assert.match(view('editor').innerHTML, /variant 1 \/ 1/);
        """,
    )


def test_shared_review_context_scope_targets_kc_not_page_audit(source) -> None:
    data = context_candidate(source)["proposed"]
    detector = RUNTIME[
        RUNTIME.index("  function detectAdapter(") : RUNTIME.index("  function targetId(")
    ]
    run_js(
        "const proposal="
        + json.dumps(data)
        + ";\n"
        + """
        const kcs=proposal.leaf_kcs, kcById=Object.fromEntries(kcs.map(k=>[k.kc_id,k]));
        const selected=2, contextSelected=true, evidenceByPage={}, kcChoiceByPage=new Map();
        function deepCopy(value){return JSON.parse(JSON.stringify(value))}
        function selectedKcRows(){return [{kc:kcs[1],evidence:null}]}
        """
        + detector,
        """
        const adapter=detectAdapter();
        assert.equal(adapter.stage, 'kc');
        assert.equal(adapter.itemType, 'leaf_kc');
        assert.equal(adapter.itemKey, 'KC-002');
        assert.equal(adapter.choiceKey, 'context');
        assert.equal(adapter.payload.source_evidence.length, 0);
        assert.equal(adapter.payload.context_evidence.length, 2);
        """,
    )


def test_review_builders_embed_legacy_json_without_default_field_rewrites(
    tmp_path, source, monkeypatch
) -> None:
    approved = payload().with_source(source)
    legacy = candidate("local-test", source)["proposed"]
    legacy["source_ref"].pop("authoring_context_sha256", None)
    legacy["leaf_kcs"][0].pop("context_evidence", None)
    for name, data in {
        "kc-proposed.json": legacy,
        "kc-run-metrics.json": {},
        "kc-generation-metadata.json": {},
    }.items():
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    assert _load_candidate(tmp_path, approved)["proposed"] == legacy

    quiz_data = quiz_review_data(source, adaptive=False)
    for name, key in {
        "quiz-proposed.json": "quiz",
        "quiz-input.json": "input",
        "quiz-run-metrics.json": "metrics",
        "quiz-generation-metadata.json": "metadata",
    }.items():
        (tmp_path / name).write_text(json.dumps(quiz_data[key]), encoding="utf-8")
    # Input-shape validity is covered by contract tests; this isolates serialization.
    monkeypatch.setattr(
        "learning_authoring.quiz_review.QuizBatch.validate_against_input", lambda *a: None
    )
    review_path = build_quiz_review(tmp_path, candidate_dir=tmp_path)
    html = review_path.read_text(encoding="utf-8")
    embedded = json.loads(re.search(r'type="application/json">(.*?)</script>', html).group(1))
    assert embedded["quiz"] == quiz_data["quiz"]


def test_showcase_rejects_stale_context_without_changing_pdf_lineage(tmp_path, source) -> None:
    metadata = SourceMetadata(
        filename=source.filename,
        page_count=source.page_count,
        source_id=source.source_id,
        source_sha256=source.sha256,
        page_images=(),
    )
    _require_context_lineage({}, tmp_path, metadata, "KC")
    context = prepare_authoring_context(tmp_path, source, context_texts=["Lecturer note."])
    source_ref = {"authoring_context_sha256": context.sha256}
    _require_context_lineage(source_ref, tmp_path, metadata, "KC")
    with pytest.raises(PublishSafetyError, match="stale or missing"):
        _require_context_lineage({}, tmp_path, metadata, "KC")
    prepare_authoring_context(tmp_path, source, context_texts=["Changed lecturer note."])
    with pytest.raises(PublishSafetyError, match="stale or missing"):
        _require_context_lineage(source_ref, tmp_path, metadata, "Quiz")
    assert metadata.source_sha256 == source.sha256


def test_legacy_v1_import_with_omitted_optional_hashes_builds_portal(
    tmp_path, monkeypatch
) -> None:
    _forbid_provider_use(monkeypatch)
    run, source = _init(tmp_path)
    _import_kcs(run, source)
    task = prepare_agent_task("quiz", run, include_all_kcs=True, variants_per_kc=2)
    candidate = _quiz_candidate(source, sha256_file(run / "kc-proposed.json"), variants=2)
    assert "authoring_context_sha256" not in candidate["source_ref"]
    candidate["source_ref"]["source_bundle_sha256"] = None
    candidate_path = run / "candidate-quiz.json"
    raw = _write_raw(candidate_path, candidate)
    imported = agent_import("quiz", run, candidate_path, task_package=Path(task["task_package"]))
    input_path = run / "quiz/quiz-input.json"
    original_input = read_json(input_path)
    assert "authoring_context_sha256" in original_input["source_ref"]
    assert original_input["source_ref"]["authoring_context_sha256"] is None
    assert "source_bundle_sha256" not in original_input["source_ref"]
    assert imported["provider_api_calls"] == 0
    assert Path(imported["proposed"]).read_bytes() == raw

    output = tmp_path / "legacy-portal"
    manifest = build_showcase(run, output)
    assert manifest["counts"]["pages"] == 1
    assert manifest["counts"]["quiz_questions"] == 2
    assert manifest["lineage"]["kc_to_quiz"] == "VERIFIED"
    assert Path(imported["proposed"]).read_bytes() == raw
    published_html = (output / "quiz-review.html").read_text(encoding="utf-8")
    published_payload = json.loads(
        re.search(r'type="application/json">(.*?)</script>', published_html).group(1)
    )
    assert "authoring_context_sha256" not in published_payload["quiz"]["source_ref"]
    assert published_payload["quiz"]["source_ref"]["source_bundle_sha256"] is None

    # Only absent/null optional hashes are equivalent; all other identity keys
    # remain strict even if a corrupt input and review HTML agree with each other.
    review_path = run / "quiz-review.html"
    original_html = review_path.read_text(encoding="utf-8")
    pattern = r'(<script id="payload" type="application/json">)(.*?)(</script>)'
    embedded = re.search(pattern, original_html, re.DOTALL)
    original_payload = json.loads(embedded.group(2))
    for index, field in enumerate(
        [
            "extraction_source_id",
            "extraction_source_sha256",
            "kc_set_sha256",
            "authoring_context_sha256",
            "source_bundle_sha256",
            "unknown_identity",
        ]
    ):
        corrupted_input = deepcopy(original_input)
        corrupted_input["source_ref"][field] = "mismatched-identity"
        write_json(input_path, corrupted_input)
        corrupted_payload = deepcopy(original_payload)
        corrupted_payload["input"] = corrupted_input
        replacement = json.dumps(corrupted_payload)
        review_path.write_text(
            re.sub(
                pattern,
                lambda match, replacement=replacement: (
                    match.group(1) + replacement + match.group(3)
                ),
                original_html,
                flags=re.DOTALL,
            ),
            encoding="utf-8",
        )
        with pytest.raises(PublishSafetyError, match="source references do not match"):
            build_showcase(run, tmp_path / f"corrupt-portal-{index}")


def test_context_only_adaptive_import_builds_portal_without_private_notes(
    tmp_path, monkeypatch
) -> None:
    _forbid_provider_use(monkeypatch)
    run, source = _init(tmp_path, notes=True)
    extraction_hash = sha256_file(run / "extracted-source.proposed.json")
    private_marker = "UNCITED_PRIVATE_LECTURER_NOTE_5cd7e20b"
    notes_path = tmp_path / "private-lecturer-notes.anything"
    notes_path.write_text(
        "Lecturer-only nuance: compare the assumptions.\n" + private_marker,
        encoding="utf-8",
    )
    agent_context(run, context_files=(notes_path,))
    _import_kcs(run, source, notes=True)
    task = prepare_agent_task("quiz", run, include_all_kcs=True)
    candidate = _adaptive_candidate(run, source, task, notes=True)
    candidate_path = run / "candidate-quiz.json"
    raw = _write_raw(candidate_path, candidate)
    imported = agent_import("quiz", run, candidate_path, task_package=Path(task["task_package"]))
    assert imported["provider_api_calls"] == 0
    assert Path(imported["proposed"]).read_bytes() == raw

    output = tmp_path / "context-portal"
    manifest = build_showcase(run, output)
    assert manifest["counts"]["pages"] == 1
    assert manifest["counts"]["leaf_kcs"] == 1
    assert manifest["counts"]["quiz_questions"] == 3
    assert len(read_json(run / "quiz/quiz-proposed.json")["assessment_slots"]) == 2
    assert sha256_file(run / "extracted-source.proposed.json") == extraction_hash
    assert Path(imported["proposed"]).read_bytes() == raw
    files = [path for path in output.rglob("*") if path.is_file()]
    assert not any("authoring-context" in path.relative_to(output).parts for path in files)
    assert not (output / "authoring-context.json").exists()
    assert not (output / notes_path.name).exists()
    combined = b"".join(path.read_bytes() for path in files)
    assert private_marker.encode() not in combined
    assert str(notes_path).encode() not in combined
    assert b'"original_path"' not in combined
    assert b"Lecturer-only nuance: compare the assumptions." in combined
    run_js(
        inline_script((output / "kc-recall.html").read_text(encoding="utf-8")),
        """
        assert.equal(pageRows.length, 1);
        assert.equal(pageRows[0].kcCount, 0);
        selectContext();
        assert.equal(selectedKcRows()[0].kc.kc_id, 'KC-001');
        assert.match(view('inspectorBody').innerHTML, /Lecturer context/);
        assert.doesNotMatch(view('slideDeck').innerHTML, /data-slide-page|<img/);
        """,
    )
