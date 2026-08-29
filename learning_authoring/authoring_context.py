"""Optional, immutable lecturer inputs, kept outside PDF-only extraction.

This module does no semantic parsing, page mapping, generation, or provider work.
The active KC author reads free-form text and inspects attachments in the same
stage that proposes KCs. Raw inputs and historical manifests are never replaced.
"""

from __future__ import annotations

import json
import mimetypes
import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from learning_authoring.artifacts import read_json, sha256_bytes, write_bytes, write_json
from learning_authoring.contracts import SourceDescriptor

if TYPE_CHECKING:
    from learning_authoring.source_bundle import SourceBundle

CONTEXT_SCHEMA_VERSION = "authoring-context.v1"
CONTEXT_MANIFEST = "authoring-context.json"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class AuthoringContextSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    page_count: int = Field(ge=1)


class AuthoringContextBundleRef(BaseModel):
    """Bind context to an ordered source collection, never to inferred slide ordinals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["source-bundle.v1"] = "source-bundle.v1"
    source_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    sources: list[AuthoringContextSourceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def sources_are_unique(self) -> AuthoringContextBundleRef:
        ids = [source.source_id for source in self.sources]
        hashes = [source.source_sha256 for source in self.sources]
        if len(ids) != len(set(ids)) or len(hashes) != len(set(hashes)):
            raise ValueError("bundle context contains duplicate source identities")
        return self


class AuthoringContextItem(BaseModel):
    """One entire user input; context IDs do not imply slide or page anchors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_id: str = Field(pattern=r"^CTX-[0-9]+$")
    origin: Literal["file", "inline_text"]
    original_path: str | None = None
    filename: str | None = None
    raw_path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=0)
    text: str | None = None
    encoding: Literal["utf-8", "utf-8-sig", "utf-16", "utf-32"] | None = None

    @field_validator("raw_path")
    @classmethod
    def validate_raw_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or path.parts[:2] != ("authoring-context", "raw")
            or len(path.parts) != 3
            or ".." in path.parts
            or "\\" in value
            or str(path) != value
        ):
            raise ValueError("context raw_path must be a file under authoring-context/raw")
        return value

    @model_validator(mode="after")
    def validate_origin_and_text(self) -> AuthoringContextItem:
        if self.origin == "file" and (not self.original_path or not self.filename):
            raise ValueError("file context requires its original path and filename")
        if self.origin == "inline_text":
            if self.original_path is not None or self.filename is not None:
                raise ValueError("inline context must not invent an original file")
            if self.text is None or self.encoding != "utf-8":
                raise ValueError("inline context must retain its UTF-8 text")
        if (self.text is None) != (self.encoding is None):
            raise ValueError("context text and encoding must both be present or both be null")
        return self


