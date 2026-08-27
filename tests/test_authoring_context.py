from __future__ import annotations

import json

import pytest

from learning_authoring.artifacts import read_json, sha256_bytes, write_json
from learning_authoring.authoring_context import (
    AuthoringContext,
    load_authoring_context,
    prepare_authoring_context,
)


def test_no_context_is_optional_and_does_not_create_artifacts(tmp_path, source) -> None:
    run_dir = tmp_path / "not-yet-created"
    assert prepare_authoring_context(run_dir, source) is None
    assert load_authoring_context(run_dir, source) is None
    assert not run_dir.exists()


@pytest.mark.parametrize("filename", ["notes", "notes.scratch", "notes.txt", "notes.json"])
def test_freeform_files_need_no_template_or_extension(tmp_path, source, filename) -> None:
    notes = tmp_path / filename
    raw = "  Nhấn mạnh cách chọn phương án.\r\nGhi chú rời, không có số slide.  \n".encode()
    notes.write_bytes(raw)
    run_dir = tmp_path / "run"

    context = prepare_authoring_context(run_dir, source, context_files=[notes])

    assert context is not None
    assert context.source_ref.source_sha256 == source.sha256
    assert context.source_ref.page_count == source.page_count
    assert len(context.items) == 1
    item = context.items[0]
    assert item.context_id == "CTX-001"
    assert item.origin == "file"
    assert item.original_path == str(notes)
    assert item.filename == filename
    assert item.text == raw.decode()
    assert item.sha256 == sha256_bytes(raw)
    assert (run_dir / item.raw_path).read_bytes() == raw
    assert notes.read_bytes() == raw
    assert not (run_dir / "extracted-source.proposed.json").exists()

    # Resuming uses the frozen input, not a changed or removed external file.
    notes.write_bytes(b"new external notes")
    assert load_authoring_context(run_dir, source) == context
    notes.unlink()
    assert load_authoring_context(run_dir, source) == context


def test_repeated_inline_inputs_are_kept_whole_in_order(tmp_path, source) -> None:
    texts = ["Emphasize exceptions.\n\n", "Unmapped lecture comment", "Emphasize exceptions.\n\n"]
    context = prepare_authoring_context(tmp_path, source, context_texts=texts)

    assert context is not None
    assert [item.context_id for item in context.items] == ["CTX-001", "CTX-002", "CTX-003"]
    assert [item.text for item in context.items] == texts
    for item, text in zip(context.items, texts, strict=True):
        assert item.origin == "inline_text"
        assert item.original_path is None and item.filename is None
        assert (tmp_path / item.raw_path).read_bytes() == text.encode()
    assert context.items[0].raw_path == context.items[2].raw_path


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-32"])
def test_text_encodings_keep_original_bytes(tmp_path, source, encoding) -> None:
    text = "Điểm cần nhấn mạnh\r\nNo mandatory page mapping."
    notes = tmp_path / "lecture.context"
    notes.write_bytes(text.encode(encoding))
    context = prepare_authoring_context(tmp_path / "run", source, context_files=[notes])

    assert context is not None
    item = context.items[0]
    assert item.text == text
    assert item.encoding == encoding
    assert (tmp_path / "run" / item.raw_path).read_bytes() == notes.read_bytes()


@pytest.mark.parametrize(
    ("filename", "raw", "media_type"),
    [
        ("annotated.pdf", b"%PDF-1.4\nASCII PDF container, not plain notes", "application/pdf"),
        ("drawing.attachment", b"\x89PNG\r\n\x1a\n\x00image", "image/png"),
        ("slides.pptx", b"PK\x03\x04binary-container", None),
        ("legacy.encoding", b"\xffundecodable text", "application/octet-stream"),
    ],
)
def test_nontext_inputs_are_preserved_as_inspectable_attachments(
    tmp_path, source, filename, raw, media_type
) -> None:
    attachment = tmp_path / filename
    attachment.write_bytes(raw)
    run_dir = tmp_path / "run"
    context = prepare_authoring_context(run_dir, source, context_files=[attachment])

    assert context is not None
    item = context.items[0]
    assert item.text is None and item.encoding is None
    assert item.original_path == str(attachment)
    assert item.filename == filename
    assert item.media_type and (media_type is None or item.media_type == media_type)
    assert (run_dir / item.raw_path).read_bytes() == raw
    assert item.sha256 == sha256_bytes(raw)


def test_reuse_does_not_rewrite_manifest_or_raw_inputs(tmp_path, source) -> None:
    context = prepare_authoring_context(tmp_path, source, context_texts=["One sparse note"])
    assert context is not None
    paths = [tmp_path / "authoring-context.json", tmp_path / context.items[0].raw_path]
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]

    assert prepare_authoring_context(tmp_path, source) == context
    assert prepare_authoring_context(tmp_path, source, context_texts=["One sparse note"]) == context
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths] == before


