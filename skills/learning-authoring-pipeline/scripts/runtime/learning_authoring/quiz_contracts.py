"""Strict contracts for the single active KC-to-Quiz pipeline."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StrictStr,
    model_serializer,
    model_validator,
)

Interaction = Literal[
    "single_select",
    "multi_select",
    "matching",
    "ordering",
    "short_text",
]
QuizSchemaVersion = Literal["quiz-batch.v1", "quiz-batch.v2", "quiz-batch.v3"]
CURRENT_QUIZ_SCHEMA_VERSION: QuizSchemaVersion = "quiz-batch.v3"
CURRENT_QUIZ_INPUT_VERSION = "quiz-input.v3"
CognitiveOperation = Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]


class QuizSourceRef(BaseModel):
    """Immutable KC lineage for either one Extraction or one source bundle."""

    model_config = ConfigDict(extra="forbid")

    extraction_source_id: str | None = Field(default=None, min_length=1)
    extraction_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_bundle_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    kc_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authoring_context_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exactly_one_source_mode(self) -> QuizSourceRef:
        single_fields = (self.extraction_source_id, self.extraction_source_sha256)
        if (single_fields[0] is None) != (single_fields[1] is None):
            raise ValueError("single-source Quiz lineage requires both source ID and SHA-256")
        single_source = single_fields[0] is not None
        source_bundle = self.source_bundle_sha256 is not None
        if single_source == source_bundle:
            raise ValueError("Quiz lineage must bind exactly one Extraction or source bundle")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = handler(self)
        for name in (
            "extraction_source_id",
            "extraction_source_sha256",
            "source_bundle_sha256",
            "authoring_context_sha256",
        ):
            if getattr(self, name) is None and name not in self.model_fields_set:
                result.pop(name, None)
        return result


class QuizEvidenceRef(BaseModel):
    """A PDF locator; bundle-mode locators must state their source explicitly."""

    model_config = ConfigDict(extra="forbid")

    source_id: str | None = Field(default=None, min_length=1)
    page: int = Field(ge=1)
    block_ids: list[str] = Field(min_length=1)

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = handler(self)
        if self.source_id is None and "source_id" not in self.model_fields_set:
            result.pop("source_id", None)
        return result


class QuizContextEvidenceRef(BaseModel):
    """A reference to an exact context excerpt/observation already bound to this KC."""

    model_config = ConfigDict(extra="forbid")

    context_id: str = Field(pattern=r"^CTX-[0-9]+$")
    excerpt: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    source_id: str | None = Field(default=None, min_length=1)
    pages: list[Annotated[int, Field(ge=1, strict=True)]] = Field(default_factory=list)

    @model_validator(mode="after")
    def context_reference_is_supported(self) -> QuizContextEvidenceRef:
        if not (self.excerpt and self.excerpt.strip()) and not (
            self.description and self.description.strip()
        ):
            raise ValueError("context evidence requires an excerpt or attachment description")
        if any(page < 1 for page in self.pages) or len(self.pages) != len(set(self.pages)):
            raise ValueError("context evidence pages must be unique positive page numbers")
        if self.source_id is not None and not self.pages:
            raise ValueError("document-level context evidence must not name a PDF source")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = handler(self)
        if self.source_id is None and "source_id" not in self.model_fields_set:
            result.pop("source_id", None)
        return result


class AssessmentSlot(BaseModel):
    """A distinct assessment intent, not a surface variant or a Bloom quota."""

    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(min_length=1)
    kc_id: str = Field(pattern=r"^KC-[0-9]+$")
    evidence_intent: str = Field(min_length=1)
    cognitive_operation: CognitiveOperation
    intended_difficulty: Literal["easy", "medium", "hard"]
    variant_count: int = Field(ge=1, strict=True)
    justification: str = Field(min_length=1)

    @model_validator(mode="after")
    def intent_is_nonblank(self) -> AssessmentSlot:
        if any(
            not value.strip() for value in (self.slot_id, self.evidence_intent, self.justification)
        ):
            raise ValueError("assessment slot identity, intent, and justification must be nonblank")
        return self


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

    selection_ids: list[str] = Field(
        description=(
            "Correct choice option IDs for single_select or multi_select only. "
            "Must be [] for matching, ordering, and short_text."
        )
    )
    ordering: list[str] = Field(
        description=(
            "Ordering option IDs in the correct sequence for ordering only. "
            "Must be [] for every other interaction."
        )
    )
    mappings: list[QuizMapping] = Field(
        description=(
            "Correct left-to-right option ID pairs for matching only. "
            "Must be [] for every other interaction."
        )
    )
    text: str = Field(
        description=(
            'Exemplar answer for short_text only. Must be the empty string ("") for '
            "single_select, multi_select, matching, and ordering; put their post-answer "
            "explanation in answer_explanation instead."
        )
    )


class QuizRubricPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1)
    points: int = Field(ge=1)


class QuizHint(BaseModel):
    """A pre-authored, learner-requested scaffold, not an answer or a mastery penalty."""

    model_config = ConfigDict(extra="forbid")

    hint_id: StrictStr = Field(min_length=1)
    kind: Literal["cue", "strategy", "step"]
    text: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def hint_identity_and_text_are_nonblank(self) -> QuizHint:
        if not self.hint_id.strip() or self.hint_id != self.hint_id.strip():
            raise ValueError("hint_id must be nonblank and have no surrounding whitespace")
        if not self.text.strip():
            raise ValueError("hint text must be nonblank")
        return self


class QuizQuestion(BaseModel):
    """One unapproved learner-facing question generated from one Leaf KC."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^Q-[0-9]+$")
    slot_id: str | None = Field(default=None, min_length=1)
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
    rubric: list[QuizRubricPoint] = Field(
        description=(
            "Scoring criteria for short_text only; short_text requires a nonempty rubric. "
            "Must be [] for single_select, multi_select, matching, and ordering, which "
            "use correct_answer selection_ids, mappings, or ordering instead."
        )
    )
    answer_explanation: str = Field(
        min_length=1,
        description=(
            "Post-answer explanation required for every interaction. For single_select, "
            "multi_select, matching, and ordering, explain the keyed answer here, not "
            "in rubric or correct_answer.text."
        ),
    )
    hints: list[QuizHint] = Field(default_factory=list)
    hint_absence_reason: StrictStr | None = Field(default=None, min_length=1)
    evidence_refs: list[QuizEvidenceRef]
    context_evidence_refs: list[QuizContextEvidenceRef] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = handler(self)
        for name in ("slot_id", "context_evidence_refs", "hints", "hint_absence_reason"):
            if name not in self.model_fields_set and not getattr(self, name):
                result.pop(name, None)
        return result

    @staticmethod
    def _ids(options: list[QuizOption], label: str) -> set[str]:
        ids = [option.option_id for option in options]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate {label} option IDs")
        return set(ids)

    @model_validator(mode="after")
    def interaction_shape_is_exact(self) -> QuizQuestion:
        if "hints" in self.model_fields_set or "hint_absence_reason" in self.model_fields_set:
            self.validate_hint_contract()
        if not self.evidence_refs and not self.context_evidence_refs:
            raise ValueError("question requires PDF or authoring-context evidence")
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

    def validate_hint_contract(self, *, require_explicit: bool = False) -> None:
        """Validate representation only; leakage and usefulness require semantic review."""

        if require_explicit and not {"hints", "hint_absence_reason"} <= self.model_fields_set:
            raise ValueError("quiz-batch.v3 requires explicit hints and hint_absence_reason")
        hint_ids = [hint.hint_id for hint in self.hints]
        if len(hint_ids) != len(set(hint_ids)):
            raise ValueError(f"{self.question_id} has duplicate hint IDs")
        if self.hints:
            if self.hint_absence_reason is not None:
                raise ValueError("nonempty hints require hint_absence_reason to be null")
        elif not self.hint_absence_reason or not self.hint_absence_reason.strip():
            raise ValueError("empty hints require a nonblank hint_absence_reason")


