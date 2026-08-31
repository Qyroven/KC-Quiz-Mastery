/* Student-only application. Shared content comes from a pinned published release.
 * No provider calls, teacher mutation, raw review UI, or silent local fallback. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.StudentUI = api;
  if (root.document) {
    try {
      root.studentApp = api.mount({
        document: root.document, config: root.STUDENT_CONFIG,
        previewData: root.STUDENT_PREVIEW_DATA, core: root.LearningCore,
        storage: root.localStorage, fetch: root.fetch?.bind(root),
        crypto: root.crypto, locks: root.navigator?.locks,
        location: root.location, history: root.history,
      });
    } catch (error) {
      const panel = root.document.getElementById("error-panel");
      if (panel) {
        panel.hidden = false;
        root.document.getElementById("error-message").textContent = error.message;
        root.document.getElementById("retry-operation").hidden = true;
      }
    }
  }
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const object = (value) => value && typeof value === "object" && !Array.isArray(value);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const emptyResponse = () => ({ selection_ids: [], ordering: [], mappings: [], text: "" });
  const submitted = (row) => row && ["graded", "pending_grade"].includes(row.status);
  const types = { single_select: "Chọn một", multi_select: "Chọn nhiều", matching: "Ghép cặp", ordering: "Sắp xếp", short_text: "Trả lời ngắn" };
  const operations = { remember: "Nhớ", understand: "Hiểu", apply: "Vận dụng", analyze: "Phân tích", evaluate: "Đánh giá", create: "Sáng tạo" };
  const difficulties = { easy: "Dễ", medium: "Vừa", hard: "Khó", unknown: "Chưa ước lượng" };
  const labels = { no_evidence: "Chưa đo", needs_practice: "Cần ôn", demonstrated: "Đã có bằng chứng độc lập", pending_grade: "Chờ chấm", assisted: "Có bằng chứng khi dùng gợi ý", developing: "Mới đo một phần" };
  function latest(rows, questionId) {
    return rows.filter((row) => row.question_id === questionId).slice().sort((a, b) =>
      String(b.started_at).localeCompare(String(a.started_at)) || String(b.attempt_id).localeCompare(String(a.attempt_id)))[0] || null;
  }
  function validatePackage(data, shared) {
    if (!object(data) || data.schema_version !== "learning-package.v1" || !data.run_id ||
        !Array.isArray(data.questions) || !Array.isArray(data.kcs) || !Array.isArray(data.slots) || !object(data.question_meta))
      throw new Error("Bộ học chưa đúng định dạng. Không ghi kết quả vào một bộ học không xác định.");
    if (shared && (data.release_id !== data.run_id || data.publication?.status !== "PUBLISHED"))
      throw new Error("Bài học chưa phải phiên bản được giảng viên phát hành.");
    for (const q of data.questions) {
      if (!q.question_id || !types[q.interaction] || !data.question_meta[q.question_id]?.question_sha256)
        throw new Error("Câu hỏi thiếu định danh hoặc có tương tác chưa hỗ trợ.");
      if (shared && (Object.hasOwn(q, "correct_answer") || Object.hasOwn(q, "answer_explanation") ||
          (q.hints || []).some((hint) => Object.hasOwn(hint, "text"))))
        throw new Error("Máy chủ gửi dữ liệu đáp án trước khi nộp. Đã dừng để giữ đúng ranh giới học viên.");
    }
    return data;
  }
  function previewPacket(data) {
    validatePackage(data, false);
    const copy = clone(data);
    copy.questions = copy.questions.map((q) => {
      delete q.correct_answer; delete q.answer_explanation;
      q.hints = (q.hints || []).map(({ hint_id, kind }) => ({ hint_id, kind }));
      return q;
    });
    copy.release_id = copy.run_id;
    copy.course_id = copy.run_id;
    copy.label = "Bản xem thử cục bộ · chưa phát hành";
    // A local fixture may not manufacture a teacher publication assertion.
    delete copy.publication;
    for (const meta of Object.values(copy.question_meta)) delete meta.human_approved;
    return copy;
  }

  function createSession({ config, previewData, core, storage, fetch: fetcher, crypto: cryptoApi, locks, now = () => new Date().toISOString() }) {
    if (!config || !["shared", "local_preview"].includes(config.mode))
      throw new Error("Chưa cấu hình không gian học viên. Không tự chuyển sang chế độ cục bộ.");
    if (!core?.computeEvidence || !core?.recommendNext || !core?.normalizeResponse)
      throw new Error("Chưa tải được quy tắc bằng chứng. Không suy đoán kết quả học.");
    const shared = config.mode === "shared";
    let base = "", project;
    if (shared) {
      const url = new URL(config.supabaseUrl || "invalid:");
      if (!config.supabasePublishableKey ||
          (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname))))
        throw new Error("Cấu hình dữ liệu dùng chung không hợp lệ. Không dùng lịch sử cục bộ thay thế.");
      base = url.href.replace(/\/$/, ""); project = url.hostname;
      if (previewData !== undefined)
        throw new Error("Không được đóng gói dữ liệu xem thử cùng bản học viên dùng chung.");
    } else {
      validatePackage(previewData, false);
      project = previewData.run_id;
    }
    const prefix = `la-student:${config.mode}:${project}`;
    const keys = { identity: `${prefix}:identity`, auth: `${prefix}:session`, pending: `${prefix}:pending`, selected: `${prefix}:selected` };
    const state = { mode: config.mode, identity: null, courses: [], catalogStatus: "unloaded", data: null, attempts: [], feedback: [], itemQuality: {}, pending: {}, loaded: false };
    let auth = null;
    const read = (key, fallback) => {
      let value;
      try { value = storage.getItem(key); }
      catch { throw new Error("Không đọc được phiên học trong trình duyệt. Không tạo người học thay thế."); }
      if (value === null) return fallback;
      try { return JSON.parse(value); }
      catch { throw new Error("Dữ liệu phiên học không đọc được. Đã giữ nguyên để kiểm tra."); }
    };
    const write = (key, value) => {
      try { storage.setItem(key, JSON.stringify(value)); }
      catch { throw new Error("Trình duyệt chưa lưu được dữ liệu. Chưa xác nhận thao tác thành công."); }
    };
    const uuid = () => {
      if (!cryptoApi?.randomUUID) throw new Error("Cần HTTPS hoặc localhost để tạo mã thao tác an toàn.");
      return cryptoApi.randomUUID();
    };
    function readPending() {
      const value = read(keys.pending, {});
      if (!object(value) || Object.values(value).some((row) => !object(row) || !row.id || !row.kind || !object(row.payload)))
        throw new Error("Dữ liệu thao tác đang chờ không hợp lệ. Không ghi đè lịch sử.");
      return value;
    }
    function remember(kind, key, payload) {
      const existing = state.pending[key];
      if (existing) {
        if (JSON.stringify(existing.payload) !== JSON.stringify(payload))
          throw new Error("Câu trả lời trước đang chờ xác nhận. Cập nhật hoặc xác nhận thao tác cũ trước khi gửi câu trả lời khác.");
        return existing;
      }
      const row = { id: uuid(), kind, key, payload: clone(payload), created_at: now() };
      const pending = { ...state.pending, [key]: row };
      write(keys.pending, pending); state.pending = pending;
      return row;
    }
    function forget(key) {
      const pending = { ...state.pending }; delete pending[key];
      write(keys.pending, pending); state.pending = pending;
    }
    function reconcile() {
      const pending = { ...state.pending };
      for (const [key, row] of Object.entries(pending)) {
        const committed = row.kind === "feedback"
          ? state.feedback.some((event) => event.event_id === row.id)
          : row.kind === "start"
            ? state.attempts.some((attempt) => attempt.attempt_id === row.id)
            : row.kind === "hint"
              ? state.attempts.some((attempt) => attempt.attempt_id === row.payload.attempt_id && (attempt.hint_ids || []).includes(row.payload.hint_id))
              : row.kind === "submit" && state.attempts.some((attempt) => attempt.attempt_id === row.payload.attempt_id && submitted(attempt));
        if (committed) delete pending[key];
      }
      write(keys.pending, pending); state.pending = pending;
    }
    async function raw(path, body, token, prefer) {
      const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timeout = controller ? setTimeout(() => controller.abort(), 20000) : null;
      try {
        const result = await fetcher(base + path, {
          method: "POST", headers: { apikey: config.supabasePublishableKey, "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(prefer ? { Prefer: prefer } : {}) },
          body: JSON.stringify(body || {}), ...(controller ? { signal: controller.signal } : {}),
        });
        const text = result.status === 204 ? "" : await result.text();
        let payload;
        try { payload = text ? JSON.parse(text) : null; }
        catch { throw new Error("Chưa đọc được phản hồi máy chủ. Cập nhật để xác nhận thao tác có được lưu không."); }
        if (!result.ok) {
          const error = new Error(payload?.message || payload?.error_description || `Máy chủ trả lỗi ${result.status}.`);
          error.status = result.status; throw error;
        }
        return payload;
      } catch (error) {
        if (error.name === "AbortError") throw new Error("Kết nối quá lâu; trạng thái lưu chưa rõ. Thử lại dùng cùng mã thao tác.");
        throw error;
      } finally { if (timeout !== null) clearTimeout(timeout); }
    }
    function saveAuth(value) {
      const session = value?.session || value;
      if (!session?.access_token || !session.refresh_token || !session.user?.id)
        throw new Error("Chưa nhận được phiên học hợp lệ từ máy chủ.");
      session.expires_at ||= Math.floor(Date.now() / 1000) + Number(session.expires_in || 3600);
      if (state.identity && state.identity.learner_id !== session.user.id)
        throw new Error("Khóa phiên không thuộc người học này. Không thay người học hay trộn lịch sử.");
      write(keys.auth, session); auth = session; return session;
    }
    async function refreshAuth() {
      try { return saveAuth(await raw("/auth/v1/token?grant_type=refresh_token", { refresh_token: auth.refresh_token })); }
      catch {
        state.catalogStatus = "error";
        throw new Error("Chưa làm mới được phiên hiện tại. Đã giữ nguyên phiên; không đăng ký người học mới thay thế.");
      }
    }
    async function ensureAuth() {
      if (auth?.access_token && Number(auth.expires_at) > Date.now() / 1000 + 60) return auth;
      if (auth?.refresh_token) return refreshAuth();
      if (state.identity) throw new Error("Thiếu khóa phiên của người học này. Tên hiển thị không thể khôi phục danh tính.");
      return saveAuth(await raw("/auth/v1/signup", { data: { application: "learning-student" } }));
    }
    async function request(path, payload, prefer) {
      const session = await ensureAuth();
      try { return await raw(path, payload, session.access_token, prefer); }
      catch (error) {
        if (error.status !== 401 || !auth?.refresh_token) throw error;
        const fresh = await refreshAuth(); return raw(path, payload, fresh.access_token, prefer);
      }
    }
    const rpc = (name, payload) => request(`/rest/v1/rpc/${name}`, payload);
    const recordsKey = () => `${prefix}:${state.identity.learner_id}:${state.data.run_id}:records`;
    function requireIdentity() {
      if (!state.identity) throw new Error("Nhập tên hiển thị để lưu lượt học của riêng bạn.");
    }
    function question(id) {
      const q = state.data?.questions.find((item) => item.question_id === id);
      if (!q) throw new Error("Không tìm thấy câu hỏi trong phiên bản đang học.");
      return q;
    }
    function putAttempt(row) {
      if (!row?.attempt_id || !row.question_id || row.run_id !== state.data?.run_id || !["in_progress", "pending_grade", "graded"].includes(row.status))
        throw new Error("Kết quả lần thử không khớp phiên bản đang học. Chưa xác nhận đã lưu.");
      if (row.learner_id && row.learner_id !== state.identity.learner_id)
        throw new Error("Máy chủ trả lịch sử không thuộc người học này.");
      const attempts = state.attempts.filter((item) => item.attempt_id !== row.attempt_id).concat([row]);
      if (!shared) write(recordsKey(), { attempts, feedback: state.feedback });
      state.attempts = attempts;
      return row;
    }
    function learningData() {
      if (!shared || !state.data) return state.data;
      const meta = {};
      for (const [id, row] of Object.entries(state.data.question_meta)) {
        const live = state.itemQuality[id];
        meta[id] = { ...row, quality_status: !live ? "UNCHECKED" :
          live.question_sha256 !== row.question_sha256 ? "STALE" : live.quality_status || "UNCHECKED" };
      }
      return { ...state.data, question_meta: meta };
    }
    async function loadRecords() {
      if (!state.data || !state.identity) return;
      const result = shared ? await rpc("get_learning_state", { p_run_id: state.data.run_id }) : read(recordsKey(), { attempts: [], feedback: [] });
      if (!Array.isArray(result?.attempts) || !Array.isArray(result.feedback))
        throw new Error("Chưa đọc được lịch sử thật của phiên này. Không thay bằng dữ liệu giả.");
      if (result.attempts.some((row) => row.run_id !== state.data.run_id || (row.learner_id && row.learner_id !== state.identity.learner_id)))
        throw new Error("Lịch sử không khớp người học hoặc phiên bản bài học.");
      state.attempts = result.attempts; state.feedback = result.feedback;
      state.itemQuality = result.item_quality || {};
      reconcile();
    }
    async function catalog() {
      if (!state.identity) return;
      state.catalogStatus = "loading";
      try {
        const courses = shared ? await rpc("list_learning_courses", {}) : [{ course_id: previewData.run_id,
          title: previewData.source?.filename || "Bản xem thử", source_filename: previewData.source?.filename,
          latest_release: { release_id: previewData.run_id, label: "Bản xem thử cục bộ", question_count: previewData.questions.length, kc_count: previewData.kcs.length },
          enrollment: { release_id: previewData.run_id } }];
        if (!Array.isArray(courses)) throw new Error("Danh sách bài học chưa đọc được.");
        state.courses = courses; state.catalogStatus = "ready";
      } catch (error) {
        // A failed read is not an empty catalog. Keep the last confirmed list.
        state.catalogStatus = "error";
        throw error;
      }
    }
    async function openCourse(courseId) {
      requireIdentity();
      const course = state.courses.find((row) => row.course_id === courseId);
      if (!course) throw new Error("Bài học không nằm trong danh sách được phép mở.");
      const target = course.enrollment?.release_id || course.latest_release?.release_id;
      if (!target) throw new Error("Giảng viên chưa phát hành bài học này. Chưa có bài để làm.");
      const enrolled = shared ? await rpc("enroll_learning_course", { p_course_id: courseId, p_release_id: target }) : { course_id: courseId, release_id: target };
      if (enrolled?.course_id !== courseId || enrolled.release_id !== target)
        throw new Error("Phiên bản đăng ký học không khớp. Không tự đổi bài đang làm.");
      const packet = shared ? validatePackage(await rpc("get_student_learning_package", { p_release_id: enrolled.release_id }), true) : previewPacket(previewData);
      if (packet.run_id !== enrolled.release_id) throw new Error("Máy chủ trả nhầm phiên bản bài học.");
      packet.course_id ||= courseId;
      write(keys.selected, { course_id: courseId, release_id: enrolled.release_id });
      state.data = packet; course.enrollment = enrolled;
      await loadRecords(); return packet;
    }
    async function reload() {
      if (!state.identity) { state.loaded = true; return state; }
      state.pending = readPending();
      await catalog();
      if (state.data) {
        const course = state.courses.find((row) => row.course_id === state.data.course_id);
        if (shared && course?.enrollment?.release_id !== state.data.run_id)
          throw new Error("Đăng ký học đã thay đổi. Lịch sử hiện tại được giữ nguyên; không tự trộn sang bản mới.");
        await loadRecords();
      } else {
        const selected = read(keys.selected, null);
        const previous = selected && state.courses.find((row) => row.course_id === selected.course_id);
        if (previous && (!shared || previous.enrollment?.release_id === selected.release_id))
          await openCourse(previous.course_id);
      }
      state.loaded = true; return state;
    }
    async function init() {
      state.identity = read(keys.identity, null);
      if (state.identity && (!state.identity.learner_id || !String(state.identity.display_name || "").trim()))
        throw new Error("Danh tính lưu trên thiết bị không hợp lệ. Không tạo phiên thay thế.");
      state.pending = readPending(); auth = shared ? read(keys.auth, null) : null;
      return reload();
    }
    async function saveName(name) {
      const value = String(name || "").trim();
      if (!value || value.length > 80) throw new Error("Tên hiển thị cần từ 1 đến 80 ký tự.");
      let learnerId = state.identity?.learner_id;
      if (shared) {
        learnerId = (await ensureAuth()).user.id;
        await request("/rest/v1/reviewer_profiles?on_conflict=user_id", { user_id: learnerId, display_name: value }, "resolution=merge-duplicates,return=minimal");
      } else learnerId ||= uuid();
      const identity = { learner_id: learnerId, display_name: value };
      write(keys.identity, identity); state.identity = identity;
      return reload();
    }
    async function startInternal(id, forceRepeat = false) {
      requireIdentity(); const q = question(id), data = state.data, meta = data.question_meta[id];
      const current = latest(state.attempts, id);
      if (current && current.status === "in_progress") return current;
      if (submitted(current) && !forceRepeat) return current;
      const payload = { run_id: data.run_id, question_id: id, question_sha256: meta.question_sha256 };
      const pending = remember("start", `start:${data.run_id}:${id}`, payload);
      let attempt;
      if (shared) attempt = await rpc("start_learning_attempt", { p_run_id: data.run_id, p_question_id: id, p_question_sha256: meta.question_sha256, p_attempt_id: pending.id });
      else attempt = { attempt_id: pending.id, run_id: data.run_id, learner_id: state.identity.learner_id,
        question_id: id, question_sha256: meta.question_sha256, kc_id: q.kc_id, slot_id: q.slot_id,
        started_at: now(), submitted_at: null, status: "in_progress", response: null,
        hint_ids: [], revealed_hints: [], is_repeat: Boolean(current), score: null, max_score: null, correct: null,
        quality_status: meta.initial_check_status, evidence_eligible: false, exclusion_reasons: ["not_graded"] };
      putAttempt(attempt); forget(pending.key); return attempt;
    }
    async function revealHintInternal(id, requestedHintId = null) {
      const q = question(id), attempt = await startInternal(id);
      if (submitted(attempt)) throw new Error("Lần thử đã nộp. Chọn làm lại nếu muốn luyện tập thêm.");
      const key = `hint:${attempt.attempt_id}`, previous = state.pending[key];
      const hint = requestedHintId
        ? (q.hints || []).find((row) => row.hint_id === requestedHintId)
        : previous
        ? (q.hints || []).find((row) => row.hint_id === previous.payload.hint_id)
        : (q.hints || []).find((row) => !(attempt.hint_ids || []).includes(row.hint_id));
      if (!hint) throw new Error("Đã mở hết gợi ý của phiên bản câu hỏi này.");
      const pending = remember("hint", key, { run_id: state.data.run_id, question_id: id,
        attempt_id: attempt.attempt_id, hint_id: hint.hint_id });
      const result = shared ? await rpc("reveal_learning_hint", { p_attempt_id: attempt.attempt_id, p_hint_id: hint.hint_id }) :
        { ...attempt, hint_ids: [...new Set([...attempt.hint_ids, hint.hint_id])], revealed_hints: [
          ...(attempt.revealed_hints || []).filter((row) => row.hint_id !== hint.hint_id),
          clone(previewData.questions.find((row) => row.question_id === id).hints.find((row) => row.hint_id === hint.hint_id))] };
      // Only show hint text returned AFTER the server records support use.
      putAttempt(result); forget(pending.key); return result;
    }
    async function submitInternal(id, response) {
      const q = question(id), normalized = core.normalizeResponse(q, response);
      if (!normalized.valid) throw new Error("Hoàn tất câu trả lời đúng dạng trước khi nộp: chọn đáp án hoặc ghép đủ các dòng.");
      if (normalized.response.text.length > 8000) throw new Error("Câu trả lời tối đa 8.000 ký tự.");
      const attempt = await startInternal(id);
      if (submitted(attempt)) return attempt;
      const payload = { run_id: state.data.run_id, attempt_id: attempt.attempt_id, question_id: id, response: normalized.response };
      const pending = remember("submit", `submit:${attempt.attempt_id}`, payload);
      let result;
      if (shared) result = await rpc("submit_learning_attempt", { p_attempt_id: attempt.attempt_id, p_response: pending.payload.response });
      else {
        result = core.buildLocalAttempt(previewData, id, pending.payload.response, { attempt_id: attempt.attempt_id,
          started_at: attempt.started_at, submitted_at: now(), hint_ids: attempt.hint_ids || [],
          learner_id: state.identity.learner_id, attempts: state.attempts.filter((row) => row.attempt_id !== attempt.attempt_id) });
        const original = previewData.questions.find((row) => row.question_id === id);
        result.revealed_hints = attempt.revealed_hints || [];
        result.answer_material = { correct_answer: clone(original.correct_answer), answer_explanation: original.answer_explanation, rubric: clone(original.rubric) };
      }
      putAttempt(result); forget(pending.key); return result;
    }
    async function sendFeedbackInternal(id, vote, note, attemptId = null) {
      requireIdentity(); question(id);
      const clean = String(note || "").trim();
      if (!["like", "dislike"].includes(vote) || clean.length > 2000)
        throw new Error("Chọn Hữu ích hoặc Cần cải thiện; ghi chú tối đa 2.000 ký tự.");
      const payload = { run_id: state.data.run_id, question_id: id, question_sha256: state.data.question_meta[id].question_sha256,
        vote, note: clean || null, attempt_id: attemptId };
      const committed = state.feedback.find((event) => event.question_id === id &&
        event.question_sha256 === payload.question_sha256 &&
        (event.attempt_id || null) === attemptId && event.payload?.vote === vote &&
        (event.payload?.note || null) === payload.note);
      // A reload may have confirmed the event and cleared its pending marker
      // before an explicit retry reaches here. Return that receipt, not a new ID.
      if (committed) return committed;
      // Bind ID to the complete immutable payload, not just question ID. A new
      // note after an uncertain save can never collide with the old event ID.
      const key = `feedback:${JSON.stringify(payload)}`;
      const pending = remember("feedback", key, payload);
      const event = shared ? await rpc("append_learning_feedback", { p_run_id: payload.run_id, p_question_id: id,
        p_question_sha256: payload.question_sha256, p_vote: vote, p_note: payload.note,
        p_attempt_id: attemptId, p_event_id: pending.id }) : { event_id: pending.id,
        run_id: payload.run_id, question_id: id, question_sha256: payload.question_sha256,
        attempt_id: attemptId, kind: "feedback", payload: { vote, note: payload.note }, created_at: now() };
      if (!event?.event_id || event.event_id !== pending.id) throw new Error("Chưa xác nhận góp ý đã được lưu.");
      const feedback = state.feedback.filter((row) => row.event_id !== event.event_id).concat([event]);
      if (!shared) write(recordsKey(), { attempts: state.attempts, feedback });
      state.feedback = feedback; forget(key); return event;
    }
    async function withWrite(task) {
      if (!shared && !locks?.request) throw new Error("Bản xem thử cần Web Locks trên HTTPS hoặc localhost để lưu an toàn giữa các tab.");
      const locked = async () => {
        state.pending = readPending();
        if (state.data && state.identity) await loadRecords();
        return task();
      };
      return locks?.request ? locks.request(`${prefix}:write`, { mode: "exclusive" }, locked) : locked();
    }
    async function retryPending(key) {
      return withWrite(async () => {
        const row = state.pending[key];
        if (!row) return null;
        if (row.payload.run_id !== state.data?.run_id) throw new Error("Mở đúng bài học của thao tác đang chờ trước khi xác nhận.");
        if (row.kind === "feedback") return sendFeedbackInternal(row.payload.question_id, row.payload.vote, row.payload.note, row.payload.attempt_id);
        if (row.kind === "submit") return submitInternal(row.payload.question_id, row.payload.response);
        if (row.kind === "start") return startInternal(row.payload.question_id, true);
        if (row.kind === "hint") return revealHintInternal(row.payload.question_id, row.payload.hint_id);
        throw new Error("Thao tác đang chờ chưa được hỗ trợ.");
      });
    }
    return { state, init, reload, saveName, openCourse, learningData, retryPending,
      start: (id) => withWrite(() => startInternal(id, true)),
      revealHint: (id, hintId) => withWrite(() => revealHintInternal(id, hintId)),
      submit: (id, response) => withWrite(() => submitInternal(id, response)),
      feedback: (id, vote, note, attemptId) => withWrite(() => sendFeedbackInternal(id, vote, note, attemptId)) };
  }

  function shuffled(items, seedText) {
    let seed = 2166136261;
    for (const char of seedText) seed = Math.imul(seed ^ char.charCodeAt(0), 16777619) >>> 0;
    const result = items.slice();
    for (let i = result.length - 1; i > 0; i--) {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      const j = seed % (i + 1); [result[i], result[j]] = [result[j], result[i]];
    }
    return result;
  }
  function answerText(q, response) {
    const options = [...(q.choice_options || []), ...(q.matching_left || []), ...(q.matching_right || []), ...(q.ordering_options || [])];
    const text = (id) => options.find((row) => row.option_id === id)?.text || id;
    if (response?.selection_ids?.length) return response.selection_ids.map(text).join("\n");
    if (response?.ordering?.length) return response.ordering.map((id, i) => `${i + 1}. ${text(id)}`).join("\n");
    if (response?.mappings?.length) return response.mappings.map((pair) => `${text(pair.left)} → ${text(pair.right)}`).join("\n");
    return response?.text || "";
  }
  function stimulusHtml(stimulus) {
    if (!stimulus || stimulus.kind === "none") return "";
    return `<div class="stimulus">${stimulus.text ? `<div>${esc(stimulus.text)}</div>` : ""}${stimulus.table_columns?.length ?
      `<div class="table-scroll"><table><thead><tr>${stimulus.table_columns.map((v) => `<th>${esc(v)}</th>`).join("")}</tr></thead><tbody>${(stimulus.table_rows || []).map((row) => `<tr>${row.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : ""}${stimulus.formula ? `<div class="formula">${esc(stimulus.formula)}</div>` : ""}</div>`;
  }
  function controlsHtml(q, response, disabled) {
    let content = "";
    if (["single_select", "multi_select"].includes(q.interaction)) {
      const type = q.interaction === "single_select" ? "radio" : "checkbox";
      content = `<p class="answer-instruction">${type === "radio" ? "Chọn một đáp án." : "Chọn tất cả đáp án phù hợp."}</p><div class="choices">${shuffled(q.choice_options, q.question_id + "choice").map((row) =>
        `<label class="choice"><input type="${type}" name="student-choice" value="${esc(row.option_id)}" data-response="choice" ${response.selection_ids.includes(row.option_id) ? "checked" : ""}><span>${esc(row.text)}</span></label>`).join("")}</div>`;
    } else if (q.interaction === "short_text") {
      content = `<label class="answer-instruction" for="student-answer">Giải thích theo cách hiểu của bạn. Giảng viên sẽ chấm theo tiêu chí của câu hỏi.</label><textarea id="student-answer" maxlength="8000" data-response="text" placeholder="Câu trả lời của bạn…">${esc(response.text)}</textarea>`;
    } else if (q.interaction === "matching") {
      content = `<p class="answer-instruction">Chọn phần phù hợp cho từng dòng.</p>${q.matching_left.map((left, i) => `<div class="matching-row"><label for="match-${i}">${esc(left.text)}</label><select id="match-${i}" data-response="match" data-left="${esc(left.option_id)}"><option value="">Chọn cặp phù hợp…</option>${shuffled(q.matching_right, q.question_id + "right").map((right) =>
        `<option value="${esc(right.option_id)}" ${response.mappings.some((pair) => pair.left === left.option_id && pair.right === right.option_id) ? "selected" : ""}>${esc(right.text)}</option>`).join("")}</select></div>`).join("")}`;
    } else if (q.interaction === "ordering") {
      content = `<p class="answer-instruction">Dùng ↑ ↓ để sắp xếp các bước.</p><ol class="order-list">${response.ordering.map((id, i) => `<li class="order-row"><span class="muted small">${i + 1}</span><span class="order-text">${esc(q.ordering_options.find((row) => row.option_id === id)?.text || id)}</span><span class="order-actions"><button type="button" data-action="move" data-index="${i}" data-direction="-1" aria-label="Đưa mục ${i + 1} lên" ${i === 0 ? "disabled" : ""}>↑</button><button type="button" data-action="move" data-index="${i}" data-direction="1" aria-label="Đưa mục ${i + 1} xuống" ${i === response.ordering.length - 1 ? "disabled" : ""}>↓</button></span></li>`).join("")}</ol>`;
    }
    return `<fieldset class="answer-controls" ${disabled ? "disabled" : ""}><legend class="sr-only">Câu trả lời của bạn</legend>${content}</fieldset>`;
  }
  function evidenceNote(attempt) {
    if (attempt.status === "pending_grade") return "Đang chờ giảng viên chấm; chưa kết luận đúng, sai hay cần ôn.";
    if (attempt.is_repeat || (attempt.exclusion_reasons || []).includes("repeated_question")) return "Đây là lần luyện lại sau khi đã thấy đáp án, không phải bằng chứng độc lập mới.";
    if (attempt.evidence_eligible !== true) return "Lần thử được lưu nhưng chưa dùng làm bằng chứng độc lập; chất lượng hoặc phiên bản nội dung cần được kiểm tra.";
    if (attempt.hint_ids?.length) return "Có dùng gợi ý. Kết quả này không được gộp với việc tự làm độc lập.";
    return "Bằng chứng cho mục tiêu của câu hỏi này; không phải điểm đánh giá toàn bộ năng lực của bạn.";
  }
  function questionHtml(data, q, { attempt = null, response = emptyResponse(), busy = false, mode = "shared" } = {}) {
    const slot = data.slots.find((row) => row.slot_id === q.slot_id) || {};
    const done = submitted(attempt), hints = attempt?.revealed_hints || [], used = attempt?.hint_ids || [];
    let html = `<header class="question-head"><div class="question-meta"><span class="tag">${esc(types[q.interaction])}</span>${[operations[slot.cognitive_operation], difficulties[slot.intended_difficulty]].filter(Boolean).map((v) => `<span class="tag">${esc(v)}</span>`).join("")}</div><h2 id="question-title">${esc(q.title)}</h2></header><div class="question-body">${stimulusHtml(q.stimulus)}<p class="question-prompt">${esc(q.prompt)}</p>${controlsHtml(q, response, done || busy)}`;
    if (!done) html += `<div class="question-actions">${q.hints?.length ? `<button type="button" class="secondary" data-action="hint" ${busy || used.length >= q.hints.length ? "disabled" : ""}>${used.length ? "Gợi ý tiếp" : "Mở gợi ý"}</button><span class="hint-counter">${used.length}/${q.hints.length}</span>` : ""}<span class="spacer"></span><button type="button" class="primary" data-action="submit" ${busy ? "disabled" : ""}>Nộp câu trả lời</button></div><p class="attempt-note">${mode === "local_preview" ? "Xem thử: chỉ lưu trên thiết bị này." : "Lưu vào phiên học riêng của bạn."} Bản nháp chưa nộp mất khi tải lại trang. Chỉ xác nhận sau khi lưu thành công.</p>`;
    html += hints.filter((hint) => used.includes(hint.hint_id)).map((hint, i) => `<div class="hint-box"><strong>GỢI Ý ${i + 1} · ĐÃ GHI NHẬN HỖ TRỢ</strong><p>${esc(hint.text)}</p></div>`).join("");
    if (done) {
      const pending = attempt.status === "pending_grade", material = attempt.answer_material;
      html += `<div class="result-card ${pending ? "pending" : attempt.correct ? "" : "incorrect"}" role="status"><h3>${pending ? "Đã nộp · Chờ chấm" : attempt.correct ? "Đã đáp ứng yêu cầu của câu hỏi" : "Cần xem lại câu trả lời"}</h3><p>${esc(evidenceNote(attempt))}</p>`;
      if (attempt.grading_method === "rubric_human") {
        const rubric = material?.rubric || q.rubric || [];
        html += `<h3>Nhận xét của giảng viên</h3><p class="teacher-note">${esc(attempt.grading_note || "Giảng viên chưa để lại ghi chú bổ sung.")}</p><ul class="rubric-list">${rubric.map((row, i) => `<li><span>${esc(row.criterion)}</span><span class="points">${esc(attempt.rubric_scores?.[i] ?? "—")} / ${esc(row.points)}</span></li>`).join("")}</ul><p class="muted small">Điểm từng tiêu chí chỉ dùng để chấm câu này, không phải điểm mastery.</p>`;
      }
      if (material) html += `<details><summary>${pending ? "Đáp án tham khảo · không phải kết quả chấm" : "Xem đáp án và giải thích"}</summary><div class="answer-text">${esc(answerText(q, material.correct_answer))}</div><p>${esc(material.answer_explanation)}</p></details>`;
      html += `</div><div class="question-actions"><button type="button" class="secondary" data-action="repeat" ${busy ? "disabled" : ""}>Làm lại · chỉ luyện tập</button><span class="hint-counter">Đã dùng ${used.length} gợi ý</span></div>`;
    }
    return html + "</div>";
  }
  function stateTag(state, core) {
    return `<span class="tag ${state === "demonstrated" ? "green" : state === "needs_practice" ? "amber" : "blue"}">${esc(core?.evidenceLabel?.(state) || labels[state] || "Chưa đo")}</span>`;
  }
  const reasons = {
    after_assisted: "Bạn đã dùng gợi ý cho mục tiêu này. Ôn lại nội dung rồi thử một câu khác để tự thể hiện cách hiểu.",
    after_incorrect: "Kết quả vừa rồi cho thấy mục tiêu này còn cần ôn. Câu tiếp được chọn để kiểm tra lại đúng mục tiêu đó.",
    unattempted_eligible_question: "Mục tiêu này chưa được đo độc lập. Một câu chưa làm có thể bổ sung bằng chứng mới.",
    human_rubric_required: "Còn câu trả lời chờ giảng viên chấm. Chưa kết luận người học hiểu hay chưa hiểu từ các câu đó.",
    no_variant_for_target_slot: "Mục tiêu này còn cần bằng chứng, nhưng chưa có câu mới phù hợp. Có thể ôn lại; làm lại cùng câu không trở thành bằng chứng độc lập mới.",
    no_unattempted_eligible_question: "Chưa còn câu mới phù hợp để bổ sung bằng chứng. Chờ giảng viên chấm hoặc bổ sung câu hỏi, thay vì lặp lại để tăng điểm.",
    another_unmeasured_objective: "Đây là một mục tiêu khác chưa được đo, không thay thế việc ôn mục tiêu còn thiếu.",
    mixed_learner_scope: "Lịch sử chưa đúng phạm vi một người học. Không đưa ra kết luận từ dữ liệu bị trộn.",
  };
  function nextHtml(data, next, busy) {
    const titles = { practice: "Thử một mục tiêu chưa đo", review_and_practice: "Ôn đúng chỗ, rồi thử câu khác", waiting_grading: "Chờ bằng chứng từ bài được chấm", need_more_evidence: "Cần thêm bằng chứng phù hợp" };
    const kc = data.kcs.find((row) => row.kc_id === (next.review?.kc_id || next.kc_id));
    const slot = data.slots.find((row) => row.slot_id === next.slot_id);
    return `<div class="next-copy"><p class="eyebrow">BƯỚC TIẾP THEO</p><h2 id="next-title">${esc(titles[next.action] || "Tiếp tục học")}</h2><p>${esc(reasons[next.reason] || "Đề xuất dựa trên mục tiêu còn thiếu bằng chứng trong bài học.")}</p>${slot?.evidence_intent ? `<p><strong>Mục tiêu:</strong> ${esc(slot.evidence_intent)}</p>` : ""}${kc ? kc.content_available === false ? '<p class="muted small">Nội dung này chưa được giảng viên phát hành.</p>' : `<button class="quiet" data-action="knowledge" data-kc="${esc(kc.kc_id)}" type="button">Ôn: ${esc(kc.name)}</button>` : ""}</div>${next.question_id ? `<button class="primary" data-action="select" data-question="${esc(next.question_id)}" type="button" ${busy ? "disabled" : ""}>Đến câu tiếp →</button>` : next.alternative?.question_id ? `<div><button class="secondary" data-action="select" data-question="${esc(next.alternative.question_id)}" type="button" ${busy ? "disabled" : ""}>Học mục tiêu khác →</button><p class="small muted">Không thay thế mục tiêu còn thiếu.</p></div>` : '<button class="secondary" data-action="refresh" type="button">Cập nhật kết quả</button>'}`;
  }

  function progressHtml(data, evidence, core) {
    const hidden = [];
    const cards = evidence.kcs.map((row) => {
      const kc = data.kcs.find((item) => item.kc_id === row.kc_id);
      if (kc?.content_available === false) { hidden.push(row); return ""; }
      return `<article class="progress-item"><strong>${esc(kc?.name || "Kiến thức")}</strong>${stateTag(row.state, core)}<p>${row.coverage_available ? `${Number(row.independent_slots) || 0} mục tiêu đã có bằng chứng độc lập; ${Number(row.total_slots) || 0} mục tiêu trong bài.` : "Chưa có mục tiêu đo đủ rõ để kết luận."}${row.assisted_slots ? ` ${row.assisted_slots} có hỗ trợ.` : ""}${row.pending_slots ? ` ${row.pending_slots} chờ chấm.` : ""}</p><details><summary>Căn cứ theo từng mục tiêu</summary><ul class="slot-list">${(row.slots || []).map((item) => { const slot = data.slots.find((s) => s.slot_id === item.slot_id); return `<li>${esc(slot?.evidence_intent || slot?.assessment_intent || "Mục tiêu của bài học")} — ${esc(core.evidenceLabel?.(item.state) || labels[item.state] || "Chưa đo")}</li>`; }).join("")}</ul></details><button type="button" class="quiet" data-action="knowledge" data-kc="${esc(row.kc_id)}">Ôn kiến thức này</button></article>`;
    }).join("");
    if (!hidden.length) return cards;
    const slots = hidden.reduce((total, row) => total + (Number(row.total_slots) || 0), 0);
    return cards + `<article class="progress-item unpublished-summary"><strong>${hidden.length} nội dung · ${slots} mục tiêu chưa phát hành</strong><p>Chưa được giảng viên phát hành không có nghĩa là bạn chưa hiểu. Các mục tiêu này vẫn thuộc phạm vi bài học, chưa dùng để kết luận năng lực.</p></article>`;
  }

  function catalogView(state, busy = false) {
    const ready = state.catalogStatus === "ready";
    const status = ready ? state.mode === "local_preview"
      ? "Bản xem thử cục bộ — chưa phát hành" : "Chỉ học từ phiên bản được phát hành"
      : state.catalogStatus === "loading" ? "Đang tải danh sách bài học…"
      : state.catalogStatus === "error" ? "Chưa cập nhật được danh sách bài học"
      : "Danh sách bài học chưa được tải";
    if (!state.courses.length) return { status, html: `<p class="empty-state">${ready
      ? "Chưa có bài học được phát hành cho không gian này."
      : state.catalogStatus === "error"
        ? "Chưa tải được danh sách bài học. Chưa xác định có bài học để mở; hãy thử cập nhật lại."
        : status}</p>` };
    const notice = ready ? "" : '<p class="empty-state">Đang hiển thị danh sách đã tải trước đó; chưa xác nhận trạng thái mới. Cập nhật thành công trước khi mở bài.</p>';
    const cards = state.courses.map((course) => {
      const release = course.latest_release, available = Boolean(course.enrollment?.release_id || release?.release_id);
      const earlier = course.enrollment && release && course.enrollment.release_id !== release.release_id;
      return `<button class="course-item" data-action="course" data-course="${esc(course.course_id)}" type="button" ${!ready || !available || busy ? "disabled" : ""}><strong>${esc(course.title || course.source_filename || "Bài học")}</strong><small>${earlier ? "Giữ nguyên phiên bản bạn đã bắt đầu." : esc(release?.label || "Chưa có phiên bản phát hành")}</small>${earlier ? `<small>Có bản mới: ${esc(release.label)}. Lượt học hiện tại không tự đổi sang bản này.</small>` : release ? `<small>${Number(release.question_count) || 0} câu hỏi · ${Number(release.kc_count) || 0} mục kiến thức trong bài</small>` : ""}<span class="open-label">${course.enrollment ? "Tiếp tục phiên bản đang học →" : available ? "Mở bài học →" : "Giảng viên chưa phát hành"}</span></button>`;
    }).join("");
    return { status, html: notice + cards };
  }

  function mount({ document: doc, config, previewData, core, storage, fetch: fetcher, crypto, locks, location, history }) {
    const session = createSession({ config, previewData, core, storage, fetch: fetcher, crypto, locks });
    const $ = (id) => doc.getElementById(id);
    const ui = { busy: false, retry: null, questionId: null, knowledgeId: null, responses: {}, feedback: {}, catalog: true };
    const q = () => session.state.data?.questions.find((row) => row.question_id === ui.questionId);
    const attempt = () => latest(session.state.attempts, ui.questionId);
    const draftKey = (id) => `${session.state.data?.run_id}:${id}`;
    function response(question) {
      const record = latest(session.state.attempts, question.question_id);
      if (submitted(record)) return record.response || emptyResponse();
      const key = draftKey(question.question_id);
      if (!ui.responses[key]) {
        ui.responses[key] = emptyResponse();
        if (question.interaction === "ordering") ui.responses[key].ordering = shuffled(question.ordering_options, question.question_id + "order").map((row) => row.option_id);
      }
      return ui.responses[key];
    }
    function showError(error, retry) {
      $("error-panel").hidden = false; $("error-message").textContent = error.message || String(error);
      $("retry-operation").hidden = !retry; ui.retry = retry || null;
    }
    async function operation(label, action) {
      if (ui.busy) return;
      ui.busy = true; ui.retry = null; $("error-panel").hidden = true;
      $("operation-status").hidden = false; $("operation-status").textContent = label; render();
      try { await action(); }
      catch (error) { showError(error, () => operation(label, action)); }
      finally { ui.busy = false; $("operation-status").hidden = true; render(); }
    }
    function select(id, focus = true) {
      if (ui.busy || !session.state.data?.questions.some((row) => row.question_id === id)) return;
      ui.questionId = id; ui.knowledgeId = null; ui.catalog = false;
      history?.replaceState(null, "", `#question=${encodeURIComponent(id)}`); render();
      if (focus) $("question-panel").focus({ preventScroll: true });
    }
    function requireIdentity() {
      if (session.state.identity) return true;
      $("identity-panel").hidden = false; $("learner-name").focus();
      showError(new Error("Nhập tên hiển thị để bắt đầu lưu kết quả.")); return false;
    }
    function renderCatalog() {
      const state = session.state;
      $("catalog-panel").hidden = !state.identity || (!ui.catalog && Boolean(state.data));
      const view = catalogView(state, ui.busy);
      $("catalog-status").textContent = view.status;
      $("course-list").innerHTML = view.html;
    }
    function renderKnowledge() {
      const data = session.state.data;
      const kc = data.kcs.find((row) => row.kc_id === (ui.knowledgeId || q()?.kc_id));
      if (kc?.content_available === false) {
        $("knowledge-panel").innerHTML = '<h3 id="knowledge-title">Nội dung chưa phát hành</h3><p class="muted small">Nội dung này chưa được giảng viên phát hành. Mục tiêu vẫn được ghi nhận là chưa đo, không suy ra người học còn yếu.</p>';
        return;
      }
      $("knowledge-panel").innerHTML = kc ? `<details ${ui.knowledgeId ? "open" : ""}><summary id="knowledge-title">Ôn kiến thức liên quan</summary><h3>${esc(kc.name)}</h3><div class="knowledge-copy">${esc(kc.knowledge_description || "")}</div>${kc.observable_claim ? `<p class="knowledge-copy"><strong>Điều cần thể hiện:</strong> ${esc(kc.observable_claim)}</p>` : ""}<p class="muted small">Nội dung chỉ đọc của phiên bản bài học đang chọn.</p></details>` : '<p class="muted small">Chưa có nội dung ôn được liên kết với câu này.</p>';
    }
    function renderFeedback() {
      const id = ui.questionId, key = draftKey(id);
      const draft = ui.feedback[key] ||= { vote: "", note: "", open: false, saved: false };
      $("feedback-panel").innerHTML = `<details ${draft.open ? "open" : ""} id="feedback-details"><summary><strong>Câu hỏi này có hữu ích?</strong><span>Góp ý về nội dung, tách riêng với bằng chứng học của bạn.</span></summary><form id="feedback-form" class="feedback-form"><div class="vote-row"><button type="button" class="secondary" data-action="vote" data-vote="like" aria-pressed="${draft.vote === "like"}">👍 Hữu ích</button><button type="button" class="secondary" data-action="vote" data-vote="dislike" aria-pressed="${draft.vote === "dislike"}">👎 Cần cải thiện</button></div><label class="sr-only" for="feedback-note">Góp ý thêm</label><textarea id="feedback-note" maxlength="2000" placeholder="Điều gì chưa rõ hoặc cần cải thiện? (không bắt buộc)">${esc(draft.note)}</textarea><div class="feedback-footer"><span class="${draft.saved ? "feedback-saved" : "muted small"}">${draft.saved ? "Đã lưu góp ý của bạn." : "Không tự sửa câu hỏi hay thay đổi kết quả học."}</span><button type="submit" class="secondary" ${!draft.vote || ui.busy ? "disabled" : ""}>Gửi góp ý</button></div></form></details>`;
      $("feedback-details").ontoggle = (event) => { draft.open = event.currentTarget.open; };
      $("feedback-note").oninput = (event) => { draft.note = event.target.value; draft.saved = false; };
      $("feedback-form").onsubmit = (event) => {
        event.preventDefault(); if (!requireIdentity()) return;
        const payload = { id, vote: draft.vote, note: draft.note, attemptId: attempt()?.attempt_id || null };
        operation("Đang lưu góp ý…", async () => { await session.feedback(payload.id, payload.vote, payload.note, payload.attemptId); draft.saved = true; });
      };
    }
    function renderProgress(evidence) {
      const data = session.state.data;
      $("progress-list").innerHTML = progressHtml(data, evidence, core);
      $("history-count").textContent = `(${session.state.attempts.length})`;
      $("history-list").innerHTML = session.state.attempts.slice().reverse().map((row) => {
        const question = data.questions.find((item) => item.question_id === row.question_id);
        const label = row.status === "in_progress" ? "Đang làm" : row.status === "pending_grade" ? "Chờ chấm" : row.correct ? "Đã đáp ứng yêu cầu câu hỏi" : "Cần xem lại";
        return `<article class="history-item"><div><button type="button" data-action="select" data-question="${esc(row.question_id)}">${esc(question?.title || "Câu hỏi")}</button><time>${esc(formatTime(row.submitted_at || row.started_at))}</time><p>${label} · ${(row.hint_ids || []).length} gợi ý${row.is_repeat ? " · Luyện lại" : ""}</p></div><div><p>${esc(evidenceNote(row))}</p>${submitted(row) && question ? `<details><summary>Câu trả lời đã lưu</summary><p class="answer-text">${esc(answerText(question, row.response))}</p>${row.grading_method === "rubric_human" ? `<p><strong>Giảng viên:</strong> ${esc(row.grading_note || "Chưa có ghi chú bổ sung.")}</p>` : ""}</details>` : ""}</div></article>`;
      }).join("") || '<p class="empty-state">Chưa có lần thử. Bằng chứng xuất hiện từ các hành động học thật của bạn.</p>';
    }
    function render() {
      const state = session.state;
      $("identity-toggle").textContent = state.identity?.display_name || "Nhập tên";
      $("identity-toggle").disabled = ui.busy;
      $("refresh-state").hidden = !state.identity;
      $("refresh-state").disabled = ui.busy;
      $("storage-status").className = `storage-status ${state.mode === "local_preview" ? "preview" : ""}`;
      $("storage-status").textContent = state.mode === "local_preview"
        ? "Xem thử cục bộ · chưa phát hành. Chỉ lưu trên trình duyệt này; dữ liệu bản xem thử có đáp án, không dùng làm bài thi bảo mật."
        : state.catalogStatus === "error" ? "Chưa cập nhật được dữ liệu dùng chung. Phiên và lịch sử đã tải được giữ nguyên; chưa xác nhận dữ liệu mới."
        : "Phiên học riêng của bạn · lưu dùng chung theo phiên bản giảng viên phát hành. Tên hiển thị không cấp quyền quản lý nội dung.";
      renderCatalog();
      $("course-panel").hidden = !state.data;
      const pending = Object.values(state.pending).filter((row) => row.payload.run_id === state.data?.run_id);
      $("pending-panel").hidden = !pending.length;
      $("pending-panel").innerHTML = pending.length ? `<div><strong>Còn ${pending.length} thao tác chưa rõ trạng thái lưu</strong><p>Cập nhật để kiểm tra, hoặc gửi lại đúng thao tác cũ; không tạo bản trùng.</p></div><button class="secondary" type="button" data-action="pending" data-key="${esc(pending[0].key)}" ${ui.busy ? "disabled" : ""}>Xác nhận thao tác cũ</button>` : "";
      if (!state.data) return;
      if (!q()) {
        const hash = new URLSearchParams(String(location?.hash || "").replace(/^#/, ""));
        ui.questionId = state.data.questions.find((row) => row.question_id === hash.get("question"))?.question_id || state.data.questions[0]?.question_id;
      }
      const course = state.courses.find((row) => row.course_id === state.data.course_id);
      $("course-title").textContent = course?.title || state.data.source?.filename || "Bài học";
      $("version-label").textContent = `${state.data.label || "Phiên bản đã phát hành"} · Lượt học luôn giữ đúng phiên bản này.${course?.latest_release?.release_id !== state.data.run_id ? " Đã có phiên bản mới; lịch sử hiện tại không tự đổi." : ""}`;
      $("question-count").textContent = `${state.data.questions.length} câu`;
      $("question-list").innerHTML = state.data.questions.map((row, i) => {
        const record = latest(state.attempts, row.question_id);
        return `<button class="question-nav ${row.question_id === ui.questionId ? "active" : ""}" type="button" data-action="select" data-question="${esc(row.question_id)}" ${ui.busy ? "disabled" : ""} ${row.question_id === ui.questionId ? 'aria-current="true"' : ""}><span class="question-number">${i + 1}</span><span><strong>${esc(row.title)}</strong><small>${record?.status === "pending_grade" ? "Chờ chấm" : submitted(record) ? "Đã làm" : record ? "Đang làm" : "Chưa làm"}</small></span></button>`;
      }).join("");
      const data = session.learningData();
      if (q()) {
        $("question-panel").innerHTML = questionHtml(data, q(), { attempt: attempt(), response: response(q()), busy: ui.busy, mode: state.mode });
        renderKnowledge(); renderFeedback();
      } else $("question-panel").innerHTML = '<p class="empty-state">Phiên bản này chưa có câu hỏi để làm.</p>';
      $("next-panel").innerHTML = nextHtml(data, core.recommendNext(data, state.attempts, { learner_id: state.identity?.learner_id }), ui.busy);
      renderProgress(core.computeEvidence(data, state.attempts, { learner_id: state.identity?.learner_id }));
    }
    $("identity-form").onsubmit = (event) => {
      event.preventDefault(); const name = $("learner-name").value;
      operation("Đang mở phiên học riêng…", async () => { await session.saveName(name); $("identity-panel").hidden = true; ui.catalog = !session.state.data; });
    };
    $("identity-toggle").onclick = () => { $("identity-panel").hidden = !$("identity-panel").hidden; $("learner-name").value = session.state.identity?.display_name || ""; if (!$("identity-panel").hidden) $("learner-name").focus(); };
    $("retry-operation").onclick = () => ui.retry?.();
    $("refresh-state").onclick = () => operation("Đang cập nhật kết quả đã lưu…", session.reload);
    $("courses-toggle").onclick = () => { ui.catalog = true; render(); $("catalog-panel").scrollIntoView?.({ behavior: "smooth", block: "start" }); };
    $("home-link").onclick = (event) => { event.preventDefault(); ui.catalog = true; render(); };
    doc.addEventListener("input", (event) => {
      const el = event.target, question = q();
      if (!question || submitted(attempt()) || !el.dataset?.response) return;
      const draft = response(question);
      if (el.dataset.response === "text") draft.text = el.value;
      if (el.dataset.response === "choice") {
        draft.selection_ids = question.interaction === "single_select" ? [el.value] :
          Array.from($("question-panel").querySelectorAll('[data-response="choice"]:checked')).map((input) => input.value);
      }
      if (el.dataset.response === "match") draft.mappings = Array.from($("question-panel").querySelectorAll('[data-response="match"]')).filter((input) => input.value).map((input) => ({ left: input.dataset.left, right: input.value }));
    });
    doc.addEventListener("click", (event) => {
      const button = event.target.closest?.("[data-action]");
      if (!button || ui.busy || button.disabled) return;
      const action = button.dataset.action;
      if (action === "select") return select(button.dataset.question);
      if (action === "course") return operation("Đang mở đúng phiên bản bài học…", async () => { await session.openCourse(button.dataset.course); ui.questionId = null; ui.knowledgeId = null; ui.catalog = false; });
      if (action === "refresh") return operation("Đang cập nhật kết quả…", session.reload);
      if (action === "pending") return operation("Đang xác nhận đúng thao tác đang chờ…", () => session.retryPending(button.dataset.key));
      if (action === "knowledge") { ui.knowledgeId = button.dataset.kc; renderKnowledge(); $("knowledge-panel").scrollIntoView?.({ behavior: "smooth", block: "center" }); return; }
      const question = q(); if (!question) return;
      if (action === "move") {
        const order = response(question).ordering, from = Number(button.dataset.index), to = from + Number(button.dataset.direction);
        if (from >= 0 && to >= 0 && from < order.length && to < order.length && !submitted(attempt())) { [order[from], order[to]] = [order[to], order[from]]; render(); } return;
      }
      if (action === "vote") { const draft = ui.feedback[draftKey(ui.questionId)]; draft.vote = button.dataset.vote; draft.open = true; draft.saved = false; renderFeedback(); return; }
      if (!requireIdentity()) return;
      const id = question.question_id;
      if (action === "hint") {
        // Capture the specific hint for this click. A retry after a confirmed
        // reload must re-request the same hint, never silently open the next one.
        const hintId = (question.hints || []).find((row) => !(attempt()?.hint_ids || []).includes(row.hint_id))?.hint_id;
        return operation("Đang ghi nhận gợi ý trước khi mở…", () => session.revealHint(id, hintId));
      }
      if (action === "submit") { const value = clone(response(question)); return operation("Đang lưu câu trả lời…", () => session.submit(id, value)); }
      if (action === "repeat") return operation("Đang mở lần luyện tập lại…", async () => { await session.start(id); delete ui.responses[draftKey(id)]; });
    });
    const ready = operation("Đang tải phiên học…", async () => { await session.init(); $("identity-panel").hidden = Boolean(session.state.identity); ui.catalog = !session.state.data; });
    return { session, ui, ready, render, select };
  }
  function formatTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("vi-VN", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }
  return { createSession, mount, validatePackage, previewPacket, questionHtml, controlsHtml, nextHtml, progressHtml, catalogView, evidenceNote, stateTag, answerText, emptyResponse, shuffled };
});
