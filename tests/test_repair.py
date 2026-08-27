from __future__ import annotations

import pytest

from learning_authoring.artifacts import RunArtifacts
from learning_authoring.contracts import (
    CrossPageRelation,
    ExtractedPage,
    ExtractedSourcePayload,
    PageNote,
    SourceRegion,
    WarningRecord,
)
from learning_authoring.repair import RepairPolicy, run_repairs
from tests.conftest import FakeResponse, block, fake_client, make_run_dir, page, payload


def policy() -> RepairPolicy:
    return RepairPolicy(
        enabled=True,
        max_attempts=2,
        model="test-model",
        reasoning_effort="low",
        response_mode="sync",
        pdf_detail="low",
        max_output_tokens=None,
        poll_interval_seconds=0.001,
        timeout_seconds=1,
        prompt="repair prompt",
        extraction_fingerprint="extract-fingerprint",
    )


def background_policy() -> RepairPolicy:
    return RepairPolicy(**{**policy().__dict__, "response_mode": "background"})


def test_successful_page_repair_is_applied(tmp_path, source) -> None:
    make_run_dir(tmp_path, source)
    repaired = page(2, "b2")
    client = fake_client(created=[FakeResponse(repaired.model_dump(mode="json"))])
    result, summary, usage, _ = run_repairs(
        payload(warning_page=2),
        source=source,
        artifacts=RunArtifacts(tmp_path),
        policy=policy(),
        client=client,
    )
    assert summary["applied_pages"] == [2]
    assert not result.pages[1].warnings
    assert usage[0]["total_tokens"] == 15
    result.with_source(source)


def test_missing_geometry_automatically_enters_targeted_repair(tmp_path, source) -> None:
    make_run_dir(tmp_path, source)
    initial = payload()
    incomplete_block = (
        initial.pages[1].blocks[0].model_copy(update={"region": SourceRegion(page=2, geometry={})})
    )
    incomplete_page = initial.pages[1].model_copy(update={"blocks": [incomplete_block]})
    initial = initial.model_copy(update={"pages": [initial.pages[0], incomplete_page]})
    repaired = page(2, "b2")
    client = fake_client(created=[FakeResponse(repaired.model_dump(mode="json"))])

    result, summary, _, _ = run_repairs(
        initial,
        source=source,
        artifacts=RunArtifacts(tmp_path),
        policy=policy(),
        client=client,
    )

    assert summary["candidate_pages"] == [2]
    assert summary["applied_pages"] == [2]
    assert result.pages[1].blocks[0].region.geometry


def test_invalid_geometry_automatically_enters_targeted_repair(tmp_path, source) -> None:
    make_run_dir(tmp_path, source)
    initial = payload()
    invalid_block = (
        initial.pages[1]
        .blocks[0]
        .model_copy(
            update={
                "region": SourceRegion(
                    page=2,
                    geometry={"bbox": [0, 0, 2, 1]},
                )
            }
        )
    )
    invalid_page = initial.pages[1].model_copy(update={"blocks": [invalid_block]})
    initial = initial.model_copy(update={"pages": [initial.pages[0], invalid_page]})
    repaired = page(2, "b2")
    client = fake_client(created=[FakeResponse(repaired.model_dump(mode="json"))])

    result, summary, _, _ = run_repairs(
        initial,
        source=source,
        artifacts=RunArtifacts(tmp_path),
        policy=policy(),
        client=client,
    )

    assert summary["candidate_reasons_by_page"]["2"]["invalid_geometry_block_ids"] == ["b2"]
    assert summary["applied_pages"] == [2]
    assert result.pages[1].blocks[0].region.geometry == repaired.blocks[0].region.geometry


