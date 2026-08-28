"""Exercise Teacher authorization, version publication and real evidence UI offline."""

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
const task = JSON.parse(fs.readFileSync(0, 'utf8'));
const UI = require(task.runtime), Core = require(task.core), data = task.data;
function memory() {
  const values = new Map();
  return {values, getItem(k){return values.has(k) ? values.get(k) : null},
    setItem(k,v){values.set(k,v)}, removeItem(k){values.delete(k)}};
}
let count = 0;
const crypto = {randomUUID(){return `00000000-0000-4000-8000-${String(++count).padStart(12,'0')}`}};
function reply(value, status=200) {
  return {status,ok:status>=200&&status<300,
    async text(){return status===204 ? '' : JSON.stringify(value)}};
}
function fixture() {
  const calls=[], failures=new Map(), storage=memory(); let allowed=false, publications=0;
  const config={enabled:true,runId:data.run_id,supabaseUrl:'https://teacher-test.supabase.co',
    supabasePublishableKey:'public-test-key'};
  const auth={access_token:'test-session',refresh_token:'test-refresh',
    expires_at:Date.now()/1000+3600,user:{id:'00000000-0000-4000-8000-000000004444'}};
  const workspace={can_teach:true,can_publish:true,can_grade:true,course_id:data.run_id,
    title:'Bài học kiểm thử',review_version:'review-opaque-v1',releases:[],learners:[],
    question_reviews:data.questions.map(q=>({question_id:q.question_id,kc_id:q.kc_id,
      title:q.title,question_approved:true,kc_approved:true,publishable:true,reason:null}))};
  const releaseData=structuredClone(data); releaseData.run_id='course-release-1';
  const learner={learner_id:'00000000-0000-4000-8000-000000005555',display_name:'Học viên',
    release_id:releaseData.run_id,attempt_count:0,pending_count:0};
  const learnerState={attempts:[],feedback:[],item_quality:{},learning_package:releaseData,
    learner,release_id:releaseData.run_id};
  const queue=[];
  const fetch=async(url,options)=>{
    assert.ok(url.startsWith(config.supabaseUrl),`Unexpected service ${url}`);
    const name=url.split('/').pop(),body=options.body ? JSON.parse(options.body) : null;
    calls.push({name,url,body,headers:options.headers});
    if(failures.has(name)) throw new Error(failures.get(name));
    if(url.includes('/auth/v1/')) return reply(auth);
    if(url.includes('/reviewer_profiles')) return reply(null,204);
    if(name==='get_teacher_access') return reply({can_teach:allowed,can_publish:allowed,
      can_grade:allowed,user_id:auth.user.id,course_id:data.run_id});
    if(!allowed) return reply({message:'course teacher role required'},403);
    if(name==='get_teacher_workspace') return reply(structuredClone(workspace));
    if(name==='get_teacher_learning_package') return reply(structuredClone(data));
    if(name==='get_teacher_learner_state') return reply(structuredClone(learnerState));
    if(name==='get_learning_grading_queue') return reply(structuredClone(queue));
    if(name==='publish_reviewed_release') {
      const existing=workspace.releases.find(r=>r.publish_event_id===body.p_event_id);
      if(existing) return reply(existing);
      if(body.p_expected_review_version!==workspace.review_version)
        return reply({message:'review version changed'},409);
      const release={release_id:releaseData.run_id,label:body.p_label,
        publish_event_id:body.p_event_id,question_count:body.p_question_ids.length,kc_count:1};
      workspace.releases.push(release); publications++;
      if(failures.has('after-publication')) throw new Error('response lost after commit');
      return reply(release);
    }
    if(name==='grade_learning_attempt') {
      const row=queue.find(a=>a.attempt_id===body.p_attempt_id);
      row.status='graded';row.rubric_scores=body.p_scores;row.grading_note=body.p_note;
      const result=structuredClone(row);queue.splice(queue.indexOf(row),1);return reply(result);
    }
    throw new Error(`Unexpected RPC ${name}`);
  };
  const session=UI.createSession({config,storage,fetch,crypto});
  return {calls,failures,storage,config,auth,workspace,learner,learnerState,releaseData,queue,
    session,fetch,grant(){allowed=true},revoke(){allowed=false},
    publicationCount(){return publications}};
}
function mount(f) {
  const nodes=new Map();
  const view=id=>{
    if(!nodes.has(id))nodes.set(id,{id,hidden:false,value:'',textContent:'',innerHTML:'',disabled:false,
      attrs:{},dataset:{},setAttribute(k,v){this.attrs[k]=v},
      getAttribute(k){return this.attrs[k]||null},
      removeAttribute(k){delete this.attrs[k]},querySelectorAll(){return[]},focus(){},select(){}});
    return nodes.get(id);
  };
  const document={getElementById:view,querySelectorAll(){return[]}};
  const app=UI.mount({document,config:f.config,data,core:Core,storage:f.storage,fetch:f.fetch,
    crypto,clipboard:{async writeText(value){view('copied').textContent=value}}});
  return {app,view};
}
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('UI','Core','data','assert','fixture','mount','memory','crypto',task.assertions)
  (UI,Core,data,assert,fixture,mount,memory,crypto).catch(error=>{
    console.error(error);process.exitCode=1;
  });
