"""Strict contracts for the single active KC-to-Quiz pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Interaction = Literal[
    "single_select",
    "multi_select",
    "matching",
    "ordering",
    "short_text",
]


class QuizSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_source_id: str = Field(min_length=1)
    extraction_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kc_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class QuizEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    block_ids: list[str] = Field(min_length=1)


class QuizOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class QuizMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: str = Field(min_length=1)
    right: str = Field(min_length=1)


class QuizStimulus(BaseModel):
    """Learner-visible context; fields for unused representations must stay empty."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["none", "text", "table", "formula"]
    text: str
    table_columns: list[str]
    table_rows: list[list[str]]
    formula: str

    @model_validator(mode="after")
    def representation_is_exact(self) -> QuizStimulus:
        if self.kind == "none":
            if self.text or self.table_columns or self.table_rows or self.formula:
                raise ValueError("none stimulus must be empty")
        elif self.kind == "text":
            if not self.text or self.table_columns or self.table_rows or self.formula:
                raise ValueError("text stimulus must use only text")
        elif self.kind == "table":
            if self.text or not self.table_columns or not self.table_rows or self.formula:
                raise ValueError("table stimulus must use only table columns and rows")
            if any(len(row) != len(self.table_columns) for row in self.table_rows):
                raise ValueError("every table row must match the column count")
        elif self.text or self.table_columns or self.table_rows or not self.formula:
            raise ValueError("formula stimulus must use only formula")
        return self


class QuizAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_ids: list[str]
    ordering: list[str]
    mappings: list[QuizMapping]
    text: str


class QuizRubricPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1)
    points: int = Field(ge=1)


