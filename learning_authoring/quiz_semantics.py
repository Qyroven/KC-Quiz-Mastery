"""Contracts for an initial, source-bound Quiz check by a coding agent.

This module neither calls a model nor changes Quiz content. Deterministic checks
can verify identity, coverage, and evidence locations, not the truth of a
reviewer's judgments or whether it actually inspected an attachment. The host
must bind the reviewer mode and enforce the solve-before-key workflow.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

VERDICTS = ("PASS", "REVIEW", "REJECT")
CRITERIA = ("grounding", "answerability", "alignment", "scoring", "cues_and_variants", "hints")
AUDIT_STATUSES = (*VERDICTS, "NOT_REVIEWED", "STALE")
PROMPT_COMPONENTS = ("foundation", "rulebook", "task")
DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent / "prompts" / "quiz-review-v1"

Nonblank = Annotated[str, Field(min_length=1, pattern=r"\S")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Verdict = Literal["PASS", "REVIEW", "REJECT"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SemanticAuditSourceRef(_StrictModel):
    """Hashes are supplied by the host, including exact immutable Quiz bytes."""

    quiz_sha256: Sha256
    kc_set_sha256: Sha256
    source_sha256: Sha256
    authoring_context_sha256: Sha256 | None
    review_input_sha256: Sha256


class SemanticReviewer(_StrictModel):
    """Host-bound execution provenance, not a self-awarded quality label."""

    mode: Literal["independent", "self_review"]
    label: Nonblank
    model: Nonblank | None


class SemanticAuditScope(_StrictModel):
    """Coverage of source needed for these questions, never the whole course."""

    source_coverage: Literal["complete", "partial", "unknown"]
    checked_source_pages: list[Annotated[int, Field(ge=1, strict=True)]]
    checked_context_ids: list[Annotated[str, Field(pattern=r"^CTX-[0-9]+$")]]
    limitations: list[Nonblank]

    @model_validator(mode="after")
    def inspection_lists_are_unique(self) -> SemanticAuditScope:
        if len(self.checked_source_pages) != len(set(self.checked_source_pages)):
            raise ValueError("checked_source_pages must be unique")
        if len(self.checked_context_ids) != len(set(self.checked_context_ids)):
            raise ValueError("checked_context_ids must be unique")
        if self.source_coverage != "complete" and not self.limitations:
            raise ValueError("partial or unknown source coverage needs a concrete limitation")
        return self


class SemanticEvidenceLocator(_StrictModel):
    """An RFC 6901 pointer into a supplied snapshot, never an arbitrary file/URL."""

    artifact: Literal["quiz", "kc", "extraction", "context"]
    pointer: str
    quote: Nonblank | None

    @model_validator(mode="after")
    def pointer_has_valid_syntax(self) -> SemanticEvidenceLocator:
        if (self.pointer and not self.pointer.startswith("/")) or re.search(
            r"~(?:[^01]|$)", self.pointer
        ):
            raise ValueError("evidence pointer must use RFC 6901 syntax")
        return self


class SemanticIssue(_StrictModel):
    stage: Literal["extraction", "kc", "quiz", "hint", "scoring"]
    observation: Nonblank
    locators: list[SemanticEvidenceLocator] = Field(min_length=1)


class SemanticCriterion(_StrictModel):
    verdict: Verdict
    rationale: Nonblank
    issues: list[SemanticIssue]

    @model_validator(mode="after")
    def verdict_and_findings_agree(self) -> SemanticCriterion:
        if self.verdict != "PASS" and not self.issues:
            raise ValueError("REVIEW and REJECT require a concrete issue with evidence locations")
        if self.verdict == "PASS" and self.issues:
            raise ValueError("a criterion with an unresolved issue cannot be PASS")
        return self


class SemanticQuestionReview(_StrictModel):
    question_id: Annotated[str, Field(pattern=r"^Q-[0-9]+$")]
    kc_id: Annotated[str, Field(pattern=r"^KC-[0-9]+$")]
    slot_id: Nonblank | None
    independent_answer: Nonblank
    grounding: SemanticCriterion
    answerability: SemanticCriterion
    alignment: SemanticCriterion
    scoring: SemanticCriterion
    cues_and_variants: SemanticCriterion
    hints: SemanticCriterion


class QuizSemanticAudit(_StrictModel):
    """An initial AI review, not an approval or a mastery measurement."""

    schema_version: Literal["quiz-semantic-audit.v1"]
    source_ref: SemanticAuditSourceRef
    reviewer: SemanticReviewer
    scope: SemanticAuditScope
    questions: list[SemanticQuestionReview] = Field(min_length=1)

    @model_validator(mode="after")
    def question_ids_are_unique(self) -> QuizSemanticAudit:
        ids = [question.question_id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("semantic review must contain each question exactly once")
        return self


def semantic_review_schema() -> dict[str, Any]:
    """Native agent schema: all judgments and nullable provenance are explicit."""

    return QuizSemanticAudit.model_json_schema()


def load_semantic_review_prompt(prompt_dir: Path = DEFAULT_PROMPT_DIR) -> dict[str, str]:
    """Load the small, ordered prompt package; no provider or run filesystem work."""

    return {
        name: (prompt_dir / f"{name}.md").read_text(encoding="utf-8") for name in PROMPT_COMPONENTS
    }


def _payload(value: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value)


def _parse_report(
    value: QuizSemanticAudit | Mapping[str, Any] | str | bytes,
) -> QuizSemanticAudit:
    if isinstance(value, QuizSemanticAudit):
        # Revalidate so a post-construction mutation cannot bypass strict contracts.
        return QuizSemanticAudit.model_validate(value.model_dump(mode="json"))
    if isinstance(value, str | bytes):
        return QuizSemanticAudit.model_validate_json(value)
    return QuizSemanticAudit.model_validate(value)


def _question_map(quiz: BaseModel | Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw_questions = _payload(quiz).get("questions", [])
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValueError("a nonempty frozen Quiz is required for semantic review")
    result = {}
    for question in raw_questions:
        if not isinstance(question, dict) or not isinstance(question.get("question_id"), str):
            raise ValueError("frozen Quiz contains an invalid question identity")
        if question["question_id"] in result:
            raise ValueError("frozen Quiz has duplicate question IDs")
        result[question["question_id"]] = question
    return result


def _citation_requirements(questions: Mapping[str, dict[str, Any]]) -> tuple[set[int], set[str]]:
    pages = {
        reference["page"]
        for question in questions.values()
        for reference in question.get("evidence_refs", [])
    }
    contexts = {
        reference["context_id"]
        for question in questions.values()
        for reference in question.get("context_evidence_refs", [])
    }
    return pages, contexts


def _resolve_pointer(artifact: Any, pointer: str) -> Any:
    current = artifact
    if not pointer:
        return current
    for encoded in pointer[1:].split("/"):
        key = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and re.fullmatch(r"0|[1-9][0-9]*", key):
            index = int(key)
            if index >= len(current):
                raise ValueError(f"evidence pointer does not resolve: {pointer}")
            current = current[index]
        else:
            raise ValueError(f"evidence pointer does not resolve: {pointer}")
    return current


def _check_question_coverage(report: QuizSemanticAudit, quiz: dict[str, Any]) -> None:
    questions = _question_map(quiz)
    if {question.question_id for question in report.questions} != set(questions):
        raise ValueError("semantic review must cover exactly the frozen Quiz question IDs")
    for reviewed in report.questions:
        original = questions[reviewed.question_id]
        if reviewed.kc_id != original.get("kc_id") or reviewed.slot_id != original.get("slot_id"):
            raise ValueError(f"{reviewed.question_id} semantic review has a changed KC or slot")


def _check_source_identity(report: QuizSemanticAudit, quiz: dict[str, Any]) -> None:
    quiz_source = quiz.get("source_ref", {})
    for name, quiz_name in (
        ("kc_set_sha256", "kc_set_sha256"),
        ("source_sha256", "extraction_source_sha256"),
        ("authoring_context_sha256", "authoring_context_sha256"),
    ):
        if getattr(report.source_ref, name) != quiz_source.get(quiz_name):
            raise ValueError(f"semantic review {name} does not match the frozen Quiz")


def validate_semantic_audit(
    report: QuizSemanticAudit | Mapping[str, Any] | str | bytes,
    *,
    quiz: BaseModel | Mapping[str, Any],
    expected_source_ref: SemanticAuditSourceRef | Mapping[str, Any],
    artifacts: Mapping[str, Any] | None = None,
    expected_reviewer: (
        SemanticReviewer | Mapping[str, Any] | Literal["independent", "self_review"] | None
    ) = None,
) -> QuizSemanticAudit:
    """Verify contract, frozen lineage, dynamic coverage, and every issue locator.

    The host calculates ``expected_source_ref`` from immutable run inputs; the
    function cannot hash Quiz bytes reconstructed from a dict. ``artifacts``
    contains the exact packet snapshots (quiz/kc/extraction/context), not paths.
    ``quiz`` always overrides the quiz snapshot to prevent locator spoofing.
    Pass the host-bound reviewer mode or full metadata when importing a real audit.
    A mode-only binding leaves label/model as reviewer-reported, not authenticated.
    This returns a parsed report; callers retain the raw candidate bytes.
    """

    parsed = _parse_report(report)
    expected = SemanticAuditSourceRef.model_validate(_payload(expected_source_ref))
    if parsed.source_ref != expected:
        raise ValueError("semantic review source_ref does not match its frozen review input")
    if isinstance(expected_reviewer, str):
        if expected_reviewer not in {"independent", "self_review"}:
            raise ValueError("unknown host-bound reviewer mode")
        if parsed.reviewer.mode != expected_reviewer:
            raise ValueError("semantic review reviewer mode does not match host-bound provenance")
    elif expected_reviewer is not None and parsed.reviewer != SemanticReviewer.model_validate(
        _payload(expected_reviewer)
    ):
        raise ValueError("semantic review reviewer does not match host-bound execution provenance")
    quiz_payload = _payload(quiz)
    _check_source_identity(parsed, quiz_payload)
    _check_question_coverage(parsed, quiz_payload)
    required_pages, required_contexts = _citation_requirements(_question_map(quiz_payload))
    checked_pages = set(parsed.scope.checked_source_pages)
    checked_contexts = set(parsed.scope.checked_context_ids)
    if parsed.scope.source_coverage == "complete" and (
        required_pages - checked_pages or required_contexts - checked_contexts
    ):
        raise ValueError("complete scope must cover all cited Quiz pages and lecturer context IDs")

    supplied = dict(artifacts or {})
    supplied["quiz"] = quiz_payload
    extraction = supplied.get("extraction")
    if isinstance(extraction, Mapping) and "pages" in extraction:
        available_pages = {page["page_number"] for page in extraction["pages"]}
        if checked_pages - available_pages:
            raise ValueError("semantic review claims a page outside the supplied source snapshot")
    # The context snapshot can be a full manifest or a list of cited snippets.
    # Only CTX IDs are checked here; exact file/manifest hashes belong to the host.
    context = supplied.get("context")
    if isinstance(context, Mapping):
        items = context.get("items", context.get("citations"))
    else:
        items = None
    if isinstance(items, list):
        available_contexts = {item["context_id"] for item in items}
        if checked_contexts - available_contexts:
            raise ValueError("semantic review claims an unknown supplied lecturer context ID")
    for reviewed in parsed.questions:
        for name in CRITERIA:
            for issue in getattr(reviewed, name).issues:
                for locator in issue.locators:
                    if locator.artifact not in supplied:
                        raise ValueError(
                            f"missing evidence artifact for locator: {locator.artifact}"
                        )
                    located = _resolve_pointer(supplied[locator.artifact], locator.pointer)
                    if locator.quote is not None and (
                        not isinstance(located, str) or locator.quote not in located
                    ):
                        raise ValueError("semantic evidence quote is not present at its locator")
    return parsed


def _scope_limitations(report: QuizSemanticAudit, quiz: dict[str, Any] | None) -> list[str]:
    reasons = list(report.scope.limitations)
    if report.reviewer.mode != "independent":
        reasons.append("Self-review is not an independent initial check.")
    if report.scope.source_coverage != "complete":
        reasons.append("Required source coverage is incomplete or unknown.")
    if quiz is not None:
        pages, contexts = _citation_requirements(_question_map(quiz))
        if pages - set(report.scope.checked_source_pages):
            reasons.append("Some cited PDF pages were not declared inspected.")
        if contexts - set(report.scope.checked_context_ids):
            reasons.append("Some cited lecturer context was not declared inspected.")
    return reasons


def _verdict(question: SemanticQuestionReview, *, limited: bool) -> str:
    verdicts = [getattr(question, name).verdict for name in CRITERIA]
    if "REJECT" in verdicts:
        return "REJECT"
    if limited or "REVIEW" in verdicts:
        return "REVIEW"
    return "PASS"


def _hint_metadata_limitations(question: Mapping[str, Any]) -> list[str]:
    """Known absence of hint decisions is not evidence of validated hint quality."""

    hints = question.get("hints")
    reason = question.get("hint_absence_reason")
    explicit = "hints" in question and "hint_absence_reason" in question
    explicit = explicit and isinstance(hints, list)
    if hints:
        explicit = explicit and reason is None
    else:
        explicit = explicit and isinstance(reason, str) and bool(reason.strip())
    if explicit:
        return []
    return [
        "Legacy or incomplete hint metadata: no explicit hint decision was recorded. "
        "This is a coverage limitation, not a semantic defect judgment."
    ]


def _unavailable_summary(
    status: Literal["NOT_REVIEWED", "STALE"],
    reason: str,
    quiz: dict[str, Any] | None,
) -> dict[str, Any]:
    questions = [
        {
            "question_id": question["question_id"],
            "kc_id": question["kc_id"],
            "slot_id": question.get("slot_id"),
            "status": status,
        }
        for question in (_question_map(quiz).values() if quiz is not None else [])
    ]
    return {
        "schema_version": "quiz-semantic-audit-summary.v1",
        "status": status,
        "reviewer": None,
        "scope": None,
        "reasons": [reason],
        "counts": {key: len(questions) if key == status else 0 for key in AUDIT_STATUSES},
        "questions": questions,
        "initial_check_only": True,
        "human_approved": False,
    }


def semantic_audit_summary(
    report: QuizSemanticAudit | Mapping[str, Any] | str | bytes | None,
    *,
    quiz: BaseModel | Mapping[str, Any] | None = None,
    expected_source_ref: SemanticAuditSourceRef | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a fail-closed UI status without rewriting the underlying report.

    Pass the current ``quiz`` and host-recomputed ``expected_source_ref`` for a
    usable status. Their absence limits even an all-PASS audit to REVIEW. Hash
    or item-identity mismatch is STALE; an absent/malformed audit is NOT_REVIEWED.
    The run loader must first validate locators and frozen reviewer provenance.
    """

    quiz_payload = _payload(quiz) if quiz is not None else None
    if report is None:
        return _unavailable_summary(
            "NOT_REVIEWED", "No initial semantic review is present.", quiz_payload
        )
    try:
        parsed = _parse_report(report)
    except (ValidationError, ValueError, TypeError):
        return _unavailable_summary(
            "NOT_REVIEWED", "No valid semantic review is present.", quiz_payload
        )
    try:
        if expected_source_ref is not None:
            expected = SemanticAuditSourceRef.model_validate(_payload(expected_source_ref))
            if parsed.source_ref != expected:
                raise ValueError("changed source binding")
        if quiz_payload is not None:
            _check_source_identity(parsed, quiz_payload)
            _check_question_coverage(parsed, quiz_payload)
    except (ValidationError, ValueError, TypeError):
        return _unavailable_summary(
            "STALE",
            "The review no longer matches this Quiz or its frozen source inputs.",
            quiz_payload,
        )
    reasons = _scope_limitations(parsed, quiz_payload)
    if expected_source_ref is None or quiz_payload is None:
        reasons.append("Current Quiz identity and source binding have not both been verified.")
    limited = bool(reasons)
    reviewed_by_id = {question.question_id: question for question in parsed.questions}
    question_ids = _question_map(quiz_payload) if quiz_payload is not None else reviewed_by_id
    questions = []
    hint_metadata_missing = False
    for question_id in question_ids:
        item_reasons = (
            _hint_metadata_limitations(question_ids[question_id])
            if quiz_payload is not None
            else []
        )
        hint_metadata_missing = hint_metadata_missing or bool(item_reasons)
        questions.append(
            {
                **reviewed_by_id[question_id].model_dump(mode="json"),
                "status": _verdict(
                    reviewed_by_id[question_id], limited=limited or bool(item_reasons)
                ),
                "status_reasons": item_reasons,
            }
        )
    if hint_metadata_missing:
        reasons.append("Some questions lack explicit hint decision metadata.")
    statuses = [question["status"] for question in questions]
    status = "REJECT" if "REJECT" in statuses else "REVIEW" if "REVIEW" in statuses else "PASS"
    return {
        "schema_version": "quiz-semantic-audit-summary.v1",
        "status": status,
        "reviewer": parsed.reviewer.model_dump(mode="json"),
        "scope": parsed.scope.model_dump(mode="json"),
        "reasons": reasons,
        "counts": {key: statuses.count(key) for key in AUDIT_STATUSES},
        "questions": questions,
        "initial_check_only": True,
        "human_approved": False,
    }
