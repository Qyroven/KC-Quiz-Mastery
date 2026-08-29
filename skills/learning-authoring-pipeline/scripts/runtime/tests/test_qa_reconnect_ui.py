"""Run the local-only recovery page against fake DOM, storage and fetch; no browser data."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).with_name("qa_reconnect.html")
APPLICATIONS = ("learning-teacher", "learning-student")

HARNESS = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const task=JSON.parse(fs.readFileSync(0,'utf8'));
const USER='00000000-0000-4000-8000-000000001111';
const OTHER='00000000-0000-4000-8000-000000002222';
function fixture(options={}) {
  const teacher=task.application==='learning-teacher',project='127.0.0.1';
  const config={application:task.application,backend:'http://127.0.0.1:39421'};
  const studentPrefix=`la-student:shared:${project}`;
  const keys={session:teacher?`la-teacher-session:${project}`:`${studentPrefix}:session`,
    identity:`${studentPrefix}:identity`,name:`la-teacher-name:${project}`,
    pending:teacher?`la-teacher-pending:${project}:qa-course`:`${studentPrefix}:pending`,
    records:`${studentPrefix}:${USER}:qa-release:records`,
    legacySession:`la-review-session:${project}`,legacyName:`la-review-name:${project}`};
  const old={access_token:'expired-fixture-access',refresh_token:'expired-fixture-refresh',
    expires_at:1,user:{id:USER}};
  const fresh={access_token:'new-fixture-access',refresh_token:'new-fixture-refresh',
    expires_at:Math.floor(Date.now()/1000)+3600,user:{id:USER}};
  const values=new Map([
    [keys.session,JSON.stringify(old)],
    [keys.pending,JSON.stringify({keep:'unconfirmed-operation',event_id:'existing-event'})],
    [keys.records,JSON.stringify({attempts:[{id:'existing-attempt'}],feedback:['keep-feedback']})],
    ['unrelated-key','must remain untouched'],
  ]);
  if(teacher) {
    values.set(keys.name,JSON.stringify('QA giảng viên'));
    values.set(`${studentPrefix}:session`,JSON.stringify({...old,user:{id:OTHER}}));
  } else {
    values.set(keys.identity,JSON.stringify({learner_id:USER,display_name:'QA học viên'}));
    values.set(`la-teacher-session:${project}`,JSON.stringify({...old,user:{id:OTHER}}));
  }
  if(options.setup) options.setup({values,keys,old,fresh});
  const nodes=new Map(),writes=[],requests=[],navigation=[],historyCalls=[];
  const node=id=>{
    if(!nodes.has(id))nodes.set(id,{disabled:id==='reconnect',textContent:'',onclick:null});
    return nodes.get(id);
  };
  const snapshot=()=>Object.fromEntries(values);
  const before=snapshot();
  const localStorage={
    getItem:key=>values.has(key)?values.get(key):null,
    setItem(key,value){
      if(options.storageFailure)throw new Error('storage unavailable');
      writes.push({key,value});values.set(key,value);
    },
    removeItem(){throw new Error('Recovery must not remove any storage key')},
    clear(){throw new Error('Recovery must not clear storage')},
  };
  const location={pathname:'/__qa/reconnect',
    hash:options.hash??`#recovery=one-use-fixture-token&user_id=${USER}`,
    replace:path=>navigation.push(path)};
  let env;
  const fetch=async(url,init)=>{
    requests.push({url,method:init.method,body:JSON.parse(init.body),headers:init.headers});
    if(options.onFetch) await options.onFetch(env);
    if(options.networkFailure)throw new Error('network unavailable');
    return {ok:options.ok!==false,status:options.ok===false?403:200,async json(){
      if(options.malformedJson)throw new SyntaxError('invalid response JSON');
      return structuredClone(Object.hasOwn(options,'response')?options.response:fresh);
    }};
  };
  env={keys,old,fresh,values,localStorage,node,writes,requests,navigation,historyCalls,before,snapshot,
    async click(){if(!node('reconnect').disabled)await node('reconnect').onclick()}};
  vm.runInNewContext(task.source.replace('__QA_RECOVERY_CONFIG__',JSON.stringify(config)),{
    URL,URLSearchParams,localStorage,location,fetch,document:{getElementById:node},
    history:{replaceState(_state,_title,path){historyCalls.push(path);location.hash=''}},
  });
  return env;
}
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('assert','fixture','USER','OTHER',task.assertions)(assert,fixture,USER,OTHER)
  .catch(error=>{console.error(error);process.exitCode=1});
"""


