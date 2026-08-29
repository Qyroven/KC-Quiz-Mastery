"""Deterministic form diagnostics for Quiz output; never a semantic quality score."""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
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
_NEGATION = re.compile(
    r"(?<!\w)(?:"
    r"not|never|no longer|cannot|can not|can't|doesn't|does not|isn't|is not|"
    r"aren't|are not|without|unnecessary|unsupported|unproven|"
    r"khong|khong con|khong can|chang|chua"
    r")(?!\w)"
)
_AFFIRMATIVE = re.compile(
    r"(?<!\w)(?:"
    r"should|must|required|necessary|remains?|keep|retain|use|reduce|trim|"
    r"summari[sz]e|limit|include|"
    r"nen|phai|can|van|giu|dung|giam|cat|tom tat|gioi han|bao gom"
    r")(?!\w)"
)
_POLARITY_WORDS = {
    "not",
    "never",
    "no",
    "longer",
    "cannot",
    "without",
    "unnecessary",
    "unsupported",
    "unproven",
    "should",
    "must",
    "required",
    "necessary",
    "remain",
    "remains",
    "keep",
    "retain",
    "use",
    "reduce",
    "trim",
    "summarize",
    "summarise",
    "limit",
    "include",
    "khong",
    "con",
    "chang",
    "chua",
    "nen",
    "phai",
    "can",
    "van",
    "giu",
    "dung",
    "giam",
    "cat",
    "tom",
    "tat",
    "gioi",
    "han",
    "bao",
    "gom",
}
_DIMENSION_DECLARATION = re.compile(
    r"(?<!\w)d[_\s]*([a-z][a-z0-9]*)\s*=\s*(\d+)(?![\d.,])",
    re.IGNORECASE,
)
_VECTOR_ASSIGNMENT = re.compile(
    r"(?<!\w)([a-z][a-z0-9_]*)\s*=\s*\[([^\[\]]+)\]",
    re.IGNORECASE,
)
_SOFTMAX_OUTPUT = re.compile(
    r"softmax\s*\(\s*\[[^\[\]]+\]\s*\)\s*(?:=|≈|~=|~|->|→)\s*"
    r"\[([^\[\]]+)\]",
    re.IGNORECASE,
)
_NORMALIZATION_LABEL = re.compile(
    r"(?<!\w)(?:softmax|probabilit(?:y|ies)|probability distribution|"
    r"attention weights?|normalized(?: weights?| distribution)?|"
    r"trong so|xac suat|phan bo xac suat)(?!\w)",
    re.IGNORECASE,
)
_NORMALIZED_VECTOR = re.compile(
    r"(?<!\w)(softmax|probabilit(?:y|ies)|probability distribution|"
    r"attention weights?|normalized(?: weights?| distribution)?|"
    r"trong so|xac suat|phan bo xac suat)\s*(?:[:=]|la)\s*"
    r"\[([^\[\]]+)\]",
    re.IGNORECASE,
)
_NUMBER_WORD_VALUES = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "mot": 1,
    "hai": 2,
    "ba": 3,
    "bon": 4,
    "tu": 4,
    "nam": 5,
    "sau": 6,
    "bay": 7,
    "tam": 8,
    "chin": 9,
    "muoi": 10,
}
_NUMBER_WORD_PATTERN = "|".join(
    sorted((re.escape(value) for value in _NUMBER_WORD_VALUES), key=len, reverse=True)
)
_ANY_OF_COUNT = re.compile(
    rf"(?:accept|allow|credit|chap nhan|any|bat ky).{{0,30}}?"
    rf"(\d+|{_NUMBER_WORD_PATTERN})\s+"
    rf"(?:of|out of|trong(?: so)?)\s+"
    rf"(\d+|{_NUMBER_WORD_PATTERN})(?!\w)"
)
_ALTERNATIVE_REQUIREMENT = re.compile(
    r"(?<!\w)(?:or|either|one of|any|hoac|mot trong|bat ky)(?!\w)"
)
_RUBRIC_REQUIREMENT_PATTERNS = {
    "rationale": re.compile(
        r"(?<!\w)(?:explain|explanation|justify|justification|rationale|reason|why|"
        r"giai thich|ly do|tai sao)(?!\w)"
    ),
    "derivation": re.compile(
        r"(?<!\w)(?:derive|derivation|show (?:the )?work|intermediate steps?|"
        r"calculation steps?|trinh bay (?:cac )?buoc|cac buoc tinh|suy dan)(?!\w)"
    ),
    "example": re.compile(r"(?<!\w)(?:example|illustration|vi du|minh hoa)(?!\w)"),
    "evidence": re.compile(
        r"(?<!\w)(?:cite|citation|evidence|source|quote|trich dan|bang chung|nguon)(?!\w)"
    ),
    "units": re.compile(r"(?<!\w)(?:unit|units|don vi)(?!\w)"),
    "format": re.compile(
        r"(?<!\w)(?:decimal places?|specific format|json|table|bullet points?|"
        r"chu so thap phan|dinh dang|bang|gach dau dong)(?!\w)"
    ),
}

