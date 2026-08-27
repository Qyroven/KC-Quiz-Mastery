(() => {
  "use strict";

  const config = window.LEARNING_AUTHORING_REVIEW;
  if (!config || !config.enabled) return;

  const projectKey = new URL(config.supabaseUrl).hostname;
  const sessionStorageKey = `la-review-session:${projectKey}`;
  const nameStorageKey = `la-review-name:${projectKey}`;
  const baselineByTarget = new Map();
  const appliedRevisionByTarget = new Map();
  const kcChoiceByPage = new Map();
  const state = {
    adapter: null,
    events: [],
    session: readStoredSession(),
    displayName: localStorage.getItem(nameStorageKey) || "",
    loading: false,
    lastKey: "",
  };

  const css = `
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
    .la-review-sheet label{display:block;margin:0 0 6px;color:#667085;font-size:12px;font-weight:650}.la-review-sheet input,.la-review-sheet textarea{width:100%;border:1px solid #d0d5dd;border-radius:9px;padding:10px 11px;font:inherit;color:#182033;background:#fff}.la-review-sheet textarea{min-height:100px;resize:vertical}.la-review-sheet textarea.la-json{min-height:430px;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;tab-size:2}.la-review-field+.la-review-field{margin-top:14px}
    .la-review-error{display:none;margin-top:10px;border-left:3px solid #c84b4b;background:#fff0f0;padding:9px 10px;color:#8f3030}.la-review-error.show{display:block}.la-review-help{color:#667085;font-size:12px}.la-review-spacer{flex:1}.la-review-secondary,.la-review-primary,.la-review-danger{border:0;border-radius:8px;padding:9px 13px;font:inherit;font-weight:650;cursor:pointer}.la-review-secondary{background:#eef0f3;color:#303846}.la-review-primary{background:#2864a5;color:#fff}.la-review-danger{background:#fff0f0;color:#a74343}.la-review-event{border:1px solid #e1e5eb;border-radius:10px;padding:11px;margin-bottom:9px}.la-review-event-head{display:flex;align-items:center;gap:8px}.la-review-event-head time{margin-left:auto;color:#667085;font-size:11px}.la-review-event p{margin:7px 0 0;color:#526071}.la-review-event code{font-size:11px;color:#667085}
    @media(max-width:760px){:root{--la-review-height:112px}#la-review-bar{flex-wrap:wrap;padding:8px}.la-review-target{flex-basis:100%}.la-review-status{margin-left:auto}.la-review-person{max-width:120px;overflow:hidden;text-overflow:ellipsis}.la-review-action{padding:7px 9px}}
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

  function revisionMatchesAdapter(adapter, payload) {
    if (!isObject(payload) || payload[adapter.identityField] !== adapter.identityValue) return false;
    if (adapter.stage === "extraction") {
      return typeof payload.role === "string" && Array.isArray(payload.blocks) &&
        Array.isArray(payload.reading_order) && isObject(payload.page_note) && Array.isArray(payload.warnings);
    }
    if (adapter.stage === "kc" && adapter.itemType === "leaf_kc") {
      return typeof payload.group_id === "string" && typeof payload.name === "string" &&
        typeof payload.semantic_form === "string" && typeof payload.knowledge_description === "string" &&
        typeof payload.observable_claim === "string" && Array.isArray(payload.source_evidence) &&
        isObject(payload.assessment_boundary) && Array.isArray(payload.assessment_boundary.included) &&
        Array.isArray(payload.assessment_boundary.excluded) && typeof payload.status === "string" &&
        Array.isArray(payload.warning_codes);
    }
    if (adapter.stage === "kc" && adapter.itemType === "page_audit") {
      return typeof payload.classification === "string" && typeof payload.summary === "string" &&
        Array.isArray(payload.source_block_ids) && Array.isArray(payload.kc_ids) &&
        Array.isArray(payload.warning_codes);
    }
    if (adapter.stage === "quiz") {
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
    try { return JSON.parse(localStorage.getItem(sessionStorageKey) || "null"); }
    catch { return null; }
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
    let session = state.session || readStoredSession();
    if (session?.access_token && Number(session.expires_at || 0) > Date.now() / 1000 + 60) {
      return session;
    }
    if (session?.refresh_token) {
      try { return await refreshSession(session); }
      catch { localStorage.removeItem(sessionStorageKey); state.session = null; }
    }
    return createAnonymousSession();
  }

  function openModal(title, bodyHtml, footerHtml) {
    modalCancelHandler = null;
    modalTitle.textContent = title;
    modalBody.innerHTML = bodyHtml;
    modalFoot.innerHTML = footerHtml;
    overlay.classList.add("open");
  }

  function closeModal() {
    overlay.classList.remove("open");
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
    localStorage.setItem(nameStorageKey, clean);
    renderPerson();
    return clean;
  }

  function askForName() {
    return new Promise((resolve, reject) => {
      openModal(
        "Bạn đang review với tên gì?",
        `<div class="la-review-field"><label for="la-review-name">Tên hiển thị</label><input id="la-review-name" maxlength="80" autocomplete="name" value="${escapeHtml(state.displayName)}" placeholder="Ví dụ: Quỳên"><p class="la-review-help">Không cần email, mật khẩu hay tài khoản. Tên này được ghi vào lịch sử review.</p></div><div id="la-review-error" class="la-review-error"></div>`,
        `<button id="la-review-new-person" class="la-review-secondary" type="button">Người review khác</button><span class="la-review-spacer"></span><button id="la-review-name-cancel" class="la-review-secondary" type="button">Hủy</button><button id="la-review-name-save" class="la-review-primary" type="button">Bắt đầu review</button>`,
      );
      const input = byId("la-review-name");
      input.focus(); input.select();
      const cancel = () => reject(new Error("cancelled"));
      modalCancelHandler = cancel;
      byId("la-review-name-cancel").onclick = cancelActiveModal;
      byId("la-review-new-person").onclick = () => {
        localStorage.removeItem(sessionStorageKey);
        localStorage.removeItem(nameStorageKey);
        state.session = null; state.displayName = ""; input.value = ""; input.focus();
      };
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
    if (state.displayName) {
      try { await saveProfile(state.displayName); return state.displayName; }
      catch { /* recreate/profile through the modal */ }
    }
    return askForName();
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
        apply(payload) { Object.keys(question).forEach(key => delete question[key]); Object.assign(question, deepCopy(payload)); render(); },
      };
    }
    if (typeof proposal !== "undefined" && typeof selected !== "undefined" && typeof evidenceByPage !== "undefined") {
      const ids = [...new Set((evidenceByPage[selected] || []).map(row => row.kc.kc_id))];
      let chosen = kcChoiceByPage.get(selected);
      if (!ids.includes(chosen)) chosen = ids[0];
      if (chosen) {
        kcChoiceByPage.set(selected, chosen);
        const kc = kcById[chosen];
        return {
          stage: "kc", itemType: "leaf_kc", itemKey: chosen,
          label: `${chosen} · ${kc.name}`, choices: ids,
          identityField: "kc_id", identityValue: chosen, payload: deepCopy(kc),
          apply(payload) { Object.keys(kc).forEach(key => delete kc[key]); Object.assign(kc, deepCopy(payload)); rebuildKcIndexes(); render(); },
        };
      }
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
      body: {
        p_run_id: config.runId,
        p_stage: adapter.stage,
        p_item_type: adapter.itemType,
        p_item_key: adapter.itemKey,
        p_base_artifact_sha256: baseArtifactSha256,
      },
    })) || [];
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
    document.body.dataset.laReviewStage = adapter.stage;
    targetLabel.textContent = adapter.label;
    renderChoice(adapter);
    setLoading(true);
    try {
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
      await renderStatus();
    } catch (error) {
      statusNode.className = "la-review-status reject";
      statusNode.textContent = `Không sync được: ${error.message}`;
    } finally { setLoading(false); }
  }

  function renderChoice(adapter) {
    if (!adapter.choices || adapter.choices.length < 2) { targetChoice.innerHTML = ""; return; }
    targetChoice.innerHTML = `<select aria-label="Chọn KC trên slide">${adapter.choices.map(id => `<option value="${escapeHtml(id)}"${id === adapter.itemKey ? " selected" : ""}>${escapeHtml(id)}</option>`).join("")}</select>`;
    targetChoice.querySelector("select").onchange = event => {
      kcChoiceByPage.set(selected, event.target.value);
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
    personButton.textContent = state.displayName ? `👤 ${state.displayName}` : "Nhập tên để review";
  }

  function setLoading(loading) {
    state.loading = loading;
    actionButtons.forEach(button => button.disabled = loading);
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

  async function makeDecision(action, note = null) {
    await loadCurrentTarget({force: true});
    const adapter = state.adapter;
    const {revision} = await effectivePayload();
    await insertEvent({
      run_id: config.runId, stage: adapter.stage, item_type: adapter.itemType,
      item_key: adapter.itemKey, action, note,
      revision_payload: null, target_revision_id: revision?.id || null,
    });
    state.lastKey = "";
    await loadCurrentTarget({force: true});
    broadcastUpdate();
  }

  async function openEditor() {
    await ensureReviewer();
    await loadCurrentTarget({force: true});
    const {payload, revision} = await effectivePayload();
    openModal(
      `Sửa ${state.adapter.label}`,
      `<div class="la-review-field"><label for="la-review-json">Revision JSON</label><textarea id="la-review-json" class="la-json" spellcheck="false"></textarea><p class="la-review-help">Raw output vẫn bất biến. Lưu ở đây tạo một revision mới dùng chung cho mọi reviewer.</p></div><div class="la-review-field"><label for="la-review-note">Ghi chú thay đổi (không bắt buộc)</label><textarea id="la-review-note" maxlength="1000" placeholder="Bạn đã sửa gì?"></textarea></div><div id="la-review-error" class="la-review-error"></div>`,
      `<button id="la-review-edit-cancel" class="la-review-secondary" type="button">Hủy</button><button id="la-review-edit-save" class="la-review-primary" type="button">Lưu revision</button>`,
    );
    byId("la-review-json").value = JSON.stringify(payload, null, 2);
    byId("la-review-edit-cancel").onclick = closeModal;
    byId("la-review-edit-save").onclick = async () => {
      const button = byId("la-review-edit-save"); button.disabled = true;
      try {
        const parsed = JSON.parse(byId("la-review-json").value);
        await saveRevision(parsed, byId("la-review-note").value.trim(), revision?.id || null);
        closeModal();
      } catch (error) { showModalError(error.message); button.disabled = false; }
    };
  }

  async function openReject() {
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
      try { await makeDecision("reject", note); closeModal(); }
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
    try { await ensureReviewer(); await makeDecision("approve"); }
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
