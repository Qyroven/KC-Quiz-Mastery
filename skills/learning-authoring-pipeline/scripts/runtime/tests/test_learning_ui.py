"""Offline checks of the real Learning controller and rendered UI (no browser/network)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[1] / "learning_authoring" / "showcase_assets"


def learning_data() -> dict:
    questions = []
    for index, kind in enumerate(
        ["single_select", "multi_select", "matching", "ordering", "short_text"], start=1
    ):
        question = {
            "question_id": f"Q-{index:05d}",
            "slot_id": f"SLOT-{index:03d}",
            "kc_id": "KC-001",
            "group_id": "KCG-001",
            "variant_index": 1,
            "title": f"Câu {index}",
            "interaction": kind,
            "prompt": "Chọn câu trả lời phù hợp.",
            "stimulus": {
                "kind": "none",
                "text": "",
                "table_columns": [],
                "table_rows": [],
                "formula": "",
            },
            "choice_options": [],
            "matching_left": [],
            "matching_right": [],
            "ordering_options": [],
            "correct_answer": {
                "selection_ids": [],
                "ordering": [],
                "mappings": [],
                "text": "",
            },
            "rubric": [],
            "answer_explanation": "EXPLANATION_HIDDEN_BEFORE_SUBMIT",
            "hints": [
                {"hint_id": "H1", "kind": "cue", "text": "FIRST_AUTHORED_HINT"},
                {"hint_id": "H2", "kind": "strategy", "text": "SECOND_AUTHORED_HINT"},
            ],
            "hint_absence_reason": None,
            "evidence_refs": [{"page": 2, "block_ids": ["p0002-b01"]}],
            "context_evidence_refs": [],
        }
        if kind in {"single_select", "multi_select"}:
            question["choice_options"] = [
                {"option_id": value, "text": f"Lựa chọn {value}"} for value in "ABCD"
            ]
            question["correct_answer"]["selection_ids"] = (
                ["A"] if kind == "single_select" else ["A", "C"]
            )
        elif kind == "matching":
            question["matching_left"] = [
                {"option_id": f"L{i}", "text": f"Trái {i}"} for i in range(1, 4)
            ]
            question["matching_right"] = [
                {"option_id": f"R{i}", "text": f"Phải {i}"} for i in range(1, 4)
            ]
            question["correct_answer"]["mappings"] = [
                {"left": f"L{i}", "right": f"R{i}"} for i in range(1, 4)
            ]
        elif kind == "ordering":
            question["ordering_options"] = [
                {"option_id": f"O{i}", "text": f"Bước {i}"} for i in range(1, 4)
            ]
            question["correct_answer"]["ordering"] = ["O1", "O2", "O3"]
        else:
            question["correct_answer"]["text"] = "EXEMPLAR_HIDDEN_BEFORE_SUBMIT"
            question["rubric"] = [
                {"criterion": "RUBRIC_HIDDEN_BEFORE_SUBMIT", "points": 2},
                {"criterion": "Giải thích", "points": 1},
            ]
        questions.append(question)
    return {
        "schema_version": "learning-package.v1",
        "run_id": "test-learning",
        "source": {"filename": "Bài học.pdf", "source_sha256": "a" * 64},
        "versions": {
            "policy_version": "evidence-rules.v1",
            "quiz_sha256": "b" * 64,
            "kc_sha256": "c" * 64,
            "extraction_sha256": "d" * 64,
            "context_sha256": "e" * 64,
        },
        "kcs": [
            {
                "kc_id": "KC-001",
                "name": "Kiến thức kiểm thử",
                "observable_claim": "Phân biệt các bước.",
                "source_evidence": [{"page": 2, "block_ids": ["p0002-b01"]}],
                "context_evidence": [],
            }
        ],
        "groups": [],
        "slots": [
            {
                "slot_id": question["slot_id"],
                "kc_id": "KC-001",
                "cognitive_operation": "understand",
                "intended_difficulty": "easy",
                "evidence_intent": "Phân biệt khái niệm",
            }
            for question in questions
        ],
        "questions": questions,
        "question_meta": {
            question["question_id"]: {
                "question_sha256": str(index) * 64,
                "initial_check_status": "PASS",
            }
            for index, question in enumerate(questions, start=1)
        },
    }


NODE_HARNESS = r"""
const fs = require('node:fs'), assert = require('node:assert/strict');
const task = JSON.parse(fs.readFileSync(0, 'utf8'));
const UI = require(task.runtime), Core = require(task.core), data = task.data;
function memory() {
  const values = new Map();
  return {values, fail: false,
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) {
      if (this.fail) throw new Error('blocked storage'); values.set(key, value);
    },
    removeItem(key) { values.delete(key); }};
}
let sequence = 0, ticks = 0;
const crypto = {randomUUID() {
  return `00000000-0000-4000-8000-${String(++sequence).padStart(12, '0')}`;
}};
const now = () => new Date(1700000000000 + (++ticks) * 1000).toISOString();
const storage = memory();
// Model Web Locks: callbacks with the same name cannot overlap, including awaits.
const lockTails = new Map(), lockNames = [];
const locks = {request(name, options, callback) {
  lockNames.push(name);
  const task = (lockTails.get(name) || Promise.resolve()).then(callback);
  lockTails.set(name, task.catch(() => {}));
  return task;
}};
const forbidden = async () => {throw new Error('network forbidden')};
function create(overrides = {}) {
  return UI.createSession({data, core: Core, storage, crypto, locks, now,
    fetch: forbidden, ...overrides});
}
function reply(payload, status = 200) {
  return {ok: status >= 200 && status < 300, status,
    async text() {return status === 204 ? '' : JSON.stringify(payload)}};
}
function remoteFixture() {
  const calls = [], attempts = [], feedback = [], grades = [], fail = new Map();
  let canGrade = false;
  const quality = Object.fromEntries(Object.entries(data.question_meta).map(([id, meta]) =>
    [id, {...meta, quality_status: meta.initial_check_status, exclusion_reasons: []}]));
  const auth = {access_token: 'public-test-access', refresh_token: 'test-refresh',
    expires_at: Date.now() / 1000 + 3600, user: {id: '00000000-0000-4000-8000-000000009999'}};
  const fetch = async (url, options) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({url, body, headers: options.headers});
    const name = url.split('/').pop();
    if (fail.has(name)) throw new Error(fail.get(name));
    if (url.includes('/auth/v1/')) return reply(auth);
    if (url.includes('/reviewer_profiles')) return reply(null, 204);
    if (name === 'get_learning_state') return reply({attempts: structuredClone(attempts),
      feedback: structuredClone(feedback), can_grade: canGrade, item_quality: quality});
    if (name === 'start_learning_attempt') {
      let row = attempts.find(item => item.attempt_id === body.p_attempt_id ||
        (item.question_id === body.p_question_id && item.status === 'in_progress'));
      if (!row) {
        const q = data.questions.find(item => item.question_id === body.p_question_id);
        row = {attempt_id: body.p_attempt_id, run_id: data.run_id, question_id: q.question_id,
          question_sha256: data.question_meta[q.question_id].question_sha256,
          kc_id: q.kc_id, slot_id: q.slot_id, started_at: now(), submitted_at: null,
          status: 'in_progress', response: UI.emptyResponse(), hint_ids: [],
          is_repeat: attempts.some(item => item.question_id === q.question_id),
          score: null, max_score: null, correct: null, grading_method: 'pending',
          grading_version: null, quality_status: quality[q.question_id].quality_status,
          evidence_eligible: false, exclusion_reasons: ['not_graded']};
        attempts.push(row);
      }
      return reply(structuredClone(row));
    }
    if (name === 'reveal_learning_hint') {
      const row = attempts.find(item => item.attempt_id === body.p_attempt_id);
      if (!row.hint_ids.includes(body.p_hint_id)) row.hint_ids.push(body.p_hint_id);
      return reply(structuredClone(row));
    }
    if (name === 'submit_learning_attempt') {
      const index = attempts.findIndex(item => item.attempt_id === body.p_attempt_id);
      const old = attempts[index];
      if (old.status === 'in_progress') attempts[index] = Core.buildLocalAttempt(data,
        old.question_id, body.p_response, {attempt_id: old.attempt_id, started_at: old.started_at,
          submitted_at: now(), hint_ids: old.hint_ids,
          attempts: attempts.filter(item => item.attempt_id !== old.attempt_id)});
      return reply(structuredClone(attempts[index]));
    }
    if (name === 'append_learning_feedback') {
      let event = feedback.find(item => item.event_id === body.p_event_id);
      if (!event) {event = {event_id: body.p_event_id, question_id: body.p_question_id,
        kind: 'feedback', payload: {vote: body.p_vote, note: body.p_note}}; feedback.push(event)}
      return reply(event);
    }
    if (name === 'get_learning_grading_queue') return reply(grades);
    if (name === 'grade_learning_attempt')
      return reply({attempt_id: body.p_attempt_id, status: 'graded'});
    throw new Error(`Unexpected request ${url}`);
  };
  return {calls, attempts, feedback, grades, fail, quality, auth,
    grant() {canGrade = true}, config: {enabled: true,
      supabaseUrl: 'https://test.supabase.co', supabasePublishableKey: 'test-public-key'}, fetch};
}
function mount(overrides = {}) {
  const nodes = new Map();
  function view(id) {
    if (!nodes.has(id)) nodes.set(id, {id, hidden: false, innerHTML: '', textContent: '',
      value: '', disabled: false, dataset: {}, listeners: {}, focus() {this.focused = true},
      addEventListener(name, callback) {this.listeners[name] = callback},
      querySelectorAll() {return []}, querySelector() {return null}});
    return nodes.get(id);
  }
  const location = {hash: ''}, document = {getElementById: view};
  const app = UI.mount({document, data, core: Core, storage, crypto, locks, fetch: forbidden,
    location, history: {replaceState(a, b, hash) {location.hash = hash}}, ...overrides});
  async function click(action, extra = {}) {
    const button = {dataset: {action, ...extra}, disabled: false};
    return view('learning-app').listeners.click({target: {closest() {return button}}});
  }
  function change(dataset, value, checked = true) {
    view('question-panel').listeners.change({target: {dataset, value, checked}});
  }
  return {app, view, click, change, location};
}
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
new AsyncFunction('UI', 'Core', 'data', 'assert', 'storage', 'create', 'remoteFixture',
  'reply', 'mount', 'crypto', 'now', task.assertions)(UI, Core, data, assert, storage, create,
  remoteFixture, reply, mount, crypto, now).catch(error => {
    console.error(error); process.exitCode = 1;
  });