@pytest.mark.parametrize("issue_class", ["source_ambiguity", "human_semantic_decision"])
def test_human_only_warning_never_triggers_png_repair(tmp_path, source, issue_class) -> None:
    make_run_dir(tmp_path, source)
    initial = payload()
    warning = WarningRecord(
        code="REVIEW_ONLY",
        message="A reviewer must interpret the source.",
        page=2,
        block_ids=["b2"],
        details={
            "issue_class": issue_class,
            "repair_route": "human_review",
            "repair_recommended": True,
            "review_disposition": "review",
        },
    )
    review_page = initial.pages[1].model_copy(update={"warnings": [warning]})
    initial = initial.model_copy(update={"pages": [initial.pages[0], review_page]})
    client = fake_client()

    result, summary, usage, costs = run_repairs(
        initial,
        source=source,
        artifacts=RunArtifacts(tmp_path),
        policy=policy(),
        client=client,
    )

    assert summary["candidate_pages"] == []
    assert client.responses.create_calls == []
    assert usage == []
    assert costs == []
    assert result.pages[1].warnings == [warning]


def test_human_review_warning_does_not_keep_geometry_repair_loop_alive(tmp_path, source) -> None:
    make_run_dir(tmp_path, source)
    warning = WarningRecord(
        code="SOURCE_AMBIGUITY",
        message="The visible source is ambiguous.",
        page=2,
        block_ids=["b2"],
        details={
            "issue_class": "source_ambiguity",
            "repair_route": "human_review",
            "repair_recommended": False,
            "review_disposition": "review",
        },
    )
    initial = payload()
    unresolved = (
        initial.pages[1].blocks[0].model_copy(update={"region": SourceRegion(page=2, geometry={})})
    )
    unresolved_page = initial.pages[1].model_copy(
        update={"blocks": [unresolved], "warnings": [warning]}
    )
    initial = initial.model_copy(update={"pages": [initial.pages[0], unresolved_page]})
    repaired_page = page(2, "b2").model_copy(update={"warnings": [warning]})
    client = fake_client(created=[FakeResponse(repaired_page.model_dump(mode="json"))])

    result, summary, _, _ = run_repairs(
        initial,
        source=source,
        artifacts=RunArtifacts(tmp_path),
        policy=policy(),
        client=client,
    )

    assert summary["applied_pages"] == [2]
    assert len(client.responses.create_calls) == 1
    assert result.pages[1].warnings == [warning]


def test_systemic_guard_refuses_to_spray_png_repairs(tmp_path, source) -> None:
    guarded_source = source.model_copy(update={"page_count": 6})
    make_run_dir(tmp_path, guarded_source)
    pages = []
    for page_number in range(1, 7):
        current = page(page_number, f"b{page_number}")
        unresolved = current.blocks[0].model_copy(
            update={"region": SourceRegion(page=page_number, geometry={})}
        )
        pages.append(current.model_copy(update={"blocks": [unresolved]}))
    initial = ExtractedSourcePayload(schema_version="extracted-source.v2", pages=pages)
    client = fake_client()

    result, summary, usage, costs = run_repairs(
        initial,
        source=guarded_source,
        artifacts=RunArtifacts(tmp_path),
        policy=policy(),
        client=client,
    )

    assert summary["guard"]["triggered"] is True
    assert "systemic_candidate_spread" in summary["guard"]["reasons"]
    assert summary["attempted_pages"] == []
    assert client.responses.create_calls == []
    assert usage == []
    assert costs == []
    assert result.warnings[-1].code == "TARGETED_REPAIR_SYSTEMIC_GUARD"


def test_candidate_page_budget_is_configurable(tmp_path, source) -> None:
    guarded_source = source.model_copy(update={"page_count": 10})
    make_run_dir(tmp_path, guarded_source)
    pages = []
    for page_number in range(1, 11):
        current = page(page_number, f"b{page_number}")
        if page_number <= 3:
            unresolved = current.blocks[0].model_copy(
                update={"region": SourceRegion(page=page_number, geometry={})}
            )
            current = current.model_copy(update={"blocks": [unresolved]})
        pages.append(current)
    initial = ExtractedSourcePayload(schema_version="extracted-source.v2", pages=pages)
    guarded_policy = RepairPolicy(
        **{
            **policy().__dict__,
            "max_candidate_pages": 2,
            "systemic_guard_min_candidate_pages": 99,
        }
    )
    client = fake_client()

    _, summary, _, _ = run_repairs(
        initial,
        source=guarded_source,
        artifacts=RunArtifacts(tmp_path),
        policy=guarded_policy,
        client=client,
    )

    assert summary["guard"]["reasons"] == ["candidate_page_budget_exceeded"]
    assert client.responses.create_calls == []


