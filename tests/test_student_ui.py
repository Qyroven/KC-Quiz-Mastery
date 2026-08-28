"""Standalone Student controller tests with an isolated, no-network RPC fixture."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_learning_ui import learning_data

ASSETS = Path(__file__).resolve().parents[1] / "learning_authoring" / "showcase_assets"

HARNESS = r"""
const fs = require('node:fs'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const UI = require(input.runtime), Core = require(input.core), original = input.data;
let sequence = 0, ticks = 0;
const crypto = {randomUUID() {
  return `00000000-0000-4000-8000-${String(++sequence).padStart(12, '0')}`;
}};
const now = () => new Date(1700000000000 + (++ticks) * 1000).toISOString();
function memory() {
  const values = new Map();
  return {values, fail: false, getItem: k => values.has(k) ? values.get(k) : null,
    setItem(k,v) { if (this.fail) throw new Error('storage blocked'); values.set(k,v); }};
}
const tails = new Map();
const locks = {request(name, options, task) {
  const work = (tails.get(name) || Promise.resolve()).then(task);
  tails.set(name, work.catch(() => {})); return work;
}};
const reply = (value, status=200) => ({ok: status>=200 && status<300, status,
  async text() { return status === 204 ? '' : JSON.stringify(value); }});
function fixture() {
  const full = structuredClone(original);
  full.run_id = 'release-1';
  const packet = UI.previewPacket(full);
  packet.course_id = 'course-a'; packet.release_id = 'release-1';
  packet.label = 'First published version';
  packet.publication = {status: 'PUBLISHED', release_id: 'release-1', review_method: 'human'};
  for (const meta of Object.values(packet.question_meta)) {
    Object.assign(meta, {human_approved:true, quality_status:'PASS'});
  }
  const courses = [{course_id:'course-a',title:'Published lesson',
    latest_release:{release_id:'release-1',label:'First published version',
      question_count:full.questions.length,kc_count:1},enrollment:null},
    {course_id:'course-empty',title:'Unpublished draft',latest_release:null,enrollment:null}];
  const attempts = [], feedback = [], calls = [], before = new Set(), after = new Set();
  const learnerId = '00000000-0000-4000-8000-000000009999';
  const quality = Object.fromEntries(Object.entries(packet.question_meta).map(([id,m])=>
    [id,{...m,quality_status:'PASS'}]));
  const auth = {access_token:'fixture-access',refresh_token:'fixture-refresh',
    expires_at:Date.now()/1000+3600,user:{id:learnerId}};
  const fetch = async (url, options) => {
    const body = JSON.parse(options.body || '{}'), name = url.split('/').pop();
    calls.push({url,name,body});
    if(before.delete(name)) throw new Error('network before commit');
    let result;
    if (url.includes('/auth/v1/')) result=auth;
    else if (url.includes('/reviewer_profiles')) return reply(null,204);
    else if (name==='list_learning_courses') result=courses;
    else if (name==='enroll_learning_course') {
      const course=courses.find(c=>c.course_id===body.p_course_id);
      assert(course?.latest_release || course?.enrollment);
      if(course.enrollment) assert.equal(body.p_release_id,course.enrollment.release_id);
      else course.enrollment={course_id:course.course_id,release_id:body.p_release_id,
        enrolled_at:now()};
      result=course.enrollment;
    } else if(name==='get_student_learning_package') {
      assert.equal(body.p_release_id,'release-1'); result=packet;
    } else if(name==='get_learning_state') {
      assert.equal(body.p_run_id,'release-1'); result={attempts,feedback,item_quality:quality};
    } else if(name==='start_learning_attempt') {
      let attempt=attempts.find(a=>a.attempt_id===body.p_attempt_id ||
        (a.question_id===body.p_question_id && a.status==='in_progress'));
      if(!attempt) {
        const q=full.questions.find(q=>q.question_id===body.p_question_id);
        attempt={attempt_id:body.p_attempt_id,learner_id:learnerId,run_id:'release-1',question_id:q.question_id,
          question_sha256:full.question_meta[q.question_id].question_sha256,kc_id:q.kc_id,slot_id:q.slot_id,
          status:'in_progress',started_at:now(),response:null,hint_ids:[],revealed_hints:[],
          quality_status:'PASS',evidence_eligible:false,exclusion_reasons:['not_graded'],
          is_repeat:attempts.some(a=>a.question_id===q.question_id)};
        attempts.push(attempt);
      } result=attempt;
    } else if(name==='reveal_learning_hint') {
      const attempt=attempts.find(a=>a.attempt_id===body.p_attempt_id);
      assert.equal(attempt.status,'in_progress');
      const q=full.questions.find(q=>q.question_id===attempt.question_id);
      if(!attempt.hint_ids.includes(body.p_hint_id)) {
        attempt.hint_ids.push(body.p_hint_id);
        attempt.revealed_hints.push(q.hints.find(h=>h.hint_id===body.p_hint_id));
      } result=attempt;
    } else if(name==='submit_learning_attempt') {
      const attempt=attempts.find(a=>a.attempt_id===body.p_attempt_id);
      if(attempt.status==='in_progress') {
        const q=full.questions.find(q=>q.question_id===attempt.question_id);
        const graded=Core.buildLocalAttempt(full,q.question_id,body.p_response,{
          attempt_id:attempt.attempt_id,
          started_at:attempt.started_at,submitted_at:now(),hint_ids:attempt.hint_ids,learner_id:learnerId,
          attempts:attempts.filter(a=>a.attempt_id!==attempt.attempt_id)});
        Object.assign(attempt,graded,{answer_material:{correct_answer:q.correct_answer,
          answer_explanation:q.answer_explanation,rubric:q.rubric}});
      } result=attempt;
    } else if(name==='append_learning_feedback') {
      let event=feedback.find(e=>e.event_id===body.p_event_id);
      if(event) {
        assert.deepEqual(event.payload,{vote:body.p_vote,note:body.p_note});
      } else {
        event={event_id:body.p_event_id,run_id:body.p_run_id,question_id:body.p_question_id,
          question_sha256:body.p_question_sha256,attempt_id:body.p_attempt_id,
          payload:{vote:body.p_vote,note:body.p_note},created_at:now()};
        feedback.push(event);
      } result=event;
    } else throw new Error('Unexpected API: '+name);
    if(after.delete(name)) throw new Error('network lost after commit');
    return reply(structuredClone(result));
  };
  return {full,packet,courses,attempts,feedback,calls,before,after,fetch,quality,learnerId};
}
async function shared(fx=fixture(),storage=memory()) {
  const session=UI.createSession({core:Core,config:{mode:'shared',
    supabaseUrl:'https://fixture.supabase.co',supabasePublishableKey:'fixture-public'},
    storage,fetch:fx.fetch,crypto,locks,now});
  await session.init(); await session.saveName('QA Student');
  return {session,fx,storage};
}
const first = original.questions[0].question_id;
async function run() {
switch(input.case) {
case 'published_only': {
  const {session,fx}=await shared();
  assert.equal(session.state.data,null); assert.equal(session.state.attempts.length,0);
  await assert.rejects(session.openCourse('course-empty'),/chưa phát hành/);
  await session.openCourse('course-a');
  assert.equal(session.state.data.run_id,'release-1');
  assert(!('correct_answer' in session.state.data.questions[0]));
  assert(!('text' in session.state.data.questions[0].hints[0]));
  assert(!fx.calls.some(c=>/learning_items|review_events|grade_learning_attempt/.test(c.url)));
  break;
}
case 'objective_loop': {
  const {session,fx}=await shared(); await session.openCourse('course-a');
  let html=UI.questionHtml(session.state.data,session.state.data.questions[0]);
  assert(!html.includes('EXPLANATION_HIDDEN')); assert(!html.includes('FIRST_AUTHORED_HINT'));
  const hint=await session.revealHint(first);
  assert.deepEqual(hint.hint_ids,['H1']);
  html=UI.questionHtml(session.state.data,session.state.data.questions[0],{attempt:hint});
  assert(html.includes('FIRST_AUTHORED_HINT')); assert(!html.includes('SECOND_AUTHORED_HINT'));
  const result=await session.submit(first,original.questions[0].correct_answer);
  assert.equal(result.correct,true); assert(result.answer_material);
  const evidence=Core.computeEvidence(session.learningData(),session.state.attempts);
  assert.equal(evidence.kcs[0].assisted_slots,1); assert.equal(evidence.kcs[0].independent_slots,0);
  const calls=fx.calls.filter(c=>c.name==='submit_learning_attempt');
  assert.deepEqual(Object.keys(calls[0].body).sort(),['p_attempt_id','p_response']);
  await session.reload(); assert.equal(session.state.attempts.length,1);
  await session.start(first); await session.submit(first,original.questions[0].correct_answer);
  assert.equal(session.state.attempts.length,2);
  assert.equal(session.state.attempts[1].is_repeat,true);
  const repeatedEvidence=Core.computeEvidence(session.learningData(),session.state.attempts);
  assert.equal(repeatedEvidence.kcs[0].independent_slots,0);
  break;
}
case 'rubric_feedback': {
  const {session,fx}=await shared(); await session.openCourse('course-a');
  const q=original.questions.find(q=>q.interaction==='short_text');
  const result=await session.submit(q.question_id,{text:'A real learner response'});
  assert.equal(result.status,'pending_grade'); assert.equal(result.correct,null);
  const publicQuestion=session.state.data.questions.find(r=>r.question_id===q.question_id);
  let html=UI.questionHtml(session.state.data,publicQuestion,{
    attempt:result,response:result.response});
  assert(html.includes('Chờ chấm')); assert(!html.includes('Cần xem lại câu trả lời'));
  const row=fx.attempts.find(a=>a.attempt_id===result.attempt_id);
  Object.assign(row,{status:'graded',score:1,max_score:3,correct:false,
    grading_method:'rubric_human',grading_version:'rubric-human-v1',rubric_scores:[1,0],
    grading_note:'Explain the second step in your own words.',
    evidence_eligible:true,exclusion_reasons:[]});
  await session.reload();
  html=UI.questionHtml(session.state.data,publicQuestion,{
    attempt:session.state.attempts[0],response:row.response});
  assert(html.includes('Explain the second step'));
  assert(html.includes('1 / 2')); assert(html.includes('0 / 1'));
  const evidence=Core.computeEvidence(session.learningData(),session.state.attempts);
  assert.equal(evidence.kcs[0].state,'needs_practice');
  break;
}
case 'pinned_release': {
  const {session,fx,storage}=await shared(); await session.openCourse('course-a');
  await session.submit(first,original.questions[0].correct_answer);
  fx.courses[0].latest_release={release_id:'release-2',label:'Newer version'};
  await session.reload(); assert.equal(session.state.data.run_id,'release-1');
  const newer=UI.createSession({core:Core,config:{mode:'shared',
    supabaseUrl:'https://fixture.supabase.co',supabasePublishableKey:'fixture-public'},
    storage,fetch:fx.fetch,crypto,locks,now});
  await newer.init();
  assert.equal(newer.state.data.run_id,'release-1'); assert.equal(newer.state.attempts.length,1);
  assert(!fx.calls.some(c=>c.name==='get_student_learning_package' &&
    c.body.p_release_id==='release-2'));
  break;
}
case 'feedback_uncertain_commit': {
  const {session,fx}=await shared(); await session.openCourse('course-a');
  fx.after.add('append_learning_feedback');
  await assert.rejects(session.feedback(first,'dislike','First note'),/network lost/);
  const id=fx.feedback[0].event_id; assert.equal(Object.keys(session.state.pending).length,1);
  await session.reload(); assert.equal(Object.keys(session.state.pending).length,0);
  const retried=await session.feedback(first,'dislike','First note');
  assert.equal(retried.event_id,id); assert.equal(fx.feedback.length,1);
  const changed=await session.feedback(first,'like','A different note');
  assert.notEqual(changed.event_id,id); assert.equal(fx.feedback.length,2);
  break;
}
case 'hint_uncertain_commit': {
  const {session,fx}=await shared(); await session.openCourse('course-a');
  fx.after.add('reveal_learning_hint');
  await assert.rejects(session.revealHint(first,'H1'),/network lost/);
  assert.deepEqual(fx.attempts[0].hint_ids,['H1']);
  await session.reload();
  assert.equal(Object.keys(session.state.pending).length,0);
  await session.revealHint(first,'H1');
  assert.deepEqual(fx.attempts[0].hint_ids,['H1']);
  assert.equal(Object.keys(session.state.pending).length,0);
  await session.revealHint(first);
  assert.deepEqual(fx.attempts[0].hint_ids,['H1','H2']);
  break;
}
case 'feedback_uncommitted_new_payload': {
  const {session,fx}=await shared(); await session.openCourse('course-a');
  fx.before.add('append_learning_feedback');
  await assert.rejects(session.feedback(first,'dislike','Uncertain original'),/network before/);
  const pending=Object.values(session.state.pending)[0];
  await session.feedback(first,'like','New explicitly submitted note');
  assert.equal(fx.feedback.length,1); assert.notEqual(fx.feedback[0].event_id,pending.id);
  await session.retryPending(pending.key);
  assert.equal(fx.feedback.length,2);
  assert(fx.feedback.some(e=>e.payload.note==='Uncertain original'));
  assert.equal(Object.keys(session.state.pending).length,0);
  break;
}
case 'submit_uncertain_commit': {
  const {session,fx}=await shared(); await session.openCourse('course-a');
  fx.after.add('submit_learning_attempt');
  await assert.rejects(session.submit(first,original.questions[0].correct_answer),/network lost/);
  assert.equal(fx.attempts.length,1); assert.equal(fx.attempts[0].correct,true);
  await session.reload();
  const again=await session.submit(first,{selection_ids:['B']});
  assert.equal(again.correct,true); assert.deepEqual(again.response.selection_ids,['A']);
  assert.equal(fx.attempts.length,1);
  break;
}
case 'failed_shared_never_local': {
  const {session,fx,storage}=await shared(); await session.openCourse('course-a');
  fx.before.add('get_learning_state');
  await assert.rejects(session.submit(first,original.questions[0].correct_answer),/network before/);
  assert.equal(fx.attempts.length,0);
  assert(!Array.from(storage.values.keys()).some(k=>k.endsWith(':records')));
  storage.fail=true;
  await assert.rejects(session.feedback(first,'like','No storage'),/chưa lưu/);
  assert.equal(fx.feedback.length,0);
  break;
}
case 'public_packet_guard': {
  const {session,fx}=await shared();
  fx.packet.questions[0].correct_answer=original.questions[0].correct_answer;
  await assert.rejects(session.openCourse('course-a'),/đáp án trước khi nộp/);
  assert.equal(session.state.data,null);
  assert.throws(()=>UI.createSession({config:{mode:'shared',
    supabaseUrl:'https://fixture.supabase.co',supabasePublishableKey:'p'},
    previewData:original,core:Core,storage:memory()}),/xem thử/);
  assert.throws(()=>UI.createSession({config:{},core:Core}),/Không tự chuyển/);
  break;
}
case 'local_explicit': {
  const storage=memory(); let requests=0;
  const session=UI.createSession({config:{mode:'local_preview'},previewData:original,
    core:Core,storage,crypto,locks,now,
    fetch:async()=>{requests++;throw new Error('network forbidden')}});
  await session.init(); assert.equal(session.state.attempts.length,0);
  await session.saveName('QA local only'); await session.openCourse(original.run_id);
  assert.equal(session.state.data.publication,undefined);
  await session.submit(first,original.questions[0].correct_answer);
  assert.equal(session.state.attempts[0].correct,true); assert.equal(requests,0);
  const noLocks=UI.createSession({config:{mode:'local_preview'},previewData:original,
    core:Core,storage,crypto,now});
  await noLocks.init();
  await assert.rejects(noLocks.feedback(first,'like','locked'),/Web Locks/);
  break;
}
case 'quality_fail_closed': {
  const {session,fx}=await shared(); await session.openCourse('course-a');
  await session.submit(first,original.questions[0].correct_answer);
  const independentCount=()=>Core.computeEvidence(
    session.learningData(),session.state.attempts).kcs[0].independent_slots;
  assert.equal(independentCount(),1);
  fx.quality[first].quality_status='STALE'; await session.reload();
  assert.equal(independentCount(),0);
  delete fx.quality[first]; await session.reload();
  assert.equal(independentCount(),0);
  break;
}
case 'unpublished_progress_summary': {
  const packet=UI.previewPacket(original);
  for (let i=1; i<=3; i++) {
    const id='unpublished-'+i;
    packet.kcs.push({kc_id:id,name:'Nội dung chưa phát hành',content_available:false});
    for (let j=0; j<i; j++) packet.slots.push({slot_id:id+'-slot-'+j,kc_id:id});
  }
  const evidence=Core.computeEvidence(packet,[]);
  const before=JSON.stringify({packet,evidence});
  const html=UI.progressHtml(packet,evidence,Core);
  assert.equal((html.match(/<article /g)||[]).length,2);
  assert.equal((html.match(/unpublished-summary/g)||[]).length,1);
  assert(html.includes('3 nội dung · 6 mục tiêu chưa phát hành'));
  assert(html.includes('không có nghĩa là bạn chưa hiểu'));
  assert(html.includes('Kiến thức kiểm thử'));
  assert(!html.includes('data-kc="unpublished-'));
  assert.equal(JSON.stringify({packet,evidence}),before);
  assert.equal(evidence.kcs.length,4);
  assert.equal(evidence.kcs.reduce((total,row)=>total+row.total_slots,0),11);
  const fullyAvailable=UI.progressHtml(original,Core.computeEvidence(original,[]),Core);
  assert(!fullyAvailable.includes('unpublished-summary'));
  break;
}
case 'render_types_levels_safety': {
  for(const q of original.questions) {
    const packet=UI.previewPacket(original);
    const safe=packet.questions.find(r=>r.question_id===q.question_id);
    const response=UI.emptyResponse();
    if(q.interaction==='ordering') response.ordering=q.ordering_options.map(o=>o.option_id);
    const html=UI.questionHtml(packet,safe,{response});
    assert(html.includes('Hiểu')); assert(html.includes('Dễ'));
    assert(!html.includes('Bloom')); assert(!html.includes('Độ khó dự kiến'));
    assert(!html.includes('EXPLANATION_HIDDEN')); assert(!html.includes('EXEMPLAR_HIDDEN'));
    assert(!html.includes('FIRST_AUTHORED_HINT'));
    assert(!html.includes('kc-recall.html')); assert(!html.includes('extraction-review.html'));
    assert(!html.includes('grade_learning_attempt'));
  }
  const malicious=UI.previewPacket(original);
  malicious.questions[0].prompt='<img src=x onerror=alert(1)>';
  assert(UI.questionHtml(malicious,malicious.questions[0]).includes('&lt;img'));
  const html=UI.nextHtml(original,{action:'need_more_evidence',
    reason:'no_variant_for_target_slot',kc_id:'KC-001',slot_id:'SLOT-001',question_id:null,
    alternative:{question_id:original.questions[1].question_id}},false);
  assert(html.includes('Học mục tiêu khác'));
  assert(html.includes('Không thay thế mục tiêu còn thiếu'));
  assert(!html.includes('Đến câu tiếp'));
  const unpublished=structuredClone(original);
  unpublished.kcs[0].content_available=false;
  const unavailable=UI.nextHtml(unpublished,{action:'need_more_evidence',
    reason:'no_variant_for_target_slot',kc_id:'KC-001',slot_id:'SLOT-001',question_id:null},false);
  assert(unavailable.includes('Nội dung này chưa được giảng viên phát hành'));
  assert(!unavailable.includes('data-action="knowledge"'));
  break;
}
default: throw new Error('Unknown test '+input.case);
}
process.stdout.write(JSON.stringify({ok:true,case:input.case}));
}
run().catch(error=>{console.error(error);process.exitCode=1});
"""


@pytest.mark.parametrize(
    "scenario",
    [
        "published_only",
        "objective_loop",
        "rubric_feedback",
        "pinned_release",
        "feedback_uncertain_commit",
        "hint_uncertain_commit",
        "feedback_uncommitted_new_payload",
        "submit_uncertain_commit",
        "failed_shared_never_local",
        "public_packet_guard",
        "local_explicit",
        "quality_fail_closed",
        "unpublished_progress_summary",
        "render_types_levels_safety",
    ],
)
def test_student_controller(scenario: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for UI controller checks")
    result = subprocess.run(
        [node, "-e", HARNESS],
        input=json.dumps(
            {
                "runtime": str(ASSETS / "student-runtime.js"),
                "core": str(ASSETS / "learning-core.js"),
                "data": learning_data(),
                "case": scenario,
            }
        ),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["ok"] is True


def test_student_entrypoint_has_no_authoring_surface_or_embedded_answer_data() -> None:
    html = (ASSETS / "student.html").read_text()
    assert "<!--STUDENT_PREVIEW_SCRIPT-->" in html
    for forbidden in (
        "review-runtime.js",
        "learning-runtime.js",
        "learning-data.js",
        "kc-recall.html",
        "extraction-review.html",
        "quiz-review.html",
        "grading-panel",
        "approve",
        "correct_answer",
    ):
        assert forbidden not in html
    assert 'lang="vi"' in html
    assert 'aria-live="polite"' in html
    assert re.search(r"@media\(max-width:680px\)", (ASSETS / "student-style.css").read_text())


def test_student_runtime_exposes_no_teacher_write_action() -> None:
    runtime = (ASSETS / "student-runtime.js").read_text()
    for forbidden in (
        "grade_learning_attempt",
        "get_learning_grading_queue",
        "append_review",
        "publish_learning",
        "OpenAI",
        "api.openai.com",
        "service_role",
    ):
        assert forbidden not in runtime
    assert "list_learning_courses" in runtime
    assert "get_student_learning_package" in runtime
    assert "rubric_scores" in runtime
    assert "grading_note" in runtime