"""


def run_js(assertions: str, *, data: dict | None = None) -> None:
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js is required for offline Learning UI tests")
    result = subprocess.run(
        [executable, "-e", NODE_HARNESS],
        input=json.dumps(
            {
                "runtime": str(ASSETS / "learning-runtime.js"),
                "core": str(ASSETS / "learning-core.js"),
                "data": data or learning_data(),
                "assertions": assertions,
            }
        ),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_offline_attempts_hints_grades_feedback_and_reload() -> None:
    run_js(
        """
        const session = create(); await session.init();
        await assert.rejects(
          () => session.submit('Q-00001', data.questions[0].correct_answer), /Nhập tên/);
        await session.saveName('An');
        const first = await session.revealHint('Q-00001');
        assert.deepEqual(first.hint_ids, ['H1']);
        assert.match([...storage.values.values()].join(''), /H1/);
        const second = await session.revealHint('Q-00001');
        assert.deepEqual(second.hint_ids, ['H1', 'H2']);
        assert.equal(second.attempt_id, first.attempt_id);
        const graded = await session.submit('Q-00001', data.questions[0].correct_answer);
        assert.equal(graded.score, 1); assert.equal(graded.correct, true);
        assert.equal(graded.evidence_eligible, true);
        assert.equal(Core.computeEvidence(data, session.state.attempts).kcs[0].state, 'assisted');
        const beforeFeedback = JSON.stringify(session.state.attempts);
        await session.feedback('Q-00001', 'like', 'Rõ', graded.attempt_id);
        assert.equal(JSON.stringify(session.state.attempts), beforeFeedback);
        assert.equal(session.state.feedback.length, 1);
        const restored = create(); await restored.init();
        assert.equal(restored.state.identity.display_name, 'An');
        assert.equal(restored.state.attempts[0].attempt_id, first.attempt_id);
        assert.deepEqual(restored.state.attempts[0].hint_ids, ['H1', 'H2']);
        assert.equal(restored.state.feedback[0].payload.note, 'Rõ');
        await restored.start('Q-00001');
        const repeated = await restored.submit('Q-00001', data.questions[0].correct_answer);
        assert.equal(repeated.is_repeat, true); assert.equal(repeated.evidence_eligible, false);
        assert.ok(repeated.exclusion_reasons.includes('repeated_question'));
        """
    )


def test_all_five_interactions_and_short_text_never_auto_pass() -> None:
    run_js(
        """
        const session = create(); await session.init(); await session.saveName('Bình');
        for (const question of data.questions) {
          const row = await session.submit(question.question_id, question.correct_answer);
          if (question.interaction === 'short_text') {
            assert.equal(row.status, 'pending_grade'); assert.equal(row.score, null);
            assert.equal(row.correct, null); assert.equal(row.evidence_eligible, false);
          } else {
            assert.equal(row.status, 'graded'); assert.equal(row.correct, true);
          }
        }
        assert.equal(session.state.attempts.length, 5);
        await assert.rejects(
          () => session.grade(session.state.attempts[4].attempt_id, [2, 1], ''), /không có quyền/);
        """
    )


def test_answer_rubric_and_future_hints_are_inert_and_hidden_before_submit() -> None:
    run_js(
        r"""
        const question = data.questions[4];
        question.prompt = '<img src=x onerror="alert(1)"> & prompt';
        const initial = UI.questionHtml(data, question, {response: UI.emptyResponse()});
        for (const text of ['EXEMPLAR_HIDDEN','RUBRIC_HIDDEN','EXPLANATION_HIDDEN',
                            'FIRST_AUTHORED_HINT','SECOND_AUTHORED_HINT'])
          assert.equal(initial.includes(text), false);
        assert.match(initial, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt; &amp; prompt/);
        assert.doesNotMatch(initial, /<img/);
        const attempt = {status: 'in_progress', hint_ids: ['H1'], quality_status: 'PASS'};
        const hinted = UI.questionHtml(data, question, {attempt, response: UI.emptyResponse()});
        assert.match(hinted, /FIRST_AUTHORED_HINT/);
        assert.doesNotMatch(hinted, /SECOND_AUTHORED_HINT/);
        const pending = {...attempt, status: 'pending_grade',
          response: {...UI.emptyResponse(), text:'My response'},
          evidence_eligible: false, exclusion_reasons: ['not_graded'], score: null, max_score: 3};
        const submitted = UI.questionHtml(data, question,
          {attempt: pending, response: pending.response});
        assert.match(submitted, /EXEMPLAR_HIDDEN/); assert.match(submitted, /RUBRIC_HIDDEN/);
        assert.match(submitted, /Chờ chấm rubric/); assert.doesNotMatch(submitted, /result-score/);
        assert.match(submitted, /KHÔNG TỰ CHẤM/);
        """
    )


def test_absent_hint_reason_and_context_only_links_remain_honest() -> None:
    run_js(
        """
        const question = data.questions[0];
        question.hints = []; question.hint_absence_reason = 'AUTHORED_NO_HINT_REASON';
        data.kcs[0].source_evidence = [];
        data.kcs[0].context_evidence = [{context_id:'CTX-01',excerpt:'Lecturer <text>',pages:[]}];
        const html = UI.questionHtml(data, question, {response:UI.emptyResponse()});
        assert.match(html, /AUTHORED_NO_HINT_REASON/);
        assert.doesNotMatch(html, /data-action="hint"/);
        assert.match(html, /kc-recall.html#context/);
        assert.doesNotMatch(html, /extraction-review.html#/);
        assert.match(html, /Lecturer &lt;text&gt;/);
        data.question_meta[question.question_id].initial_check_status = 'REJECT';
        assert.match(UI.questionHtml(data, question,
          {response:UI.emptyResponse()}), /Chỉ luyện tập/);
        """
    )


def test_ui_buttons_drive_real_local_learning_and_keep_feedback_separate() -> None:
    run_js(
        """
        const {app, view, click, change} = mount(); await app.ready;
        assert.equal(view('learning-app').hidden, false);
        assert.match(view('storage-label').textContent, /Chỉ trên thiết bị này/);
        view('learner-name').value = 'Chi';
        await view('identity-form').onsubmit({preventDefault(){}});
        assert.equal(app.session.state.identity.display_name, 'Chi');
        change({response:'choice'}, 'A');
        await click('hint');
        assert.match(view('question-panel').innerHTML, /FIRST_AUTHORED_HINT/);
        assert.deepEqual(app.ui.responses['Q-00001'].selection_ids, ['A']);
        await click('submit');
        assert.equal(app.session.state.attempts[0].correct, true);
        assert.match(view('question-panel').innerHTML, /EXPLANATION_HIDDEN/);
        assert.match(view('evidence-summary').innerHTML, /Đúng khi có hỗ trợ/);
        assert.doesNotMatch(view('next-action').innerHTML,
          /after_assisted|unattempted_eligible_question/);
        await click('repeat');
        assert.equal(app.session.state.attempts.length, 2);
        assert.match(view('question-panel').innerHTML, /Nộp câu trả lời/);
        assert.doesNotMatch(view('question-panel').innerHTML, /EXPLANATION_HIDDEN/);
        await click('select', {question:'Q-00004'});
        const before = [...app.ui.responses['Q-00004'].ordering];
        await click('move', {index:'0',direction:'1'});
        assert.equal(app.ui.responses['Q-00004'].ordering[1], before[0]);
        await click('select', {question:'Q-00003'});
        for (let i=1;i<=3;i++) change({response:'match',left:`L${i}`}, `R${i}`);
        await click('submit');
        assert.equal(app.session.state.attempts.at(-1).correct, true);
        await click('vote',{vote:'dislike'});
        view('feedback-note').oninput({target:{value:'Góp ý riêng'}});
        await view('feedback-form').onsubmit({preventDefault(){}});
        assert.equal(app.session.state.feedback.length, 1);
        assert.equal(app.session.state.feedback[0].payload.vote, 'dislike');
        assert.equal(view('grading-toggle').hidden, true);
        """
    )


def test_failed_hint_storage_does_not_reveal_text_or_claim_success() -> None:
    run_js(
        """
        const session = create(); await session.init(); await session.saveName('Dung');
        const attempt = await session.start('Q-00001');
        storage.fail = true;
        await assert.rejects(() => session.revealHint('Q-00001'), /Không lưu được/);
        assert.deepEqual(session.state.attempts[0].hint_ids, []);
        assert.doesNotMatch(UI.questionHtml(data,data.questions[0],
          {attempt:session.state.attempts[0],response:UI.emptyResponse()}), /FIRST_AUTHORED_HINT/);
        storage.fail = false;
        const retry = await session.revealHint('Q-00001');
        assert.equal(retry.attempt_id, attempt.attempt_id); assert.deepEqual(retry.hint_ids,['H1']);
        """
    )


def test_remote_failure_never_falls_back_or_submits_client_grading() -> None:
    run_js(
        """
        const remote = remoteFixture(), session = create(remote);
        await session.init(); await session.saveName('Hà');
        remote.fail.set('submit_learning_attempt','connection lost');
        await assert.rejects(
          () => session.submit('Q-00001',data.questions[0].correct_answer), /connection lost/);
        assert.equal(session.state.mode, 'shared');
        assert.equal(session.state.attempts[0].status, 'in_progress');
        const id = session.state.attempts[0].attempt_id;
        assert.equal([...storage.values.keys()].some(key => key.endsWith(':records')), false);
        remote.fail.clear();
        await session.submit('Q-00001',data.questions[0].correct_answer);
        const submits = remote.calls.filter(call => call.url.endsWith('submit_learning_attempt'));
        assert.equal(submits.length,2);
        assert.equal(submits[0].body.p_attempt_id,id);
        assert.equal(submits[1].body.p_attempt_id,id);
        assert.deepEqual(Object.keys(submits[0].body).sort(),['p_attempt_id','p_response']);
        assert.deepEqual(Object.keys(submits[0].body.p_response).sort(),
          ['mappings','ordering','selection_ids','text']);
        assert.equal(session.state.attempts[0].status,'graded');
        """
    )


def test_remote_hint_failure_uses_same_hint_before_showing_it() -> None:
    run_js(
        """
        const remote = remoteFixture(), session = create(remote);
        await session.init(); await session.saveName('Huy');
        remote.fail.set('reveal_learning_hint','hint save failed');
        await assert.rejects(() => session.revealHint('Q-00001'), /hint save failed/);
        assert.deepEqual(session.state.attempts[0].hint_ids,[]);
        remote.fail.clear(); await session.revealHint('Q-00001');
        const calls = remote.calls.filter(call => call.url.endsWith('reveal_learning_hint'));
        assert.deepEqual(calls.map(call => call.body.p_hint_id),['H1','H1']);
        assert.equal(calls[0].body.p_attempt_id,calls[1].body.p_attempt_id);
        assert.deepEqual(session.state.attempts[0].hint_ids,['H1']);
        """
    )


def test_feedback_retry_keeps_event_id_across_reload() -> None:
    run_js(
        """
        const remote = remoteFixture(), session = create(remote);
        await session.init(); await session.saveName('Lan');
        remote.fail.set('append_learning_feedback','unknown save');
        await assert.rejects(() => session.feedback('Q-00001','like','test'), /unknown save/);
        const first = remote.calls.at(-1).body.p_event_id;
        remote.fail.clear();
        const restored = create(remote); await restored.init();
        await restored.feedback('Q-00001','like','test');
        const second = remote.calls.at(-1).body.p_event_id;
        assert.equal(first, second); assert.equal(remote.feedback.length,1);
        assert.equal(restored.state.attempts.length,0);
        """
    )


def test_remote_expired_session_is_preserved_not_silently_replaced() -> None:
    run_js(
        """
        const remote = remoteFixture(), session = create(remote);
        await session.init(); await session.saveName('Linh');
        const authKey = [...storage.values.keys()].find(key => key.endsWith(':session'));
        const auth = JSON.parse(storage.values.get(authKey)); auth.expires_at = 1;
        storage.values.set(authKey,JSON.stringify(auth));
        remote.fail.set('token?grant_type=refresh_token','refresh failure');
        const restored = create(remote);
        await assert.rejects(() => restored.init(), /Không làm mới được phiên/);
        assert.equal(JSON.parse(storage.values.get(authKey)).user.id,auth.user.id);
        assert.equal(remote.calls.filter(call => call.url.endsWith('/signup')).length,1);
        assert.equal([...storage.values.keys()].some(key => key.endsWith(':records')),false);
        """
    )


def test_dynamic_quality_filters_recommendations_without_rewriting_authoring() -> None:
    run_js(
        """
        const remote = remoteFixture(), session = create(remote);
        remote.quality['Q-00001'].quality_status = 'STALE';
        remote.quality['Q-00002'].question_sha256 = 'changed-hash';
        await session.init(); await session.saveName('Mai');
        const effective = session.learningData();
        assert.equal(effective.question_meta['Q-00001'].initial_check_status,'STALE');
        assert.equal(effective.question_meta['Q-00002'].initial_check_status,'STALE');
        assert.equal(Core.recommendNext(effective,[]).question_id,'Q-00003');
        assert.equal(data.question_meta['Q-00001'].initial_check_status,'PASS');
        assert.match(UI.questionHtml(effective,data.questions[0],
          {response:UI.emptyResponse()}),/Chỉ luyện tập/);
        """
    )


def test_registry_pass_cannot_promote_a_nonpass_package_question() -> None:
    run_js(
        """
        const remote = remoteFixture();
        const statuses = ['REJECT', 'REVIEW', 'STALE', 'UNCHECKED'];
        statuses.forEach((status, i) => {
          data.question_meta[data.questions[i].question_id].initial_check_status = status;
        });
        const session = create(remote); await session.init(); await session.saveName('Ngân');
        const effective = session.learningData();
        statuses.forEach((status, i) => {
          const id = data.questions[i].question_id;
          assert.equal(remote.quality[id].quality_status, 'PASS');
          assert.equal(effective.question_meta[id].initial_check_status, status);
          assert.equal(data.question_meta[id].initial_check_status, status);
        });
        assert.equal(Core.recommendNext(effective, []).question_id, 'Q-00005');
        const attempt = await session.submit('Q-00001', data.questions[0].correct_answer);
        attempt.quality_status = 'PASS'; attempt.evidence_eligible = true;
        attempt.exclusion_reasons = [];
        assert.equal(Core.computeEvidence(effective, [attempt]).kcs[0].state, 'no_evidence');
        """
    )


def test_local_tabs_preserve_recorded_hint_and_other_attempts() -> None:
    run_js(
        """
        const tabA = create(); await tabA.init(); await tabA.saveName('Phương');
        const tabB = create(); await tabB.init();
        const hinted = await tabA.revealHint('Q-00001');
        assert.equal(tabB.state.attempts.length, 0); // deliberately stale tab
        const submitted = await tabB.submit('Q-00001', data.questions[0].correct_answer);
        assert.equal(submitted.attempt_id, hinted.attempt_id);
        assert.deepEqual(submitted.hint_ids, ['H1']);
        assert.equal(Core.computeEvidence(data, [submitted]).kcs[0].state, 'assisted');
        await tabA.submit('Q-00002', data.questions[1].correct_answer);
        await tabB.feedback('Q-00001', 'like', 'Góp ý', submitted.attempt_id);
        const restored = create(); await restored.init();
        assert.equal(restored.state.attempts.length, 2);
        assert.equal(restored.state.feedback.length, 1);
        assert.deepEqual(restored.state.attempts.find(
          row => row.attempt_id === hinted.attempt_id).hint_ids, ['H1']);
        """
    )


def test_local_simultaneous_submit_is_serialized_and_returns_one_attempt() -> None:
    run_js(
        """
        const tabA = create(); await tabA.init(); await tabA.saveName('Quân');
        const tabB = create(); await tabB.init();
        const [first, second] = await Promise.all([
          tabA.submit('Q-00001', data.questions[0].correct_answer),
          tabB.submit('Q-00001', data.questions[0].correct_answer)
        ]);
        assert.equal(first.attempt_id, second.attempt_id);
        const restored = create(); await restored.init();
        assert.equal(restored.state.attempts.length, 1);
        assert.equal(restored.state.attempts[0].is_repeat, false);
        const [a, b] = await Promise.all([
          tabA.revealHint('Q-00002'), tabB.revealHint('Q-00002')
        ]);
        assert.equal(a.attempt_id, b.attempt_id);
        await restored.reload();
        assert.deepEqual(restored.state.attempts.find(
          row => row.question_id === 'Q-00002').hint_ids, ['H1', 'H2']);
        """
    )


def test_local_mutations_fail_closed_without_cross_tab_lock_support() -> None:
    run_js(
        """
        const session = create({locks: null}); await session.init();
        await assert.rejects(() => session.saveName('Sơn'), /Web Locks/);
        assert.equal(storage.values.size, 0);
        assert.equal(session.state.identity, null);
        """
    )


def test_grading_queue_renders_full_frozen_stimulus_inertly() -> None:
    run_js(
        r"""
        const remote = remoteFixture(); remote.grant();
        const frozen = structuredClone(data.questions[4]);
        frozen.stimulus = {kind:'table', text:'FROZEN_SCENARIO <img src=x>',
          table_columns:['FROZEN_HEADER'], table_rows:[['FROZEN_CELL & detail']],
          formula:'FROZEN_FORMULA x < y'};
        remote.grades.push({attempt_id:'other-learner', question_payload:frozen,
          response:{...UI.emptyResponse(), text:'LEARNER_RESPONSE'}, learner_name:'Người học'});
        const {app, view} = mount(remote); await app.ready;
        view('learner-name').value = 'Giảng viên được cấp quyền';
        await view('identity-form').onsubmit({preventDefault(){}});
        await view('grading-toggle').onclick();
        const html = view('grading-queue').innerHTML;
        assert.match(html,/FROZEN_SCENARIO &lt;img src=x&gt;/);
        assert.match(html,/<th>FROZEN_HEADER<\/th>/);
        assert.match(html,/FROZEN_CELL &amp; detail/);
        assert.match(html,/FROZEN_FORMULA x &lt; y/);
        assert.match(html,/LEARNER_RESPONSE/);
        assert.doesNotMatch(html,/<img/);
        """
    )


def test_staff_queue_is_server_gated_and_rubric_scores_are_bounded() -> None:
    run_js(
        """
        const remote = remoteFixture(), session = create(remote);
        await session.init(); await session.saveName('Teacher admin');
        await assert.rejects(() => session.loadQueue(), /không có quyền/);
        assert.equal(remote.calls.some(
          call => call.url.endsWith('get_learning_grading_queue')),false);
        remote.grant();
        remote.grades.push({attempt_id:'student-attempt',question_payload:data.questions[4]});
        await session.reload(); await session.loadQueue();
        await assert.rejects(() => session.grade('student-attempt',[99,1],''), /điểm hợp lệ/);
        await session.grade('student-attempt',[2,1],'Đúng rubric');
        const call = remote.calls.find(call => call.url.endsWith('grade_learning_attempt'));
        assert.deepEqual(call.body.p_scores,[2,1]); assert.ok(call.body.p_event_id);
        assert.equal(session.state.queue.length,0);
        """
    )


def test_invalid_responses_do_not_record_wrong_answers() -> None:
    run_js(
        """
        const session = create(); await session.init(); await session.saveName('Minh');
        await assert.rejects(() => session.submit('Q-00001',UI.emptyResponse()), /hoàn tất/);
        await assert.rejects(() => session.submit('Q-00003',{...UI.emptyResponse(),
          mappings:[{left:'L1',right:'R1'}]}), /hoàn tất/);
        await assert.rejects(
          () => session.submit('Q-00005',{...UI.emptyResponse(),text:' '}), /hoàn tất/);
        assert.equal(session.state.attempts.length,0);
        """
    )


def test_page_has_local_assets_and_no_review_runtime_or_provider_calls() -> None:
    html = (ASSETS / "learning.html").read_text(encoding="utf-8")
    runtime = (ASSETS / "learning-runtime.js").read_text(encoding="utf-8")
    style = (ASSETS / "learning-style.css").read_text(encoding="utf-8")
    assert 'lang="vi"' in html
    assert "learning-style.css" in html
    assert "learning-core.js" in html
    assert "learning-data.js" in html
    assert "review-runtime.js" not in html
    assert "https://" not in html
    assert "api.openai.com" not in runtime
    assert "service_role" not in runtime
    assert re.search(r"@media\s*\(\s*max-width\s*:\s*740px\s*\)", style)
    assert "prefers-reduced-motion" in style
