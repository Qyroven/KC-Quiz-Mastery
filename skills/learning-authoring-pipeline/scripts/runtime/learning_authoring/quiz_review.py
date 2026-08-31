"""Build one local review UI for the canonical Quiz schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from learning_authoring.artifacts import read_json, write_text
from learning_authoring.quiz_contracts import QuizBatch
from learning_authoring.quiz_media import QUIZ_STIMULUS_RENDERER, render_quiz_images
from learning_authoring.quiz_review_state import load_quiz_semantic_state


def _embedded(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def build_quiz_review(
    run_dir: Path,
    *,
    candidate_dir: Path,
    output_name: str = "quiz-review.html",
) -> Path:
    """Build a read-only review page; local decisions never mutate Quiz JSON."""

    if Path(output_name).name != output_name:
        raise ValueError("output_name must be a filename without directories")
    root = run_dir.expanduser().resolve()
    candidate = candidate_dir.expanduser().resolve()
    quiz_input = read_json(candidate / "quiz-input.json")
    raw_batch = read_json(candidate / "quiz-proposed.json")
    batch = QuizBatch.model_validate(raw_batch)
    batch.validate_against_input(quiz_input)
    metrics = read_json(candidate / "quiz-run-metrics.json")
    metadata = read_json(candidate / "quiz-generation-metadata.json")
    audit_path = candidate / "quiz-form-audit.json"
    audit = (
        read_json(audit_path)
        if audit_path.is_file()
        else {
            "scope": "not generated",
            "summary": {"flag_count": 0},
            "questions": [],
            "portfolio": {"issues": []},
        }
    )
    payload = {
        # Optional v2 reader defaults must not rewrite a legacy review baseline.
        "quiz": raw_batch,
        "stimulus_images": render_quiz_images(root, quiz_input, batch),
        "input": quiz_input,
        "metrics": metrics,
        "metadata": metadata,
        "form_audit": audit,
        "semantic_audit": load_quiz_semantic_state(root, candidate_dir=candidate),
    }
    output = root / output_name
    write_text(output, _TEMPLATE.replace("__QUIZ_PAYLOAD__", _embedded(payload)))
    return output


_TEMPLATE = r"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quiz Review</title>
<style>
:root{--ink:#182033;--muted:#697386;--line:#dfe4eb;--canvas:#f4f7fb;--card:#fff;--blue:#2864a5;--blue-soft:#eef5fd;--amber:#bd6b13;--red:#c84b4b;--nav:326px}
*{box-sizing:border-box}[hidden]{display:none!important}html,body{margin:0;min-height:100%;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,system-ui,sans-serif;color:var(--ink);background:var(--canvas)}button,input,textarea,select{font:inherit}button{cursor:pointer}.page{min-height:100vh;padding:24px 28px 84px}.shell{max-width:1500px;margin:auto;background:var(--card);border:1px solid #e7ebf0;box-shadow:0 8px 30px #1720330b}.intro{padding:26px 28px 21px;border-bottom:1px solid var(--line)}.kicker{display:flex;gap:9px;align-items:center;color:#315d8d;font-size:14px;font-weight:650}.local{color:#707684;background:#f0f1f3;border-radius:999px;padding:5px 10px;font-size:12px}.intro h1{margin:10px 0 5px;font-size:28px}.intro p{margin:0;color:var(--muted);font-size:14px}.metrics{display:flex;flex-wrap:wrap;gap:9px;margin-top:18px}.metric{display:flex;align-items:center;gap:7px;padding:8px 12px;border-radius:999px;background:#f3f4f6;color:#515968;font-size:13px;font-weight:650}.metric i{width:8px;height:8px;border-radius:50%;background:#8391a5}.metric.warn i{background:var(--amber)}.workspace{display:grid;grid-template-columns:var(--nav) 6px minmax(0,1fr);min-height:690px}body.nav-off .workspace{grid-template-columns:0 0 minmax(0,1fr)}.navigator{grid-column:1;min-width:0;border-right:1px solid var(--line);background:#fbfcfe;overflow:hidden}body.nav-off .navigator,body.nav-off .resizer{display:none}.nav-tools{display:grid;grid-template-columns:1fr 38px;gap:8px;padding:14px;border-bottom:1px solid var(--line)}.search{min-width:0;width:100%;border:1px solid #d5dbe4;border-radius:9px;padding:10px 12px;background:#fff}.icon{border:0;background:transparent;color:#526071;border-radius:8px;min-width:34px;height:34px}.icon:hover{background:#edf1f6}.nav-list{height:calc(100vh - 285px);min-height:540px;overflow:auto;padding:6px 12px 24px}.group{padding-top:13px}.group-name{font-size:11px;letter-spacing:.08em;text-transform:uppercase;font-weight:760;color:#737b89;padding:5px 8px 8px}.qrow{width:100%;border:1px solid transparent;background:transparent;border-radius:9px;display:grid;grid-template-columns:10px 1fr auto;gap:8px;text-align:left;color:inherit;padding:10px 9px;margin-bottom:3px}.qrow:hover{background:#f0f3f7}.qrow.active{background:#eef3f9;border-color:#7393b8}.bullet{width:7px;height:7px;border-radius:50%;margin-top:5px;background:#9299a5}.bullet.review{background:var(--amber)}.qtitle{display:block;font-size:13px;font-weight:680;line-height:1.3}.qmeta{display:block;margin-top:4px;color:#727b89;font-size:11px}.qnum{color:#7c8492;font-size:11px;font-weight:700}.resizer{grid-column:2;cursor:col-resize;border-right:1px solid #e8ebef}.resizer:hover,.resizer.drag{background:#7aa0c74d}.main{grid-column:3;min-width:0;background:#fff}.context-bar{position:sticky;top:0;z-index:8;background:#fffc;backdrop-filter:blur(18px);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;align-items:center;gap:10px}.context-copy{min-width:0;display:flex;align-items:center;gap:8px;flex:1}.context-title{max-width:360px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:14px;font-weight:720}.tag{border-radius:999px;background:#f0f2f5;color:#5c6574;padding:6px 9px;font-size:11px;font-weight:680}.tag.blue{background:var(--blue-soft);color:var(--blue)}.position{color:#7a8290;font-size:12px}.segmented{display:flex;background:#f1f3f6;border-radius:9px;padding:3px}.segmented button{border:0;background:transparent;padding:7px 11px;border-radius:7px;color:#626b78;font-size:12px}.segmented button.active{background:#fff;color:#202938;box-shadow:0 1px 4px #1720331a}.content{padding:32px;max-width:980px;margin:auto}.preview-card{border:1px solid #dfe4eb;border-radius:15px;overflow:hidden;box-shadow:0 10px 28px #1720330b}.preview-head{padding:20px 24px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:12px}.preview-label{color:#315d8d;font-size:13px;font-weight:720}.preview-note{margin-left:10px;color:#818896;font-size:11px}.preview-head h2{margin:8px 0 0;font-size:23px}.flow{padding:24px}.step{margin-bottom:22px}.step-title{display:flex;align-items:center;gap:10px;margin-bottom:10px;font-size:13px;font-weight:710;color:#5e6775}.step-no{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;background:#2f6dac;color:#fff}.stimulus{border:1px solid #dfe3e8;border-radius:10px;background:#f8f9fb;padding:15px;font-size:14px;line-height:1.55}.data-table{width:100%;border-collapse:collapse;margin-top:12px}.data-table th,.data-table td{border:1px solid #dfe3e8;padding:8px;text-align:left}.code{white-space:pre-wrap;background:#f5f6f8;border-radius:8px;padding:12px;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}.prompt{font-size:17px;line-height:1.5;font-weight:680}.options{display:grid;gap:8px}.option{width:100%;display:flex;align-items:flex-start;gap:11px;border:1px solid #dce1e7;background:#fff;border-radius:10px;padding:12px;text-align:left;color:inherit}.option:hover{border-color:#88a3c1;background:#f8fbff}.option.selected{border-color:#4e7daf;background:#eef5fd}.option.correct{border-color:#48a36a;background:#eff9f2}.option.wrong{border-color:#d66a6a;background:#fff1f1}.option-key{width:24px;height:24px;border-radius:50%;background:#eef0f3;display:grid;place-items:center;font-size:11px;font-weight:750;flex:none}.answer-box{width:100%;min-height:110px;resize:vertical;border:1px solid #d8dde5;border-radius:10px;padding:12px}.match{display:grid;grid-template-columns:minmax(0,1fr) 220px;gap:10px;align-items:center;margin-bottom:9px}.match select{border:1px solid #d5dbe4;border-radius:8px;padding:9px;background:#fff}.order-row{display:grid;grid-template-columns:28px 1fr auto;gap:9px;align-items:center;border:1px solid #dce1e7;border-radius:9px;padding:9px 10px;margin-bottom:7px}.order-actions button{border:0;background:#eef1f5;width:28px;height:27px;border-radius:6px;margin-left:3px}.simulate{margin-top:15px;display:flex;gap:8px}.primary,.secondary,.danger{border:0;border-radius:8px;padding:9px 14px;font-size:12px;font-weight:680}.primary{background:#2f65a3;color:#fff}.secondary{background:#eef0f3;color:#303846}.danger{background:#fff0f0;color:#a74343}.feedback{display:none;margin-top:12px;border-left:3px solid #4b83bd;background:#f0f6fc;padding:11px 12px;font-size:12px;line-height:1.5}.feedback.show{display:block}.review-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.review-card{border:1px solid #dfe4eb;border-radius:11px;padding:15px}.review-card.wide{grid-column:1/-1}.review-card h3{margin:0 0 10px;color:#737c89;font-size:11px;letter-spacing:.08em;text-transform:uppercase}.review-card p,.review-card li{margin:0;font-size:13px;line-height:1.55}.review-card ul{margin:8px 0 0;padding-left:18px}.audit-note{border-left:3px solid #8391a5;padding:9px 10px;background:#f4f6f8;font-size:12px}.issue{border-left:3px solid var(--amber);background:#fff8ec;padding:9px 10px;margin-top:8px;font-size:12px}.issue.major{border-color:var(--red);background:#fff1f1}.bottom{position:fixed;z-index:20;left:0;right:0;bottom:0;height:64px;display:flex;align-items:center;gap:10px;padding:0 28px;background:#fffef8f2;border-top:1px solid #e4e1d7;backdrop-filter:blur(18px)}.bottom-note{color:#777d87;font-size:12px}.bottom .space,.sheet-head .space{flex:1}.bottom button.active{outline:2px solid #315d8d55}.modal{position:fixed;inset:0;z-index:60;display:none;place-items:center;padding:24px;background:#11182788}.modal.open{display:grid}.sheet{width:min(1000px,96vw);height:min(820px,92vh);background:#fff;border-radius:14px;display:grid;grid-template-rows:52px 1fr;overflow:hidden}.sheet-head{display:flex;align-items:center;padding:0 14px;border-bottom:1px solid var(--line);font-size:13px;font-weight:700}.sheet pre{margin:0;padding:17px;overflow:auto;background:#f6f7f9;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}@media(max-width:800px){.page{padding:0 0 78px}.shell{border:0}.workspace{display:block}.navigator,.resizer{display:none!important}.content{padding:14px}.context-title{display:none}}
</style>
<style>
.simulate{flex-wrap:wrap}.hint-stack{display:grid;gap:9px;margin-top:16px}.hint-card{border:1px solid #d9e5f2;border-radius:10px;background:#f4f8fd;padding:12px 14px;font-size:13px;line-height:1.55;white-space:pre-wrap}.hint-card strong{display:block;margin-bottom:4px;color:#315d8d;font-size:12px}.preview-state{margin:12px 0 0;color:#737c89;font-size:11px;line-height:1.5}.hint-empty{color:#737c89;font-size:12px;margin-top:13px}.simulate button:disabled{opacity:.55;cursor:default}.bullet.pass,.metric.pass i{background:#23864a}.bullet.reject,.metric.reject i{background:var(--red)}.bullet.stale{background:var(--amber)}.semantic-status{display:inline-flex;align-items:center;gap:6px;padding:5px 9px;border-radius:999px;background:#f0f2f5;color:#697386;font-size:11px;font-weight:700}.semantic-status.pass{background:#edf8f1;color:#257546}.semantic-status.review,.semantic-status.stale{background:#fff6e9;color:#a2670f}.semantic-status.reject{background:#fff0f0;color:#a74343}.semantic-summary{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}.semantic-check{border-top:1px solid var(--line);padding-top:11px;margin-top:11px}.semantic-check-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:6px}.semantic-check-head strong{font-size:12px}.semantic-issue{margin:9px 0;padding:10px;border-radius:8px;background:#f6f8fb;font-size:12px}.semantic-issue details{margin-top:6px;color:#657187}.semantic-issue blockquote{margin:7px 0;padding-left:10px;border-left:2px solid #cdd8e5;white-space:pre-wrap}.semantic-note{margin-top:9px!important;color:#737c89;font-size:12px!important}.hint-review-list{display:grid;gap:9px}.hint-review-id{color:#737c89;font-size:11px}.hint-review-list p{white-space:pre-wrap}.feedback .code{margin:8px 0;background:#ffffffa6}
</style>
<style>
.context-bar{flex-wrap:wrap}.context-copy{flex:1 1 260px;flex-wrap:wrap;row-gap:6px}.context-title{min-width:0;max-width:100%;flex:1 1 220px}.context-copy>.tag,.context-copy>.position{flex-shrink:0;white-space:nowrap}.assessment-tag{padding:5px 8px;font-size:11px}.context-bar>.icon,.context-bar>.segmented{flex-shrink:0}
@media(max-width:800px){.context-bar{padding:10px 12px;gap:8px}.context-copy{gap:6px}.assessment-tag{font-size:10px}}
</style>
</head>
<body>
<div class="page"><section class="shell">
<header class="intro"><div class="kicker">Learning Authoring · <span id="model"></span><span class="local">Quiz experimental</span></div><h1>Kiểm duyệt quiz</h1><p>Kiểm định ban đầu, gợi ý và nội dung câu hỏi. Kết quả AI không thay thế phê duyệt của giảng viên.</p><div class="metrics" id="metrics"></div></header>
<div class="workspace">
<aside class="navigator"><div class="nav-tools"><input id="search" class="search" placeholder="Tìm câu hỏi hoặc KC"><button id="collapseNav" class="icon" title="Thu gọn">◧</button></div><nav id="navList" class="nav-list"></nav></aside>
<div id="resizer" class="resizer"></div>
<main class="main"><div class="context-bar"><button id="expandNav" class="icon" hidden>☰</button><button id="prev" class="icon">‹</button><button id="next" class="icon">›</button><div class="context-copy"><span id="contextTitle" class="context-title"></span><span id="kcTag" class="tag blue"></span><span id="typeTag" class="tag"></span><span id="position" class="position"></span><span id="bloomTag" class="tag assessment-tag" hidden></span><span id="difficultyTag" class="tag assessment-tag" hidden></span></div><button id="rawButton" class="icon" title="Raw JSON">{}</button><div class="segmented"><button id="studentMode" class="active">Học viên thấy</button><button id="reviewMode">Reviewer</button></div></div><div id="content" class="content"></div></main>
</div></section></div>
<footer class="bottom"><span class="bottom-note">Trạng thái local không sửa output JSON.</span><span class="space"></span><button class="secondary decision" data-decision="edit">✎ Sửa</button><button class="danger decision" data-decision="reject">× Từ chối</button><button class="primary decision" data-decision="approve">✓ Duyệt</button></footer>
<div id="modal" class="modal"><div class="sheet"><div class="sheet-head">Raw question JSON<span class="space"></span><button id="closeModal" class="icon">✕</button></div><pre id="raw"></pre></div></div>
<script id="payload" type="application/json">__QUIZ_PAYLOAD__</script>
<script>
const DATA=JSON.parse(document.getElementById("payload").textContent),questions=DATA.quiz.questions;
const assessmentSlots=new Map((DATA.quiz.assessment_slots||[]).map(function(slot){return[slot.slot_id,slot]}));
const kcs=new Map(DATA.input.leaf_kcs.map(function(k){return[k.kc_id,k]})),groups=new Map(DATA.input.kc_groups.map(function(g){return[g.group_id,g]}));
const audits=new Map((DATA.form_audit.questions||[]).map(function(a){return[a.question_id,a]}));
const $=function(s){return document.querySelector(s)},esc=function(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
function stableShuffle(rows,seed){let h=2166136261;for(const c of seed)h=Math.imul(h^c.charCodeAt(0),16777619);const out=rows.slice();for(let i=out.length-1;i>0;i--){h=Math.imul(h^(i+1),16777619);const j=(h>>>0)%(i+1),x=out[i];out[i]=out[j];out[j]=x}return out}
const storageKey="quiz-review-"+DATA.quiz.source_ref.kc_set_sha256.slice(0,12)+"-"+DATA.metadata.request_fingerprint.slice(0,12);
let index=0,mode="student",order=[],decisions=JSON.parse(localStorage.getItem(storageKey)||"{}");
// These copies remain the generated baseline. Shared edits are separate revisions.
const baselineQuestions=new Map(questions.map(q=>[q.question_id,JSON.parse(JSON.stringify(q))]));
const revisedQuestionIds=new Set();
const semanticAudit=DATA.semantic_audit||null;
const semanticRows=new Map(((semanticAudit&&semanticAudit.questions)||[]).map(row=>[row.question_id,row]));
let previewState=null;
let semanticUpstreamStale='',semanticSyncUncertain='';
function setQuizReviewDependencyState({stale='',uncertain=''}={}){
  // Once a changed upstream revision is observed, this generated snapshot's
  // report cannot become current again merely by navigating to another item.
  if(stale)semanticUpstreamStale=stale;
  semanticSyncUncertain=uncertain;
  render();
}
function stableContent(value){
  if(Array.isArray(value))return '['+value.map(stableContent).join(',')+']';
  if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+stableContent(value[key])).join(',')+'}';
  return JSON.stringify(value);
}
function currentQuizHasRevision(){
  return revisedQuestionIds.size>0||questions.some(q=>stableContent(q)!==stableContent(baselineQuestions.get(q.question_id)));
}
function markQuestionRevision(questionId){
  revisedQuestionIds.add(questionId);
  previewState=null;
}
function resetPreview(q){
  previewState={question_id:q.question_id,content:stableContent(q),hint_ids_shown:[],answer_seen:false};
  return previewState;
}
function questionPreview(q){
  if(!previewState||previewState.question_id!==q.question_id||previewState.content!==stableContent(q))return resetPreview(q);
  return previewState;
}
function authoredHints(q){return Array.isArray(q.hints)?q.hints:[]}
function hasHintDecision(q){return Array.isArray(q.hints)&&Object.hasOwn(q,'hint_absence_reason')&&(q.hints.length?q.hint_absence_reason===null:typeof q.hint_absence_reason==='string'&&Boolean(q.hint_absence_reason.trim()))}
function hintKindLabel(kind){return {cue:'Gợi mở',strategy:'Hướng giải',step:'Một bước hỗ trợ'}[kind]||kind}
function previewStateHTML(q){
  const state=questionPreview(q);
  return 'Mô phỏng tại trình duyệt · đã xem '+state.hint_ids_shown.length+' gợi ý · '+(state.answer_seen?'đã mở đáp án / reviewer':'chưa mở đáp án')+'. Không lưu lượt học hay tính mastery.';
}
function shownHintsHTML(q){
  const shown=new Set(questionPreview(q).hint_ids_shown);
  return authoredHints(q).filter(h=>shown.has(h.hint_id)).map((h,i)=>'<section class="hint-card" data-hint-id="'+esc(h.hint_id)+'"><strong>Gợi ý '+(i+1)+' · '+esc(hintKindLabel(h.kind))+'</strong>'+esc(h.text)+'</section>').join('');
}
function updateHintPreview(q){
  const hints=authoredHints(q),state=questionPreview(q),button=$('#hintButton');
  if(button){
    const remaining=hints.length-state.hint_ids_shown.length;
    button.disabled=remaining<=0;
    button.textContent=remaining>0?(state.hint_ids_shown.length?'Gợi ý tiếp theo':'Gợi ý')+' ('+(state.hint_ids_shown.length+1)+'/'+hints.length+')':'Đã xem hết '+hints.length+' gợi ý';
  }
  if($('#hintStack'))$('#hintStack').innerHTML=shownHintsHTML(q);
  if($('#previewState'))$('#previewState').textContent=previewStateHTML(q);
}
function showNextHint(q){
  const state=questionPreview(q),next=authoredHints(q).find(h=>!state.hint_ids_shown.includes(h.hint_id));
  if(next)state.hint_ids_shown.push(next.hint_id);
  updateHintPreview(q);
}
function semanticQuestionState(q){
  const row=semanticRows.get(q.question_id),base=semanticAudit||{};
  if(!semanticAudit||base.status==='NOT_REVIEWED')return {status:'NOT_REVIEWED',row:null,reason:'Chưa có kiểm định ngữ nghĩa ban đầu cho bản này.'};
  if(base.status==='STALE'||currentQuizHasRevision()||semanticUpstreamStale)return {status:'STALE',row:row||null,reason:semanticUpstreamStale||(currentQuizHasRevision()?'Bộ quiz đã có revision. Kiểm định snapshot gốc không còn áp dụng; cần kiểm định lại bản sửa.':(base.reasons||[]).join(' ')||base.reason||'Nguồn hoặc nội dung đã đổi; cần kiểm định lại.')};
  if(!row)return {status:'NOT_REVIEWED',row:null,reason:'Câu này chưa có kết quả kiểm định.'};
  let status=row.status||'NOT_REVIEWED';
  const reviewer=base.reviewer||(base.report&&base.report.reviewer),scope=base.scope||(base.report&&base.report.scope);
  if(status==='PASS'&&(!reviewer||reviewer.mode!=='independent'||!scope||scope.source_coverage!=='complete'||(scope.limitations||[]).length||semanticSyncUncertain||!hasHintDecision(q)))status='REVIEW';
  return {status:status,row:row,reason:[...(base.reasons||[]),...(row.status_reasons||[]),semanticSyncUncertain].filter(Boolean).join(' ')};
}
function semanticLabel(status){return {PASS:'AI: đạt kiểm định ban đầu',REVIEW:'Cần xem lại',REJECT:'Không đạt kiểm định',NOT_REVIEWED:'Chưa kiểm định',STALE:'Cần kiểm định lại'}[status]||'Chưa kiểm định'}
function semanticBadge(status){return '<span class="semantic-status '+esc(status.toLowerCase())+'">'+esc(semanticLabel(status))+'</span>'}
function semanticMetrics(){
  const counts={};questions.forEach(q=>{const s=semanticQuestionState(q).status;counts[s]=(counts[s]||0)+1});
  return Object.entries(counts).map(([s,n])=>'<span class="metric '+(s==='PASS'?'pass':s==='REJECT'?'reject':s==='REVIEW'||s==='STALE'?'warn':'')+'"><i></i>'+n+' '+esc(semanticLabel(s))+'</span>').join('');
}
function renderMetrics(){
  const flagged=(DATA.form_audit.questions||[]).filter(x=>x.status==="FORM_REVIEW").length;
  const rows=[["Câu hỏi",questions.length,""],["Leaf KC",new Set(questions.flatMap(questionKCIds)).size,""],["Dạng tương tác",new Set(questions.map(q=>q.interaction)).size,""]];
  if(assessmentSlots.size)rows.splice(1,0,["Assessment slots",assessmentSlots.size,""]);
  if(flagged)rows.push(["Có cờ hình thức",flagged,"warn"]);
  $("#metrics").innerHTML=rows.map(x=>'<span class="metric '+x[2]+'"><i></i>'+x[1]+" "+x[0]+"</span>").join("")+semanticMetrics();
}
function variantLabel(q){const slot=assessmentSlots.get(q.slot_id);return slot?slot.slot_id+" · variant "+q.variant_index+" / "+slot.variant_count:"variant "+q.variant_index+" · legacy KC numbering"}
function questionAssessment(q){
  const slot=assessmentSlots.get(q.slot_id);
  // An item's own assessment overrides planning labels, never a broken source binding.
  // Legacy records keep their slot labels without writing synthetic fields into raw JSON.
  const bound=!q.slot_id||(slot&&slot.kc_id===q.kc_id);
  const assessment=bound?(q.assessment??(q.slot_id?slot:q)):{};
  const blooms={remember:'Remember',understand:'Understand',apply:'Apply',analyze:'Analyze',evaluate:'Evaluate',create:'Create'};
  const difficulties={easy:'Dễ',medium:'Trung bình',hard:'Khó',unknown:'Chưa ước lượng'};
  const label=(labels,value)=>typeof value==='string'&&Object.hasOwn(labels,value)?labels[value]:'';
  return {bloom:label(blooms,assessment.cognitive_operation),difficulty:label(difficulties,assessment.intended_difficulty)};
}
function renderAssessmentBadges(q){
  const assessment=questionAssessment(q),bloom=$('#bloomTag'),difficulty=$('#difficultyTag');
  const origin=q.slot_id&&q.assessment==null?' Mức chung của slot; chưa có đánh giá riêng cho câu.':' Đánh giá riêng của câu.';
  bloom.hidden=!assessment.bloom;
  bloom.textContent=assessment.bloom;
  bloom.title=assessment.bloom?'Thao tác nhận thức theo Bloom: '+assessment.bloom+'. Không phải điểm hay độ khó.'+origin:'';
  bloom.setAttribute('aria-label',bloom.title);
  difficulty.hidden=!assessment.difficulty;
  difficulty.textContent=assessment.difficulty;
  difficulty.title=assessment.difficulty?'Độ khó dự kiến: '+assessment.difficulty+'. Chưa hiệu chuẩn bằng dữ liệu người học.'+origin:'';
  difficulty.setAttribute('aria-label',difficulty.title);
}
function questionKCIds(q){return [...new Set([q.kc_id,...(q.additional_slot_ids||[]).map(id=>(assessmentSlots.get(id)||{}).kc_id)].filter(Boolean))]}
function slotHTML(q){
  const own=q.assessment, labels=questionAssessment(q);
  const item=own&&(labels.bloom||labels.difficulty)?'<section class="review-card wide"><h3>Mức độ của câu này</h3><p>'+esc(labels.bloom)+' · '+esc(labels.difficulty)+'</p><p>'+esc(own.rationale||'')+'</p></section>':'';
  return item+[q.slot_id,...(q.additional_slot_ids||[])].map(id=>{const slot=assessmentSlots.get(id);if(!slot)return'';return'<section class="review-card wide"><h3>Assessment slot · '+esc(slot.slot_id)+' · '+esc(slot.kc_id)+'</h3><p><b>'+esc(slot.evidence_intent)+'</b></p><p>Mức mục tiêu chung: '+esc(slot.cognitive_operation)+' · '+esc(slot.intended_difficulty)+' · variant '+esc(q.variant_index)+' / '+esc(slot.variant_count)+'</p><p style="margin-top:8px">'+esc(slot.justification)+'</p></section>'}).join('')
}
function contextEvidenceHTML(q){const rows=q.context_evidence_refs||[];if(!rows.length)return'';return'<section class="review-card wide"><h3>Ngữ cảnh giảng viên · tách biệt với Extraction</h3>'+rows.map(function(e){return'<p><b>'+esc(e.context_id)+'</b> · '+(e.pages&&e.pages.length?'Liên hệ trang PDF: '+e.pages.map(esc).join(', '):'Ngữ cảnh toàn tài liệu · không gán trang PDF')+'</p><div class="code">'+esc(e.excerpt||e.description||'')+'</div>'}).join('')+'</section>'}
function grouped(rows){const map=new Map;rows.forEach(function(q){if(!map.has(q.group_id))map.set(q.group_id,[]);map.get(q.group_id).push(q)});return map}
function renderNav(){
  const term=$("#search").value.trim().toLowerCase(),rows=questions.filter(q=>[q.question_id,q.title,q.prompt,questionKCIds(q).join(" "),q.slot_id,(assessmentSlots.get(q.slot_id)||{}).evidence_intent,(kcs.get(q.kc_id)||{}).name].join(" ").toLowerCase().includes(term));
  let html="";
  for(const [gid,list] of grouped(rows)){
    html+='<section class="group"><div class="group-name">'+esc((groups.get(gid)||{}).name||gid)+"</div>";
    list.forEach(q=>{
      const i=questions.indexOf(q),audit=audits.get(q.question_id),state=semanticQuestionState(q);
      const formFlag=Boolean(audit&&audit.status==="FORM_REVIEW"),dot=state.status==="NOT_REVIEWED"&&formFlag?"review":state.status.toLowerCase();
      const title=semanticLabel(state.status)+(formFlag?" · có cảnh báo hình thức":"");
      html+='<button class="qrow '+(i===index?"active":"")+'" data-index="'+i+'"><span class="bullet '+dot+'" title="'+esc(title)+'"></span><span><span class="qtitle">'+esc(q.title)+'</span><span class="qmeta">'+esc(q.interaction.replaceAll("_"," "))+" · "+esc(variantLabel(q))+'</span></span><span class="qnum">'+esc(q.question_id.replace("Q-",""))+"</span></button>";
    });
    html+="</section>";
  }
  $("#navList").innerHTML=html||'<p style="padding:20px;color:#777">Không có kết quả</p>';
  document.querySelectorAll(".qrow").forEach(b=>{b.onclick=()=>select(+b.dataset.index)});
}
__QUIZ_STIMULUS_RENDERER__
function stimulusHTML(s){return renderQuizStimulus(s,DATA.stimulus_images||[])}
function optionHTML(q){return'<div class="options">'+stableShuffle(q.choice_options,q.question_id+"-choice").map(function(o,i){return'<button class="option" data-option="'+o.option_id+'"><span class="option-key">'+String.fromCharCode(65+i)+"</span><span>"+esc(o.text)+"</span></button>"}).join("")+"</div>"}
function orderHTML(q){return order.map(function(id,i){const o=q.ordering_options.find(function(x){return x.option_id===id});return'<div class="order-row"><strong>'+(i+1)+"</strong><span>"+esc(o?o.text:id)+'</span><span class="order-actions"><button data-move="-1" data-pos="'+i+'">↑</button><button data-move="1" data-pos="'+i+'">↓</button></span></div>'}).join("")}
function responseHTML(q){
  if(q.interaction==="single_select"||q.interaction==="multi_select")return optionHTML(q);
  if(q.interaction==="short_text")return'<textarea class="answer-box" placeholder="Nhập câu trả lời…"></textarea>';
  if(q.interaction==="numeric_input")return'<label>Nhập kết quả'+(q.correct_answer.numeric.unit?' ('+esc(q.correct_answer.numeric.unit)+')':'')+'<input class="answer-box" type="number" step="any" inputmode="decimal" aria-label="Câu trả lời số" autocomplete="off"></label>';
  if(q.interaction==="matching"){const right=stableShuffle(q.matching_right,q.question_id+"-match");return q.matching_left.map(function(o){return'<div class="match"><span><b>'+esc(o.text)+'</b></span><select><option>Chọn đáp án</option>'+right.map(function(r){return'<option value="'+esc(r.option_id)+'">'+esc(r.text)+"</option>"}).join("")+"</select></div>"}).join("")}
  order=stableShuffle(q.ordering_options,q.question_id+"-order").map(function(o){return o.option_id});
  return'<div id="order">'+orderHTML(q)+"</div>";
}
function step(n,label,body){return'<section class="step"><div class="step-title"><span class="step-no">'+n+"</span><span>"+label+"</span></div>"+body+"</section>"}
function renderStudent(q){
  let n=1,flow="";
  const hints=authoredHints(q),state=questionPreview(q);
  if(q.stimulus.kind!=="none")flow+=step(n++,"Dữ kiện",stimulusHTML(q.stimulus));
  flow+=step(n++,"Câu hỏi",'<div class="prompt">'+esc(q.prompt)+"</div>");
  flow+=step(n,"Trả lời",responseHTML(q));
  const hintControl=hints.length?'<button id="hintButton" class="secondary">Gợi ý</button>':"";
  const noHint=hints.length?"":'<p class="hint-empty">'+(Object.hasOwn(q,"hints")?"Câu này không có gợi ý.":"Bản câu hỏi này chưa có gợi ý được soạn.")+"</p>";
  const feedback=state.answer_seen?answerFeedbackHTML(q):"";
  return '<article class="preview-card"><header class="preview-head"><div><span class="preview-label">Học viên sẽ thấy</span><span class="preview-note">Mô phỏng tương tác</span><h2>'+esc(q.title)+'</h2></div><span class="tag">'+esc(q.interaction.replaceAll("_"," "))+'</span></header><div class="flow">'+flow+'<div class="simulate">'+hintControl+'<button id="check" class="primary">Xem đáp án</button><button id="reset" class="secondary">Làm lại</button></div>'+noHint+'<div id="hintStack" class="hint-stack" aria-live="polite">'+shownHintsHTML(q)+'</div><p id="previewState" class="preview-state">'+esc(previewStateHTML(q))+'</p><div id="feedback" class="feedback'+(state.answer_seen?" show":"")+'">'+feedback+"</div></div></article>";
}
function answerText(q){const a=q.correct_answer,all=[].concat(q.choice_options,q.matching_left,q.matching_right,q.ordering_options),text=function(id){const row=all.find(function(o){return o.option_id===id});return row?row.text:id};if(a.numeric)return a.numeric.value+(a.numeric.unit?' '+a.numeric.unit:'')+' · Sai số tuyệt đối cho phép: '+a.numeric.absolute_tolerance;if(a.selection_ids.length)return a.selection_ids.map(text).join(", ");if(a.ordering.length)return a.ordering.map(text).join(" → ");if(a.mappings.length)return a.mappings.map(function(m){return(m.slot_id?'['+m.slot_id+'] ':'')+text(m.left)+" → "+text(m.right)}).join("\n");return a.text}
function renderReviewer(q){
  const kc=kcs.get(q.kc_id)||{},audit=audits.get(q.question_id);
  const issues=(audit&&audit.issues||[]).map(x=>'<div class="issue '+(x.severity==="major"?"major":"")+'"><b>'+esc(x.code)+"</b><br>"+esc(x.note)+"</div>").join("");
  const formNote=currentQuizHasRevision()?"Cảnh báo hình thức dưới đây thuộc snapshot gốc; chưa kiểm tra lại revision.":"Không có cờ hình thức không có nghĩa câu hỏi đúng, hay, không mơ hồ hoặc đã được duyệt.";
  return '<div class="review-grid">'+semanticReviewHTML(q)+'<section class="review-card wide"><h3>KC chính · xem các mục tiêu được đo ở từng slot</h3><p><b>'+esc(kc.name||q.kc_id)+'</b></p><p style="margin-top:8px">'+esc(kc.observable_claim||kc.knowledge_description||"")+'</p></section>'+slotHTML(q)+'<section class="review-card"><h3>Đáp án</h3><div class="code">'+esc(answerText(q))+'</div></section><section class="review-card"><h3>Rubric</h3><ul>'+(q.rubric.map(r=>"<li>"+(r.slot_id?"["+esc(r.slot_id)+"] ":"")+esc(r.criterion)+" ("+r.points+")</li>").join("")||"<li>Chấm theo đáp án cấu trúc</li>")+'</ul></section><section class="review-card"><h3>Giải thích</h3><p>'+esc(q.answer_explanation)+'</p></section><section class="review-card"><h3>Nguồn PDF từ KC</h3><p>'+(q.evidence_refs.map(e=>"Trang "+esc(e.page)+" · "+e.block_ids.map(esc).join(", ")).join("<br>")||'Không có nguồn PDF · xem ngữ cảnh giảng viên')+'</p></section>'+contextEvidenceHTML(q)+reviewHintsHTML(q)+'<section class="review-card wide"><h3>Cảnh báo hình thức</h3><div class="audit-note">'+esc(formNote)+'</div>'+(issues||'<p style="margin-top:10px">'+(audit?'Heuristic không phát hiện cue hình thức trong snapshot.':'Chưa có form audit cho câu này.')+'</p>')+"</section></div>";
}
function bindStudent(q){
  document.querySelectorAll("[data-option]").forEach(b=>{b.onclick=()=>{
    if(q.interaction==="single_select")document.querySelectorAll("[data-option]").forEach(x=>x.classList.remove("selected"));
    b.classList.toggle("selected");
  }});
  document.querySelectorAll("[data-move]").forEach(b=>{b.onclick=()=>{
    const p=+b.dataset.pos,n=p+(+b.dataset.move);
    if(n<0||n>=order.length)return;
    const x=order[p];order[p]=order[n];order[n]=x;
    $("#order").innerHTML=orderHTML(q);bindStudent(q);
  }});
  $("#check").onclick=()=>{
    questionPreview(q).answer_seen=true;
    const right=new Set(q.correct_answer.selection_ids);
    document.querySelectorAll("[data-option]").forEach(b=>{
      if(right.has(b.dataset.option))b.classList.add("correct");
      else if(b.classList.contains("selected"))b.classList.add("wrong");
    });
    $("#feedback").innerHTML=answerFeedbackHTML(q);
    $("#feedback").classList.add("show");
    updateHintPreview(q);
  };
  if(authoredHints(q).length&&$("#hintButton"))$("#hintButton").onclick=()=>showNextHint(q);
  $("#reset").onclick=()=>{resetPreview(q);render()};
  updateHintPreview(q);
}
function answerFeedbackHTML(q){
  return '<strong>Đáp án / lời giải</strong><div class="code">'+esc(answerText(q))+'</div><div>'+esc(q.answer_explanation)+'</div><p class="semantic-note">Đây là bản xem thử, không phải điểm chấm hay evidence về người học.</p>';
}
function reviewHintsHTML(q){
  const hints=authoredHints(q);
  const body=hints.length?'<div class="hint-review-list">'+hints.map((h,i)=>'<div class="hint-card"><strong>Gợi ý '+(i+1)+' · '+esc(hintKindLabel(h.kind))+'</strong><p>'+esc(h.text)+'</p><span class="hint-review-id">'+esc(h.hint_id)+'</span></div>').join('')+'</div>':'<p>'+esc(q.hint_absence_reason||'Bản cũ chưa có phần gợi ý được soạn; không suy diễn hint từ lời giải.')+'</p>';
  return '<section class="review-card wide"><h3>Gợi ý được soạn · tách khỏi đáp án</h3>'+body+'<p class="semantic-note">Số gợi ý tùy câu. Xem gợi ý là có hỗ trợ; không tự cộng/trừ mastery.</p></section>';
}
function semanticIssueHTML(issue){
  const evidence=(issue.locators||[]).map(loc=>'<p><code>'+esc(loc.artifact)+' · '+esc(loc.pointer)+'</code></p>'+(loc.quote?'<blockquote>'+esc(loc.quote)+'</blockquote>':'')).join('');
  return '<div class="semantic-issue"><b>'+esc(issue.stage)+'</b><p>'+esc(issue.observation)+'</p>'+(evidence?'<details><summary>Bằng chứng đối chiếu</summary>'+evidence+'</details>':'')+'</div>';
}
function semanticReviewHTML(q){
  const state=semanticQuestionState(q),base=semanticAudit||{};
  const reviewer=base.reviewer||(base.report&&base.report.reviewer),scope=base.scope||(base.report&&base.report.scope);
  let body='<div class="semantic-summary">'+semanticBadge(state.status)+(reviewer?'<span class="hint-review-id">'+esc(reviewer.mode==='independent'?'Reviewer độc lập':'Tự kiểm tra')+' · '+esc(reviewer.label||reviewer.model||'')+'</span>':'')+'</div>';
  if(state.reason)body+='<p>'+esc(state.reason)+'</p>';
  if(scope&&(scope.limitations||[]).length)body+='<ul>'+scope.limitations.map(s=>'<li>'+esc(s)+'</li>').join('')+'</ul>';
  // Stale checks are not shown as green decisions on a revised question.
  if(state.row&&state.status!=='STALE'&&state.status!=='NOT_REVIEWED'){
    const row=state.row;
    if(row.independent_answer)body+='<details class="semantic-check"><summary>Cách trả lời của reviewer độc lập</summary><div class="code">'+esc(typeof row.independent_answer==='string'?row.independent_answer:JSON.stringify(row.independent_answer,null,2))+'</div></details>';
    const checks=[['grounding','Đúng nguồn'],['answerability','Rõ câu hỏi và đủ dữ kiện'],['alignment','Đo đúng KC / slot'],['scoring','Đáp án và cách chấm'],['cues_and_variants','Đoán mẹo, lặp và biến thể'],['hints','Gợi ý hữu ích, không lộ đáp án']];
    body+=checks.filter(([key])=>row[key]).map(([key,label])=>{
      const restricted=semanticSyncUncertain||!reviewer||reviewer.mode!=='independent'||!scope||scope.source_coverage!=='complete'||(scope.limitations||[]).length||(key==='hints'&&!hasHintDecision(q));
      const check=row[key],status=state.status==='REVIEW'&&check.verdict==='PASS'&&restricted?'REVIEW':check.verdict;
      return '<div class="semantic-check"><div class="semantic-check-head"><strong>'+label+'</strong>'+semanticBadge(status)+'</div><p>'+esc(check.rationale)+'</p>'+(check.issues||[]).map(semanticIssueHTML).join('')+'</div>';
    }).join('');
  }
  body+='<p class="semantic-note">Kiểm định AI ban đầu, không phải phê duyệt của con người; không chứng minh mastery hay chất lượng tuyệt đối.</p>';
  return '<section class="review-card wide"><h3>Kiểm định ngữ nghĩa ban đầu</h3>'+body+'</section>';
}
function syncDecisions(){const d=decisions[questions[index].question_id];document.querySelectorAll(".decision").forEach(function(b){b.classList.toggle("active",b.dataset.decision===d)})}
function render(){
  const q=questions[index];
  if(mode==="review")questionPreview(q).answer_seen=true;
  $("#contextTitle").textContent=q.title;
  $("#kcTag").textContent=q.kc_id;
  $("#typeTag").textContent=q.interaction.replaceAll("_"," ");
  $("#position").textContent="Câu "+(index+1)+" / "+questions.length;
  renderAssessmentBadges(q);
  $("#studentMode").classList.toggle("active",mode==="student");
  $("#reviewMode").classList.toggle("active",mode==="review");
  $("#content").innerHTML=mode==="student"?renderStudent(q):renderReviewer(q);
  if(mode==="student")bindStudent(q);
  renderNav();renderMetrics();syncDecisions();
  history.replaceState(null,"","#"+q.question_id);
}
function select(i){
  const next=(i+questions.length)%questions.length;
  if(next!==index)resetPreview(questions[next]);
  index=next;render();
}
$("#model").textContent=DATA.metadata.model;$("#search").oninput=renderNav;$("#prev").onclick=function(){select(index-1)};$("#next").onclick=function(){select(index+1)};$("#studentMode").onclick=function(){mode="student";render()};$("#reviewMode").onclick=function(){mode="review";render()};$("#collapseNav").onclick=function(){document.body.classList.add("nav-off");$("#expandNav").hidden=false};$("#expandNav").onclick=function(){document.body.classList.remove("nav-off");$("#expandNav").hidden=true};$("#rawButton").onclick=function(){questionPreview(questions[index]).answer_seen=true;$("#raw").textContent=JSON.stringify(baselineQuestions.get(questions[index].question_id),null,2);$("#modal").classList.add("open");if(mode==="student")updateHintPreview(questions[index])};$("#closeModal").onclick=function(){$("#modal").classList.remove("open")};$("#modal").onclick=function(e){if(e.target===$("#modal"))$("#modal").classList.remove("open")};document.querySelectorAll(".decision").forEach(function(b){b.onclick=function(){decisions[questions[index].question_id]=b.dataset.decision;localStorage.setItem(storageKey,JSON.stringify(decisions));syncDecisions()}});
const resizer=$("#resizer");resizer.onpointerdown=function(e){resizer.setPointerCapture(e.pointerId);resizer.classList.add("drag")};resizer.onpointermove=function(e){if(!resizer.hasPointerCapture(e.pointerId))return;document.documentElement.style.setProperty("--nav",Math.max(250,Math.min(520,e.clientX-28))+"px")};resizer.onpointerup=function(e){resizer.releasePointerCapture(e.pointerId);resizer.classList.remove("drag")};addEventListener("keydown",function(e){if(e.target.matches("input,textarea,select"))return;if(e.key==="ArrowLeft")select(index-1);if(e.key==="ArrowRight")select(index+1);if(e.key==="Escape")$("#modal").classList.remove("open")});
renderMetrics();const initial=questions.findIndex(function(q){return q.question_id===location.hash.slice(1)});if(initial>=0)index=initial;render();
</script>
</body>
</html>"""

_TEMPLATE = _TEMPLATE.replace("__QUIZ_STIMULUS_RENDERER__", QUIZ_STIMULUS_RENDERER)
