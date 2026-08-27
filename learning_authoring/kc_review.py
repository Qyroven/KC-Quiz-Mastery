"""Static one-port overview, source-first KC review, and comparison UI."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from learning_authoring.artifacts import RunArtifacts, read_json, write_text
from learning_authoring.authoring_context import load_authoring_context
from learning_authoring.contracts import ExtractedSource, SourceDescriptor
from learning_authoring.kc import load_approved_extraction
from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.review import build_review


def _candidate_metrics(
    proposed: ProposedKCSet,
    approved: ExtractedSource,
    run_metrics: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    source_blocks = {block.block_id for page in approved.pages for block in page.blocks}
    evidence_rows = [e for kc in proposed.leaf_kcs for e in kc.source_evidence]
    referenced_blocks = {block_id for evidence in evidence_rows for block_id in evidence.block_ids}
    multi_page = sum(1 for kc in proposed.leaf_kcs if len({e.page for e in kc.source_evidence}) > 1)
    conditional = re.compile(
        r"\b(khi|nếu|given|when|trong (?:một )?tình huống|được (?:cho|cung cấp))\b", re.I
    )
    observable_rate = (
        sum(bool(conditional.search(kc.observable_claim)) for kc in proposed.leaf_kcs)
        / len(proposed.leaf_kcs)
        if proposed.leaf_kcs
        else 0.0
    )
    usage = run_metrics.get("usage") or {}
    return {
        "model": metadata.get("model", "unknown"),
        "contract_valid": bool(run_metrics.get("contract_valid")),
        "leaf_kcs": len(proposed.leaf_kcs),
        "groups": len(proposed.kc_groups),
        "page_audits": len(proposed.page_audit),
        "pages_with_kcs": len({evidence.page for evidence in evidence_rows}),
        "evidence_records": len(evidence_rows),
        "context_evidence_records": sum(len(kc.context_evidence) for kc in proposed.leaf_kcs),
        "context_only_kcs": sum(not kc.source_evidence for kc in proposed.leaf_kcs),
        "referenced_source_blocks": len(referenced_blocks),
        "source_blocks": len(source_blocks),
        "block_reference_rate": len(referenced_blocks) / len(source_blocks) if source_blocks else 0,
        "multi_page_kcs": multi_page,
        "conditional_claim_rate": observable_rate,
        "warnings": len(proposed.generation_warnings),
        "uncovered_items": len(proposed.uncovered_content),
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": usage.get("cached_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "model_seconds": run_metrics.get("model_elapsed_seconds"),
        "cost_usd": run_metrics.get("gateway_reported_cost_usd"),
    }


def _discover_candidates(run_dir: Path) -> list[Path]:
    root = run_dir / "kc-candidates"
    if not root.is_dir():
        return []
    return sorted(path.parent for path in root.glob("*/kc-proposed.json"))


def _load_candidate(
    path: Path, approved: ExtractedSource, *, run_dir: Path | None = None
) -> dict[str, Any]:
    raw_proposed = read_json(path / "kc-proposed.json")
    proposed = ProposedKCSet.model_validate(raw_proposed)
    context = load_authoring_context(run_dir or path, approved.source)
    proposed.validate_against_source(approved, context)
    metrics = read_json(path / "kc-run-metrics.json")
    metadata = read_json(path / "kc-generation-metadata.json")
    return {
        "id": path.name,
        "model": metadata.get("model", path.name),
        "path": str(path),
        # Keep legacy baselines byte-shape compatible: validation defaults are not edits.
        "proposed": raw_proposed,
        "metrics": _candidate_metrics(proposed, approved, metrics, metadata),
        "raw_metrics": metrics,
        "metadata": metadata,
    }


def _json_script(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def _selected_candidate(
    candidates: list[dict[str, Any]], evaluation: dict[str, Any] | None
) -> dict[str, Any]:
    """Select the review candidate without changing any model output."""

    recommended = ((evaluation or {}).get("verdict") or {}).get("recommended_candidate")
    if recommended:
        match = next((item for item in candidates if item["model"] == recommended), None)
        if match:
            return match
    sol = next((item for item in candidates if item["model"] == "gpt-5.6-sol"), None)
    return sol or candidates[0]


def _recall_html(
    approved: ExtractedSource,
    candidate: dict[str, Any],
    *,
    scroll_mode: bool = False,
    upstream_status: str = "HUMAN_APPROVED",
) -> str:
    """Build the source-first review view used to find omissions before approval."""

    data = {
        "source": approved.model_dump(mode="json"),
        "candidate": candidate,
        "scroll_mode": scroll_mode,
    }
    template = r'''<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>KC Recall Review — __TITLE__</title>
<style>
:root{color-scheme:light;--bg:#f4f7fb;--bar:rgba(255,255,255,.9);--panel:#fff;--raised:#f7f8fa;--ink:#182033;--secondary:#697386;--tertiary:#858c98;--line:#dfe4eb;--blue:#2864a5;--blue-fill:#eef5fd;--green:#23864a;--green-fill:#eff9f2;--orange:#bd6b13;--orange-fill:#fff8ed;--red:#c84b4b;--red-fill:#fff0f0;--purple:#7d4da1;--purple-fill:#f4edf8;--shadow:0 1px 2px rgba(24,32,51,.04),0 10px 32px rgba(24,32,51,.06);--radius:14px;--left-width:286px;--right-width:390px}
@media(prefers-color-scheme:dark){:root{color-scheme:light;--bg:#f4f7fb;--bar:rgba(255,255,255,.9);--panel:#fff;--raised:#f7f8fa;--ink:#182033;--secondary:#697386;--tertiary:#858c98;--line:#dfe4eb;--blue:#2864a5;--blue-fill:#eef5fd;--green:#23864a;--green-fill:#eff9f2;--orange:#bd6b13;--orange-fill:#fff8ed;--red:#c84b4b;--red-fill:#fff0f0;--purple:#7d4da1;--purple-fill:#f4edf8;--shadow:0 1px 2px rgba(24,32,51,.04),0 10px 32px rgba(24,32,51,.06)}}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;overflow:hidden;background:var(--bg);color:var(--ink);font:14px/1.42 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif;letter-spacing:-.008em}button,input,a{font:inherit}button{color:inherit}a{color:var(--blue);text-decoration:none}h1,h2,h3,p{margin:0}.global{height:52px;display:flex;align-items:center;gap:16px;padding:0 18px;border-bottom:1px solid var(--line);background:var(--bar);backdrop-filter:saturate(180%) blur(22px);-webkit-backdrop-filter:saturate(180%) blur(22px);position:relative;z-index:20}.mark{width:28px;height:28px;border-radius:8px;background:linear-gradient(145deg,#182033 0 52%,#2864a5 52%);box-shadow:inset 0 0 0 1px #fff2}.product{font-weight:650;font-size:15px}.crumb{color:var(--secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.global-spacer{flex:1}.navlink{padding:6px 9px;border-radius:8px;color:var(--secondary)}.navlink:hover{background:var(--raised);color:var(--ink)}.model-chip{padding:5px 9px;border-radius:999px;background:var(--blue-fill);color:var(--blue);font-size:12px;font-weight:600}
.workspace{height:calc(100% - 52px);display:grid;grid-template-columns:var(--left-width) 7px minmax(430px,1fr) 7px var(--right-width);min-height:0}.workspace.left-closed{grid-template-columns:0 0 minmax(430px,1fr) 7px var(--right-width)}.workspace.right-closed{grid-template-columns:var(--left-width) 7px minmax(430px,1fr) 0 0}.workspace.left-closed.right-closed{grid-template-columns:0 0 minmax(430px,1fr) 0 0}.workspace.left-closed .sidebar,.workspace.left-closed #leftResize,.workspace.right-closed .inspector,.workspace.right-closed #rightResize{display:none}.sidebar,.canvas,.inspector,.resize-handle{grid-row:1}.sidebar{grid-column:1}.canvas{grid-column:3}.inspector{grid-column:5}#leftResize{grid-column:2}#rightResize{grid-column:4}.sidebar,.inspector{min-width:0;min-height:0;background:var(--panel)}.sidebar{border-right:1px solid var(--line);display:grid;grid-template-rows:auto auto minmax(0,1fr);overflow:hidden}.inspector{border-left:1px solid var(--line);overflow:auto}.resize-handle{position:relative;background:color-mix(in srgb,var(--line) 55%,transparent);cursor:col-resize;touch-action:none}.resize-handle:after{content:"";position:absolute;top:calc(50% - 24px);left:3px;width:1px;height:48px;background:var(--tertiary);opacity:.45}.resize-handle:hover,.resize-handle.dragging{background:var(--blue-fill)}.resize-handle:hover:after,.resize-handle.dragging:after{background:var(--blue);opacity:1}body.resizing{cursor:col-resize;user-select:none}.side-head,.inspect-head{padding:18px 16px 13px}.side-head-row{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.side-head-row .icon-btn{margin-top:-2px;flex:0 0 auto}.eyebrow{font-size:11px;line-height:1.2;text-transform:uppercase;letter-spacing:.08em;color:var(--secondary);font-weight:650}.side-head h1{margin-top:4px;font-size:22px;letter-spacing:-.025em}.summary-line{margin-top:5px;color:var(--secondary);font-size:12px}.search-wrap{padding:0 12px 10px}.search{width:100%;border:0;border-radius:10px;background:var(--bg);padding:9px 11px 9px 32px;outline:none;color:var(--ink);box-shadow:inset 0 0 0 1px var(--line)}.search:focus{box-shadow:inset 0 0 0 2px var(--blue)}.search-box{position:relative}.search-icon{position:absolute;left:10px;top:10px;color:var(--tertiary);pointer-events:none}.filters{display:grid;gap:3px;padding:0 10px 10px}.filter{width:100%;display:grid;grid-template-columns:54px minmax(0,1fr) auto;gap:7px;align-items:center;border:0;background:transparent;padding:7px 8px;border-radius:9px;color:var(--secondary);text-align:left;cursor:pointer}.filter:hover{background:var(--raised)}.filter.active{background:var(--ink);color:var(--panel)}.filter-id{font-size:10px;font-weight:700;letter-spacing:.025em}.filter-name{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:11px}.filter-count{font-size:10px;font-variant-numeric:tabular-nums;opacity:.72}.page-list{min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:0 8px 14px}.page-row{width:100%;display:grid;grid-template-columns:34px minmax(0,1fr) auto;gap:8px;align-items:center;border:0;background:transparent;padding:8px;border-radius:10px;text-align:left;cursor:pointer}.page-row:hover{background:var(--raised)}.page-row.active{background:var(--blue-fill)}.page-no{font-variant-numeric:tabular-nums;font-weight:650}.page-main{display:block;min-width:0}.page-role{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px}.page-meta{display:block;margin-top:1px;color:var(--secondary);font-size:11px}.signal{width:8px;height:8px;border-radius:50%;background:var(--tertiary);box-shadow:0 0 0 3px transparent}.signal.covered{background:var(--green)}.signal.context{background:var(--tertiary)}.signal.review{background:var(--orange)}.signal.uncovered{background:var(--red)}.page-row.active .signal{box-shadow:0 0 0 3px color-mix(in srgb,var(--blue) 18%,transparent)}
.canvas{min-width:0;display:grid;grid-template-rows:48px minmax(0,1fr);background:var(--bg)}.toolbar{display:flex;align-items:center;justify-content:space-between;padding:0 14px;border-bottom:1px solid var(--line);background:var(--bar);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px)}.tool-cluster{display:flex;align-items:center;gap:6px}.icon-btn{width:32px;height:32px;border:0;border-radius:8px;background:transparent;display:grid;place-items:center;cursor:pointer}.icon-btn:hover:not(:disabled){background:var(--panel)}.icon-btn:disabled{opacity:.28}.position{min-width:94px;text-align:center;font-size:12px;color:var(--secondary);font-variant-numeric:tabular-nums}.zoom{min-width:42px;text-align:center;color:var(--secondary);font-size:12px}.slide-scroll{overflow:auto;padding:28px;scroll-behavior:smooth}.slide-deck{min-width:0}.slide-shell{width:min(100%,1100px);margin:0 auto 30px;transition:width .18s ease}.slide-shell:last-child{margin-bottom:0}.slide-shell.active img{box-shadow:0 0 0 3px color-mix(in srgb,var(--blue) 55%,transparent),0 18px 60px rgba(0,0,0,.17)}.slide-shell img{display:block;width:100%;height:auto;background:white;border-radius:4px;box-shadow:0 18px 60px rgba(0,0,0,.17)}
.inspect-head{position:sticky;top:0;z-index:5;background:var(--bar);backdrop-filter:saturate(180%) blur(22px);-webkit-backdrop-filter:saturate(180%) blur(22px);border-bottom:1px solid var(--line)}.inspect-title{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.inspect-title h2{font-size:21px;letter-spacing:-.025em;margin-top:3px}.status-pill{padding:4px 8px;border-radius:999px;font-size:11px;font-weight:650;white-space:nowrap}.status-pill.covered{color:var(--green);background:var(--green-fill)}.status-pill.context{color:var(--secondary);background:var(--bg)}.status-pill.review{color:var(--orange);background:var(--orange-fill)}.status-pill.uncovered{color:var(--red);background:var(--red-fill)}.inspect-body{padding:14px 14px 40px}.section-title{margin:4px 0 10px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--secondary);font-weight:650}.kc-card,.block-card,.notice,.decision-card{border:1px solid var(--line);background:var(--raised);border-radius:var(--radius);margin-bottom:9px;overflow:hidden}.decision-card{padding:13px}.decision-head{display:flex;align-items:center;gap:8px;font-weight:650}.decision-class{margin-left:auto;padding:2px 7px;border-radius:999px;background:var(--bg);color:var(--secondary);font-size:10px}.decision-copy{margin-top:7px;color:var(--secondary);font-size:12px}.decision-note{margin-top:8px;padding-top:8px;border-top:1px solid var(--line);font-size:11px;color:var(--orange)}.kc-button{display:block;width:100%;border:0;background:transparent;text-align:left;padding:13px;cursor:pointer}.kc-button:hover{background:var(--panel)}.kc-top{display:flex;align-items:flex-start;gap:8px}.kc-id{color:var(--blue);font-weight:650;font-size:12px}.form{margin-left:auto;padding:2px 6px;border-radius:6px;background:var(--purple-fill);color:var(--purple);font-size:10px}.kc-group,.float-kc-group{margin-top:4px;color:var(--secondary);font-size:10px;font-weight:600}.kc-name{font-size:14px;font-weight:650;margin-top:4px}.kc-detail{padding:0 13px 13px;border-top:1px solid var(--line)}.detail-label{margin-top:11px;font-size:10px;text-transform:uppercase;letter-spacing:.065em;color:var(--secondary);font-weight:650}.detail-copy{margin-top:3px}.boundary{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:6px}.boundary>div{border-radius:9px;background:var(--panel);padding:8px;font-size:11px}.boundary b{display:block;margin-bottom:4px}.boundary ul{margin:0;padding-left:15px}.source-check{margin-top:18px;border-top:1px solid var(--line);padding-top:4px}.source-check>summary{list-style:none;display:flex;align-items:center;gap:8px;padding:12px 2px;cursor:pointer;color:var(--blue);font-weight:600}.source-check>summary::-webkit-details-marker{display:none}.source-check>summary:before{content:"›";font-size:18px;transition:transform .15s}.source-check[open]>summary:before{transform:rotate(90deg)}.source-check-copy{color:var(--secondary);margin:0 0 12px}.block-card{padding:11px 12px}.block-top{display:flex;align-items:center;gap:7px}.block-id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px}.block-kind{color:var(--secondary);font-size:11px}.block-state{margin-left:auto;font-size:10px;font-weight:650}.block-state.covered{color:var(--green)}.block-state.context{color:var(--secondary)}.block-state.review{color:var(--orange)}.block-state.uncovered{color:var(--red)}.block-content{margin-top:6px;color:var(--secondary);font-size:11px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.notice{padding:11px 12px;background:var(--orange-fill);border-color:color-mix(in srgb,var(--orange) 30%,var(--line));color:var(--orange)}.notice.red{background:var(--red-fill);border-color:color-mix(in srgb,var(--red) 30%,var(--line));color:var(--red)}.notice b{display:block;font-size:11px}.notice p{margin-top:3px;font-size:12px;color:var(--ink)}.empty{padding:18px;border:1px dashed var(--line);border-radius:var(--radius);text-align:center;color:var(--secondary)}.footer-note{margin-top:18px;padding-top:14px;border-top:1px solid var(--line);color:var(--secondary);font-size:11px}.kbd{display:inline-block;border:1px solid var(--line);border-bottom-width:2px;border-radius:5px;padding:0 5px;background:var(--raised);font-size:10px}.panel-toggle.active{background:var(--blue-fill);color:var(--blue)}.kc-float{display:none}.scroll-mode{overflow:auto}.scroll-mode .global{position:sticky;top:0}.scroll-mode .workspace,.scroll-mode .workspace.left-closed,.scroll-mode .workspace.right-closed,.scroll-mode .workspace.left-closed.right-closed{height:auto;display:block}.scroll-mode .sidebar,.scroll-mode #leftResize,.scroll-mode .inspector,.scroll-mode #rightResize,.scroll-mode #sidebarToggle,.scroll-mode #inspectorToggle{display:none}.scroll-mode .canvas{display:block;max-width:1280px;width:100%;margin:0 auto}.scroll-mode .toolbar{position:sticky;top:52px;z-index:19;height:48px}.scroll-mode .slide-scroll{overflow:visible;padding:34px max(24px,calc((100vw - 1120px)/2)) 210px}.scroll-mode .slide-shell{scroll-margin-top:118px}.scroll-mode .kc-float{display:block;position:fixed;z-index:30;right:22px;bottom:22px;width:min(390px,calc(100vw - 44px));max-height:min(52vh,520px);overflow:auto;padding:12px;border:1px solid var(--line);border-radius:18px;background:color-mix(in srgb,var(--panel) 92%,transparent);box-shadow:0 20px 70px rgba(0,0,0,.2);backdrop-filter:saturate(180%) blur(24px);-webkit-backdrop-filter:saturate(180%) blur(24px);opacity:0;transform:translateY(14px) scale(.98);transition:opacity .18s ease,transform .18s ease}.scroll-mode .kc-float.show{opacity:1;transform:none}.float-head{display:flex;align-items:center;gap:8px;margin-bottom:9px}.float-slide{font-weight:700}.float-count{margin-left:auto;color:var(--secondary);font-size:11px}.float-kc{padding:11px 12px;border:1px solid var(--line);border-radius:12px;background:var(--raised);margin-top:8px}.float-kc-top{display:flex;align-items:center;gap:8px}.float-kc-id{color:var(--blue);font-weight:650;font-size:11px}.float-kc-form{margin-left:auto;color:var(--purple);font-size:10px}.float-kc-name{font-weight:650;margin-top:3px}.float-kc-copy{color:var(--secondary);font-size:11px;margin-top:5px}.float-empty{padding:12px;border-radius:12px;background:var(--raised);color:var(--secondary);font-size:12px}
.scroll-kc-resize{display:none}.scroll-mode{overflow:hidden;--scroll-kc-width:460px}.scroll-mode .global{position:relative;top:auto}.scroll-mode .workspace,.scroll-mode .workspace.left-closed,.scroll-mode .workspace.right-closed,.scroll-mode .workspace.left-closed.right-closed{height:calc(100% - 52px);display:grid;grid-template-columns:minmax(440px,1fr) 8px var(--scroll-kc-width);min-height:0}.scroll-mode .workspace.right-closed{grid-template-columns:minmax(440px,1fr) 0 0}.scroll-mode .canvas{grid-column:1;grid-row:1;display:grid;grid-template-rows:48px minmax(0,1fr);max-width:none;width:auto;margin:0}.scroll-mode .toolbar{position:relative;top:auto;height:auto}.scroll-mode .slide-scroll{overflow:auto;padding:28px 34px 180px}.scroll-mode .slide-shell{scroll-margin-top:18px}.scroll-mode #inspectorToggle{display:grid}.scroll-mode .scroll-kc-resize{display:block;grid-column:2;grid-row:1;position:relative;background:color-mix(in srgb,var(--line) 55%,transparent);cursor:col-resize;touch-action:none}.scroll-mode .scroll-kc-resize:after{content:"";position:absolute;top:calc(50% - 28px);left:3px;width:2px;height:56px;border-radius:2px;background:var(--tertiary);opacity:.5}.scroll-mode .scroll-kc-resize:hover,.scroll-mode .scroll-kc-resize.dragging{background:var(--blue-fill)}.scroll-mode .scroll-kc-resize:hover:after,.scroll-mode .scroll-kc-resize.dragging:after{background:var(--blue);opacity:1}.scroll-mode .kc-float,.scroll-mode .kc-float.show{display:block;grid-column:3;grid-row:1;position:relative;right:auto;bottom:auto;z-index:1;width:auto;max-height:none;overflow:auto;padding:18px;border:0;border-left:1px solid var(--line);border-radius:0;background:var(--panel);box-shadow:none;backdrop-filter:none;-webkit-backdrop-filter:none;opacity:1;transform:none;transition:none}.scroll-mode .workspace.right-closed .kc-float,.scroll-mode .workspace.right-closed .scroll-kc-resize{display:none}.scroll-mode .float-head{position:sticky;top:-18px;z-index:3;margin:-18px -18px 10px;padding:17px 18px 12px;background:var(--bar);border-bottom:1px solid var(--line);backdrop-filter:saturate(180%) blur(22px);-webkit-backdrop-filter:saturate(180%) blur(22px)}.scroll-mode .float-kc{padding:0;overflow:hidden;animation:kcPop .2s ease both}.float-kc-button{display:block;width:100%;padding:13px;border:0;background:transparent;color:inherit;text-align:left;cursor:pointer}.float-kc-button:hover{background:var(--raised)}.float-kc-detail{padding:0 13px 13px;border-top:1px solid var(--line)}.float-detail-label{margin-top:10px;color:var(--secondary);font-size:10px;font-weight:650;text-transform:uppercase;letter-spacing:.06em}.float-detail-copy{margin-top:3px;font-size:12px}.float-boundary{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:6px}.float-boundary>div{padding:8px;border-radius:9px;background:var(--bg);font-size:11px}.float-boundary b{display:block;margin-bottom:4px}.float-boundary ul{margin:0;padding-left:15px}@keyframes kcPop{from{opacity:0;transform:translateX(12px)}to{opacity:1;transform:none}}
.scroll-mode .workspace,.scroll-mode .workspace.left-closed,.scroll-mode .workspace.right-closed,.scroll-mode .workspace.left-closed.right-closed{grid-template-rows:minmax(0,1fr);overflow:hidden}.scroll-mode .canvas{min-height:0}
.context-sheet{max-width:850px;margin:auto;padding:24px;border:1px solid var(--line);border-radius:var(--radius);background:var(--panel)}.context-sheet h2{margin-bottom:8px}.context-excerpt{white-space:pre-wrap;overflow-wrap:anywhere}.context-evidence{margin-top:8px;padding:10px;border-left:3px solid var(--purple);background:var(--purple-fill);border-radius:6px}.context-note{color:var(--secondary);font-size:12px}.context-row .page-no{font-size:10px;color:var(--purple)}[hidden]{display:none!important}
@media(max-width:1060px){:root{--left-width:240px;--right-width:330px}.scroll-mode{--scroll-kc-width:380px}}@media(max-width:820px){body{overflow:auto}.global{position:sticky;top:0}.workspace,.workspace.left-closed,.workspace.right-closed,.workspace.left-closed.right-closed{height:auto;display:block}.resize-handle{display:none}.sidebar{border:0;border-bottom:1px solid var(--line)}.page-list{max-height:260px}.canvas{height:70vh}.inspector{border:0;border-top:1px solid var(--line)}.navlink.optional{display:none}.scroll-mode .workspace,.scroll-mode .workspace.left-closed,.scroll-mode .workspace.right-closed,.scroll-mode .workspace.left-closed.right-closed{height:calc(100% - 52px);display:grid;grid-template-columns:minmax(320px,1fr) 7px minmax(300px,var(--scroll-kc-width))}.scroll-mode .canvas{height:auto}.scroll-mode .scroll-kc-resize{display:block}}
</style></head><body class="__BODY_CLASS__">
<header class="global"><span class="mark" aria-hidden="true"></span><span class="product">Learning Authoring</span><span class="crumb">/ __VIEW__ / __TITLE__</span><span class="global-spacer"></span><span class="model-chip">Extraction: __UPSTREAM__</span><span class="model-chip">__MODEL__</span><a class="navlink" id="contextLink" href="#context" hidden>Lecturer context</a><a class="navlink" id="viewLink" href="__VIEW_LINK__">__VIEW_LABEL__</a><a class="navlink" id="extractionLink" href="extraction-review.html?from=kc#1">← Extraction</a></header>
<main class="workspace">
<aside class="sidebar"><div class="side-head"><div class="side-head-row"><div><div class="eyebrow">Slides</div><h1>Source</h1><p class="summary-line" id="sourceSummary"></p></div><button class="icon-btn" id="sidebarCollapse" aria-label="Collapse slides panel" title="Collapse slides panel">←</button></div></div><div><div class="search-wrap"><div class="search-box"><span class="search-icon">⌕</span><input class="search" id="search" type="search" placeholder="Search slide or KC…"></div></div><div class="filters" id="filters" aria-label="Filter slides by KC group"></div></div><nav class="page-list" id="pageList" aria-label="Source slides"></nav></aside>
<div class="resize-handle" id="leftResize" title="Drag to resize · double-click to reset"></div>
<section class="canvas"><div class="toolbar"><div class="tool-cluster"><button class="icon-btn panel-toggle" id="sidebarToggle" aria-label="Toggle slides panel">☰</button><button class="icon-btn" id="previous" aria-label="Previous slide">‹</button><span class="position" id="position"></span><button class="icon-btn" id="next" aria-label="Next slide">›</button></div><div class="tool-cluster"><button class="icon-btn" id="zoomOut" aria-label="Zoom out">−</button><span class="zoom" id="zoomLabel">100%</span><button class="icon-btn" id="zoomIn" aria-label="Zoom in">＋</button><button class="icon-btn" id="fit" aria-label="Fit slide">⌗</button><button class="icon-btn panel-toggle" id="inspectorToggle" aria-label="Toggle KC panel">☷</button></div></div><div class="slide-scroll" id="slideScroll"><div class="slide-deck" id="slideDeck"></div></div></section>
<div class="resize-handle" id="rightResize" title="Drag to resize · double-click to reset"></div>
<aside class="inspector"><div class="inspect-head"><div class="inspect-title"><div><div class="eyebrow">Knowledge Components</div><h2 id="pageTitle"></h2></div><span class="status-pill" id="pageStatus"></span></div><p class="summary-line" id="pageRole"></p></div><div class="inspect-body" id="inspectorBody"></div></aside>
<div class="scroll-kc-resize" id="scrollKcResize" title="Drag to resize KC panel · double-click to reset"></div><aside class="kc-float" id="kcFloat" aria-live="polite"></aside>
</main>
<script>const DATA=__DATA__;
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
const source=DATA.source,proposal=DATA.candidate.proposed,scrollMode=Boolean(DATA.scroll_mode),kcs=proposal.leaf_kcs,kcById=Object.fromEntries(kcs.map(k=>[k.kc_id,k])),groupList=proposal.kc_groups,groups=Object.fromEntries(groupList.map(g=>[g.group_id,g]));
const auditByPage=Object.fromEntries(proposal.page_audit.map(x=>[x.page,x]));
const evidenceByPage={};kcs.forEach(k=>k.source_evidence.forEach(e=>(evidenceByPage[e.page]??=[]).push({kc:k,evidence:e})));
const contextKcs=()=>kcs.filter(k=>(k.context_evidence||[]).length);
const uncoveredByPage={};proposal.uncovered_content.forEach(x=>(uncoveredByPage[x.page]??=[]).push(x));
const warningsByPage={};proposal.generation_warnings.forEach(x=>x.pages.forEach(p=>(warningsByPage[p]??=[]).push(x)));
const contextClasses=new Set(['context','administrative','cover','section_divider']);
function compactContent(value){if(typeof value==='string')return value;if(Array.isArray(value))return value.map(compactContent).join(' · ');if(value&&typeof value==='object')return Object.entries(value).map(([k,v])=>`${k}: ${compactContent(v)}`).join(' · ');return String(value??'')}
function blockState(page,blockId){const audit=auditByPage[page];if((uncoveredByPage[page]||[]).some(x=>x.block_ids.includes(blockId)))return'uncovered';if((evidenceByPage[page]||[]).some(x=>x.evidence.block_ids.includes(blockId)))return'covered';if(contextClasses.has(audit.classification))return'context';return'review'}
function pageState(page){const sourcePage=source.pages[page-1],states=sourcePage.blocks.map(b=>blockState(page,b.block_id));if(states.includes('uncovered'))return'uncovered';if(states.includes('review'))return'review';if(states.includes('covered'))return'covered';return'context'}
const pageRows=source.pages.map(p=>{const a=auditByPage[p.page_number],state=pageState(p.page_number),related=new Set((evidenceByPage[p.page_number]||[]).map(x=>x.kc.kc_id)),groupIds=[...new Set([...related].map(id=>kcById[id].group_id))];return{page:p.page_number,role:p.role,audit:a,state,kcCount:related.size,groupIds,search:compactContent({p,a,related:[...related].map(id=>kcById[id]),groups:groupIds.map(id=>groups[id])}).toLowerCase()}});
let selected=Math.max(1,Math.min(source.source.page_count,Number(location.hash.slice(1))||1)),contextSelected=location.hash==='#context'&&contextKcs().length>0,filter='all',zoom=1;
const byId=id=>document.getElementById(id);
const pageList=byId('pageList'),search=byId('search'),slideDeck=byId('slideDeck'),slideScroll=byId('slideScroll'),workspace=document.querySelector('.workspace'),pageTitle=byId('pageTitle'),pageRole=byId('pageRole'),pageStatus=byId('pageStatus'),inspectorBody=byId('inspectorBody'),kcFloat=byId('kcFloat'),scrollKcResize=byId('scrollKcResize'),position=byId('position'),previous=byId('previous'),next=byId('next'),extractionLink=byId('extractionLink'),sourceSummary=byId('sourceSummary'),sidebarToggle=byId('sidebarToggle'),sidebarCollapse=byId('sidebarCollapse'),inspectorToggle=byId('inspectorToggle'),zoomOut=byId('zoomOut'),zoomIn=byId('zoomIn'),zoomLabel=byId('zoomLabel'),fit=byId('fit'),filters=byId('filters');
const labels={covered:'Has KC',context:'No KC',review:'Check',uncovered:'Uncovered'};
const stateHelp={covered:'At least one KC uses this slide as evidence.',context:'The model classified this slide as non-teaching context, so no KC was expected.',review:'This slide contains learning or example content but no KC uses it as evidence.',uncovered:'The model explicitly declared content on this slide uncovered.'};
function renderGroupFilters(){const rows=[{group_id:'all',name:'All groups',leaf_kc_ids:kcs.map(k=>k.kc_id)},...groupList];filters.innerHTML=rows.map(g=>`<button class="filter ${filter===g.group_id?'active':''}" data-group="${esc(g.group_id)}" title="${esc(g.name)}"><span class="filter-id">${g.group_id==='all'?'ALL':esc(g.group_id)}</span><span class="filter-name">${esc(g.name)}</span><span class="filter-count">${g.leaf_kc_ids.length} KC</span></button>`).join('');filters.querySelectorAll('[data-group]').forEach(button=>button.onclick=()=>setGroupFilter(button.dataset.group))}
function rowMatchesFilter(row){return filter==='all'||row.groupIds.includes(filter)}
function contextMatchesFilter(k){return filter==='all'||k.group_id===filter}
function selectedKcRows(){return contextSelected?contextKcs().filter(contextMatchesFilter).map(k=>({kc:k,evidence:null})):[...new Map((evidenceByPage[selected]||[]).map(row=>[row.kc.kc_id,row])).values()]}
function renderList(){const q=search.value.trim().toLowerCase(),contextRows=contextKcs().filter(contextMatchesFilter),showContext=contextRows.length&&(!q||compactContent(contextRows).toLowerCase().includes(q)||'lecturer context'.includes(q));let html=showContext?`<button class="page-row context-row ${contextSelected?'active':''}" data-context><span class="page-no">CTX</span><span class="page-main"><span class="page-role">Lecturer context</span><span class="page-meta">${contextRows.length} KC · not extraction</span></span><span class="signal context"></span></button>`:'';html+=pageRows.filter(r=>rowMatchesFilter(r)&&(!q||r.search.includes(q))).map(r=>`<button class="page-row ${!contextSelected&&r.page===selected?'active':''}" data-page="${r.page}"><span class="page-no">${r.page}</span><span class="page-main"><span class="page-role">${esc(r.role)}</span><span class="page-meta">${r.kcCount?r.kcCount+' KC':esc(labels[r.state])}</span></span><span class="signal ${r.state}" title="${esc(stateHelp[r.state])}"></span></button>`).join('');pageList.innerHTML=html||'<div class="empty">No matching slides or context</div>';pageList.querySelectorAll('[data-page]').forEach(x=>x.onclick=()=>selectPage(Number(x.dataset.page),true));pageList.querySelector('[data-context]')?.addEventListener('click',selectContext)}
function setGroupFilter(groupId){filter=groupId;renderGroupFilters();if(contextSelected&&contextKcs().some(contextMatchesFilter)){render();return}const current=pageRows[selected-1];if(contextSelected||!rowMatchesFilter(current)){const first=pageRows.find(rowMatchesFilter);if(first){selectPage(first.page,true);return}if(contextKcs().some(contextMatchesFilter)){selectContext();return}}renderList()}
function contextEvidenceHtml(k,prefix='detail'){const rows=k.context_evidence||[];if(!rows.length)return'';return`<div class="${prefix}-label">Lecturer context · separate from extraction</div>${rows.map(e=>`<div class="context-evidence"><b>${esc(e.context_id)}</b><div class="context-excerpt">${esc(e.excerpt||e.description||'')}</div><div>${esc(e.supports)}</div><p class="context-note">${e.pages?.length?'Related PDF pages: '+e.pages.map(esc).join(', '):e.mapping_method==='document_level'?'Document-level context · no PDF page':'Unmapped context · no PDF page'} · ${esc(e.mapping_method||'unmapped')} · confidence: ${esc(e.mapping_confidence||'unmapped')}</p></div>`).join('')}`}
function pdfEvidenceHtml(k,e,prefix='detail'){const rows=e?[e]:(k.source_evidence||[]);return`<div class="${prefix}-label">${e?'Evidence on this slide':'PDF source evidence'}</div><div class="${prefix}-copy">${rows.length?rows.map(item=>`${!e?'Page '+esc(item.page)+' · ':''}${item.block_ids.map(x=>`<code>${esc(x)}</code>`).join(' · ')}<br>${esc(item.description)}<br>${esc(item.supports)}`).join('<br>'):'No PDF evidence · lecturer-context-only KC'}</div>`}
function renderDeck(){if(contextSelected){const rows=selectedKcRows();slideDeck.innerHTML=`<article class="context-sheet"><h2>Lecturer context</h2><p class="context-note">Evidence excerpts used by the KCs below. Supplemental document input, not extracted PDF blocks. Page associations are recorded mappings, not new slides.</p>${rows.map(row=>`<section><div class="detail-label">${esc(row.kc.kc_id)} · ${esc(row.kc.name)}</div>${contextEvidenceHtml(row.kc)}</section>`).join('')}</article>`;return}const rows=scrollMode?pageRows:[pageRows[selected-1]];slideDeck.innerHTML=rows.map(row=>`<article class="slide-shell${row.page===selected?' active':''}" data-slide-page="${row.page}"><img src="pages/page-${String(row.page).padStart(4,'0')}.png" alt="Slide ${row.page}: ${esc(row.role)}"></article>`).join('')}
function kcCard(row){const k=row.kc,e=row.evidence,g=groups[k.group_id];return`<article class="kc-card"><button class="kc-button" aria-expanded="false"><div class="kc-top"><span class="kc-id">${esc(k.kc_id)}</span><span class="form">${esc(k.semantic_form)}</span></div><div class="kc-group">${esc(k.group_id)} · ${esc(g?.name||'')}</div><div class="kc-name">${esc(k.name)}</div></button><div class="kc-detail" hidden><div class="detail-label">Knowledge topic</div><div class="detail-copy">${esc(g?.name||k.group_id)}</div><div class="detail-label">Knowledge description</div><div class="detail-copy">${esc(k.knowledge_description)}</div><div class="detail-label">Observable learner response</div><div class="detail-copy">${esc(k.observable_claim)}</div>${pdfEvidenceHtml(k,e)}${contextEvidenceHtml(k)}<div class="detail-label">Assessment boundary</div><div class="boundary"><div><b>Included</b><ul>${k.assessment_boundary.included.map(x=>`<li>${esc(x)}</li>`).join('')||'<li>—</li>'}</ul></div><div><b>Excluded</b><ul>${k.assessment_boundary.excluded.map(x=>`<li>${esc(x)}</li>`).join('')||'<li>—</li>'}</ul></div></div></div></article>`}
function renderFloatingKcs(){if(!scrollMode)return;const audit=auditByPage[selected],unique=selectedKcRows();kcFloat.innerHTML=`<div class="float-head"><span class="float-slide">${contextSelected?'Lecturer context':'Slide '+selected}</span><span class="float-count">${unique.length?`${unique.length} KC`:'No KC'}</span></div>${unique.length?unique.map(row=>{const k=row.kc,e=row.evidence,g=groups[k.group_id];return`<article class="float-kc"><button class="float-kc-button" aria-expanded="false"><div class="float-kc-top"><span class="float-kc-id">${esc(k.kc_id)}</span><span class="float-kc-form">${esc(k.semantic_form)}</span></div><div class="float-kc-group">${esc(k.group_id)} · ${esc(g?.name||'')}</div><div class="float-kc-name">${esc(k.name)}</div><p class="float-kc-copy">${esc(k.knowledge_description)}</p></button><div class="float-kc-detail" hidden><div class="float-detail-label">Knowledge topic</div><div class="float-detail-copy">${esc(g?.name||k.group_id)}</div><div class="float-detail-label">Observable learner response</div><div class="float-detail-copy">${esc(k.observable_claim)}</div>${pdfEvidenceHtml(k,e,'float-detail')}${contextEvidenceHtml(k,'float-detail')}<div class="float-detail-label">Assessment boundary</div><div class="float-boundary"><div><b>Included</b><ul>${k.assessment_boundary.included.map(x=>`<li>${esc(x)}</li>`).join('')||'<li>—</li>'}</ul></div><div><b>Excluded</b><ul>${k.assessment_boundary.excluded.map(x=>`<li>${esc(x)}</li>`).join('')||'<li>—</li>'}</ul></div></div></div></article>`}).join(''):`<div class="float-empty"><b>${esc(audit.classification)}</b><br>${esc(audit.summary)}</div>`}`;kcFloat.querySelectorAll('.float-kc-button').forEach(button=>button.onclick=()=>{const detail=button.nextElementSibling,open=detail.hidden;detail.hidden=!open;button.setAttribute('aria-expanded',String(open))})}
function renderContextInspector(){const rows=selectedKcRows();pageTitle.textContent='Lecturer context';pageRole.textContent='Supplemental document input · not PDF extraction';pageStatus.className='status-pill context';pageStatus.textContent=`${rows.length} KC`;inspectorBody.innerHTML=`<div class="section-title">KCs using lecturer context</div>${rows.map(kcCard).join('')}`;inspectorBody.querySelectorAll('.kc-button').forEach(button=>button.onclick=()=>{const detail=button.nextElementSibling,open=detail.hidden;detail.hidden=!open;button.setAttribute('aria-expanded',String(open))})}
function renderInspector(){const page=source.pages[selected-1],audit=auditByPage[selected],evidence=evidenceByPage[selected]||[],uniqueKcs=[...new Map(evidence.map(x=>[x.kc.kc_id,x])).values()],uncovered=uncoveredByPage[selected]||[],warnings=warningsByPage[selected]||[],state=pageState(selected);pageTitle.textContent=`Slide ${selected}`;pageRole.textContent=page.role;pageStatus.className=`status-pill ${state}`;pageStatus.textContent=uniqueKcs.length?`${uniqueKcs.length} KC`:labels[state];let html=uniqueKcs.length?`<div class="section-title">KCs on this slide</div>${uniqueKcs.map(kcCard).join('')}`:`<div class="section-title">KC decision</div><div class="decision-card"><div class="decision-head">No KC generated<span class="decision-class">${esc(audit.classification)}</span></div><p class="decision-copy">${esc(audit.summary)}</p>${state==='review'?`<p class="decision-note">Review needed: this is learning/example content, but no KC cites it as evidence.</p>`:''}</div>`;html+=`<details class="source-check"><summary>Check against extraction</summary><p class="source-check-copy">${esc(stateHelp[state])}</p>`;if(uncovered.length)html+=`<div class="section-title">Uncovered content</div>${uncovered.map(x=>`<div class="notice red"><b>${x.block_ids.map(esc).join(' · ')}</b><p>${esc(x.description)}<br><span>${esc(x.reason)}</span></p></div>`).join('')}`;if(warnings.length)html+=`<div class="section-title">Warnings</div>${warnings.map(x=>`<div class="notice"><b>${esc(x.code)}</b><p>${esc(x.description)}</p></div>`).join('')}`;html+=`<div class="section-title">Extracted blocks</div>${page.blocks.map(b=>{const s=blockState(selected,b.block_id);return`<div class="block-card"><div class="block-top"><span class="block-id">${esc(b.block_id)}</span><span class="block-kind">${esc(b.kind)}</span><span class="block-state ${s}">${esc(labels[s])}</span></div><div class="block-content">${esc(compactContent(b.content))}</div></div>`}).join('')}<div class="footer-note"><a href="extraction-review.html?from=kc#${selected}">Open full extraction for this slide</a></div></details>`;inspectorBody.innerHTML=html;inspectorBody.querySelectorAll('.kc-button').forEach(b=>b.onclick=()=>{const d=b.nextElementSibling,open=d.hidden;d.hidden=!open;b.setAttribute('aria-expanded',String(open))})}
function render(){if(!scrollMode||contextSelected||!slideDeck.querySelector('[data-slide-page]'))renderDeck();position.textContent=contextSelected?'Lecturer context':`Slide ${selected} of ${source.source.page_count}`;previous.disabled=contextSelected||selected<=1;next.disabled=contextSelected||selected>=source.source.page_count;extractionLink.hidden=contextSelected;extractionLink.href=`extraction-review.html?from=kc#${selected}`;byId('contextLink').hidden=!contextKcs().length;byId('contextLink').textContent=contextSelected?'← Slides':'Lecturer context';byId('viewLink').href=`${scrollMode?'kc-recall':'kc-scroll'}.html#${contextSelected?'context':selected}`;slideDeck.querySelectorAll('[data-slide-page]').forEach(x=>x.classList.toggle('active',Number(x.dataset.slidePage)===selected));renderList();if(contextSelected)renderContextInspector();else renderInspector();renderFloatingKcs();requestAnimationFrame(()=>{if(!scrollMode||contextSelected||fit.disabled)setZoom(zoom);pageList.querySelector('.page-row.active')?.scrollIntoView({block:'nearest'})})}
function scrollToPage(page,behavior='smooth'){const target=slideDeck.querySelector(`[data-slide-page="${page}"]`);if(target){const top=target.getBoundingClientRect().top-slideDeck.getBoundingClientRect().top;if(behavior==='auto')slideScroll.scrollTop=top;else slideScroll.scrollTo({top,behavior})}}
function selectPage(page,shouldScroll=false,behavior='smooth'){const nextPage=Math.max(1,Math.min(source.source.page_count,page)),wasContext=contextSelected;if(wasContext&&!rowMatchesFilter(pageRows[nextPage-1])){filter='all';renderGroupFilters()}contextSelected=false;if(nextPage!==selected||wasContext){selected=nextPage;history.replaceState(null,'',`#${selected}`);render()}if(shouldScroll){scrollToPage(selected,behavior);if(wasContext&&scrollMode)Promise.all([...slideDeck.querySelectorAll('img')].map(img=>img.complete?Promise.resolve():new Promise(resolve=>{img.onload=resolve;img.onerror=resolve}))).then(()=>{if(!contextSelected)scrollToPage(selected,behavior)})}}
function selectContext(){if(!contextKcs().length)return;if(!contextKcs().some(contextMatchesFilter)){filter='all';renderGroupFilters()}contextSelected=true;history.replaceState(null,'','#context');render();slideScroll.scrollTop=0}
let scrollFrame=0,scrollTracking=false;
function syncPageFromScroll(){scrollFrame=0;const viewport=slideScroll.getBoundingClientRect(),shells=[...slideDeck.querySelectorAll('[data-slide-page]')];let best=null,bestVisible=-1;shells.forEach(shell=>{const rect=shell.getBoundingClientRect(),visible=Math.max(0,Math.min(rect.bottom,viewport.bottom)-Math.max(rect.top,viewport.top));if(visible>bestVisible){best=shell;bestVisible=visible}});if(best)selectPage(Number(best.dataset.slidePage),false)}
function fitSlideWidth(){return Math.max(240,Math.min(1100,slideScroll.clientWidth-56))}
function setZoom(value){fit.disabled=contextSelected;if(contextSelected){zoomOut.disabled=true;zoomIn.disabled=true;zoomLabel.textContent='—';return}zoom=Math.max(.5,Math.min(1.6,Math.round(value*10)/10));const width=`${Math.round(fitSlideWidth()*zoom)}px`;slideDeck.querySelectorAll('.slide-shell').forEach(shell=>shell.style.width=width);zoomLabel.textContent=`${Math.round(zoom*100)}%`;zoomOut.disabled=zoom<=.5;zoomIn.disabled=zoom>=1.6;if(scrollMode&&scrollTracking)requestAnimationFrame(()=>scrollToPage(selected,'auto'))}
function updatePanelToggles(){sidebarToggle.classList.toggle('active',workspace.classList.contains('left-closed'));inspectorToggle.classList.toggle('active',workspace.classList.contains('right-closed'))}
const panelStorageKey=name=>`kc-${scrollMode?'scroll-':''}${name}`;
function togglePanel(side){const name=side==='left'?'left-closed':'right-closed';workspace.classList.toggle(name);localStorage.setItem(panelStorageKey(name),String(workspace.classList.contains(name)));updatePanelToggles();requestAnimationFrame(()=>setZoom(zoom))}
function installResize(id,property,side,defaultWidth){const handle=document.getElementById(id);handle.addEventListener('pointerdown',event=>{event.preventDefault();const startX=event.clientX,startWidth=parseFloat(getComputedStyle(workspace).getPropertyValue(property));handle.classList.add('dragging');document.body.classList.add('resizing');const move=e=>{const delta=e.clientX-startX,width=side==='left'?startWidth+delta:startWidth-delta;workspace.style.setProperty(property,`${Math.max(220,Math.min(620,width))}px`)};const stop=()=>{handle.classList.remove('dragging');document.body.classList.remove('resizing');removeEventListener('pointermove',move);removeEventListener('pointerup',stop);localStorage.setItem(`kc-${property}`,getComputedStyle(workspace).getPropertyValue(property).trim())};addEventListener('pointermove',move);addEventListener('pointerup',stop)});handle.ondblclick=()=>{workspace.style.setProperty(property,`${defaultWidth}px`);localStorage.removeItem(`kc-${property}`)}}
function installScrollKcResize(){if(!scrollMode)return;const property='--scroll-kc-width',saved=localStorage.getItem('kc-scroll-width');if(saved)workspace.style.setProperty(property,saved);scrollKcResize.addEventListener('pointerdown',event=>{event.preventDefault();const startX=event.clientX,startWidth=parseFloat(getComputedStyle(workspace).getPropertyValue(property));scrollKcResize.classList.add('dragging');document.body.classList.add('resizing');const move=e=>{const width=Math.max(320,Math.min(Math.min(720,innerWidth*.58),startWidth-(e.clientX-startX)));workspace.style.setProperty(property,`${width}px`);setZoom(zoom)};const stop=()=>{scrollKcResize.classList.remove('dragging');document.body.classList.remove('resizing');removeEventListener('pointermove',move);removeEventListener('pointerup',stop);localStorage.setItem('kc-scroll-width',getComputedStyle(workspace).getPropertyValue(property).trim())};addEventListener('pointermove',move);addEventListener('pointerup',stop)});scrollKcResize.ondblclick=()=>{workspace.style.setProperty(property,'460px');localStorage.removeItem('kc-scroll-width');setZoom(zoom)}}
for(const [name,className] of [['left','left-closed'],['right','right-closed']])if((!scrollMode||name==='right')&&localStorage.getItem(panelStorageKey(className))==='true')workspace.classList.add(className);for(const property of ['--left-width','--right-width']){const saved=localStorage.getItem(`kc-${property}`);if(saved)workspace.style.setProperty(property,saved)}installResize('leftResize','--left-width','left',286);installResize('rightResize','--right-width','right',390);installScrollKcResize();
sourceSummary.textContent=`${source.source.page_count} slides · ${kcs.length} Leaf KCs · ${groupList.length} groups${contextKcs().length?' · '+contextKcs().length+' with lecturer context':''}`;
byId('contextLink').onclick=event=>{event.preventDefault();if(contextSelected)selectPage(selected,true);else selectContext()};
sidebarToggle.onclick=()=>togglePanel('left');sidebarCollapse.onclick=()=>togglePanel('left');inspectorToggle.onclick=()=>togglePanel('right');previous.onclick=()=>selectPage(selected-1,true);next.onclick=()=>selectPage(selected+1,true);zoomOut.onclick=()=>setZoom(zoom-.1);zoomIn.onclick=()=>setZoom(zoom+.1);fit.onclick=()=>setZoom(1);search.oninput=renderList;
const trackScroll=()=>{if(scrollMode&&!contextSelected&&scrollTracking&&!scrollFrame)scrollFrame=requestAnimationFrame(syncPageFromScroll)};
addEventListener('scroll',trackScroll,{passive:true});slideScroll.addEventListener('scroll',trackScroll,{passive:true});addEventListener('keydown',e=>{if(contextSelected||e.target.matches('input,textarea'))return;if(e.key==='ArrowLeft')selectPage(selected-1,true);if(e.key==='ArrowRight')selectPage(selected+1,true)});addEventListener('hashchange',()=>location.hash==='#context'?selectContext():selectPage(Number(location.hash.slice(1))||1,true));addEventListener('resize',()=>setZoom(zoom));renderGroupFilters();renderDeck();updatePanelToggles();render();
Promise.all([...slideDeck.querySelectorAll('img')].map(img=>img.complete?Promise.resolve():new Promise(resolve=>{img.onload=resolve;img.onerror=resolve}))).then(()=>{setZoom(1);if(scrollMode)requestAnimationFrame(()=>{render();setTimeout(()=>{if(!contextSelected)scrollToPage(selected,'auto');requestAnimationFrame(()=>{scrollTracking=true})},120)})});
</script></body></html>'''
    return (
        template.replace("__TITLE__", approved.source.filename)
        .replace("__MODEL__", str(candidate["model"]))
        .replace("__UPSTREAM__", upstream_status.replace("_", " "))
        .replace("__BODY_CLASS__", "scroll-mode" if scroll_mode else "")
        .replace("__VIEW__", "KC Scroll Review" if scroll_mode else "KC Recall Review")
        .replace("__VIEW_LINK__", "kc-recall.html#1" if scroll_mode else "kc-scroll.html#1")
        .replace("__VIEW_LABEL__", "Recall View" if scroll_mode else "Scroll View")
        .replace("__DATA__", _json_script(data))
    )


def _comparison_html(
    approved: ExtractedSource,
    candidates: list[dict[str, Any]],
    evaluation: dict[str, Any] | None,
    *,
    upstream_status: str = "HUMAN_APPROVED",
) -> str:
    data = {
        "source": approved.source.model_dump(mode="json"),
        "candidates": candidates,
        "evaluation": evaluation,
    }
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KC comparison — {approved.source.filename}</title>
<style>
:root{{--ink:#172033;--muted:#667085;--line:#d8dfeb;--soft:#f5f7fb;--blue:#315efb;--green:#067647;--warn:#b54708}}
*{{box-sizing:border-box}}body{{margin:0;font:15px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;color:var(--ink);background:#eef2f7}}
header{{position:sticky;top:0;z-index:8;background:#fff;border-bottom:1px solid var(--line);padding:16px 22px;display:flex;gap:18px;align-items:center;justify-content:space-between}}
h1,h2,h3,p{{margin:0}}h1{{font-size:21px}}button,a{{font:inherit}}a{{color:var(--blue);text-decoration:none}}.toplinks{{display:flex;gap:10px}}.btn{{padding:9px 12px;border:1px solid var(--line);border-radius:10px;background:#fff;color:var(--ink);cursor:pointer}}
.summary{{padding:18px 22px;background:#fff;border-bottom:1px solid var(--line)}}.metric-table{{width:100%;border-collapse:separate;border-spacing:0;margin-top:14px;border:1px solid var(--line);border-radius:12px;overflow:hidden}}.metric-table th,.metric-table td{{padding:9px 12px;border-bottom:1px solid var(--line);text-align:right}}.metric-table th:first-child,.metric-table td:first-child{{text-align:left}}.metric-table tr:last-child td{{border:0}}.metric-table th{{background:var(--soft)}}
.toolbar{{display:flex;gap:10px;padding:12px 22px;background:#fff;border-bottom:1px solid var(--line)}}input{{min-width:280px;padding:10px 12px;border:1px solid var(--line);border-radius:10px}}.grid{{display:grid;grid-template-columns:repeat(var(--cols),minmax(420px,1fr));gap:14px;padding:14px;align-items:start}}.candidate{{min-width:0}}.candidate-head{{position:sticky;top:73px;z-index:5;background:#172033;color:#fff;padding:13px 15px;border-radius:12px 12px 0 0;display:flex;justify-content:space-between}}.stack{{background:#fff;border:1px solid var(--line);border-top:0;border-radius:0 0 12px 12px;padding:12px;max-height:calc(100vh - 150px);overflow:auto}}
.group{{border:1px solid var(--line);border-radius:12px;margin-bottom:12px;overflow:hidden}}.group>summary{{cursor:pointer;padding:12px;background:var(--soft);font-weight:700}}.kc{{margin:10px;border:1px solid var(--line);border-radius:10px;padding:12px}}.kc h3{{font-size:16px;margin-bottom:7px}}.badge{{display:inline-block;padding:2px 7px;border-radius:999px;background:#eaf0ff;color:#2847b8;font-size:12px;margin-right:5px}}.label{{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:700;margin-top:9px}}.boundary{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}.box{{background:var(--soft);border-radius:8px;padding:8px}}.evidence{{border-left:3px solid var(--blue);padding:7px 9px;margin-top:7px;background:#f8faff}}.muted{{color:var(--muted)}}.warning{{color:var(--warn)}}.empty{{padding:20px;color:var(--muted)}}pre{{white-space:pre-wrap;word-break:break-word;background:#101828;color:#e7eefc;padding:12px;border-radius:10px;max-height:420px;overflow:auto}}dialog{{width:min(900px,90vw);border:0;border-radius:14px;box-shadow:0 24px 80px #0005}}dialog::backdrop{{background:#0007}}.dialog-head{{display:flex;justify-content:space-between;margin-bottom:10px}}@media(max-width:900px){{.grid{{grid-template-columns:1fr}}.candidate-head{{position:static}}.stack{{max-height:none}}}}
</style></head><body>
<header><div><h1>KC comparison</h1><p class="muted">{approved.source.filename} · {approved.source.page_count} pages · upstream extraction: {upstream_status.replace("_", " ")} · raw outputs are unchanged</p></div><div class="toplinks"><a class="btn" href="index.html">Overview</a><a class="btn" href="kc-recall.html#1">Recall Review</a><a class="btn" href="extraction-review.html#1">Extraction</a></div></header>
<section class="summary"><h2>Run metrics</h2><p class="muted">Block-reference rate and conditional-claim rate are diagnostics, not accuracy scores.</p><div id="metrics"></div><div id="quality"></div></section>
<div class="toolbar"><input id="search" placeholder="Search KC, group, evidence…"><button class="btn" id="expand">Expand all</button><button class="btn" id="collapse">Collapse all</button></div>
<main class="grid" id="grid" style="--cols:{max(1, len(candidates))}"></main>
<dialog id="raw"><div class="dialog-head"><h2 id="rawTitle">Raw JSON</h2><button class="btn" onclick="raw.close()">Close</button></div><button class="btn" id="copyRaw">Copy JSON</button><pre id="rawText"></pre></dialog>
<script>const DATA={_json_script(data)};
const metricsNode=document.getElementById('metrics'),qualityNode=document.getElementById('quality'),searchInput=document.getElementById('search'),expandButton=document.getElementById('expand'),collapseButton=document.getElementById('collapse');
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
const fmt=v=>v==null?'—':typeof v==='number'?Number.isInteger(v)?v.toLocaleString():v.toFixed(3):v;
const metricRows=[['Contract valid','contract_valid'],['Leaf KCs','leaf_kcs'],['Groups','groups'],['Pages with KCs','pages_with_kcs'],['PDF evidence records','evidence_records'],['Lecturer context evidence','context_evidence_records'],['Context-only KCs','context_only_kcs'],['Referenced source blocks','referenced_source_blocks'],['Block reference rate','block_reference_rate','pct'],['Multi-page KCs','multi_page_kcs'],['Conditional claim rate','conditional_claim_rate','pct'],['Warnings','warnings'],['Uncovered items','uncovered_items'],['Input tokens','input_tokens'],['Cached input tokens','cached_input_tokens'],['Output tokens','output_tokens'],['Reasoning tokens','reasoning_tokens'],['Total tokens','total_tokens'],['Model time (s)','model_seconds'],['Reported cost (USD)','cost_usd']];
metricsNode.innerHTML='<table class="metric-table"><thead><tr><th>Metric</th>'+DATA.candidates.map(c=>`<th>${{esc(c.model)}}</th>`).join('')+'</tr></thead><tbody>'+metricRows.map(([label,key,kind])=>'<tr><td>'+label+'</td>'+DATA.candidates.map(c=>`<td>${{kind==='pct'?(100*(c.metrics[key]||0)).toFixed(1)+'%':fmt(c.metrics[key])}}</td>`).join('')+'</tr>').join('')+'</tbody></table>';
if(DATA.evaluation){{const e=DATA.evaluation;const ids=['source_grounding','kc_eligibility','granularity','observable_claim','assessment_boundary','coverage_accountability','grouping_coherence','total'];qualityNode.innerHTML=`<h2 style="margin-top:22px">Locked-rubric quality assessment</h2><p class="muted">Quality excludes model identity, speed, tokens, cost, and raw KC count by itself. Raw outputs were not edited.</p><table class="metric-table"><thead><tr><th>Criterion</th>${{DATA.candidates.map(c=>`<th>${{esc(c.model)}}</th>`).join('')}}</tr></thead><tbody>${{ids.map(id=>`<tr><td>${{esc(id.replaceAll('_',' '))}}</td>${{DATA.candidates.map(c=>`<td>${{fmt(e.scores[c.model]?.[id])}}</td>`).join('')}}</tr>`).join('')}}</tbody></table><div class="box" style="margin-top:12px"><b>Recommendation: ${{esc(e.verdict.recommended_candidate)}}</b><br>${{esc(e.verdict.reason)}}<br><span class="warning">Human review is still required.</span></div>`}}
function evidence(e){{return `<div class="evidence"><b>${{esc(e.evidence_id)}}</b> · <a href="extraction-review.html#${{e.page}}">Page ${{e.page}}</a> · ${{e.block_ids.map(x=>`<span class="badge">${{esc(x)}}</span>`).join('')}}<div>${{esc(e.supports)}}</div><div class="muted">${{esc(e.description)}}</div></div>`}}
function contextEvidence(e){{return `<div class="evidence"><b>${{esc(e.context_id)}}</b> · Lecturer context<div>${{esc(e.excerpt||e.description||'')}}</div><div>${{esc(e.supports)}}</div><div class="muted">${{e.pages?.length?'Related PDF pages: '+e.pages.map(esc).join(', '):e.mapping_method==='document_level'?'Document-level context · no PDF page':'Unmapped context · no PDF page'}} · ${{esc(e.mapping_method)}} · ${{esc(e.mapping_confidence)}}</div></div>`}}
function kcCard(k){{return `<article class="kc" data-search="${{esc(JSON.stringify(k).toLowerCase())}}"><h3>${{esc(k.kc_id)}} · ${{esc(k.name)}}</h3><span class="badge">${{esc(k.semantic_form)}}</span>${{k.warning_codes.map(x=>`<span class="badge warning">${{esc(x)}}</span>`).join('')}}<div class="label">Knowledge</div><div>${{esc(k.knowledge_description)}}</div><div class="label">Observable claim</div><div>${{esc(k.observable_claim)}}</div><div class="label">Boundary</div><div class="boundary"><div class="box"><b>Included</b><br>${{k.assessment_boundary.included.map(esc).join('<br>')||'—'}}</div><div class="box"><b>Excluded</b><br>${{k.assessment_boundary.excluded.map(esc).join('<br>')||'—'}}</div></div><div class="label">PDF evidence</div>${{k.source_evidence.map(evidence).join('')||'<div class="muted">No PDF evidence</div>'}}${{(k.context_evidence||[]).length?'<div class="label">Lecturer context · separate from extraction</div>'+k.context_evidence.map(contextEvidence).join(''):''}}</article>`}}
function candidate(c){{const byId=Object.fromEntries(c.proposed.leaf_kcs.map(k=>[k.kc_id,k]));const groups=c.proposed.kc_groups.map(g=>`<details class="group" open data-search="${{esc(JSON.stringify(g).toLowerCase())}}"><summary>${{esc(g.group_id)}} · ${{esc(g.name)}} <span class="muted">(${{g.leaf_kc_ids.length}})</span></summary>${{g.leaf_kc_ids.map(id=>kcCard(byId[id])).join('')}}</details>`).join('');return `<section class="candidate"><div class="candidate-head"><b>${{esc(c.model)}}</b><button class="btn" onclick="showRaw('${{esc(c.id)}}')">Raw JSON</button></div><div class="stack">${{groups||'<div class="empty">No KC groups</div>'}}${{c.proposed.generation_warnings.length?'<h3 class="warning">Warnings</h3>'+c.proposed.generation_warnings.map(w=>`<div class="evidence warning">${{esc(w.code)}} · ${{esc(w.description)}}</div>`).join(''):''}}${{c.proposed.uncovered_content.length?'<h3>Uncovered content</h3>'+c.proposed.uncovered_content.map(u=>`<div class="evidence"><a href="extraction-review.html#${{u.page}}">Page ${{u.page}}</a> · ${{esc(u.description)}}<div class="muted">${{esc(u.reason)}}</div></div>`).join(''):''}}</div></section>`}}
grid.innerHTML=DATA.candidates.map(candidate).join('');
searchInput.addEventListener('input',()=>{{const q=searchInput.value.trim().toLowerCase();document.querySelectorAll('.kc').forEach(card=>card.hidden=Boolean(q&&!card.dataset.search.includes(q)));document.querySelectorAll('.group').forEach(group=>{{const groupMatch=Boolean(q&&group.dataset.search.includes(q));if(groupMatch)group.querySelectorAll('.kc').forEach(card=>card.hidden=false);group.hidden=Boolean(q&&!groupMatch&&![...group.querySelectorAll('.kc')].some(card=>!card.hidden));}})}});expandButton.onclick=()=>document.querySelectorAll('details').forEach(x=>x.open=true);collapseButton.onclick=()=>document.querySelectorAll('details').forEach(x=>x.open=false);
function showRaw(id){{const c=DATA.candidates.find(x=>x.id===id);rawTitle.textContent='Raw JSON — '+c.model;rawText.textContent=JSON.stringify(c.proposed,null,2);raw.showModal()}}copyRaw.onclick=()=>navigator.clipboard.writeText(rawText.textContent);
</script></body></html>"""