"""


def run_js(assertions: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for Teacher UI tests")
    result = subprocess.run(
        [node, "-e", HARNESS],
        input=json.dumps({
            "runtime": str(ASSETS / "teacher-runtime.js"),
            "core": str(ASSETS / "learning-core.js"),
            "data": learning_data(),
            "assertions": assertions,
        }),
        text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_name_creates_identity_not_teacher_role_or_private_reads() -> None:
    run_js("""
      const f=fixture(); await f.session.init();
      assert.equal(f.calls.length,0); assert.equal(f.session.state.identity,null);
      await f.session.saveName('Professor Admin');
      assert.equal(f.session.state.identity.user_id,f.auth.user.id);
      assert.equal(f.session.state.access.can_teach,false);
      assert.equal(f.session.state.workspace,null);
      assert.ok(!f.calls.some(c=>c.name==='get_teacher_workspace'));
      await assert.rejects(()=>f.session.publish(['Q-00001'],'test'),/không được cấp quyền/);
      await assert.rejects(()=>f.session.loadLearner('someone','release'),/không được cấp quyền/);
      assert.ok(!f.calls.some(c=>c.name.includes('publish_reviewed')));
    """)


def test_authorized_teacher_loads_real_workspace_but_never_implicitly_publishes() -> None:
    run_js("""
      const f=fixture(); f.grant(); await f.session.saveName('Giảng viên');
      assert.equal(f.session.state.access.can_teach,true);
      assert.equal(f.session.state.workspace.question_reviews.length,data.questions.length);
      assert.equal(f.session.state.workspace.learners.length,0);
      assert.equal(f.publicationCount(),0);
      assert.equal(f.calls.filter(c=>c.name==='get_teacher_learning_package').length,1);
      assert.ok(f.calls.filter(c=>c.name.startsWith('get_teacher_'))
        .every(c=>c.headers.Authorization==='Bearer test-session'));
    """)


def test_explicit_selection_counts_actual_slots_and_never_modifies_raw_question() -> None:
    run_js("""
      const f=fixture(),before=JSON.stringify(data);f.grant();
      await f.session.saveName('An');
      const stats=UI.selectionSummary(data,f.workspace.question_reviews,new Set(['Q-00001']));
      assert.equal(stats.selected,1);assert.equal(stats.omitted,4);
      assert.equal(stats.uncovered_slots,4);assert.equal(stats.covered_kcs,1);
      await assert.rejects(()=>f.session.publish([],'empty'),/Chỉ phát hành/);
      f.session.state.workspace.question_reviews[1].publishable=false;
      await assert.rejects(()=>f.session.publish(['Q-00002'],'blocked'),/đủ điều kiện/);
      await f.session.publish(['Q-00001'],'Bản đã review');
      const sent=f.calls.find(c=>c.name==='publish_reviewed_release').body;
      assert.deepEqual(sent.p_question_ids,['Q-00001']);
      assert.equal(sent.p_expected_review_version,'review-opaque-v1');
      assert.equal(f.calls.some(c=>c.name==='append_review_event'),false);
      assert.equal(JSON.stringify(data),before);
    """)


def test_changed_review_version_is_not_silently_published_or_rebased() -> None:
    run_js("""
      const f=fixture();f.grant();await f.session.saveName('An');
      f.workspace.review_version='review-opaque-v2';
      await assert.rejects(()=>f.session.publish(['Q-00001'],'old'),/review version changed/);
      assert.equal(f.publicationCount(),0);
      assert.equal(f.session.state.pending.publication,null);
      await f.session.reload();await f.session.publish(['Q-00001'],'reviewed current');
      assert.equal(f.publicationCount(),1);
    """)


def test_uncertain_publication_recovers_by_event_id_without_duplicate() -> None:
    run_js("""
      const f=fixture();f.grant();await f.session.saveName('An');
      f.failures.set('after-publication','lost');
      await assert.rejects(()=>f.session.publish(['Q-00001'],'Bản 1'),/response lost/);
      assert.equal(f.publicationCount(),1);
      const pending=f.session.state.pending.publication;
      assert.ok(pending.event_id);
      await assert.rejects(()=>f.session.publish(['Q-00002'],'different'),/chưa rõ/);
      await f.session.reload();assert.equal(f.session.state.pending.publication,null);
      assert.equal(f.session.state.workspace.releases[0].publish_event_id,pending.event_id);
      assert.equal(f.publicationCount(),1);
    """)


def test_uncommitted_publication_recovers_after_another_teacher_changes_reviews() -> None:
    run_js("""
      const f=fixture();f.grant();await f.session.saveName('An');
      f.failures.set('publish_reviewed_release','network before commit');
      await assert.rejects(()=>f.session.publish(['Q-00001'],'Bản 1'),/network before commit/);
      const pending=f.session.state.pending.publication;
      f.failures.delete('publish_reviewed_release');f.workspace.review_version='new-review';
      await f.session.reload();
      await assert.rejects(()=>f.session.retryPublication(),/review version changed/);
      const retried=f.calls.filter(c=>c.name==='publish_reviewed_release').at(-1).body;
      assert.equal(retried.p_event_id,pending.event_id);
      assert.equal(retried.p_expected_review_version,'review-opaque-v1');
      assert.equal(f.session.state.pending.publication,null);
      await f.session.publish(['Q-00001'],'Bản mới đã review');
      assert.equal(f.publicationCount(),1);
    """)


def test_revoked_or_unverifiable_role_clears_private_data() -> None:
    run_js("""
      const f=fixture();f.grant();await f.session.saveName('An');
      f.session.state.learner={secret:'must disappear'};
      f.session.state.queue=[{secret:'must disappear'}];
      f.revoke();await f.session.reload();
      assert.equal(f.session.state.access.can_teach,false);
      assert.equal(f.session.state.learner,null);assert.deepEqual(f.session.state.queue,[]);
      f.grant();await f.session.reload();f.failures.set('get_teacher_access','unreachable');
      await assert.rejects(()=>f.session.reload(),/unreachable/);
      assert.equal(f.session.state.workspace,null);assert.equal(f.session.state.access.can_teach,false);
    """)


def test_auth_refresh_failure_preserves_existing_identity_without_signup() -> None:
    run_js("""
      const f=fixture(); f.grant();await f.session.saveName('An');
      f.auth.expires_at=0;
      f.storage.setItem('la-teacher-session:teacher-test.supabase.co',JSON.stringify(f.auth));
      f.failures.set('token?grant_type=refresh_token','offline');
      const signups=f.calls.filter(c=>c.name==='signup').length;
      await assert.rejects(()=>f.session.reload(),/giữ nguyên danh tính/);
      assert.equal(f.calls.filter(c=>c.name==='signup').length,signups);
      assert.equal(JSON.parse(f.storage.getItem('la-teacher-session:teacher-test.supabase.co')).user.id,f.auth.user.id);
    """)


def test_previous_review_identity_migrates_but_student_session_does_not() -> None:
    run_js("""
      const f=fixture();f.grant();
      f.storage.setItem('la-review-session:teacher-test.supabase.co',JSON.stringify(f.auth));
      f.storage.setItem('la-review-name:teacher-test.supabase.co','Legacy reviewer');
      await f.session.init();
      assert.equal(f.session.state.identity.user_id,f.auth.user.id);
      assert.equal(f.session.state.identity.display_name,'Legacy reviewer');
      assert.equal(f.calls.some(c=>c.name==='signup'),false);
      const other=fixture();
      other.storage.setItem('la-learning:shared:teacher-test.supabase.co:session',JSON.stringify(f.auth));
      await other.session.init();assert.equal(other.session.state.identity,null);
      assert.equal(other.calls.length,0);
    """)


def test_learner_scope_and_release_are_verified_before_rendering() -> None:
    run_js("""
      const f=fixture();f.grant();f.workspace.learners.push(f.learner);
      await f.session.saveName('An');
      await assert.rejects(()=>f.session.loadLearner('other',f.releaseData.run_id),/không thuộc/);
      const row=await f.session.loadLearner(f.learner.learner_id,f.releaseData.run_id);
      assert.equal(row.attempts.length,0);
      f.learnerState.attempts=[{learner_id:'wrong-person',run_id:f.releaseData.run_id}];
      await assert.rejects(
        ()=>f.session.loadLearner(f.learner.learner_id,f.releaseData.run_id),/chưa khớp/);
      assert.equal(f.session.state.learner,null);
    """)


def test_rubric_grade_uses_frozen_limits_and_saves_teacher_comment_separately() -> None:
    run_js("""
      const f=fixture();f.grant();
      f.workspace.releases.push({release_id:f.releaseData.run_id,label:'Release'});
      const q=f.releaseData.questions.find(q=>q.interaction==='short_text');
      f.queue.push({attempt_id:'attempt1',learner_id:f.learner.learner_id,
        run_id:f.releaseData.run_id,question_id:q.question_id,status:'pending_grade',
        question_payload:q,response:{text:'Learner own words'},hint_ids:['H1']});
      await f.session.saveName('An');await f.session.loadQueue(f.releaseData.run_id);
      assert.equal(f.session.state.queue[0].status,'pending_grade');
      await assert.rejects(()=>f.session.grade('attempt1',[3,1],''),/rubric/);
      await assert.rejects(()=>f.session.grade('attempt1',[2],''),/rubric/);
      const result=await f.session.grade('attempt1',[2,0],'Cần giải thích rõ hơn');
      assert.equal(result.status,'graded');assert.deepEqual(result.rubric_scores,[2,0]);
      assert.equal(result.grading_note,'Cần giải thích rõ hơn');
      assert.deepEqual(f.session.state.queue,[]);
      assert.ok(!f.calls.some(c=>/generate|completions|responses/.test(c.url)));
    """)


def test_teacher_history_shows_pending_rubric_details_without_mastery_score() -> None:
    run_js(r"""
      const f=fixture(),q=f.releaseData.questions.find(q=>q.interaction==='short_text');
      const attempt=Core.buildLocalAttempt(f.releaseData,q.question_id,
        {selection_ids:[],ordering:[],mappings:[],text:'<script>bad</script>'},
        {attempt_id:'a1',learner_id:f.learner.learner_id,hint_ids:['H1'],attempts:[],
          started_at:'2026-08-28T10:00:00Z',submitted_at:'2026-08-28T10:01:00Z'});
      f.learnerState.attempts=[attempt];
      f.learnerState.learner.display_name='<img src=x onerror=bad>';
      f.learnerState.feedback=[{question_id:q.question_id,
        payload:{vote:'dislike',note:'Need clarity'}}];
      const before=JSON.stringify(f.learnerState),html=UI.learnerHtml(f.learnerState,Core);
      assert.match(html,/Chờ chấm rubric — chưa kết luận/);assert.match(html,/Chưa đo/);
      assert.match(html,/&lt;script&gt;bad&lt;\/script&gt;/);assert.doesNotMatch(html,/<script>/);
      assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img/);
      assert.match(html,/FIRST_AUTHORED_HINT/);assert.match(html,/Need clarity/);
      assert.doesNotMatch(html,/mastery-score|mastery_percent|[0-9]+%/);
      attempt.status='graded';attempt.rubric_scores=[1,1];attempt.grading_note='TEACHER_COMMENT';
      attempt.score=2;attempt.max_score=3;attempt.correct=false;
      const graded=UI.learnerHtml(f.learnerState,Core);
      assert.match(graded,/TEACHER_COMMENT/);
      assert.match(graded,/RUBRIC_HIDDEN_BEFORE_SUBMIT: 1\/2/);
      assert.notEqual(before,JSON.stringify(f.learnerState));
      const now=JSON.stringify(f.learnerState);UI.learnerHtml(f.learnerState,Core);
      assert.equal(now,JSON.stringify(f.learnerState));
    """)


def test_mount_has_no_permission_state_and_no_review_iframe_or_fake_students() -> None:
    run_js("""
      const f=fixture(),{app,view}=mount(f);await app.ready;
      assert.equal(view('teacher-app').hidden,true);
      assert.equal(view('teacher-review-frame').getAttribute('src'),null);
      assert.match(view('teacher-status').textContent,/Nhập tên để tạo phiên/);
      assert.doesNotMatch(view('teacher-status').textContent,/Đã cập nhật từ máy chủ/);
      assert.equal(f.calls.length,0);
      view('teacher-name').value='Teacher';
      await view('teacher-identity-form').onsubmit({preventDefault(){}});
      assert.equal(view('teacher-no-access').hidden,false);
      assert.equal(view('teacher-account-id').value,f.auth.user.id);
      await view('teacher-copy-account').onclick();
      assert.equal(view('copied').textContent,f.auth.user.id);
      assert.equal(view('copied').textContent.includes('test-session'),false);
      f.grant();await view('teacher-refresh').onclick();
      assert.equal(view('teacher-app').hidden,false);
      assert.equal(view('teacher-review-frame').getAttribute('src'),'extraction-review.html');
      assert.match(view('teacher-learner-list').innerHTML,/Chưa có lượt học/);
      assert.equal(app.ui.selected.size,0);
      assert.equal(view('teacher-publish').disabled,true);
      f.revoke();await view('teacher-refresh').onclick();
      assert.equal(view('teacher-app').hidden,true);
      assert.equal(view('teacher-review-frame').getAttribute('src'),null);
    """)


def test_ui_rejects_external_review_paths_and_discloses_selection_gaps() -> None:
    run_js("""
      for(const path of ['https://example.com/x.html','../x.html','javascript:x','//evil/x.html'])
        assert.equal(UI.localReviewPath(path),null);
      assert.equal(UI.localReviewPath('quiz-review.html'),'quiz-review.html');
      const f=fixture();f.grant();const {app,view}=mount(f);await app.ready;
      view('teacher-name').value='An';
      await view('teacher-identity-form').onsubmit({preventDefault(){}});
      view('teacher-question-selection').onchange({target:{dataset:{publishQuestion:'Q-00001'},checked:true}});
      assert.equal(app.ui.selected.size,1);
      assert.match(view('teacher-publish-summary').innerHTML,/4 mục tiêu chưa có câu/);
      assert.match(view('teacher-question-selection').innerHTML,/AI ban đầu: PASS/);
      assert.match(view('teacher-question-selection').innerHTML,/Câu đã duyệt/);
      assert.equal(f.publicationCount(),0);
    """)


def test_switching_learner_clears_old_detail_and_blocks_racing_selection() -> None:
    run_js("""
      const f=fixture();f.grant();
      f.workspace.releases.push({release_id:f.releaseData.run_id,label:'Version'});
      const first={...f.learner,display_name:'OLD_LEARNER'},second={...f.learner,
        learner_id:'00000000-0000-4000-8000-000000006666',display_name:'NEW_LEARNER'};
      f.workspace.learners.push(first,second);
      const original=f.fetch;let resume,hold=false;
      f.fetch=async(url,options)=>{
        if(hold&&url.endsWith('/get_teacher_learner_state'))
          await new Promise(resolve=>{resume=resolve});
        return original(url,options);
      };
      const {app,view}=mount(f);await app.ready;
      view('teacher-name').value='An';
      await view('teacher-identity-form').onsubmit({preventDefault(){}});
      app.session.state.learner={...structuredClone(f.learnerState),learner:first};
      app.ui.learnerId=first.learner_id;app.render();
      assert.match(view('teacher-learner-detail').innerHTML,/OLD_LEARNER/);
      f.learnerState.learner=second;hold=true;
      const click=id=>view('teacher-learner-list').onclick({target:{closest(){
        return {dataset:{learner:id}};
      }}});
      const opening=click(second.learner_id);
      assert.doesNotMatch(view('teacher-learner-detail').innerHTML,/OLD_LEARNER/);
      click(first.learner_id);assert.equal(app.ui.learnerId,second.learner_id);
      while(!resume) await Promise.resolve();resume();await opening;
      assert.match(view('teacher-learner-detail').innerHTML,/NEW_LEARNER/);
      assert.equal(app.ui.learnerId,second.learner_id);
    """)


def test_local_preview_displays_read_only_authoring_without_inventing_role() -> None:
    run_js("""
      const f=fixture();f.config.enabled=false;f.config.mode='local_preview';
      const {app,view}=mount(f);await app.ready;
      assert.equal(view('teacher-app').hidden,false);
      assert.equal(view('teacher-review-frame').getAttribute('src'),'extraction-review.html');
      assert.equal(view('teacher-publish').disabled,true);
      assert.equal(view('teacher-identity').hidden,true);
      assert.match(view('teacher-status').textContent,/Chỉ đọc/);
      assert.equal(app.session.state.access.can_teach,false);
      assert.equal(app.session.state.workspace,null);
      assert.equal(f.calls.length,0);
      await assert.rejects(()=>app.session.publish(['Q-00001'],'local'),/không được cấp quyền/);
    """)


def test_teacher_navigation_and_shared_review_fail_closed_in_source() -> None:
    page = (ASSETS / "teacher.html").read_text()
    runtime = (ASSETS / "teacher-runtime.js").read_text()
    review = (ASSETS / "review-runtime.js").read_text()
    ids = re.findall(r'id="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    assert 'data-tab="learners"' in page and 'data-tab="grading"' in page
    assert 'data-tab="student"' not in page and 'href="student.html"' not in page
    assert "get_teacher_access" in review and "!state.canReview" in review
    assert "la-teacher-session:" in review and "la-teacher-session:" in runtime
    assert "Nhập tên không cấp quyền sửa/duyệt" in review
    assert "localStorage.removeItem(sessionStorageKey)" not in review
    assert "append_review_event" not in runtime  # Publish never silently approves.
    assert "publish_reviewed_release" in runtime
    assert "p_expected_review_version" in runtime and "p_question_ids" in runtime


def test_review_decision_rejects_an_unseen_revision_instead_of_approving_latest() -> None:
    source = (ASSETS / "review-runtime.js").read_text()
    functions = "\n".join(
        re.search(rf"  async function {name}\([^\n]*\) \{{.*?\n  \}}", source, re.S)[0]
        for name in ("decisionSnapshot", "makeDecision")
    )
    run_js(functions + """
      const config={runId:'course'},sent=[];
      const state={adapter:{stage:'quiz',itemType:'question',itemKey:'Q1'},events:[]};
      let revision=null,hash='baseline',change=false;
      const targetId=a=>`${a.stage}:${a.itemKey}`;
      async function effectivePayload(){return {revision,sha256:hash}}
      async function loadCurrentTarget(){
        if(change){revision={id:'new-unseen-revision'};hash='changed'}
      }
      async function insertEvent(event){sent.push(event)}
      function broadcastUpdate(){}
      const shown=await decisionSnapshot();change=true;
      await assert.rejects(()=>makeDecision('approve',null,shown),/đã thay đổi/);
      await assert.rejects(()=>makeDecision('reject','old note',shown),/đã thay đổi/);
      assert.equal(sent.length,0);
      change=false;const now=await decisionSnapshot();
      await makeDecision('approve',null,now);
      assert.equal(sent.length,1);
      assert.equal(sent[0].target_revision_id,'new-unseen-revision');
    """)