def test_repair_cannot_apply_while_geometry_remains_empty(tmp_path, source) -> None:
    make_run_dir(tmp_path, source)
    initial = payload()
    incomplete_block = (
        initial.pages[1].blocks[0].model_copy(update={"region": SourceRegion(page=2, geometry={})})
    )
    incomplete_page = initial.pages[1].model_copy(update={"blocks": [incomplete_block]})
    initial = initial.model_copy(update={"pages": [initial.pages[0], incomplete_page]})
    unresolved_json = incomplete_page.model_dump(mode="json")
    client = fake_client(
        created=[
            FakeResponse(unresolved_json, response_id="repair_geometry_1"),
            FakeResponse(unresolved_json, response_id="repair_geometry_2"),
        ]
    )

    result, summary, _, _ = run_repairs(
        initial,
        source=source,
        artifacts=RunArtifacts(tmp_path),
        policy=policy(),
        client=client,
    )

    assert summary["failed_pages"] == [2]
    assert result.pages[1].warnings[-1].code == "TARGETED_REPAIR_EXHAUSTED"
    assert result.pages[1].warnings[-1].block_ids == ["b2"]


def test_invalid_cross_page_repair_falls_back_to_document_valid_page(tmp_path, source) -> None:
    make_run_dir(tmp_path, source)
    initial = payload(warning_page=2).model_copy(
        update={
            "cross_page_relations": [
                CrossPageRelation(
                    relation_type="continues",
                    source_block_id="b1",
                    target_block_id="b2",
                )
            ]
        }
    )
    invalid_repair = ExtractedPage(
        page_number=2,
        role="lesson",
        blocks=[block("b2-new", 2)],
        reading_order=["b2-new"],
        page_note=PageNote(summary="changed ID", evidence_block_ids=["b2-new"]),
    )
    client = fake_client(
        created=[
            FakeResponse(invalid_repair.model_dump(mode="json"), response_id="repair_1"),
            FakeResponse(invalid_repair.model_dump(mode="json"), response_id="repair_2"),
        ]
    )
    result, summary, _, _ = run_repairs(
        initial,
        source=source,
        artifacts=RunArtifacts(tmp_path),
        policy=policy(),
        client=client,
    )
    assert summary["failed_pages"] == [2]
    assert result.pages[1].blocks[0].block_id == "b2"
    assert result.pages[1].warnings[-1].code == "TARGETED_REPAIR_EXHAUSTED"
    result.with_source(source)


def test_interrupted_repair_resumes_same_response_without_new_create(tmp_path, source) -> None:
    make_run_dir(tmp_path, source)
    initial = payload(warning_page=2)
    first_client = fake_client(
        created=[
            FakeResponse(
                page(2, "b2").model_dump(mode="json"),
                response_id="repair_existing",
                status="queued",
            )
        ],
        retrieved=[RuntimeError("temporary retrieve failure")],
    )
    with pytest.raises(RuntimeError, match="temporary"):
        run_repairs(
            initial,
            source=source,
            artifacts=RunArtifacts(tmp_path),
            policy=background_policy(),
            client=first_client,
        )
    assert len(first_client.responses.create_calls) == 1

    repaired = page(2, "b2")
    second_client = fake_client(
        retrieved=[FakeResponse(repaired.model_dump(mode="json"), response_id="repair_existing")]
    )
    result, summary, _, _ = run_repairs(
        initial,
        source=source,
        artifacts=RunArtifacts(tmp_path),
        policy=background_policy(),
        client=second_client,
    )
    assert second_client.responses.create_calls == []
    assert second_client.responses.retrieve_calls == ["repair_existing"]
    assert summary["applied_pages"] == [2]
    result.with_source(source)