class QuizQuestionV2(QuizQuestion):
    slot_id: str = Field(min_length=1)


class QuizQuestionV3(QuizQuestionV2):
    """Adaptive assessment item with an explicit, variably-sized hint decision."""

    hints: list[QuizHint]
    hint_absence_reason: StrictStr | None = Field(min_length=1)


class QuizBatch(BaseModel):
    """Raw model output. Contract validity does not imply content approval."""

    model_config = ConfigDict(extra="forbid")

    schema_version: QuizSchemaVersion
    source_ref: QuizSourceRef
    assessment_slots: list[AssessmentSlot] = Field(default_factory=list)
    questions: list[QuizQuestion] = Field(min_length=1)

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = handler(self)
        if self.schema_version == "quiz-batch.v1" and (
            "assessment_slots" not in self.model_fields_set
        ):
            result.pop("assessment_slots", None)
        return result

    @model_validator(mode="after")
    def question_ids_are_unique(self) -> QuizBatch:
        question_ids = [question.question_id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("duplicate question IDs")
        bundle_mode = self.source_ref.source_bundle_sha256 is not None
        for question in self.questions:
            for reference in question.evidence_refs:
                if bundle_mode and reference.source_id is None:
                    raise ValueError(
                        f"{question.question_id} bundle PDF evidence requires source_id"
                    )
                if not bundle_mode and reference.source_id is not None:
                    raise ValueError(
                        f"{question.question_id} single-source PDF evidence must not add source_id"
                    )
            for reference in question.context_evidence_refs:
                if bundle_mode and reference.pages and reference.source_id is None:
                    raise ValueError(
                        f"{question.question_id} bundle page-mapped context requires source_id"
                    )
                if not bundle_mode and reference.source_id is not None:
                    raise ValueError(
                        f"{question.question_id} single-source context must not add source_id"
                    )
        if self.schema_version == "quiz-batch.v1":
            if self.assessment_slots:
                raise ValueError("assessment slots require quiz-batch.v2 or quiz-batch.v3")
            return self
        if not self.assessment_slots:
            raise ValueError(f"{self.schema_version} requires assessment_slots")
        slot_ids = [slot.slot_id for slot in self.assessment_slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("duplicate assessment slot IDs")
        slots = {slot.slot_id: slot for slot in self.assessment_slots}
        questions_by_slot: dict[str, list[QuizQuestion]] = {slot_id: [] for slot_id in slots}
        for question in self.questions:
            if self.schema_version == "quiz-batch.v3":
                question.validate_hint_contract(require_explicit=True)
            if not question.slot_id or question.slot_id not in slots:
                raise ValueError(f"{question.question_id} requires a known assessment slot_id")
            if question.kc_id != slots[question.slot_id].kc_id:
                raise ValueError(f"{question.question_id} KC does not match its assessment slot")
            # Keep the v1 reader's historical validation unchanged. New adaptive
            # outputs also require distinct keys and a genuine distractor.
            selected_ids = question.correct_answer.selection_ids
            if len(selected_ids) != len(set(selected_ids)):
                raise ValueError("duplicate selected answer IDs")
            if question.interaction == "multi_select" and len(selected_ids) >= len(
                question.choice_options
            ):
                raise ValueError("multi_select requires at least one incorrect choice")
            questions_by_slot[question.slot_id].append(question)
        expected_count = sum(slot.variant_count for slot in self.assessment_slots)
        if len(self.questions) != expected_count:
            raise ValueError(
                f"assessment slots require {expected_count} questions, got {len(self.questions)}"
            )
        for slot_id, questions in questions_by_slot.items():
            count = slots[slot_id].variant_count
            if len(questions) != count:
                raise ValueError(f"{slot_id} must have exactly {count} variants")
            if {question.variant_index for question in questions} != set(range(1, count + 1)):
                raise ValueError(f"{slot_id} variant indexes are not contiguous")
        return self

    def validate_against_input(self, payload: dict[str, Any]) -> None:
        if self.source_ref != QuizSourceRef.model_validate(payload["source_ref"]):
            raise ValueError("Quiz source_ref does not match its frozen input")

        runtime = payload["runtime"]
        selected = runtime["selected_kc_ids"]
        variants_per_kc = runtime.get("variants_per_kc")
        adaptive = variants_per_kc is None
        input_version = payload.get("input_version")
        expected_schema = (
            CURRENT_QUIZ_SCHEMA_VERSION
            if input_version == CURRENT_QUIZ_INPUT_VERSION
            else "quiz-batch.v2"
            if adaptive
            else "quiz-batch.v1"
        )
        known_input_versions = {None, "quiz-input.v1", "quiz-input.v2", CURRENT_QUIZ_INPUT_VERSION}
        if (input_version == CURRENT_QUIZ_INPUT_VERSION and not adaptive) or (
            input_version not in known_input_versions
        ):
            raise ValueError("frozen Quiz input version does not match its count policy")
        if input_version == "quiz-input.v1" and adaptive:
            raise ValueError("frozen Quiz input version does not match its count policy")
        if input_version == "quiz-input.v2" and not adaptive:
            raise ValueError("frozen Quiz input version does not match its count policy")
        if (
            self.schema_version != expected_schema
            or runtime.get("expected_schema_version", expected_schema) != expected_schema
        ):
            raise ValueError(f"frozen Quiz policy requires {expected_schema}")
        expected_mode = "adaptive_slots" if adaptive else "legacy_per_kc"
        if runtime.get("assessment_mode", expected_mode) != expected_mode:
            raise ValueError("frozen Quiz assessment mode does not match its count policy")
        budget = runtime.get("total_question_budget")
        if budget is not None and len(self.questions) > budget:
            raise ValueError(f"question count exceeds total_question_budget {budget}")

        kc_by_id = {kc["kc_id"]: kc for kc in payload["leaf_kcs"]}
        if not selected or len(selected) != len(set(selected)) or set(selected) - set(kc_by_id):
            raise ValueError("frozen Quiz input must contain unique, known selected KCs")
        allowed_interactions = set(runtime["allowed_interactions"])
        questions_by_kc: dict[str, list[QuizQuestion]] = {}
        for question in self.questions:
            questions_by_kc.setdefault(question.kc_id, []).append(question)
        if set(questions_by_kc) != set(selected):
            raise ValueError("questions must cover exactly the selected KCs")

        if adaptive:
            slots_by_kc: dict[str, list[AssessmentSlot]] = {}
            for slot in self.assessment_slots:
                slots_by_kc.setdefault(slot.kc_id, []).append(slot)
                exact_variants = runtime.get("variants_per_slot")
                max_variants = runtime.get("max_variants_per_slot")
                if exact_variants is not None and slot.variant_count != exact_variants:
                    raise ValueError(f"{slot.slot_id} must have exactly {exact_variants} variants")
                if max_variants is not None and slot.variant_count > max_variants:
                    raise ValueError(f"{slot.slot_id} exceeds max_variants_per_slot {max_variants}")
            if set(slots_by_kc) != set(selected):
                raise ValueError("assessment slots must cover exactly the selected KCs")
            min_slots = runtime.get("min_slots_per_kc", 1)
            max_slots = runtime.get("max_slots_per_kc")
            for kc_id in selected:
                count = len(slots_by_kc[kc_id])
                if count < min_slots:
                    raise ValueError(f"{kc_id} requires at least {min_slots} assessment slots")
                if max_slots is not None and count > max_slots:
                    raise ValueError(f"{kc_id} exceeds max_slots_per_kc {max_slots}")
        else:
            expected_count = len(selected) * variants_per_kc
            if len(self.questions) != expected_count:
                raise ValueError(f"expected {expected_count} questions, got {len(self.questions)}")

        for kc_id in selected:
            questions = questions_by_kc[kc_id]
            if not adaptive:
                if len(questions) != variants_per_kc:
                    raise ValueError(f"{kc_id} must have exactly {variants_per_kc} variants")
                if {question.variant_index for question in questions} != set(
                    range(1, variants_per_kc + 1)
                ):
                    raise ValueError(f"{kc_id} variant indexes are not contiguous")

            kc = kc_by_id[kc_id]
            allowed_evidence = {
                (evidence.get("source_id"), evidence["page"], block_id)
                for evidence in kc["source_evidence"]
                for block_id in evidence["block_ids"]
            }
            bundle_mode = self.source_ref.source_bundle_sha256 is not None
            for evidence in kc.get("context_evidence", []):
                pages = evidence.get("pages", [])
                source_id = evidence.get("source_id")
                if bundle_mode and pages and not source_id:
                    raise ValueError("bundle page-mapped context evidence requires source_id")
                if not bundle_mode and source_id is not None:
                    raise ValueError("single-source context evidence must not add source_id")
            allowed_context = {
                (
                    evidence["context_id"],
                    evidence.get("excerpt"),
                    evidence.get("description"),
                    evidence.get("source_id"),
                    tuple(evidence.get("pages", [])),
                )
                for evidence in kc.get("context_evidence", [])
            }
            for question in questions:
                if question.group_id != kc["group_id"]:
                    raise ValueError(f"{question.question_id} group does not match its KC")
                if question.interaction not in allowed_interactions:
                    raise ValueError(f"{question.question_id} uses a disabled interaction")
                cited = {
                    (reference.source_id, reference.page, block_id)
                    for reference in question.evidence_refs
                    for block_id in reference.block_ids
                }
                if not cited <= allowed_evidence:
                    raise ValueError(f"{question.question_id} cites evidence outside its KC")
                cited_context = {
                    (
                        reference.context_id,
                        reference.excerpt,
                        reference.description,
                        reference.source_id,
                        tuple(reference.pages),
                    )
                    for reference in question.context_evidence_refs
                }
                if cited_context and not self.source_ref.authoring_context_sha256:
                    raise ValueError(
                        "Quiz context evidence requires a bound authoring context hash"
                    )
                if not cited_context <= allowed_context:
                    raise ValueError(
                        f"{question.question_id} cites context evidence outside its KC"
                    )


class QuizBatchV1(QuizBatch):
    """Explicit legacy-count generation; old v1 artifacts still use the unified reader."""

    schema_version: Literal["quiz-batch.v1"]


class QuizBatchV2(QuizBatch):
    """Adaptive slots and their variants returned together in one generation stage."""

    schema_version: Literal["quiz-batch.v2"]
    assessment_slots: list[AssessmentSlot] = Field(min_length=1)
    questions: list[QuizQuestionV2] = Field(min_length=1)


class QuizBatchV3(QuizBatchV2):
    """Slots, question variants, and optional scaffolding in one generation stage."""

    schema_version: Literal["quiz-batch.v3"]
    questions: list[QuizQuestionV3] = Field(min_length=1)


def quiz_output_schema(
    schema_version: QuizSchemaVersion = CURRENT_QUIZ_SCHEMA_VERSION, *, strict_output: bool = True
) -> dict[str, Any]:
    """Select one version, retaining native optional fields unless API strict output is needed."""

    contracts = {
        "quiz-batch.v1": QuizBatchV1,
        "quiz-batch.v2": QuizBatchV2,
        "quiz-batch.v3": QuizBatchV3,
    }
    if schema_version not in contracts:
        raise ValueError(f"unsupported Quiz schema version: {schema_version}")
    contract = contracts[schema_version]
    schema = contract.model_json_schema()
    schema.pop("$schema", None)
    if schema_version != "quiz-batch.v3":
        question_name = "QuizQuestionV2" if schema_version == "quiz-batch.v2" else "QuizQuestion"
        question_properties = schema["$defs"][question_name]["properties"]
        question_properties.pop("hints", None)
        question_properties.pop("hint_absence_reason", None)
        schema["$defs"].pop("QuizHint", None)
    if schema_version == "quiz-batch.v1":
        schema["properties"].pop("assessment_slots", None)
        schema.get("$defs", {}).pop("AssessmentSlot", None)
        schema["$defs"]["QuizQuestion"]["properties"].pop("slot_id", None)

    def make_required(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                make_required(value)
        elif isinstance(node, list):
            for value in node:
                make_required(value)

    if strict_output:
        make_required(schema)
    return schema