def test_changed_context_keeps_history_and_does_not_change_extraction(tmp_path, source) -> None:
    extraction = tmp_path / "extracted-source.proposed.json"
    extraction.write_bytes(b'{"unchanged":"PDF-only extraction"}\n  ')
    extraction_before = extraction.read_bytes()
    first = prepare_authoring_context(tmp_path, source, context_texts=["First note"])
    assert first is not None
    first_manifest = (tmp_path / "authoring-context.json").read_bytes()
    second = prepare_authoring_context(tmp_path, source, context_texts=["Revised note"])

    assert second is not None and second.sha256 != first.sha256
    snapshot = tmp_path / "authoring-context" / "manifests" / f"{first.sha256}.json"
    assert snapshot.read_bytes() == first_manifest
    assert (tmp_path / first.items[0].raw_path).read_bytes() == b"First note"
    assert (tmp_path / second.items[0].raw_path).read_bytes() == b"Revised note"
    assert load_authoring_context(tmp_path, source) == second
    assert extraction.read_bytes() == extraction_before


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"sha256": "b" * 64}, "source SHA-256"),
        ({"source_id": "different-pdf"}, "source_id"),
        ({"page_count": 7}, "page count"),
    ],
)
def test_context_cannot_be_reused_for_another_pdf(tmp_path, source, update, message) -> None:
    prepare_authoring_context(tmp_path, source, context_texts=["Notes"])
    different = source.model_copy(update=update)
    before = (tmp_path / "authoring-context.json").read_bytes()

    with pytest.raises(ValueError, match=message):
        load_authoring_context(tmp_path, different)
    with pytest.raises(ValueError, match=message):
        prepare_authoring_context(tmp_path, different, context_texts=["Changed notes"])
    assert (tmp_path / "authoring-context.json").read_bytes() == before


def test_raw_tampering_blocks_load_and_reprepare(tmp_path, source) -> None:
    context = prepare_authoring_context(tmp_path, source, context_texts=["Original"])
    assert context is not None
    (tmp_path / context.items[0].raw_path).write_bytes(b"Tampered")

    with pytest.raises(ValueError, match="raw SHA-256 mismatch"):
        load_authoring_context(tmp_path, source)
    with pytest.raises(ValueError, match="raw SHA-256 mismatch"):
        prepare_authoring_context(tmp_path, source, context_texts=["Replacement"])


def test_manifest_tampering_is_not_a_new_context_revision(tmp_path, source) -> None:
    prepare_authoring_context(tmp_path, source, context_texts=["Original"])
    manifest = tmp_path / "authoring-context.json"
    data = read_json(manifest)
    data["items"][0]["text"] = "Edited without original raw"
    write_json(manifest, data)

    with pytest.raises(ValueError, match="SHA-256 does not match its manifest"):
        load_authoring_context(tmp_path, source)


def test_missing_raw_or_snapshot_fails_explicitly(tmp_path, source) -> None:
    context = prepare_authoring_context(tmp_path, source, context_texts=["Original"])
    assert context is not None
    raw = tmp_path / context.items[0].raw_path
    original = raw.read_bytes()
    raw.unlink()
    with pytest.raises(ValueError, match="missing authoring context raw"):
        load_authoring_context(tmp_path, source)
    raw.write_bytes(original)
    snapshot = tmp_path / "authoring-context" / "manifests" / f"{context.sha256}.json"
    snapshot.unlink()
    with pytest.raises(ValueError, match="immutable snapshot"):
        load_authoring_context(tmp_path, source)


def test_raw_paths_cannot_escape_run(tmp_path, source) -> None:
    context = prepare_authoring_context(tmp_path, source, context_texts=["Original"])
    assert context is not None
    data = context.model_dump(mode="json")
    data["items"][0]["raw_path"] = "authoring-context/raw/../../../outside.txt"
    with pytest.raises(ValueError, match="raw_path"):
        AuthoringContext.model_validate(data)


def test_partial_context_is_not_silently_omitted(tmp_path, source) -> None:
    context = prepare_authoring_context(tmp_path, source, context_texts=["Original"])
    assert context is not None
    (tmp_path / "authoring-context.json").unlink()
    with pytest.raises(ValueError, match="incomplete authoring context"):
        load_authoring_context(tmp_path, source)
    assert prepare_authoring_context(tmp_path, source, context_texts=["Original"]) == context


def test_existing_raw_snapshot_is_checked_after_interrupted_prepare(tmp_path, source) -> None:
    context = prepare_authoring_context(tmp_path, source, context_texts=["Original"])
    assert context is not None
    (tmp_path / "authoring-context.json").unlink()
    (tmp_path / context.items[0].raw_path).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="raw SHA-256 mismatch"):
        prepare_authoring_context(tmp_path, source, context_texts=["Original"])


def test_manifest_json_preserves_freeform_prompt_content(tmp_path, source) -> None:
    text = 'A user note with braces: {"emphasis": "why?"}\nNo parsing, please.'
    context = prepare_authoring_context(tmp_path, source, context_texts=[text])
    assert context is not None
    manifest = json.loads((tmp_path / "authoring-context.json").read_text())
    assert manifest["items"][0]["text"] == text
    assert "pages" not in manifest["items"][0]