# These codes describe deterministic defects that can make a structurally valid
# Quiz candidate misleading or trivially gameable.  They are intentionally a
# narrow subset of the audit: minor style flags and semantic judgement remain
# review concerns, not reasons to regenerate output automatically.
_FRESH_CANDIDATE_TRIGGER_CODES = frozenset(
    {
        "ANSWER_POSITION_IMBALANCE",
        "CORRECT_OPTION_LENGTH_PATTERN",
        "CUMULATIVE_HINT_KEY_LEAK",
        "DECLARED_DIMENSION_MISMATCH",
        "MATCHING_POSITION_CUE",
        "MULTI_SELECT_KEY_PATTERN",
        "NORMALIZATION_SUM_MISMATCH",
        "ORDERING_ALREADY_SOLVED",
        "RUBRIC_CHOICE_SET_MISMATCH",
        "RUBRIC_HIDDEN_REQUIREMENT",
    }
)


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
    column_text = " ".join(stimulus.table_columns)
    table_text = " ".join(value for row in stimulus.table_rows for value in row)
    return " ".join(
        value
        for value in (
            stimulus.text,
            column_text,
            table_text,
            stimulus.formula,
            question.prompt,
        )
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


def _polarity(text: str) -> str:
    folded = _fold(text)
    if _NEGATION.search(folded):
        return "negative"
    if _AFFIRMATIVE.search(folded):
        return "affirmative"
    return "unmarked"


def _comparable_tokens(text: str) -> set[str]:
    return _tokens(text) - _POLARITY_WORDS


def _tokens_equivalent(left: str, right: str) -> bool:
    return left == right or (min(len(left), len(right)) >= 5 and left[:5] == right[:5])


def _shared_terms(left: set[str], right: set[str]) -> set[str]:
    return {
        left_token
        for left_token in left
        if any(_tokens_equivalent(left_token, right_token) for right_token in right)
    }


def _choice_polarity_issues(question: QuizQuestion) -> list[dict[str, Any]]:
    if question.interaction not in {"single_select", "multi_select"}:
        return []
    options = {option.option_id: option for option in question.choice_options}
    clauses = [
        clause.strip()
        for clause in re.split(r"[.;\n]+", question.answer_explanation)
        if clause.strip()
    ] or [question.answer_explanation]
    mismatches: list[dict[str, Any]] = []
    for option_id in question.correct_answer.selection_ids:
        option = options[option_id]
        option_polarity = _polarity(option.text)
        if option_polarity == "unmarked":
            continue
        option_terms = _comparable_tokens(option.text)
        opposite = "affirmative" if option_polarity == "negative" else "negative"
        best_opposite: tuple[float, set[str], str] | None = None
        best_same = 0.0
        for clause in clauses:
            clause_terms = _comparable_tokens(clause)
            shared = _shared_terms(option_terms, clause_terms)
            score = len(shared) / max(1, min(len(option_terms), len(clause_terms)))
            clause_polarity = _polarity(clause)
            if clause_polarity == option_polarity:
                best_same = max(best_same, score)
            elif clause_polarity == opposite and (
                best_opposite is None or score > best_opposite[0]
            ):
                best_opposite = (score, shared, clause)
        if best_opposite is None:
            continue
        score, shared, clause = best_opposite
        supported = len(shared) >= 2 or any(len(term) >= 6 for term in shared)
        if supported and score >= 0.1 and score > best_same:
            mismatches.append(
                {
                    "option_id": option_id,
                    "option_polarity": option_polarity,
                    "explanation_polarity": opposite,
                    "shared_terms": sorted(shared),
                    "explanation_clause": clause,
                }
            )
    if not mismatches:
        return []
    return [
        _issue(
            "KEY_EXPLANATION_POLARITY_MISMATCH",
            "major",
            "Keyed option and its closest explanation clause have opposite polarity.",
            mismatches=mismatches,
        )
    ]


def _numeric_vector(body: str) -> list[float] | None:
    separator = ";" if ";" in body else "," if "," in body else None
    parts = body.split(separator) if separator else body.split()
    values: list[float] = []
    for raw_part in parts:
        part = raw_part.strip().replace("%", "").replace("−", "-")
        if not re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", part):
            return None
        values.append(float(part.replace(",", ".")))
    return values if len(values) >= 2 else None


def _dimension_issues(question: QuizQuestion) -> list[dict[str, Any]]:
    visible = _fold(_visible_stem(question))
    declarations = {
        symbol: int(size) for symbol, size in _DIMENSION_DECLARATION.findall(visible)
    }
    if not declarations:
        return []
    mismatches: list[dict[str, Any]] = []
    for variable, body in _VECTOR_ASSIGNMENT.findall(visible):
        base_symbol = re.sub(r"\d+$", "", variable).rstrip("_")
        if base_symbol not in declarations:
            continue
        values = _numeric_vector(body)
        if values is None or len(values) == declarations[base_symbol]:
            continue
        mismatches.append(
            {
                "dimension_symbol": f"d_{base_symbol}",
                "declared_dimension": declarations[base_symbol],
                "variable": variable,
                "observed_width": len(values),
            }
        )
    if not mismatches:
        return []
    return [
        _issue(
            "DECLARED_DIMENSION_MISMATCH",
            "major",
            "A declared symbol dimension conflicts with a visible vector assignment.",
            mismatches=mismatches,
        )
    ]


def _normalization_issues(question: QuizQuestion) -> list[dict[str, Any]]:
    visible = _fold(_visible_stem(question))
    candidates = [
        ("softmax", match.group(1), match.group(0))
        for match in _SOFTMAX_OUTPUT.finditer(visible)
    ]
    candidates.extend(
        (match.group(1), match.group(2), match.group(0))
        for match in _NORMALIZED_VECTOR.finditer(visible)
    )
    contradictions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for label, body, matched_text in candidates:
        signature = (label, body)
        if signature in seen:
            continue
        seen.add(signature)
        values = _numeric_vector(body)
        if values is None:
            continue
        if any(value < 0 for value in values):
            contradictions.append(
                {
                    "label": label,
                    "values": values,
                    "reason": "negative_normalized_value",
                }
            )
            continue
        total = sum(values)
        percent_notation = "%" in matched_text
        expected_total = 100.0 if percent_notation or abs(total - 100.0) <= 3.0 else 1.0
        tolerance = 1.5 if expected_total == 100.0 else 0.03
        if abs(total - expected_total) <= tolerance:
            continue
        contradictions.append(
            {
                "label": label,
                "values": values,
                "observed_total": round(total, 6),
                "expected_total": expected_total,
            }
        )
    if question.stimulus.kind == "table":
        for column_index, column in enumerate(question.stimulus.table_columns):
            if not _NORMALIZATION_LABEL.search(_fold(column)):
                continue
            raw_values = [row[column_index].strip() for row in question.stimulus.table_rows]
            if not raw_values or any(
                not re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?%?", value)
                for value in raw_values
            ):
                continue
            values = [float(value.rstrip("%").replace(",", ".")) for value in raw_values]
            if any(value < 0 for value in values):
                contradictions.append(
                    {
                        "label": column,
                        "values": values,
                        "reason": "negative_normalized_value",
                        "table_column_index": column_index,
                    }
                )
                continue
            total = sum(values)
            percent_notation = any("%" in value for value in raw_values)
            expected_total = (
                100.0 if percent_notation or abs(total - 100.0) <= 3.0 else 1.0
            )
            tolerance = 1.5 if expected_total == 100.0 else 0.03
            if abs(total - expected_total) <= tolerance:
                continue
            contradictions.append(
                {
                    "label": column,
                    "values": values,
                    "observed_total": round(total, 6),
                    "expected_total": expected_total,
                    "table_column_index": column_index,
                }
            )
    if not contradictions:
        return []
    return [
        _issue(
            "NORMALIZATION_SUM_MISMATCH",
            "major",
            "A visible normalized vector does not sum to its stated scale.",
            contradictions=contradictions,
        )
    ]


def _number_value(value: str) -> int:
    return int(value) if value.isdigit() else _NUMBER_WORD_VALUES[value]


def _any_of_requirement(text: str) -> tuple[int, int] | None:
    match = _ANY_OF_COUNT.search(_fold(text))
    if not match:
        return None
    return _number_value(match.group(1)), _number_value(match.group(2))


def _rubric_issues(question: QuizQuestion) -> list[dict[str, Any]]:
    if question.interaction != "short_text":
        return []
    issues: list[dict[str, Any]] = []
    visible = _fold(_visible_stem(question))
    hidden_requirements: list[dict[str, Any]] = []
    for index, point in enumerate(question.rubric, start=1):
        criterion = _fold(point.criterion)
        categories = [
            category
            for category, pattern in _RUBRIC_REQUIREMENT_PATTERNS.items()
            if pattern.search(criterion) and not pattern.search(visible)
        ]
        if categories:
            hidden_requirements.append(
                {"rubric_index": index, "requirement_categories": categories}
            )
    if hidden_requirements:
        issues.append(
            _issue(
                "RUBRIC_HIDDEN_REQUIREMENT",
                "major",
                "A rubric requires a response form that the learner-visible task does not request.",
                requirements=hidden_requirements,
            )
        )

    accepted_set = _any_of_requirement(question.correct_answer.text)
    if accepted_set is None:
        accepted_set = _any_of_requirement(question.answer_explanation)
    if accepted_set is not None:
        accepted_count, candidate_count = accepted_set
        fixed_criteria = [
            index
            for index, point in enumerate(question.rubric, start=1)
            if not _ALTERNATIVE_REQUIREMENT.search(_fold(point.criterion))
        ]
        if (
            accepted_count < candidate_count
            and accepted_count >= 2
            and len(fixed_criteria) >= accepted_count - 1
        ):
            issues.append(
                _issue(
                    "RUBRIC_CHOICE_SET_MISMATCH",
                    "major",
                    "The exemplar allows any N-of-M response, but the rubric fixes most criteria.",
                    accepted_count=accepted_count,
                    candidate_count=candidate_count,
                    fixed_rubric_indexes=fixed_criteria,
                )
            )
    return issues


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


def _normalized_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", _fold(text)).strip()


def _distinctive_option_tokens(question: QuizQuestion) -> dict[str, set[str]]:
    option_tokens = {
        option.option_id: _comparable_tokens(option.text) for option in question.choice_options
    }
    distinctive: dict[str, set[str]] = {}
    for option_id, tokens in option_tokens.items():
        other_tokens = set().union(
            *(
                values
                for candidate_id, values in option_tokens.items()
                if candidate_id != option_id
            ),
            set(),
        )
        distinctive[option_id] = {
            token
            for token in tokens
            if not any(_tokens_equivalent(token, other) for other in other_tokens)
        }
    return distinctive


def _covered_option_ids(
    question: QuizQuestion, cumulative_hint: str
) -> tuple[set[str], dict[str, list[str]]]:
    hint_tokens = _comparable_tokens(cumulative_hint)
    distinctive = _distinctive_option_tokens(question)
    covered_terms: dict[str, list[str]] = {}
    for option_id, tokens in distinctive.items():
        covered = sorted(
            token
            for token in tokens
            if any(_tokens_equivalent(token, hint_token) for hint_token in hint_tokens)
        )
        if covered:
            covered_terms[option_id] = covered
    return set(covered_terms), covered_terms


def _selected_response_hint_leak(
    question: QuizQuestion, cumulative_hint: str
) -> dict[str, Any] | None:
    key_ids = set(question.correct_answer.selection_ids)
    distractor_ids = {option.option_id for option in question.choice_options} - key_ids
    folded_hint = _normalized_phrase(cumulative_hint)
    exact_key_ids = {
        option.option_id
        for option in question.choice_options
        if option.option_id in key_ids
        and len(_normalized_phrase(option.text)) >= 8
        and _normalized_phrase(option.text) in folded_hint
    }
    if exact_key_ids:
        return {"mode": "exact_key_text", "covered_key_ids": sorted(exact_key_ids)}

    covered_ids, covered_terms = _covered_option_ids(question, cumulative_hint)
    covered_keys = covered_ids & key_ids
    covered_distractors = covered_ids & distractor_ids
    if question.interaction == "multi_select":
        if len(key_ids) >= 2 and covered_keys == key_ids and not covered_distractors:
            return {
                "mode": "all_key_concepts_only",
                "covered_key_ids": sorted(covered_keys),
                "covered_terms": {
                    option_id: covered_terms[option_id] for option_id in sorted(covered_keys)
                },
            }
        return None

    key_id = next(iter(key_ids))
    key_terms = _distinctive_option_tokens(question).get(key_id, set())
    covered_key_terms = covered_terms.get(key_id, [])
    coverage = len(covered_key_terms) / max(1, len(key_terms))
    if key_id in covered_keys and not covered_distractors and coverage >= 0.5:
        return {
            "mode": "distinctive_key_concepts",
            "covered_key_ids": [key_id],
            "covered_terms": {key_id: covered_key_terms},
            "distinctive_coverage": round(coverage, 3),
        }
    return None


def _short_text_hint_leak(question: QuizQuestion, cumulative_hint: str) -> dict[str, Any] | None:
    hint_tokens = _comparable_tokens(cumulative_hint)
    targets = {
        "exemplar": _comparable_tokens(question.correct_answer.text),
        "rubric": _comparable_tokens(" ".join(point.criterion for point in question.rubric)),
    }
    for target_name, target_tokens in targets.items():
        if len(target_tokens) < 4:
            continue
        covered = _shared_terms(target_tokens, hint_tokens)
        coverage = len(covered) / len(target_tokens)
        if coverage >= 0.75:
            return {
                "mode": f"{target_name}_token_coverage",
                "covered_terms": sorted(covered),
                "coverage": round(coverage, 3),
            }
    return None


def _matching_hint_leak(question: QuizQuestion, cumulative_hint: str) -> dict[str, Any] | None:
    hint = _normalized_phrase(cumulative_hint)
    left = {option.option_id: _normalized_phrase(option.text) for option in question.matching_left}
    right = {
        option.option_id: _normalized_phrase(option.text) for option in question.matching_right
    }
    exposed = [
        (mapping.left, mapping.right)
        for mapping in question.correct_answer.mappings
        if len(left[mapping.left]) >= 4
        and len(right[mapping.right]) >= 4
        and left[mapping.left] in hint
        and right[mapping.right] in hint
    ]
    if exposed and len(exposed) == len(question.correct_answer.mappings):
        return {"mode": "all_matching_pairs", "exposed_mappings": exposed}
    return None


def _ordering_hint_leak(question: QuizQuestion, cumulative_hint: str) -> dict[str, Any] | None:
    hint = _normalized_phrase(cumulative_hint)
    options = {
        option.option_id: _normalized_phrase(option.text) for option in question.ordering_options
    }
    phrases = [options[option_id] for option_id in question.correct_answer.ordering]
    if any(len(phrase) < 4 or phrase not in hint for phrase in phrases):
        return None
    positions = [hint.index(phrase) for phrase in phrases]
    if positions == sorted(positions):
        return {"mode": "complete_order", "ordering": question.correct_answer.ordering}
    return None


def _hint_issues(question: QuizQuestion) -> list[dict[str, Any]]:
    if not question.hints:
        return []
    cumulative: list[str] = []
    for prefix_length, hint in enumerate(question.hints, start=1):
        cumulative.append(hint.text)
        cumulative_hint = " ".join(cumulative)
        leak: dict[str, Any] | None
        if question.interaction in {"single_select", "multi_select"}:
            leak = _selected_response_hint_leak(question, cumulative_hint)
        elif question.interaction == "short_text":
            leak = _short_text_hint_leak(question, cumulative_hint)
        elif question.interaction == "matching":
            leak = _matching_hint_leak(question, cumulative_hint)
        else:
            leak = _ordering_hint_leak(question, cumulative_hint)
        if leak is not None:
            return [
                _issue(
                    "CUMULATIVE_HINT_KEY_LEAK",
                    "major",
                    "A prefix of the ordered hints exposes the keyed response.",
                    hint_prefix_length=prefix_length,
                    **leak,
                )
            ]
    return []


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


def _portfolio_choice_issues(batch: QuizBatch) -> list[dict[str, Any]]:
    questions = [
        question for question in batch.questions if question.interaction == "single_select"
    ]
    issues: list[dict[str, Any]] = []
    if len(questions) >= 8:
        position_counts: Counter[int] = Counter()
        uniquely_longest: list[str] = []
        for question in questions:
            key_id = question.correct_answer.selection_ids[0]
            position = next(
                index
                for index, option in enumerate(question.choice_options, start=1)
                if option.option_id == key_id
            )
            position_counts[position] += 1
            lengths = {
                option.option_id: len(option.text.strip())
                for option in question.choice_options
            }
            longest = max(lengths.values())
            if lengths[key_id] == longest and list(lengths.values()).count(longest) == 1:
                uniquely_longest.append(question.question_id)

        option_count = len(questions[0].choice_options)
        counts = {
            str(position): position_counts[position]
            for position in range(1, option_count + 1)
        }
        unused = [position for position, count in counts.items() if count == 0]
        dominant_share = max(counts.values()) / len(questions)
        if unused or dominant_share >= 0.6:
            issues.append(
                _issue(
                    "ANSWER_POSITION_IMBALANCE",
                    "major",
                    "Single-select keys are imbalanced across displayed answer positions.",
                    question_count=len(questions),
                    position_counts=counts,
                    unused_positions=unused,
                    dominant_share=round(dominant_share, 3),
                )
            )

        longest_share = len(uniquely_longest) / len(questions)
        if len(uniquely_longest) >= 5 and longest_share >= 0.6:
            issues.append(
                _issue(
                    "CORRECT_OPTION_LENGTH_PATTERN",
                    "major",
                    "The correct option is uniquely longest across most single-select questions.",
                    question_count=len(questions),
                    question_ids=uniquely_longest,
                    share=round(longest_share, 3),
                )
            )

    multi_select = [
        question for question in batch.questions if question.interaction == "multi_select"
    ]
    by_option_count: dict[int, list[QuizQuestion]] = {}
    for question in multi_select:
        by_option_count.setdefault(len(question.choice_options), []).append(question)
    for option_count, group in sorted(by_option_count.items()):
        scopes = {question.slot_id or question.kc_id for question in group}
        if len(group) < 4 or len(scopes) < 3:
            continue
        patterns: Counter[tuple[int, ...]] = Counter()
        for question in group:
            keyed = set(question.correct_answer.selection_ids)
            pattern = tuple(
                index
                for index, option in enumerate(question.choice_options, start=1)
                if option.option_id in keyed
            )
            patterns[pattern] += 1
        dominant_pattern, dominant_count = patterns.most_common(1)[0]
        dominant_share = dominant_count / len(group)
        if dominant_share >= 0.75:
            issues.append(
                _issue(
                    "MULTI_SELECT_KEY_PATTERN",
                    "major",
                    "The same displayed multi-select key shape repeats across distinct slots.",
                    question_count=len(group),
                    option_count=option_count,
                    distinct_slot_or_kc_count=len(scopes),
                    keyed_positions=list(dominant_pattern),
                    dominant_count=dominant_count,
                    dominant_share=round(dominant_share, 3),
                )
            )
    return issues


def _portfolio_interaction_issues(batch: QuizBatch) -> list[dict[str, Any]]:
    """Surface representation concentration without treating diversity as quality."""

    question_count = len(batch.questions)
    distinct_kcs = {question.kc_id for question in batch.questions}
    if question_count < 3 or len(distinct_kcs) < 2:
        return []

    counts = Counter(question.interaction for question in batch.questions)
    dominant_interaction, dominant_count = counts.most_common(1)[0]
    if dominant_count <= question_count - dominant_count:
        return []

    return [
        _issue(
            "INTERACTION_CONCENTRATION_REVIEW",
            "minor",
            (
                "One interaction represents a majority of the batch. Review whether each "
                "slot independently requires it; concentration alone is not a defect or a "
                "reason to enforce diversity."
            ),
            question_count=question_count,
            distinct_kc_count=len(distinct_kcs),
            interaction_counts=dict(sorted(counts.items())),
            dominant_interaction=dominant_interaction,
            dominant_share=round(dominant_count / question_count, 3),
        )
    ]


def build_quiz_form_audit(batch: QuizBatch) -> dict[str, Any]:
    """Flag deterministic form/consistency risks; absence never means an item is good."""

    question_reports: list[dict[str, Any]] = []
    for question in batch.questions:
        issues = (
            _choice_issues(question)
            + _choice_polarity_issues(question)
            + _matching_issues(question)
            + _ordering_issues(question)
            + _dimension_issues(question)
            + _normalization_issues(question)
            + _rubric_issues(question)
            + _hint_issues(question)
        )
        question_reports.append(
            {
                "question_id": question.question_id,
                "status": "FORM_REVIEW" if issues else "NO_FORM_FLAG",
                "issues": issues,
            }
        )

    portfolio_issues = _portfolio_choice_issues(batch) + _portfolio_interaction_issues(
        batch
    )
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
    trigger_question_ids = sorted(
        {
            report["question_id"]
            for report in question_reports
            if any(
                issue["severity"] == "major"
                and issue["code"] in _FRESH_CANDIDATE_TRIGGER_CODES
                for issue in report["issues"]
            )
        }
    )
    trigger_codes = sorted(
        {
            issue["code"]
            for report in question_reports
            for issue in report["issues"]
            if issue["severity"] == "major"
            and issue["code"] in _FRESH_CANDIDATE_TRIGGER_CODES
        }
        | {
            issue["code"]
            for issue in portfolio_issues
            if issue["severity"] == "major"
            and issue["code"] in _FRESH_CANDIDATE_TRIGGER_CODES
        }
    )
    revision_recommended = bool(trigger_codes)
    return {
        "audit_version": "quiz-form-audit.v2",
        "scope": (
            "surface-form heuristics plus deterministic internal-consistency checks; "
            "not semantic correctness or approval"
        ),
        "raw_output_modified": False,
        "summary": {
            "question_count": len(batch.questions),
            "flag_count": flag_count,
            "status": "HAS_FORM_FLAGS" if flag_count else "NO_FORM_FLAGS",
        },
        "fresh_candidate_guidance": {
            "recommended": revision_recommended,
            "trigger_codes": trigger_codes,
            "question_ids": trigger_question_ids,
            "max_fresh_candidate_revisions": 1,
            "automatic_repair_performed": False,
            "semantic_quality_proven": False,
            "next_action": (
                "AUTHOR_ONE_FRESH_CANDIDATE_FROM_THE_SAME_FROZEN_TASK"
                if revision_recommended
                else "PROCEED_TO_INDEPENDENT_SEMANTIC_REVIEW"
            ),
        },
        "questions": question_reports,
        "portfolio": {"issues": portfolio_issues},
    }
