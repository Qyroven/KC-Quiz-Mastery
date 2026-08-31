from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from learning_authoring.artifacts import write_json
from learning_authoring.contracts import (
    ExtractedPage,
    ExtractedSourcePayload,
    PageNote,
    SemanticBlock,
    SourceDescriptor,
    SourceRegion,
    WarningRecord,
)


@pytest.fixture
def source() -> SourceDescriptor:
    return SourceDescriptor(
        source_id="sha256:aaaaaaaaaaaaaaaa",
        filename="lesson.pdf",
        sha256="a" * 64,
        page_count=2,
    )


def block(block_id: str, page: int, content: str = "Source content") -> SemanticBlock:
    return SemanticBlock(
        block_id=block_id,
        kind="text",
        content=content,
        region=SourceRegion(
            page=page,
            geometry={"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.2},
        ),
    )


def page(
    page_number: int,
    block_id: str,
    *,
    warning: bool = False,
) -> ExtractedPage:
    warnings = (
        [
            WarningRecord(
                code="UNCLEAR",
                message="Needs repair",
                page=page_number,
                block_ids=[block_id],
                details={"repair_recommended": True, "review_disposition": "review"},
            )
        ]
        if warning
        else []
    )
    return ExtractedPage(
        page_number=page_number,
        role="lesson",
        blocks=[block(block_id, page_number)],
        reading_order=[block_id],
        page_note=PageNote(summary="Page summary", evidence_block_ids=[block_id]),
        warnings=warnings,
    )


def payload(*, warning_page: int | None = None) -> ExtractedSourcePayload:
    return ExtractedSourcePayload(
        schema_version="extracted-source.v2",
        pages=[
            page(1, "b1", warning=warning_page == 1),
            page(2, "b2", warning=warning_page == 2),
        ],
    )


def make_run_dir(path: Path, source: SourceDescriptor) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pages").mkdir()
    (path / "text-audit").mkdir()
    (path / "source.pdf").write_bytes(b"%PDF fake test source")
    for page_number in range(1, source.page_count + 1):
        (path / "pages" / f"page-{page_number:04d}.png").write_bytes(b"png")
        (path / "text-audit" / f"page-{page_number:04d}.txt").write_text(
            "Source content", encoding="utf-8"
        )
    write_json(
        path / "source-manifest.json",
        {
            "manifest_version": "source-package.v2",
            "source": source.model_dump(mode="json"),
            "render_dpi": 160,
        },
    )


def write_blank_pdf(path: Path) -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode())
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(payload)


def write_text_pdf(path: Path) -> None:
    """Write a tiny dependency-free PDF with two native text lines."""

    content = b"BT /F1 18 Tf 20 160 Td (Native title) Tj 0 -32 Td (Second source line) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode())
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(payload)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
