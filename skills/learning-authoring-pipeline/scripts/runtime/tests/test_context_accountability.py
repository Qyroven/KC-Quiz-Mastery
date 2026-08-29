"""Quote/reference accountability tests, not proof that all meanings were retained."""

from copy import deepcopy

import pytest

from learning_authoring.authoring_context import prepare_authoring_context
from learning_authoring.kc_contracts import ProposedKCSet
from tests.conftest import payload
from tests.test_kc_context import _candidate, _context


def _audit(context_id="CTX-001"):
    return {
        "context_id": context_id,
        "excerpt": "Focus on the exception",
        "claim": "Identify the condition where the exception applies.",
        "disposition": "represented",
        "kc_ids": ["KC-001"],
        "reason": "The conditional distinction is retained in this KC.",
    }


def test_fresh_context_requires_accountability_but_legacy_remains_readable(tmp_path, source):
    context = _context(tmp_path, source)
    value = _candidate(source, context=context)
    proposed = ProposedKCSet.model_validate(value)
    proposed.validate_against_source(payload().with_source(source), context)
    with pytest.raises(ValueError, match="context_audit omits"):
        proposed.validate_against_source(
            payload().with_source(source), context, require_context_audit=True,
        )
    value["context_audit"] = [_audit()]
    ProposedKCSet.model_validate(value).validate_against_source(
        payload().with_source(source), context, require_context_audit=True,
    )


def test_each_freeform_input_is_accounted_without_requiring_page_anchors(tmp_path, source):
    context = prepare_authoring_context(
        tmp_path, source,
        context_texts=["Focus on the exception", "Room opens at nine."],
    )
    value = _candidate(source, context=context)
    value["context_audit"] = [_audit()]
    with pytest.raises(ValueError, match="CTX-002"):
        ProposedKCSet.model_validate(value).validate_against_source(
            payload().with_source(source), context, require_context_audit=True,
        )
    value["context_audit"].append({
        "context_id": "CTX-002", "excerpt": "Room opens at nine.",
        "claim": "Room opening time", "disposition": "not_assessed", "kc_ids": [],
        "reason": "Administrative logistics rather than course knowledge.",
    })
    ProposedKCSet.model_validate(value).validate_against_source(
        payload().with_source(source), context, require_context_audit=True,
    )
    assert all("pages" not in item for item in value["context_audit"])


@pytest.mark.parametrize("change, message", [
    ({"excerpt": "An invented quote"}, "not an exact quote"),
    ({"context_id": "CTX-999", "disposition": "not_assessed", "kc_ids": []}, "unknown context_id"),
    ({"kc_ids": ["KC-999"]}, "unknown KCs"),
    ({"kc_ids": []}, "requires KC references"),
    ({"disposition": "unresolved"}, "must not claim KC coverage"),
])
def test_context_ledger_cannot_invent_quotes_or_targets(tmp_path, source, change, message):
    context = _context(tmp_path, source)
    value = _candidate(source, context=context)
    value["context_audit"] = [{**_audit(), **change}]
    with pytest.raises(ValueError, match=message):
        ProposedKCSet.model_validate(value).validate_against_source(
            payload().with_source(source), context, require_context_audit=True,
        )


def test_coverage_link_must_have_context_evidence_on_the_kc(tmp_path, source):
    context = _context(tmp_path, source)
    value = _candidate(source, context=context)
    value["context_audit"] = [_audit()]
    value["leaf_kcs"][0]["context_evidence"] = []
    with pytest.raises(ValueError, match="lacks the cited context evidence"):
        ProposedKCSet.model_validate(value)


def test_multiple_claims_can_remain_in_one_kc_without_a_count_rule(tmp_path, source):
    context = _context(tmp_path, source)
    value = _candidate(source, context=context)
    value["context_audit"] = [_audit(), deepcopy(_audit())]
    value["context_audit"][1]["claim"] = "Explain the exception boundary."
    ProposedKCSet.model_validate(value).validate_against_source(
        payload().with_source(source), context, require_context_audit=True,
    )
    assert len(value["leaf_kcs"]) == 1


def test_native_kc_task_declares_context_accountability_and_preserves_failed_raw(tmp_path):
    from pathlib import Path

    from learning_authoring.agent_session import agent_import, prepare_agent_task
    from learning_authoring.artifacts import read_json
    from tests.test_agent_context_slots import _init
    from tests.test_agent_session import _kc_candidate, _write_raw

    run, source = _init(tmp_path, notes=True)
    task = prepare_agent_task("kc", run, allow_proposed_extraction_demo=True)
    package = read_json(Path(task["task_package"]))
    assert package["input_boundary"]["context_accountability"] == "claim_to_kc_audit_required"
    candidate = _kc_candidate(source)
    candidate["source_ref"] = package["input_boundary"]["expected_source_ref"]
    path = run / "missing-context-audit.json"
    raw = _write_raw(path, candidate)
    with pytest.raises(ValueError, match="context_audit omits"):
        agent_import("kc", run, path, task_package=Path(task["task_package"]))
    assert not (run / "kc-proposed.json").exists()
    assert any(p.read_bytes() == raw for p in (run / "agent-session/candidates").glob("kc-*.json"))
