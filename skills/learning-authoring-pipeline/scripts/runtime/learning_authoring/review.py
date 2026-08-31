"""Generate a portable, read-only extraction review UI."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from learning_authoring.artifacts import RunArtifacts, read_json
from learning_authoring.contracts import ExtractedSource


def _json_for_script(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def _optional_json(path: Path) -> dict[str, Any]:
    return read_json(path) if path.is_file() else {}


def _warning_scope(extracted: ExtractedSource) -> dict[str, Any]:
    """Resolve document warnings to pages without assuming block-id formats."""

    known_pages = {page.page_number for page in extracted.pages}
    block_pages = {
        block.block_id: page.page_number for page in extracted.pages for block in page.blocks
    }
    by_page: dict[str, list[dict[str, Any]]] = {
        str(page_number): [] for page_number in sorted(known_pages)
    }
    document: list[dict[str, Any]] = []

    for warning in extracted.warnings:
        targets: set[int] = set()
        if warning.page in known_pages:
            targets.add(warning.page)
        detail_pages = warning.details.get("pages")
        if isinstance(detail_pages, list):
            targets.update(
                page_number
                for page_number in detail_pages
                if isinstance(page_number, int) and page_number in known_pages
            )
        targets.update(
            block_pages[block_id] for block_id in warning.block_ids if block_id in block_pages
        )

        row = warning.model_dump(mode="json")
        if targets:
            for page_number in sorted(targets):
                by_page[str(page_number)].append(row)
        else:
            document.append(row)

    return {
        "by_page": by_page,
        "document": document,
        "record_count": len(extracted.warnings)
        + sum(len(page.warnings) for page in extracted.pages),
    }


def build_review(run_dir: Path) -> Path:
    """Build a dependency-free HTML reviewer beside the extraction artifacts."""

    artifacts = RunArtifacts(run_dir.expanduser().resolve())
    raw_extraction = read_json(artifacts.proposed)
    extracted = ExtractedSource.model_validate(raw_extraction)
    document_title = html.escape(extracted.source.filename)
    replacements = {
        "__DOCUMENT_TITLE__": document_title,
        "__NEXT_STAGE_ACTION__": (
            '<a class="stage-link" id="next-stage" href="kc-recall.html#1">KC Review →</a>'
            if (artifacts.run_dir / "kc-recall.html").is_file()
            else ""
        ),
        # Validate navigation/identity, but display the delivered fields unchanged.
        # Dumping the model here inserts defaults that were never authored.
        "__SOURCE_JSON__": _json_for_script(raw_extraction),
        "__AUDIT_JSON__": _json_for_script(_optional_json(artifacts.audit)),
        "__METRICS_JSON__": _json_for_script(_optional_json(artifacts.metrics)),
        "__MANIFEST_JSON__": _json_for_script(_optional_json(artifacts.source_manifest)),
        "__WARNING_SCOPE_JSON__": _json_for_script(_warning_scope(extracted)),
    }
    output = _REVIEW_TEMPLATE
    for marker, value in replacements.items():
        output = output.replace(marker, value)
    artifacts.review_html.write_text(output, encoding="utf-8")
    return artifacts.review_html


_REVIEW_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Extraction review — __DOCUMENT_TITLE__</title>
<style>
:root{--ink:#182033;--muted:#697386;--line:#dfe4eb;--paper:#fff;--bg:#f4f7fb;--accent:#2864a5;--left-width:280px;--right-width:390px}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,sans-serif;overflow:hidden}
button,input{font:inherit}button{border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:8px;padding:7px 10px;cursor:pointer}button:hover{border-color:#88a3c1;background:#f8fbff}button:disabled{opacity:.4;cursor:not-allowed}.icon-button{padding:6px 9px}.primary{background:#eef5fd;border-color:#7393b8;color:#315d8d}.stage-link{display:inline-flex;align-items:center;padding:7px 12px;border-radius:8px;background:var(--accent);color:#fff;text-decoration:none;font-weight:650}.stage-link:hover{background:#1f558f}.copy-icon{display:grid;place-items:center;width:32px;height:32px;padding:0;flex:0 0 auto}.copy-icon svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8}
.app-header{height:66px;display:flex;align-items:center;gap:16px;padding:8px 14px;background:var(--paper);border-bottom:1px solid var(--line);white-space:nowrap}.brand{min-width:0}.brand strong{display:block;font-size:16px}.brand span{display:block;max-width:260px;overflow:hidden;text-overflow:ellipsis;color:var(--muted);font-size:12px}.stats{display:flex;gap:6px;min-width:0;overflow:hidden}.stat{padding:5px 8px;border-radius:7px;background:#f3f4f6;color:#515968;font-size:12px}.header-actions{display:flex;gap:6px;margin-left:auto}
.layout{display:grid;grid-template-columns:var(--left-width) 8px minmax(360px,1fr) 8px var(--right-width);height:calc(100vh - 66px);min-width:0}.layout.left-collapsed{grid-template-columns:0 0 minmax(360px,1fr) 8px var(--right-width)}.layout.right-collapsed{grid-template-columns:var(--left-width) 8px minmax(360px,1fr) 0 0}.layout.left-collapsed.right-collapsed{grid-template-columns:0 0 minmax(360px,1fr) 0 0}.layout.left-collapsed #left,.layout.left-collapsed #left-handle,.layout.right-collapsed #right,.layout.right-collapsed #right-handle{display:none}
.panel{background:var(--paper);min-width:0;overflow:auto}.left-panel{border-right:1px solid var(--line)}.right-panel{border-left:1px solid var(--line)}.resize-handle{position:relative;background:#edf1f6;cursor:col-resize;touch-action:none}.resize-handle::after{content:"";position:absolute;top:45%;left:3px;width:2px;height:42px;border-radius:2px;background:#9aa4b2}.resize-handle:hover,.resize-handle.dragging{background:#dce8f5}.resize-handle:hover::after,.resize-handle.dragging::after{background:var(--accent)}body.dragging{cursor:col-resize;user-select:none}
.panel-header{position:sticky;top:0;z-index:3;background:#fff;border-bottom:1px solid var(--line);padding:10px}.panel-title-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}.panel-title-row strong{flex:1}.search{width:100%;border:1px solid var(--line);border-radius:8px;padding:9px 10px;outline:none}.search:focus{border-color:#638bb7;box-shadow:0 0 0 3px #2864a51a}.search-meta{margin-top:6px;color:var(--muted);font-size:12px}
.page-list{padding:7px}.page-button{display:grid;grid-template-columns:38px 1fr auto;align-items:center;gap:8px;width:100%;text-align:left;margin:4px 0;padding:8px}.page-button.active{background:#eef3f9;border-color:#7393b8}.page-number{font-weight:700;font-variant-numeric:tabular-nums}.page-role{min-width:0;overflow:hidden;text-overflow:ellipsis}.page-role small{display:block;color:var(--muted)}.page-signal{font-size:11px;color:var(--muted)}.page-signal.warning{color:#bd6b13}
.center{display:grid;grid-template-rows:auto minmax(0,1fr);min-width:0;background:#edf1f6}.center-toolbar{display:flex;align-items:center;justify-content:center;gap:10px;padding:9px;background:#fff;border-bottom:1px solid var(--line)}.center-toolbar .page-position{min-width:120px;text-align:center}.zoom-tools{display:flex;gap:5px;margin-left:10px;align-items:center}.zoom-label{min-width:44px;text-align:center;color:var(--muted);font-size:12px}.image-scroll{overflow:auto;padding:20px;text-align:center}.image-scroll img{display:inline-block;width:100%;height:auto;vertical-align:top;background:#fff;box-shadow:0 10px 35px #18203328;transform-origin:top center}
.details{padding:14px}.right-sticky{position:sticky;top:0;z-index:3;background:#fff;border-bottom:1px solid var(--line);padding:10px 12px;display:flex;align-items:center;gap:8px}.right-sticky h2{flex:1;font-size:15px;margin:0}.section-label{font-size:11px;font-weight:750;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:18px 0 7px}.card{border:1px solid var(--line);border-radius:10px;padding:10px;margin:8px 0;background:#fff}.muted{color:var(--muted)}.warning-card{border-color:#e5b974;background:#fff8ed}.page-json{white-space:pre-wrap;word-break:break-word;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0}
.toast{position:fixed;right:18px;bottom:18px;z-index:20;padding:9px 13px;border-radius:9px;background:#182033;color:#fff;box-shadow:0 8px 28px #0003;opacity:0;transform:translateY(8px);pointer-events:none;transition:.18s}.toast.show{opacity:1;transform:none}.empty{padding:20px;color:var(--muted);text-align:center}
@media(max-width:900px){body{overflow:auto}.app-header{height:auto;min-height:66px;flex-wrap:wrap}.stats{order:3;width:100%}.layout,.layout.left-collapsed,.layout.right-collapsed,.layout.left-collapsed.right-collapsed{display:block;height:auto}.resize-handle{display:none}.panel{border:0;border-bottom:1px solid var(--line)}.left-panel{max-height:38vh}.center{height:70vh}.right-panel{min-height:360px}.layout.left-collapsed #left,.layout.right-collapsed #right{display:none}.brand span{max-width:180px}.center-toolbar{position:sticky;top:0;z-index:2;flex-wrap:wrap}.zoom-tools{margin-left:0}}
</style>
</head>
<body>
<header class="app-header">
  <div class="brand"><strong>Extraction review</strong><span>__DOCUMENT_TITLE__</span></div>
  <div class="stats" id="stats"></div>
  <div class="header-actions"><button id="left-toggle" aria-label="Toggle pages panel">☰ Pages</button><button id="right-toggle" aria-label="Toggle details panel">Details ☷</button>__NEXT_STAGE_ACTION__</div>
</header>
<main class="layout" id="layout">
  <aside class="panel left-panel" id="left">
    <div class="panel-header">
      <div class="panel-title-row"><strong>Source pages</strong><button class="icon-button" id="left-close" title="Close pages panel">←</button></div>
      <input class="search" id="search" type="search" placeholder="Search page, role or block…" autocomplete="off">
      <div class="search-meta" id="search-meta"></div>
    </div>
    <div class="page-list" id="page-list"></div>
  </aside>
  <div class="resize-handle" id="left-handle" title="Drag to resize · double-click to reset"></div>
  <section class="center">
    <div class="center-toolbar">
      <button id="prev">← Previous</button><strong class="page-position" id="page-position"></strong><button id="next">Next →</button>
      <div class="zoom-tools"><button class="icon-button" id="zoom-out" title="Zoom out">−</button><span class="zoom-label" id="zoom-label">100%</span><button class="icon-button" id="zoom-in" title="Zoom in">+</button><button id="zoom-reset">Fit</button></div>
    </div>
    <div class="image-scroll" id="image-scroll"><img id="page-image" alt="Rendered source page"></div>
  </section>
  <div class="resize-handle" id="right-handle" title="Drag to resize · double-click to reset"></div>
  <aside class="panel right-panel" id="right">
    <div class="right-sticky"><h2 id="page-title"></h2><button class="copy-icon" id="copy-page" aria-label="Copy page output JSON" title="Copy page output JSON"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="11" height="11" rx="2"></rect><rect x="4" y="8" width="11" height="11" rx="2"></rect></svg></button><button class="icon-button" id="right-close" title="Close details panel">→</button></div>
    <div class="details"><div class="section-label">Page output JSON</div><pre class="page-json" id="page-json"></pre><div class="section-label">Page warnings</div><div id="warnings"></div><div id="document-warning-section" hidden><div class="section-label">Document warnings</div><div id="document-warnings"></div></div></div>
  </aside>
</main>
<div class="toast" id="toast" role="status"></div>
<script>
const source=__SOURCE_JSON__;
const audit=__AUDIT_JSON__;
const metrics=__METRICS_JSON__;
const sourceManifest=__MANIFEST_JSON__;
const warningScope=__WARNING_SCOPE_JSON__;
const fromKc=new URLSearchParams(location.search).get('from')==='kc';
const el=id=>document.getElementById(id);
const escapeHtml=value=>{const node=document.createElement('div');node.textContent=String(value??'');return node.innerHTML};
const auditByPage=new Map((audit.pages||[]).map(row=>[row.page,row]));
let selected=Math.max(0,Math.min(source.pages.length-1,Number(location.hash.slice(1)||1)-1));
let zoom=100;
let toastTimer;

function warningRows(page){return [...(page.warnings||[]),...(warningScope.by_page[String(page.page_number)]||[])]}
function formatNumber(value){return Number(value||0).toLocaleString('en-US')}
function formatDuration(seconds){if(!Number.isFinite(Number(seconds)))return null;const total=Math.round(Number(seconds));return total>=60?`${Math.floor(total/60)}m ${total%60}s`:`${total}s`}
function renderStats(){
  const repaired=(metrics.repair?.applied_pages||[]).length;
  const warnings=warningScope.record_count;
  const elapsed=metrics.execution_mode==='agent_subscription_session'?'model time unavailable':metrics.total_elapsed_seconds==null?null:formatDuration(Number(metrics.total_elapsed_seconds)+Number(sourceManifest.elapsed_seconds||0));
  const missingGeometry=Number(audit.missing_geometry_block_count||0);
  const usage=metrics.usage_available===false||metrics.usage?.total_tokens==null?'usage unavailable':`${formatNumber(metrics.usage.total_tokens)} tokens`;
  const rows=[`${source.pages.length}/${source.source.page_count} pages`,usage,elapsed,`${repaired} repaired`,`${missingGeometry} missing regions`,`${warnings} review warnings`].filter(Boolean);
  el('stats').innerHTML=rows.map(value=>`<span class="stat">${escapeHtml(value)}</span>`).join('');
}
function searchableText(page){return JSON.stringify(page).toLocaleLowerCase()}
function renderPageList(){
  const query=el('search').value.trim().toLocaleLowerCase();
  const matches=source.pages.map((page,index)=>({page,index})).filter(({page})=>!query||searchableText(page).includes(query));
  el('search-meta').textContent=`${matches.length} of ${source.pages.length} pages`;
  el('page-list').innerHTML=matches.map(({page,index})=>{
    const overlap=auditByPage.get(page.page_number)?.diagnostic_text_token_overlap;
    const warnings=warningRows(page).length;
    const missingGeometry=(auditByPage.get(page.page_number)?.missing_geometry_block_ids||[]).length;
    const needsReview=warnings||missingGeometry;
    const signal=missingGeometry?`⌖ ${missingGeometry}`:warnings?`⚠ ${warnings}`:overlap==null?'—':`${Math.round(overlap*100)}%`;
    const blockCount=(page.blocks||[]).length;
    return `<button class="page-button${index===selected?' active':''}" data-page-index="${index}"><span class="page-number">${page.page_number}</span><span class="page-role">${escapeHtml(page.role)}<small>${blockCount} block${blockCount===1?'':'s'}</small></span><span class="page-signal${needsReview?' warning':''}">${signal}</span></button>`;
  }).join('')||'<div class="empty">No matching pages</div>';
  el('page-list').querySelectorAll('[data-page-index]').forEach(button=>button.onclick=()=>selectPage(Number(button.dataset.pageIndex)));
}
function render(){
  const page=source.pages[selected];
  location.hash=String(page.page_number);
  el('page-position').textContent=`Page ${page.page_number} / ${source.pages.length}`;
  el('page-title').textContent=`Page ${page.page_number} · ${page.role}`;
  if(el('next-stage')){
    el('next-stage').href=`kc-recall.html#${page.page_number}`;
    el('next-stage').textContent=fromKc?'← Back to KC':'KC Review →';
  }
  el('prev').disabled=selected===0;el('next').disabled=selected===source.pages.length-1;
  el('page-image').src=`pages/page-${String(page.page_number).padStart(4,'0')}.png`;
  el('page-json').textContent=JSON.stringify(page,null,2);
  const warnings=warningRows(page);
  el('warnings').innerHTML=warnings.map(w=>`<div class="card warning-card"><strong>${escapeHtml(w.code)}</strong><div>${escapeHtml(w.message)}</div></div>`).join('')||'<div class="muted">No warnings</div>';
  el('document-warning-section').hidden=warningScope.document.length===0;
  el('document-warnings').innerHTML=warningScope.document.map(w=>`<div class="card warning-card"><strong>${escapeHtml(w.code)}</strong><div>${escapeHtml(w.message)}</div></div>`).join('');
  renderPageList();
  requestAnimationFrame(()=>el('page-list').querySelector('.active')?.scrollIntoView({block:'nearest'}));
}
function selectPage(index){selected=Math.max(0,Math.min(source.pages.length-1,index));render();el('image-scroll').scrollTo({top:0,left:0})}
function setZoom(value){zoom=Math.max(50,Math.min(220,value));el('page-image').style.width=`${zoom}%`;el('zoom-label').textContent=`${zoom}%`;el('image-scroll').scrollTo({top:0,left:0})}
async function copyText(text,message){
  try{await navigator.clipboard.writeText(text)}catch(error){const area=document.createElement('textarea');area.value=text;area.style.position='fixed';area.style.opacity='0';document.body.appendChild(area);area.select();document.execCommand('copy');area.remove()}
  el('toast').textContent=message;el('toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el('toast').classList.remove('show'),1500);
}
function copyJson(value,message){return copyText(JSON.stringify(value,null,2),message)}
function togglePanel(side,forceClosed){const className=`${side}-collapsed`;const close=forceClosed??!el('layout').classList.contains(className);el('layout').classList.toggle(className,close);localStorage.setItem(`review-${className}`,String(close));updateToggleLabels()}
function updateToggleLabels(){const leftClosed=el('layout').classList.contains('left-collapsed');const rightClosed=el('layout').classList.contains('right-collapsed');el('left-toggle').textContent=leftClosed?'☰ Show pages':'☰ Pages';el('right-toggle').textContent=rightClosed?'Show details ☷':'Details ☷';el('left-toggle').classList.toggle('primary',leftClosed);el('right-toggle').classList.toggle('primary',rightClosed)}
function installResize(handleId,side){
  const handle=el(handleId);const property=side==='left'?'--left-width':'--right-width';const defaultWidth=side==='left'?280:390;
  const startDrag=event=>{event.preventDefault();const startX=event.touches?.[0]?.clientX??event.clientX;const start=parseFloat(getComputedStyle(el('layout')).getPropertyValue(property));handle.classList.add('dragging');document.body.classList.add('dragging');const move=moveEvent=>{const clientX=moveEvent.touches?.[0]?.clientX??moveEvent.clientX;const delta=clientX-startX;const width=side==='left'?start+delta:start-delta;el('layout').style.setProperty(property,`${Math.max(210,Math.min(560,width))}px`)};const stop=()=>{handle.classList.remove('dragging');document.body.classList.remove('dragging');window.removeEventListener('mousemove',move);window.removeEventListener('mouseup',stop);window.removeEventListener('touchmove',move);window.removeEventListener('touchend',stop);localStorage.setItem(`review-${property}`,getComputedStyle(el('layout')).getPropertyValue(property).trim())};window.addEventListener('mousemove',move);window.addEventListener('mouseup',stop);window.addEventListener('touchmove',move,{passive:false});window.addEventListener('touchend',stop)};
  handle.addEventListener('mousedown',startDrag);handle.addEventListener('touchstart',startDrag,{passive:false});
  handle.ondblclick=()=>{el('layout').style.setProperty(property,`${defaultWidth}px`);localStorage.removeItem(`review-${property}`)};
}
el('search').oninput=renderPageList;
el('prev').onclick=()=>selectPage(selected-1);el('next').onclick=()=>selectPage(selected+1);
el('zoom-out').onclick=()=>setZoom(zoom-10);el('zoom-in').onclick=()=>setZoom(zoom+10);el('zoom-reset').onclick=()=>setZoom(100);
el('copy-page').onclick=()=>copyJson(source.pages[selected],'Page output JSON copied');
el('left-toggle').onclick=()=>togglePanel('left');el('right-toggle').onclick=()=>togglePanel('right');el('left-close').onclick=()=>togglePanel('left',true);el('right-close').onclick=()=>togglePanel('right',true);
addEventListener('keydown',event=>{if(event.target.matches('input,textarea'))return;if(event.key==='ArrowLeft')selectPage(selected-1);if(event.key==='ArrowRight')selectPage(selected+1);if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='f'){event.preventDefault();el('search').focus()}});
for(const side of ['left','right'])if(localStorage.getItem(`review-${side}-collapsed`)==='true')el('layout').classList.add(`${side}-collapsed`);
for(const property of ['--left-width','--right-width']){const stored=localStorage.getItem(`review-${property}`);if(stored)el('layout').style.setProperty(property,stored)}
installResize('left-handle','left');installResize('right-handle','right');renderStats();updateToggleLabels();setZoom(100);render();
</script>
</body></html>
"""
