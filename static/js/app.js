(function () {
  "use strict";

  // ── element refs ──────────────────────────────────────────────
  const loginScreen    = document.getElementById("login-screen");
  const appScreen      = document.getElementById("app-screen");
  const authForm       = document.getElementById("auth-form");
  const authTabs       = document.querySelectorAll(".auth-tab");
  const authTitle      = document.getElementById("auth-title");
  const authSub        = document.getElementById("auth-sub");
  const usernameField  = document.getElementById("username-field");
  const usernameInput  = document.getElementById("username-input");
  const emailField     = document.getElementById("email-field");
  const emailInput     = document.getElementById("email-input");
  const passwordInput  = document.getElementById("password-input");
  const passwordToggle = document.getElementById("password-toggle");
  const authSubmit     = document.getElementById("auth-submit");
  const errorEl        = document.getElementById("login-error");
  const usernameDisplay= document.getElementById("username-display");
  const userAvatar     = document.getElementById("user-avatar");
  const logoutBtn      = document.getElementById("logout-btn");

  const newProjectBtn      = document.getElementById("new-project-btn");
  const projectListEl      = document.getElementById("project-list");
  const convScopeLabel     = document.getElementById("conv-scope-label");
  const scopeBackBtn       = document.getElementById("scope-back-btn");
  const projectModalOverlay= document.getElementById("project-modal-overlay");
  const projectForm        = document.getElementById("project-form");
  const projectNameInput   = document.getElementById("project-name-input");
  const projectDescInput   = document.getElementById("project-desc-input");
  const projectModalCancel = document.getElementById("project-modal-cancel");

  const messagesEl     = document.getElementById("messages");
  const emptyState     = document.getElementById("empty-state");
  const msgContainer   = document.getElementById("msg-container");
  const composer       = document.getElementById("composer");
  const messageInput   = document.getElementById("message-input");
  const sendBtn        = document.getElementById("send-btn");

  const newGeneralConvBtn = document.getElementById("new-general-conversation-btn");
  const headerTitle    = document.getElementById("chat-header-title");
  const generalConvList = document.getElementById("general-conversation-list");
  const projectConversationSection = document.getElementById("project-conversation-section");
  const projectConvList = document.getElementById("project-conversation-list");
  const projectChatLabel = document.getElementById("project-chat-label");

  const traceSidebar      = document.getElementById("trace-sidebar");
  const traceSidebarBody  = document.getElementById("trace-sidebar-body");
  const traceSubtitle     = document.getElementById("trace-sidebar-subtitle");
  const traceCollapseBtn  = document.getElementById("trace-collapse-btn");
  const traceHeaderToggle = document.getElementById("trace-header-toggle");

  const plusBtn        = document.getElementById("plus-btn");
  const plusWrap       = document.getElementById("plus-wrap") || (plusBtn && plusBtn.parentElement);
  const plusProgressValue = plusWrap
    ? plusWrap.querySelector(".plus-progress-value")
    : null;
  const plusMenu        = document.getElementById("plus-menu");
  const menuUploadBtn   = document.getElementById("menu-upload-btn");
  const menuWebSearchBtn = document.getElementById("menu-websearch-btn");
  const webSearchCheck  = document.getElementById("websearch-check");
  const menuAgentBtn    = document.getElementById("menu-agent-btn");
  const agentCheck      = document.getElementById("agent-check");
  const attachInput    = document.getElementById("attach-input");
  const uploadProgressEl = document.getElementById("upload-progress");
  let activeSessionId  = null;
  let authMode         = "login";

  // ── trace side panel state ──────────────────────────────────────
  let traceCounter           = 0;
  const traceStore           = new Map();   // msgId -> trace object
  let activeTraceMsgId       = null;
  let traceSidebarOpen       = window.innerWidth > 1080;
  let traceUserPinnedState   = false;        // becomes true once the user manually toggles
  traceHeaderToggle.classList.toggle("active", traceSidebarOpen);

  // live-streaming trace state (the in-flight request currently being rendered)
  let liveTraceSteps = [];

  // ── pipeline trace rendering ─────────────────────────────────
  const STEP_ICONS = {
    history: "🕘",
    rewrite: "✏️",
    orchestrate: "🧭",
    retrieval: "📚",
    web_search: "🌐",
    generate: "💬",
    refine: "🔁",
    fallback: "⚠️",
    file: "📄",
    agent_plan: "🤖",
    subagent_start: "▶",
    subagent_memory: "🧠",
    agent_aggregate: "🧩"
  };

  let activeProjectId = null; // null = general chats, else the selected project's id
  async function refreshConversations() {
    if (activeProjectId) {
      const res  = await authFetch(`/api/projects/${activeProjectId}/conversations`);
      const data = await res.json();
      if (res.ok) renderProjectConversations(data.conversations);
      return data;
    }
    const meRes  = await authFetch("/api/me");
    const meData = await meRes.json();
    if (meRes.ok) renderGeneralConversations(meData.conversations);
    return meData;
  }
  function nodeClass(step) {
    if (step.type === "evaluation") return step.relevant ? "node-success" : "node-danger";
    if (step.type === "fallback") return "node-warning";
    if (step.type === "subagent_memory") {
      if (step.status === "ok") return "node-success";
      if (step.status === "error" || step.status === "timeout") return "node-danger";
      if (step.status === "empty") return "node-warning";
    }
    return "node-accent";
  }

  function stepIcon(step) {
    if (step.type === "evaluation") return step.relevant ? "✓" : "✕";
    return STEP_ICONS[step.type] || "•";
  }

  function renderTraceStep(step) {
    let labelHtml = escapeHtml(step.label || step.type);
    let bodyHtml  = "";

    switch (step.type) {
      case "history": {
        const n = step.message_count || 0;
        bodyHtml = `<div class="trace-step-body">${n} previous message${n === 1 ? "" : "s"} in this conversation.</div>`;
        break;
      }

      case "rewrite": {
        if (step.original_query === step.rewritten_query) {
          bodyHtml = `<div class="trace-step-body">Query unchanged: "${escapeHtml(step.rewritten_query)}"</div>`;
        } else {
          bodyHtml = `<div class="trace-query-diff">
              <div class="orig">${escapeHtml(step.original_query)}</div>
              <div class="new">→ ${escapeHtml(step.rewritten_query)}</div>
            </div>`;
        }
        break;
      }

      case "orchestrate": {
        const badge = step.rag_needed
          ? `<span class="trace-badge neutral">Retrieval</span>`
          : `<span class="trace-badge neutral">Direct answer</span>`;
        labelHtml += " " + badge;
        bodyHtml = `<div class="trace-step-body">${
          step.rag_needed
            ? "This query needs the knowledge base."
            : "This query can be answered without retrieval."
        }</div>`;
        break;
      }

      case "retrieval": {
        const docs = (step.documents || []).map(d => {
          const meta = [];
          if (d.source) meta.push(escapeHtml(d.source));
          if (d.page !== null && d.page !== undefined && d.page !== "") meta.push("p. " + escapeHtml(d.page));
          const preview = escapeHtml(d.preview || "") + (d.truncated ? "…" : "");
          return `<div class="trace-doc">
              <div class="trace-doc-head">
                <span>${meta.join(" · ") || "Unknown source"}</span>
                <span class="trace-doc-score">${typeof d.score === "number" ? d.score.toFixed(4) : ""}</span>
              </div>
              <div class="trace-doc-preview">${preview}</div>
            </div>`;
        }).join("");
        bodyHtml = `<div class="trace-step-body">Query sent to retriever: "${escapeHtml(step.query)}"</div>
          <div class="trace-docs">${docs}</div>`;
        break;
      }

      case "web_search": {
        const results = (step.results || []).map((result, idx) => {
          const title = escapeHtml(result.title || result.url || `Result ${idx + 1}`);
          const url = escapeHtml(result.url || "");
          const snippet = escapeHtml(result.snippet || "");
          return `<div class="trace-doc">
              <div class="trace-doc-head">
                ${url ? `<a class="trace-doc-link" href="${url}" target="_blank" rel="noopener">${title}</a>` : `<span>${title}</span>`}
              </div>
              ${snippet ? `<div class="trace-doc-preview">${snippet}</div>` : ""}
            </div>`;
        }).join("");
        bodyHtml = `<div class="trace-step-body">Performed web search for this query and found ${escapeHtml(String((step.results || []).length))} result${(step.results || []).length === 1 ? "" : "s"}.</div>
          <div class="trace-docs">${results}</div>`;
        break;
      }

      case "evaluation": {
        const badge = step.relevant
          ? `<span class="trace-badge yes">Relevant</span>`
          : `<span class="trace-badge no">Not relevant</span>`;
        labelHtml += " " + badge;
        if (step.feedback) {
          bodyHtml = `<div class="trace-feedback">${escapeHtml(step.feedback)}</div>`;
        }
        break;
      }

      case "generate": {
        const tag = step.response_type === "grounded"
          ? "Grounded in retrieved documents."
          : "Answered directly, without retrieval.";
        bodyHtml = `<div class="trace-step-body">${tag}</div>`;
        break;
      }

      case "refine": {
        bodyHtml = `<div class="trace-step-body">New query for next attempt: "${escapeHtml(step.new_query)}"</div>`;
        break;
      }

      case "fallback": {
        bodyHtml = `<div class="trace-step-body">${
          step.reason === "retry_limit_reached"
            ? `No relevant context found after ${step.retries_used ?? ""} retr${(step.retries_used ?? 0) === 1 ? "y" : "ies"} — returned a safe fallback response.`
            : "Returned a safe fallback response."
        }</div>`;
        break;
      }

      case "agent_plan": {
        const subs = (step.subtasks || []).map(s =>
          `<div class="trace-doc"><div class="trace-doc-head"><span>${escapeHtml(s.id)} · ${escapeHtml(s.type)}</span></div>
            <div class="trace-doc-preview">${escapeHtml(s.instruction || "")}</div></div>`
        ).join("");
        bodyHtml = `<div class="trace-step-body">${
          step.subtasks
            ? `Planned ${step.subtasks.length} sub-agent task${step.subtasks.length === 1 ? "" : "s"}.`
            : "Parent is planning subtasks."
        }</div>${subs ? `<div class="trace-docs">${subs}</div>` : ""}`;
        break;
      }

      case "subagent_start": {
        bodyHtml = `<div class="trace-step-body">${escapeHtml(step.instruction || "Running isolated subtask.")}
          <div class="trace-doc-preview">Tools: ${escapeHtml((step.tools || []).join(", ") || "none")}</div>
        </div>`;
        break;
      }

      case "subagent_memory": {
        const badge = `<span class="trace-badge ${step.status === "ok" ? "yes" : "no"}">${escapeHtml(step.status || "?")}</span>`;
        labelHtml += " " + badge;
        const sources = (step.sources_used || []).map(s => {
          const bits = [];
          if (s.source) bits.push(escapeHtml(s.source));
          if (s.page !== null && s.page !== undefined && s.page !== "") bits.push("p. " + escapeHtml(String(s.page)));
          return bits.join(" · ");
        }).filter(Boolean);
        bodyHtml = `<div class="trace-step-body">${escapeHtml(step.result_summary || "")}</div>
          ${sources.length ? `<div class="trace-docs">${sources.map(s => `<div class="trace-doc"><div class="trace-doc-head"><span>${s}</span></div></div>`).join("")}</div>` : ""}
          ${step.error ? `<div class="trace-feedback">${escapeHtml(step.error)}</div>` : ""}`;
        break;
      }

      case "agent_aggregate": {
        const statuses = step.statuses
          ? Object.entries(step.statuses).map(([id, st]) => `${escapeHtml(id)}=${escapeHtml(String(st))}`).join(", ")
          : "";
        bodyHtml = `<div class="trace-step-body">${
          step.memory_count !== undefined
            ? `Combining ${escapeHtml(String(step.memory_count))} memory object${step.memory_count === 1 ? "" : "s"}.`
            : (step.citation_count !== undefined
              ? `Assembled final answer (${escapeHtml(String(step.citation_count))} citation${step.citation_count === 1 ? "" : "s"}).`
              : "Aggregating sub-agent memories.")
        }${statuses ? `<div class="trace-doc-preview">${statuses}</div>` : ""}</div>`;
        break;
      }

      default:
        bodyHtml = "";
    }

    return `<div class="trace-step">
        <div class="trace-node ${nodeClass(step)}">${stepIcon(step)}</div>
        <div class="trace-step-content">
          <div class="trace-step-label">${labelHtml}</div>
          ${bodyHtml}
        </div>
      </div>`;
  }

  function buildTraceSummary(trace) {
    const attempts = trace.steps.filter(s => s.type === "retrieval").length;
    const finalStep = [...trace.steps].reverse().find(s => s.type === "generate" || s.type === "fallback");

    let outcomeLabel = "—", outcomeClass = "";
    if (finalStep) {
      if (finalStep.type === "fallback") { outcomeLabel = "Fallback"; outcomeClass = "outcome-warning"; }
      else if (finalStep.response_type === "grounded") { outcomeLabel = "Grounded"; outcomeClass = "outcome-success"; }
      else { outcomeLabel = "Direct"; outcomeClass = "outcome-accent"; }
    }

    return `<div class="trace-summary-grid">
        <div class="trace-summary-chip"><span class="chip-label">Path</span><span class="chip-value">${trace.rag_used ? "Retrieval" : "Direct"}</span></div>
        <div class="trace-summary-chip"><span class="chip-label">Attempts</span><span class="chip-value">${attempts}</span></div>
        <div class="trace-summary-chip"><span class="chip-label">Outcome</span><span class="chip-value ${outcomeClass}">${outcomeLabel}</span></div>
      </div>`;
  }

  function buildTraceHTML(trace) {
    if (!trace || !trace.steps || !trace.steps.length) return "";
    const stepsHtml = trace.steps.map(renderTraceStep).join("");
    return `${buildTraceSummary(trace)}<div class="trace-timeline">${stepsHtml}</div>`;
  }

  function truncateText(s, n) {
    if (!s) return "";
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  function tracePreviewText(trace) {
    const rewriteStep = trace.steps.find(s => s.type === "rewrite");
    return rewriteStep ? rewriteStep.original_query : "";
  }

  // ── trace side panel control ─────────────────────────────────
  function showTraceEmpty() {
    traceSidebarBody.innerHTML =
      '<div class="trace-empty">' +
        '<div class="trace-empty-icon">🧪</div>' +
        '<div class="trace-empty-title">No trace yet</div>' +
        '<div class="trace-empty-sub">Send a message and the pipeline\u2019s steps will show up here.</div>' +
      '</div>';
    traceSubtitle.textContent = "";
  }

  function setTraceSidebarOpen(open, opts) {
    opts = opts || {};
    if (opts.userInitiated) traceUserPinnedState = true;
    traceSidebarOpen = open;
    traceSidebar.classList.toggle("collapsed", !open);
    traceSidebar.classList.toggle("force-open", open);
    traceHeaderToggle.classList.toggle("active", open);
  }

  function selectTrace(msgId) {
    activeTraceMsgId = msgId;
    const trace = traceStore.get(msgId);
    if (!trace) { showTraceEmpty(); return; }

    traceSubtitle.textContent = truncateText(tracePreviewText(trace), 56);
    traceSidebarBody.innerHTML = DOMPurify.sanitize(buildTraceHTML(trace));
  }

  function resetTracePanel() {
    traceStore.clear();
    activeTraceMsgId = null;
    showTraceEmpty();
  }

  // ── live trace streaming (while a request is in flight) ──────
  // Renders steps into the sidebar the instant each one arrives over SSE,
  // before the final assistant message even exists yet.
  function startLiveTrace() {
    liveTraceSteps = [];
    activeTraceMsgId = null;

    traceSidebarBody.innerHTML = '<div class="trace-timeline" id="live-trace-timeline"></div>';
    traceSubtitle.textContent = "Running…";

    if (!traceUserPinnedState) setTraceSidebarOpen(true);
  }

  function appendLiveTraceStep(step) {
    liveTraceSteps.push(step);

    const timeline = document.getElementById("live-trace-timeline");
    if (timeline) {
      timeline.insertAdjacentHTML("beforeend", DOMPurify.sanitize(renderTraceStep(step)));
      traceSidebarBody.scrollTop = traceSidebarBody.scrollHeight;
    }

    if (step.type === "rewrite") {
      traceSubtitle.textContent = truncateText(step.original_query, 56);
    }
  }

  traceCollapseBtn.addEventListener("click", () => setTraceSidebarOpen(false, { userInitiated: true }));
  traceHeaderToggle.addEventListener("click", () => {
    if (traceSidebarOpen) { setTraceSidebarOpen(false, { userInitiated: true }); return; }
    if (activeTraceMsgId === null && traceStore.size) {
      selectTrace([...traceStore.keys()].pop());
    }
    setTraceSidebarOpen(true, { userInitiated: true });
  });

  // ── helpers ───────────────────────────────────────────────────
  function now() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function autoResize() {
    messageInput.style.height = "auto";
    messageInput.style.height = Math.min(messageInput.scrollHeight, 140) + "px";
  }

  function scrollBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function showEmpty() {
    emptyState.style.display  = "";
    msgContainer.style.display = "none";
    headerTitle.textContent    = "New chat";
  }

  function showChat() {
    emptyState.style.display  = "none";
    msgContainer.style.display = "";
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ── pipeline trace: message pill (links to side panel) ───────
  function attachTracePill(row, msgId, trace) {
    if (!trace || !trace.steps || !trace.steps.length) return;

    traceStore.set(msgId, trace);

    // Newest trace becomes the one shown; only force the panel open
    // automatically if the user hasn't explicitly closed it before.
    selectTrace(msgId);
    if (!traceUserPinnedState) setTraceSidebarOpen(true);
  }

  function reasoningDetailForStep(step) {
    let detail = "Completed";

    if (step.type === "rewrite") {
      detail = step.original_query === step.rewritten_query
        ? "Query stayed the same."
        : "Rewrote the request for better retrieval.";
    } else if (step.type === "orchestrate") {
      detail = step.rag_needed ? "Route selected retrieval." : "Route selected a direct answer.";
    } else if (step.type === "retrieval") {
      const count = Array.isArray(step.documents) ? step.documents.length : 0;
      detail = count ? `Fetched ${count} document${count === 1 ? "" : "s"} for context.` : "Fetched supporting context.";
    } else if (step.type === "evaluation") {
      detail = step.relevant ? "Retrieved context looked relevant." : "Retrieved context looked off-topic.";
    } else if (step.type === "web_search") {
      detail = "Performed a web search and collected the top results.";
    } else if (step.type === "generate") {
      detail = step.response_type === "grounded" ? "Generated a grounded response." : "Generated a direct response.";
    } else if (step.type === "fallback") {
      detail = "Used a safe fallback response.";
    } else if (step.type === "agent_plan") {
      detail = step.subtasks
        ? `Planned ${step.subtasks.length} sub-agent task${step.subtasks.length === 1 ? "" : "s"}.`
        : "Parent planning subtasks.";
    } else if (step.type === "subagent_start") {
      detail = `Started ${step.subtask_type || "sub-agent"} (${step.subtask_id || "?"}).`;
    } else if (step.type === "subagent_memory") {
      detail = step.result_summary
        ? String(step.result_summary).slice(0, 160)
        : `Sub-agent finished with status ${step.status || "unknown"}.`;
    } else if (step.type === "agent_aggregate") {
      detail = "Parent assembled the final answer from memories.";
    }

    return detail;
  }

  function buildReasoningItem(step) {
    const label = step.label || step.type || "Step";
    const detail = reasoningDetailForStep(step);
    return `<div class="msg-reasoning-item"><div class="msg-reasoning-label">${escapeHtml(label)}</div><div class="msg-reasoning-detail">${escapeHtml(detail)}</div></div>`;
  }

  function buildReasoningSummary(trace) {
    if (!trace || !trace.steps || !trace.steps.length) return "";

    const items = trace.steps.slice(0, 6).map(buildReasoningItem).join("");
    return `<div class="msg-reasoning-body">${items}</div>`;
  }

  function createReasoningPanel() {
    const reasoningWrap = document.createElement("div");
    reasoningWrap.className = "msg-reasoning";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "msg-reasoning-toggle";
    toggle.textContent = "Hide thinking";
    toggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const bodyEl = reasoningWrap.querySelector(".msg-reasoning-body");
      if (!bodyEl) return;
      const isExpanded = !bodyEl.hidden;
      bodyEl.hidden = isExpanded;
      bodyEl.style.display = isExpanded ? "none" : "flex";
      toggle.textContent = isExpanded ? "Show thinking" : "Hide thinking";
    });

    const reasoningBody = document.createElement("div");
    reasoningBody.className = "msg-reasoning-body";
    reasoningBody.hidden = false;
    reasoningBody.style.display = "flex";

    reasoningWrap.appendChild(toggle);
    reasoningWrap.appendChild(reasoningBody);

    const docsWrap = document.createElement("div");
    docsWrap.className = "msg-documents";
    docsWrap.hidden = true;

    const docsTitle = document.createElement("div");
    docsTitle.className = "msg-documents-title";
    docsTitle.textContent = "Referenced documents";

    const docsBody = document.createElement("div");
    docsBody.className = "msg-documents-body";

    docsWrap.appendChild(docsTitle);
    docsWrap.appendChild(docsBody);

    reasoningWrap.appendChild(docsWrap);
    return { reasoningWrap, reasoningBody, docsBody, toggle };
  }

  function appendStreamingReasoningStep(reasoningBody, docsBody, step) {
    const item = document.createElement("div");
    item.className = "msg-reasoning-item";
    item.innerHTML = `<div class="msg-reasoning-label">${escapeHtml(step.label || step.type || "Step")}</div><div class="msg-reasoning-detail">${escapeHtml(reasoningDetailForStep(step))}</div>`;
    reasoningBody.appendChild(item);
    reasoningBody.scrollTop = reasoningBody.scrollHeight;
    if (reasoningBody.hidden) {
      reasoningBody.hidden = false;
      reasoningBody.style.display = "flex";
    }
    if (step.type === "retrieval" || step.type === "web_search") {
      appendStreamingReferencedDocs(docsBody, step.documents || step.results || []);
    } else if (step.type === "subagent_memory" && Array.isArray(step.sources_used) && step.sources_used.length) {
      appendStreamingReferencedDocs(docsBody, step.sources_used.map(s => ({
        source: s.source,
        page: s.page,
        document_id: s.document_id,
        viewer_url: s.document_id
          ? `/docs/${encodeURIComponent(String(s.document_id))}${s.page != null && s.page !== "" ? `?page=${encodeURIComponent(String(s.page))}` : ""}`
          : (s.url || ""),
        score: undefined,
        preview: "",
      })));
    }
  }

  function buildDocHref(doc) {
    const docId = doc.document_id !== undefined && doc.document_id !== null ? String(doc.document_id) : "";
    const base = doc.fileUrl || doc.viewer_url || doc.url || doc.source_url || (docId ? `/docs/${encodeURIComponent(docId)}` : "");
    if (!base) return "";
    const page = doc.page;
    if (page !== undefined && page !== null && page !== "" && page !== "?" && !String(base).includes("page=")) {
      const sep = String(base).includes("?") ? "&" : "?";
      return `${base}${sep}page=${encodeURIComponent(String(page))}`;
    }
    return base;
  }

  function appendStreamingReferencedDocs(docsBody, documents) {
    if (!documents || !documents.length) return;
    docsBody.innerHTML = documents.map(doc => {
      const title = escapeHtml(doc.title || doc.source || "Unknown source");
      const page = doc.page !== undefined && doc.page !== null ? `Page ${escapeHtml(String(doc.page))}` : "";
      const preview = escapeHtml(doc.preview || doc.snippet || "");
      const href = escapeHtml(buildDocHref(doc));
      const score = doc.score !== undefined ? String(doc.score.toFixed(4)) : "";
      const titleHtml = href
        ? `<a class="msg-doc-link" href="${href}" target="_blank" rel="noopener noreferrer">${title}${page ? ` · ${page}` : ""}</a>`
        : `<span class="msg-doc-label disabled">${title}${page ? ` · ${page}` : ""}</span>`;

      return `<div class="msg-doc">
          <div class="msg-doc-head">
            ${titleHtml}
            <span class="msg-doc-score">${score}</span>
          </div>
          ${preview ? `<div class="msg-doc-preview">${preview}</div>` : ""}
        </div>`;
    }).join("");
    docsBody.parentElement.hidden = false;
    docsBody.scrollTop = docsBody.scrollHeight;
  }

  function startAssistantMessageRow() {
    showChat();
    const row = document.createElement("div");
    row.className = "msg-row";

    const head = document.createElement("div");
    head.className = "msg-head";

    const av = document.createElement("div");
    av.className = "msg-av assistant";
    av.textContent = "AI";

    const rl = document.createElement("span");
    rl.className = "msg-role";
    rl.textContent = "Assistant";

    const tm = document.createElement("span");
    tm.className = "msg-time";
    tm.textContent = now();

    head.appendChild(av);
    head.appendChild(rl);
    head.appendChild(tm);

    const body = document.createElement("div");
    body.className = "msg-body";
    body.innerHTML = renderMessageHtml("Thinking…");

    const { reasoningWrap, reasoningBody, docsBody } = createReasoningPanel();

    row.appendChild(head);
    row.appendChild(body);
    row.appendChild(reasoningWrap);
    msgContainer.appendChild(row);
    scrollBottom();

    return { row, body, reasoningBody, docsBody };
  }

  function finalizeAssistantMessageRow(row, body, trace, text, file, citations) {
    body.innerHTML = renderMessageHtml(
      text || "I was unable to generate a response. Please try again.",
      citations || []
    );
    if (file) {
      const downloadWrapper = document.createElement("div");
      downloadWrapper.className = "msg-download";
      const downloadLink = document.createElement("a");
      const filename = `${file.filename || "generated_document"}${file.extension || ""}`;
      downloadLink.href = `/download/generated/${encodeURIComponent(filename)}`;
      downloadLink.target = "_blank";
      downloadLink.rel = "noopener";
      downloadLink.textContent = `Download ${filename}`;
      downloadWrapper.appendChild(downloadLink);
      row.appendChild(downloadWrapper);
    }
    if (trace && trace.steps && trace.steps.length) {
      const existingBody = row.querySelector(".msg-reasoning-body");
      const existingDocsBody = row.querySelector(".msg-documents-body");
      if (existingBody) {
        existingBody.innerHTML = "";
        trace.steps.slice(0, 6).forEach(step => appendStreamingReasoningStep(existingBody, existingDocsBody, step));
      }
    }

    // Collapse reasoning once the stream is fully done (live view stays expanded until here).
    const reasoningToggle = row.querySelector(".msg-reasoning-toggle");
    const reasoningBodyEl = row.querySelector(".msg-reasoning-body");
    if (reasoningBodyEl) {
      reasoningBodyEl.hidden = true;
      reasoningBodyEl.style.display = "none";
    }
    if (reasoningToggle) {
      reasoningToggle.textContent = "Show thinking";
    }

    const msgId = ++traceCounter;
    row.dataset.msgId = String(msgId);
    attachTracePill(row, msgId, trace);
  }

  // ── message rendering ─────────────────────────────────────────
  const CITATION_FULL_RE = /\[(Document|Doc)\s+(\d+)\s*\|\s*Source:\s*([^|\]]+?)\s*\|\s*Page:\s*(\d+)\]/gi;
  const CITATION_GROUP_RE = /\[(?:(?:Document|Doc)\s+\d+(?:\s*,\s*)?)+\]/gi;
  const CITATION_SHORT_RE = /\[(Document|Doc)\s+(\d+)\](?!\s*\|)/gi;
  const CITATION_INDEX_RE = /(?:Document|Doc)\s+(\d+)/gi;

  function lookupCitation(citations, index) {
    if (!Array.isArray(citations)) return null;
    return citations.find(c => Number(c.index) === index) || null;
  }

  function buildCitationHref(citation) {
    if (!citation) return "";
    if (citation.fileUrl) return citation.fileUrl;
    if (citation.url) {
      const page = citation.page;
      if (page !== undefined && page !== null && page !== "" && page !== "?" && !String(citation.url).includes("page=")) {
        const sep = String(citation.url).includes("?") ? "&" : "?";
        return `${citation.url}${sep}page=${encodeURIComponent(String(page))}`;
      }
      return citation.url;
    }
    const docId = citation.document_id ?? citation.docId;
    if (!docId) return "";
    let href = `/docs/${encodeURIComponent(String(docId))}`;
    const page = citation.page;
    if (page !== undefined && page !== null && page !== "" && page !== "?") {
      href += `?page=${encodeURIComponent(String(page))}`;
    }
    return href;
  }

  function formatCitationLabel(citation, index) {
    const source = citation?.sourceFile || citation?.source;
    const page = citation?.page;
    if (source) {
      const shortName = source.length > 24 ? `${source.slice(0, 21)}…` : source;
      if (page !== undefined && page !== null && page !== "" && page !== "?") {
        return `${shortName} · p.${page}`;
      }
      return shortName;
    }
    return `[${index}]`;
  }

  function renderCitationLink(index, citations, fallbackText) {
    const mapped = lookupCitation(citations, index);
    if (!mapped) return fallbackText;
    const href = buildCitationHref(mapped);
    const label = formatCitationLabel(mapped, index);
    const title = mapped.sourceFile || mapped.source || `Document ${index}`;
    if (!href) {
      return `<span class="citation-badge" title="${escapeHtml(title)}">${escapeHtml(label)}</span>`;
    }
    return `<a class="citation-badge doc-ref" href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(title)}">${escapeHtml(label)}</a>`;
  }

  function renderCitationGroup(match, citations) {
    const indexes = [];
    CITATION_INDEX_RE.lastIndex = 0;
    let part;
    while ((part = CITATION_INDEX_RE.exec(match)) !== null) {
      indexes.push(Number(part[1]));
    }
    if (!indexes.length) return match;
    return indexes
      .map(index => renderCitationLink(index, citations, `[Document ${index}]`))
      .join(", ");
  }

  function renderMessageHtml(text, citations) {
    let raw = typeof marked !== "undefined" && typeof marked.parse === "function"
      ? marked.parse(text)
      : escapeHtml(text).replace(/\n/g, "<br>");

    raw = raw.replace(CITATION_FULL_RE, (match, _label, numStr) =>
      renderCitationLink(Number(numStr), citations, match)
    );

    // Grouped citations like [Document 1, Document 2, Document 3] before singles.
    raw = raw.replace(CITATION_GROUP_RE, match => {
      if (!/,/.test(match)) return match; // leave true singles for CITATION_SHORT_RE
      return renderCitationGroup(match, citations);
    });

    raw = raw.replace(CITATION_SHORT_RE, (match, _label, numStr) =>
      renderCitationLink(Number(numStr), citations, match)
    );

    if (typeof DOMPurify !== "undefined" && typeof DOMPurify.sanitize === "function") {
      return DOMPurify.sanitize(raw, { ADD_ATTR: ["target", "rel", "title"] });
    }
    return raw;
  }

  function appendMsg(role, text, trace) {
    showChat();
    const row  = document.createElement("div");
    row.className = "msg-row" + (role === "user" ? " user-msg" : "");

    const head = document.createElement("div");
    head.className = "msg-head";

    const av = document.createElement("div");
    av.className = "msg-av " + role;
    av.textContent = role === "user" ? "U" : "AI";

    const rl = document.createElement("span");
    rl.className = "msg-role";
    rl.textContent = role === "user" ? "You" : "Assistant";

    const tm = document.createElement("span");
    tm.className = "msg-time";
    tm.textContent = now();

    head.appendChild(av);
    head.appendChild(rl);
    head.appendChild(tm);

    const body = document.createElement("div");
    body.className = "msg-body";
    body.innerHTML = renderMessageHtml(text);
    row.appendChild(head);
    row.appendChild(body);

    if (role === "assistant" && trace && trace.steps && trace.steps.length) {
      const { reasoningWrap, reasoningBody, docsBody } = createReasoningPanel();
      // Replay every saved step so "Show thinking" + referenced docs match live.
      trace.steps.forEach(step => appendStreamingReasoningStep(reasoningBody, docsBody, step));
      reasoningBody.hidden = true;
      reasoningBody.style.display = "none";
      reasoningWrap.querySelector(".msg-reasoning-toggle").textContent = "Show thinking";
      row.appendChild(reasoningWrap);

      const msgId = ++traceCounter;
      row.dataset.msgId = String(msgId);
      attachTracePill(row, msgId, trace);
    }

    msgContainer.appendChild(row);

    scrollBottom();
  }

  function addTyping() {
    const wrap = document.createElement("div");
    wrap.className = "typing";
    wrap.id = "typing-indicator";

    const av = document.createElement("div");
    av.className = "msg-av assistant";
    av.textContent = "AI";
    wrap.appendChild(av);

    for (let i = 0; i < 3; i++) {
      const d = document.createElement("div");
      d.className = "typing-dot";
      wrap.appendChild(d);
    }
    msgContainer.appendChild(wrap);
    scrollBottom();
  }

  function removeTyping() {
    const el = document.getElementById("typing-indicator");
    if (el) el.remove();
  }

  function addError(text) {
    showChat();
    const p = document.createElement("p");
    p.className = "err-line";
    p.textContent = text;
    msgContainer.appendChild(p);
    scrollBottom();
  }

  function renderHistory(messages) {
    msgContainer.innerHTML = "";
    if (!messages || messages.length === 0) { showEmpty(); return; }
    showChat();
    messages.forEach(m => appendMsg(m.role, m.content, m.trace || null));
  }

  // ── sidebar ───────────────────────────────────────────────────
  function renderGeneralConversations(conversations) {
    generalConvList.innerHTML = "";
    if (!conversations || conversations.length === 0) {
      generalConvList.innerHTML = '<div class="project-empty-hint">No conversations yet.</div>';
      return;
    }

    (conversations || []).forEach(conv => {
      const item = document.createElement("div");
      item.className = "conv-item" + (conv.session_id === activeSessionId ? " active" : "");
      item.dataset.sessionId = conv.session_id;

      const icon = document.createElement("span");
      icon.className = "conv-icon";
      icon.textContent = "💬";

      const label = document.createElement("button");
      label.className = "conv-label";
      label.textContent = conv.title || "New chat";
      label.addEventListener("click", () => switchTo(conv.session_id, conv.title));

      const del = document.createElement("button");
      del.className = "conv-del";
      del.textContent = "×";
      del.title = "Delete";
      del.addEventListener("click", e => { e.stopPropagation(); deleteConv(conv.session_id); });

      item.appendChild(icon);
      item.appendChild(label);
      item.appendChild(del);
      generalConvList.appendChild(item);
    });
  }

  function getExpandedProjectChatsEl() {
    if (!activeProjectId) return null;
    return projectListEl.querySelector(".project-item-wrap.expanded .project-chats");
  }

  function renderProjectConversations(conversations) {
    // Legacy separate section is no longer used — chats render inline under the project.
    projectConversationSection.hidden = true;
    projectConvList.innerHTML = "";

    const chatsEl = getExpandedProjectChatsEl();
    if (!chatsEl) return;

    chatsEl.innerHTML = "";
    if (!conversations || conversations.length === 0) {
      chatsEl.innerHTML = '<div class="project-empty-hint">No project chats yet.</div>';
      return;
    }

    (conversations || []).forEach(conv => {
      const item = document.createElement("div");
      item.className = "conv-item" + (conv.session_id === activeSessionId ? " active" : "");
      item.dataset.sessionId = conv.session_id;

      const icon = document.createElement("span");
      icon.className = "conv-icon";
      icon.textContent = "🧷";

      const label = document.createElement("button");
      label.className = "conv-label";
      label.textContent = conv.title || "New chat";
      label.addEventListener("click", (e) => {
        e.stopPropagation();
        switchTo(conv.session_id, conv.title);
      });

      const del = document.createElement("button");
      del.className = "conv-del";
      del.textContent = "×";
      del.title = "Delete";
      del.addEventListener("click", e => { e.stopPropagation(); deleteConv(conv.session_id); });

      item.appendChild(icon);
      item.appendChild(label);
      item.appendChild(del);
      chatsEl.appendChild(item);
    });
  }
  // ── projects ──────────────────────────────────────────────────
  function renderProjects(projects) {
    projectListEl.innerHTML = "";
    // Keep the old project-conversation section permanently hidden (accordion replaces it).
    projectConversationSection.hidden = true;
    scopeBackBtn.hidden = true;

    if (!projects || !projects.length) {
      projectListEl.innerHTML = '<div class="project-empty-hint">No projects yet.</div>';
      return;
    }
    projects.forEach(p => {
      const id = p.id ?? "";
      const name = p.name || "Untitled project";
      const isExpanded = String(id) === String(activeProjectId);

      const wrap = document.createElement("div");
      wrap.className = "project-item-wrap" + (isExpanded ? " expanded" : "");
      wrap.dataset.projectId = String(id);

      const item = document.createElement("div");
      item.className = "project-item" + (isExpanded ? " active" : "");

      const icon = document.createElement("span");
      icon.className = "project-icon";
      icon.textContent = "📁";

      const label = document.createElement("span");
      label.className = "project-name";
      label.textContent = name;

      const chevron = document.createElement("span");
      chevron.className = "project-chevron";
      chevron.textContent = isExpanded ? "▾" : "▸";

      const del = document.createElement("button");
      del.type = "button";
      del.className = "conv-del project-del";
      del.textContent = "×";
      del.title = "Delete project";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        // Read id from the row dataset at click-time (avoids empty/stale closure values).
        const pid = wrap.dataset.projectId;
        deleteProject(pid, name);
      });

      item.appendChild(icon);
      item.appendChild(label);
      item.appendChild(del);
      item.appendChild(chevron);
      item.addEventListener("click", () => selectProject(id, name));
      wrap.appendChild(item);

      if (isExpanded) {
        const labelRow = document.createElement("div");
        labelRow.className = "sidebar-label-row project-chats-label-row";

        const chatsLabel = document.createElement("span");
        chatsLabel.className = "sidebar-label";
        chatsLabel.textContent = "Chats";

        const newChatBtn = document.createElement("button");
        newChatBtn.type = "button";
        newChatBtn.className = "new-btn";
        newChatBtn.title = "New project chat";
        newChatBtn.textContent = "+";
        newChatBtn.addEventListener("click", async (e) => {
          e.stopPropagation();
          // Expanded project already sets activeProjectId, so this hits
          // POST /api/projects/<project_id>/conversations
          await createConversationAndSelect();
        });

        labelRow.appendChild(chatsLabel);
        labelRow.appendChild(newChatBtn);
        wrap.appendChild(labelRow);

        const chats = document.createElement("div");
        chats.className = "project-chats";
        wrap.appendChild(chats);
      }

      projectListEl.appendChild(wrap);
    });
  }

  async function loadProjects() {
    try {
      const res  = await authFetch("/api/projects");
      const data = await res.json();
      if (res.ok) renderProjects(data.projects);
    } catch { /* sidebar just stays empty */ }
  }

  async function selectProject(projectId, projectName) {
    // Accordion: click again to collapse; click another to switch expand.
    // Do NOT touch the main chat panel — only a chat click updates it.
    if (String(activeProjectId) === String(projectId)) {
      await clearProjectSelection();
      return;
    }

    activeProjectId = projectId;
    await loadProjects();
    await refreshConversations();
  }

  async function clearProjectSelection() {
    activeProjectId = null;
    await loadProjects();
  }

  scopeBackBtn.addEventListener("click", clearProjectSelection);

  newProjectBtn.addEventListener("click", () => {
    projectNameInput.value = "";
    projectDescInput.value = "";
    projectModalOverlay.hidden = false;
    projectNameInput.focus();
  });

  projectModalCancel.addEventListener("click", () => { projectModalOverlay.hidden = true; });
  projectModalOverlay.addEventListener("click", (e) => {
    if (e.target === projectModalOverlay) projectModalOverlay.hidden = true;
  });

  projectForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = projectNameInput.value.trim();
    if (!name) return;
    const description = projectDescInput.value.trim();

    try {
      const res  = await authFetch("/api/projects", {
        method: "POST",
        body: JSON.stringify({ name, description }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not create project.");

      projectModalOverlay.hidden = true;
      await selectProject(data.id, data.name);
    } catch (err) {
      addError(err.message || "Could not create project.");
    }
  });

  async function switchTo(sessionId, title) {
    activeSessionId = sessionId;
    headerTitle.textContent = title || "Chat";
    msgContainer.innerHTML  = "";
    showEmpty();
    resetTracePanel();

    // re-highlight active
    document.querySelectorAll(".conv-item").forEach(el => {
      const isActive = el.dataset.sessionId === String(sessionId);
      el.classList.toggle("active", isActive);
    });

    await refreshConversations();

    try {
      const res  = await authFetch("/api/conversations/" + sessionId + "/messages");
      const data = await res.json();
      if (data.error) { addError("Could not load messages."); return; }
      renderHistory(data.messages);
    } catch {
      addError("Could not load conversation.");
    }
  }

  async function deleteConv(sessionId) {
    try {
      const res  = await authFetch("/api/conversations/" + sessionId, { method: "DELETE" });
      const data = await res.json();
      if (sessionId === activeSessionId) {
        activeSessionId = data.conversations?.[0]?.session_id || null;
        if (activeSessionId) {
          await switchTo(activeSessionId, data.conversations[0].title);
        } else {
          showEmpty();
        }
      }
      if (data.project_id != null) renderProjectConversations(data.conversations);
      else renderGeneralConversations(data.conversations);
    } catch {
      addError("Could not delete conversation.");
    }
  }

  async function deleteProject(projectId, projectName) {
    const label = projectName || "this project";
    const pid = projectId == null ? "" : String(projectId).trim();

    // Empty id becomes DELETE /api/projects/ → Flask HTML 404 (route requires <project_id>).
    if (!pid || pid === "undefined" || pid === "null") {
      console.error("Delete project aborted: missing project id", { projectId, projectName });
      addError("Could not delete project: missing project id in the UI.");
      return;
    }
    if (!/^\d+$/.test(pid)) {
      console.error("Delete project aborted: invalid project id", { projectId: pid });
      addError(`Could not delete project: invalid project id "${pid}".`);
      return;
    }

    const ok = window.confirm(
      `Delete "${label}" and ALL of its chats, messages, traces, and uploaded documents? This cannot be undone.`
    );
    if (!ok) return;

    // Exact URL the backend registers: DELETE /api/projects/<project_id>
    const url = `/api/projects/${encodeURIComponent(pid)}`;

    try {
      const res = await authFetch(url, { method: "DELETE" });
      let data = {};
      let rawText = "";
      try {
        rawText = await res.text();
        data = rawText ? JSON.parse(rawText) : {};
      } catch (parseErr) {
        console.error("Delete project: non-JSON response", {
          requestedUrl: url,
          responseUrl: res.url,
          status: res.status,
          rawText,
          parseErr,
        });
        data = {};
      }

      if (!res.ok) {
        let detail =
          data.error ||
          data.msg ||
          data.message ||
          (rawText && rawText.slice(0, 200)) ||
          `HTTP ${res.status}`;

        // Werkzeug HTML 404 = no matching route (wrong URL or server not restarted).
        if (res.status === 404 && /<!doctype html>/i.test(rawText || "")) {
          detail =
            `No DELETE route matched ${url}. Restart the Flask server so it loads the latest app.py, then try again.`;
        }

        console.error("Delete project failed", {
          requestedUrl: url,
          responseUrl: res.url,
          status: res.status,
          projectId: pid,
          detail,
          body: data,
        });
        addError(`Could not delete project: ${detail}`);
        return;
      }

      // If we were viewing this project (or a chat inside it), clear the panel.
      if (String(activeProjectId) === String(pid)) {
        activeProjectId = null;
        activeSessionId = null;
        showEmpty();
      }

      await loadProjects();
      await refreshConversations();
    } catch (err) {
      console.error("Delete project exception", { url, err });
      addError(`Could not delete project: ${err && err.message ? err.message : String(err)}`);
    }
  }

  // ── screens ───────────────────────────────────────────────────
  function getCsrfToken() {
    return document.cookie
      .split('; ')
      .find(row => row.startsWith('csrf_access_token='))
      ?.split('=')[1] || '';
  }

  function authFetch(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (!headers.has('Content-Type') && options.body && typeof options.body === 'string') {
      headers.set('Content-Type', 'application/json');
    }
    const csrf = getCsrfToken();
    if (csrf) headers.set('X-CSRF-TOKEN', csrf);
    return fetch(url, {
      ...options,
      credentials: 'include',
      headers,
    });
  }

  async function showApp(username, conversations) {
    loginScreen.hidden = true;
    appScreen.hidden   = false;

    usernameDisplay.textContent = `Welcome, ${username}`;
    userAvatar.textContent      = username.charAt(0).toUpperCase();

    renderGeneralConversations(conversations);
    await loadProjects();


    if (conversations?.length) {
      const first = conversations[0];
      activeSessionId = first.session_id;
      await switchTo(first.session_id, first.title);
    } else {
      await createConversationAndSelect();
    }

    messageInput.focus();
  }

  function setAuthMode(mode) {
    authMode = mode;
    authTabs.forEach(tab => tab.classList.toggle("active", tab.dataset.mode === mode));
    if (mode === "register") {
      authTitle.textContent = "Create your account";
      authSub.textContent = "Register once and access your conversations forever.";
      authSubmit.textContent = "Register";
      usernameField.hidden = false;
      emailField.hidden = false;
    } else {
      authTitle.textContent = "Welcome back";
      authSub.textContent = "Sign in to continue your conversations.";
      authSubmit.textContent = "Sign in";
      usernameField.hidden = true;
      emailField.hidden = false;
    }
    usernameInput.required = mode === "register";
    passwordInput.required = true;
    emailInput.required = true;
    errorEl.hidden = true;
  }

  passwordToggle.addEventListener("click", () => {
    const showing = passwordInput.type === "text";
    passwordInput.type = showing ? "password" : "text";
    passwordToggle.textContent = showing ? "👁" : "🙈";
    passwordToggle.setAttribute("aria-label", showing ? "Show password" : "Hide password");
  });

  authTabs.forEach(tab => {
    tab.addEventListener("click", () => setAuthMode(tab.dataset.mode));
  });

  function showLogin() {
    appScreen.hidden  = true;
    loginScreen.hidden = false;
    setAuthMode("login");
    usernameInput.focus();
  }

  // ── init ──────────────────────────────────────────────────────
  authFetch("/api/me")
    .then(r => r.json())
    .then(data => {
      if (data.logged_in) {
        showApp(data.username, data.conversations).catch(() => showLogin());
      } else {
        showLogin();
      }
    })
    .catch(() => showLogin());

  // ── auth ──────────────────────────────────────────────────────
  authForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.hidden = true;

    const username = usernameInput.value.trim();
    const password = passwordInput.value;
    const email = emailInput.value.trim();
    const endpoint = authMode === "register" ? "/api/auth/register" : "/api/auth/login";

    const body = authMode === "register"
      ? { username, email, password }
      : { email, password };

    try {
      const res = await authFetch(endpoint, {
        method: "POST",
        body: JSON.stringify(body),
      });
      const data = await res.json();

      if (!res.ok) {
        errorEl.className = "login-error";
        errorEl.textContent = data.error || "Something went wrong.";
        errorEl.hidden = false;
        return;
      }

      if (authMode === "register") {
        setAuthMode("login");
        passwordInput.value = "";
        errorEl.className = "login-success";
        errorEl.textContent = "Account created — please sign in.";
        errorEl.hidden = false;
        usernameInput.focus();
        return;
      }

      const meRes = await authFetch("/api/me");
      const meData = await meRes.json();
      if (meRes.ok && meData.logged_in) {
        showApp(meData.username, meData.conversations);
      } else {
        errorEl.className = "login-error";
        errorEl.textContent = "Unable to sign in. Please try again.";
        errorEl.hidden = false;
      }
    } catch (err) {
      errorEl.className = "login-error";
      errorEl.textContent = "Network error — please try again.";
      errorEl.hidden = false;
    }
  });
  // ── new conversation ──────────────────────────────────────────
  async function createConversationAndSelect() {
    try {
      const endpoint = activeProjectId ? `/api/projects/${activeProjectId}/conversations` : "/api/conversations";
      const res  = await authFetch(endpoint, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not create a conversation.");

      activeSessionId = data.session_id;
      headerTitle.textContent = data.title || "New chat";
      msgContainer.innerHTML = "";
      showEmpty();
      resetTracePanel();
      await refreshConversations();
      return true;
    } catch (err) {
      addError(err.message || "Could not create a conversation.");
      return false;
    }
  }
  // Conversations "+" — always creates a GENERAL chat (not project-scoped).
  // Collapse any expanded project first so createConversationAndSelect()
  // hits /api/conversations (activeProjectId === null).
  newGeneralConvBtn.addEventListener("click", async () => {
    if (activeProjectId !== null) {
      await clearProjectSelection();
    }
    await createConversationAndSelect();
  });
  //     msgContainer.innerHTML = "";
  //     showEmpty();
  //     headerTitle.textContent = "New chat";
  //     resetTracePanel();

  //     // refresh sidebar
  //     const meRes  = await authFetch("/api/me");
  //     const meData = await meRes.json();
  //     renderConversations(meData.conversations);
  //     messageInput.focus();
  //   } catch {
  //     addError("Could not start a new conversation.");
  //   }
  // });
  // ── plus menu: web search toggle + upload trigger ─────────────
let webSearchEnabled = false;
let agentModeEnabled = false;
let uploadingFile     = null; // {name, percent} while an upload is in flight

function closePlusMenu() { plusMenu.hidden = true; plusBtn.classList.remove("open"); }
function togglePlusMenu() {
  const willOpen = plusMenu.hidden;
  if (willOpen) {
    const rect = plusBtn.getBoundingClientRect();
    plusMenu.style.left = rect.left + "px";
    plusMenu.style.top = (rect.top - 8) + "px";
    plusMenu.style.transform = "translateY(-100%)"; // sit above the button
  }
  plusMenu.hidden = !willOpen;
  plusBtn.classList.toggle("open", willOpen);
}
plusBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  togglePlusMenu();
});