def _context_digest(payload: dict[str, Any]) -> str:
    """Bind source, order, complete raw provenance, and the lossless text view."""

    bound = {key: value for key, value in payload.items() if key != "sha256"}
    return sha256_bytes(
        json.dumps(bound, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


class AuthoringContext(BaseModel):
    """Code-owned context identity, independent of extraction artifact identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["authoring-context.v1"] = CONTEXT_SCHEMA_VERSION
    source_ref: AuthoringContextSourceRef | AuthoringContextBundleRef
    items: list[AuthoringContextItem] = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_integrity(self) -> AuthoringContext:
        ids = [item.context_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate authoring context IDs")
        if self.sha256 != _context_digest(self.model_dump(mode="json")):
            raise ValueError("authoring context SHA-256 does not match its manifest")
        return self

    def validate_against_source(self, source: SourceDescriptor) -> None:
        # Check again even for an already parsed model: nested lists are mutable.
        self.validate_integrity()
        if not isinstance(self.source_ref, AuthoringContextSourceRef):
            raise ValueError("bundle-bound authoring context cannot be used as one-PDF context")
        if self.source_ref.source_sha256 != source.sha256:
            raise ValueError("authoring context source SHA-256 does not match the PDF")
        if self.source_ref.source_id != source.source_id:
            raise ValueError("authoring context source_id does not match the PDF")
        if self.source_ref.page_count != source.page_count:
            raise ValueError("authoring context page count does not match the PDF")

    def validate_against_bundle(self, bundle: SourceBundle) -> None:
        """Verify exact ordered bundle identity without inferring cross-PDF page mappings."""

        self.validate_integrity()
        if not isinstance(self.source_ref, AuthoringContextBundleRef):
            raise ValueError("one-PDF authoring context cannot be used as bundle context")
        if self.source_ref.source_bundle_sha256 != bundle.bundle_sha256:
            raise ValueError("authoring context source-bundle SHA-256 does not match")
        expected = [
            AuthoringContextSourceRef(
                source_id=entry.source.source_id,
                source_sha256=entry.source.sha256,
                page_count=entry.source.page_count,
            )
            for entry in bundle.sources
        ]
        if self.source_ref.sources != expected:
            raise ValueError("authoring context ordered source identities do not match bundle")


def _decode_text(data: bytes) -> tuple[str | None, str | None]:
    # Some PDFs consist entirely of ASCII. Do not mistake their container bytes
    # (or other common binary containers) for a lecturer's plain-text notes.
    binary_signatures = (
        b"%PDF-",
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"\x89PNG",
        b"\xff\xd8\xff",
        b"GIF87a",
        b"GIF89a",
        b"RIFF",
        b"ID3",
        b"\x1f\x8b",
        b"II*\x00",
        b"MM\x00*",
    )
    if data.startswith(binary_signatures):
        return None, None
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encoding = "utf-32"
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    elif data.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"
    try:
        text = data.decode(encoding)
    except UnicodeError:
        return None, None
    if any(ord(char) < 32 and char not in "\t\n\r\f" for char in text):
        return None, None
    return text, encoding


def _media_type(filename: str | None, data: bytes, text: str | None) -> str:
    guessed = mimetypes.guess_type(filename or "")[0]
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if text is not None:
        # A readable text file can have any extension (including none).
        return (
            guessed
            if guessed
            and (guessed.startswith("text/") or guessed in {"application/json", "application/xml"})
            else "text/plain"
        )
    return guessed or "application/octet-stream"


def _raw_target(run_dir: Path, item: AuthoringContextItem) -> Path:
    target = run_dir / item.raw_path
    if target.is_symlink() or not target.resolve().is_relative_to(run_dir):
        raise ValueError("authoring context raw path escapes the run or is a symlink")
    return target


def _verify_raw(run_dir: Path, item: AuthoringContextItem) -> None:
    target = _raw_target(run_dir, item)
    if not target.is_file():
        raise ValueError(f"missing authoring context raw input: {item.context_id}")
    data = target.read_bytes()
    if len(data) != item.size_bytes or sha256_bytes(data) != item.sha256:
        raise ValueError(f"authoring context raw SHA-256 mismatch: {item.context_id}")
    if item.text is not None:
        try:
            decoded = data.decode(item.encoding or "utf-8")
        except UnicodeError as exc:
            raise ValueError(f"invalid authoring context text: {item.context_id}") from exc
        if decoded != item.text:
            raise ValueError(f"authoring context text differs from raw input: {item.context_id}")


def load_authoring_context(run_dir: Path, source: SourceDescriptor) -> AuthoringContext | None:
    """Load the current context and verify its source, manifest, and raw bytes.

    Original external files need not still exist: the immutable copied bytes are
    authoritative. Absence of a context manifest is the legacy PDF-only path.
    """

    output = Path(run_dir).expanduser().resolve()
    manifest = output / CONTEXT_MANIFEST
    if not manifest.exists():
        if manifest.is_symlink() or (output / "authoring-context").exists():
            raise ValueError("incomplete authoring context; prepare again with original inputs")
        return None
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("authoring context manifest must be a regular file")
    context = AuthoringContext.model_validate(read_json(manifest))
    context.validate_against_source(source)
    snapshot = output / "authoring-context" / "manifests" / f"{context.sha256}.json"
    if (
        snapshot.is_symlink()
        or not snapshot.resolve().is_relative_to(output)
        or not snapshot.is_file()
        or snapshot.read_bytes() != manifest.read_bytes()
    ):
        raise ValueError("authoring context manifest differs from its immutable snapshot")
    for item in context.items:
        _verify_raw(output, item)
    return context


def load_bundle_authoring_context(
    run_dir: Path,
    bundle: SourceBundle,
) -> AuthoringContext | None:
    """Load context bound to a complete source bundle and verify copied raw bytes."""

    output = Path(run_dir).expanduser().resolve()
    manifest = output / CONTEXT_MANIFEST
    if not manifest.exists():
        if manifest.is_symlink() or (output / "authoring-context").exists():
            raise ValueError("incomplete authoring context; prepare again with original inputs")
        return None
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("authoring context manifest must be a regular file")
    context = AuthoringContext.model_validate(read_json(manifest))
    context.validate_against_bundle(bundle)
    snapshot = output / "authoring-context" / "manifests" / f"{context.sha256}.json"
    if (
        snapshot.is_symlink()
        or not snapshot.resolve().is_relative_to(output)
        or not snapshot.is_file()
        or snapshot.read_bytes() != manifest.read_bytes()
    ):
        raise ValueError("authoring context manifest differs from its immutable snapshot")
    for item in context.items:
        _verify_raw(output, item)
    return context


def _freeze_context_inputs(
    output: Path,
    *,
    source_ref: AuthoringContextSourceRef | AuthoringContextBundleRef,
    context_files: Sequence[Path | str],
    context_texts: Sequence[str],
    existing: AuthoringContext | None,
) -> AuthoringContext:
    """Shared lossless storage; source/bundle binding is supplied by code, not inferred."""

    manifest = output / CONTEXT_MANIFEST
    if manifest.is_symlink():
        raise ValueError("authoring context manifest must be a regular file")

    inputs: list[tuple[bytes, Path | None, str | None, str | None]] = []
    for value in context_files:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"authoring context file does not exist: {path}")
        data = path.read_bytes()
        text, encoding = _decode_text(data)
        inputs.append((data, path, text, encoding))
    for text in context_texts:
        if not isinstance(text, str):
            raise ValueError("inline authoring context must be text")
        inputs.append((text.encode("utf-8"), None, text, "utf-8"))

    items = []
    for index, (data, path, text, encoding) in enumerate(inputs, start=1):
        digest = sha256_bytes(data)
        suffix = path.suffix if path else ".txt"
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,16}", suffix):
            suffix = ".txt" if text is not None else ".bin"
        items.append(
            AuthoringContextItem(
                context_id=f"CTX-{index:03d}",
                origin="file" if path else "inline_text",
                original_path=str(path) if path else None,
                filename=path.name if path else None,
                raw_path=f"authoring-context/raw/{digest}{suffix}",
                media_type=_media_type(path.name if path else None, data, text),
                sha256=digest,
                size_bytes=len(data),
                text=text,
                encoding=encoding,
            )
        )
    payload = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "source_ref": source_ref.model_dump(mode="json"),
        "items": [item.model_dump(mode="json") for item in items],
    }
    context = AuthoringContext.model_validate({**payload, "sha256": _context_digest(payload)})
    if existing is not None and existing.sha256 == context.sha256:
        return existing

    for item in items:
        target = _raw_target(output, item)
        if target.exists():
            _verify_raw(output, item)
    snapshot = output / "authoring-context" / "manifests" / f"{context.sha256}.json"
    if snapshot.is_symlink() or not snapshot.resolve().is_relative_to(output):
        raise ValueError("authoring context snapshot must remain inside the run")
    if snapshot.exists() and read_json(snapshot) != context.model_dump(mode="json"):
        raise ValueError("authoring context snapshot SHA-256 mismatch")
    for item, (data, _, _, _) in zip(items, inputs, strict=True):
        target = _raw_target(output, item)
        if not target.exists():
            write_bytes(target, data)
    if not snapshot.exists():
        write_json(snapshot, context.model_dump(mode="json"))
    write_bytes(manifest, snapshot.read_bytes())
    return context


def prepare_authoring_context(
    run_dir: Path,
    source: SourceDescriptor,
    context_files: Sequence[Path | str] = (),
    context_texts: Sequence[str] = (),
) -> AuthoringContext | None:
    """Freeze optional free-form inputs; explicit replacements keep all history.

    An omitted input list reuses existing context. A changed explicit input list
    changes KC/Quiz lineage, not Extraction. No note syntax, anchors, page count,
    extension, or semantic interpretation is required from the lecturer.
    """

    output = Path(run_dir).expanduser().resolve()
    if not context_files and not context_texts:
        return load_authoring_context(output, source)
    manifest = output / CONTEXT_MANIFEST
    existing = load_authoring_context(output, source) if manifest.exists() else None
    _freeze_context_inputs(
        output,
        source_ref=AuthoringContextSourceRef(
            source_id=source.source_id,
            source_sha256=source.sha256,
            page_count=source.page_count,
        ),
        context_files=context_files,
        context_texts=context_texts,
        existing=existing,
    )
    return load_authoring_context(output, source)


def prepare_bundle_authoring_context(
    run_dir: Path,
    bundle: SourceBundle,
    context_files: Sequence[Path | str] = (),
    context_texts: Sequence[str] = (),
) -> AuthoringContext | None:
    """Freeze arbitrary lecturer context against an ordered 1..N PDF bundle."""

    output = Path(run_dir).expanduser().resolve()
    if not context_files and not context_texts:
        return load_bundle_authoring_context(output, bundle)
    manifest = output / CONTEXT_MANIFEST
    existing = load_bundle_authoring_context(output, bundle) if manifest.exists() else None
    source_ref = AuthoringContextBundleRef(
        source_bundle_sha256=bundle.bundle_sha256,
        sources=[
            AuthoringContextSourceRef(
                source_id=entry.source.source_id,
                source_sha256=entry.source.sha256,
                page_count=entry.source.page_count,
            )
            for entry in bundle.sources
        ],
    )
    _freeze_context_inputs(
        output,
        source_ref=source_ref,
        context_files=context_files,
        context_texts=context_texts,
        existing=existing,
    )
    return load_bundle_authoring_context(output, bundle)
