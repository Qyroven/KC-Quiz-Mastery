"""Original KC JSON shape is part of the review baseline, not a formatting choice."""

from __future__ import annotations

from copy import deepcopy

import pytest

from learning_authoring.artifacts import read_json, sha256_file, write_json
from learning_authoring.kc_contracts import ProposedKCSet
from learning_authoring.quiz import QuizConfig, build_quiz_input, prepare_quiz_request
from tests.test_quiz import KC_SHA256, kc_set


def _raw_kcs(source):
    raw = kc_set(source).model_dump(mode="json", exclude_unset=True)
    for kc in raw["leaf_kcs"]:
        kc.pop("warning_codes", None)
    return raw


def test_selected_kcs_preserve_original_shape_order_and_group_membership(source) -> None:
    raw = _raw_kcs(source)
    before = deepcopy(raw)
    validated = ProposedKCSet.model_validate(raw)
    selected = build_quiz_input(
        validated, raw_kc_set=raw, kc_set_sha256=KC_SHA256,
        config=QuizConfig(selected_kc_ids=("KC-002", "KC-001")),
    )

    assert selected["leaf_kcs"] == list(reversed(raw["leaf_kcs"]))
    assert selected["kc_groups"] == raw["kc_groups"]
    assert "context_evidence" not in selected["leaf_kcs"][0]
    assert "warning_codes" not in selected["leaf_kcs"][0]
    assert raw == before
    selected["leaf_kcs"][0]["assessment_boundary"]["included"].append("Changed consumer copy")
    selected["kc_groups"][0]["leaf_kc_ids"].clear()
    assert raw == before
    assert validated == ProposedKCSet.model_validate(before)


@pytest.mark.parametrize("field", ["name", "source_summary", "source_ref"])
def test_raw_snapshot_must_match_the_validated_kc_source(source, field) -> None:
    raw = _raw_kcs(source)
    validated = ProposedKCSet.model_validate(raw)
    if field == "name":
        raw["leaf_kcs"][0]["name"] = "Unrelated content"
    elif field == "source_ref":
        raw[field]["source_sha256"] = "d" * 64
    else:
        raw[field] = "Unrelated summary"
    with pytest.raises(ValueError, match="raw KC set does not match"):
        build_quiz_input(
            validated, raw_kc_set=raw, kc_set_sha256=KC_SHA256,
            config=QuizConfig(include_all_kcs=True),
        )


def test_legacy_preview_also_preserves_original_kc_records(tmp_path, source) -> None:
    raw = _raw_kcs(source)
    path = tmp_path / "kc-proposed.json"
    write_json(path, raw)
    before = sha256_file(path)

    prepare_quiz_request(
        tmp_path, config=QuizConfig(selected_kc_ids=("KC-001",), variants_per_kc=1),
    )

    preview = read_json(tmp_path / "quiz/quiz-input.json")
    assert preview["leaf_kcs"] == raw["leaf_kcs"][:1]
    assert preview["kc_groups"] == raw["kc_groups"]
    assert sha256_file(path) == before
