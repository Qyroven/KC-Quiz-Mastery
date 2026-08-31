"""Build a deterministic, read-only review portal for one source bundle.

The portal is deliberately smaller than the production role apps.  It presents
each verified Extraction independently and the shared KC/Quiz artifacts as one
connected journey.  It never edits a candidate, awards approval, or infers that
two page ordinals from different PDFs refer to the same content.
"""

# The dependency-free review pages are intentionally embedded beside their
# deterministic builder, like the existing Extraction/KC/Quiz review modules.
# ruff: noqa: E501

from __future__ import annotations

import html
import json
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from learning_authoring.artifacts import read_json, sha256_file, write_json, write_text
from learning_authoring.authoring_context import load_bundle_authoring_context
from learning_authoring.contracts import ExtractedSource
from learning_authoring.quiz_contracts import QuizBatch
from learning_authoring.quiz_media import QUIZ_STIMULUS_RENDERER, render_quiz_images
from learning_authoring.quiz_review_state import load_quiz_semantic_state
from learning_authoring.source_bundle import (
    SourceBundle,
    SourceBundleKCSet,
    load_bundle_extractions,
    load_source_bundle,
    validate_kc_set_against_bundle,
)

PORTAL_SCHEMA_VERSION = "source-bundle-review-portal.v1"
PORTAL_MANIFEST = "bundle-portal-manifest.json"


class BundlePortalError(ValueError):
    """A bundle cannot be represented without weakening its lineage."""


