(function (root) {
  "use strict";

  const escapeHtml = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (char) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[char],
    );
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const emptyResponse = () => ({
    selection_ids: [],
    ordering: [],
    mappings: [],
    text: "",
  });
  const done = (attempt) =>
    Boolean(attempt && ["graded", "pending_grade"].includes(attempt.status));
  const latest = (attempts, questionId) =>
    attempts
      .filter((item) => item.question_id === questionId)
      .slice()
      .reverse()
      .sort((a, b) =>
        String(b.started_at).localeCompare(String(a.started_at)),
      )[0] || null;
  const stateNames = {
    no_evidence: "Chưa có bằng chứng",
    pending_grade: "Chờ chấm rubric",
    needs_practice: "Cần ôn thêm",
    assisted: "Đúng khi có hỗ trợ",
    developing: "Đang hình thành",
    demonstrated: "Đã có bằng chứng độc lập",
  };
  const reasonNames = {
    initial_check_not_pass: "Câu hỏi chưa PASS kiểm tra ban đầu",
    repeated_question: "Làm lại sau khi đã thấy đáp án",
    content_review_changed: "Nội dung hoặc review nguồn đã thay đổi",
    not_graded: "Chưa được chấm",
    pending_grade: "Chờ người được cấp quyền chấm rubric",
    assisted: "Có dùng gợi ý",
    stale_lineage: "Nguồn đã thay đổi",
    stale_question: "Phiên bản câu hỏi đã thay đổi",
    question_version_mismatch: "Phiên bản câu hỏi không còn khớp",
    source_version_mismatch: "Phiên bản nguồn không còn khớp",
    policy_version_mismatch: "Phiên bản quy tắc không khớp",
    evidence_ineligible: "Lần thử không đủ điều kiện bằng chứng",
    invalid_grade: "Kết quả chấm chưa hợp lệ",
    invalid_hint_history: "Lịch sử gợi ý chưa hợp lệ",
    grading_version_mismatch: "Phiên bản chấm không khớp",
    mixed_learner_scope: "Lịch sử có nhiều người học",
    run_mismatch: "Lần thử thuộc bộ học khác",
    invalid_attempt_identity: "Lần thử chưa đủ thông tin định danh",
    duplicate_attempt_snapshot: "Lần thử bị lặp",
    item_identity_mismatch: "KC hoặc mục tiêu đo không khớp",
    missing_exclusion_history: "Thiếu kiểm tra điều kiện bằng chứng",
  };
  const nextReasons = {
    after_assisted:
      "Bạn đã dùng gợi ý. Ôn lại KC rồi thử một câu chưa làm để có bằng chứng độc lập.",
    after_incorrect:
      "Lần vừa rồi chưa đúng trọn vẹn. Ôn lại kiến thức liên quan trước khi thử câu khác.",
    unattempted_eligible_question:
      "Một câu chưa làm và đã PASS kiểm tra ban đầu có thể bổ sung bằng chứng mới.",
    human_rubric_required:
      "Còn câu trả lời đang chờ người được cấp quyền chấm theo rubric; chưa suy đoán đúng hoặc sai.",
    no_unattempted_eligible_question:
      "Bộ học hiện không còn câu PASS chưa làm. Có thể ôn lại hoặc luyện lại, nhưng cần câu mới để có thêm bằng chứng độc lập.",
    mixed_learner_scope:
      "Chưa thể đề xuất từ lịch sử có nhiều người học. Cần kiểm tra lại phạm vi phiên học.",
  };
  const operationNames = {
    remember: "Nhớ",
    understand: "Hiểu",
    apply: "Vận dụng",
    analyze: "Phân tích",
    evaluate: "Đánh giá",
    create: "Sáng tạo",
  };
  const difficultyNames = { easy: "Dễ", medium: "Vừa", hard: "Khó" };
  const interactionNames = {
    single_select: "Chọn một",
    multi_select: "Chọn nhiều",
    matching: "Ghép cặp",
    ordering: "Sắp xếp",
    short_text: "Trả lời ngắn",
  };

  function shuffled(items, seedText) {
    let seed = 2166136261;
    for (const char of seedText)
      seed = Math.imul(seed ^ char.charCodeAt(0), 16777619) >>> 0;
    const result = items.slice();
    for (let i = result.length - 1; i > 0; i--) {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      const j = seed % (i + 1);
      [result[i], result[j]] = [result[j], result[i]];
    }
    return result;
  }

  function validateData(data) {
    if (
      !data ||
      data.schema_version !== "learning-package.v1" ||
      !data.run_id ||
      !Array.isArray(data.questions) ||
      !Array.isArray(data.kcs) ||
      !Array.isArray(data.slots) ||
      !data.question_meta
    ) {
      throw new Error(
        "Bộ học chưa tải hoặc không đúng định dạng. Hãy tải lại trang; chưa có lần thử nào được ghi.",
      );
    }
    for (const question of data.questions) {
      if (
        !data.question_meta[question.question_id]?.question_sha256 ||
        !interactionNames[question.interaction]
      ) {
        throw new Error(
          "Bộ học thiếu phiên bản câu hỏi hoặc có kiểu tương tác chưa hỗ trợ. Không thể ghi kết quả.",
        );
      }
    }
    return data;
  }

  // The controller is also used by the offline test harness. It never submits
  // browser-computed scores to the shared backend, nor falls back after a failure.
  function createSession({
    data,
    core,
    config,
    storage,
    fetch: fetcher,
    crypto: cryptoApi,
    locks,
    now = () => new Date().toISOString(),
  }) {
    validateData(data);
    if (
      !core ||
      !core.normalizeResponse ||
      !core.buildLocalAttempt ||
      !core.computeEvidence ||
      !core.recommendNext
    )
      throw new Error(
        "Chưa tải được bộ quy tắc chấm và bằng chứng. Hãy tải lại trang.",
      );
    const mode = config?.enabled ? "shared" : "local";
    let project = data.run_id,
      baseUrl = "";
    if (mode === "shared") {
      try {
        const url = new URL(config.supabaseUrl);
        if (
          !["https:", "http:"].includes(url.protocol) ||
          !config.supabasePublishableKey
        )
          throw new Error();
        project = url.hostname;
        baseUrl = url.href.replace(/\/$/, "");
      } catch {
        throw new Error(
          "Cấu hình lưu dùng chung chưa hợp lệ. Không tự chuyển sang lưu cục bộ.",
        );
      }
    }
    const prefix = `la-learning:${mode}:${project}`;
    const keys = {
      identity: `${prefix}:identity`,
      session: `${prefix}:session`,
      pending: `${prefix}:${data.run_id}:pending`,
    };
    const state = {
      mode,
      identity: null,
      attempts: [],
      feedback: [],
      itemQuality: {},
      canGrade: false,
      queue: [],
      loaded: false,
      pending: { starts: {}, feedback: {}, grades: {} },
    };
    let authSession = null;

    function read(key, fallback) {
      let value;
      try {
        value = storage.getItem(key);
      } catch {
        throw new Error(
          "Trình duyệt không cho đọc dữ liệu phiên học. Hãy cho phép lưu trữ rồi thử lại; chưa tự tạo phiên khác.",
        );
      }
      if (value === null) return fallback;
      try {
        return JSON.parse(value);
      } catch {
        throw new Error(
          "Dữ liệu phiên học trên thiết bị không đọc được. Đã giữ nguyên dữ liệu, không ghi đè hay tự tạo lịch sử mới.",
        );
      }
    }
    function write(key, value) {
      try {
        storage.setItem(key, JSON.stringify(value));
      } catch {
        throw new Error(
          "Không lưu được trên trình duyệt này. Kiểm tra quyền lưu trữ/dung lượng rồi thử lại; chưa xác nhận thao tác thành công.",
        );
      }
    }
    function readIdentity() {
      const identity = read(keys.identity, null);
      if (
        identity !== null &&
        (typeof identity !== "object" ||
          typeof identity.display_name !== "string" ||
          !identity.display_name.trim() ||
          typeof identity.learner_id !== "string" ||
          !identity.learner_id)
      )
        throw new Error(
          "Thông tin người học trên thiết bị không hợp lệ. Đã giữ nguyên dữ liệu để kiểm tra; không tự tạo người học mới.",
        );
      return identity;
    }
    function readPending() {
      const pending = read(keys.pending, {
        starts: {},
        feedback: {},
        grades: {},
      });
      if (!pending || !pending.starts || !pending.feedback || !pending.grades)
        throw new Error(
          "Mã thao tác đang chờ không đọc được; không tự tạo thao tác mới.",
        );
      return pending;
    }
    function uuid() {
      if (!cryptoApi?.randomUUID)
        throw new Error(
          "Trình duyệt cần kết nối HTTPS hoặc localhost để tạo mã lần thử an toàn.",
        );
      return cryptoApi.randomUUID();
    }
    function localKey() {
      return `${prefix}:${data.run_id}:${state.identity.learner_id}:records`;
    }
    function persistLocal(attempts, feedback) {
      write(localKey(), {
        schema_version: "learning-local.v1",
        run_id: data.run_id,
        attempts,
        feedback,
      });
      state.attempts = attempts;
      state.feedback = feedback;
    }
    function updateAttempt(attempt) {
      if (
        !attempt ||
        !attempt.attempt_id ||
        !attempt.question_id ||
        !["in_progress", "pending_grade", "graded"].includes(attempt.status)
      )
        throw new Error(
          "Máy chủ trả về lần thử không hợp lệ. Hãy cập nhật để kiểm tra, không tự chấm thay máy chủ.",
        );
      const rows = state.attempts
        .filter((item) => item.attempt_id !== attempt.attempt_id)
        .concat([attempt]);
      if (mode === "local") persistLocal(rows, state.feedback);
      else state.attempts = rows;
      return attempt;
    }
    function pendingId(kind, id) {
      if (!state.pending[kind][id]) {
        const next = clone(state.pending);
        next[kind][id] = uuid();
        write(keys.pending, next);
        state.pending = next;
      }
      return state.pending[kind][id];
    }
    function clearPending(kind, id) {
      const next = clone(state.pending);
      delete next[kind][id];
      write(keys.pending, next);
      state.pending = next;
    }
    function requireIdentity() {
      if (!state.identity)
        throw new Error(
          "Nhập tên hiển thị trước khi lưu lần thử. Tên không cần email hay mật khẩu.",
        );
    }
    function questionFor(id) {
      const question = data.questions.find((item) => item.question_id === id);
      if (!question)
        throw new Error("Không tìm thấy câu hỏi trong bộ học này.");
      return question;
    }
    function learningData() {
      if (mode === "local" || !state.identity) return data;
      const questionMeta = {};
      for (const [id, meta] of Object.entries(data.question_meta)) {
        const current = state.itemQuality[id];
        // A registry entry from an older audit cannot promote this package.
        const baseline = ["PASS", "REVIEW", "REJECT", "STALE"].includes(
          meta.initial_check_status,
        )
          ? meta.initial_check_status
          : "UNCHECKED";
        const live =
          current &&
          ["PASS", "REVIEW", "REJECT", "STALE"].includes(current.quality_status)
            ? current.quality_status
            : "UNCHECKED";
        const quality =
          current && current.question_sha256 !== meta.question_sha256
            ? "STALE"
            : baseline !== "PASS"
              ? baseline
              : live;
        questionMeta[id] = {
          ...meta,
          initial_check_status: quality || "UNCHECKED",
        };
      }
      return { ...data, question_meta: questionMeta };
    }
    async function rawRequest(
      path,
      { body, token, method = "POST", prefer } = {},
    ) {
      const controller =
        typeof AbortController !== "undefined" ? new AbortController() : null;
      const timer = controller
        ? setTimeout(() => controller.abort(), 20000)
        : null;
      const headers = {
        apikey: config.supabasePublishableKey,
        "Content-Type": "application/json",
      };
      if (token) headers.Authorization = `Bearer ${token}`;
      if (prefer) headers.Prefer = prefer;
      try {
        const result = await fetcher(`${baseUrl}${path}`, {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          ...(controller ? { signal: controller.signal } : {}),
        });
        const text = result.status === 204 ? "" : await result.text();
        let payload = null;
        try {
          payload = text ? JSON.parse(text) : null;
        } catch {
          throw new Error(
            "Phản hồi máy chủ không đọc được. Hãy thử lại để xác nhận trạng thái đã lưu.",
          );
        }
        if (!result.ok) {
          const error = new Error(
            payload?.message ||
              payload?.error_description ||
              payload?.error ||
              `Máy chủ trả lỗi ${result.status}.`,
          );
          error.status = result.status;
          throw error;
        }
        return payload;
      } catch (error) {
        if (error.name === "AbortError")
          throw new Error(
            "Kết nối quá lâu. Trạng thái lưu chưa rõ; Thử lại dùng cùng mã thao tác, không tạo lần thử mới.",
          );
        throw error;
      } finally {
        if (timer !== null) clearTimeout(timer);
      }
    }
    function saveAuth(payload) {
      const session = payload?.session || payload;
      if (!session?.access_token || !session.refresh_token || !session.user?.id)
        throw new Error(
          "Không nhận được phiên học hợp lệ. Chưa thể ghi dữ liệu dùng chung.",
        );
      session.expires_at ||=
        Math.floor(Date.now() / 1000) + Number(session.expires_in || 3600);
      write(keys.session, session);
      authSession = session;
      return session;
    }
    async function refreshAuth() {
      // Do not replace a lost/expired identity with a new anonymous learner.
      try {
        return saveAuth(
          await rawRequest("/auth/v1/token?grant_type=refresh_token", {
            body: { refresh_token: authSession.refresh_token },
          }),
        );
      } catch {
        throw new Error(
          "Không làm mới được phiên học hiện tại. Đã giữ nguyên phiên và lịch sử; kiểm tra kết nối rồi thử lại. Không tự chuyển người học.",
        );
      }
    }
    async function ensureAuth() {
      if (
        authSession?.access_token &&
        Number(authSession.expires_at) > Date.now() / 1000 + 60
      )
        return authSession;
      if (authSession?.refresh_token) return refreshAuth();
      if (state.identity)
        throw new Error(
          "Không còn khóa phiên của người học này trên trình duyệt. Không thể khôi phục lịch sử chỉ bằng tên hiển thị.",
        );
      return saveAuth(
        await rawRequest("/auth/v1/signup", {
          body: { data: { application: "learning-mvp" } },
        }),
      );
    }
    async function request(path, body, prefer) {
      const session = await ensureAuth();
      try {
        return await rawRequest(path, {
          body,
          token: session.access_token,
          prefer,
        });
      } catch (error) {
        if (error.status !== 401 || !authSession?.refresh_token) throw error;
        const fresh = await refreshAuth();
        return rawRequest(path, { body, token: fresh.access_token, prefer });
      }
    }
    const rpc = (name, body) => request(`/rest/v1/rpc/${name}`, body);

    async function reload() {
      if (mode === "local") {
        state.identity = readIdentity();
        state.pending = readPending();
      }
      if (!state.identity) {
        state.attempts = [];
        state.feedback = [];
        state.canGrade = false;
        state.queue = [];
        state.itemQuality = {};
        state.loaded = true;
        return state;
      }
      if (mode === "shared") {
        const result = await rpc("get_learning_state", {
          p_run_id: data.run_id,
        });
        if (
          !result ||
          !Array.isArray(result.attempts) ||
          !Array.isArray(result.feedback) ||
          typeof result.can_grade !== "boolean"
        )
          throw new Error(
            "Máy chủ chưa trả về trạng thái học hợp lệ. Không sử dụng lịch sử giả hoặc dữ liệu cục bộ thay thế.",
          );
        state.attempts = result.attempts;
        state.feedback = result.feedback;
        state.itemQuality =
          result.item_quality && typeof result.item_quality === "object"
            ? result.item_quality
            : {};
        state.canGrade = result.can_grade === true;
        if (!state.canGrade) state.queue = [];
      } else {
        const result = read(localKey(), { attempts: [], feedback: [] });
        if (
          !result ||
          !Array.isArray(result.attempts) ||
          !Array.isArray(result.feedback)
        )
          throw new Error(
            "Lịch sử cục bộ không đúng định dạng. Đã giữ nguyên dữ liệu, không ghi đè.",
          );
        state.attempts = result.attempts;
        state.feedback = result.feedback;
      }
      state.loaded = true;
      return state;
    }
    async function init() {
      state.identity = readIdentity();
      authSession = mode === "shared" ? read(keys.session, null) : null;
      state.pending = readPending();
      return reload();
    }
    async function saveName(name) {
      const clean = String(name || "").trim();
      if (!clean || clean.length > 80)
        throw new Error("Tên hiển thị cần từ 1 đến 80 ký tự.");
      let learnerId = state.identity?.learner_id;
      if (mode === "shared") {
        const session = await ensureAuth();
        learnerId = session.user.id;
        await request(
          "/rest/v1/reviewer_profiles?on_conflict=user_id",
          { user_id: learnerId, display_name: clean },
          "resolution=merge-duplicates,return=minimal",
        );
      } else learnerId ||= uuid();
      const identity = { display_name: clean, learner_id: learnerId };
      write(keys.identity, identity);
      state.identity = identity;
      return reload();
    }
    async function start(questionId) {
      requireIdentity();
      const question = questionFor(questionId),
        meta = data.question_meta[questionId];
      const active = state.attempts.find(
        (item) =>
          item.question_id === questionId &&
          item.question_sha256 === meta.question_sha256 &&
          item.status === "in_progress",
      );
      if (active) {
        if (state.pending.starts[questionId])
          clearPending("starts", questionId);
        return active;
      }
      const attemptId = pendingId("starts", questionId);
      let attempt;
      if (mode === "shared")
        attempt = await rpc("start_learning_attempt", {
          p_run_id: data.run_id,
          p_question_id: questionId,
          p_question_sha256: meta.question_sha256,
          p_attempt_id: attemptId,
        });
      else
        attempt = {
          attempt_id: attemptId,
          run_id: data.run_id,
          learner_id: state.identity.learner_id,
          question_id: questionId,
          question_sha256: meta.question_sha256,
          kc_id: question.kc_id,
          slot_id: question.slot_id,
          started_at: now(),
          submitted_at: null,
          status: "in_progress",
          response: emptyResponse(),
          hint_ids: [],
          is_repeat: state.attempts.some(
            (item) => item.question_id === questionId && done(item),
          ),
          score: null,
          max_score: null,
          correct: null,
          grading_method: "pending",
          grading_version: null,
          quality_status: meta.initial_check_status || "UNCHECKED",
          evidence_eligible: false,
          exclusion_reasons: ["not_graded"],
        };
      updateAttempt(attempt);
      clearPending("starts", questionId);
      return attempt;
    }
    async function revealHint(questionId) {
      const last = latest(state.attempts, questionId);
      if (done(last))
        throw new Error(
          "Lần thử này đã nộp ở một phiên đang mở. Cập nhật kết quả hoặc chọn Làm lại để luyện tập tiếp.",
        );
      const question = questionFor(questionId),
        attempt = await start(questionId);
      if (done(attempt))
        throw new Error("Lần thử này đã nộp. Chọn Làm lại để luyện tập tiếp.");
      const hint = (question.hints || []).find(
        (item) => !(attempt.hint_ids || []).includes(item.hint_id),
      );
      if (!hint) throw new Error("Đã mở hết gợi ý được soạn cho câu này.");
      const changed =
        mode === "shared"
          ? await rpc("reveal_learning_hint", {
              p_attempt_id: attempt.attempt_id,
              p_hint_id: hint.hint_id,
            })
          : {
              ...attempt,
              hint_ids: [...(attempt.hint_ids || []), hint.hint_id],
            };
      return updateAttempt(changed); // Persist first; only then may the view show text.
    }
    async function submit(questionId, response) {
      requireIdentity();
      const question = questionFor(questionId),
        normalized = core.normalizeResponse(question, response);
      if (!normalized.valid)
        throw new Error(
          normalized.reason === "incomplete_or_unknown_response"
            ? "Hãy hoàn tất câu trả lời: chọn đáp án hoặc ghép đủ các dòng trước khi nộp."
            : "Câu trả lời không phù hợp cấu trúc câu hỏi; chưa ghi điểm. Hãy kiểm tra rồi thử lại.",
        );
      if (normalized.response.text.length > 8000)
        throw new Error("Câu trả lời ngắn tối đa 8.000 ký tự.");
      // A second tab submitting the same active attempt must receive the saved
      // result, not silently turn into a new repeat with a fresh hint history.
      const current = latest(
        state.attempts.filter(
          (item) =>
            item.question_sha256 ===
            data.question_meta[questionId].question_sha256,
        ),
        questionId,
      );
      const attempt = current || (await start(questionId));
      if (done(attempt)) return attempt;
      const result =
        mode === "shared"
          ? await rpc("submit_learning_attempt", {
              p_attempt_id: attempt.attempt_id,
              p_response: normalized.response,
            })
          : core.buildLocalAttempt(data, questionId, normalized.response, {
              attempt_id: attempt.attempt_id,
              started_at: attempt.started_at,
              submitted_at: now(),
              hint_ids: attempt.hint_ids || [],
              attempts: state.attempts.filter(
                (item) => item.attempt_id !== attempt.attempt_id,
              ),
              learner_id: state.identity.learner_id,
            });
      return updateAttempt(result);
    }
    async function feedback(questionId, vote, note, attemptId = null) {
      requireIdentity();
      questionFor(questionId);
      if (!["like", "dislike"].includes(vote))
        throw new Error("Chọn Hữu ích hoặc Cần cải thiện trước khi gửi.");
      if (String(note).length > 2000)
        throw new Error("Góp ý tối đa 2.000 ký tự.");
      const eventId = pendingId("feedback", questionId),
        meta = data.question_meta[questionId];
      const event =
        mode === "shared"
          ? await rpc("append_learning_feedback", {
              p_run_id: data.run_id,
              p_question_id: questionId,
              p_question_sha256: meta.question_sha256,
              p_vote: vote,
              p_note: String(note).trim() || null,
              p_attempt_id: attemptId,
              p_event_id: eventId,
            })
          : {
              event_id: eventId,
              run_id: data.run_id,
              question_id: questionId,
              question_sha256: meta.question_sha256,
              attempt_id: attemptId,
              kind: "feedback",
              payload: { vote, note: String(note).trim() },
              created_at: now(),
            };
      if (!event?.event_id)
        throw new Error(
          "Chưa xác nhận góp ý đã lưu. Thử lại sẽ dùng cùng mã góp ý.",
        );
      const rows = state.feedback
        .filter((item) => item.event_id !== event.event_id)
        .concat([event]);
      if (mode === "local") persistLocal(state.attempts, rows);
      else state.feedback = rows;
      clearPending("feedback", questionId);
      return event;
    }
    async function loadQueue() {
      requireIdentity();
      if (mode !== "shared" || !state.canGrade)
        throw new Error(
          "Phiên này không có quyền chấm rubric. Tên hiển thị không cấp quyền giảng viên.",
        );
      const rows = await rpc("get_learning_grading_queue", {
        p_run_id: data.run_id,
      });
      if (!Array.isArray(rows)) throw new Error("Chưa đọc được hàng chờ chấm.");
      state.queue = rows;
      return rows;
    }
    async function grade(attemptId, scores, note) {
      if (!state.canGrade || mode !== "shared")
        throw new Error("Phiên này không có quyền chấm rubric.");
      const row = state.queue.find((item) => item.attempt_id === attemptId);
      if (
        !row ||
        !Array.isArray(scores) ||
        scores.length !== row.question_payload.rubric.length ||
        scores.some(
          (score, index) =>
            !Number.isFinite(score) ||
            score < 0 ||
            score > row.question_payload.rubric[index].points,
        )
      )
        throw new Error("Nhập điểm hợp lệ cho tất cả tiêu chí rubric.");
      const result = await rpc("grade_learning_attempt", {
        p_attempt_id: attemptId,
        p_scores: scores,
        p_note: String(note || "").trim() || null,
        p_event_id: pendingId("grades", attemptId),
      });
      if (!result?.attempt_id || result.status !== "graded")
        throw new Error("Chưa xác nhận kết quả chấm đã lưu.");
      clearPending("grades", attemptId);
      state.queue = state.queue.filter((item) => item.attempt_id !== attemptId);
      return result;
    }
    function writable(fn) {
      return async (...args) => {
        if (mode === "shared") return fn(...args);
        if (!locks || typeof locks.request !== "function")
          throw new Error(
            "Trình duyệt chưa hỗ trợ khóa lưu an toàn giữa các tab. Chỉ có thể xem; hãy mở bằng trình duyệt hỗ trợ Web Locks qua HTTPS hoặc localhost để ghi lần thử. Chưa ghi hay ghi đè dữ liệu.",
          );
        return locks.request(
          `${prefix}:${data.run_id}:writer`,
          { mode: "exclusive" },
          async () => {
            // One atomic read/modify/write journey across every tab on this origin.
            // The persisted active attempt (including hints) always wins over an
            // earlier in-memory view. No stale whole-history snapshot is written.
            await reload();
            return fn(...args);
          },
        );
      };
    }
    return {
      state,
      init,
      reload,
      saveName: writable(saveName),
      start: writable(start),
      revealHint: writable(revealHint),
      submit: writable(submit),
      feedback: writable(feedback),
      loadQueue,
      grade,
      learningData,
    };
  }

  function answerText(question, response) {
    const all = [
      ...(question.choice_options || []),
      ...(question.matching_left || []),
      ...(question.matching_right || []),
      ...(question.ordering_options || []),
    ];
    const text = (id) =>
      all.find((option) => option.option_id === id)?.text || id;
    if (response.selection_ids?.length)
      return response.selection_ids.map(text).join("\n");
    if (response.ordering?.length)
      return response.ordering
        .map((id, index) => `${index + 1}. ${text(id)}`)
        .join("\n");
    if (response.mappings?.length)
      return response.mappings
        .map((pair) => `${text(pair.left)} → ${text(pair.right)}`)
        .join("\n");
    return response.text || "";
  }
  function kcLinks(kc) {
    const pages = [
      ...new Set(
        (kc?.source_evidence || [])
          .map((item) => Number(item.page))
          .filter((page) => Number.isInteger(page) && page > 0),
      ),
    ];
    const context = kc?.context_evidence || [];
    const kcHref = pages.length
      ? `kc-recall.html#${pages[0]}`
      : "kc-recall.html#context";
    const sourceLinks = pages
      .map(
        (page) =>
          `<a href="extraction-review.html#${page}" target="_blank" rel="noopener">Nguồn PDF · trang ${page} ↗</a>`,
      )
      .join("");
    return `<a href="${kcHref}" target="_blank" rel="noopener">Ôn KC ↗</a>${sourceLinks}${context.length ? '<a href="kc-recall.html#context" target="_blank" rel="noopener">Ngữ cảnh giảng viên ↗</a>' : ""}`;
  }
  function evidenceReason(attempt) {
    if (attempt.evidence_eligible === true)
      return attempt.hint_ids?.length
        ? "Bằng chứng có hỗ trợ; không coi là làm độc lập."
        : "Bằng chứng hợp lệ cho mục tiêu đo của câu hỏi.";
    const reasons = attempt.exclusion_reasons || [];
    return `Chưa dùng làm bằng chứng mastery${reasons.length ? ": " + reasons.map((reason) => reasonNames[reason] || reason).join("; ") : "."}`;
  }
  function badge(state) {
    return `<span class="tag ${state === "demonstrated" ? "green" : ["assisted", "needs_practice", "pending_grade"].includes(state) ? "amber" : "blue"}">${escapeHtml(stateNames[state] || state)}</span>`;
  }
  function stimulusHtml(stimulus) {
    if (!stimulus || stimulus.kind === "none") return "";
    let content = stimulus.text
      ? `<div>${escapeHtml(stimulus.text)}</div>`
      : "";
    if (stimulus.table_columns?.length)
      content += `<div class="table-scroll"><table><thead><tr>${stimulus.table_columns.map((item) => `<th>${escapeHtml(item)}</th>`).join("")}</tr></thead><tbody>${(stimulus.table_rows || []).map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    if (stimulus.formula)
      content += `<div class="formula">${escapeHtml(stimulus.formula)}</div>`;
    return `<div class="stimulus">${content}</div>`;
  }
  function controlsHtml(question, response, locked) {
    const esc = escapeHtml,
      seed = question.question_id;
    let html = "";
    if (["single_select", "multi_select"].includes(question.interaction)) {
      const kind =
        question.interaction === "single_select" ? "radio" : "checkbox";
      html = `<div class="answer-instruction">${kind === "radio" ? "Chọn một đáp án." : "Chọn tất cả đáp án phù hợp."}</div><div class="choice-list">${shuffled(
        question.choice_options,
        seed + "choice",
      )
        .map(
          (option) =>
            `<label class="choice"><input type="${kind}" name="learning-choice" value="${esc(option.option_id)}" data-response="choice" ${response.selection_ids.includes(option.option_id) ? "checked" : ""}><span>${esc(option.text)}</span></label>`,
        )
        .join("")}</div>`;
    } else if (question.interaction === "short_text") {
      html =
        '<label class="answer-instruction" for="short-answer">Viết câu trả lời của bạn. Câu này cần người được cấp quyền chấm theo rubric.</label><textarea id="short-answer" data-response="text" maxlength="8000" placeholder="Giải thích theo cách hiểu của bạn…">' +
        esc(response.text) +
        "</textarea>";
    } else if (question.interaction === "matching") {
      html = `<div class="answer-instruction">Chọn phần phù hợp cho từng dòng.</div><div class="matching-list">${question.matching_left
        .map(
          (option, index) =>
            `<div class="match-row"><label for="match-${index}">${esc(option.text)}</label><select id="match-${index}" data-response="match" data-left="${esc(option.option_id)}"><option value="">Chọn cặp phù hợp…</option>${shuffled(
              question.matching_right,
              seed + "right",
            )
              .map(
                (right) =>
                  `<option value="${esc(right.option_id)}" ${response.mappings.some((pair) => pair.left === option.option_id && pair.right === right.option_id) ? "selected" : ""}>${esc(right.text)}</option>`,
              )
              .join("")}</select></div>`,
        )
        .join("")}</div>`;
    } else if (question.interaction === "ordering") {
      html = `<div class="answer-instruction">Dùng ↑ ↓ để sắp xếp; có thể thao tác bằng bàn phím.</div><ol class="order-list">${response.ordering.map((id, index) => `<li class="order-row"><span class="order-number">${index + 1}</span><span class="order-text">${esc(question.ordering_options.find((item) => item.option_id === id)?.text || id)}</span><span class="order-moves"><button type="button" class="move-button" data-action="move" data-index="${index}" data-direction="-1" aria-label="Đưa mục ${index + 1} lên" ${index === 0 || locked ? "disabled" : ""}>↑</button><button type="button" class="move-button" data-action="move" data-index="${index}" data-direction="1" aria-label="Đưa mục ${index + 1} xuống" ${index === response.ordering.length - 1 || locked ? "disabled" : ""}>↓</button></span></li>`).join("")}</ol>`;
    }
    return `<fieldset class="answer-controls" ${locked ? "disabled" : ""}><legend class="sr-only">Câu trả lời của bạn</legend>${html}</fieldset>`;
  }
  function questionHtml(
    data,
    question,
    { attempt, response, busy = false, mode = "local" } = {},
  ) {
    const esc = escapeHtml,
      submitted = done(attempt),
      slot = data.slots.find((item) => item.slot_id === question.slot_id) || {};
    const kc = data.kcs.find((item) => item.kc_id === question.kc_id),
      currentQuality =
        data.question_meta[question.question_id].initial_check_status;
    const quality =
      currentQuality !== "PASS"
        ? currentQuality
        : attempt?.quality_status || currentQuality;
    const hints = question.hints || [],
      shown = attempt?.hint_ids || [];
    const tags = [
      operationNames[slot.cognitive_operation] || slot.cognitive_operation,
      difficultyNames[slot.intended_difficulty] || slot.intended_difficulty,
    ]
      .filter(Boolean)
      .map((item) => `<span class="tag">${esc(item)}</span>`)
      .join("");
    const flagged = quality !== "PASS";
    let html = `<header class="question-head"><div class="question-topline"><span class="question-position">${esc(question.question_id)} · ${esc(interactionNames[question.interaction])}</span><div class="tags">${tags}</div></div><h2 id="question-title">${esc(question.title)}</h2><p class="kc-subtitle">${esc(question.kc_id)} · ${esc(kc?.name || "")}</p></header><div class="question-body">`;
    if (flagged || attempt?.is_repeat)
      html += `<p class="practice-note">Chỉ luyện tập · ${flagged ? `Trạng thái câu hỏi: ${esc(quality || "UNCHECKED")}. Câu hỏi vẫn được giữ để góp ý, không tính vào mastery.` : "Bạn đã thấy đáp án ở lần trước. Lần làm lại không tạo bằng chứng độc lập mới."}</p>`;
    html +=
      stimulusHtml(question.stimulus) +
      `<p class="question-prompt">${esc(question.prompt)}</p>` +
      controlsHtml(question, response || emptyResponse(), submitted || busy);
    if (!submitted) {
      html += `<div class="question-actions">${hints.length ? `<button class="secondary" type="button" data-action="hint" ${busy || shown.length >= hints.length ? "disabled" : ""}>${shown.length ? "Gợi ý tiếp" : "Mở gợi ý"}</button><span class="hint-counter">${shown.length}/${hints.length}</span>` : ""}<span class="spacer"></span><button class="primary" type="button" data-action="submit" ${busy ? "disabled" : ""}>Nộp câu trả lời</button></div><p class="attempt-note">${mode === "local" ? "Lưu riêng trên trình duyệt này" : "Ghi vào phiên học riêng của bạn"} · Chỉ xác nhận kết quả khi lưu thành công. Bản trả lời chưa nộp sẽ mất khi tải lại trang.</p>`;
    }
    if (!hints.length)
      html += `<p class="no-hints">Không có gợi ý được soạn. ${esc(question.hint_absence_reason || "Bản câu hỏi này chưa có lý do không dùng gợi ý; không tự sinh thêm.")}</p>`;
    html += `<div class="hint-stack" aria-live="polite">${hints
      .filter((hint) => shown.includes(hint.hint_id))
      .map(
        (hint) =>
          `<div class="hint-box"><strong>GỢI Ý ${hints.indexOf(hint) + 1} · ĐÃ GHI NHẬN HỖ TRỢ</strong><p>${esc(hint.text)}</p></div>`,
      )
      .join("")}</div>`;
    if (submitted) {
      const pending = attempt.status === "pending_grade",
        correct = attempt.correct === true;
      const title = pending
        ? "Đã nộp · Chờ chấm rubric"
        : correct
          ? "Đúng với đáp án của câu hỏi"
          : "Chưa đúng trọn vẹn";
      const description = pending
        ? "Câu trả lời đã được giữ nguyên. Đáp án tham khảo dưới đây không phải kết quả chấm."
        : attempt.grading_method === "rubric_human"
          ? "Đã được người có quyền chấm theo rubric đóng băng của câu hỏi."
          : "Chấm theo đáp án cấu trúc của phiên bản câu hỏi này.";
      html += `<div class="result-card ${pending ? "pending" : correct ? "" : "incorrect"}" role="status"><div class="result-head"><div><h3 class="result-title">${title}</h3><p class="result-description">${description}</p></div>${!pending && attempt.score !== null && attempt.max_score !== null ? `<span class="result-score">${esc(attempt.score)} / ${esc(attempt.max_score)}</span>` : ""}</div><p class="evidence-line">${esc(evidenceReason(attempt))}</p><div class="answer-details"><h3>${pending ? "ĐÁP ÁN THAM KHẢO · KHÔNG TỰ CHẤM" : "ĐÁP ÁN VÀ GIẢI THÍCH"}</h3><div class="answer-text">${esc(answerText(question, question.correct_answer))}</div><p class="explanation">${esc(question.answer_explanation)}</p>${question.rubric?.length ? `<ul class="rubric-list">${question.rubric.map((row) => `<li>${esc(row.criterion)} · tối đa ${esc(row.points)} điểm</li>`).join("")}</ul>` : ""}</div></div><div class="question-actions"><button type="button" data-action="repeat" class="secondary" ${busy ? "disabled" : ""}>Làm lại · chỉ luyện tập</button><span class="hint-counter">Đã dùng ${shown.length} gợi ý</span></div>`;
    }
    html += `<div class="sources">${kcLinks(kc)}</div>`;
    if (kc?.context_evidence?.length)
      html += `<details class="source-details"><summary>Ngữ cảnh giảng viên liên quan${kc.source_evidence?.length ? "" : " · KC chỉ có nguồn ngữ cảnh"}</summary>${kc.context_evidence.map((item) => `<p><strong>${esc(item.context_id)}</strong> · ${esc(item.excerpt || item.description || item.supports || "")}</p>`).join("")}</details>`;
    return html + "</div>";
  }

  function mount({
    document: doc,
    data,
    core,
    config,
    storage,
    fetch: fetcher,
    crypto: cryptoApi,
    locks,
    location,
    history,
  }) {
    const $ = (id) => doc.getElementById(id),
      session = createSession({
        data,
        core,
        config,
        storage,
        fetch: fetcher,
        crypto: cryptoApi,
        locks,
      });
    const ui = {
      questionId: null,
      busy: false,
      retry: null,
      responses: {},
      feedback: {},
      showAll: false,
      queueOpen: false,
      gradeDrafts: {},
    };
    const question = () =>
      data.questions.find((item) => item.question_id === ui.questionId);
    const attempt = () => latest(session.state.attempts, ui.questionId);
    function defaultResponse(q) {
      const value = emptyResponse();
      if (q.interaction === "ordering")
        value.ordering = shuffled(
          q.ordering_options,
          q.question_id + "order",
        ).map((item) => item.option_id);
      return value;
    }
    function response(q) {
      const record = latest(session.state.attempts, q.question_id);
      if (done(record)) return record.response || emptyResponse();
      return (ui.responses[q.question_id] ||= defaultResponse(q));
    }
    function hideError() {
      $("error-panel").hidden = true;
      ui.retry = null;
    }
    function showError(error, retry) {
      $("error-message").textContent = error.message || String(error);
      $("error-panel").hidden = false;
      $("retry-operation").hidden = !retry;
      ui.retry = retry || null;
    }
    async function operation(label, task) {
      if (ui.busy) return;
      hideError();
      ui.busy = true;
      $("operation-status").textContent = label;
      $("operation-status").hidden = false;
      render();
      try {
        await task();
      } catch (error) {
        showError(error, () => operation(label, task));
      } finally {
        ui.busy = false;
        $("operation-status").hidden = true;
        render();
      }
    }
    function requireName() {
      if (session.state.identity) return true;
      $("identity-panel").hidden = false;
      $("learner-name").focus();
      showError(new Error("Nhập tên ở trên để bắt đầu lưu lần thử và gợi ý."));
      return false;
    }
    function select(questionId, focus = false) {
      if (
        ui.busy ||
        !data.questions.some((item) => item.question_id === questionId)
      )
        return;
      ui.questionId = questionId;
      history?.replaceState(null, "", `#${encodeURIComponent(questionId)}`);
      hideError();
      render();
      if (focus) $("question-panel").focus({ preventScroll: true });
    }
    function renderNavigation(evidence) {
      const q = question(),
        kc = data.kcs.find((item) => item.kc_id === q?.kc_id),
        progress = evidence.kcs.find((item) => item.kc_id === q?.kc_id);
      $("kc-count").textContent = String(data.kcs.length);
      $("kc-select").innerHTML = data.kcs
        .map(
          (item) =>
            `<option value="${escapeHtml(item.kc_id)}" ${item.kc_id === q?.kc_id ? "selected" : ""}>${escapeHtml(item.kc_id + " · " + item.name)}</option>`,
        )
        .join("");
      $("kc-select").disabled = ui.busy;
      $("kc-summary").innerHTML =
        `${badge(progress?.state || "no_evidence")}<p>${escapeHtml(kc?.observable_claim || kc?.knowledge_description || "")}</p>`;
      const rows = data.questions.filter((item) => item.kc_id === q?.kc_id);
      $("question-count").textContent = String(rows.length);
      $("question-list").innerHTML = rows
        .map((item) => {
          const record = latest(session.state.attempts, item.question_id),
            flag =
              session.learningData().question_meta[item.question_id]
                .initial_check_status !== "PASS";
          const dot = flag
            ? "flagged"
            : ["graded", "pending_grade"].includes(record?.status)
              ? record.status
              : "";
          return `<button class="question-nav ${item.question_id === ui.questionId ? "active" : ""}" data-action="select" data-question="${escapeHtml(item.question_id)}" ${ui.busy ? "disabled" : ""} ${item.question_id === ui.questionId ? 'aria-current="true"' : ""}><span class="status-dot ${dot}"></span><span class="question-copy"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(interactionNames[item.interaction])} · ${flag ? "Chỉ luyện tập" : done(record) ? (record.status === "pending_grade" ? "Chờ chấm" : "Đã làm") : "Chưa nộp"}</small></span></button>`;
        })
        .join("");
    }
    function renderNext() {
      const next = core.recommendNext(
          session.learningData(),
          session.state.attempts,
        ),
        reviewKc = data.kcs.find((item) => item.kc_id === next.review?.kc_id);
      const names = {
        review_and_practice: "Ôn kiến thức, rồi thử câu khác",
        practice: "Tiếp tục với một lần thử mới",
        need_more_evidence: "Cần thêm bằng chứng",
        waiting_grading: "Đợi chấm rubric để có bằng chứng",
      };
      const target = data.questions.find(
        (item) => item.question_id === next.question_id,
      );
      $("next-action").innerHTML =
        `<span class="next-symbol" aria-hidden="true">↗</span><div class="next-copy"><span class="small-cap">BƯỚC TIẾP THEO</span><h2 id="next-title">${escapeHtml(names[next.action] || "Chọn bước học tiếp theo")}</h2><p>${escapeHtml(nextReasons[next.reason] || next.reason || "Chưa có đủ bằng chứng để kết luận.")}</p>${reviewKc ? `<div class="next-links">${kcLinks(reviewKc)}</div>` : ""}</div>${target ? `<button type="button" data-action="select" data-question="${escapeHtml(target.question_id)}" class="primary" ${ui.busy ? "disabled" : ""}>${target.question_id === ui.questionId && !done(attempt()) ? "Tiếp tục câu này" : "Đến câu tiếp"} →</button>` : '<button type="button" data-action="refresh" class="secondary">Cập nhật kết quả</button>'}`;
    }
    function renderFeedback() {
      const draft = (ui.feedback[ui.questionId] ||= {
        vote: "",
        note: "",
        saved: false,
        open: false,
      });
      const previous = session.state.feedback.filter(
        (item) => item.question_id === ui.questionId,
      ).length;
      $("feedback-panel").innerHTML =
        `<details ${draft.open ? "open" : ""} id="feedback-details"><summary><strong>Câu hỏi này có hữu ích?</strong><span>Góp ý tách riêng · không đổi điểm hay mastery</span></summary><form id="feedback-form" class="feedback-form"><div class="vote-options"><button type="button" class="vote-button" data-action="vote" data-vote="like" aria-pressed="${draft.vote === "like"}">＋ Hữu ích</button><button type="button" class="vote-button" data-action="vote" data-vote="dislike" aria-pressed="${draft.vote === "dislike"}">− Cần cải thiện</button></div><label class="sr-only" for="feedback-note">Góp ý thêm, không bắt buộc</label><textarea id="feedback-note" maxlength="2000" placeholder="Điều gì rõ, khó hiểu hoặc cần sửa? (không bắt buộc)">${escapeHtml(draft.note)}</textarea><div class="feedback-footer"><span class="${draft.saved ? "saved-feedback" : "muted small"}">${draft.saved ? `Đã lưu góp ý ${session.state.mode === "local" ? "trên thiết bị này" : "dùng chung"}.` : previous ? `Bạn đã gửi ${previous} góp ý cho câu này.` : "Không dùng góp ý để chấm người học."}</span><button type="submit" class="secondary" ${ui.busy || !draft.vote ? "disabled" : ""}>Gửi góp ý</button></div></form></details>`;
      $("feedback-details").ontoggle = (event) => {
        draft.open = event.currentTarget.open;
      };
      $("feedback-note").oninput = (event) => {
        draft.note = event.target.value;
        draft.saved = false;
      };
      $("feedback-form").onsubmit = (event) => {
        event.preventDefault();
        if (!requireName()) return;
        const qid = ui.questionId,
          vote = draft.vote,
          note = draft.note,
          attemptId = attempt()?.attempt_id || null;
        return operation("Đang lưu góp ý riêng với kết quả học…", async () => {
          await session.feedback(qid, vote, note, attemptId);
          draft.saved = true;
        });
      };
    }
    function renderEvidence(evidence) {
      const activeKc = question()?.kc_id;
      const touched = evidence.kcs.filter(
        (row) => row.kc_id === activeKc || row.state !== "no_evidence",
      );
      const rows = ui.showAll
        ? evidence.kcs
        : touched.length
          ? touched
          : evidence.kcs.slice(0, 3);
      $("evidence-summary").innerHTML =
        rows
          .map((row) => {
            const kc = data.kcs.find((item) => item.kc_id === row.kc_id),
              target = data.questions.find((item) => item.kc_id === row.kc_id);
            return `<button class="kc-evidence ${row.kc_id === activeKc ? "active" : ""}" data-action="select" data-question="${escapeHtml(target?.question_id || "")}" ${ui.busy || !target ? "disabled" : ""}><span class="kc-evidence-name">${escapeHtml(row.kc_id)} · ${escapeHtml(kc?.name || "")}</span><span class="kc-evidence-bottom">${badge(row.state)}<span class="kc-evidence-count">${row.coverage_available ? `${Number(row.independent_slots) || 0}/${Number(row.total_slots) || 0} mục tiêu độc lập` : "Chưa có slot"}</span></span>${row.assisted_slots || row.pending_slots ? `<span class="muted small">${Number(row.assisted_slots) || 0} có hỗ trợ · ${Number(row.pending_slots) || 0} chờ chấm</span>` : ""}</button>`;
          })
          .join("") +
        (rows.length < evidence.kcs.length || ui.showAll
          ? `<button class="secondary" data-action="toggle-evidence" type="button">${ui.showAll ? "Thu gọn" : `Xem cả ${evidence.kcs.length} KC`}</button>`
          : "");
      const attempts = session.state.attempts
        .slice()
        .sort((a, b) =>
          String(b.started_at).localeCompare(String(a.started_at)),
        );
      $("history-count").textContent = `(${attempts.length})`;
      $("attempt-history").innerHTML = attempts.length
        ? attempts
            .map((row) => {
              const q = data.questions.find(
                  (item) => item.question_id === row.question_id,
                ),
                status =
                  row.status === "in_progress"
                    ? "Đang làm"
                    : row.status === "pending_grade"
                      ? "Chờ chấm rubric"
                      : row.correct
                        ? "Đúng"
                        : "Chưa đúng trọn vẹn";
              const exclusion = evidence.excluded_attempts.find(
                (item) => item.attempt_id === row.attempt_id,
              );
              const effective = exclusion
                ? {
                    ...row,
                    evidence_eligible: false,
                    exclusion_reasons: exclusion.reasons,
                  }
                : row;
              const recordedResponse =
                q &&
                row.question_sha256 ===
                  data.question_meta[row.question_id]?.question_sha256
                  ? answerText(q, row.response || emptyResponse())
                  : JSON.stringify(row.response || {});
              return `<article class="history-row"><div><button type="button" class="history-action" data-action="select" data-question="${escapeHtml(row.question_id)}">${escapeHtml(q?.title || row.question_id)}</button><time>${escapeHtml(formatTime(row.submitted_at || row.started_at))}</time></div><div><strong>${status}${row.status === "graded" && row.score !== null ? ` · ${escapeHtml(row.score)}/${escapeHtml(row.max_score)}` : ""}</strong><p>${(row.hint_ids || []).length} gợi ý${row.is_repeat ? " · Làm lại" : ""}</p></div><div><p>${escapeHtml(evidenceReason(effective))}</p>${done(row) ? `<details class="source-details"><summary>Câu trả lời đã lưu</summary><p>${escapeHtml(recordedResponse)}</p></details>` : ""}</div></article>`;
            })
            .join("")
        : '<p class="empty-state">Chưa có lần thử. Lịch sử sẽ xuất hiện sau khi bạn mở gợi ý hoặc nộp câu trả lời.</p>';
      $("grading-toggle").hidden = !session.state.canGrade;
    }
    function renderQueue() {
      $("grading-panel").hidden = !session.state.canGrade || !ui.queueOpen;
      if ($("grading-panel").hidden) return;
      $("grading-queue").innerHTML = session.state.queue.length
        ? session.state.queue
            .map((row) => {
              const q = row.question_payload,
                draft = (ui.gradeDrafts[row.attempt_id] ||= {
                  scores: q.rubric.map(() => ""),
                  note: "",
                });
              return `<form class="grading-item" data-grade="${escapeHtml(row.attempt_id)}"><h3>${escapeHtml(q.title)}</h3><p class="muted small">${escapeHtml(row.learner_name || "Người học")} · ${escapeHtml(row.question_id)}</p>${stimulusHtml(q.stimulus)}<p class="question-prompt">${escapeHtml(q.prompt)}</p><div class="grading-response">${escapeHtml(row.response?.text || "")}</div><details><summary class="small">Đáp án tham khảo</summary><p class="answer-text">${escapeHtml(q.correct_answer.text)}</p></details>${q.rubric.map((criterion, index) => `<label class="rubric-input"><span>${escapeHtml(criterion.criterion)} <span class="muted">(tối đa ${escapeHtml(criterion.points)})</span></span><input type="number" min="0" max="${escapeHtml(criterion.points)}" step="any" required data-grade-index="${index}" value="${escapeHtml(draft.scores[index])}" aria-label="Điểm tiêu chí ${index + 1}"></label>`).join("")}<label class="sr-only" for="note-${escapeHtml(row.attempt_id)}">Nhận xét chấm</label><textarea id="note-${escapeHtml(row.attempt_id)}" maxlength="2000" data-grade-note placeholder="Nhận xét chấm (không bắt buộc)">${escapeHtml(draft.note)}</textarea><button type="submit" class="primary" ${ui.busy ? "disabled" : ""}>Lưu điểm theo rubric</button><p class="grading-note">Quyền do máy chủ kiểm tra; không thể chấm bài của chính mình.</p></form>`;
            })
            .join("")
        : '<p class="empty-state">Không có bài đang chờ bạn chấm.</p>';
      $("grading-queue")
        .querySelectorAll("[data-grade]")
        .forEach((form) => {
          const id = form.dataset.grade,
            draft = ui.gradeDrafts[id];
          form.querySelectorAll("[data-grade-index]").forEach((input) => {
            input.oninput = () => {
              draft.scores[Number(input.dataset.gradeIndex)] = input.value;
            };
          });
          form.querySelector("[data-grade-note]").oninput = (event) => {
            draft.note = event.target.value;
          };
          form.onsubmit = (event) => {
            event.preventDefault();
            const scores = draft.scores.map((value) =>
              value === "" ? NaN : Number(value),
            );
            return operation("Đang lưu điểm rubric…", () =>
              session.grade(id, scores, draft.note),
            );
          };
        });
    }
    function render() {
      const state = session.state;
      $("identity-toggle").textContent =
        state.identity?.display_name || "Nhập tên";
      $("identity-toggle").disabled = ui.busy;
      $("save-identity").disabled = ui.busy;
      $("refresh-state").hidden = !state.identity;
      $("refresh-state").disabled = ui.busy;
      $("storage-bar").className = `storage-bar ${state.mode}`;
      $("storage-label").textContent =
        state.mode === "local"
          ? "Chỉ trên thiết bị này · Lần thử, gợi ý và góp ý không gửi lên máy chủ."
          : "Lưu dùng chung · Bạn chỉ xem lịch sử phiên học của mình. Không tự chuyển sang lưu cục bộ khi mất kết nối.";
      $("identity-help").textContent =
        state.mode === "local"
          ? "Không cần email hay mật khẩu. Dữ liệu chỉ nằm trong trình duyệt này; xóa dữ liệu trình duyệt sẽ mất lịch sử. Người dùng chung thiết bị có thể xem phiên này."
          : "Không cần email hay mật khẩu. Tên chỉ là nhãn, không phải đăng nhập. Xóa dữ liệu trình duyệt có thể mất quyền vào lịch sử; không khôi phục bằng cách nhập lại cùng tên. Người dùng chung thiết bị có thể xem phiên này.";
      $("learning-app").hidden = !state.loaded;
      if (!state.loaded || !data.questions.length) return;
      if (!ui.questionId) {
        let requested = "";
        try {
          requested = decodeURIComponent(location?.hash?.slice(1) || "");
        } catch {
          /* Invalid fragment is not a question. */
        }
        const active = state.attempts.find(
          (item) =>
            item.status === "in_progress" &&
            data.questions.some(
              (q) =>
                q.question_id === item.question_id &&
                data.question_meta[q.question_id].question_sha256 ===
                  item.question_sha256,
            ),
        );
        ui.questionId = data.questions.some((q) => q.question_id === requested)
          ? requested
          : active?.question_id ||
            core.recommendNext(session.learningData(), state.attempts)
              .question_id ||
            data.questions[0].question_id;
      }
      const effectiveData = session.learningData(),
        evidence = core.computeEvidence(effectiveData, state.attempts);
      renderNavigation(evidence);
      const currentAttempt = attempt(),
        exclusion = evidence.excluded_attempts.find(
          (item) => item.attempt_id === currentAttempt?.attempt_id,
        );
      const effective = exclusion
        ? {
            ...currentAttempt,
            evidence_eligible: false,
            exclusion_reasons: exclusion.reasons,
          }
        : currentAttempt;
      $("question-panel").innerHTML = questionHtml(effectiveData, question(), {
        attempt: effective,
        response: response(question()),
        busy: ui.busy,
        mode: state.mode,
      });
      renderNext();
      renderFeedback();
      renderEvidence(evidence);
      renderQueue();
    }
    $("source-name").textContent = data.source?.filename || data.run_id;
    $("run-label").textContent = data.run_id;
    $("identity-toggle").onclick = () => {
      $("identity-panel").hidden = !$("identity-panel").hidden;
      $("learner-name").value = session.state.identity?.display_name || "";
      if (!$("identity-panel").hidden) $("learner-name").focus();
    };
    $("identity-form").onsubmit = (event) => {
      event.preventDefault();
      const name = $("learner-name").value;
      return operation("Đang mở phiên học…", async () => {
        await session.saveName(name);
        $("identity-panel").hidden = true;
      });
    };
    $("refresh-state").onclick = () =>
      operation("Đang cập nhật lịch sử và kết quả chấm…", () =>
        session.reload(),
      );
    $("retry-operation").onclick = () => {
      if (ui.retry) ui.retry();
    };
    $("kc-select").onchange = (event) => {
      const q = data.questions.find(
        (item) => item.kc_id === event.target.value,
      );
      if (q) select(q.question_id);
    };
    $("grading-toggle").onclick = () => {
      ui.queueOpen = !ui.queueOpen;
      if (ui.queueOpen)
        return operation("Đang tải bài chờ chấm…", () => session.loadQueue());
      return renderQueue();
    };
    $("refresh-queue").onclick = () =>
      operation("Đang cập nhật hàng chờ…", () => session.loadQueue());
    $("question-panel").addEventListener("change", (event) => {
      const input = event.target,
        q = question();
      if (ui.busy || done(attempt())) return;
      const value = response(q);
      if (input.dataset.response === "choice") {
        if (q.interaction === "single_select")
          value.selection_ids = [input.value];
        else
          value.selection_ids = input.checked
            ? [...new Set([...value.selection_ids, input.value])]
            : value.selection_ids.filter((id) => id !== input.value);
      } else if (input.dataset.response === "match") {
        value.mappings = value.mappings.filter(
          (pair) => pair.left !== input.dataset.left,
        );
        if (input.value)
          value.mappings.push({ left: input.dataset.left, right: input.value });
      }
    });
    $("question-panel").addEventListener("input", (event) => {
      if (
        event.target.dataset.response === "text" &&
        !ui.busy &&
        !done(attempt())
      )
        response(question()).text = event.target.value;
    });
    $("learning-app").addEventListener("click", (event) => {
      const button = event.target.closest("[data-action]");
      if (!button || button.disabled || ui.busy) return;
      const action = button.dataset.action,
        qid = ui.questionId;
      if (action === "select") return select(button.dataset.question, true);
      if (action === "refresh")
        return operation("Đang cập nhật kết quả…", () => session.reload());
      if (action === "toggle-evidence") {
        ui.showAll = !ui.showAll;
        return render();
      }
      if (action === "move") {
        const value = response(question()),
          index = Number(button.dataset.index),
          next = index + Number(button.dataset.direction);
        if (next >= 0 && next < value.ordering.length)
          [value.ordering[index], value.ordering[next]] = [
            value.ordering[next],
            value.ordering[index],
          ];
        return render();
      }
      if (action === "vote") {
        const draft = ui.feedback[qid];
        draft.vote = button.dataset.vote;
        draft.open = true;
        draft.saved = false;
        return renderFeedback();
      }
      if (!requireName()) return;
      if (action === "hint")
        return operation("Đang ghi nhận gợi ý trước khi mở…", () =>
          session.revealHint(qid),
        );
      if (action === "submit") {
        const value = clone(response(question()));
        return operation("Đang lưu câu trả lời và chấm kết quả…", () =>
          session.submit(qid, value),
        );
      }
      if (action === "repeat")
        return operation("Đang tạo lần luyện tập mới…", async () => {
          await session.start(qid);
          ui.responses[qid] = defaultResponse(question());
        });
    });
    const ready = operation("Đang tải phiên học…", async () => {
      await session.init();
      $("identity-panel").hidden = Boolean(session.state.identity);
    });
    return { session, ui, ready, render, select };
  }

  function formatTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? ""
      : date.toLocaleString("vi-VN", {
          day: "2-digit",
          month: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        });
  }
  const api = {
    createSession,
    mount,
    escapeHtml,
    emptyResponse,
    questionHtml,
    controlsHtml,
    kcLinks,
    evidenceReason,
    answerText,
    shuffled,
  };
  root.LearningUI = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root.document && root.LEARNING_DATA !== undefined) {
    try {
      mount({
        document: root.document,
        data: root.LEARNING_DATA,
        core: root.LearningCore,
        config: root.LEARNING_AUTHORING_REVIEW,
        storage: root.localStorage,
        fetch: root.fetch?.bind(root),
        crypto: root.crypto,
        locks: root.navigator?.locks,
        location: root.location,
        history: root.history,
      });
    } catch (error) {
      showFatal(error);
    }
  } else if (root.document)
    showFatal(
      new Error(
        "Chưa tải được bộ học. Hãy tải lại trang; chưa có lần thử nào được ghi.",
      ),
    );
  function showFatal(error) {
    const panel = root.document.getElementById("error-panel"),
      message = root.document.getElementById("error-message"),
      retry = root.document.getElementById("retry-operation");
    if (panel && message && retry) {
      panel.hidden = false;
      message.textContent = error.message;
      retry.textContent = "Tải lại trang";
      retry.onclick = () => root.location.reload();
    }
  }
})(typeof window !== "undefined" ? window : globalThis);
