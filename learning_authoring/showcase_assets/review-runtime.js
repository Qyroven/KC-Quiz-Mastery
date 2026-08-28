(() => {
  "use strict";

  const config = window.LEARNING_AUTHORING_REVIEW;
  if (!config || !config.enabled) return;

  const projectKey = new URL(config.supabaseUrl).hostname;
  // Teacher and same-origin review iframes share one authenticated identity.
  // A learner session is deliberately never used as an authorization shortcut.
  const sessionStorageKey = `la-teacher-session:${projectKey}`;
  const nameStorageKey = `la-teacher-name:${projectKey}`;
  const legacySessionStorageKey = `la-review-session:${projectKey}`;
  const legacyNameStorageKey = `la-review-name:${projectKey}`;
  const baselineByTarget = new Map();
  const appliedRevisionByTarget = new Map();
  const kcChoiceByPage = new Map();
  const state = {
    adapter: null,
    events: [],
    session: readStoredSession(),
    displayName: readStoredName(),
    canReview: false,
    accessChecked: false,
    loading: false,
    lastKey: "",
    upstreamStale: "",
  };
  let tableDraft = {columns: ["Cột 1"], rows: [[""]]};

  const css = `
    .la-review-hint-row{border:1px solid #d9e5f2;border-radius:10px;background:#f8fbfe;padding:12px;margin-bottom:10px}.la-review-hint-row label{margin-top:9px}.la-review-hint-row textarea{min-height:85px}.la-review-hint-head{display:flex;align-items:center;justify-content:space-between;gap:10px}
    :root{--la-review-height:62px}
    body[data-la-review-stage="extraction"] .layout{height:calc(100vh - 66px - var(--la-review-height))!important}
    body[data-la-review-stage="kc"] .workspace{height:calc(100% - 52px - var(--la-review-height))!important}
    body[data-la-review-stage="quiz"] .bottom{display:none!important}
    body[data-la-review-stage="quiz"] .page{padding-bottom:calc(var(--la-review-height) + 18px)!important}
    #la-review-bar{position:fixed;z-index:100;left:0;right:0;bottom:0;min-height:var(--la-review-height);display:flex;align-items:center;gap:9px;padding:9px 16px;background:rgba(255,255,255,.94);border-top:1px solid #dfe4eb;box-shadow:0 -8px 30px rgba(24,32,51,.07);backdrop-filter:saturate(180%) blur(20px);font:13px/1.3 -apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,system-ui,sans-serif;color:#182033}
    #la-review-bar button,#la-review-bar select{font:inherit}
    .la-review-target{min-width:0;display:flex;align-items:center;gap:8px;flex:1}
    .la-review-target strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .la-review-target select{max-width:220px;border:1px solid #d7dde6;border-radius:8px;background:#fff;padding:7px 9px;color:#182033}
    .la-review-status{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;padding:6px 9px;border-radius:999px;background:#f0f2f5;color:#667085;font-size:11px;font-weight:650}
    .la-review-status:before{content:"";width:7px;height:7px;border-radius:50%;background:#98a2b3}
    .la-review-status.edit:before{background:#d98914}.la-review-status.approve:before{background:#23864a}.la-review-status.reject:before{background:#c84b4b}
    .la-review-person{border:0;background:#eef5fd;color:#2864a5;border-radius:999px;padding:7px 10px;white-space:nowrap;cursor:pointer}
    .la-review-action{border:0;border-radius:8px;padding:8px 12px;font-weight:650;cursor:pointer}.la-review-action:disabled{opacity:.5;cursor:wait}
    .la-review-edit{background:#eef0f3;color:#303846}.la-review-reject{background:#fff0f0;color:#a74343}.la-review-approve{background:#2864a5;color:#fff}.la-review-history{border:0;background:transparent;color:#526071;border-radius:8px;padding:8px;cursor:pointer}
    #la-review-overlay{position:fixed;z-index:140;inset:0;display:none;place-items:center;padding:22px;background:rgba(17,24,39,.54);font:14px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,system-ui,sans-serif;color:#182033}
    #la-review-overlay.open{display:grid}.la-review-sheet{width:min(760px,96vw);max-height:92vh;display:grid;grid-template-rows:auto minmax(0,1fr) auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 30px 100px rgba(17,24,39,.28)}
    .la-review-sheet-head,.la-review-sheet-foot{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid #e4e7ec}.la-review-sheet-foot{border-top:1px solid #e4e7ec;border-bottom:0;justify-content:flex-end}.la-review-sheet-head strong{font-size:16px}.la-review-sheet-body{overflow:auto;padding:16px}
    .la-review-sheet label{display:block;margin:0 0 6px;color:#667085;font-size:12px;font-weight:650}.la-review-sheet input,.la-review-sheet textarea,.la-review-sheet select{width:100%;border:1px solid #d0d5dd;border-radius:9px;padding:10px 11px;font:inherit;color:#182033;background:#fff}.la-review-sheet textarea{min-height:100px;resize:vertical}.la-review-sheet textarea.la-json{min-height:430px;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;tab-size:2}.la-review-field+.la-review-field{margin-top:14px}
    .la-review-sheet-wide{width:min(920px,96vw)}.la-review-intro{margin:0 0 16px;padding:12px 14px;border:1px solid #cfe0f3;border-radius:11px;background:#f3f8fe;color:#345b84}.la-review-intro strong{display:block;margin-bottom:3px;color:#174f87}.la-review-intro p{margin:0}.la-review-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.la-review-grid .la-review-field{margin:0}.la-review-full{grid-column:1/-1}.la-review-section{margin-top:18px;padding-top:16px;border-top:1px solid #e4e7ec}.la-review-section h3{margin:0 0 11px;font-size:14px}.la-review-readonly{display:flex;align-items:center;gap:8px;padding:10px 11px;border-radius:9px;background:#f3f4f6;color:#596579}.la-review-pill{display:inline-flex;align-items:center;border-radius:999px;background:#eaf2fc;color:#2864a5;padding:4px 8px;font-size:11px;font-weight:700;text-transform:none}.la-review-option-list{display:grid;gap:9px}.la-review-option{display:grid;grid-template-columns:auto minmax(0,1fr);gap:10px;align-items:start;padding:10px;border:1px solid #e0e5eb;border-radius:10px;background:#fbfcfd}.la-review-option input[type="radio"],.la-review-option input[type="checkbox"]{width:18px;height:18px;margin-top:10px}.la-review-option label{margin:0}.la-review-option-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(180px,.7fr);gap:10px;align-items:end;padding:10px;border:1px solid #e0e5eb;border-radius:10px;background:#fbfcfd}.la-review-order-row{display:grid;grid-template-columns:34px minmax(0,1fr) auto;gap:9px;align-items:center;padding:9px;border:1px solid #e0e5eb;border-radius:10px;background:#fbfcfd}.la-review-order-index{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#eaf2fc;color:#2864a5;font-weight:750}.la-review-mini-actions{display:flex;gap:5px}.la-review-mini{border:0;border-radius:7px;background:#eef0f3;color:#394354;padding:8px 9px;cursor:pointer}.la-review-mini:disabled{opacity:.4;cursor:default}.la-review-rubric-row{display:grid;grid-template-columns:minmax(0,1fr) 85px auto;gap:8px;align-items:end;margin-bottom:8px}.la-review-remove{border:0;border-radius:7px;background:#fff0f0;color:#a74343;padding:10px;cursor:pointer}.la-review-add{margin-top:4px;border:1px dashed #9db7d3;border-radius:8px;background:#f5f9fd;color:#2864a5;padding:8px 11px;cursor:pointer}.la-review-hidden{display:none!important}.la-review-technical-note{margin-top:14px;color:#667085;font-size:12px}.la-review-table-help{margin:6px 0 0;color:#667085;font-size:11px}.la-review-table-scroll{overflow:auto;padding-bottom:4px}.la-review-table-grid{display:grid;gap:7px;min-width:max-content}.la-review-table-row{display:flex;gap:7px;align-items:center}.la-review-table-row input{width:170px}.la-review-table-row.head input{font-weight:700;background:#f5f7fa}.la-review-table-remove{flex:0 0 auto;border:0;border-radius:7px;background:#fff0f0;color:#a74343;padding:10px;cursor:pointer}.la-review-table-actions{display:flex;gap:8px;margin-top:9px}.la-review-stale{margin:0 0 12px;padding:10px 12px;border:1px solid #f3c16f;border-radius:10px;background:#fff7e8;color:#8b5700}
    .la-review-error{display:none;margin-top:10px;border-left:3px solid #c84b4b;background:#fff0f0;padding:9px 10px;color:#8f3030}.la-review-error.show{display:block}.la-review-help{color:#667085;font-size:12px}.la-review-spacer{flex:1}.la-review-secondary,.la-review-primary,.la-review-danger{border:0;border-radius:8px;padding:9px 13px;font:inherit;font-weight:650;cursor:pointer}.la-review-secondary{background:#eef0f3;color:#303846}.la-review-primary{background:#2864a5;color:#fff}.la-review-danger{background:#fff0f0;color:#a74343}.la-review-event{border:1px solid #e1e5eb;border-radius:10px;padding:11px;margin-bottom:9px}.la-review-event-head{display:flex;align-items:center;gap:8px}.la-review-event-head time{margin-left:auto;color:#667085;font-size:11px}.la-review-event p{margin:7px 0 0;color:#526071}.la-review-event code{font-size:11px;color:#667085}
    @media(max-width:760px){:root{--la-review-height:112px}#la-review-bar{flex-wrap:wrap;padding:8px}.la-review-target{flex-basis:100%}.la-review-status{margin-left:auto}.la-review-person{max-width:120px;overflow:hidden;text-overflow:ellipsis}.la-review-action{padding:7px 9px}.la-review-grid{grid-template-columns:1fr}.la-review-full{grid-column:auto}.la-review-option-row,.la-review-rubric-row{grid-template-columns:1fr}.la-review-order-row{grid-template-columns:30px minmax(0,1fr) auto}.la-review-table-row input{width:145px}}
  `;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  const bar = document.createElement("footer");
  bar.id = "la-review-bar";
  bar.innerHTML = `
    <div class="la-review-target"><strong id="la-review-target-label">Đang tải review…</strong><span id="la-review-target-choice"></span><span id="la-review-status" class="la-review-status">Chưa review</span></div>
    <button id="la-review-person" class="la-review-person" type="button">Nhập tên để review</button>
    <button id="la-review-history" class="la-review-history" type="button" title="Lịch sử review">Lịch sử</button>
    <button class="la-review-action la-review-edit" data-la-action="edit" type="button">✎ Sửa</button>
    <button class="la-review-action la-review-reject" data-la-action="reject" type="button">× Từ chối</button>
    <button class="la-review-action la-review-approve" data-la-action="approve" type="button">✓ Duyệt</button>`;
  document.body.appendChild(bar);

  const overlay = document.createElement("div");
  overlay.id = "la-review-overlay";
  overlay.innerHTML = `<section class="la-review-sheet" role="dialog" aria-modal="true"><header class="la-review-sheet-head"><strong id="la-review-modal-title"></strong><span class="la-review-spacer"></span><button id="la-review-close" class="la-review-secondary" type="button">Đóng</button></header><div id="la-review-modal-body" class="la-review-sheet-body"></div><footer id="la-review-modal-foot" class="la-review-sheet-foot"></footer></section>`;
  document.body.appendChild(overlay);

  const byId = id => document.getElementById(id);
  const targetLabel = byId("la-review-target-label");
  const targetChoice = byId("la-review-target-choice");
  const statusNode = byId("la-review-status");
  const personButton = byId("la-review-person");
  const modalTitle = byId("la-review-modal-title");
  const modalBody = byId("la-review-modal-body");
  const modalFoot = byId("la-review-modal-foot");
  const modalSheet = overlay.querySelector(".la-review-sheet");
  const actionButtons = [...document.querySelectorAll("[data-la-action]")];
  let modalCancelHandler = null;

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  }

  function deepCopy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function isObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function quizHintsAreValid(payload, baseline) {
    const hasHints = Object.hasOwn(payload, "hints"), hasReason = Object.hasOwn(payload, "hint_absence_reason");
    // Loading an old revision must not silently strip a newly authored hint contract.
    if ((Object.hasOwn(baseline, "hints") || Object.hasOwn(baseline, "hint_absence_reason")) && (!hasHints || !hasReason)) return false;
    if (!hasHints && !hasReason) return true;
    if (!hasHints || !hasReason || !Array.isArray(payload.hints)) return false;
    const ids = new Set();
    for (const hint of payload.hints) {
      if (!isObject(hint) || typeof hint.hint_id !== "string" || !hint.hint_id.trim() || ids.has(hint.hint_id) ||
          !["cue", "strategy", "step"].includes(hint.kind) || typeof hint.text !== "string" || !hint.text.trim()) return false;
      ids.add(hint.hint_id);
    }
    return payload.hints.length ? payload.hint_absence_reason === null :
      typeof payload.hint_absence_reason === "string" && Boolean(payload.hint_absence_reason.trim());
  }

  function revisionMatchesAdapter(adapter, payload) {
    if (!isObject(payload) || payload[adapter.identityField] !== adapter.identityValue) return false;
    const unchanged = fields => fields.every(field =>
      canonical(payload[field]) === canonical(adapter.payload[field]));
    if (adapter.stage === "extraction") {
      return typeof payload.role === "string" && Array.isArray(payload.blocks) &&
        Array.isArray(payload.reading_order) && isObject(payload.page_note) && Array.isArray(payload.warnings);
    }
    if (adapter.stage === "kc" && adapter.itemType === "leaf_kc") {
      if (!unchanged(["group_id", "source_evidence", "context_evidence"])) return false;
      return typeof payload.group_id === "string" && typeof payload.name === "string" &&
        typeof payload.semantic_form === "string" && typeof payload.knowledge_description === "string" &&
        typeof payload.observable_claim === "string" && Array.isArray(payload.source_evidence) &&
        isObject(payload.assessment_boundary) && Array.isArray(payload.assessment_boundary.included) &&
        Array.isArray(payload.assessment_boundary.excluded) && typeof payload.status === "string" &&
        Array.isArray(payload.warning_codes);
    }
    if (adapter.stage === "kc" && adapter.itemType === "page_audit") {
      if (!unchanged(["source_block_ids", "kc_ids"])) return false;
      return typeof payload.classification === "string" && typeof payload.summary === "string" &&
        Array.isArray(payload.source_block_ids) && Array.isArray(payload.kc_ids) &&
        Array.isArray(payload.warning_codes);
    }
    if (adapter.stage === "quiz") {
      if (!unchanged(["kc_id", "group_id", "slot_id", "variant_index", "evidence_refs", "context_evidence_refs"])) return false;
      if (!quizHintsAreValid(payload, adapter.payload)) return false;
      return typeof payload.kc_id === "string" && typeof payload.group_id === "string" &&
        typeof payload.title === "string" && typeof payload.interaction === "string" &&
        isObject(payload.stimulus) && typeof payload.prompt === "string" &&
        Array.isArray(payload.choice_options) && Array.isArray(payload.matching_left) &&
        Array.isArray(payload.matching_right) && Array.isArray(payload.ordering_options) &&
        isObject(payload.correct_answer) && Array.isArray(payload.rubric) &&
        Array.isArray(payload.evidence_refs);
    }
    return false;
  }

  function canonical(value) {
    if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
    if (value && typeof value === "object") {
      return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
    }
    return JSON.stringify(value);
  }

  async function payloadSha256(value) {
    const bytes = new TextEncoder().encode(canonical(value));
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
  }

  function rebuildKcIndexes() {
    if (typeof kcs === "undefined" || typeof evidenceByPage === "undefined" ||
        typeof kcById === "undefined" || typeof pageRows === "undefined") return;
    Object.keys(kcById).forEach(key => delete kcById[key]);
    Object.keys(evidenceByPage).forEach(key => delete evidenceByPage[key]);
    kcs.forEach(kc => {
      kcById[kc.kc_id] = kc;
      kc.source_evidence.forEach(evidence => ((evidenceByPage[evidence.page] ??= []).push({kc, evidence})));
    });
    if (typeof groupList !== "undefined" && typeof groups !== "undefined") {
      groupList.forEach(group => { group.leaf_kc_ids.length = 0; });
      kcs.forEach(kc => {
        const group = groups[kc.group_id];
        if (group && !group.leaf_kc_ids.includes(kc.kc_id)) group.leaf_kc_ids.push(kc.kc_id);
      });
      if (typeof renderGroupFilters === "function") renderGroupFilters();
    }
    const nextRows = source.pages.map(page => {
      const audit = auditByPage[page.page_number];
      const rowState = pageState(page.page_number);
      const related = new Set((evidenceByPage[page.page_number] || []).map(row => row.kc.kc_id));
      const groupIds = [...new Set([...related].map(id => kcById[id].group_id))];
      return {
        page: page.page_number, role: page.role, audit, state: rowState,
        kcCount: related.size, groupIds,
        search: compactContent({page, audit, related: [...related].map(id => kcById[id]), groups: groupIds.map(id => groups[id])}).toLowerCase(),
      };
    });
    pageRows.splice(0, pageRows.length, ...nextRows);
  }

  function readStoredSession() {
    try {
      const current = localStorage.getItem(sessionStorageKey);
      if (current) return JSON.parse(current);
      const previous = localStorage.getItem(legacySessionStorageKey);
      if (previous) {
        const session = JSON.parse(previous);
        localStorage.setItem(sessionStorageKey, previous);
        return session;
      }
      return null;
    }
    catch { throw new Error("Không đọc được phiên Teacher đã lưu. Giữ nguyên dữ liệu; không tự tạo danh tính khác."); }
  }

  function readStoredName() {
    try {
      const current = localStorage.getItem(nameStorageKey);
      return current ? JSON.parse(current) : localStorage.getItem(legacyNameStorageKey) || "";
    } catch { return ""; }
  }

  function storeSession(session) {
    state.session = session;
    localStorage.setItem(sessionStorageKey, JSON.stringify(session));
  }

  async function request(path, {method = "GET", body, authenticated = false, prefer = ""} = {}) {
    const headers = {apikey: config.supabasePublishableKey};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (prefer) headers.Prefer = prefer;
    if (authenticated) {
      const session = await ensureSession();
      headers.Authorization = `Bearer ${session.access_token}`;
    }
    const response = await fetch(`${config.supabaseUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const error = await response.json();
        message = error.message || error.error_description || error.hint || message;
      } catch { /* keep HTTP status */ }
      throw new Error(message);
    }
    if (response.status === 204) return null;
    const text = await response.text();
    return text ? JSON.parse(text) : null;
  }

  async function refreshSession(session) {
    const response = await fetch(`${config.supabaseUrl}/auth/v1/token?grant_type=refresh_token`, {
      method: "POST",
      headers: {apikey: config.supabasePublishableKey, "Content-Type": "application/json"},
      body: JSON.stringify({refresh_token: session.refresh_token}),
    });
    if (!response.ok) throw new Error("Phiên review đã hết hạn");
    const fresh = await response.json();
    fresh.expires_at ||= Math.floor(Date.now() / 1000) + Number(fresh.expires_in || 3600);
    storeSession(fresh);
    return fresh;
  }

  async function createAnonymousSession() {
    const response = await fetch(`${config.supabaseUrl}/auth/v1/signup`, {
      method: "POST",
      headers: {apikey: config.supabasePublishableKey, "Content-Type": "application/json"},
      body: JSON.stringify({data: {application: "learning-authoring-review"}}),
    });
    if (!response.ok) {
      let message = "Không thể tạo phiên review ẩn danh";
      try { message = (await response.json()).message || message; } catch { /* keep message */ }
      throw new Error(message);
    }
    const payload = await response.json();
    const session = payload.session || payload;
    session.user ||= payload.user;
    session.expires_at ||= Math.floor(Date.now() / 1000) + Number(session.expires_in || 3600);
    if (!session.access_token || !session.refresh_token || !session.user?.id) {
      throw new Error("Supabase không trả về anonymous session hợp lệ");
    }
    storeSession(session);
    return session;
  }

  async function ensureSession() {
    let session = readStoredSession() || state.session;
    if (session?.access_token && Number(session.expires_at || 0) > Date.now() / 1000 + 60) {
      return session;
    }
    if (session?.refresh_token) {
      try { return await refreshSession(session); }
      catch { throw new Error("Chưa làm mới được phiên Teacher. Giữ nguyên danh tính, không tự tạo tài khoản khác."); }
    }
    return createAnonymousSession();
  }

  async function refreshAccess() {
    state.canReview = false;
    state.accessChecked = false;
    state.session = readStoredSession() || state.session;
    if (!state.session) { state.accessChecked = true; return false; }
    const access = await request("/rest/v1/rpc/get_teacher_access", {
      method: "POST", authenticated: true, body: {p_run_id: config.runId},
    });
    if (!access || typeof access.can_teach !== "boolean") {
      throw new Error("Chưa xác minh được quyền giảng viên; chỉ cho phép xem.");
    }
    state.canReview = access.can_teach === true;
    state.accessChecked = true;
    state.displayName = readStoredName() || state.displayName;
    return state.canReview;
  }

  function openModal(title, bodyHtml, footerHtml, {wide = false} = {}) {
    modalCancelHandler = null;
    modalTitle.textContent = title;
    modalBody.innerHTML = bodyHtml;
    modalFoot.innerHTML = footerHtml;
    modalSheet.classList.toggle("la-review-sheet-wide", wide);
    overlay.classList.add("open");
  }

  function closeModal() {
    overlay.classList.remove("open");
    modalSheet.classList.remove("la-review-sheet-wide");
    modalBody.innerHTML = "";
    modalFoot.innerHTML = "";
  }

  function cancelActiveModal() {
    const handler = modalCancelHandler;
    modalCancelHandler = null;
    closeModal();
    if (handler) handler();
  }

  function showModalError(message) {
    const node = byId("la-review-error");
    if (!node) return;
    node.textContent = message;
    node.classList.add("show");
  }

  async function saveProfile(displayName) {
    const clean = displayName.trim();
    if (clean.length < 1 || clean.length > 80) throw new Error("Tên cần từ 1 đến 80 ký tự");
    const session = await ensureSession();
    await request("/rest/v1/reviewer_profiles?on_conflict=user_id", {
      method: "POST",
      authenticated: true,
      prefer: "resolution=merge-duplicates,return=minimal",
      body: {user_id: session.user.id, display_name: clean},
    });
    state.displayName = clean;
    localStorage.setItem(nameStorageKey, JSON.stringify(clean));
    await refreshAccess();
    renderPerson();
    return clean;
  }

  function askForName() {
    return new Promise((resolve, reject) => {
      openModal(
        "Bạn đang review với tên gì?",
        `<div class="la-review-field"><label for="la-review-name">Tên hiển thị</label><input id="la-review-name" maxlength="80" autocomplete="name" value="${escapeHtml(state.displayName)}" placeholder="Tên của bạn"><p class="la-review-help">Tên chỉ ghi vào lịch sử. Quyền sửa/duyệt phải được quản trị viên cấp cho đúng tài khoản của bài học.</p>${state.session?.user?.id ? `<label for="la-review-account-id">Mã tài khoản để xin cấp quyền</label><input id="la-review-account-id" readonly value="${escapeHtml(state.session.user.id)}">` : ""}</div><div id="la-review-error" class="la-review-error"></div>`,
        `<a class="la-review-secondary" href="index.html" target="_top">Mở Teacher</a><span class="la-review-spacer"></span><button id="la-review-name-cancel" class="la-review-secondary" type="button">Hủy</button><button id="la-review-name-save" class="la-review-primary" type="button">Lưu tên</button>`,
      );
      const input = byId("la-review-name");
      input.focus(); input.select();
      const cancel = () => reject(new Error("cancelled"));
      modalCancelHandler = cancel;
      byId("la-review-name-cancel").onclick = cancelActiveModal;
      byId("la-review-name-save").onclick = async () => {
        const button = byId("la-review-name-save");
        button.disabled = true;
        try { const name = await saveProfile(input.value); modalCancelHandler = null; closeModal(); resolve(name); }
        catch (error) { showModalError(error.message); button.disabled = false; }
      };
      input.onkeydown = event => { if (event.key === "Enter") byId("la-review-name-save").click(); };
    });
  }

  async function ensureReviewer() {
    if (!state.displayName) await askForName();
    if (!await refreshAccess()) {
      setLoading(false);
      throw new Error("Tài khoản chưa được cấp quyền giảng viên của bài học này. Nhập tên không cấp quyền sửa/duyệt.");
    }
    return state.displayName;
  }

  function detectAdapter() {
    if (typeof questions !== "undefined" && Array.isArray(questions) && typeof index !== "undefined") {
      const question = questions[index];
      if (!question) return null;
      return {
        stage: "quiz", itemType: "question", itemKey: question.question_id,
        label: `${question.question_id} · ${question.title}`,
        identityField: "question_id", identityValue: question.question_id,
        payload: deepCopy(question),
        apply(payload) {
          // Even an identical saved revision is a new review target. Never retain
          // a green AI verdict from the generated snapshot after a shared edit.
          if (typeof markQuestionRevision === "function") markQuestionRevision(question.question_id);
          Object.keys(question).forEach(key => delete question[key]);
          Object.assign(question, deepCopy(payload));
          render();
        },
      };
    }
    if (typeof proposal !== "undefined" && typeof selected !== "undefined" && typeof evidenceByPage !== "undefined") {
      const contextView = typeof contextSelected !== "undefined" && contextSelected;
      const choiceKey = contextView ? "context" : selected;
      const rows = typeof selectedKcRows === "function" ? selectedKcRows() : evidenceByPage[selected] || [];
      const ids = [...new Set(rows.map(row => row.kc.kc_id))];
      let chosen = kcChoiceByPage.get(choiceKey);
      if (!ids.includes(chosen)) chosen = ids[0];
      if (chosen) {
        kcChoiceByPage.set(choiceKey, chosen);
        const kc = kcById[chosen];
        return {
          stage: "kc", itemType: "leaf_kc", itemKey: chosen,
          label: `${chosen} · ${kc.name}`, choices: ids, choiceKey,
          identityField: "kc_id", identityValue: chosen, payload: deepCopy(kc),
          apply(payload) { Object.keys(kc).forEach(key => delete kc[key]); Object.assign(kc, deepCopy(payload)); rebuildKcIndexes(); render(); },
        };
      }
      if (contextView) return null;
      const audit = auditByPage[selected];
      if (!audit) return null;
      return {
        stage: "kc", itemType: "page_audit", itemKey: `page:${String(selected).padStart(4, "0")}`,
        label: `Slide ${selected} · quyết định không tạo KC`,
        identityField: "page", identityValue: selected, payload: deepCopy(audit),
        apply(payload) { Object.keys(audit).forEach(key => delete audit[key]); Object.assign(audit, deepCopy(payload)); rebuildKcIndexes(); render(); },
      };
    }
    if (typeof source !== "undefined" && Array.isArray(source.pages) && typeof selected !== "undefined") {
      const page = source.pages[selected];
      if (!page) return null;
      return {
        stage: "extraction", itemType: "page", itemKey: `page:${String(page.page_number).padStart(4, "0")}`,
        label: `Page ${page.page_number} · ${page.role}`,
        identityField: "page_number", identityValue: page.page_number, payload: deepCopy(page),
        apply(payload) { Object.keys(page).forEach(key => delete page[key]); Object.assign(page, deepCopy(payload)); render(); },
      };
    }
    return null;
  }

  function targetId(adapter) {
    return `${config.runId}:${adapter.stage}:${adapter.itemType}:${adapter.itemKey}`;
  }

  async function fetchEvents(adapter, baseArtifactSha256) {
    return (await request("/rest/v1/rpc/get_review_target_events", {
      method: "POST",
      authenticated: true,
      body: {
        p_run_id: config.runId,
        p_stage: adapter.stage,
        p_item_type: adapter.itemType,
        p_item_key: adapter.itemKey,
        p_base_artifact_sha256: baseArtifactSha256,
      },
    })) || [];
  }

  async function hasChangedRevision(adapter, baselinePayload) {
    const baselineSha256 = await payloadSha256(baselinePayload);
    const events = await fetchEvents(adapter, baselineSha256);
    const revision = latestRevision(events);
    return Boolean(revision && revision.payload_sha256 !== baselineSha256);
  }

  async function upstreamStaleMessage(adapter) {
    if (adapter.stage === "quiz" && typeof kcs !== "undefined" && typeof kcs.get === "function") {
      const kc = kcs.get(adapter.payload.kc_id);
      if (!kc) return "";
      const changed = await hasChangedRevision({
        stage: "kc", itemType: "leaf_kc", itemKey: kc.kc_id,
      }, kc);
      return changed ? `${kc.kc_id} đã được sửa sau khi Quiz này được tạo · cần tạo lại Quiz trước khi duyệt` : "";
    }
    if (adapter.stage === "kc" && typeof source !== "undefined" && Array.isArray(source.pages)) {
      const pageNumbers = adapter.itemType === "leaf_kc"
        ? [...new Set(adapter.payload.source_evidence.map(evidence => evidence.page))]
        : [adapter.payload.page];
      for (const pageNumber of pageNumbers) {
        const page = source.pages.find(item => item.page_number === pageNumber);
        if (!page) continue;
        const changed = await hasChangedRevision({
          stage: "extraction", itemType: "page",
          itemKey: `page:${String(pageNumber).padStart(4, "0")}`,
        }, page);
        if (changed) return `Slide ${pageNumber} ở bước Trích xuất đã được sửa · cần tạo lại KC trước khi duyệt`;
      }
    }
    return "";
  }

  function latestRevision(events) {
    return events.find(event => event.action === "edit") || null;
  }

  function currentDecision(events, revision, payloadHash) {
    const revisionId = revision?.id || null;
    return events.find(event =>
      (event.action === "approve" || event.action === "reject") &&
      (event.target_revision_id || null) === revisionId &&
      event.payload_sha256 === payloadHash
    ) || null;
  }

  async function loadCurrentTarget({force = false} = {}) {
    const adapter = detectAdapter();
    if (!adapter) return;
    const id = targetId(adapter);
    if (!baselineByTarget.has(id)) {
      baselineByTarget.set(id, {payload: deepCopy(adapter.payload), sha256: await payloadSha256(adapter.payload)});
    }
    if (!force && state.lastKey === id) return;
    state.lastKey = id;
    state.adapter = adapter;
    state.upstreamStale = "";
    document.body.dataset.laReviewStage = adapter.stage;
    targetLabel.textContent = adapter.label;
    renderChoice(adapter);
    setLoading(true);
    if (adapter.stage === "quiz" && typeof setQuizReviewDependencyState === "function") {
      setQuizReviewDependencyState({uncertain: "Đang đối chiếu revision và KC nguồn với review chung."});
    }
    try {
      if (!await refreshAccess()) {
        state.events = [];
        statusNode.className = "la-review-status";
        statusNode.textContent = "Chỉ đọc · Chưa cấp quyền giảng viên";
        renderPerson();
        return;
      }
      const baseline = baselineByTarget.get(id);
      state.events = await fetchEvents(adapter, baseline.sha256);
      const revision = latestRevision(state.events);
      if (revision && appliedRevisionByTarget.get(id) !== revision.id) {
        if (!revisionMatchesAdapter(adapter, revision.revision_payload)) {
          throw new Error("Revision được chia sẻ không còn khớp schema của mục này");
        }
        adapter.apply(revision.revision_payload);
        appliedRevisionByTarget.set(id, revision.id);
        state.adapter = detectAdapter() || adapter;
      }
      state.upstreamStale = await upstreamStaleMessage(state.adapter);
      if (adapter.stage === "quiz" && typeof setQuizReviewDependencyState === "function") {
        setQuizReviewDependencyState({stale: state.upstreamStale});
      }
      await renderStatus();
    } catch (error) {
      state.canReview = false;
      if (adapter.stage === "quiz" && typeof setQuizReviewDependencyState === "function") {
        setQuizReviewDependencyState({uncertain: "Chưa xác minh được revision/KC nguồn từ review chung."});
      }
      statusNode.className = "la-review-status reject";
      statusNode.textContent = `Không sync được: ${error.message}`;
    } finally { setLoading(false); }
  }

  function renderChoice(adapter) {
    if (!adapter.choices || adapter.choices.length < 2) { targetChoice.innerHTML = ""; return; }
    targetChoice.innerHTML = `<select aria-label="Chọn KC để review">${adapter.choices.map(id => `<option value="${escapeHtml(id)}"${id === adapter.itemKey ? " selected" : ""}>${escapeHtml(id)}</option>`).join("")}</select>`;
    targetChoice.querySelector("select").onchange = event => {
      kcChoiceByPage.set(adapter.choiceKey ?? selected, event.target.value);
      state.lastKey = "";
      loadCurrentTarget({force: true});
    };
  }

  async function effectivePayload() {
    const baseline = baselineByTarget.get(targetId(state.adapter));
    const revision = latestRevision(state.events);
    const payload = revision ? revision.revision_payload : baseline.payload;
    return {payload, revision, sha256: revision?.payload_sha256 || baseline.sha256, baseline};
  }

  async function renderStatus() {
    if (!state.adapter) return;
    if (state.upstreamStale) {
      statusNode.className = "la-review-status reject";
      statusNode.textContent = state.upstreamStale;
      renderPerson();
      return;
    }
    const {revision, sha256} = await effectivePayload();
    const decision = currentDecision(state.events, revision, sha256);
    statusNode.className = "la-review-status";
    if (decision?.action === "approve") {
      statusNode.classList.add("approve");
      statusNode.textContent = `Đã duyệt · ${decision.reviewer_name}`;
    } else if (decision?.action === "reject") {
      statusNode.classList.add("reject");
      statusNode.textContent = `Từ chối · ${decision.reviewer_name}`;
    } else if (revision) {
      statusNode.classList.add("edit");
      statusNode.textContent = `Revision · ${revision.reviewer_name}`;
    } else {
      statusNode.textContent = "Chưa review";
    }
    renderPerson();
  }

  function renderPerson() {
    personButton.textContent = state.displayName ? `👤 ${state.displayName}` : "Danh tính & quyền";
  }

  function setLoading(loading) {
    state.loading = loading;
    actionButtons.forEach(button => {
      button.disabled = loading || !state.canReview || (button.dataset.laAction === "approve" && Boolean(state.upstreamStale));
    });
  }

  async function insertEvent(event) {
    await ensureReviewer();
    const row = await request("/rest/v1/rpc/append_review_event", {
      method: "POST",
      authenticated: true,
      body: {
        p_run_id: event.run_id,
        p_stage: event.stage,
        p_item_type: event.item_type,
        p_item_key: event.item_key,
        p_action: event.action,
        p_note: event.note,
        p_revision_payload: event.revision_payload,
        p_expected_revision_id: event.target_revision_id,
      },
    });
    return Array.isArray(row) ? row[0] : row;
  }

  async function saveRevision(payload, note, expectedRevisionId) {
    await loadCurrentTarget({force: true});
    const current = latestRevision(state.events);
    if ((current?.id || null) !== (expectedRevisionId || null)) {
      throw new Error("Có revision mới từ reviewer khác. Đã reload; hãy mở Sửa lại.");
    }
    const adapter = state.adapter;
    if (payload?.[adapter.identityField] !== adapter.identityValue) {
      throw new Error(`${adapter.identityField} không được thay đổi`);
    }
    if (!revisionMatchesAdapter(adapter, payload)) {
      throw new Error("Cấu trúc, nguồn đối chiếu và định danh KC / slot / variant phải được giữ nguyên");
    }
    await insertEvent({
      run_id: config.runId, stage: adapter.stage, item_type: adapter.itemType,
      item_key: adapter.itemKey, action: "edit", note: note || null,
      revision_payload: payload, target_revision_id: expectedRevisionId || null,
    });
    adapter.apply(payload);
    state.lastKey = "";
    await loadCurrentTarget({force: true});
    broadcastUpdate();
  }

  async function decisionSnapshot() {
    if (!state.adapter) throw new Error("Chưa tải được mục cần review");
    const target = targetId(state.adapter);
    const {revision, sha256} = await effectivePayload();
    return {target, revisionId: revision?.id || null, sha256};
  }

  async function makeDecision(action, note = null, expected = null) {
    const shown = expected || await decisionSnapshot();
    await loadCurrentTarget({force: true});
    const adapter = state.adapter;
    const {revision, sha256} = await effectivePayload();
    if (shown.target !== targetId(adapter) || shown.revisionId !== (revision?.id || null) || shown.sha256 !== sha256) {
      throw new Error("Nội dung đã thay đổi từ lúc bạn xem. Đã tải revision mới; hãy đọc lại trước khi duyệt hoặc từ chối.");
    }
    await insertEvent({
      run_id: config.runId, stage: adapter.stage, item_type: adapter.itemType,
      item_key: adapter.itemKey, action, note,
      revision_payload: null, target_revision_id: revision?.id || null,
    });
    state.lastKey = "";
    await loadCurrentTarget({force: true});
    broadcastUpdate();
  }

  function requiredValue(id, label) {
    const value = byId(id)?.value.trim() || "";
    if (!value) throw new Error(`${label} không được để trống`);
    return value;
  }

  function lineList(id) {
    return (byId(id)?.value || "").split("\n").map(value => value.trim()).filter(Boolean);
  }

  function noteField() {
    return `<div class="la-review-field la-review-section"><label for="la-review-note">Ghi chú thay đổi (không bắt buộc)</label><textarea id="la-review-note" maxlength="1000" placeholder="Bạn đã sửa gì?"></textarea></div><div id="la-review-error" class="la-review-error"></div>`;
  }

  function editorFooter() {
    return `<button id="la-review-edit-cancel" class="la-review-secondary" type="button">Hủy</button><button id="la-review-edit-save" class="la-review-primary" type="button">Lưu thay đổi</button>`;
  }

  function bindEditorSave(buildPayload, revision) {
    byId("la-review-edit-cancel").onclick = closeModal;
    byId("la-review-edit-save").onclick = async () => {
      const button = byId("la-review-edit-save");
      button.disabled = true;
      try {
        const nextPayload = buildPayload();
        await saveRevision(nextPayload, byId("la-review-note").value.trim(), revision?.id || null);
        closeModal();
      } catch (error) {
        showModalError(error.message);
        button.disabled = false;
      }
    };
  }

  function openExtractionEditor(payload, revision) {
    openModal(
      `Sửa ${state.adapter.label}`,
      `<div class="la-review-intro"><strong>Trang Extraction là dữ liệu có cấu trúc.</strong><p>Editor kỹ thuật này chỉ thuộc bước 1. KC và Quiz dùng form nội dung riêng, không sửa qua JSON.</p></div><div class="la-review-field"><label for="la-review-json">Dữ liệu trang</label><textarea id="la-review-json" class="la-json" spellcheck="false"></textarea><p class="la-review-help">Bản output gốc vẫn bất biến; thao tác này tạo một revision dùng chung.</p></div>${noteField()}`,
      editorFooter(),
      {wide: true},
    );
    byId("la-review-json").value = JSON.stringify(payload, null, 2);
    bindEditorSave(() => JSON.parse(byId("la-review-json").value), revision);
  }

  function openKcEditor(payload, revision) {
    if (state.adapter.itemType === "page_audit") {
      const classifications = [
        ["learning_content", "Nội dung học"], ["example", "Ví dụ"],
        ["exercise", "Bài tập"], ["context", "Bối cảnh"],
        ["administrative", "Thông tin hành chính"], ["cover", "Trang bìa"],
        ["section_divider", "Trang phân đoạn"], ["unclear", "Chưa rõ"],
      ];
      openModal(
        `Sửa quyết định KC · Slide ${payload.page}`,
        `<div class="la-review-intro"><strong>Đây là quyết định của bước KC, không phải Extraction.</strong><p>Bạn chỉ sửa cách trang này được hiểu trong luồng tạo kiến thức. Nội dung trích xuất và các liên kết nguồn được giữ nguyên.</p></div><div class="la-review-grid"><div class="la-review-field"><label for="la-kc-classification">Loại trang</label><select id="la-kc-classification">${classifications.map(([value, label]) => `<option value="${value}"${payload.classification === value ? " selected" : ""}>${label}</option>`).join("")}</select></div><div class="la-review-field"><label>Trang nguồn</label><div class="la-review-readonly"><span class="la-review-pill">Slide ${payload.page}</span><span>${payload.source_block_ids.length} phần nội dung được liên kết</span></div></div><div class="la-review-field la-review-full"><label for="la-kc-summary">Lý do / tóm tắt quyết định</label><textarea id="la-kc-summary">${escapeHtml(payload.summary)}</textarea></div></div>${noteField()}`,
        editorFooter(),
        {wide: true},
      );
      bindEditorSave(() => {
        const next = deepCopy(payload);
        next.classification = byId("la-kc-classification").value;
        next.summary = requiredValue("la-kc-summary", "Lý do / tóm tắt");
        return next;
      }, revision);
      return;
    }

    const forms = [
      ["fact", "Sự kiện / dữ kiện"], ["concept", "Khái niệm"],
      ["distinction", "Phân biệt"], ["principle", "Nguyên lý"],
      ["procedure", "Quy trình"], ["decision_rule", "Quy tắc quyết định"],
    ];
    const pages = [...new Set(payload.source_evidence.map(item => item.page))].join(", ");
    const contextRefs = payload.context_evidence || [];
    const contextIds = [...new Set(contextRefs.map(item => item.context_id))].join(", ");
    const provenanceHtml = `<div class="la-review-readonly"><span class="la-review-pill">${pages ? `Slide ${escapeHtml(pages)}` : "Không có nguồn PDF"}</span><span>${payload.source_evidence.length} phần nguồn PDF</span></div>${contextRefs.length ? `<div class="la-review-readonly"><span class="la-review-pill">${escapeHtml(contextIds)}</span><span>${contextRefs.length} phần ngữ cảnh giảng viên · không phải Extraction</span></div>` : ""}`;
    const boundaries = payload.assessment_boundary || {included: [], excluded: []};
    openModal(
      `Sửa ${payload.kc_id} · ${payload.name}`,
      `<div class="la-review-intro"><strong>Chỉ sửa nội dung KC.</strong><p>Nguồn PDF và ngữ cảnh giảng viên được khóa và giữ nguyên. Muốn sửa nội dung trích xuất, hãy quay về bước 1.</p></div><div class="la-review-grid"><div class="la-review-field la-review-full"><label for="la-kc-name">Tên KC</label><input id="la-kc-name" maxlength="240" value="${escapeHtml(payload.name)}"></div><div class="la-review-field"><label for="la-kc-semantic-form">Loại kiến thức</label><select id="la-kc-semantic-form">${forms.map(([value, label]) => `<option value="${value}"${payload.semantic_form === value ? " selected" : ""}>${label}</option>`).join("")}</select></div><div class="la-review-field"><label>Nguồn đối chiếu được giữ nguyên</label>${provenanceHtml}</div><div class="la-review-field la-review-full"><label for="la-kc-description">Mô tả kiến thức</label><textarea id="la-kc-description">${escapeHtml(payload.knowledge_description)}</textarea><p class="la-review-help">Nêu chính xác điều người học cần hiểu hoặc biết làm.</p></div><div class="la-review-field la-review-full"><label for="la-kc-observable">Biểu hiện quan sát được ở người học</label><textarea id="la-kc-observable">${escapeHtml(payload.observable_claim)}</textarea><p class="la-review-help">Mô tả bằng hành vi có thể kiểm tra được, ví dụ phân biệt, giải thích, áp dụng hoặc đánh giá.</p></div><div class="la-review-field"><label for="la-kc-included">Nội dung được phép đánh giá</label><textarea id="la-kc-included" placeholder="Mỗi ý một dòng">${escapeHtml(boundaries.included.join("\n"))}</textarea></div><div class="la-review-field"><label for="la-kc-excluded">Nội dung không đánh giá</label><textarea id="la-kc-excluded" placeholder="Mỗi ý một dòng">${escapeHtml(boundaries.excluded.join("\n"))}</textarea></div></div><p class="la-review-technical-note">Mã định danh, nhóm KC, trạng thái, cảnh báo và toàn bộ provenance được hệ thống giữ tự động.</p>${noteField()}`,
      editorFooter(),
      {wide: true},
    );
    bindEditorSave(() => {
      const next = deepCopy(payload);
      next.name = requiredValue("la-kc-name", "Tên KC");
      next.semantic_form = byId("la-kc-semantic-form").value;
      next.knowledge_description = requiredValue("la-kc-description", "Mô tả kiến thức");
      next.observable_claim = requiredValue("la-kc-observable", "Biểu hiện quan sát được");
      next.assessment_boundary = {included: lineList("la-kc-included"), excluded: lineList("la-kc-excluded")};
      return next;
    }, revision);
  }

  function tableGridHtml() {
    const header = `<div class="la-review-table-row head">${tableDraft.columns.map((value, index) => `<input data-la-table-header="${index}" aria-label="Tên cột ${index + 1}" value="${escapeHtml(value)}">`).join("")}<button class="la-review-table-remove" data-la-remove-column type="button"${tableDraft.columns.length <= 1 ? " disabled" : ""}>− Cột</button></div>`;
    const rows = tableDraft.rows.map((row, rowIndex) => `<div class="la-review-table-row">${tableDraft.columns.map((_, columnIndex) => `<input data-la-table-cell="${rowIndex}:${columnIndex}" aria-label="Dòng ${rowIndex + 1}, cột ${columnIndex + 1}" value="${escapeHtml(row[columnIndex] ?? "")}">`).join("")}<button class="la-review-table-remove" data-la-remove-row="${rowIndex}" type="button"${tableDraft.rows.length <= 1 ? " disabled" : ""}>− Dòng</button></div>`).join("");
    return `<div class="la-review-table-grid">${header}${rows}</div>`;
  }

  function syncTableDraft() {
    const grid = byId("la-quiz-table-grid");
    if (!grid) return;
    grid.querySelectorAll("[data-la-table-header]").forEach(input => {
      tableDraft.columns[Number(input.dataset.laTableHeader)] = input.value;
    });
    grid.querySelectorAll("[data-la-table-cell]").forEach(input => {
      const [row, column] = input.dataset.laTableCell.split(":").map(Number);
      tableDraft.rows[row][column] = input.value;
    });
  }

  function renderTableGrid() {
    byId("la-quiz-table-grid").innerHTML = tableGridHtml();
  }

  function setupTableControls(stimulus) {
    tableDraft = stimulus.kind === "table"
      ? {columns: [...stimulus.table_columns], rows: stimulus.table_rows.map(row => [...row])}
      : {columns: ["Cột 1"], rows: [[""]]};
    renderTableGrid();
    const wrap = byId("la-stimulus-table-wrap");
    wrap.onclick = event => {
      const removeRow = event.target.closest("[data-la-remove-row]");
      const removeColumn = event.target.closest("[data-la-remove-column]");
      if (removeRow && tableDraft.rows.length > 1) {
        syncTableDraft();
        tableDraft.rows.splice(Number(removeRow.dataset.laRemoveRow), 1);
        renderTableGrid();
      } else if (removeColumn && tableDraft.columns.length > 1) {
        syncTableDraft();
        tableDraft.columns.pop();
        tableDraft.rows.forEach(row => row.pop());
        renderTableGrid();
      }
    };
    byId("la-add-table-row").onclick = () => {
      syncTableDraft();
      tableDraft.rows.push(tableDraft.columns.map(() => ""));
      renderTableGrid();
    };
    byId("la-add-table-column").onclick = () => {
      syncTableDraft();
      tableDraft.columns.push(`Cột ${tableDraft.columns.length + 1}`);
      tableDraft.rows.forEach(row => row.push(""));
      renderTableGrid();
    };
  }

  function interactionLabel(interaction) {
    return ({
      single_select: "Chọn một đáp án",
      multi_select: "Chọn nhiều đáp án",
      matching: "Ghép cặp",
      ordering: "Sắp xếp thứ tự",
      short_text: "Trả lời ngắn",
    })[interaction] || interaction;
  }

  function stimulusEditor(stimulus) {
    return `<div class="la-review-section"><h3>Bối cảnh học viên nhìn thấy</h3><div class="la-review-grid"><div class="la-review-field"><label for="la-quiz-stimulus-kind">Dạng bối cảnh</label><select id="la-quiz-stimulus-kind"><option value="none"${stimulus.kind === "none" ? " selected" : ""}>Không có bối cảnh riêng</option><option value="text"${stimulus.kind === "text" ? " selected" : ""}>Đoạn văn / tình huống</option><option value="formula"${stimulus.kind === "formula" ? " selected" : ""}>Công thức / dữ kiện tính toán</option><option value="table"${stimulus.kind === "table" ? " selected" : ""}>Bảng dữ liệu</option></select></div><div class="la-review-field"><label>Loại câu hỏi</label><div class="la-review-readonly"><span class="la-review-pill">${escapeHtml(interactionLabel(state.adapter.payload.interaction))}</span><span>Được giữ cố định để bảo toàn cấu trúc đáp án</span></div></div><div id="la-stimulus-text-wrap" class="la-review-field la-review-full"><label for="la-quiz-stimulus-text">Tình huống / dữ kiện</label><textarea id="la-quiz-stimulus-text">${escapeHtml(stimulus.text)}</textarea></div><div id="la-stimulus-formula-wrap" class="la-review-field la-review-full"><label for="la-quiz-stimulus-formula">Công thức / dữ kiện</label><textarea id="la-quiz-stimulus-formula">${escapeHtml(stimulus.formula)}</textarea></div><div id="la-stimulus-table-wrap" class="la-review-field la-review-full"><label>Bảng dữ liệu</label><div id="la-quiz-table-grid" class="la-review-table-scroll"></div><div class="la-review-table-actions"><button id="la-add-table-row" class="la-review-add" type="button">+ Thêm dòng</button><button id="la-add-table-column" class="la-review-add" type="button">+ Thêm cột</button></div><p class="la-review-table-help">Sửa trực tiếp từng ô; không cần nhập JSON hay dùng phím Tab.</p></div></div></div>`;
  }

  function setStimulusVisibility() {
    const kind = byId("la-quiz-stimulus-kind").value;
    byId("la-stimulus-text-wrap").classList.toggle("la-review-hidden", kind !== "text");
    byId("la-stimulus-formula-wrap").classList.toggle("la-review-hidden", kind !== "formula");
    byId("la-stimulus-table-wrap").classList.toggle("la-review-hidden", kind !== "table");
  }

  function collectStimulus() {
    const kind = byId("la-quiz-stimulus-kind").value;
    const stimulus = {kind, text: "", table_columns: [], table_rows: [], formula: ""};
    if (kind === "text") stimulus.text = requiredValue("la-quiz-stimulus-text", "Tình huống / dữ kiện");
    if (kind === "formula") stimulus.formula = requiredValue("la-quiz-stimulus-formula", "Công thức / dữ kiện");
    if (kind === "table") {
      syncTableDraft();
      if (!tableDraft.columns.length || !tableDraft.rows.length) throw new Error("Bảng cần ít nhất một cột và một dòng dữ liệu");
      if (tableDraft.rows.some(row => row.length !== tableDraft.columns.length)) throw new Error("Mỗi dòng dữ liệu phải đủ số cột");
      stimulus.table_columns = [...tableDraft.columns];
      stimulus.table_rows = tableDraft.rows.map(row => [...row]);
    }
    return stimulus;
  }

  function selectionEditor(question) {
    const inputType = question.interaction === "single_select" ? "radio" : "checkbox";
    const checked = new Set(question.correct_answer.selection_ids);
    return `<div class="la-review-section"><h3>Các lựa chọn và đáp án đúng</h3><div class="la-review-option-list">${question.choice_options.map((option, index) => `<div class="la-review-option"><input type="${inputType}" name="la-quiz-correct" data-la-correct="${escapeHtml(option.option_id)}" aria-label="Đánh dấu đáp án đúng"${checked.has(option.option_id) ? " checked" : ""}><label><span>Lựa chọn ${index + 1}</span><input data-la-option-text="${escapeHtml(option.option_id)}" value="${escapeHtml(option.text)}"></label></div>`).join("")}</div><p class="la-review-help">${question.interaction === "single_select" ? "Chọn đúng một đáp án." : "Chọn từ hai đáp án đúng trở lên."}</p></div>`;
  }

  function matchingEditor(question) {
    const mapping = Object.fromEntries(question.correct_answer.mappings.map(item => [item.left, item.right]));
    return `<div class="la-review-section"><h3>Các cặp ghép và đáp án đúng</h3><div class="la-review-option-list">${question.matching_left.map((left, index) => `<div class="la-review-option-row"><div><label for="la-left-${index}">Vế trái ${index + 1}</label><input id="la-left-${index}" data-la-left-text="${escapeHtml(left.option_id)}" value="${escapeHtml(left.text)}"></div><div><label for="la-map-${index}">Ghép đúng với</label><select id="la-map-${index}" data-la-map-left="${escapeHtml(left.option_id)}">${question.matching_right.map(right => `<option data-la-right-option="${escapeHtml(right.option_id)}" value="${escapeHtml(right.option_id)}"${mapping[left.option_id] === right.option_id ? " selected" : ""}>${escapeHtml(right.text)}</option>`).join("")}</select></div></div>`).join("")}</div><div class="la-review-section"><h3>Nội dung các lựa chọn bên phải</h3><div class="la-review-option-list">${question.matching_right.map((right, index) => `<div class="la-review-field"><label for="la-right-${index}">Lựa chọn ${index + 1}</label><input id="la-right-${index}" data-la-right-text="${escapeHtml(right.option_id)}" value="${escapeHtml(right.text)}"></div>`).join("")}</div></div></div>`;
  }

  function setupMatchingControls() {
    modalBody.oninput = event => {
      const input = event.target.closest("[data-la-right-text]");
      if (!input) return;
      const id = input.dataset.laRightText;
      modalBody.querySelectorAll(`[data-la-right-option="${CSS.escape(id)}"]`).forEach(option => {
        option.textContent = input.value || "(Chưa có nội dung)";
      });
    };
  }

  function orderingEditor(question) {
    const byOption = Object.fromEntries(question.ordering_options.map(option => [option.option_id, option]));
    const ordered = question.correct_answer.ordering.map(id => byOption[id]).filter(Boolean);
    question.ordering_options.forEach(option => { if (!ordered.includes(option)) ordered.push(option); });
    return `<div class="la-review-section"><h3>Thứ tự đáp án đúng</h3><p class="la-review-help">Sửa nội dung và dùng mũi tên để đặt thứ tự chuẩn.</p><div id="la-order-list" class="la-review-option-list">${ordered.map((option, index) => `<div class="la-review-order-row" data-la-order-id="${escapeHtml(option.option_id)}"><span class="la-review-order-index">${index + 1}</span><input data-la-order-text="${escapeHtml(option.option_id)}" value="${escapeHtml(option.text)}"><span class="la-review-mini-actions"><button class="la-review-mini" data-la-move="up" type="button" aria-label="Đưa lên">↑</button><button class="la-review-mini" data-la-move="down" type="button" aria-label="Đưa xuống">↓</button></span></div>`).join("")}</div></div>`;
  }

  function rubricRow(point = {criterion: "", points: 1}) {
    return `<div class="la-review-rubric-row" data-la-rubric-row><div><label>Tiêu chí chấm</label><input data-la-rubric-criterion value="${escapeHtml(point.criterion)}"></div><div><label>Điểm</label><input data-la-rubric-points type="number" min="1" max="100" value="${escapeHtml(point.points)}"></div><button class="la-review-remove" data-la-remove-rubric type="button" aria-label="Xóa tiêu chí">×</button></div>`;
  }

  function shortTextEditor(question) {
    return `<div class="la-review-section"><h3>Đáp án mẫu và cách chấm</h3><div class="la-review-field"><label for="la-quiz-text-answer">Đáp án mẫu</label><textarea id="la-quiz-text-answer">${escapeHtml(question.correct_answer.text)}</textarea></div><div class="la-review-field"><label>Rubric</label><div id="la-rubric-list">${question.rubric.map(rubricRow).join("")}</div><button id="la-add-rubric" class="la-review-add" type="button">+ Thêm tiêu chí chấm</button></div></div>`;
  }

  function setupOrderingControls() {
    const list = byId("la-order-list");
    if (!list) return;
    const refresh = () => {
      const rows = [...list.querySelectorAll("[data-la-order-id]")];
      rows.forEach((row, index) => {
        row.querySelector(".la-review-order-index").textContent = index + 1;
        row.querySelector('[data-la-move="up"]').disabled = index === 0;
        row.querySelector('[data-la-move="down"]').disabled = index === rows.length - 1;
      });
    };
    list.onclick = event => {
      const button = event.target.closest("[data-la-move]");
      if (!button) return;
      const row = button.closest("[data-la-order-id]");
      if (button.dataset.laMove === "up" && row.previousElementSibling) list.insertBefore(row, row.previousElementSibling);
      if (button.dataset.laMove === "down" && row.nextElementSibling) list.insertBefore(row.nextElementSibling, row);
      refresh();
    };
    refresh();
  }

  function setupRubricControls() {
    const list = byId("la-rubric-list");
    if (!list) return;
    list.onclick = event => {
      const button = event.target.closest("[data-la-remove-rubric]");
      if (button) button.closest("[data-la-rubric-row]").remove();
    };
    byId("la-add-rubric").onclick = () => {
      const holder = document.createElement("div");
      holder.innerHTML = rubricRow();
      list.appendChild(holder.firstElementChild);
    };
  }

  function collectQuizResponse(next) {
    const interaction = next.interaction;
    const answer = {selection_ids: [], ordering: [], mappings: [], text: ""};
    if (interaction === "single_select" || interaction === "multi_select") {
      next.choice_options.forEach(option => {
        const input = modalBody.querySelector(`[data-la-option-text="${CSS.escape(option.option_id)}"]`);
        option.text = input.value.trim();
        if (!option.text) throw new Error("Nội dung lựa chọn không được để trống");
      });
      answer.selection_ids = [...modalBody.querySelectorAll("[data-la-correct]:checked")].map(input => input.dataset.laCorrect);
      if (interaction === "single_select" && answer.selection_ids.length !== 1) throw new Error("Câu single select cần đúng một đáp án đúng");
      if (interaction === "multi_select" && answer.selection_ids.length < 2) throw new Error("Câu multi select cần ít nhất hai đáp án đúng");
      next.rubric = [];
    } else if (interaction === "matching") {
      next.matching_left.forEach(option => {
        const input = modalBody.querySelector(`[data-la-left-text="${CSS.escape(option.option_id)}"]`);
        option.text = input.value.trim();
        if (!option.text) throw new Error("Vế trái không được để trống");
        const select = modalBody.querySelector(`[data-la-map-left="${CSS.escape(option.option_id)}"]`);
        answer.mappings.push({left: option.option_id, right: select.value});
      });
      next.matching_right.forEach(option => {
        const input = modalBody.querySelector(`[data-la-right-text="${CSS.escape(option.option_id)}"]`);
        option.text = input.value.trim();
        if (!option.text) throw new Error("Lựa chọn bên phải không được để trống");
      });
      next.rubric = [];
    } else if (interaction === "ordering") {
      const rows = [...byId("la-order-list").querySelectorAll("[data-la-order-id]")];
      const textById = {};
      rows.forEach(row => {
        const value = row.querySelector("[data-la-order-text]").value.trim();
        if (!value) throw new Error("Bước trong câu sắp xếp không được để trống");
        textById[row.dataset.laOrderId] = value;
        answer.ordering.push(row.dataset.laOrderId);
      });
      next.ordering_options.forEach(option => { option.text = textById[option.option_id]; });
      next.rubric = [];
    } else if (interaction === "short_text") {
      answer.text = requiredValue("la-quiz-text-answer", "Đáp án mẫu");
      next.rubric = [...modalBody.querySelectorAll("[data-la-rubric-row]")].map(row => {
        const criterion = row.querySelector("[data-la-rubric-criterion]").value.trim();
        const points = Number(row.querySelector("[data-la-rubric-points]").value);
        if (!criterion) throw new Error("Tiêu chí chấm không được để trống");
        if (!Number.isInteger(points) || points < 1) throw new Error("Điểm rubric phải là số nguyên từ 1 trở lên");
        return {criterion, points};
      });
      if (!next.rubric.length) throw new Error("Câu short text cần ít nhất một tiêu chí chấm");
    } else {
      throw new Error(`Chưa có form biên tập cho dạng ${interaction}`);
    }
    next.correct_answer = answer;
  }

  function quizHintRow(hint, index) {
    const kinds = [["cue", "Gợi mở"], ["strategy", "Hướng giải"], ["step", "Một bước hỗ trợ"]];
    return `<div class="la-review-hint-row" data-la-hint-id="${escapeHtml(hint.hint_id)}"><div class="la-review-hint-head"><strong data-la-hint-number>Gợi ý ${index + 1}</strong><div class="la-review-mini-actions"><button class="la-review-mini" data-la-hint-action="up" type="button" aria-label="Đưa gợi ý lên">↑</button><button class="la-review-mini" data-la-hint-action="down" type="button" aria-label="Đưa gợi ý xuống">↓</button><button class="la-review-remove" data-la-hint-action="remove" type="button" aria-label="Xóa gợi ý">×</button></div></div><label>Loại hỗ trợ<select data-la-hint-kind>${kinds.map(([value, label]) => `<option value="${value}"${hint.kind === value ? " selected" : ""}>${label}</option>`).join("")}</select></label><label>Nội dung gợi ý<textarea data-la-hint-text>${escapeHtml(hint.text)}</textarea></label><span class="la-review-help">${escapeHtml(hint.hint_id)}</span></div>`;
  }

  function quizHintEditor(question) {
    const hints = question.hints || [];
    return `<div class="la-review-section"><h3>Gợi ý theo từng lần bấm</h3><p class="la-review-help">Chỉ gợi hướng suy nghĩ, không chép đáp án. Số gợi ý tùy câu; thứ tự ở đây là thứ tự được mở. Lời giải vẫn nằm riêng bên dưới.</p><div id="la-quiz-hints">${hints.map(quizHintRow).join("")}</div><button id="la-add-hint" class="la-review-add" type="button">+ Thêm gợi ý</button><div id="la-hint-absence-wrap" class="la-review-field${hints.length ? " la-review-hidden" : ""}"><label for="la-quiz-hint-absence">Vì sao không có gợi ý?</label><textarea id="la-quiz-hint-absence" placeholder="Nếu gợi ý sẽ lộ ngay đáp án hoặc không có giá trị hỗ trợ, giải thích ở đây.">${escapeHtml(question.hint_absence_reason || "")}</textarea><p class="la-review-help">Bản cũ chưa có hint có thể giữ nguyên. Với câu đã soạn hint, nếu bỏ hết cần nêu lý do.</p></div></div>`;
  }

  function newQuizHintId(questionId, reserved) {
    let id;
    do {
      const suffix = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
      id = `${questionId}-hint-${suffix}`;
    } while (reserved.has(id));
    reserved.add(id);
    return id;
  }

  function setupQuizHintControls(question) {
    const list = byId("la-quiz-hints"), reserved = new Set((question.hints || []).map(hint => hint.hint_id));
    const refresh = () => {
      const rows = [...list.querySelectorAll("[data-la-hint-id]")];
      rows.forEach((row, index) => {
        row.querySelector("[data-la-hint-number]").textContent = `Gợi ý ${index + 1}`;
        row.querySelector('[data-la-hint-action="up"]').disabled = index === 0;
        row.querySelector('[data-la-hint-action="down"]').disabled = index === rows.length - 1;
      });
      byId("la-hint-absence-wrap").classList.toggle("la-review-hidden", rows.length > 0);
    };
    list.onclick = event => {
      const button = event.target.closest("[data-la-hint-action]");
      if (!button) return;
      const row = button.closest("[data-la-hint-id]");
      if (button.dataset.laHintAction === "remove") row.remove();
      if (button.dataset.laHintAction === "up" && row.previousElementSibling) list.insertBefore(row, row.previousElementSibling);
      if (button.dataset.laHintAction === "down" && row.nextElementSibling) list.insertBefore(row.nextElementSibling, row);
      refresh();
    };
    byId("la-add-hint").onclick = () => {
      const holder = document.createElement("div");
      holder.innerHTML = quizHintRow({hint_id: newQuizHintId(question.question_id, reserved), kind: "cue", text: ""}, list.children.length);
      list.appendChild(holder.firstElementChild);
      refresh();
      list.lastElementChild.querySelector("[data-la-hint-text]").focus();
    };
    refresh();
  }

  function collectQuizHints(next, baseline) {
    const rows = [...byId("la-quiz-hints").querySelectorAll("[data-la-hint-id]")];
    const hints = rows.map(row => {
      const text = row.querySelector("[data-la-hint-text]").value.trim();
      const kind = row.querySelector("[data-la-hint-kind]").value;
      if (!text) throw new Error("Gợi ý không được để trống; hãy viết nội dung hoặc xóa gợi ý.");
      return {hint_id: row.dataset.laHintId, kind, text};
    });
    const reason = byId("la-quiz-hint-absence").value.trim();
    const hadContract = Object.hasOwn(baseline, "hints") || Object.hasOwn(baseline, "hint_absence_reason");
    if (!hadContract && !hints.length && !reason) return;
    if (!hints.length && !reason) throw new Error("Cần giải thích vì sao câu này không có gợi ý.");
    next.hints = hints;
    next.hint_absence_reason = hints.length ? null : reason;
  }

  function openQuizEditor(payload, revision) {
    let responseHtml = "";
    if (payload.interaction === "single_select" || payload.interaction === "multi_select") responseHtml = selectionEditor(payload);
    else if (payload.interaction === "matching") responseHtml = matchingEditor(payload);
    else if (payload.interaction === "ordering") responseHtml = orderingEditor(payload);
    else if (payload.interaction === "short_text") responseHtml = shortTextEditor(payload);
    else throw new Error(`Chưa có form biên tập cho dạng ${payload.interaction}`);
    const slot = typeof DATA !== "undefined"
      ? (DATA.quiz?.assessment_slots || []).find(item => item.slot_id === payload.slot_id)
      : null;
    const slotHtml = slot ? `<div class="la-review-readonly"><span class="la-review-pill">${escapeHtml(slot.slot_id)}</span><span>variant ${escapeHtml(payload.variant_index)} / ${escapeHtml(slot.variant_count)} · ${escapeHtml(slot.evidence_intent)}</span></div>` : "";
    const provenanceNote = `Liên kết tới ${escapeHtml(payload.kc_id)}, ${payload.evidence_refs.length} phần nguồn PDF và ${(payload.context_evidence_refs || []).length} phần ngữ cảnh giảng viên được giữ nguyên. Định danh slot / variant không đổi.`;
    openModal(
      `Sửa ${payload.question_id} · ${payload.title}`,
      `<div class="la-review-intro"><strong>Sửa câu hỏi bằng nội dung người học nhìn thấy.</strong><p>KC, nguồn đối chiếu và mã hệ thống được giữ nguyên. Bạn không cần đọc hoặc chỉnh JSON. Lưu revision sẽ yêu cầu kiểm định lại bản sửa.</p></div>${slotHtml}<div class="la-review-grid"><div class="la-review-field la-review-full"><label for="la-quiz-title">Tên câu hỏi</label><input id="la-quiz-title" maxlength="240" value="${escapeHtml(payload.title)}"></div><div class="la-review-field la-review-full"><label for="la-quiz-prompt">Câu hỏi / yêu cầu</label><textarea id="la-quiz-prompt">${escapeHtml(payload.prompt)}</textarea></div></div>${stimulusEditor(payload.stimulus)}${responseHtml}${quizHintEditor(payload)}<div class="la-review-section"><div class="la-review-field"><label for="la-quiz-explanation">Giải thích đáp án</label><textarea id="la-quiz-explanation">${escapeHtml(payload.answer_explanation)}</textarea></div></div><p class="la-review-technical-note">${provenanceNote}</p>${noteField()}`,
      editorFooter(),
      {wide: true},
    );
    byId("la-quiz-stimulus-kind").onchange = setStimulusVisibility;
    setupTableControls(payload.stimulus);
    setStimulusVisibility();
    setupMatchingControls();
    setupOrderingControls();
    setupRubricControls();
    setupQuizHintControls(payload);
    bindEditorSave(() => {
      const next = deepCopy(payload);
      next.title = requiredValue("la-quiz-title", "Tên câu hỏi");
      next.prompt = requiredValue("la-quiz-prompt", "Câu hỏi / yêu cầu");
      next.stimulus = collectStimulus();
      next.answer_explanation = requiredValue("la-quiz-explanation", "Giải thích đáp án");
      collectQuizResponse(next);
      collectQuizHints(next, payload);
      return next;
    }, revision);
  }

  async function openEditor() {
    await ensureReviewer();
    await loadCurrentTarget({force: true});
    const {payload, revision} = await effectivePayload();
    if (state.adapter.stage === "kc") openKcEditor(payload, revision);
    else if (state.adapter.stage === "quiz") openQuizEditor(payload, revision);
    else openExtractionEditor(payload, revision);
  }

  async function openReject() {
    const shown = await decisionSnapshot();
    await ensureReviewer();
    openModal(
      `Từ chối ${state.adapter.label}`,
      `<div class="la-review-field"><label for="la-review-note">Lý do từ chối</label><textarea id="la-review-note" maxlength="2000" placeholder="Nêu vấn đề cần sửa…"></textarea></div><div id="la-review-error" class="la-review-error"></div>`,
      `<button id="la-review-reject-cancel" class="la-review-secondary" type="button">Hủy</button><button id="la-review-reject-save" class="la-review-danger" type="button">Xác nhận từ chối</button>`,
    );
    byId("la-review-note").focus();
    byId("la-review-reject-cancel").onclick = closeModal;
    byId("la-review-reject-save").onclick = async () => {
      const note = byId("la-review-note").value.trim();
      if (!note) { showModalError("Cần ghi lý do từ chối"); return; }
      const button = byId("la-review-reject-save"); button.disabled = true;
      try { await makeDecision("reject", note, shown); closeModal(); }
      catch (error) { showModalError(error.message); button.disabled = false; }
    };
  }

  function openHistory() {
    const rows = state.events.map(event => {
      const label = event.action === "approve" ? "Duyệt" : event.action === "reject" ? "Từ chối" : "Sửa";
      const time = new Date(event.created_at).toLocaleString("vi-VN");
      return `<article class="la-review-event"><div class="la-review-event-head"><strong>${label}</strong><span>${escapeHtml(event.reviewer_name)}</span><time>${escapeHtml(time)}</time></div>${event.note ? `<p>${escapeHtml(event.note)}</p>` : ""}<p><code>${escapeHtml(event.payload_sha256.slice(0, 12))}…</code></p></article>`;
    }).join("") || '<p class="la-review-help">Chưa có lịch sử review cho mục này.</p>';
    openModal(`Lịch sử · ${state.adapter.label}`, rows, '<button id="la-review-history-close" class="la-review-primary" type="button">Xong</button>');
    byId("la-review-history-close").onclick = closeModal;
  }

  function broadcastUpdate() {
    try { new BroadcastChannel("learning-authoring-review").postMessage({runId: config.runId}); }
    catch { /* older browser */ }
  }

  byId("la-review-close").onclick = cancelActiveModal;
  overlay.onclick = event => { if (event.target === overlay) cancelActiveModal(); };
  personButton.onclick = () => askForName().catch(() => {});
  byId("la-review-history").onclick = openHistory;
  document.querySelector('[data-la-action="edit"]').onclick = () => openEditor().catch(error => { if (error.message !== "cancelled") alert(error.message); });
  document.querySelector('[data-la-action="reject"]').onclick = () => openReject().catch(error => { if (error.message !== "cancelled") alert(error.message); });
  document.querySelector('[data-la-action="approve"]').onclick = async () => {
    setLoading(true);
    try { const shown = await decisionSnapshot(); await ensureReviewer(); await makeDecision("approve", null, shown); }
    catch (error) { if (error.message !== "cancelled") alert(error.message); }
    finally { setLoading(false); }
  };
  addEventListener("keydown", event => { if (event.key === "Escape" && overlay.classList.contains("open")) cancelActiveModal(); });

  try {
    const channel = new BroadcastChannel("learning-authoring-review");
    channel.onmessage = event => { if (event.data?.runId === config.runId) loadCurrentTarget({force: true}); };
  } catch { /* older browser */ }

  renderPerson();
  loadCurrentTarget({force: true});
  setInterval(() => {
    const adapter = detectAdapter();
    if (adapter && targetId(adapter) !== state.lastKey) loadCurrentTarget({force: true});
  }, 500);
  setInterval(() => loadCurrentTarget({force: true}), 15000);
})();