def _json_script(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _inside(root: Path, reference: str, *, label: str) -> Path:
    posix = PurePosixPath(reference)
    if (
        posix.is_absolute()
        or not posix.parts
        or ".." in posix.parts
        or "\\" in reference
        or str(posix) != reference
    ):
        raise BundlePortalError(f"{label} must be a normalized relative path")
    path = root / reference
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise BundlePortalError(f"{label} escapes its source run or is a symlink")
    return path


def _source_images(
    root: Path,
    bundle: SourceBundle,
    destination: Path,
) -> dict[str, dict[int, str]]:
    """Copy only source-manifest-bound page images; missing images stay explicit."""

    result: dict[str, dict[int, str]] = {}
    for ordinal, entry in enumerate(bundle.sources, start=1):
        source_key = f"source-{ordinal:03d}"
        run_dir = root / entry.run_ref
        manifest = read_json(root / entry.source_manifest_ref)
        records = manifest.get("page_records", [])
        if not isinstance(records, list):
            raise BundlePortalError(
                f"source manifest page_records is not a list: {entry.source.source_id}"
            )
        page_map: dict[int, str] = {}
        for record in records:
            if not isinstance(record, dict):
                raise BundlePortalError("source manifest contains an invalid page record")
            page = record.get("page")
            reference = record.get("image_ref")
            digest = record.get("image_sha256")
            if (
                isinstance(page, bool)
                or not isinstance(page, int)
                or not 1 <= page <= entry.source.page_count
                or not isinstance(reference, str)
                or not isinstance(digest, str)
                or len(digest) != 64
            ):
                raise BundlePortalError(
                    f"source manifest contains an invalid page image record: "
                    f"{entry.source.source_id}"
                )
            if page in page_map:
                raise BundlePortalError(
                    f"source manifest contains duplicate page image records: "
                    f"{entry.source.source_id} page {page}"
                )
            source = _inside(run_dir, reference, label="page image_ref")
            if not source.is_file() or sha256_file(source) != digest:
                raise BundlePortalError(
                    f"source page image changed: {entry.source.source_id} page {page}"
                )
            suffix = source.suffix.lower() if source.suffix else ".bin"
            relative = f"assets/{source_key}/page-{page:04d}{suffix}"
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            page_map[page] = f"../../{relative}"
        result[entry.source.source_id] = page_map
    return result


def _load_quiz(
    root: Path,
    quiz_dir: Path,
    bundle: SourceBundle,
    kc_raw: dict[str, Any],
    kc_set: SourceBundleKCSet,
    kc_sha256: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    names = {
        "candidate": quiz_dir / "quiz-proposed.json",
        "input": quiz_dir / "quiz-input.json",
        "metadata": quiz_dir / "quiz-generation-metadata.json",
    }
    present = {name: path.is_file() for name, path in names.items()}
    if not any(present.values()):
        return None, {
            "code": "NOT_GENERATED",
            "label": "Not generated",
            "human_approved": False,
        }
    if not all(present.values()):
        missing = sorted(name for name, exists in present.items() if not exists)
        raise BundlePortalError(f"incomplete Quiz artifact set: {missing}")
    if any(path.is_symlink() or not path.resolve().is_relative_to(root) for path in names.values()):
        raise BundlePortalError("Quiz artifacts must be regular files inside the bundle root")

    raw = read_json(names["candidate"])
    quiz_input = read_json(names["input"])
    metadata = read_json(names["metadata"])
    batch = QuizBatch.model_validate(raw)
    batch.validate_against_input(quiz_input)
    source_ref = batch.source_ref
    if source_ref.source_bundle_sha256 != bundle.bundle_sha256:
        raise BundlePortalError("Quiz source-bundle lineage is stale")
    if source_ref.kc_set_sha256 != kc_sha256:
        raise BundlePortalError("Quiz KC lineage is stale")
    if source_ref.authoring_context_sha256 != kc_set.source_ref.authoring_context_sha256:
        raise BundlePortalError("Quiz authoring-context lineage is stale")

    selected = quiz_input.get("runtime", {}).get("selected_kc_ids")
    if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)):
        raise BundlePortalError("Quiz input has invalid selected KC identities")
    current_kcs = {kc["kc_id"]: kc for kc in kc_raw["leaf_kcs"]}
    supplied_kcs = quiz_input.get("leaf_kcs")
    if not isinstance(supplied_kcs, list) or supplied_kcs != [
        current_kcs[kc_id] for kc_id in selected if kc_id in current_kcs
    ]:
        raise BundlePortalError("Quiz input differs from the current selected KC content")
    if any(kc_id not in current_kcs for kc_id in selected):
        raise BundlePortalError("Quiz input selects a KC outside the shared KC set")
    selected_groups = {current_kcs[kc_id]["group_id"] for kc_id in selected}
    expected_groups = [
        group for group in kc_raw["kc_groups"] if group["group_id"] in selected_groups
    ]
    if quiz_input.get("kc_groups") != expected_groups:
        raise BundlePortalError("Quiz input differs from the current KC groups")

    candidate_sha = sha256_file(names["candidate"])
    metadata_kc = metadata.get("kc_set")
    if (
        metadata.get("stage") != "quiz"
        or metadata.get("candidate_raw_sha256") != candidate_sha
        or not isinstance(metadata_kc, Mapping)
        or metadata_kc.get("sha256") != kc_sha256
        or metadata.get("selected_kc_ids") != selected
    ):
        raise BundlePortalError("Quiz metadata does not bind the current candidate and KCs")
    approval = metadata.get("approval_status")
    quality = metadata.get("quality_status")
    if approval != "EXPERIMENTAL_UNAPPROVED" or quality != "experimental_unapproved":
        raise BundlePortalError("unsupported or ambiguous Quiz review status")
    return raw, {
        "code": approval,
        "label": "Experimental · human review required",
        "human_approved": False,
        "selected_kc_count": len(selected),
        "question_count": len(batch.questions),
        "assessment_slot_count": len(batch.assessment_slots),
        "candidate_sha256": candidate_sha,
    }


def _base_style() -> str:
    return """
:root{color-scheme:light;--bg:#f4f7fb;--panel:#fff;--ink:#172033;--muted:#697386;--line:#dce3ec;--blue:#1769c2;--bluefill:#eaf3ff;--green:#208548;--amber:#a7640d;--red:#b53b3b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}button,select{font:inherit}a{color:var(--blue)}.top{height:64px;display:flex;align-items:center;gap:16px;padding:0 24px;background:#fff;border-bottom:1px solid var(--line)}.brand{font-size:17px;font-weight:750}.sub{color:var(--muted);font-size:12px}.spacer{flex:1}.pill{display:inline-flex;align-items:center;padding:4px 9px;border-radius:999px;background:#edf1f5;color:#596273;font-size:11px;font-weight:700}.pill.proposed,.pill.review{background:#fff3df;color:var(--amber)}.pill.approved{background:#eaf7ee;color:var(--green)}.wrap{max-width:1320px;margin:0 auto;padding:22px}.card{background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:0 10px 28px #1720330b}.muted{color:var(--muted)}.empty{padding:36px;text-align:center;color:var(--muted)}
"""