document.addEventListener("click", (e) => {
  if (!plusMenu.hidden && !plusMenu.contains(e.target) && e.target !== plusBtn) {
    closePlusMenu();
  }
});

menuUploadBtn.addEventListener("click", () => {
  closePlusMenu();
  attachInput.click();
});

menuWebSearchBtn.addEventListener("click", () => {
  webSearchEnabled = !webSearchEnabled;
  webSearchCheck.hidden = !webSearchEnabled;
  syncPlusBtn();
  closePlusMenu();
});

if (menuAgentBtn && agentCheck) {
  menuAgentBtn.addEventListener("click", () => {
    agentModeEnabled = !agentModeEnabled;
    agentCheck.hidden = !agentModeEnabled;
    syncPlusBtn();
    closePlusMenu();
  });
}

function syncPlusBtn() {
  if (agentModeEnabled) {
    plusBtn.textContent = "🤖";
    plusBtn.classList.add("websearch-active");
    plusBtn.title = "Agent mode on";
  } else if (webSearchEnabled) {
    plusBtn.textContent = "🌐";
    plusBtn.classList.add("websearch-active");
    plusBtn.title = "Web search on";
  } else {
    plusBtn.textContent = "+";
    plusBtn.classList.remove("websearch-active");
    plusBtn.title = "Add";
  }
}

  // ── send message ──────────────────────────────────────────────
  messageInput.addEventListener("input", () => {
  autoResize();
  sendBtn.disabled = !messageInput.value.trim();
});

  async function submitChatMessage(text) {
  if (!activeSessionId) {
    const created = await createConversationAndSelect();
    if (!created) {
      sendBtn.disabled = false;
      return;
    }
  }

  appendMsg("user", text);
  messageInput.value = "";
  autoResize();
  sendBtn.disabled = true;
  addTyping();
  startLiveTrace();
  const assistantRow = startAssistantMessageRow();

  let settled = false;

  try {
    const res = await authFetch("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        message: text,
        session_id: activeSessionId,
        web_search: webSearchEnabled,
        agent_mode: agentModeEnabled
      })
    });

      if (!res.ok || !res.body) {
        let errMsg = "Something went wrong.";
        try { const data = await res.json(); errMsg = data.error || errMsg; } catch {}
        removeTyping();
        addError(errMsg);
        settled = true;
      } else {
        const reader  = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const { events, rest } = parseSSEBuffer(buffer);
          buffer = rest;

          for (const evt of events) {
            if (evt.type === "step") {
              appendLiveTraceStep(evt.step);
              appendStreamingReasoningStep(assistantRow.reasoningBody, assistantRow.docsBody, evt.step);

            } else if (evt.type === "done") {
              removeTyping();
              settled = true; 
              const finalTrace = { steps: liveTraceSteps, rag_used: !!evt.rag_used };
                finalizeAssistantMessageRow(
                  assistantRow.row,
                  assistantRow.body,
                  finalTrace,
                  evt.response,
                  evt.file,
                  evt.citations || []
                );
              if (evt.title) {
                headerTitle.textContent = evt.title;
                await refreshConversations();
              }

            } else if (evt.type === "error") {
              removeTyping();
              addError(evt.error || "Something went wrong.");
              settled = true;
            }
          }
        }
      }

      if (!settled) {
        removeTyping();
        addError("Connection closed unexpectedly.");
      }
    } catch {
      removeTyping();
      addError("Could not reach the server.");
    } finally {
      sendBtn.disabled = !messageInput.value.trim();
      messageInput.focus();
    } 
  }

  // webSearchBtn.addEventListener("click", async () => {
  //   const text = messageInput.value.trim();
  //   if (!text) return;
  //   await submitChatMessage(text, true);
  // });

  messageInput.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); composer.requestSubmit(); }
  });

  // Parses a buffer of raw SSE text into {events, rest}: events is the list of
  // fully-received "data: {...}" frames found so far, rest is the leftover
  // partial frame to keep buffering (SSE frames are separated by a blank line).
  function parseSSEBuffer(buffer) {
    const frames = buffer.split("\n\n");
    const rest = frames.pop(); // last chunk may be incomplete — keep it for next read
    const events = [];
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      const jsonStr = line.slice(5).trim();
      if (!jsonStr) continue;
      try {
        events.push(JSON.parse(jsonStr));
      } catch {
        // ignore malformed frame
      }
    }
    return { events, rest };
  }

  // ── file upload ──────────────────────────────────────────────
  let plusUploadResetTimer = null;

  function setPlusUploadProgress(percent) {
    if (!plusProgressValue) return;
    const p = Math.max(0, Math.min(100, Number(percent) || 0));
    // pathLength=100 → dashoffset 100 means empty, 0 means full
    plusProgressValue.style.strokeDashoffset = String(100 - p);
  }

  function setPlusUploadState(state) {
    if (!plusWrap) return;
    plusWrap.classList.remove("is-uploading", "is-success", "is-error");
    if (state === "uploading") plusWrap.classList.add("is-uploading");
    else if (state === "success") plusWrap.classList.add("is-success");
    else if (state === "error") plusWrap.classList.add("is-error");
  }

  function schedulePlusUploadIdle(delayMs) {
    if (plusUploadResetTimer) clearTimeout(plusUploadResetTimer);
    plusUploadResetTimer = setTimeout(() => {
      setPlusUploadState("idle");
      setPlusUploadProgress(0);
      plusUploadResetTimer = null;
    }, delayMs);
  }

  function renderUploadProgress() {
    if (!uploadingFile) {
      uploadProgressEl.hidden = true;
      uploadProgressEl.textContent = "";
      return;
    }
    uploadProgressEl.hidden = false;
    uploadProgressEl.textContent = `📎 ${uploadingFile.name} ${uploadingFile.percent}%`;
    setPlusUploadState("uploading");
    setPlusUploadProgress(uploadingFile.percent);
  }

  attachInput.addEventListener("change", async () => {
  const file = attachInput.files[0];
  if (!file) return;

  if (!activeSessionId) {
    const created = await createConversationAndSelect();
    if (!created) { attachInput.value = ""; return; }
  }

  if (plusUploadResetTimer) {
    clearTimeout(plusUploadResetTimer);
    plusUploadResetTimer = null;
  }

  uploadingFile = { name: file.name, percent: 0 };
  setPlusUploadState("uploading");
  setPlusUploadProgress(0);
  renderUploadProgress();

  try {
    await uploadFileWithProgress(file);
    if (uploadingFile) uploadingFile.percent = 100;
    renderUploadProgress();
    setPlusUploadProgress(100);
    setPlusUploadState("success");
    uploadingFile = null;
    renderUploadProgress();
    schedulePlusUploadIdle(900);
    appendMsg("assistant", `📎 *${file.name}* was added to this conversation. You can ask me about it now.`);
  } catch (err) {
    const failedAt = uploadingFile ? uploadingFile.percent : 100;
    setPlusUploadProgress(Math.max(failedAt, 8));
    setPlusUploadState("error");
    uploadingFile = null;
    renderUploadProgress();
    schedulePlusUploadIdle(1200);
    addError(`Upload failed: ${err.message || "Upload failed"}`);
  } finally {
    attachInput.value = "";
  }
});

function uploadFileWithProgress(file) {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/conversations/${activeSessionId}/upload`);

    const csrf = getCsrfToken();
    if (csrf) xhr.setRequestHeader("X-CSRF-TOKEN", csrf);
    xhr.withCredentials = true;

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable && uploadingFile) {
        uploadingFile.percent = Math.round((e.loaded / e.total) * 100);
        renderUploadProgress();
      }
    });

    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.error || "Upload failed"));
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(formData);
  });
}


  composer.addEventListener("submit", async e => {
    e.preventDefault();
    const text = messageInput.value.trim();
    if (!text) return;
    await submitChatMessage(text);
  });
})();   