def _overview_html(
    approved: ExtractedSource,
    candidates: list[dict[str, Any]],
    selected: dict[str, Any],
    *,
    upstream_status: str = "HUMAN_APPROVED",
) -> str:
    cards = (
        "".join(
            f"""<div class="candidate"><b>{item["model"]}</b><span>PROPOSED</span><small>{item["metrics"]["leaf_kcs"]} Leaf KCs · {item["metrics"]["total_tokens"] or "—"} tokens · {item["metrics"]["model_seconds"] or "—"}s</small></div>"""
            for item in candidates
        )
        or '<div class="candidate"><b>KC candidates</b><span>NOT STARTED</span></div>'
    )
    extraction_label = upstream_status.replace("_", " ")
    extraction_copy = (
        "Source-faithful semantic JSON reviewed and approved."
        if upstream_status == "HUMAN_APPROVED"
        else "Proposed extraction used for an explicit demo only; human approval is still required."
    )
    status_class = "status" if upstream_status == "HUMAN_APPROVED" else "status warning"
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Learning Authoring — {approved.source.filename}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#eef2f7;color:#172033;font:16px/1.5 Inter,system-ui,sans-serif}}main{{max-width:1050px;margin:0 auto;padding:42px 22px}}h1{{margin:0;font-size:34px}}.sub{{color:#667085;margin:5px 0 30px}}.flow{{display:grid;grid-template-columns:1fr 54px 1fr 54px 1fr 54px 1fr;align-items:center}}.stage{{background:#fff;border:1px solid #d8dfeb;border-radius:16px;padding:20px;min-height:170px;box-shadow:0 8px 28px #1720330d}}.stage.active{{border:2px solid #315efb}}.arrow{{text-align:center;font-size:28px;color:#98a2b3}}.status{{display:inline-block;font-size:12px;font-weight:700;padding:3px 8px;border-radius:99px;background:#dff7eb;color:#067647}}.status.warning{{background:#fff1d8;color:#9b5700}}.pending{{background:#f2f4f7;color:#667085}}a{{display:inline-block;margin-top:18px;margin-right:6px;padding:9px 12px;border-radius:10px;background:#315efb;color:#fff;text-decoration:none}}a.secondary{{background:#fff;color:#315efb;border:1px solid #b8c6f7}}.candidate{{border-top:1px solid #eaecf0;padding:8px 0;display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px}}.candidate:first-of-type{{margin-top:12px}}.candidate span{{font-size:11px;color:#b54708}}.candidate small{{flex-basis:100%;color:#667085}}@media(max-width:850px){{.flow{{grid-template-columns:1fr}}.arrow{{transform:rotate(90deg)}}}}</style></head><body><main><h1>Learning Authoring Tool</h1><div class="sub">{approved.source.filename} · {approved.source.page_count} pages · standalone pipeline</div><div class="flow"><section class="stage"><span class="{status_class}">{extraction_label}</span><h2>Extraction</h2><p>{extraction_copy}</p><a href="extraction-review.html#1">Open extraction</a></section><div class="arrow">→</div><section class="stage active"><span class="status warning">HUMAN REVIEW</span><h2>Knowledge Components</h2>{cards}<p><b>{selected["model"]}</b> is selected for source-first recall review.</p><a href="kc-recall.html#1">Open Recall Review</a><a class="secondary" href="kc-comparison.html">Compare candidates</a></section><div class="arrow">→</div><section class="stage"><span class="status pending">NOT STARTED</span><h2>Quiz</h2><p>Consumes reviewed KCs; no KC approval is implied here.</p></section><div class="arrow">→</div><section class="stage"><span class="status pending">NOT STARTED</span><h2>Mastery</h2><p>Consumes approved Quiz/KC contracts.</p></section></div></main></body></html>"""


