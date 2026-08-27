"""Deterministic PDF source gate with safe partial-run reuse."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from learning_authoring.artifacts import (
    RunArtifacts,
    read_json,
    sha256_file,
    write_json,
    write_text,
)
from learning_authoring.contracts import SourceDescriptor
from learning_authoring.rendering import write_pdfium_png

DEFAULT_RENDER_DPI = 160


def _notify(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _validate_input(pdf_path: Path, render_dpi: int) -> Path:
    source = pdf_path.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError(f"expected an existing PDF: {source}")
    if render_dpi <= 0:
        raise ValueError("render_dpi must be positive")
    return source


def inspect_pdf(pdf_path: Path) -> dict[str, Any]:
    """Return deterministic source metadata without modifying the PDF."""

    source = _validate_input(pdf_path, render_dpi=1)
    source_hash = sha256_file(source)
    try:
        document = pdfium.PdfDocument(source)
    except Exception as exc:
        raise ValueError(f"invalid PDF: {source}") from exc
    try:
        page_count = len(document)
    finally:
        document.close()
    if page_count < 1:
        raise ValueError(f"PDF has no pages: {source}")
    return {
        "filename": source.name,
        "absolute_path": str(source),
        "size_bytes": source.stat().st_size,
        "sha256": source_hash,
        "page_count": page_count,
    }


def preflight_source(
    pdf_path: Path,
    run_dir: Path,
    *,
    render_dpi: int = DEFAULT_RENDER_DPI,
) -> dict[str, Any]:
    """Inspect one PDF and intended run directory without writing either one."""

    if render_dpi <= 0:
        raise ValueError("render_dpi must be positive")
    metadata = inspect_pdf(pdf_path)
    source = Path(metadata["absolute_path"])
    output = run_dir.expanduser().resolve()
    if not output.exists():
        state = "fresh"
        detail = "run directory does not exist"
    elif not output.is_dir():
        state = "conflict"
        detail = "run path exists and is not a directory"
    elif not any(output.iterdir()):
        state = "empty"
        detail = "run directory exists and is empty"
    else:
        artifacts = RunArtifacts(output)
        identity_path = artifacts.source_manifest
        if not identity_path.is_file():
            identity_path = artifacts.source_preparation
        if not identity_path.is_file():
            state = "conflict"
            detail = "non-empty run directory has no source identity artifact"
        elif identity_path == artifacts.source_manifest:
            try:
                descriptor, _ = _reuse_source(
                    source,
                    artifacts,
                    render_dpi=render_dpi,
                    page_count=metadata["page_count"],
                )
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                state = "conflict"
                detail = str(exc)
            else:
                state = "reusable-same-source"
                detail = f"{identity_path.name} matches {descriptor.page_count}-page source"
        else:
            try:
                identity = read_json(identity_path)
                existing_hash = identity["source_sha256"]
                existing_dpi = identity["render_dpi"]
            except (KeyError, OSError, TypeError, ValueError):
                state = "conflict"
                detail = f"invalid source identity artifact: {identity_path.name}"
            else:
                stored_source = artifacts.source_pdf
                stored_matches = not stored_source.exists() or (
                    stored_source.is_file() and sha256_file(stored_source) == metadata["sha256"]
                )
                reusable = (
                    existing_hash == metadata["sha256"]
                    and existing_dpi == render_dpi
                    and stored_matches
                )
                state = "reusable-same-source" if reusable else "conflict"
                detail = f"{identity_path.name} matches resumable source" if reusable else (
                    f"{identity_path.name} source, DPI, or stored PDF conflicts"
                )

    return {
        **metadata,
        "render_dpi": render_dpi,
        "run_dir": str(output),
        "run_dir_state": state,
        "run_dir_detail": detail,
        "ready": state != "conflict",
    }


def _png_complete(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 20:
        return False
    with path.open("rb") as handle:
        if handle.read(8) != b"\x89PNG\r\n\x1a\n":
            return False
        handle.seek(-12, 2)
        return handle.read() == b"\x00\x00\x00\x00IEND\xaeB`\x82"


def _copy_source_atomic(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.preparing")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def _reuse_source(
    source: Path,
    artifacts: RunArtifacts,
    *,
    render_dpi: int,
    page_count: int,
) -> tuple[SourceDescriptor, dict[str, Any]]:
    manifest = read_json(artifacts.source_manifest)
    descriptor = SourceDescriptor.model_validate(manifest["source"])
    if descriptor.sha256 != sha256_file(source):
        raise RuntimeError("run directory belongs to a different source PDF")
    if descriptor.page_count != page_count:
        raise RuntimeError("source manifest page count differs from input PDF")
    if sha256_file(artifacts.source_pdf) != descriptor.sha256:
        raise RuntimeError("stored source PDF no longer matches its manifest")
    if manifest.get("render_dpi") != render_dpi:
        raise RuntimeError("run directory was prepared with a different render DPI")
    expected = [
        artifacts.run_dir / "pages" / f"page-{page:04d}.png"
        for page in range(1, descriptor.page_count + 1)
    ]
    if not all(_png_complete(path) for path in expected):
        raise RuntimeError("completed run has a missing or invalid rendered page image")
    return descriptor, manifest


def prepare_or_reuse_source(
    pdf_path: Path,
    run_dir: Path,
    *,
    render_dpi: int,
    progress: Callable[[str], None] | None = None,
) -> tuple[SourceDescriptor, dict[str, Any], bool]:
    """Prepare a source package, or validate and reuse an interrupted one."""

    if render_dpi <= 0:
        raise ValueError("render_dpi must be positive")
    metadata = inspect_pdf(pdf_path)
    source = Path(metadata["absolute_path"])
    output = run_dir.expanduser().resolve()
    artifacts = RunArtifacts(output)
    source_hash = metadata["sha256"]
    if artifacts.source_manifest.is_file():
        descriptor, manifest = _reuse_source(
            source,
            artifacts,
            render_dpi=render_dpi,
            page_count=metadata["page_count"],
        )
        _notify(progress, f"[source] REUSED: {descriptor.page_count} rendered pages")
        return descriptor, manifest, True

    started = time.perf_counter()
    preparation_resumed = artifacts.source_preparation.is_file()
    if output.exists() and any(output.iterdir()) and not preparation_resumed:
        raise FileExistsError(f"run directory has no reusable source preparation: {output}")
    output.mkdir(parents=True, exist_ok=True)
    pages_dir = output / "pages"
    text_dir = output / "text-audit"
    if preparation_resumed:
        preparation = read_json(artifacts.source_preparation)
        if preparation.get("source_sha256") != source_hash:
            raise RuntimeError("partial source preparation belongs to a different PDF")
        if preparation.get("render_dpi") != render_dpi:
            raise RuntimeError("partial source preparation uses a different render DPI")
    else:
        write_json(
            artifacts.source_preparation,
            {
                "preparation_version": "source-preparation.v1",
                "source_sha256": source_hash,
                "source_filename": source.name,
                "render_dpi": render_dpi,
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
    pages_dir.mkdir(exist_ok=True)
    text_dir.mkdir(exist_ok=True)
    if artifacts.source_pdf.is_file():
        if sha256_file(artifacts.source_pdf) != source_hash:
            raise RuntimeError("partial stored PDF differs from the input PDF")
    else:
        _copy_source_atomic(source, artifacts.source_pdf)
    if sha256_file(artifacts.source_pdf) != source_hash:
        raise RuntimeError("stored PDF hash differs from input PDF")

    document = pdfium.PdfDocument(artifacts.source_pdf)
    page_count = len(document)
    if page_count != metadata["page_count"]:
        document.close()
        raise RuntimeError("stored PDF page count changed after source inspection")
    page_records: list[dict[str, Any]] = []
    text_layer_pages = 0
    try:
        for index in range(page_count):
            page_number = index + 1
            text_path = text_dir / f"page-{page_number:04d}.txt"
            image_path = pages_dir / f"page-{page_number:04d}.png"
            if not text_path.is_file() or not _png_complete(image_path):
                page = document[index]
                try:
                    if not text_path.is_file():
                        text_page = page.get_textpage()
                        try:
                            text = text_page.get_text_range()
                        finally:
                            text_page.close()
                        write_text(text_path, text)
                    if not _png_complete(image_path):
                        bitmap = page.render(scale=render_dpi / 72)
                        try:
                            write_pdfium_png(bitmap, image_path)
                        finally:
                            bitmap.close()
                finally:
                    page.close()
            text = text_path.read_text(encoding="utf-8")
            if text.strip():
                text_layer_pages += 1
            page_records.append(
                {
                    "page": page_number,
                    "image_ref": str(image_path.relative_to(output)),
                    "image_sha256": sha256_file(image_path),
                    "text_audit_ref": str(text_path.relative_to(output)),
                    "text_audit_sha256": sha256_file(text_path),
                }
            )
    finally:
        document.close()

    descriptor = SourceDescriptor(
        source_id=f"sha256:{source_hash[:16]}",
        filename=source.name,
        sha256=source_hash,
        page_count=page_count,
    )
    manifest = {
        "manifest_version": "source-package.v2",
        "source": descriptor.model_dump(mode="json"),
        "stored_pdf": "source.pdf",
        "page_image_pattern": "pages/page-NNNN.png",
        "text_audit_pattern": "text-audit/page-NNNN.txt",
        "text_audit_role": "diagnostic_only_not_model_input",
        "page_records": page_records,
        "rendered_page_count": len(page_records),
        "text_layer_page_count": text_layer_pages,
        "render_dpi": render_dpi,
        "status": "SOURCE_READY",
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_json(artifacts.source_manifest, manifest)
    artifacts.source_preparation.unlink(missing_ok=True)
    _notify(progress, f"[source] READY: {page_count}/{page_count} rendered pages")
    return descriptor, manifest, preparation_resumed
