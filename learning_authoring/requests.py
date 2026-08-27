"""Provider request construction for extraction and page repair."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from learning_authoring.contracts import ExtractedPage, ExtractedSource, ExtractedSourcePayload


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("$schema", None)
    return schema


def data_url(path: Path, media_type: str) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def extraction_descriptor(
    *,
    stage_version: str,
    source_sha256: str,
    model: str,
    reasoning_effort: str,
    response_mode: str,
    render_dpi: int,
    pdf_detail: str,
    max_output_tokens: int | None,
    targeted_repair: bool,
    repair_max_attempts: int,
    repair_max_candidate_pages: int | None,
    repair_systemic_guard_min_candidate_pages: int,
    repair_systemic_guard_max_page_fraction: float,
    prompt: str,
    repair_prompt: str,
) -> tuple[str, dict[str, Any]]:
    schema = json_schema(ExtractedSourcePayload)
    descriptor = {
        "stage_version": stage_version,
        "source_sha256": source_sha256,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "response_mode": response_mode,
        "render_dpi": render_dpi,
        "pdf_detail": pdf_detail,
        "source_delivery": "pdf_native",
        "max_output_tokens": max_output_tokens,
        "targeted_repair": targeted_repair,
        "repair_max_attempts": repair_max_attempts,
        "repair_max_candidate_pages": repair_max_candidate_pages,
        "repair_systemic_guard_min_candidate_pages": (repair_systemic_guard_min_candidate_pages),
        "repair_systemic_guard_max_page_fraction": (repair_systemic_guard_max_page_fraction),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "repair_prompt_sha256": hashlib.sha256(repair_prompt.encode()).hexdigest(),
        "schema_sha256": hashlib.sha256(
            json.dumps(schema, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
    }
    encoded = json.dumps(descriptor, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest(), descriptor


def extraction_input(
    run_dir: Path,
    *,
    filename: str,
    page_count: int,
) -> list[dict[str, Any]]:
    manifest = {
        "expected_page_count": page_count,
        "page_numbering": "1-based",
        "required_schema_version": "extracted-source.v2",
        "required_page_note_count": page_count,
        "source_identity_is_code_owned": True,
        "source_delivery": "pdf_native",
        "filename": filename,
        "local_text_layer": "diagnostic_only_not_model_input",
    }
    content: list[dict[str, Any]] = [
        {
            "type": "input_file",
            "filename": filename,
            "file_data": data_url(run_dir / "source.pdf", "application/pdf"),
        },
        {
            "type": "input_text",
            "text": "Code-owned extraction manifest:\n" + json.dumps(manifest, ensure_ascii=False),
        },
    ]
    return [{"role": "user", "content": content}]


def build_extraction_request(
    *,
    run_dir: Path,
    filename: str,
    page_count: int,
    prompt: str,
    model: str,
    reasoning_effort: str,
    response_mode: str,
    max_output_tokens: int | None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": model,
        "reasoning": {"effort": reasoning_effort},
        "instructions": prompt,
        "input": extraction_input(
            run_dir,
            filename=filename,
            page_count=page_count,
        ),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "extracted_source_v2",
                "strict": False,
                "schema": json_schema(ExtractedSourcePayload),
            }
        },
        # Background responses must be retrievable after the create call.
        "store": response_mode == "background",
    }
    if max_output_tokens is not None:
        request["max_output_tokens"] = max_output_tokens
    if response_mode == "background":
        request["background"] = True
    return request


def build_repair_request(
    *,
    run_dir: Path,
    page: ExtractedPage,
    page_count: int,
    filename: str,
    prompt: str,
    model: str,
    reasoning_effort: str,
    response_mode: str,
    pdf_detail: str,
    max_output_tokens: int | None,
    attempt_number: int,
    attempt_limit: int,
) -> dict[str, Any]:
    manifest = {
        "task": "targeted_page_repair",
        "filename": filename,
        "expected_page": page.page_number,
        "expected_document_page_count": page_count,
        "attempt_number": attempt_number,
        "attempt_limit": attempt_limit,
        "current_extracted_page": page.model_dump(mode="json"),
    }
    request: dict[str, Any] = {
        "model": model,
        "reasoning": {"effort": reasoning_effort},
        "instructions": prompt,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Code-owned targeted repair manifest:\n"
                        + json.dumps(manifest, ensure_ascii=False),
                    },
                    {
                        "type": "input_image",
                        "image_url": data_url(
                            run_dir / "pages" / f"page-{page.page_number:04d}.png",
                            "image/png",
                        ),
                        "detail": pdf_detail,
                    },
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "extracted_page_repair_v2",
                "strict": False,
                "schema": json_schema(ExtractedPage),
            }
        },
        # Background responses must be retrievable after the create call.
        "store": response_mode == "background",
    }
    if max_output_tokens is not None:
        request["max_output_tokens"] = max_output_tokens
    if response_mode == "background":
        request["background"] = True
    return request


def kc_input(approved: ExtractedSource) -> list[dict[str, Any]]:
    """Return the exact KC model input: one text item containing only extraction JSON."""

    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": approved.model_dump_json(),
                }
            ],
        }
    ]


def build_kc_request(
    *,
    approved: ExtractedSource,
    instructions: str,
    output_schema: dict[str, Any],
    model: str,
    reasoning_effort: str,
    response_mode: str,
    max_output_tokens: int | None,
) -> dict[str, Any]:
    """Build KC generation with no source input besides approved extraction JSON."""

    schema_for_api = {key: value for key, value in output_schema.items() if key != "$schema"}
    request: dict[str, Any] = {
        "model": model,
        "reasoning": {"effort": reasoning_effort},
        "instructions": instructions,
        "input": kc_input(approved),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "proposed_kc_set_v1",
                "strict": True,
                "schema": schema_for_api,
            }
        },
        "store": response_mode == "background",
    }
    if max_output_tokens is not None:
        request["max_output_tokens"] = max_output_tokens
    if response_mode == "background":
        request["background"] = True
    return request


def build_quiz_request(
    *,
    quiz_input_payload: dict[str, Any],
    instructions: str,
    output_schema: dict[str, Any],
    model: str,
    reasoning_effort: str,
    response_mode: str,
    max_output_tokens: int | None,
) -> dict[str, Any]:
    """Build one compact Quiz call with no PDF, PNG, or unrelated KC input."""

    schema_for_api = {key: value for key, value in output_schema.items() if key != "$schema"}
    request: dict[str, Any] = {
        "model": model,
        "reasoning": {"effort": reasoning_effort},
        "instructions": instructions,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(quiz_input_payload, ensure_ascii=False),
                    }
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "quiz_batch_v1",
                "strict": True,
                "schema": schema_for_api,
            }
        },
        "store": response_mode == "background",
    }
    if max_output_tokens is not None:
        request["max_output_tokens"] = max_output_tokens
    if response_mode == "background":
        request["background"] = True
    return request