class QuizQuestion(BaseModel):
    """One unapproved learner-facing question generated from one Leaf KC."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^Q-[0-9]+$")
    variant_index: int = Field(ge=1)
    kc_id: str = Field(pattern=r"^KC-[0-9]+$")
    group_id: str = Field(pattern=r"^KCG-[0-9]+$")
    title: str = Field(min_length=1)
    interaction: Interaction
    stimulus: QuizStimulus
    prompt: str = Field(min_length=1)
    choice_options: list[QuizOption]
    matching_left: list[QuizOption]
    matching_right: list[QuizOption]
    ordering_options: list[QuizOption]
    correct_answer: QuizAnswer
    rubric: list[QuizRubricPoint]
    answer_explanation: str = Field(min_length=1)
    evidence_refs: list[QuizEvidenceRef] = Field(min_length=1)

    @staticmethod
    def _ids(options: list[QuizOption], label: str) -> set[str]:
        ids = [option.option_id for option in options]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate {label} option IDs")
        return set(ids)

    @model_validator(mode="after")
    def interaction_shape_is_exact(self) -> QuizQuestion:
        choice_ids = self._ids(self.choice_options, "choice")
        left_ids = self._ids(self.matching_left, "matching-left")
        right_ids = self._ids(self.matching_right, "matching-right")
        ordering_ids = self._ids(self.ordering_options, "ordering")
        answer = self.correct_answer

        if self.interaction == "single_select":
            if len(choice_ids) != 4 or len(answer.selection_ids) != 1:
                raise ValueError("single_select requires 4 choices and exactly 1 answer")
            if set(answer.selection_ids) - choice_ids:
                raise ValueError("single_select answer references an unknown choice")
            if left_ids or right_ids or ordering_ids or answer.ordering or answer.mappings:
                raise ValueError("single_select contains fields for another interaction")
            if answer.text or self.rubric:
                raise ValueError("single_select must not contain text-answer fields")
        elif self.interaction == "multi_select":
            if len(choice_ids) < 4 or len(answer.selection_ids) < 2:
                raise ValueError("multi_select requires at least 4 choices and 2 answers")
            if set(answer.selection_ids) - choice_ids:
                raise ValueError("multi_select answer references an unknown choice")
            if left_ids or right_ids or ordering_ids or answer.ordering or answer.mappings:
                raise ValueError("multi_select contains fields for another interaction")
            if answer.text or self.rubric:
                raise ValueError("multi_select must not contain text-answer fields")
        elif self.interaction == "matching":
            if len(left_ids) < 3 or len(right_ids) < 3:
                raise ValueError("matching requires at least 3 options on each side")
            mapped_left = [mapping.left for mapping in answer.mappings]
            mapped_right = [mapping.right for mapping in answer.mappings]
            if set(mapped_left) != left_ids or len(mapped_left) != len(set(mapped_left)):
                raise ValueError("matching must map every left option exactly once")
            if set(mapped_right) - right_ids:
                raise ValueError("matching answer references an unknown right option")
            if choice_ids or ordering_ids or answer.selection_ids or answer.ordering:
                raise ValueError("matching contains fields for another interaction")
            if answer.text or self.rubric:
                raise ValueError("matching must not contain text-answer fields")
        elif self.interaction == "ordering":
            if len(ordering_ids) < 3:
                raise ValueError("ordering requires at least 3 options")
            if set(answer.ordering) != ordering_ids or len(answer.ordering) != len(ordering_ids):
                raise ValueError("ordering answer must contain every option exactly once")
            if choice_ids or left_ids or right_ids or answer.selection_ids or answer.mappings:
                raise ValueError("ordering contains fields for another interaction")
            if answer.text or self.rubric:
                raise ValueError("ordering must not contain text-answer fields")
        else:
            if not answer.text or not self.rubric:
                raise ValueError("short_text requires an exemplar answer and rubric")
            if choice_ids or left_ids or right_ids or ordering_ids:
                raise ValueError("short_text contains response options")
            if answer.selection_ids or answer.ordering or answer.mappings:
                raise ValueError("short_text contains an answer for another interaction")
        return self


class QuizBatch(BaseModel):
    """Raw model output. Contract validity does not imply content approval."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["quiz-batch.v1"]
    source_ref: QuizSourceRef
    questions: list[QuizQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def question_ids_are_unique(self) -> QuizBatch:
        question_ids = [question.question_id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("duplicate question IDs")
        return self

    def validate_against_input(self, payload: dict[str, Any]) -> None:
        if self.source_ref.model_dump(mode="json") != payload["source_ref"]:
            raise ValueError("Quiz source_ref does not match its frozen input")

        runtime = payload["runtime"]
        selected = runtime["selected_kc_ids"]
        variants_per_kc = runtime["variants_per_kc"]
        expected_count = len(selected) * variants_per_kc
        if len(self.questions) != expected_count:
            raise ValueError(f"expected {expected_count} questions, got {len(self.questions)}")

        kc_by_id = {kc["kc_id"]: kc for kc in payload["leaf_kcs"]}
        allowed_interactions = set(runtime["allowed_interactions"])
        questions_by_kc: dict[str, list[QuizQuestion]] = {}
        for question in self.questions:
            questions_by_kc.setdefault(question.kc_id, []).append(question)
        if set(questions_by_kc) != set(selected):
            raise ValueError("questions must cover exactly the selected KCs")

        for kc_id in selected:
            questions = questions_by_kc[kc_id]
            if len(questions) != variants_per_kc:
                raise ValueError(f"{kc_id} must have exactly {variants_per_kc} variants")
            if {question.variant_index for question in questions} != set(
                range(1, variants_per_kc + 1)
            ):
                raise ValueError(f"{kc_id} variant indexes are not contiguous")

            kc = kc_by_id[kc_id]
            allowed_evidence = {
                (evidence["page"], block_id)
                for evidence in kc["source_evidence"]
                for block_id in evidence["block_ids"]
            }
            for question in questions:
                if question.group_id != kc["group_id"]:
                    raise ValueError(f"{question.question_id} group does not match its KC")
                if question.interaction not in allowed_interactions:
                    raise ValueError(f"{question.question_id} uses a disabled interaction")
                cited = {
                    (reference.page, block_id)
                    for reference in question.evidence_refs
                    for block_id in reference.block_ids
                }
                if not cited <= allowed_evidence:
                    raise ValueError(f"{question.question_id} cites evidence outside its KC")
