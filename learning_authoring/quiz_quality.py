"""Deterministic form diagnostics for Quiz output; never a semantic quality score."""

from __future__ import annotations

import re
import statistics
import unicodedata
from itertools import combinations
from typing import Any

from learning_authoring.quiz_contracts import QuizBatch, QuizQuestion

_TOKEN = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_STOPWORDS = {
    "a",
    "b",
    "c",
    "d",
    "các",
    "có",
    "của",
    "cho",
    "được",
    "để",
    "gì",
    "khi",
    "là",
    "một",
    "này",
    "nên",
    "những",
    "phải",
    "sau",
    "sẽ",
    "theo",
    "thì",
    "trong",
    "từ",
    "và",
    "với",
    "về",
    "ở",
    "như",
    "hãy",
    "chọn",
}
_ABSOLUTE_CUES = {
    "always",
    "never",
    "everyone",
    "random",
    "ignore",
    "chac chan",
    "luon",
    "ngau nhien",
    "bo qua",
    "cam moi",
    "hoan toan",
    "lap tuc",
    "khong can",
}


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN.findall(_fold(text))
        if len(token) > 2 and token not in _STOPWORDS
    }


def _visible_stem(question: QuizQuestion) -> str:
    stimulus = question.stimulus
    table_text = " ".join(value for row in stimulus.table_rows for value in row)
    return " ".join(
        value
        for value in (stimulus.text, table_text, stimulus.formula, question.prompt)
        if value
    )


def _jaccard(left: str, right: str) -> float:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


def _cue_hits(text: str) -> set[str]:
    folded = _fold(text)
    return {
        cue
        for cue in _ABSOLUTE_CUES
        if re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", folded)
    }


def _issue(code: str, severity: str, note: str, **metrics: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "note": note, "metrics": metrics}


def _choice_issues(question: QuizQuestion) -> list[dict[str, Any]]:
    if question.interaction not in {"single_select", "multi_select"}:
        return []
    options = question.choice_options
    correct_ids = set(question.correct_answer.selection_ids)
    lengths = {option.option_id: len(option.text.strip()) for option in options}
    median_length = statistics.median(lengths.values())
    longest = max(lengths.values())
    shortest = min(lengths.values())
    issues: list[dict[str, Any]] = []

    uniquely_longest = [option_id for option_id, value in lengths.items() if value == longest]
    if (
        len(uniquely_longest) == 1
        and uniquely_longest[0] in correct_ids
        and longest >= max(median_length * 1.2, shortest + 18)
    ):
        issues.append(
            _issue(
                "CORRECT_OPTION_LENGTH_CUE",
                "major",
                "Đáp án đúng là lựa chọn dài nhất duy nhất và dài hơn đáng kể.",
                option_lengths=lengths,
            )
        )
    if shortest and longest / shortest > 1.8:
        issues.append(
            _issue(
                "OPTION_LENGTH_IMBALANCE",
                "minor",
                "Các lựa chọn chênh lệch độ dài lớn; cần review tính song song.",
                ratio=round(longest / shortest, 3),
                option_lengths=lengths,
            )
        )

    stem = _visible_stem(question)
    overlaps = {option.option_id: _jaccard(stem, option.text) for option in options}
    correct_overlap = max((overlaps[item] for item in correct_ids), default=0.0)
    distractor_overlap = max(
        (value for option_id, value in overlaps.items() if option_id not in correct_ids),
        default=0.0,
    )
    if correct_overlap >= 0.22 and correct_overlap >= distractor_overlap + 0.1:
        issues.append(
            _issue(
                "CORRECT_OPTION_LEXICAL_CUE",
                "minor",
                "Đáp án đúng lặp từ với stem nổi bật hơn distractor.",
                overlaps={key: round(value, 3) for key, value in overlaps.items()},
            )
        )

    for left, right in combinations(options, 2):
        similarity = _jaccard(left.text, right.text)
        left_numbers = set(_NUMBER.findall(left.text))
        right_numbers = set(_NUMBER.findall(right.text))
        if left_numbers and right_numbers and left_numbers != right_numbers:
            continue
        if similarity >= 0.78:
            issues.append(
                _issue(
                    "NEAR_DUPLICATE_OPTIONS",
                    "minor",
                    "Hai lựa chọn gần trùng nội dung.",
                    option_ids=[left.option_id, right.option_id],
                    similarity=round(similarity, 3),
                )
            )

    correct_cues = set().union(
        *(_cue_hits(option.text) for option in options if option.option_id in correct_ids),
        set(),
    )
    distractor_cues = set().union(
        *(_cue_hits(option.text) for option in options if option.option_id not in correct_ids),
        set(),
    )
    if distractor_cues and not correct_cues:
        issues.append(
            _issue(
                "ABSOLUTIST_DISTRACTOR_CUE",
                "major",
                "Distractor có từ tuyệt đối hoặc hành động dễ bị loại bằng giọng văn.",
                distractor_cues=sorted(distractor_cues),
            )
        )
    return issues


