"""Dependency-light PDFium bitmap rendering."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pypdfium2 as pdfium

from learning_authoring.artifacts import write_bytes


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


def pdfium_png_bytes(bitmap: pdfium.PdfBitmap) -> bytes:
    if bitmap.mode not in {"BGR", "BGRA", "BGRx"}:
        raise RuntimeError(f"unsupported PDFium bitmap mode: {bitmap.mode}")
    source_channels = bitmap.n_channels
    alpha = bitmap.mode == "BGRA"
    rows = bytearray()
    buffer = bytes(bitmap.buffer)
    for row_index in range(bitmap.height):
        start = row_index * bitmap.stride
        row = buffer[start : start + bitmap.width * source_channels]
        rows.append(0)
        for offset in range(0, len(row), source_channels):
            blue, green, red = row[offset : offset + 3]
            rows.extend((red, green, blue))
            if alpha:
                rows.append(row[offset + 3])
    color_type = 6 if alpha else 2
    header = struct.pack(">IIBBBBB", bitmap.width, bitmap.height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=6))
        + _png_chunk(b"IEND", b"")
    )


def write_pdfium_png(bitmap: pdfium.PdfBitmap, path: Path) -> None:
    write_bytes(path, pdfium_png_bytes(bitmap))
