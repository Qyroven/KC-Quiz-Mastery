/* Deterministic practice rules. No network, clock, storage, model or DOM access. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.LearningCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const POLICY_VERSION = "evidence-rules.v1";
  const EXACT_VERSION = "exact-v1";
  const RUBRIC_VERSION = "rubric-human-v1";
  const TYPES = new Set([
    "single_select",
    "multi_select",
    "matching",
    "ordering",
    "short_text",
  ]);
  const RESPONSE_KEYS = new Set([
    "selection_ids",
    "ordering",
    "mappings",
    "text",
  ]);
  const own = (object, key) =>
    Object.prototype.hasOwnProperty.call(object, key);
  const object = (value) =>
    value !== null && typeof value === "object" && !Array.isArray(value);
  const nonblank = (value) =>
    typeof value === "string" && value.trim().length > 0;
  const unique = (values) => new Set(values).size === values.length;
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const same = (a, b) =>
    a.length === b.length && a.every((value, index) => value === b[index]);
  const idList = (values) =>
    Array.isArray(values) && values.every(nonblank) && unique(values);

  function optionIds(options) {
    if (!Array.isArray(options) || options.some((item) => !object(item)))
      return null;
    const ids = options.map((item) => item.option_id);
    return idList(ids) ? ids : null;
  }

  function questionShape(question) {
    if (!object(question) || !TYPES.has(question.interaction))
      return "unsupported_interaction";
    const ids = {
      choice: optionIds(question.choice_options),
      left: optionIds(question.matching_left),
      right: optionIds(question.matching_right),
      order: optionIds(question.ordering_options),
    };
    if (Object.values(ids).some((value) => value === null))
      return "invalid_question_options";
    const type = question.interaction;
    if (type === "single_select" || type === "multi_select") {
      if (
        (type === "single_select"
          ? ids.choice.length !== 4
          : ids.choice.length < 4) ||
        ids.left.length ||
        ids.right.length ||
        ids.order.length
      )
        return "invalid_question_options";
    } else if (type === "matching") {
      if (
        ids.left.length < 3 ||
        ids.right.length < 3 ||
        ids.choice.length ||
        ids.order.length
      )
        return "invalid_question_options";
    } else if (type === "ordering") {
      if (
        ids.order.length < 3 ||
        ids.choice.length ||
        ids.left.length ||
        ids.right.length
      )
        return "invalid_question_options";
    } else if (Object.values(ids).some((value) => value.length))
      return "invalid_question_options";
    if (!Array.isArray(question.rubric)) return "invalid_rubric";
    if (type === "short_text") {
      if (
        !question.rubric.length ||
        question.rubric.some(
          (point) =>
            !object(point) ||
            !nonblank(point.criterion) ||
            !Number.isInteger(point.points) ||
            point.points < 1,
        )
      )
        return "invalid_rubric";
    } else if (question.rubric.length) return "invalid_rubric";
    return null;
  }

  function normalizeResponse(question, response) {
    const shapeError = questionShape(question);
    if (shapeError) return { valid: false, response: null, reason: shapeError };
    if (
      !object(response) ||
      Object.keys(response).some((key) => !RESPONSE_KEYS.has(key))
    )
      return { valid: false, response: null, reason: "invalid_response_shape" };
    const value = {
      selection_ids: own(response, "selection_ids")
        ? response.selection_ids
        : [],
      ordering: own(response, "ordering") ? response.ordering : [],
      mappings: own(response, "mappings") ? response.mappings : [],
      text: own(response, "text") ? response.text : "",
    };
    if (
      !idList(value.selection_ids) ||
      !idList(value.ordering) ||
      !Array.isArray(value.mappings) ||
      typeof value.text !== "string"
    )
      return { valid: false, response: null, reason: "invalid_response_shape" };
    const type = question.interaction;
    if (
      (type !== "single_select" &&
        type !== "multi_select" &&
        value.selection_ids.length) ||
      (type !== "ordering" && value.ordering.length) ||
      (type !== "matching" && value.mappings.length) ||
      (type !== "short_text" && value.text)
    )
      return {
        valid: false,
        response: null,
        reason: "response_contains_other_interaction",
      };
    let valid = false;
    if (type === "single_select" || type === "multi_select") {
      const allowed = new Set(optionIds(question.choice_options));
      valid =
        (type === "single_select"
          ? value.selection_ids.length === 1
          : value.selection_ids.length > 0) &&
        value.selection_ids.every((id) => allowed.has(id));
    } else if (type === "ordering") {
      const allowed = new Set(optionIds(question.ordering_options));
      valid =
        value.ordering.length === allowed.size &&
        value.ordering.every((id) => allowed.has(id));
    } else if (type === "matching") {
      const left = new Set(optionIds(question.matching_left));
      const right = new Set(optionIds(question.matching_right));
      valid =
        value.mappings.length === left.size &&
        value.mappings.every(
          (pair) =>
            object(pair) &&
            Object.keys(pair).length === 2 &&
            left.has(pair.left) &&
            right.has(pair.right),
        ) &&
        unique(value.mappings.map((pair) => pair.left));
    } else valid = nonblank(value.text);
    return valid
      ? { valid: true, response: clone(value), reason: null }
      : {
          valid: false,
          response: null,
          reason: "incomplete_or_unknown_response",
        };
  }

  function gradeResponse(question, response) {
    const supplied = normalizeResponse(question, response);
    const unavailable = (reason) => ({
      status: reason === "unsupported_interaction" ? "unsupported" : "invalid",
      score: null,
      max_score: null,
      correct: null,
      grading_method: "pending",
      grading_version: null,
      reason,
      response: null,
    });
    if (!supplied.valid) return unavailable(supplied.reason);
    const key = normalizeResponse(question, question.correct_answer);
    if (
      !key.valid ||
      (question.interaction === "multi_select" &&
        (key.response.selection_ids.length < 2 ||
          key.response.selection_ids.length >= question.choice_options.length))
    )
      return unavailable("invalid_answer_key");
    if (question.interaction === "short_text") {
      return {
        status: "pending_grade",
        score: null,
        max_score: question.rubric.reduce(
          (sum, point) => sum + point.points,
          0,
        ),
        correct: null,
        grading_method: "pending",
        grading_version: RUBRIC_VERSION,
        reason: "human_rubric_required",
        response: supplied.response,
      };
    }
    const expected = key.response,
      actual = supplied.response;
    let correct,
      score,
      maximum = 1;
    if (question.interaction === "matching") {
      const keyed = new Map(
        expected.mappings.map((pair) => [pair.left, pair.right]),
      );
      maximum = expected.mappings.length;
      score = actual.mappings.filter(
        (pair) => keyed.get(pair.left) === pair.right,
      ).length;
      correct = score === maximum;
    } else if (question.interaction === "ordering")
      correct = same(actual.ordering, expected.ordering);
    else
      correct = same(
        [...actual.selection_ids].sort(),
        [...expected.selection_ids].sort(),
      );
    if (question.interaction !== "matching") score = correct ? 1 : 0;
    return {
      status: "graded",
      score,
      max_score: maximum,
      correct,
      grading_method: "exact",
      grading_version: EXACT_VERSION,
      reason: null,
      response: supplied.response,
    };
  }

  function item(data, questionId) {
    return (data.questions || []).find(
      (question) => question.question_id === questionId,
    );
  }

  function timestamp(value) {
    return typeof value === "string" && Number.isFinite(Date.parse(value))
      ? Date.parse(value)
      : null;
  }

  function sameSubject(attempt, learnerId) {
    return (
      learnerId === undefined ||
      attempt.learner_id === undefined ||
      attempt.learner_id === learnerId
    );
  }

  function buildLocalAttempt(data, questionId, response, options) {
    options = options || {};
    const question = item(data, questionId),
      meta = (data.question_meta || {})[questionId];
    if (!question || !meta) throw new Error("Unknown Learning question");
    if (
      !nonblank(options.attempt_id) ||
      timestamp(options.started_at) === null ||
      timestamp(options.submitted_at) === null ||
      timestamp(options.submitted_at) < timestamp(options.started_at)
    )
      throw new Error(
        "An attempt ID and valid start/submission timestamps are required",
      );
    const hints = options.hint_ids === undefined ? [] : options.hint_ids;
    const availableHints = new Set(
      (question.hints || []).map((hint) => hint.hint_id),
    );
    if (!idList(hints) || hints.some((id) => !availableHints.has(id)))
      throw new Error("Unknown or duplicate hint ID");
    const grade = gradeResponse(question, response);
    if (grade.status === "invalid" || grade.status === "unsupported")
      throw new Error(grade.reason);
    const repeated = (options.attempts || []).some(
      (attempt) =>
        attempt.attempt_id !== options.attempt_id &&
        attempt.run_id === data.run_id &&
        attempt.question_id === questionId &&
        sameSubject(attempt, options.learner_id),
    );
    const reasons = [];
    if (meta.initial_check_status !== "PASS")
      reasons.push("initial_check_not_pass");
    if (repeated) reasons.push("repeated_question");
    if (grade.status !== "graded") reasons.push("not_graded");
    if (!data.versions || data.versions.policy_version !== POLICY_VERSION)
      reasons.push("policy_version_mismatch");
    return {
      attempt_id: options.attempt_id,
      run_id: data.run_id,
      question_id: questionId,
      question_sha256: meta.question_sha256,
      kc_id: question.kc_id,
      slot_id: question.slot_id || null,
      started_at: options.started_at,
      submitted_at: options.submitted_at,
      status: grade.status,
      response: grade.response,
      hint_ids: [...hints],
      is_repeat: repeated,
      score: grade.score,
      max_score: grade.max_score,
      correct: grade.correct,
      grading_method: grade.grading_method,
      grading_version: grade.grading_version,
      quality_status: meta.initial_check_status,
      evidence_eligible: reasons.length === 0,
      exclusion_reasons: reasons,
      policy_version: POLICY_VERSION,
      versions: clone(data.versions || {}),
      trust_scope: "local_device",
      ...(options.learner_id === undefined
        ? {}
        : { learner_id: options.learner_id }),
    };
  }

  function orderAttempts(a, b) {
    const timeA = timestamp(a.started_at),
      timeB = timestamp(b.started_at);
    const idA = String(a.attempt_id || ""),
      idB = String(b.attempt_id || "");
    return (
      (timeA === null ? Infinity : timeA) -
        (timeB === null ? Infinity : timeB) ||
      (idA < idB ? -1 : idA > idB ? 1 : 0)
    );
  }

  function latestEvidence(a, b) {
    return (
      (timestamp(a.submitted_at) || timestamp(a.started_at) || 0) -
        (timestamp(b.submitted_at) || timestamp(b.started_at) || 0) ||
      orderAttempts(a, b)
    );
  }

  function inspectAttempts(data, attempts, options) {
    options = options || {};
    const subjects = new Set(
      attempts
        .filter(object)
        .map((attempt) => attempt.learner_id)
        .filter((value) => value !== undefined),
    );
    const mixed = options.learner_id === undefined && subjects.size > 1;
    const current = [],
      excluded = [],
      firstByQuestion = new Map();
    const selected = attempts
      .filter(object)
      .filter((attempt) => sameSubject(attempt, options.learner_id));
    const seenAttemptIds = new Set();
    for (const attempt of [...selected].sort(orderAttempts)) {
      const question = item(data, attempt.question_id);
      const meta = (data.question_meta || {})[attempt.question_id];
      const reasons = [];
      if (mixed) reasons.push("mixed_learner_scope");
      if (
        data.schema_version !== "learning-package.v1" ||
        !data.versions ||
        data.versions.policy_version !== POLICY_VERSION
      )
        reasons.push("policy_version_mismatch");
      if (attempt.run_id !== data.run_id) reasons.push("run_mismatch");
      if (
        !question ||
        !meta ||
        attempt.question_sha256 !== meta.question_sha256
      )
        reasons.push("question_version_mismatch");
      if (question && questionShape(question))
        reasons.push("unsupported_question");
      if (
        attempt.policy_version !== undefined &&
        attempt.policy_version !== POLICY_VERSION
      )
        reasons.push("policy_version_mismatch");
      if (
        object(attempt.versions) &&
        Object.keys(data.versions || {}).some(
          (key) => attempt.versions[key] !== data.versions[key],
        )
      )
        reasons.push("source_version_mismatch");
      if (object(attempt.lineage)) {
        const expected = {
          quiz_sha256: data.versions?.quiz_sha256,
          kc_set_sha256: data.versions?.kc_sha256,
          extraction_sha256: data.versions?.extraction_sha256,
          authoring_context_sha256: data.versions?.context_sha256,
          policy_version: POLICY_VERSION,
          ...(data.source ? { source_sha256: data.source.source_sha256 } : {}),
        };
        if (
          Object.keys(expected).some(
            (key) => attempt.lineage[key] !== expected[key],
          )
        )
          reasons.push("source_version_mismatch");
      }
      if (
        !nonblank(attempt.attempt_id) ||
        timestamp(attempt.started_at) === null
      )
        reasons.push("invalid_attempt_identity");
      // Seeing an earlier version does not become a fresh independent attempt
      // just because its hash changed. Version mismatch still excludes its grade.
      const repeated = firstByQuestion.has(attempt.question_id);
      if (
        !repeated &&
        attempt.run_id === data.run_id &&
        question &&
        nonblank(attempt.attempt_id) &&
        timestamp(attempt.started_at) !== null
      )
        firstByQuestion.set(attempt.question_id, attempt);
      if (repeated || attempt.is_repeat === true)
        reasons.push("repeated_question");
      if (seenAttemptIds.has(attempt.attempt_id))
        reasons.push("duplicate_attempt_snapshot");
      seenAttemptIds.add(attempt.attempt_id);
      if (
        question &&
        (attempt.kc_id !== question.kc_id ||
          (attempt.slot_id || null) !== (question.slot_id || null))
      )
        reasons.push("item_identity_mismatch");
      if (reasons.length) {
        excluded.push({
          attempt_id: attempt.attempt_id,
          question_id: attempt.question_id,
          reasons,
        });
        continue;
      }
      if (
        meta.initial_check_status !== "PASS" ||
        attempt.quality_status !== "PASS"
      )
        reasons.push("initial_check_not_pass");
      if (!idList(attempt.hint_ids)) reasons.push("invalid_hint_history");
      else {
        const knownHints = new Set(
          (question.hints || []).map((hint) => hint.hint_id),
        );
        if (attempt.hint_ids.some((id) => !knownHints.has(id)))
          reasons.push("invalid_hint_history");
      }
      // Trust server exclusions. Pending is tracked separately, never scored as wrong.
      const suppliedReasons = Array.isArray(attempt.exclusion_reasons)
        ? attempt.exclusion_reasons
        : ["missing_exclusion_history"];
      reasons.push(
        ...suppliedReasons.filter((reason) => reason !== "not_graded"),
      );
      const pending = attempt.status === "pending_grade";
      if (attempt.status !== "graded") {
        if (!pending) reasons.push("not_graded");
      } else {
        if (attempt.evidence_eligible !== true)
          reasons.push("evidence_ineligible");
        if (suppliedReasons.includes("not_graded")) reasons.push("not_graded");
        if (
          timestamp(attempt.submitted_at) === null ||
          timestamp(attempt.submitted_at) < timestamp(attempt.started_at) ||
          !finite(attempt.score) ||
          !finite(attempt.max_score) ||
          attempt.max_score <= 0 ||
          attempt.score < 0 ||
          attempt.score > attempt.max_score ||
          typeof attempt.correct !== "boolean" ||
          attempt.correct !== (attempt.score === attempt.max_score)
        )
          reasons.push("invalid_grade");
        const method =
          question.interaction === "short_text" ? "rubric_human" : "exact";
        const version = method === "exact" ? EXACT_VERSION : RUBRIC_VERSION;
        if (
          attempt.grading_method !== method ||
          attempt.grading_version !== version
        )
          reasons.push("grading_version_mismatch");
      }
      if (reasons.length)
        excluded.push({
          attempt_id: attempt.attempt_id,
          question_id: attempt.question_id,
          reasons: [...new Set(reasons)],
        });
      else current.push(attempt);
    }
    return {
      current,
      excluded,
      firstByQuestion,
      scope_error: mixed ? "mixed_learner_scope" : null,
    };
  }

  function attemptState(attempt) {
    if (!attempt) return "no_evidence";
    if (attempt.status === "pending_grade") return "pending_grade";
    if (attempt.correct !== true || attempt.score !== attempt.max_score)
      return "needs_practice";
    return attempt.hint_ids.length ? "assisted" : "demonstrated";
  }

  function computeEvidence(data, attempts, options) {
    const inspected = inspectAttempts(
      data,
      Array.isArray(attempts) ? attempts : [],
      options,
    );
    const bySlot = new Map(),
      pendingBySlot = new Map();
    for (const attempt of [...inspected.current].sort(latestEvidence)) {
      if (!attempt.slot_id) continue;
      if (attempt.status === "graded") bySlot.set(attempt.slot_id, attempt);
      else if (attempt.status === "pending_grade") {
        const pending = pendingBySlot.get(attempt.slot_id) || [];
        pending.push(attempt);
        pendingBySlot.set(attempt.slot_id, pending);
      }
    }
    const kcs = (data.kcs || []).map((kc) => {
      const slots = (data.slots || [])
        .filter((slot) => slot.kc_id === kc.kc_id)
        .map((slot) => {
          const pending = pendingBySlot.get(slot.slot_id) || [];
          // An unfinished grade cannot erase existing graded evidence for this slot.
          const attempt =
            bySlot.get(slot.slot_id) || pending[pending.length - 1];
          return {
            slot_id: slot.slot_id,
            state: attemptState(attempt),
            attempt_id: attempt ? attempt.attempt_id : null,
            question_id: attempt ? attempt.question_id : null,
            pending_attempts: pending.length,
          };
        });
      const count = (state) =>
        slots.filter((slot) => slot.state === state).length;
      const independent = count("demonstrated"),
        assisted = count("assisted");
      const pending = count("pending_grade"),
        needsPractice = count("needs_practice");
      let state = "no_evidence";
      if (slots.length && independent === slots.length) state = "demonstrated";
      else if (needsPractice) state = "needs_practice";
      else if (assisted) state = "assisted";
      else if (pending) state = "pending_grade";
      else if (independent) state = "developing";
      return {
        kc_id: kc.kc_id,
        state,
        coverage_available: slots.length > 0,
        total_slots: slots.length,
        covered_slots: independent,
        independent_slots: independent,
        assisted_slots: assisted,
        pending_slots: slots.filter((slot) => slot.pending_attempts > 0).length,
        needs_practice_slots: needsPractice,
        slots,
      };
    });
    return {
      policy_version: POLICY_VERSION,
      provisional: true,
      label: "Observed evidence only; not a calibrated mastery percentage.",
      scope_error: inspected.scope_error,
      kcs,
      excluded_attempts: inspected.excluded,
      counts: {
        attempted_questions: inspected.firstByQuestion.size,
        graded: inspected.current.filter(
          (attempt) => attempt.status === "graded",
        ).length,
        pending: inspected.current.filter(
          (attempt) => attempt.status === "pending_grade",
        ).length,
        excluded: inspected.excluded.length,
      },
    };
  }

  function reviewContext(data, kcId) {
    const kc = (data.kcs || []).find((value) => value.kc_id === kcId);
    if (!kc) return null;
    const pages = [
      ...new Set((kc.source_evidence || []).map((ref) => ref.page)),
    ].sort((a, b) => a - b);
    return {
      kc_id: kcId,
      pages,
      context_ids: [
        ...new Set((kc.context_evidence || []).map((ref) => ref.context_id)),
      ],
      has_pdf: pages.length > 0,
    };
  }

  function recommendNext(data, attempts, options) {
    const inspected = inspectAttempts(
      data,
      Array.isArray(attempts) ? attempts : [],
      options,
    );
    const candidates = (data.questions || []).filter(
      (question) =>
        (data.question_meta || {})[question.question_id]
          ?.initial_check_status === "PASS" &&
        !questionShape(question) &&
        !inspected.firstByQuestion.has(question.question_id),
    );
    const latest = [...inspected.current]
      .filter((attempt) => attempt.status === "graded")
      .sort(latestEvidence)
      .pop();
    const needsReview =
      latest && ["needs_practice", "assisted"].includes(attemptState(latest));
    const review = needsReview ? reviewContext(data, latest.kc_id) : null;
    const next =
      (needsReview &&
        candidates.find((question) => question.kc_id === latest.kc_id)) ||
      candidates[0];
    if (inspected.scope_error)
      return {
        action: "need_more_evidence",
        reason: inspected.scope_error,
        kc_id: null,
        question_id: null,
        review: null,
      };
    if (next)
      return {
        action: needsReview ? "review_and_practice" : "practice",
        reason: needsReview
          ? latest.correct
            ? "after_assisted"
            : "after_incorrect"
          : "unattempted_eligible_question",
        kc_id: next.kc_id,
        question_id: next.question_id,
        review,
      };
    const pending = inspected.current.some(
      (attempt) => attempt.status === "pending_grade",
    );
    return {
      action: pending ? "waiting_grading" : "need_more_evidence",
      reason: pending
        ? "human_rubric_required"
        : "no_unattempted_eligible_question",
      kc_id: needsReview ? latest.kc_id : null,
      question_id: null,
      review,
    };
  }

  return Object.freeze({
    POLICY_VERSION,
    EXACT_VERSION,
    RUBRIC_VERSION,
    normalizeResponse,
    gradeResponse,
    buildLocalAttempt,
    computeEvidence,
    recommendNext,
    reviewContext,
  });
});