def _initial_check_projection(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return a publishable, fail-closed summary without review material or paths."""

    status = state.get("status")
    if status not in {"PASS", "REVIEW", "REJECT", "STALE", "NOT_REVIEWED"}:
        status = "STALE"
    raw_counts = state.get("counts")
    counts = {
        key: value
        for key, value in (raw_counts.items() if isinstance(raw_counts, Mapping) else ())
        if key in {"PASS", "REVIEW", "REJECT", "STALE", "NOT_REVIEWED"}
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }
    raw_reasons = state.get("reasons")
    reasons = (
        [value for value in raw_reasons if isinstance(value, str)]
        if isinstance(raw_reasons, list)
        else []
    )
    if not reasons and isinstance(state.get("reason"), str):
        reasons = [state["reason"]]
    reviewer = state.get("reviewer")
    scope = state.get("scope")
    return {
        "status": status,
        "counts": counts,
        "reasons": reasons,
        "reviewer_mode": reviewer.get("mode") if isinstance(reviewer, Mapping) else None,
        "source_coverage": (scope.get("source_coverage") if isinstance(scope, Mapping) else None),
        "initial_check_only": True,
        "human_approved": False,
    }


def _index_html(manifest: dict[str, Any]) -> str:
    title = "Multi-source authoring review"
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>{_base_style()}
body{{height:100vh;display:grid;grid-template-rows:auto auto 1fr;overflow:hidden}}.journey{{display:flex;align-items:center;gap:8px;padding:12px 24px;background:#fff;border-bottom:1px solid var(--line)}}.journey button{{border:1px solid var(--line);background:#fff;border-radius:10px;padding:8px 13px;color:var(--muted);font-weight:700}}.journey button.active{{color:var(--blue);border-color:#8db8e8;background:var(--bluefill)}}.journey .arrow{{color:#a5afbc}}select{{min-width:260px;border:1px solid var(--line);border-radius:9px;background:#fff;padding:8px 10px}}iframe{{width:100%;height:100%;border:0;background:var(--bg)}}
</style></head><body><header class="top"><div><div class="brand">Learning Authoring</div><div class="sub">Verified source bundle · read-only review</div></div><div class="spacer"></div><label class="sub" for="source">Source</label><select id="source"></select></header><nav class="journey"><button data-stage="extraction">Extraction</button><span class="arrow">→</span><button data-stage="kc">Knowledge Components</button><span class="arrow">→</span><button data-stage="quiz">Quiz</button><span class="spacer"></span><span id="status" class="pill"></span></nav><iframe id="view" title="Review stage"></iframe><script id="portal" type="application/json">{_json_script(manifest)}</script><script>
const P=JSON.parse(document.getElementById('portal').textContent),select=document.getElementById('source'),frame=document.getElementById('view'),status=document.getElementById('status');
for(const s of P.sources){{const o=document.createElement('option');o.value=s.key;o.textContent=`${{s.filename}} · ${{s.page_count}} pages`;select.appendChild(o)}}
const params=new URLSearchParams(location.search);let stage=params.get('stage')||'extraction';const requested=params.get('source');if(P.sources.some(s=>s.key===requested))select.value=requested;
function currentSource(){{return P.sources.find(s=>s.key===select.value)||P.sources[0]}}function render(){{document.querySelectorAll('[data-stage]').forEach(b=>b.classList.toggle('active',b.dataset.stage===stage));select.disabled=stage!=='extraction';let href,label;if(stage==='kc'){{href=P.entrypoints.kc;label=P.statuses.kc.label}}else if(stage==='quiz'){{href=P.entrypoints.quiz;label=P.statuses.quiz.label}}else{{const s=currentSource();href=s.view+(params.get('page')?'#'+params.get('page'):'');label=s.extraction_status}}frame.src=href;status.textContent=label;status.className='pill '+(label.includes('APPROVED')?'approved':label.includes('REVIEW')||label.includes('PROPOSED')||label.includes('Experimental')?'review':'')}}
document.querySelectorAll('[data-stage]').forEach(b=>b.onclick=()=>{{stage=b.dataset.stage;render()}});select.onchange=render;render();
</script></body></html>"""


def _source_html(
    entry: dict[str, Any],
    extracted: ExtractedSource,
    images: dict[int, str],
) -> str:
    data = {
        "source": extracted.model_dump(mode="json"),
        "images": {str(page): path for page, path in images.items()},
        "status": entry["extraction_status"],
    }
    filename = html.escape(extracted.source.filename)
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Extraction · {filename}</title><style>{_base_style()}
body{{height:100vh;display:grid;grid-template-rows:auto 1fr;overflow:hidden}}.layout{{display:grid;grid-template-columns:270px minmax(0,1fr) 390px;min-height:0}}aside,main{{min-height:0;overflow:auto}}aside{{background:#fff;border-right:1px solid var(--line)}}aside.right{{border-right:0;border-left:1px solid var(--line);padding:16px}}.tools{{padding:12px;border-bottom:1px solid var(--line)}}input{{width:100%;padding:9px;border:1px solid var(--line);border-radius:8px}}.pages{{padding:8px}}.page{{width:100%;display:grid;grid-template-columns:34px 1fr;gap:7px;text-align:left;border:1px solid transparent;background:#fff;border-radius:9px;padding:9px;margin:2px 0}}.page.active{{border-color:#8db8e8;background:var(--bluefill)}}.page small{{display:block;color:var(--muted)}}main{{padding:22px;text-align:center}}main img{{max-width:100%;background:#fff;box-shadow:0 12px 36px #17203324}}.missing{{margin:60px auto;max-width:480px;padding:30px;background:#fff;border:1px dashed var(--line);border-radius:12px;color:var(--muted)}}pre{{white-space:pre-wrap;word-break:break-word;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;text-align:left}}h2{{margin:0 0 4px;font-size:18px}}@media(max-width:900px){{.layout{{grid-template-columns:220px 1fr}}aside.right{{display:none}}}}
</style></head><body><header class="top"><div><div class="brand">Extraction · {filename}</div><div class="sub">Independent source · page identities are local to this PDF</div></div><span class="spacer"></span><span class="pill {"approved" if entry["extraction_status"] == "HUMAN_APPROVED" else "proposed"}">{html.escape(entry["extraction_status"])}</span></header><section class="layout"><aside><div class="tools"><input id="search" placeholder="Search pages or blocks"></div><div id="pages" class="pages"></div></aside><main id="visual"></main><aside class="right"><h2 id="title"></h2><div id="summary" class="muted"></div><pre id="raw"></pre></aside></section><script id="data" type="application/json">{_json_script(data)}</script><script>
const D=JSON.parse(document.getElementById('data').textContent),pages=D.source.pages,list=document.getElementById('pages'),visual=document.getElementById('visual'),raw=document.getElementById('raw'),title=document.getElementById('title'),summary=document.getElementById('summary');let selected=Math.max(0,Math.min(pages.length-1,Number(location.hash.slice(1)||1)-1));
function text(p){{return JSON.stringify(p).toLowerCase()}}function renderList(){{const q=document.getElementById('search').value.trim().toLowerCase();list.replaceChildren();pages.forEach((p,i)=>{{if(q&&!text(p).includes(q))return;const b=document.createElement('button');b.className='page '+(i===selected?'active':'');b.innerHTML=`<b>${{p.page_number}}</b><span>${{escapeHtml(p.role)}}<small>${{p.blocks.length}} blocks</small></span>`;b.onclick=()=>{{selected=i;location.hash=p.page_number;render()}};list.appendChild(b)}})}}
function escapeHtml(v){{const n=document.createElement('span');n.textContent=String(v);return n.innerHTML}}function render(){{const p=pages[selected],src=D.images[String(p.page_number)];visual.replaceChildren();if(src){{const img=document.createElement('img');img.src=src;img.alt=`Source page ${{p.page_number}}`;visual.appendChild(img)}}else{{const x=document.createElement('div');x.className='missing';x.textContent='No manifest-bound page image is available. The verified Extraction JSON remains visible.';visual.appendChild(x)}}title.textContent=`Page ${{p.page_number}} · ${{p.role}}`;summary.textContent=p.page_note.summary;raw.textContent=JSON.stringify(p,null,2);renderList()}}document.getElementById('search').oninput=renderList;addEventListener('hashchange',()=>{{const n=Number(location.hash.slice(1));const i=pages.findIndex(p=>p.page_number===n);if(i>=0)selected=i;render()}});render();
</script></body></html>"""


def _kc_html(kc_set: SourceBundleKCSet, source_keys: dict[str, str]) -> str:
    data = {
        "kc": kc_set.model_dump(mode="json"),
        "source_keys": source_keys,
    }
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Shared Knowledge Components</title><style>{_base_style()}
.summary{{display:flex;gap:10px;flex-wrap:wrap}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px;margin-top:16px}}.kc{{padding:17px}}.kc h2{{font-size:17px;margin:3px 0 7px}}.group{{color:var(--blue);font-size:11px;font-weight:700}}.claim{{padding:10px;border-left:3px solid #7da8d6;background:#f3f8fe;margin:10px 0}}.evidence{{border-top:1px solid var(--line);padding-top:9px;margin-top:9px}}.evidence a{{margin-right:8px}}ul{{padding-left:18px}}
</style></head><body><header class="top"><div><div class="brand">Shared Knowledge Components</div><div class="sub">Merged only with source-qualified evidence · read-only proposed output</div></div><span class="spacer"></span><span class="pill proposed">PROPOSED · REVIEW REQUIRED</span></header><main class="wrap"><div id="summary" class="summary"></div><div id="grid" class="grid"></div></main><script id="data" type="application/json">{_json_script(data)}</script><script>
const D=JSON.parse(document.getElementById('data').textContent),K=D.kc,groups=new Map(K.kc_groups.map(g=>[g.group_id,g]));function node(tag,cls,text){{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}}const summary=document.getElementById('summary');[['Leaf KC',K.leaf_kcs.length],['KC groups',K.kc_groups.length],['Source pages audited',K.page_audit.length],['Uncovered items',K.uncovered_content.length]].forEach(([a,b])=>{{const p=node('span','pill',`${{b}} ${{a}}`);summary.appendChild(p)}});const grid=document.getElementById('grid');for(const kc of K.leaf_kcs){{const c=node('article','card kc'),g=groups.get(kc.group_id);c.append(node('div','group',`${{kc.kc_id}} · ${{g?g.name:kc.group_id}}`));c.append(node('h2','',kc.name));c.append(node('p','muted',kc.knowledge_description));c.append(node('div','claim',kc.observable_claim));const ev=node('div','evidence');ev.append(node('b','',`Evidence (${{kc.source_evidence.length}})`));for(const e of kc.source_evidence){{const row=node('p','');const key=D.source_keys[e.source_id],a=node('a','',`${{e.source_id}} · page ${{e.page}}`);a.href=`index.html?stage=extraction&source=${{encodeURIComponent(key)}}&page=${{e.page}}`;a.target='_top';row.append(a,node('span','muted',e.description));ev.append(row)}}if(kc.context_evidence.length)ev.append(node('p','muted',`${{kc.context_evidence.length}} lecturer-context citation(s), kept separate from Extraction.`));c.append(ev);grid.append(c)}}
</script></body></html>"""


def _quiz_html(
    quiz: dict[str, Any] | None,
    status: dict[str, Any],
    initial_check: dict[str, Any],
    stimulus_images: list[dict[str, Any]] | None = None,
) -> str:
    if quiz is None:
        body = '<div class="card empty">No Quiz candidate is connected to this bundle yet.</div>'
        data = "null"
    else:
        body = '<div class="layout"><aside id="list" class="card list"></aside><main id="question" class="card question"></main></div>'
        data = _json_script(quiz)
    check_status = html.escape(initial_check["status"])
    check_counts = (
        ", ".join(
            f"{html.escape(key)} {value}" for key, value in initial_check["counts"].items() if value
        )
        or "no item verdicts"
    )
    check_reasons = " ".join(html.escape(reason) for reason in initial_check["reasons"])
    check_panel = (
        '<section class="card check">'
        f"<b>Initial semantic check · {check_status}</b>"
        f'<span class="muted">{html.escape(check_counts)}</span>'
        + (f'<p class="muted">{check_reasons}</p>' if check_reasons else "")
        + '<p class="muted">This check never creates human approval and never rewrites the Quiz.</p>'
        "</section>"
    )
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Shared Quiz review</title><style>{_base_style()}
.check{{padding:12px 14px;margin-bottom:16px;display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}}.check p{{width:100%;margin:0}}.layout{{display:grid;grid-template-columns:300px minmax(0,1fr);gap:16px}}.list,.question{{padding:14px}}.list button{{width:100%;border:0;background:#fff;text-align:left;padding:10px;border-radius:8px}}.list button.active{{background:var(--bluefill);color:var(--blue)}}.question h1{{font-size:24px;margin:5px 0 14px}}.prompt{{font-size:18px;font-weight:650}}.panel{{padding:12px;background:#f7f9fc;border:1px solid var(--line);border-radius:10px;margin-top:12px}}.answer{{border-left:3px solid #4d88c6}}.hint{{border-left:3px solid #d29a46}}pre{{white-space:pre-wrap;word-break:break-word}}@media(max-width:800px){{.layout{{grid-template-columns:1fr}}}}
</style></head><body><header class="top"><div><div class="brand">Shared Quiz review</div><div class="sub">Generated candidate is unchanged; status is not human approval</div></div><span class="spacer"></span><span class="pill review">{html.escape(status["label"])}</span></header><main class="wrap">{check_panel}{body}</main><script id="data" type="application/json">{data}</script><script>
{QUIZ_STIMULUS_RENDERER}
const Q=JSON.parse(document.getElementById('data').textContent),images={_json_script(stimulus_images or [])};
if(Q){{
  const list=document.getElementById('list'),box=document.getElementById('question');let selected=0;
  function n(tag,cls,text){{const x=document.createElement(tag);if(cls)x.className=cls;if(text!==undefined)x.textContent=text;return x}}
  function render(){{
    list.replaceChildren();Q.questions.forEach((q,i)=>{{const b=n('button',i===selected?'active':'',`${{q.question_id}} · ${{q.title}}`);b.onclick=()=>{{selected=i;render()}};list.append(b)}});
    const q=Q.questions[selected];box.replaceChildren();
    box.append(n('div','muted',`${{q.kc_id}} · ${{q.interaction}} · variant ${{q.variant_index}}`),n('h1','',q.title));
    const stimulus=n('div','panel');stimulus.innerHTML=renderQuizStimulus(q.stimulus,images);if(stimulus.innerHTML)box.append(stimulus);
    box.append(n('p','prompt',q.prompt));
    for(const [label,options] of [['Choices',q.choice_options],['Match',q.matching_left],['With',q.matching_right],['Order',q.ordering_options]]){{
      if(options&&options.length){{const ul=n('ul','');options.forEach(o=>ul.append(n('li','',o.text)));box.append(n('b','',label),ul)}}
    }}
    const answer=n('details','panel answer');answer.append(n('summary','','Answer / rubric'),n('pre','',JSON.stringify({{correct_answer:q.correct_answer,rubric:q.rubric,explanation:q.answer_explanation}},null,2)));box.append(answer);
    const hint=n('details','panel hint');hint.append(n('summary','','Hints'),n('pre','',q.hints&&q.hints.length?JSON.stringify(q.hints,null,2):(q.hint_absence_reason||'No explicit hint decision.')));box.append(hint);
  }}
  render();
}}
</script></body></html>"""


def _inventory(root: Path) -> list[dict[str, str]]:
    return [
        {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != PORTAL_MANIFEST
    ]


def build_bundle_portal(
    bundle_root: Path,
    output_dir: Path,
    *,
    kc_path: Path | None = None,
    quiz_dir: Path | None = None,
) -> dict[str, Any]:
    """Create a fresh static portal from exact, verified bundle artifacts.

    The destination must not exist.  No deploy is performed and no source or
    candidate file is modified.
    """

    root = bundle_root.expanduser().resolve()
    requested_destination = output_dir.expanduser()
    if requested_destination.is_symlink():
        raise BundlePortalError("bundle portal output must be a fresh path")
    destination = requested_destination.resolve()
    if destination.exists():
        raise BundlePortalError("bundle portal output must be a fresh path")
    destination.parent.mkdir(parents=True, exist_ok=True)

    bundle = load_source_bundle(root)
    extractions = load_bundle_extractions(root, bundle)
    context = load_bundle_authoring_context(root, bundle)
    requested_kc = (kc_path or root / "kc-proposed.json").expanduser()
    if requested_kc.is_symlink():
        raise BundlePortalError("shared kc-proposed.json must not be a symlink")
    resolved_kc = requested_kc.resolve()
    if (
        not resolved_kc.is_file()
        or resolved_kc.is_symlink()
        or not resolved_kc.is_relative_to(root)
    ):
        raise BundlePortalError("shared kc-proposed.json must be inside the bundle root")
    kc_raw = read_json(resolved_kc)
    parsed = validate_kc_set_against_bundle(
        kc_raw,
        bundle,
        extractions,
        authoring_context=context,
    )
    if not isinstance(parsed, SourceBundleKCSet):
        raise BundlePortalError("multi-source portal requires source-qualified shared KCs")
    kc_sha = sha256_file(resolved_kc)

    requested_quiz = (quiz_dir or root / "quiz").expanduser()
    if requested_quiz.is_symlink():
        raise BundlePortalError("Quiz directory must not be a symlink")
    resolved_quiz = requested_quiz.resolve()
    if not resolved_quiz.is_relative_to(root):
        raise BundlePortalError("Quiz directory must remain inside the bundle root")
    quiz, quiz_status = _load_quiz(root, resolved_quiz, bundle, kc_raw, parsed, kc_sha)
    stimulus_images = (
        render_quiz_images(root, read_json(resolved_quiz / "quiz-input.json"),
                           QuizBatch.model_validate(quiz))
        if quiz is not None else []
    )
    initial_check = _initial_check_projection(
        load_quiz_semantic_state(root, candidate_dir=resolved_quiz)
    )

    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        images = _source_images(root, bundle, temporary)
        sources = []
        source_keys: dict[str, str] = {}
        for ordinal, entry in enumerate(bundle.sources, start=1):
            key = f"source-{ordinal:03d}"
            source_keys[entry.source.source_id] = key
            view = f"sources/{key}/index.html"
            record = {
                "key": key,
                "source_id": entry.source.source_id,
                "filename": entry.source.filename,
                "page_count": entry.source.page_count,
                "extraction_status": entry.extraction_status,
                "extraction_sha256": entry.extraction_sha256,
                "manifest_bound_image_count": len(images[entry.source.source_id]),
                "view": view,
            }
            write_text(
                temporary / view,
                _source_html(
                    record,
                    extractions[entry.source.source_id],
                    images[entry.source.source_id],
                ),
            )
            sources.append(record)

        statuses = {
            "extraction": {
                "label": (
                    "HUMAN_APPROVED"
                    if all(s["extraction_status"] == "HUMAN_APPROVED" for s in sources)
                    else "PROPOSED · REVIEW REQUIRED"
                ),
                "human_approved": all(s["extraction_status"] == "HUMAN_APPROVED" for s in sources),
            },
            "kc": {
                "label": "PROPOSED · REVIEW REQUIRED",
                "human_approved": False,
            },
            "quiz": quiz_status,
        }
        manifest: dict[str, Any] = {
            "schema_version": PORTAL_SCHEMA_VERSION,
            "source_bundle_sha256": bundle.bundle_sha256,
            "kc_set_sha256": kc_sha,
            "authoring_context_sha256": context.sha256 if context else None,
            "quiz_candidate_sha256": quiz_status.get("candidate_sha256"),
            "sources": sources,
            "counts": {
                "sources": len(sources),
                "source_pages": sum(source["page_count"] for source in sources),
                "kc_groups": len(parsed.kc_groups),
                "leaf_kcs": len(parsed.leaf_kcs),
                "assessment_slots": quiz_status.get("assessment_slot_count", 0),
                "quiz_questions": quiz_status.get("question_count", 0),
            },
            "statuses": statuses,
            "quiz_initial_check": initial_check,
            "entrypoints": {
                "portal": "index.html",
                "kc": "kc.html",
                "quiz": "quiz.html",
            },
            "candidate_content_modified": False,
            "deployment_performed": False,
        }
        write_text(temporary / "kc.html", _kc_html(parsed, source_keys))
        write_text(
            temporary / "quiz.html",
            _quiz_html(quiz, quiz_status, initial_check, stimulus_images),
        )
        write_text(temporary / "index.html", _index_html(manifest))
        manifest["files"] = _inventory(temporary)
        write_json(temporary / PORTAL_MANIFEST, manifest)
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return read_json(destination / PORTAL_MANIFEST)
