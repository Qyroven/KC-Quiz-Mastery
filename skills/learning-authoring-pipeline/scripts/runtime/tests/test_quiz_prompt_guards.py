"""Prompt-presence regressions, not evidence that generated questions pass review."""

from __future__ import annotations

import re

import pytest

from learning_authoring.quiz import load_quiz_prompt_package
from learning_authoring.quiz_semantics import load_semantic_review_prompt


def normalized(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize(
    ("generator_rule", "review_rule"),
    [
        (
            "Score only work the visible task requests",
            "Compare every scored requirement with the visible task",
        ),
        (
            "does not authorize a hidden mandatory set of categories",
            "does not imply a hidden mandatory set of categories",
        ),
        (
            "without demanding an unrequested derivation",
            "need not show an unrequested derivation",
        ),
        (
            "open to valid alternative methods or wording",
            "equivalent values or methods must not lose credit",
        ),
        (
            "recognition, not analysis of that distinction",
            "naming it is not analysis of that distinction",
        ),
        (
            "an extreme or unrelated strawman is not a meaningful alternative",
            "strawmen can expose the only sensible option",
        ),
        (
            "an ordered mnemonic can expose the whole key without using answer IDs",
            "complete matching or ordering key without answer IDs",
        ),
        (
            "choosing or recalling it is not itself the target being scored",
            "leaks the answer when selecting or recalling it is the target",
        ),
        (
            "distinguish executable code from pseudocode or an incomplete excerpt",
            "distinguish executable code, pseudocode, and labeled excerpts",
        ),
        (
            "never silently treat `...` or an omitted operation as working code",
            "do not assume `...` performs an omitted operation",
        ),
        (
            "construct the strongest plausible alternative answer",
            "Construct the strongest plausible competing answer",
        ),
        (
            "Do not state that each required evidence category is absent",
            "explicitly announces each missing evidence category",
        ),
        (
            "including a rationale, derivation, evidence citation, required category, example",
            "An unrequested example is also a hidden deliverable",
        ),
        (
            "include at least one plausible unused right-side option",
            "the final pair derivable by elimination",
        ),
        (
            "Use `short_text` only when learner-authored explanation",
            "Compare every scored requirement with the visible task",
        ),
        (
            "identify the exact learner-visible phrase that requests the scored work",
            "Compare every scored requirement with the visible task",
        ),
    ],
)
def test_generator_and_independent_review_share_quality_guards(
    generator_rule: str, review_rule: str
) -> None:
    generator = normalized(load_quiz_prompt_package().instructions)
    reviewer = normalized("\n".join(load_semantic_review_prompt().values()))
    assert generator_rule in generator
    assert review_rule in reviewer


def test_quality_guards_preserve_simple_tasks_flexibility_and_independent_review() -> None:
    generator = normalized(load_quiz_prompt_package().instructions)
    reviewer = normalized("\n".join(load_semantic_review_prompt().values()))
    assert "Recall and ordinary calculation can be useful evidence" in generator
    assert "Recall and ordinary numerical practice are valid" in reviewer
    assert (
        "no universal question count, Bloom ladder, difficulty mix, or interaction quota"
        in generator
    )
    assert "one response" in generator
    assert "or generation call when a learner requests a hint" in generator
    assert "no safe helpful hint" in generator
    assert "Do not require a fixed count" in reviewer
    assert "Do not force a quota of PASS, REVIEW, or REJECT" in reviewer
    assert "Do not rewrite it to match the key" in reviewer
    assert "never human approval" in reviewer
    for prompt in (generator, reviewer):
        assert not re.search(r"\b(?:KC-\d+|DQ-\d+|Day\s*\d+)\b", prompt, re.IGNORECASE)