def run_js(application: str, assertions: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for QA recovery page tests")
    script = re.search(r"<script>\s*(.*?)\s*</script>", PAGE.read_text(), re.S)
    assert script, "Recovery page must have its inline script"
    result = subprocess.run(
        [node, "-e", HARNESS],
        input=json.dumps({
            "application": application, "source": script[1], "assertions": assertions,
        }),
        text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize("application", APPLICATIONS)
def test_same_identity_updates_only_matching_role_session_key(application: str) -> None:
    run_js(application, """
      const f=fixture();
      assert.equal(f.node('reconnect').disabled,false);
      assert.equal(f.requests.length,0);assert.equal(f.writes.length,0);
      assert.deepEqual(f.historyCalls,['/__qa/reconnect']);
      assert.doesNotMatch(f.node('identity').textContent,/one-use-fixture-token/);
      await f.click();
      assert.equal(f.writes.length,1);assert.equal(f.writes[0].key,f.keys.session);
      assert.deepEqual(f.snapshot(),{...f.before,[f.keys.session]:JSON.stringify(f.fresh)});
      assert.equal(JSON.parse(f.values.get(f.keys.session)).user.id,USER);
      assert.equal(f.requests.length,1);
      assert.equal(f.requests[0].url,'http://127.0.0.1:39421/__qa/reconnect');
      assert.equal(f.requests[0].method,'POST');
      assert.equal(f.requests[0].body.user_id,USER);
      assert.equal(f.requests[0].body.token,'one-use-fixture-token');
      assert.ok(['learning-teacher','learning-student'].includes(f.requests[0].body.application));
      assert.deepEqual(f.navigation,['/']);
      assert.match(f.node('status').textContent,/Đã khôi phục đúng tài khoản/);
      await f.click();assert.equal(f.requests.length,1);
    """)


@pytest.mark.parametrize("application", APPLICATIONS)
def test_different_existing_identity_cannot_be_replaced(application: str) -> None:
    run_js(application, """
      const f=fixture({setup({values,keys,old}){
        values.set(keys.session,JSON.stringify({...old,user:{id:OTHER}}));
        if(values.has(keys.identity)) values.set(keys.identity,
          JSON.stringify({learner_id:OTHER,display_name:'Different learner'}));
      }});
      assert.equal(f.node('reconnect').disabled,true);await f.click();
      assert.match(f.node('status').textContent,/không lưu đúng tài khoản/);
      assert.deepEqual(f.snapshot(),f.before);assert.equal(f.writes.length,0);
      assert.equal(f.requests.length,0);assert.deepEqual(f.navigation,[]);
    """)


@pytest.mark.parametrize("application", APPLICATIONS)
def test_no_saved_session_does_not_create_identity_or_call_backend(application: str) -> None:
    run_js(application, """
      const f=fixture({setup({values,keys}){values.delete(keys.session)}});
      assert.equal(f.node('reconnect').disabled,true);await f.click();
      assert.match(f.node('status').textContent,/không lưu đúng tài khoản/);
      assert.deepEqual(f.snapshot(),f.before);assert.equal(f.writes.length,0);
      assert.equal(f.requests.length,0);assert.deepEqual(f.navigation,[]);
    """)


def test_student_requires_matching_saved_identity_in_addition_to_session() -> None:
    run_js("learning-student", """
      for(const missing of [true,false]) {
        const f=fixture({setup({values,keys}){
          if(missing)values.delete(keys.identity);
          else values.set(keys.identity,JSON.stringify({learner_id:OTHER,display_name:'Other'}));
        }});
        assert.equal(f.node('reconnect').disabled,true);await f.click();
        assert.match(f.node('status').textContent,/không lưu đúng tài khoản/);
        assert.deepEqual(f.snapshot(),f.before);assert.equal(f.writes.length,0);
        assert.equal(f.requests.length,0);
      }
    """)


@pytest.mark.parametrize("application", APPLICATIONS)
def test_malformed_saved_session_is_not_silently_replaced(application: str) -> None:
    run_js(application, """
      for(const value of ['not-json','null','{}','{"user":{"id":12}}']) {
        const f=fixture({setup({values,keys}){values.set(keys.session,value)}});
        assert.equal(f.node('reconnect').disabled,true);await f.click();
        assert.deepEqual(f.snapshot(),f.before);assert.equal(f.writes.length,0);
        assert.equal(f.requests.length,0);assert.deepEqual(f.navigation,[]);
      }
    """)


@pytest.mark.parametrize("application", APPLICATIONS)
def test_network_or_rejected_one_time_link_keeps_every_storage_key(application: str) -> None:
    run_js(application, """
      for(const options of [{networkFailure:true},{ok:false},{malformedJson:true}]) {
        const f=fixture(options);await f.click();
        assert.equal(f.requests.length,1);assert.equal(f.writes.length,0);
        assert.deepEqual(f.snapshot(),f.before);assert.deepEqual(f.navigation,[]);
        assert.doesNotMatch(f.node('status').textContent,/Đã khôi phục đúng tài khoản/);
      }
    """)


@pytest.mark.parametrize("application", APPLICATIONS)
def test_wrong_or_malformed_returned_session_never_overwrites_old_session(application: str) -> None:
    run_js(application, """
      const fresh=fixture().fresh;
      const malformed=[null,{},[],{...fresh,user:{id:OTHER}},
        {...fresh,access_token:{}},{...fresh,refresh_token:123},
        {...fresh,access_token:''},{...fresh,refresh_token:'   '},
        {...fresh,expires_at:undefined},{...fresh,expires_at:'9999999999'},
        {...fresh,expires_at:fresh.expires_at+0.5},{...fresh,expires_at:1}];
      for(const response of malformed) {
        const f=fixture({response});await f.click();
        assert.equal(f.requests.length,1);assert.equal(f.writes.length,0);
        assert.deepEqual(f.snapshot(),f.before);assert.deepEqual(f.navigation,[]);
        assert.match(f.node('status').textContent,/Không thay đổi phiên/);
      }
    """)


@pytest.mark.parametrize("application", APPLICATIONS)
def test_identity_change_before_click_or_during_fetch_is_not_overwritten(application: str) -> None:
    run_js(application, """
      const change=f=>{
        f.values.set(f.keys.session,JSON.stringify({...f.old,user:{id:OTHER}}));
        if(f.values.has(f.keys.identity))f.values.set(f.keys.identity,
          JSON.stringify({learner_id:OTHER,display_name:'Different account'}));
      };
      const beforeClick=fixture();change(beforeClick);
      const expected=beforeClick.snapshot();await beforeClick.click();
      assert.deepEqual(beforeClick.snapshot(),expected);
      assert.equal(beforeClick.requests.length,0);assert.equal(beforeClick.writes.length,0);
      let duringFetch;
      const pending=fixture({onFetch(f){change(f);duringFetch=f.snapshot()}});
      await pending.click();
      assert.equal(pending.requests.length,1);assert.equal(pending.writes.length,0);
      assert.deepEqual(pending.snapshot(),duringFetch);assert.deepEqual(pending.navigation,[]);
    """)


def test_teacher_legacy_reviewer_session_is_preserved_when_canonical_is_created() -> None:
    run_js("learning-teacher", """
      const f=fixture({setup({values,keys,old}){
        values.delete(keys.session);values.delete(keys.name);
        values.set(keys.legacySession,JSON.stringify(old));
        values.set(keys.legacyName,'Legacy reviewer');
      }});
      assert.match(f.node('identity').textContent,/Legacy reviewer/);await f.click();
      assert.equal(f.writes.length,1);assert.equal(f.writes[0].key,f.keys.session);
      assert.deepEqual(f.snapshot(),{...f.before,[f.keys.session]:JSON.stringify(f.fresh)});
      const conflict=fixture({setup({values,keys,old}){
        values.set(keys.session,JSON.stringify({...old,user:{id:OTHER}}));
        values.set(keys.legacySession,JSON.stringify(old));
      }});
      assert.equal(conflict.node('reconnect').disabled,true);await conflict.click();
      assert.deepEqual(conflict.snapshot(),conflict.before);assert.equal(conflict.writes.length,0);
      assert.equal(conflict.requests.length,0);
    """)


@pytest.mark.parametrize("application", APPLICATIONS)
def test_missing_recovery_link_and_storage_failure_do_not_claim_success(application: str) -> None:
    run_js(application, """
      for(const hash of ['',`#user_id=${USER}`,'#recovery=fixture']) {
        const f=fixture({hash});await f.click();
        assert.equal(f.node('reconnect').disabled,true);
        assert.deepEqual(f.snapshot(),f.before);assert.equal(f.requests.length,0);
        assert.match(f.node('status').textContent,/Cần liên kết khôi phục/);
      }
      const f=fixture({storageFailure:true});await f.click();
      assert.deepEqual(f.snapshot(),f.before);assert.equal(f.writes.length,0);
      assert.deepEqual(f.navigation,[]);
      assert.match(f.node('status').textContent,/storage unavailable/);
    """)