def _matching_issues(question: QuizQuestion) -> list[dict[str, Any]]:
    if question.interaction != "matching":
        return []
    issues: list[dict[str, Any]] = []
    mappings = question.correct_answer.mappings
    mapped_right = [mapping.right for mapping in mappings]
    if (
        len(question.matching_left) == len(question.matching_right)
        and len(mapped_right) == len(set(mapped_right))
    ):
        issues.append(
            _issue(
                "ONE_TO_ONE_MATCHING_ELIMINATION",
                "minor",
                "Số vế trái và phải bằng nhau; cặp cuối có thể suy ra bằng elimination.",
            )
        )
    displayed_right = [option.option_id for option in question.matching_right]
    mapped_by_left = {mapping.left: mapping.right for mapping in mappings}
    aligned_right = [mapped_by_left.get(option.option_id) for option in question.matching_left]
    if aligned_right == displayed_right[: len(aligned_right)]:
        issues.append(
            _issue(
                "MATCHING_POSITION_CUE",
                "major",
                "Các đáp án đúng được serialize cùng vị trí với vế trái.",
            )
        )
    return issues


def _ordering_issues(question: QuizQuestion) -> list[dict[str, Any]]:
    if question.interaction != "ordering":
        return []
    displayed = [option.option_id for option in question.ordering_options]
    if displayed == question.correct_answer.ordering:
        return [
            _issue(
                "ORDERING_ALREADY_SOLVED",
                "major",
                "Các lựa chọn đã được serialize đúng thứ tự đáp án.",
            )
        ]
    return []


def build_quiz_form_audit(batch: QuizBatch) -> dict[str, Any]:
    """Flag surface cues only; absence of flags never means the question is good."""

    question_reports: list[dict[str, Any]] = []
    for question in batch.questions:
        issues = (
            _choice_issues(question)
            + _matching_issues(question)
            + _ordering_issues(question)
        )
        question_reports.append(
            {
                "question_id": question.question_id,
                "status": "FORM_REVIEW" if issues else "NO_FORM_FLAG",
                "issues": issues,
            }
        )

    portfolio_issues: list[dict[str, Any]] = []
    for left, right in combinations(batch.questions, 2):
        if left.kc_id != right.kc_id:
            continue
        similarity = _jaccard(_visible_stem(left), _visible_stem(right))
        if similarity >= 0.58:
            portfolio_issues.append(
                _issue(
                    "NEAR_DUPLICATE_QUESTION_STEMS",
                    "minor",
                    "Hai biến thể của cùng KC có stem gần trùng.",
                    question_ids=[left.question_id, right.question_id],
                    similarity=round(similarity, 3),
                )
            )

    flag_count = sum(len(report["issues"]) for report in question_reports)
    flag_count += len(portfolio_issues)
    return {
        "audit_version": "quiz-form-audit.v1",
        "scope": "surface-form heuristics only; not semantic correctness or approval",
        "raw_output_modified": False,
        "summary": {
            "question_count": len(batch.questions),
            "flag_count": flag_count,
            "status": "HAS_FORM_FLAGS" if flag_count else "NO_FORM_FLAGS",
        },
        "questions": question_reports,
        "portfolio": {"issues": portfolio_issues},
    }