def _load_review_extraction(
    run_path: Path,
    *,
    allow_proposed_extraction_demo: bool,
) -> tuple[ExtractedSource, str]:
    artifacts = RunArtifacts(run_path)
    has_approval = artifacts.approval.is_file()
    has_approved = artifacts.approved.is_file()
    if has_approval or has_approved:
        if not (has_approval and has_approved):
            raise RuntimeError("extraction approval boundary is incomplete")
        approved, _, _ = load_approved_extraction(run_path)
        return approved, "HUMAN_APPROVED"
    if not allow_proposed_extraction_demo:
        raise RuntimeError(
            "KC review requires an approved extraction; pass "
            "--allow-proposed-extraction-demo only for a visibly marked demo"
        )
    if not artifacts.proposed.is_file():
        raise RuntimeError(f"proposed extraction is missing: {artifacts.proposed}")
    proposed = ExtractedSource.model_validate(read_json(artifacts.proposed))
    manifest_source = SourceDescriptor.model_validate(read_json(artifacts.source_manifest)["source"])
    if proposed.source != manifest_source:
        raise RuntimeError("proposed extraction does not match the code-owned source manifest")
    return proposed, "PROPOSED_DEMO_ONLY"


def build_kc_demo(
    run_dir: Path,
    candidate_dirs: list[Path] | None = None,
    *,
    allow_proposed_extraction_demo: bool = False,
) -> dict[str, Any]:
    run_path = run_dir.expanduser().resolve()
    approved, upstream_status = _load_review_extraction(
        run_path,
        allow_proposed_extraction_demo=allow_proposed_extraction_demo,
    )
    paths = (
        [path.expanduser().resolve() for path in candidate_dirs]
        if candidate_dirs
        else _discover_candidates(run_path)
    )
    candidates = [_load_candidate(path, approved, run_dir=run_path) for path in paths]
    if not candidates:
        raise RuntimeError("no complete KC candidate outputs found")
    evaluation_path = run_path / "kc-quality-evaluation.json"
    evaluation = read_json(evaluation_path) if evaluation_path.is_file() else None
    selected = _selected_candidate(candidates, evaluation)
    comparison = run_path / "kc-comparison.html"
    recall = run_path / "kc-recall.html"
    scroll = run_path / "kc-scroll.html"
    overview = run_path / "index.html"
    write_text(
        comparison,
        _comparison_html(
            approved,
            candidates,
            evaluation,
            upstream_status=upstream_status,
        ),
    )
    write_text(recall, _recall_html(approved, selected, upstream_status=upstream_status))
    write_text(
        scroll,
        _recall_html(
            approved,
            selected,
            scroll_mode=True,
            upstream_status=upstream_status,
        ),
    )
    write_text(
        overview,
        _overview_html(
            approved,
            candidates,
            selected,
            upstream_status=upstream_status,
        ),
    )
    build_review(run_path)
    return {
        "overview": str(overview),
        "comparison": str(comparison),
        "recall": str(recall),
        "scroll": str(scroll),
        "selected_model": selected["model"],
        "upstream_extraction_status": upstream_status,
        "candidate_count": len(candidates),
        "models": [candidate["model"] for candidate in candidates],
        "metrics": {candidate["model"]: candidate["metrics"] for candidate in candidates},
    }
