"""Source-bound quiz figures, embedded in the portal without altering candidates."""

from __future__ import annotations

import base64
import json
from pathlib import Path, PurePosixPath
from typing import Any

import pypdfium2 as pdfium

from learning_authoring.artifacts import read_json, sha256_file
from learning_authoring.quiz_contracts import QuizBatch
from learning_authoring.rendering import pdfium_png_bytes
from learning_authoring.source_bundle import load_source_bundle

QUIZ_STIMULUS_RENDERER = r"""
function renderQuizStimulus(s, images=[]){
  const escapes={'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
  const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>escapes[c]);
  const sameCrop=(a,b)=>a==null?b==null:b!=null&&['x','y','w','h'].every(k=>a[k]===b[k]);
  if(!s||s.kind==='none')return '';
  return (s.kind==='composite'?s.blocks:[s]).map(p=>{
    if(p.kind==='image'){
      const asset=images.find(i=>i.asset_id===p.asset_id&&sameCrop(i.crop,p.crop));
      if(!asset||!/^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(asset.data_url))
        return '<p role="alert">Thiếu hình nguồn — câu hỏi chưa đủ dữ kiện để làm.</p>';
      return '<figure class="stimulus"><img style="max-width:100%;height:auto" src="'
        +asset.data_url+'" alt="'+esc(p.alt)+'"></figure>';
    }
    if(p.kind==='table'){
      const head=p.table_columns.map(c=>'<th>'+esc(c)+'</th>').join('');
      const rows=p.table_rows.map(r=>'<tr>'+r.map(c=>'<td>'+esc(c)+'</td>').join('')
        +'</tr>').join('');
      return '<div class="stimulus" style="overflow-x:auto"><table class="data-table">'
        +'<thead><tr>'+head+'</tr></thead><tbody>'+rows+'</tbody></table></div>';
    }
    return '<div class="stimulus" style="white-space:pre-wrap">'
      +esc(p.kind==='formula'?p.formula:p.text)+'</div>';
  }).join('');
}
"""


def _inside(root: Path, ref: str) -> Path:
    posix = PurePosixPath(ref)
    path = root / ref
    if (
        not ref
        or posix.is_absolute()
        or ".." in posix.parts
        or "\\" in ref
        or str(posix) != ref
        or path.is_symlink()
        or not path.resolve().is_relative_to(root.resolve())
    ):
        raise ValueError("quiz media must stay inside its source run")
    return path


def build_media_catalog(root: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose only the source pages cited by the selected KCs, never arbitrary paths."""
    if payload["source_ref"].get("source_bundle_sha256"):
        bundle = load_source_bundle(root)
        sources = [(e.source.source_id, root / e.run_ref) for e in bundle.sources]
    else:
        sources = [(None, root)]
    cited = {
        (e.get("source_id"), e["page"]) for kc in payload["leaf_kcs"] for e in kc["source_evidence"]
    }
    assets = []
    for source_id, run in sources:
        manifest = read_json(run / "source-manifest.json")
        source = manifest["source"]
        for record in manifest.get("page_records", []):
            if (source_id, record["page"]) not in cited:
                continue
            image = _inside(run, record["image_ref"])
            pdf = _inside(run, manifest.get("stored_pdf", "source.pdf"))
            assets.append(
                {
                    "asset_id": f"{source['source_id']}:page:{record['page']}",
                    "source_id": source_id,
                    "page": record["page"],
                    "image_ref": image.relative_to(root).as_posix(),
                    "image_sha256": record["image_sha256"],
                    "pdf_ref": pdf.relative_to(root).as_posix(),
                    "pdf_sha256": source["sha256"],
                    "render_dpi": manifest.get("render_dpi", 160),
                }
            )
    return assets


def render_quiz_images(
    root: Path,
    payload: dict[str, Any],
    batch: QuizBatch,
) -> list[dict[str, Any]]:
    """Validate chosen source bytes; render crops in top-left normalized coordinates.

    Inline PNGs keep exports portable and avoid publishing unrelated source files.
    A missing/changed image is an error, not a silent fallback to a broken figure.
    """
    batch.validate_against_input(payload)
    catalog = {a["asset_id"]: a for a in payload.get("media_assets", [])}
    rendered: dict[str, dict[str, Any]] = {}
    verified: set[tuple[str, str]] = set()
    for question in batch.questions:
        for part in question.stimulus.parts():
            if part.kind != "image":
                continue
            crop = part.crop.model_dump() if part.crop else None
            key = json.dumps([part.asset_id, crop], sort_keys=True)
            if key in rendered:
                continue
            asset = catalog[part.asset_id]
            image = _inside(root, asset["image_ref"])
            files = [(image, asset["image_sha256"])]
            if crop:
                pdf = _inside(root, asset["pdf_ref"])
                files.append((pdf, asset["pdf_sha256"]))
            for path, digest in files:
                binding = (str(path), digest)
                if binding not in verified:
                    if not path.is_file() or sha256_file(path) != digest:
                        raise ValueError(f"quiz source media missing or changed: {path.name}")
                    verified.add(binding)
            if crop:
                with pdfium.PdfDocument(pdf) as document:
                    page = document[asset["page"] - 1]
                    try:
                        width, height = page.get_size()
                        margins = (
                            crop["x"] * width,
                            (1 - crop["y"] - crop["h"]) * height,
                            (1 - crop["x"] - crop["w"]) * width,
                            crop["y"] * height,
                        )
                        bitmap = page.render(
                            scale=asset["render_dpi"] / 72,
                            crop=tuple(max(0, margin) for margin in margins),
                        )
                        try:
                            data = pdfium_png_bytes(bitmap)
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
            else:
                data = image.read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("quiz figure must be a source PNG")
            rendered[key] = {
                "asset_id": part.asset_id,
                "crop": crop,
                "data_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
            }
    return list(rendered.values())
