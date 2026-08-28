(function (root) {
  "use strict";

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const noAccess = () => ({ can_teach: false, can_publish: false, can_grade: false });
  const labels = {
    no_evidence: "Chưa đo", needs_practice: "Cần ôn",
    demonstrated: "Đã có bằng chứng độc lập", assisted: "Đúng khi có hỗ trợ",
    developing: "Mới đo một phần", pending_grade: "Chờ chấm rubric",
  };
  const label = (core, state) => core.evidenceLabel?.(state) || labels[state] || "Chưa đủ thông tin";
  const reasonLabels = {
    after_assisted: "Đã dùng gợi ý. Cần một câu khác cho cùng mục tiêu để kiểm tra khả năng làm độc lập.",
    after_incorrect: "Lần đã chấm cho thấy còn thiếu. Ôn đúng mục tiêu rồi thử một câu khác.",
    unattempted_eligible_question: "Mục tiêu này chưa có đủ bằng chứng; câu chưa làm có thể bổ sung bằng chứng.",
    human_rubric_required: "Đang chờ giảng viên chấm theo rubric. Chưa kết luận học viên sai hoặc đã hiểu.",
    no_unattempted_eligible_question: "Chưa còn câu phù hợp chưa làm. Cần thêm bằng chứng hoặc nội dung đã được review, không lặp câu cũ để tăng mastery.",
    no_variant_for_target_slot: "Mục tiêu cần ôn chưa có biến thể phù hợp chưa làm. Cần thêm câu được review; câu thuộc mục tiêu khác không thay thế bằng chứng này.",
    mixed_learner_scope: "Lịch sử không cùng một người học; chưa tính hay đề xuất từ dữ liệu này.",
  };
  const exclusionLabels = {
    repeated_question: "Làm lại sau khi đã thấy đáp án",
    initial_check_not_pass: "Câu chưa đạt điều kiện kiểm định",
    content_review_changed: "Nội dung hoặc quyết định review đã thay đổi",
    not_graded: "Chưa chấm", pending_grade: "Chờ chấm rubric",
    stale_lineage: "Nguồn đã thay đổi", stale_question: "Câu đã đổi phiên bản",
    question_version_mismatch: "Không khớp phiên bản câu hỏi",
    source_version_mismatch: "Không khớp phiên bản nguồn",
    policy_version_mismatch: "Không khớp quy tắc bằng chứng",
    evidence_ineligible: "Không đủ điều kiện bằng chứng độc lập",
    invalid_grade: "Kết quả chấm không hợp lệ", invalid_hint_history: "Lịch sử hint không hợp lệ",
    grading_version_mismatch: "Không khớp phiên bản chấm",
  };
  const publicationReasons = {
    question_not_approved: "Cần duyệt câu hỏi hiện tại",
    kc_not_approved: "Cần duyệt KC hiện tại",
    assessment_mapping_changed_requires_new_authoring_run: "Liên kết câu hỏi–mục tiêu đã đổi; cần một run authoring mới",
    question_approval_precedes_kc_revision: "KC đã sửa sau lần duyệt câu; cần đối chiếu và duyệt lại câu",
    registered_baseline_changed: "Không khớp bản nguồn đã đăng ký",
    upstream_extraction_rejected: "Phần trích xuất nguồn đang bị từ chối",
    approval_precedes_extraction_revision: "Trích xuất đã sửa sau lần duyệt KC/câu; cần kiểm tra lại",
    source_reference_invalid: "Trích dẫn nguồn không hợp lệ; sửa lại tham chiếu trước khi phát hành",
  };

  function createSession({ config, storage, fetch: fetcher, crypto: cryptoApi }) {
    const enabled = Boolean(config?.enabled);
    let baseUrl = "", project = "local";
    if (enabled) {
      const url = new URL(config.supabaseUrl);
      if (!["https:", "http:"].includes(url.protocol) || !config.supabasePublishableKey || !config.runId)
        throw new Error("Thiếu cấu hình Teacher. Không tự chuyển sang quyền hoặc dữ liệu cục bộ.");
      baseUrl = url.href.replace(/\/$/, "");
      project = url.hostname;
    }
    const sessionKey = `la-teacher-session:${project}`;
    const nameKey = `la-teacher-name:${project}`;
    const pendingKey = `la-teacher-pending:${project}:${config?.runId || "local"}`;
    let auth = null;
    const state = { enabled, identity: null, access: noAccess(), workspace: null,
      draft: null, learner: null, queue: [], pending: { publication: null, grades: {} }, loaded: false };
    function read(key, fallback) {
      try {
        const value = storage.getItem(key);
        return value === null ? fallback : JSON.parse(value);
      } catch {
        throw new Error("Không đọc được phiên Teacher. Dữ liệu được giữ nguyên; chưa tự tạo danh tính khác.");
      }
    }
    function write(key, value) {
      try { storage.setItem(key, JSON.stringify(value)); }
      catch { throw new Error("Không lưu được phiên trên trình duyệt; chưa xác nhận thao tác thành công."); }
    }
    function clearPrivate() {
      state.access = noAccess(); state.workspace = null; state.draft = null;
      state.learner = null; state.queue = [];
    }
    function uuid() {
      if (!cryptoApi?.randomUUID) throw new Error("Cần HTTPS hoặc localhost để tạo mã thao tác an toàn.");
      return cryptoApi.randomUUID();
    }
    async function raw(path, { body, token, method = "POST", prefer } = {}) {
      const headers = { apikey: config.supabasePublishableKey, "Content-Type": "application/json" };
      if (token) headers.Authorization = `Bearer ${token}`;
      if (prefer) headers.Prefer = prefer;
      const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timer = controller ? setTimeout(() => controller.abort(), 20000) : null;
      try {
        const response = await fetcher(baseUrl + path, { method, headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          ...(controller ? { signal: controller.signal } : {}) });
        const text = response.status === 204 ? "" : await response.text();
        let payload;
        try { payload = text ? JSON.parse(text) : null; }
        catch { throw new Error("Phản hồi máy chủ không đọc được; cập nhật trước khi thử lại."); }
        if (!response.ok) {
          const error = new Error(payload?.message || payload?.error_description || `Máy chủ trả lỗi ${response.status}.`);
          error.status = response.status;
          throw error;
        }
        return payload;
      } catch (error) {
        if (error.name === "AbortError") throw new Error("Kết nối quá lâu. Trạng thái lưu chưa rõ; Cập nhật trước khi thử lại với cùng mã thao tác.");
        throw error;
      } finally { if (timer !== null) clearTimeout(timer); }
    }
    function saveAuth(payload) {
      const fresh = payload?.session || payload;
      if (fresh) fresh.user ||= payload?.user;
      if (!fresh?.access_token || !fresh.refresh_token || !fresh.user?.id)
        throw new Error("Máy chủ chưa trả về phiên tài khoản hợp lệ.");
      fresh.expires_at ||= Math.floor(Date.now() / 1000) + Number(fresh.expires_in || 3600);
      write(sessionKey, fresh); auth = fresh;
      return fresh;
    }
    async function refreshAuth() {
      try { return saveAuth(await raw("/auth/v1/token?grant_type=refresh_token", { body: { refresh_token: auth.refresh_token } })); }
      catch { throw new Error("Chưa làm mới được phiên Teacher. Đã giữ nguyên danh tính; không tự tạo tài khoản hay mất quyền đang có."); }
    }
    async function ensureAuth(allowCreate = false) {
      // Re-read to share one identity with same-origin review iframes after a refresh.
      auth = read(sessionKey, auth);
      if (auth?.access_token && Number(auth.expires_at) > Date.now() / 1000 + 60) return auth;
      if (auth?.refresh_token) return refreshAuth();
      if (!allowCreate || state.identity) throw new Error("Chưa có phiên Teacher hợp lệ. Nhập tên để tạo danh tính, rồi quản trị viên cấp quyền riêng.");
      return saveAuth(await raw("/auth/v1/signup", { body: { data: { application: "learning-teacher" } } }));
    }
    async function request(path, body, prefer) {
      const session = await ensureAuth();
      try { return await raw(path, { body, prefer, token: session.access_token }); }
      catch (error) {
        if (error.status !== 401 || !auth?.refresh_token) throw error;
        const fresh = await refreshAuth();
        return raw(path, { body, prefer, token: fresh.access_token });
      }
    }
    const rpc = (name, body) => request(`/rest/v1/rpc/${name}`, body);
    function requireRole(key = "can_teach") {
      if (!enabled || !state.access[key]) throw new Error("Tài khoản hiện tại không được cấp quyền thao tác này cho bài học. Tên không tạo quyền.");
    }
    function savePending(next) { write(pendingKey, next); state.pending = next; }
    async function init() {
      if (!enabled) { state.loaded = true; return state; }
      auth = read(sessionKey, null);
      if (!auth) {
        // Upgrade the existing reviewer session, not an unrelated student identity.
        const previous = read(`la-review-session:${project}`, null);
        if (previous) { write(sessionKey, previous); auth = previous; }
      }
      state.pending = read(pendingKey, { publication: null, grades: {} });
      if (!state.pending || typeof state.pending.grades !== "object") throw new Error("Mã thao tác đang chờ chưa đọc được; không tự ghi đè.");
      if (auth) {
        let name = read(nameKey, "");
        if (!name) name = storage.getItem(`la-review-name:${project}`) || "";
        state.identity = { user_id: auth.user?.id || "", display_name: String(name) };
        return reload();
      }
      state.loaded = true;
      return state;
    }
    async function saveName(value) {
      if (!enabled) throw new Error("Teacher dùng chung cần backend phân quyền; chưa cấu hình lưu hay phát hành.");
      const name = String(value || "").trim();
      if (!name || name.length > 80) throw new Error("Tên hiển thị cần từ 1 đến 80 ký tự.");
      const session = await ensureAuth(true);
      await request("/rest/v1/reviewer_profiles?on_conflict=user_id", { user_id: session.user.id, display_name: name }, "resolution=merge-duplicates,return=minimal");
      write(nameKey, name);
      state.identity = { user_id: session.user.id, display_name: name };
      return reload();
    }
    async function reload() {
      clearPrivate();
      if (!state.identity) { state.loaded = true; return state; }
      try {
        state.identity.display_name = read(nameKey, state.identity.display_name);
        const access = await rpc("get_teacher_access", { p_run_id: config.runId });
        if (!access || typeof access.can_teach !== "boolean" || !access.user_id)
          throw new Error("Chưa xác minh được quyền Teacher từ máy chủ; không mở dữ liệu học viên.");
        state.identity.user_id = access.user_id;
        if (!access.can_teach) { state.loaded = true; return state; }
        const [workspace, draft] = await Promise.all([
          rpc("get_teacher_workspace", { p_run_id: config.runId }),
          rpc("get_teacher_learning_package", { p_run_id: config.runId }),
        ]);
        if (!workspace?.can_teach || !Array.isArray(workspace.releases) || !Array.isArray(workspace.learners) ||
            !Array.isArray(workspace.question_reviews) || !workspace.review_version ||
            draft?.schema_version !== "learning-package.v1")
          throw new Error("Không gian Teacher chưa đúng contract; không thay bằng dữ liệu mẫu.");
        state.access = { ...access, can_publish: access.can_publish === true && workspace.can_publish === true,
          can_grade: access.can_grade === true && workspace.can_grade === true };
        state.workspace = workspace; state.draft = draft;
        const pending = state.pending.publication;
        if (pending && workspace.releases.some((release) => release.publish_event_id === pending.event_id))
          savePending({ ...state.pending, publication: null });
        state.loaded = true;
        return state;
      } catch (error) { clearPrivate(); throw error; }
    }
    async function loadLearner(learnerId, releaseId) {
      requireRole(); state.learner = null;
      if (!state.workspace.learners.some((learner) => learner.learner_id === learnerId && learner.release_id === releaseId))
        throw new Error("Người học không thuộc phiên bản đang được phép xem.");
      const value = await rpc("get_teacher_learner_state", {
        p_run_id: config.runId, p_learner_id: learnerId, p_release_id: releaseId,
      });
      if (!value || !Array.isArray(value.attempts) || !Array.isArray(value.feedback) ||
          value.learning_package?.schema_version !== "learning-package.v1" ||
          value.learning_package.run_id !== releaseId || value.release_id !== releaseId ||
          value.learner?.learner_id !== learnerId ||
          value.attempts.some((attempt) => attempt.learner_id !== learnerId || attempt.run_id !== releaseId))
        throw new Error("Lịch sử chưa khớp đúng học viên và phiên bản; không tổng hợp lẫn dữ liệu.");
      state.learner = value;
      const pending = copy(state.pending);
      for (const attempt of value.attempts) {
        const grade = pending.grades[attempt.attempt_id];
        if (grade && attempt.status === "graded" &&
            JSON.stringify(attempt.rubric_scores) === JSON.stringify(grade.body.p_scores) &&
            (attempt.grading_note || null) === grade.body.p_note) delete pending.grades[attempt.attempt_id];
      }
      if (JSON.stringify(pending) !== JSON.stringify(state.pending)) savePending(pending);
      return value;
    }
    async function loadQueue(releaseId) {
      requireRole("can_grade"); state.queue = [];
      if (!state.workspace.releases.some((release) => release.release_id === releaseId))
        throw new Error("Chọn một phiên bản đã phát hành để chấm.");
      const queue = await rpc("get_learning_grading_queue", { p_run_id: releaseId });
      if (!Array.isArray(queue) || queue.some((row) => row.run_id !== releaseId ||
          typeof row.learner_id !== "string" || row.learner_id === state.identity.user_id ||
          row.question_payload?.interaction !== "short_text" || !Array.isArray(row.question_payload.rubric)))
        throw new Error("Hàng chờ chưa khớp phiên bản hoặc phạm vi được chấm.");
      state.queue = queue;
      return queue;
    }
    async function publish(questionIds, releaseLabel) {
      requireRole("can_publish");
      const ids = [...new Set(questionIds)].sort(), name = String(releaseLabel || "").trim();
      if (!name || name.length > 120) throw new Error("Nhập tên phiên bản từ 1 đến 120 ký tự.");
      if (!ids.length || ids.some((id) => !state.workspace.question_reviews.some((row) => row.question_id === id && row.publishable === true)))
        throw new Error("Chỉ phát hành các câu được chọn rõ và đã đủ điều kiện duyệt hiện tại.");
      const body = { p_run_id: config.runId, p_label: name, p_expected_review_version: state.workspace.review_version, p_question_ids: ids };
      let pending = state.pending.publication;
      if (pending && JSON.stringify(pending.body) !== JSON.stringify(body))
        throw new Error("Lần phát hành trước chưa rõ đã lưu hay chưa. Bấm Xác nhận lần phát hành đang chờ trước khi tạo thao tác khác.");
      if (!pending) { pending = { body, event_id: uuid() }; savePending({ ...state.pending, publication: pending }); }
      return commitPublication(pending);
    }
    async function retryPublication() {
      requireRole("can_publish");
      if (!state.pending.publication) throw new Error("Không còn lần phát hành đang chờ xác nhận.");
      // Retry the saved request, not a new request rebased onto changed reviews.
      return commitPublication(state.pending.publication);
    }
    async function commitPublication(pending) {
      try {
        const release = await rpc("publish_reviewed_release", { ...pending.body, p_event_id: pending.event_id });
        if (!release?.release_id) throw new Error("Chưa xác nhận phiên bản đã phát hành; cập nhật hoặc thử lại với cùng mã.");
        savePending({ ...state.pending, publication: null });
        await reload();
        return release;
      } catch (error) {
        if (error.status >= 400 && error.status < 500) savePending({ ...state.pending, publication: null });
        throw error;
      }
    }
    async function grade(attemptId, scores, note) {
      requireRole("can_grade");
      const row = state.queue.find((item) => item.attempt_id === attemptId);
      const rubric = row?.question_payload?.rubric;
      if (!row || !Array.isArray(rubric) || !Array.isArray(scores) || scores.length !== rubric.length ||
          scores.some((score, i) => !Number.isFinite(score) || score < 0 || score > rubric[i].points))
        throw new Error("Nhập điểm hợp lệ cho từng tiêu chí rubric đã đóng băng.");
      const cleanNote = String(note || "").trim();
      if (cleanNote.length > 2000) throw new Error("Nhận xét tối đa 2.000 ký tự.");
      const body = { p_attempt_id: attemptId, p_scores: scores, p_note: cleanNote || null };
      let pending = state.pending.grades[attemptId];
      if (pending && JSON.stringify(pending.body) !== JSON.stringify(body))
        throw new Error("Lần lưu điểm trước chưa rõ kết quả. Cập nhật trước khi đổi điểm hay nhận xét.");
      if (!pending) { pending = { body, event_id: uuid() }; savePending({ ...state.pending, grades: { ...state.pending.grades, [attemptId]: pending } }); }
      const result = await rpc("grade_learning_attempt", { ...pending.body, p_event_id: pending.event_id });
      if (result?.status !== "graded" || result.attempt_id !== attemptId) throw new Error("Chưa xác nhận kết quả chấm đã lưu.");
      const next = copy(state.pending); delete next.grades[attemptId]; savePending(next);
      state.queue = state.queue.filter((item) => item.attempt_id !== attemptId);
      return result;
    }
    return { state, init, reload, saveName, loadLearner, loadQueue, publish, retryPublication, grade };
  }

  function selectionSummary(draft, reviews, selectedIds) {
    const selected = new Set(selectedIds), rows = reviews.filter((row) => selected.has(row.question_id));
    const questions = (draft?.questions || []).filter((question) => selected.has(question.question_id));
    const kcIds = new Set(questions.map((question) => question.kc_id));
    const slotIds = new Set(questions.map((question) => question.slot_id).filter(Boolean));
    return { selected: rows.length, blocked: reviews.filter((row) => !row.publishable).length,
      omitted: reviews.length - rows.length, covered_kcs: kcIds.size,
      uncovered_kcs: (draft?.kcs || []).filter((kc) => !kcIds.has(kc.kc_id)).length,
      uncovered_slots: (draft?.slots || []).filter((slot) => !slotIds.has(slot.slot_id)).length };
  }
  function time(value) {
    if (!value) return "Chưa có";
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? "Chưa rõ thời điểm" : parsed.toLocaleString("vi-VN");
  }
  function learnerPackage(value) {
    const data = copy(value.learning_package);
    for (const [id, meta] of Object.entries(data.question_meta)) {
      const status = (value.item_quality || {})[id];
      meta.quality_status = !status ? "UNCHECKED" : status.question_sha256 !== meta.question_sha256 ? "STALE" : status.quality_status || "UNCHECKED";
    }
    return data;
  }
  function badge(state, text) {
    const color = state === "demonstrated" ? "green" : ["needs_practice", "assisted"].includes(state) ? "amber" : "";
    return `<span class="badge ${color}">${esc(text)}</span>`;
  }
  function answerText(question, response) {
    const options = [...(question?.choice_options || []), ...(question?.matching_left || []),
      ...(question?.matching_right || []), ...(question?.ordering_options || [])];
    const text = (id) => options.find((option) => option.option_id === id)?.text || id;
    if (response?.selection_ids?.length) return response.selection_ids.map(text).join("\n");
    if (response?.ordering?.length) return response.ordering.map(text).join(" → ");
    if (response?.mappings?.length) return response.mappings.map((pair) => `${text(pair.left)} → ${text(pair.right)}`).join("\n");
    return response?.text || "Chưa nộp câu trả lời";
  }
  function stimulusHtml(stimulus) {
    if (!stimulus || stimulus.kind === "none") return "";
    let contents = stimulus.text ? `<p>${esc(stimulus.text)}</p>` : "";
    if (stimulus.table_columns?.length) contents += `<div class="table-scroll"><table><thead><tr>${stimulus.table_columns.map((cell) => `<th>${esc(cell)}</th>`).join("")}</tr></thead><tbody>${(stimulus.table_rows || []).map((row) => `<tr>${(Array.isArray(row) ? row : row.cells || []).map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    if (stimulus.formula) contents += `<p>${esc(stimulus.formula)}</p>`;
    return `<div class="stimulus">${contents}</div>`;
  }
  function learnerHtml(value, core) {
    const data = learnerPackage(value), attempts = value.attempts;
    const evidence = core.computeEvidence(data, attempts), next = core.recommendNext(data, attempts);
    const name = value.learner?.display_name || "Học viên";
    const kcFor = (id) => data.kcs.find((kc) => kc.kc_id === id);
    const nextKc = kcFor(next.kc_id || next.review?.kc_id);
    const nextQ = data.questions.find((question) => question.question_id === next.question_id);
    const reason = reasonLabels[next.reason] || "Chưa đủ thông tin để đề xuất thêm; cần đối chiếu bằng chứng của mục tiêu này.";
    return `<section class="card"><h2>${esc(name)}</h2><p class="muted small">Phiên bản ${esc(value.release_id)} · ${attempts.length} lượt được lưu · không dùng điểm tổng để xếp mastery</p>
      <div class="next-action"><h3>Hành động tiếp theo có căn cứ</h3><p>${esc(reason)}</p>${nextKc ? `<p><strong>${esc(nextKc.name)}</strong></p>` : ""}${nextQ ? `<p>Câu được đề xuất: ${esc(nextQ.title)}</p>` : ""}${next.alternative ? `<p class="muted">Có thể tiếp tục mục tiêu khác trong khi chờ; mục tiêu này vẫn chưa có bằng chứng thay thế.</p>` : ""}</div>
      <div class="evidence-grid">${evidence.kcs.map((kc) => {
        const content = kcFor(kc.kc_id);
        return `<article class="evidence-item"><h3>${esc(content?.name || kc.kc_id)}</h3>${badge(kc.state, label(core, kc.state))}<p>${kc.independent_slots}/${kc.total_slots} mục tiêu có bằng chứng độc lập${kc.assisted_slots ? ` · ${kc.assisted_slots} có hỗ trợ` : ""}${kc.pending_slots ? ` · ${kc.pending_slots} chờ chấm` : ""}</p><details><summary>Dựa trên những mục tiêu nào?</summary>${kc.slots.map((slot) => {
          const definition = data.slots.find((row) => row.slot_id === slot.slot_id);
          return `<div class="slot-row"><strong>${esc(definition?.evidence_intent || definition?.learner_evidence || slot.slot_id)}</strong><span>${esc(label(core, slot.state))}${slot.question_id ? ` · ${esc(slot.question_id)}` : ""}</span></div>`;
        }).join("") || '<p>Chưa có mục tiêu đánh giá; không suy ra đã hiểu.</p>'}</details></article>`;
      }).join("")}</div></section>
      <section class="card"><h2>Lần làm → chấm → bằng chứng</h2><p class="muted small">Giữ nguyên cả lượt sai, hint và lượt làm lại. Chờ chấm không được coi là sai.</p><div class="attempt-history">${attempts.slice().sort((a, b) => String(b.started_at).localeCompare(String(a.started_at))).map((attempt) => {
        const question = attempt.question_payload || data.questions.find((item) => item.question_id === attempt.question_id);
        const excluded = evidence.excluded_attempts.find((row) => row.attempt_id === attempt.attempt_id);
        const status = attempt.status === "pending_grade" ? "Chờ chấm rubric — chưa kết luận" : attempt.status === "in_progress" ? "Đang làm — chưa kết luận" : attempt.correct ? "Đạt tiêu chí của câu" : "Chưa đạt đủ tiêu chí";
        const hints = (attempt.hint_ids || []).map((id) => question?.hints?.find((hint) => hint.hint_id === id)?.text || id);
        return `<article class="attempt-row"><div class="attempt-heading"><strong>${esc(question?.title || attempt.question_id)}</strong><time>${esc(time(attempt.submitted_at || attempt.started_at))}</time></div><p>${esc(status)}${attempt.status === "graded" ? ` · ${esc(attempt.score)}/${esc(attempt.max_score)} điểm rubric/câu` : ""} · ${hints.length} gợi ý${attempt.is_repeat ? " · Làm lại" : ""}</p>${excluded ? `<p>Không dùng như bằng chứng độc lập: ${excluded.reasons.map((item) => esc(exclusionLabels[item] || "Điều kiện bằng chứng chưa được đáp ứng")).join("; ")}</p>` : ""}<details><summary>Xem bài làm và nhận xét</summary><p class="response-text">${esc(answerText(question, attempt.response))}</p>${hints.length ? `<p>Gợi ý đã dùng:</p><ul>${hints.map((hint) => `<li>${esc(hint)}</li>`).join("")}</ul>` : ""}${Array.isArray(attempt.rubric_scores) ? `<ol class="rubric-result">${attempt.rubric_scores.map((score, index) => `<li>${esc(question?.rubric?.[index]?.criterion || `Tiêu chí ${index + 1}`)}: ${esc(score)}/${esc(question?.rubric?.[index]?.points)}</li>`).join("")}</ol>` : ""}${attempt.grading_note ? `<p>Nhận xét giảng viên:</p><p class="response-text">${esc(attempt.grading_note)}</p>` : ""}<p>Phiên bản chấm: ${esc(attempt.grading_version || "Chưa chấm")}</p></details></article>`;
      }).join("") || '<p class="empty-state">Chưa có lượt làm. Không tạo dữ liệu mẫu hay kết luận năng lực.</p>'}</div></section>
      <section class="card"><h2>Góp ý về nội dung</h2><p class="muted small">Dữ liệu cho vòng cải thiện hệ thống; không tự thay đổi điểm hay mastery của học viên.</p>${value.feedback.map((event) => `<article class="feedback-row"><strong>${event.payload?.vote === "like" ? "Hữu ích" : "Cần cải thiện"} · ${esc(event.question_id)}</strong><p>${esc(event.payload?.note || "Không có ghi chú")}</p></article>`).join("") || '<p class="empty-state">Chưa có góp ý.</p>'}</section>`;
  }
  function gradingHtml(queue, drafts, busy) {
    return queue.map((row) => {
      const question = row.question_payload;
      const draft = (drafts[row.attempt_id] ||= { scores: question.rubric.map(() => ""), note: "" });
      return `<form class="grading-card" data-grade="${esc(row.attempt_id)}"><h3>${esc(question.title)}</h3><p class="muted small">${esc(row.learner_name || "Học viên")} · ${esc(row.question_id)} · ${esc(row.run_id)}</p>${stimulusHtml(question.stimulus)}<p class="prompt">${esc(question.prompt)}</p><p class="response-text">${esc(row.response?.text || "")}</p><p class="muted small">Đã dùng ${(row.hint_ids || []).length} gợi ý${row.is_repeat ? " · Bài làm lại" : ""}</p><details><summary>Đáp án tham khảo và gợi ý đã dùng</summary><p class="response-text">${esc(question.correct_answer?.text || "")}</p>${(row.hint_ids || []).map((id) => `<p>${esc(question.hints?.find((hint) => hint.hint_id === id)?.text || id)}</p>`).join("")}</details>${question.rubric.map((criterion, index) => `<label class="rubric-input"><span>${esc(criterion.criterion)} <small>(tối đa ${esc(criterion.points)})</small></span><input data-score="${index}" aria-label="Điểm tiêu chí ${index + 1}" type="number" min="0" max="${esc(criterion.points)}" step="any" required value="${esc(draft.scores[index])}" /></label>`).join("")}<label for="teacher-note-${esc(row.attempt_id)}">Nhận xét gửi học viên</label><textarea id="teacher-note-${esc(row.attempt_id)}" data-grade-note maxlength="2000">${esc(draft.note)}</textarea><button type="submit" class="primary" ${busy ? "disabled" : ""}>Lưu kết quả chấm</button></form>`;
    }).join("") || '<p class="card empty-state">Không có bài đang chờ bạn chấm trong phiên bản này.</p>';
  }
  function localReviewPath(path) {
    if (typeof path !== "string" || path.includes("..") || !/^[a-zA-Z0-9_-][a-zA-Z0-9_./-]*\.html$/.test(path)) return null;
    return path;
  }

  function mount({ document, config, data, core, storage, fetch: fetcher, crypto: cryptoApi, clipboard }) {
    const $ = (id) => document.getElementById(id);
    const session = createSession({ config, storage, fetch: fetcher, crypto: cryptoApi });
    const ui = { tab: "content", review: "extraction", selected: new Set(), releaseId: "", gradingRelease: "",
      learnerId: "", busy: false, identityOpen: false, drafts: {} };
    async function operation(message, fn) {
      if (ui.busy) return;
      ui.busy = true; $("teacher-error").hidden = true; $("teacher-status").textContent = message; render();
      try { await fn(); $("teacher-status").textContent = "Đã cập nhật từ máy chủ. Quyền và phiên bản được kiểm tra cho từng thao tác."; }
      catch (error) { $("teacher-error-message").textContent = error.message; $("teacher-error").hidden = false; $("teacher-status").textContent = "Chưa xác nhận thao tác hoàn tất. Dữ liệu cũ được giữ nguyên."; }
      finally { ui.busy = false; render(); }
    }
    function renderReleaseSelect(id, selected) {
      const releases = session.state.workspace?.releases || [];
      $(id).innerHTML = releases.map((release) => `<option value="${esc(release.release_id)}">${esc(release.label || release.release_id)} · ${release.question_count} câu</option>`).join("") || '<option value="">Chưa phát hành phiên bản</option>';
      $(id).value = selected;
      $(id).disabled = ui.busy || !releases.length;
    }
    function renderReviewFrame() {
      document.querySelectorAll("[data-review]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.review === ui.review)));
      const paths = { extraction: "extraction-review.html", kc: "kc-recall.html", quiz: "quiz-review.html", ...config?.reviewViews };
      const path = localReviewPath(paths[ui.review]);
      const frame = $("teacher-review-frame");
      if (path && frame.getAttribute("src") !== path) { frame.setAttribute("src", path); frame.title = `Review ${ui.review}`; }
    }
    function render() {
      const state = session.state, workspace = state.workspace;
      const localPreview = !state.enabled && config?.mode === "local_preview";
      $("teacher-course-name").textContent = workspace?.title || data?.source?.filename || "";
      $("teacher-run-label").textContent = config?.runId ? `Nguồn: ${config.runId}` : "Chưa cấu hình bài học";
      $("teacher-person").textContent = state.identity?.display_name || "Phiên giảng viên";
      $("teacher-identity").hidden = state.access.can_teach && !ui.identityOpen;
      $("teacher-account").hidden = !state.identity?.user_id;
      $("teacher-account-id").value = state.identity?.user_id || "";
      $("teacher-refresh").hidden = !state.identity;
      $("teacher-refresh").disabled = ui.busy;
      $("teacher-no-access").hidden = !state.identity || state.access.can_teach;
      $("teacher-app").hidden = !state.access.can_teach;
      if (!state.enabled) $("teacher-status").textContent = "Chưa cấu hình backend phân quyền. Không thể sửa, phát hành hoặc xem dữ liệu học viên.";
      else if (!state.identity && !ui.busy) $("teacher-status").textContent = "Nhập tên để tạo phiên. Quyền giảng viên của bài học phải được quản trị viên cấp riêng; chưa tải dữ liệu riêng từ máy chủ.";
      if (localPreview) {
        $("teacher-identity").hidden = true;
        $("teacher-person").hidden = true;
        $("teacher-app").hidden = false;
        $("teacher-content-panel").hidden = false;
        $("teacher-learners-panel").hidden = true;
        $("teacher-grading-panel").hidden = true;
        $("teacher-status").textContent = "Bản xem trước trên máy · Chỉ đọc nội dung. Chưa cấu hình phân quyền, phát hành hay dữ liệu học viên dùng chung.";
        document.querySelectorAll("[data-tab]").forEach((button) => { button.disabled = button.dataset.tab !== "content"; });
        renderReviewFrame();
        $("teacher-question-selection").innerHTML = '<p class="empty-state">Đây là bản xem trước chỉ đọc. Cần backend và quyền giảng viên để review, chọn câu và phát hành.</p>';
        $("teacher-release-list").innerHTML = '<p class="empty-state">Chưa kết nối dữ liệu phiên bản. Không tạo phiên bản mẫu.</p>';
        for (const id of ["teacher-publish", "teacher-select-publishable", "teacher-clear-selection", "teacher-refresh-reviews", "teacher-release-label"]) $(id).disabled = true;
        return;
      }
      if (!state.access.can_teach) { $("teacher-review-frame").removeAttribute("src"); return; }
      document.querySelectorAll("[data-tab]").forEach((button) => {
        button.setAttribute("aria-selected", String(button.dataset.tab === ui.tab)); button.disabled = ui.busy;
      });
      for (const name of ["content", "learners", "grading"]) $("teacher-" + name + "-panel").hidden = ui.tab !== name;
      renderReviewFrame();
      const reviews = workspace.question_reviews;
      const selectable = new Set(reviews.filter((row) => row.publishable).map((row) => row.question_id));
      ui.selected = new Set([...ui.selected].filter((id) => selectable.has(id)));
      const stats = selectionSummary(state.draft, reviews, ui.selected);
      $("teacher-publish-summary").innerHTML = `<span class="badge green">${stats.selected} câu được chọn</span><span class="badge">${stats.covered_kcs} KC có câu</span><span class="badge">${stats.omitted} câu chưa chọn</span><span class="badge amber">${stats.blocked} câu bị chặn</span><span class="badge amber">${stats.uncovered_slots} mục tiêu chưa có câu trong bản này</span><span class="badge">${stats.uncovered_kcs} KC chưa có câu</span>`;
      $("teacher-question-selection").innerHTML = reviews.map((row) => {
        const check = state.draft.question_meta?.[row.question_id]?.initial_check_status || "Chưa kiểm định";
        const reasons = Array.isArray(row.reasons) ? row.reasons : String(row.reason || "").split(", ").filter(Boolean);
        const reason = reasons.map((code) => publicationReasons[code] || code).join("; ");
        return `<label class="question-option ${row.publishable ? "" : "blocked"}"><input type="checkbox" data-publish-question="${esc(row.question_id)}" ${ui.selected.has(row.question_id) ? "checked" : ""} ${!row.publishable || ui.busy ? "disabled" : ""} /><span>${esc(row.title || row.question_id)}<small>${esc(row.question_id)} · ${esc(row.kc_id)} · AI ban đầu: ${esc(check)} · ${row.question_approved ? "Câu đã duyệt" : "Câu chưa duyệt"} · ${row.kc_approved ? "KC đã duyệt" : "KC chưa duyệt"}${reason ? ` · ${esc(reason)}` : ""}</small></span><span class="badge ${row.publishable ? "green" : "amber"}">${row.publishable ? "Có thể chọn" : "Chưa được phát hành"}</span></label>`;
      }).join("") || '<p class="empty-state">Chưa có câu hỏi được đăng ký cho bài học này.</p>';
      $("teacher-publish").disabled = ui.busy || !state.access.can_publish || !stats.selected || Boolean(state.pending.publication);
      $("teacher-pending-publication").hidden = !state.pending.publication;
      $("teacher-retry-publication").disabled = ui.busy || !state.access.can_publish;
      $("teacher-select-publishable").disabled = ui.busy || !selectable.size;
      $("teacher-clear-selection").disabled = ui.busy || !ui.selected.size;
      $("teacher-release-list").innerHTML = workspace.releases.map((release) => `<article class="release-row"><div><strong>${esc(release.label || release.release_id)}</strong><p>${esc(release.release_id)} · ${release.question_count} câu · ${release.kc_count} KC</p></div><time>${esc(time(release.published_at || release.created_at))}</time></article>`).join("") || '<p class="empty-state">Chưa có bản được phát hành. Bản draft không tự thành bài test của học viên.</p>';
      if (!workspace.releases.some((release) => release.release_id === ui.releaseId)) ui.releaseId = workspace.releases[0]?.release_id || "";
      if (!workspace.releases.some((release) => release.release_id === ui.gradingRelease)) ui.gradingRelease = workspace.releases[0]?.release_id || "";
      renderReleaseSelect("teacher-learner-release", ui.releaseId); renderReleaseSelect("teacher-grading-release", ui.gradingRelease);
      const learners = workspace.learners.filter((learner) => learner.release_id === ui.releaseId);
      $("teacher-learner-list").innerHTML = learners.map((learner) => `<button type="button" class="learner-button" data-learner="${esc(learner.learner_id)}" aria-current="${learner.learner_id === ui.learnerId}" ${ui.busy ? "disabled" : ""}><strong>${esc(learner.display_name || "Học viên")}</strong><span>${learner.attempt_count} lượt · ${learner.pending_count} chờ chấm</span><span>${esc(time(learner.last_activity))}</span></button>`).join("") || '<p class="empty-state">Chưa có lượt học trong phiên bản này.</p>';
      $("teacher-learner-detail").innerHTML = state.learner ? learnerHtml(state.learner, core) : '<div class="card empty-state">Chọn học viên để xem mục tiêu, bài làm, hint, kết quả chấm và hành động tiếp theo. Không hiển thị dữ liệu ngoài phạm vi được cấp quyền.</div>';
      $("teacher-queue-count").textContent = state.queue.length ? `(${state.queue.length})` : "";
      $("teacher-grading-queue").innerHTML = state.access.can_grade ? gradingHtml(state.queue, ui.drafts, ui.busy) : '<p class="card empty-state">Tài khoản chưa được cấp quyền chấm.</p>';
      document.querySelectorAll("[data-grade]").forEach((form) => {
        const id = form.dataset.grade, draft = ui.drafts[id];
        form.querySelectorAll("[data-score]").forEach((input) => { input.oninput = () => { draft.scores[Number(input.dataset.score)] = input.value; }; });
        form.querySelector("[data-grade-note]").oninput = (event) => { draft.note = event.target.value; };
        form.onsubmit = (event) => { event.preventDefault(); return operation("Đang lưu kết quả rubric…", async () => {
          await session.grade(id, draft.scores.map((score) => score === "" ? NaN : Number(score)), draft.note);
          await session.reload();
          await session.loadQueue(ui.gradingRelease);
          if (ui.learnerId && ui.releaseId) await session.loadLearner(ui.learnerId, ui.releaseId);
        }); };
      });
    }
    $("teacher-identity-form").onsubmit = (event) => { event.preventDefault(); return operation("Đang xác minh danh tính và quyền bài học…", () => session.saveName($("teacher-name").value)); };
    $("teacher-person").onclick = () => { ui.identityOpen = !ui.identityOpen; render(); };
    $("teacher-copy-account").onclick = async () => {
      try { await clipboard.writeText(session.state.identity?.user_id || ""); $("teacher-status").textContent = "Đã sao chép mã tài khoản, không sao chép khóa phiên."; }
      catch { $("teacher-account-id").focus(); $("teacher-account-id").select(); $("teacher-status").textContent = "Chọn mã ở trên và sao chép thủ công."; }
    };
    const reload = () => operation("Đang đối chiếu quyền, review và phiên bản…", async () => {
      await session.reload();
      if (ui.tab === "grading" && ui.gradingRelease && session.state.access.can_grade) await session.loadQueue(ui.gradingRelease);
      if (ui.learnerId && ui.releaseId && session.state.access.can_teach) await session.loadLearner(ui.learnerId, ui.releaseId);
    });
    $("teacher-refresh").onclick = reload; $("teacher-refresh-reviews").onclick = reload;
    document.querySelectorAll("[data-tab]").forEach((button) => { button.onclick = () => { if (ui.busy) return; ui.tab = button.dataset.tab; render(); if (ui.tab === "grading" && ui.gradingRelease) return operation("Đang đọc bài chờ chấm…", () => session.loadQueue(ui.gradingRelease)); }; });
    document.querySelectorAll("[data-review]").forEach((button) => { button.onclick = () => { ui.review = button.dataset.review; render(); }; });
    $("teacher-question-selection").onchange = (event) => { const id = event.target.dataset.publishQuestion; if (!id) return; if (event.target.checked) ui.selected.add(id); else ui.selected.delete(id); render(); };
    $("teacher-select-publishable").onclick = () => { ui.selected = new Set(session.state.workspace.question_reviews.filter((row) => row.publishable).map((row) => row.question_id)); render(); };
    $("teacher-clear-selection").onclick = () => { ui.selected.clear(); render(); };
    $("teacher-publish-form").onsubmit = (event) => { event.preventDefault(); const ids = [...ui.selected], name = $("teacher-release-label").value; return operation("Đang phát hành đúng các câu đã chọn…", async () => { const release = await session.publish(ids, name); ui.selected.clear(); $("teacher-release-label").value = ""; ui.releaseId = release.release_id; ui.gradingRelease = release.release_id; }); };
    $("teacher-retry-publication").onclick = () => operation("Đang xác nhận đúng lần phát hành trước…", async () => {
      const release = await session.retryPublication(); ui.releaseId = release.release_id; ui.gradingRelease = release.release_id; ui.selected.clear();
    });
    $("teacher-learner-release").onchange = (event) => { ui.releaseId = event.target.value; ui.learnerId = ""; session.state.learner = null; render(); };
    $("teacher-learner-list").onclick = (event) => { if (ui.busy) return; const button = event.target.closest("[data-learner]"); if (!button) return; ui.learnerId = button.dataset.learner; session.state.learner = null; return operation("Đang mở bằng chứng của học viên…", () => session.loadLearner(ui.learnerId, ui.releaseId)); };
    $("teacher-grading-release").onchange = (event) => { if (ui.busy) return; ui.gradingRelease = event.target.value; session.state.queue = []; return operation("Đang đọc hàng chờ của phiên bản…", () => session.loadQueue(ui.gradingRelease)); };
    $("teacher-refresh-grading").onclick = () => operation("Đang đọc bài chờ chấm…", () => session.loadQueue(ui.gradingRelease));
    const ready = operation("Đang xác minh phiên Teacher…", () => session.init());
    return { session, ui, ready, render };
  }

  const api = { createSession, selectionSummary, learnerPackage, learnerHtml, gradingHtml, localReviewPath, answerText, mount };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else {
    root.LearningTeacher = api;
    const run = () => {
      try { mount({ document: root.document, config: root.LEARNING_AUTHORING_REVIEW,
        data: root.LEARNING_DATA, core: root.LearningCore, storage: root.localStorage,
        fetch: root.fetch.bind(root), crypto: root.crypto, clipboard: root.navigator.clipboard }); }
      catch (error) {
        const panel = root.document.getElementById("teacher-error"), text = root.document.getElementById("teacher-error-message");
        if (panel && text) { panel.hidden = false; text.textContent = error.message; }
      }
    };
    if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", run);
    else run();
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
