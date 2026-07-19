const SESSION_KEY = "wls_ui_session";

function captureSessionToken() {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const supplied = fragment.get("session");
  if (supplied) {
    sessionStorage.setItem(SESSION_KEY, supplied);
    history.replaceState(null, "", window.location.pathname + window.location.search);
    return supplied;
  }
  return sessionStorage.getItem(SESSION_KEY);
}

let sessionToken = captureSessionToken();
const state = {
  view: "home",
  home: null,
  projects: [],
  inbox: { count: 0, items: [] },
  runs: [],
  library: null,
  product: null,
  garbage: null,
};

const main = document.querySelector("#main");
const inspector = document.querySelector("#inspector");
const toast = document.querySelector("#toast");

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (sessionToken) headers.Authorization = `Bearer ${sessionToken}`;
  if (options.method && options.method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-WLS-UI"] = "1";
  }
  const response = await fetch(path, { credentials: "omit", ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) {
      sessionStorage.removeItem(SESSION_KEY);
      sessionToken = null;
    }
    throw new Error(data.message || data.error || `HTTP ${response.status}`);
  }
  return data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusClass(status) {
  if (["SUCCEEDED", "COMPLETED", "VERIFIED", "ACTIVE"].includes(status)) return "good";
  if (String(status || "").endsWith("_PASSED")) return "good";
  if (["FAILED", "KILLED", "UNKNOWN_SIDE_EFFECT", "BLOCKED"].includes(status)) return "bad";
  if (String(status || "").endsWith("_BLOCKED")) return "bad";
  return "warn";
}

function fmtDate(value) {
  if (!value) return "-";
  try {
    return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
  } catch {
    return value;
  }
}

function showToast(message, bad = false) {
  toast.textContent = message;
  toast.setAttribute("role", bad ? "alert" : "status");
  toast.classList.toggle("bad", bad);
  toast.classList.toggle("good", !bad);
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, 3800);
}

function eventCount(home = state.home) {
  return Object.values(((home || {}).counts || {}).events || {}).reduce((a, b) => a + Number(b || 0), 0);
}

function updateTopContext() {
  document.querySelector("#top-projects").textContent = state.home?.active_goal_count || 0;
  document.querySelector("#top-pending").textContent = state.inbox?.count || 0;
  document.querySelector("#top-events").textContent = eventCount();
}

function updateEventDrawer() {
  const latest = state.home?.latest_cycle;
  const summary = latest
    ? `Latest cycle ${latest.cycle_id || latest.run_id || "unknown"} - ${latest.status || "UNKNOWN"}; ${eventCount()} evidence events indexed.`
    : `No cycle is active; ${eventCount()} evidence events indexed.`;
  document.querySelector("#event-summary").textContent = summary;
}

function workflowStrip() {
  return `<div class="workflow-strip" aria-label="Core task path">
    <span class="pill">1. Health</span>
    <span class="pill">2. Project</span>
    <span class="pill">3. Run</span>
    <span class="pill">4. Report</span>
    <span class="pill">5. Export / Evidence</span>
  </div>`;
}

function statusStrip(items) {
  return `<div class="status-strip">${items.map((item) =>
    `<span class="pill ${item.tone || ""}">${escapeHtml(item.label)}: ${escapeHtml(item.value)}</span>`
  ).join("")}</div>`;
}

function emptyState(title, message, actionLabel = "", view = "") {
  const action = actionLabel && view
    ? `<button class="secondary-action" data-view-jump="${escapeHtml(view)}" type="button">${escapeHtml(actionLabel)}</button>`
    : "";
  return `<div class="empty"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p>${action}</div>`;
}

async function loadAll() {
  const data = await api("/api/bootstrap");
  state.home = data.home;
  state.projects = data.projects || [];
  state.inbox = data.inbox || { count: 0, items: [] };
  state.runs = data.runs || [];
  document.querySelector("#inbox-badge").textContent = state.inbox.count || "";
  document.querySelector("#health-dot").classList.remove("bad");
  document.querySelector("#health-dot").classList.add("ok");
  document.querySelector("#health-label").textContent = state.home.system.paused ? "Paused" : "Online";
  document.querySelector("#system-caption").textContent =
    `v${state.home.system.version || "?"} - canonical projection`;
  updateTopContext();
  updateEventDrawer();
  fillProjectSelect();
  render();
}

function pageHead(eyebrow, title, description) {
  return `<div class="page-head"><div>
    <div class="eyebrow">${escapeHtml(eyebrow)}</div>
    <h1>${escapeHtml(title)}</h1>
    <p>${escapeHtml(description)}</p>
  </div></div>`;
}

function metricCard(title, value, suffix) {
  return `<div class="card"><h3>${escapeHtml(title)}</h3><div class="metric">${escapeHtml(value)}<small>${escapeHtml(suffix)}</small></div></div>`;
}

function renderHome() {
  const h = state.home;
  const counts = h.counts || {};
  const active = h.active_goal_count || 0;
  const running = (counts.cycles || {}).RUNNING || 0;
  const waiting = h.pending_action_count || 0;
  const evidence = eventCount(h);
  main.innerHTML = pageHead("Today", "Owner Console", "Goals, runs, approvals, evidence, and runtime state in one owner surface.") +
    workflowStrip() +
    statusStrip([
      { label: "Runtime", value: h.system.paused ? "Paused" : "Online", tone: h.system.paused ? "warn" : "good" },
      { label: "Authority", value: h.integrity_hint?.state_owner || "LivingSystem" },
      { label: "Projection only", value: h.integrity_hint?.projection_only ? "Yes" : "No", tone: h.integrity_hint?.projection_only ? "good" : "bad" },
    ]) + `
    <div class="grid cols-4">
      ${metricCard("Active Goals", active, "canonical")}
      ${metricCard("Running", running, "cycles")}
      ${metricCard("Needs Decision", waiting, "actions")}
      ${metricCard("Events", evidence, "recorded")}
    </div>
    <div class="section-title"><h2>Current Focus</h2><small>runtime.next_focus</small></div>
    <div class="list">
      ${(h.focus && h.focus.length) ? h.focus.map((item) => `
        <div class="row-card"><div><h3>${escapeHtml(typeof item === "string" ? item : JSON.stringify(item))}</h3></div></div>
      `).join("") : emptyState("No focus queued", "The runtime has not published next_focus items. Create a project or run one cycle to produce fresh context.", "Open Projects", "projects")}
    </div>
    <div class="section-title"><h2>Recent Runs</h2><small>Cycle evidence</small></div>
    ${renderRunList(state.runs.slice(0, 6))}
  `;
  bindSelectable();
}

function renderProjects() {
  main.innerHTML = pageHead("Long-lived Objects", "Projects", "Top-level Goals appear as Projects; child Goals appear as Tasks.") +
    statusStrip([
      { label: "Projects", value: state.projects.length },
      { label: "Create path", value: "Top command" },
      { label: "Write target", value: "GoalStore", tone: "good" },
    ]) +
    (state.projects.length ? `<div class="grid cols-2">${state.projects.map(projectCard).join("")}</div>` :
      emptyState("No projects yet", "Create the first Project from the top command. Tasks remain attached to a top-level project so context is never lost."));
  bindSelectable();
}

function projectCard(p) {
  return `<button class="row-card selectable" data-type="project" data-id="${escapeHtml(p.goal_id)}" type="button">
    <div>
      <h3>${escapeHtml(p.title)}</h3>
      <p>${escapeHtml(p.description || "No description")}</p>
      <div class="meta">
        <span class="pill ${statusClass(p.status)}">${escapeHtml(p.status)}</span>
        <span class="pill">${Number(p.active_task_count || 0)} active / ${Number(p.task_count || 0)} tasks</span>
        <span class="pill">priority ${Number(p.priority || 0).toFixed(2)}</span>
      </div>
      <progress class="progress" max="100" value="${Math.round(Number(p.progress || 0) * 100)}">${Math.round(Number(p.progress || 0) * 100)}%</progress>
    </div>
    <div><small>${fmtDate(p.updated_at)}</small></div>
  </button>`;
}

function renderInbox() {
  const items = state.inbox.items || [];
  main.innerHTML = pageHead("Owner Intervention", "Approvals", "Approvals, approved resumptions, unknown side effects, and blocked goals are collected here.") +
    statusStrip([
      { label: "Open items", value: items.length, tone: items.length ? "warn" : "good" },
      { label: "Recovery", value: "Approve / Reject / Resume / Resolve" },
    ]) +
    (items.length ? `<div class="list">${items.map(inboxCard).join("")}</div>` :
      emptyState("No pending owner decisions", "There are no approvals, unresolved side effects, or blocked goals waiting for owner intervention.", "Review Runs", "runs"));
  bindSelectable();
}

function inboxCard(item) {
  const title = item.item_type === "action" ? item.purpose : item.title;
  const description = item.item_type === "action"
    ? `${item.tool} - ${item.risk}`
    : (item.description || "");
  return `<button class="row-card selectable" data-type="${escapeHtml(item.item_type)}" data-id="${escapeHtml(item.item_id)}" type="button">
    <div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(description)}</p>
      <div class="meta"><span class="pill ${statusClass(item.status)}">${escapeHtml(item.status)}</span></div>
    </div><div>Open</div>
  </button>`;
}

function renderRuns() {
  main.innerHTML = pageHead("Execution Surface", "Runs", "Cycles are runtime records; plans and actions are inspectable execution steps.") +
    statusStrip([
      { label: "Runs", value: state.runs.length },
      { label: "Primary action", value: "Run One Cycle" },
      { label: "High-risk actions", value: "Inbox gated", tone: "good" },
    ]) +
    renderRunList(state.runs);
  bindSelectable();
}

function renderRunList(runs) {
  if (!runs.length) return emptyState("No runs recorded", "Run one cycle from the left rail to create auditable execution evidence.");
  return `<div class="list">${runs.map((r) => `
    <button class="row-card selectable" data-type="run" data-id="${escapeHtml(r.run_id)}" type="button">
      <div><h3>${escapeHtml(r.run_id)}</h3>
        <p>${fmtDate(r.started_at)} - ${r.action_count} actions - ${r.failed_actions} failed - ${r.waiting_actions} waiting</p>
        <div class="meta"><span class="pill ${statusClass(r.status)}">${escapeHtml(r.status)}</span></div>
      </div><div>Open</div>
    </button>`).join("")}</div>`;
}

async function renderPanels() {
  if (!state.product) state.product = await api("/api/product");
  const panels = state.product.panels || [];
  const readiness = readinessCards();
  main.innerHTML = pageHead("Living Agent OS", "Evidence", "Read-only product panels over LivingSystem.status; no canonical writes.") +
    statusStrip([
      { label: "Mode", value: state.product.mode || "-" },
      { label: "Writes canonical state", value: state.product.writes_canonical_state ? "Yes" : "No", tone: state.product.writes_canonical_state ? "bad" : "good" },
      { label: "Direct tool execution", value: state.product.direct_tool_execution ? "Yes" : "No", tone: state.product.direct_tool_execution ? "bad" : "good" },
    ]) + `
    <div class="grid cols-4">
      ${metricCard("Panels", panels.length, "read-only")}
      ${metricCard("Mode", state.product.mode || "-", "projection")}
      ${metricCard("Writes", state.product.writes_canonical_state ? "Yes" : "No", "canonical")}
      ${metricCard("Tools", state.product.direct_tool_execution ? "Direct" : "None", "execution")}
    </div>
    <div class="section-title"><h2>Delivery Readiness</h2><small>candidate evidence</small></div>
    <div class="grid cols-4">${readiness.join("")}</div>
    <div class="section-title"><h2>Release Handoff</h2><small>candidate evidence</small></div>
    <div class="grid cols-2">${releaseHandoffCards().join("")}</div>
    <div class="section-title"><h2>Organs</h2><small>${escapeHtml(state.product.projection_version || "-")}</small></div>
    <div class="grid cols-2">${panels.map(panelCard).join("") || emptyState("No product panels projected", "The runtime did not publish product projection panels for this bootstrapped view.")}</div>
  `;
  bindSelectable();
}

async function renderReview() {
  if (!state.garbage) state.garbage = await api("/api/garbage-audit");
  const candidates = Array.isArray(state.garbage.candidates) ? state.garbage.candidates : [];
  const cleanupEnabled = candidates.length > 0 && !state.garbage.cleanup_executed;
  const clearEnabled = Boolean(state.garbage.quarantine_root);
  main.innerHTML = pageHead("Owner Review", "Garbage Review", "Review cleanup candidates, quarantine approved items, and clear quarantine with a second approval.") +
    statusStrip([
      { label: "Audit", value: state.garbage.status || "NO_AUDIT", tone: state.garbage.status === "NO_AUDIT" ? "warn" : "good" },
      { label: "Owner review", value: "Required" },
      { label: "Clear policy", value: "Second approval" },
    ]) + `
    <div class="grid cols-4">
      ${metricCard("Status", state.garbage.status || "-", "audit")}
      ${metricCard("Candidates", candidates.length, "items")}
      ${metricCard("Bytes", state.garbage.total_candidate_bytes || state.garbage.quarantined_bytes || 0, "detected")}
      ${metricCard("Cleanup", state.garbage.cleanup_executed ? "Done" : "Pending", "owner")}
    </div>
    <div class="action-row">
      <button class="secondary-action" id="scan-garbage" type="button">Scan</button>
      <button class="primary-action" id="quarantine-garbage" type="button" title="${cleanupEnabled ? "Quarantine current candidates" : "Run Scan and select detected candidates first"}" ${cleanupEnabled ? "" : "disabled"}>Quarantine</button>
      <button class="danger-action" id="clear-quarantine" type="button" title="${clearEnabled ? "Clear quarantined files after second approval" : "Quarantine must exist before clear is available"}" ${clearEnabled ? "" : "disabled"}>Clear Quarantine</button>
    </div>
    <div class="section-title"><h2>Candidates</h2><small>${escapeHtml(state.garbage.audit_id || "-")}</small></div>
    <div class="list">${candidates.map(garbageCandidateCard).join("") || emptyState("No cleanup candidates", "Run Scan to refresh cleanup candidates. Quarantine and clear operations require explicit owner references.")}</div>
  `;
  document.querySelector("#scan-garbage")?.addEventListener("click", scanGarbage);
  document.querySelector("#quarantine-garbage")?.addEventListener("click", quarantineGarbage);
  document.querySelector("#clear-quarantine")?.addEventListener("click", clearGarbageQuarantine);
  bindSelectable();
}

function garbageCandidateCard(candidate) {
  return `<button class="row-card selectable" data-type="garbage-candidate" data-json="${encodeURIComponent(JSON.stringify(candidate))}" type="button">
    <div>
      <h3>${escapeHtml(candidate.kind || candidate.candidate_id || "candidate")}</h3>
      <p>${escapeHtml(candidate.path || "")}</p>
      <div class="meta">
        <span class="pill ${statusClass(candidate.risk)}">${escapeHtml(candidate.risk || "-")}</span>
        <span class="pill">${escapeHtml(candidate.bytes || 0)} bytes</span>
      </div>
    </div>
    <div>${escapeHtml(candidate.recommended_action || "review")}</div>
  </button>`;
}

function readinessCards() {
  const summary = state.product.delivery_readiness || {};
  const items = Array.isArray(summary.items) ? summary.items : [];
  if (!items.length) return [readinessCard("Readiness", "NO_RECEIPT", "P74-P77")];
  return items.map((item) =>
    readinessCard(item.label || item.readiness_id, item.status || "UNKNOWN", item.pass_id || "-")
  );
}

function releaseHandoffCards() {
  const summary = state.product.release_handoff || {};
  const items = Array.isArray(summary.items) ? summary.items : [];
  if (!items.length) return [readinessCard("Release", "NO_RECEIPT", "P79-P80")];
  return items.map((item) =>
    readinessCard(item.label || item.readiness_id, item.status || "UNKNOWN", item.pass_id || "-")
  );
}

function readinessCard(title, status, suffix) {
  return `<div class="card readiness ${statusClass(status)}">
    <h3>${escapeHtml(title)}</h3>
    <div class="metric compact">${escapeHtml(status)}</div>
    <div class="meta"><span class="pill">${escapeHtml(suffix)}</span></div>
  </div>`;
}

function panelCard(panel) {
  const status = panel.status || {};
  const summary = Object.entries(status).slice(0, 4).map(([key, value]) =>
    `<span class="pill">${escapeHtml(key)}: ${escapeHtml(formatPanelValue(value))}</span>`
  ).join("");
  return `<button class="row-card selectable" data-type="panel" data-json="${encodeURIComponent(JSON.stringify(panel))}" type="button">
    <div>
      <h3>${escapeHtml(panel.title || panel.panel_id)}</h3>
      <p>${escapeHtml(panel.summary || panel.claim_ceiling || "Read-only product panel")}</p>
      <div class="meta">${summary}</div>
    </div>
    <div>${escapeHtml(panel.panel_id)}</div>
  </button>`;
}

function formatPanelValue(value) {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return Array.isArray(value) ? `${value.length} items` : `${Object.keys(value).length} keys`;
  return String(value);
}

async function renderLibrary() {
  if (!state.library) state.library = await api("/api/library");
  const evidence = state.library.evidence || [];
  const skills = state.library.skills || [];
  main.innerHTML = pageHead("Assets & Proof", "Library", "Read-only projection of evidence records and skill candidates.") +
    statusStrip([
      { label: "Evidence", value: evidence.length },
      { label: "Skills", value: skills.length },
      { label: "Mutation", value: "Read-only", tone: "good" },
    ]) + `
    <div class="section-title"><h2>Evidence</h2><small>${evidence.length} recent records</small></div>
    <div class="list">${evidence.length ? evidence.slice(0, 80).map((e) => `
      <button class="row-card selectable" data-type="evidence" data-json="${encodeURIComponent(JSON.stringify(e))}" type="button">
        <div><h3>${escapeHtml(e.event_type)}</h3>
          <p>${escapeHtml(e.evidence_id)} - ${fmtDate(e.created_at)}</p>
        </div><div>#${e.seq}</div>
      </button>`).join("") : emptyState("No evidence records", "No evidence records are available in the current projection. Run a cycle or inspect runtime health first.")}</div>
    <div class="section-title"><h2>Skills</h2><small>${skills.length} records</small></div>
    <div class="list">${skills.map((s) => `
      <button class="row-card selectable" data-type="skill" data-json="${encodeURIComponent(JSON.stringify(s))}" type="button">
        <div><h3>${escapeHtml(s.name)} v${s.version}</h3>
          <p>${s.use_count} uses - success ${Math.round(Number(s.success_rate || 0) * 100)}%</p>
          <div class="meta"><span class="pill ${statusClass(s.status)}">${escapeHtml(s.status)}</span></div>
        </div><div>Open</div>
      </button>`).join("") || emptyState("No skills recorded", "The Skills table has no projected rows for this runtime.")}</div>
  `;
  bindSelectable();
}

function render() {
  document.querySelectorAll(".nav-item").forEach((el) =>
    el.classList.toggle("active", el.dataset.view === state.view));
  if (state.view === "home") renderHome();
  else if (state.view === "inbox") renderInbox();
  else if (state.view === "projects") renderProjects();
  else if (state.view === "runs") renderRuns();
  else if (state.view === "panels") renderPanels().catch(handleError);
  else if (state.view === "review") renderReview().catch(handleError);
  else if (state.view === "library") renderLibrary().catch(handleError);
  updateTopContext();
  updateEventDrawer();
}

function bindSelectable() {
  document.querySelectorAll(".selectable").forEach((el) => {
    el.addEventListener("click", async () => {
      try {
        if (el.dataset.json) {
          showInspector(el.dataset.type, JSON.parse(decodeURIComponent(el.dataset.json)));
          return;
        }
        const type = el.dataset.type;
        const id = el.dataset.id;
        if (type === "project") showInspector(type, await api(`/api/projects/${encodeURIComponent(id)}`));
        else if (type === "run") showInspector(type, await api(`/api/runs/${encodeURIComponent(id)}`));
        else showInspector(type, (state.inbox.items || []).find((x) => x.item_id === id) || {});
      } catch (error) { handleError(error); }
    });
  });
}

function showInspector(type, data) {
  const title = data.title || data.purpose || data.event_type || data.name || data.run_id || type;
  let body = `<div class="eyebrow">${escapeHtml(type)}</div><h2>${escapeHtml(title)}</h2>`;
  if (type === "project") {
    body += `<p>${escapeHtml(data.description || "")}</p>${definitionList(data, ["goal_id", "status", "priority", "progress", "updated_at"])}`;
    body += `<h3>Tasks</h3>${(data.tasks || []).map((t) => `<p><b>${escapeHtml(t.title)}</b><br><span class="pill ${statusClass(t.status)}">${escapeHtml(t.status)}</span></p>`).join("") || "<p>No child tasks recorded.</p>"}`;
    body += `<h3>Recent Runs</h3>${(data.recent_runs || []).map((r) => `<p><b>${escapeHtml(r.run_id)}</b><br>${escapeHtml(r.status)} - ${fmtDate(r.started_at)}</p>`).join("") || "<p>No project runs recorded.</p>"}`;
  } else if (type === "run") {
    body += definitionList(data.cycle || {}, ["cycle_id", "status", "started_at", "finished_at", "error"]);
    body += `<h3>Actions</h3>${(data.actions || []).map((a) => `<p><b>${escapeHtml(a.purpose)}</b><br>${escapeHtml(a.tool)} - <span class="pill ${statusClass(a.status)}">${escapeHtml(a.status)}</span></p>`).join("") || "<p>No actions recorded for this run.</p>"}`;
  } else if (type === "action") {
    const operations = data.allowed_operations || [];
    body += `<p>${escapeHtml(data.expected_result || "Owner decision required before this action can progress.")}</p>${definitionList(data, ["action_id", "tool", "risk", "status", "goal_id", "error"])}`;
    body += `<div class="action-row">
      ${operations.includes("approve") ? '<button class="primary-action" id="approve-action" type="button">Approve</button>' : ""}
      ${operations.includes("reject") ? '<button class="danger-action" id="reject-action" type="button">Reject</button>' : ""}
      ${operations.includes("resume") ? '<button class="primary-action" id="resume-action" type="button">Resume Action</button>' : ""}
      ${operations.includes("resolve") ? '<button class="secondary-action" id="resolve-action" type="button">Resolve Unknown</button>' : ""}
    </div>`;
    if (!operations.length) body += `<p>No owner operation is currently available for this action state.</p>`;
  } else {
    body += `<pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>`;
  }
  inspector.innerHTML = body;
  document.querySelector("#approve-action")?.addEventListener("click", () => decideAction(data.action_id, "approve"));
  document.querySelector("#reject-action")?.addEventListener("click", () => decideAction(data.action_id, "reject"));
  document.querySelector("#resume-action")?.addEventListener("click", () => resumeAction(data.action_id));
  document.querySelector("#resolve-action")?.addEventListener("click", () => resolveUnknownAction(data.action_id));
}

function definitionList(data, keys) {
  return `<dl>${keys.map((key) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(data[key] ?? "-")}</dd>`).join("")}</dl>`;
}

async function decideAction(actionId, decision) {
  if (!confirm(`${decision === "approve" ? "Approve" : "Reject"} this Action?`)) return;
  await api(`/api/actions/${encodeURIComponent(actionId)}/${decision}`, {
    method: "POST",
    body: JSON.stringify({ reason: "Owner decision from WLS UI" }),
  });
  showToast("Owner decision recorded.");
  await refreshInboxRuns();
}

async function resumeAction(actionId) {
  if (!confirm("Resume this approved Action?")) return;
  const result = await api(`/api/actions/${encodeURIComponent(actionId)}/resume`, { method: "POST", body: "{}" });
  showToast(`Action finished: ${result.status || "UNKNOWN"}`);
  await refreshInboxRuns();
}

async function resolveUnknownAction(actionId) {
  const resolution = prompt("Resolution: SUCCEEDED / FAILED / CANCELLED / RETRY_SAFE", "FAILED");
  if (!resolution) return;
  const ownerNote = prompt("Evidence note required", "");
  if (!ownerNote || !ownerNote.trim()) {
    showToast("Evidence is required.", true);
    return;
  }
  await api(`/api/actions/${encodeURIComponent(actionId)}/resolve`, {
    method: "POST",
    body: JSON.stringify({
      resolution: resolution.trim().toUpperCase(),
      evidence: { owner_note: ownerNote.trim(), source: "wls-ui" },
    }),
  });
  showToast("Unknown side effect resolved.");
  await refreshInboxRuns();
}

async function refreshInboxRuns() {
  state.inbox = await api("/api/inbox");
  state.runs = await api("/api/runs");
  state.product = null;
  document.querySelector("#inbox-badge").textContent = state.inbox.count || "";
  updateTopContext();
  updateEventDrawer();
  render();
}

async function scanGarbage() {
  state.garbage = await api("/api/garbage-audit/scan", {
    method: "POST",
    body: JSON.stringify({ reason: "Owner UI garbage review scan" }),
  });
  showToast("Garbage scan recorded.");
  render();
}

async function quarantineGarbage() {
  if (!state.garbage || !state.garbage.candidate_count) return;
  const approval = prompt("Approval reference required", "owner-ui-cleanup");
  if (!approval || !approval.trim()) {
    showToast("Approval reference is required.", true);
    return;
  }
  const reason = prompt("Reason", "Owner approved garbage quarantine from WLS UI");
  if (!reason || !reason.trim()) {
    showToast("Reason is required.", true);
    return;
  }
  state.garbage = await api("/api/garbage-audit/cleanup", {
    method: "POST",
    body: JSON.stringify({
      approval_reference: approval.trim(),
      reason: reason.trim(),
    }),
  });
  state.product = null;
  showToast("Candidates quarantined.");
  render();
}

async function clearGarbageQuarantine() {
  if (!state.garbage || !state.garbage.audit_id) return;
  const approval = prompt("Second approval reference required", "owner-ui-clear");
  if (!approval || !approval.trim()) {
    showToast("Approval reference is required.", true);
    return;
  }
  const reason = prompt("Reason", "Owner approved garbage quarantine clear from WLS UI");
  if (!reason || !reason.trim()) {
    showToast("Reason is required.", true);
    return;
  }
  const result = await api("/api/garbage-audit/quarantine-clear", {
    method: "POST",
    body: JSON.stringify({
      audit_id: state.garbage.audit_id,
      approval_reference: approval.trim(),
      reason: reason.trim(),
    }),
  });
  state.garbage = { ...state.garbage, quarantine_clear: result, quarantine_root: null };
  state.product = null;
  showToast(`Quarantine clear: ${result.status || "recorded"}`);
  render();
}

function fillProjectSelect() {
  const select = document.querySelector("#project-select");
  select.innerHTML = `<option value="">Unassigned</option>` +
    state.projects.map((p) => `<option value="${escapeHtml(p.goal_id)}">${escapeHtml(p.title)}</option>`).join("");
}

function handleError(error) {
  console.error(error);
  document.querySelector("#health-dot").classList.add("bad");
  document.querySelector("#health-label").textContent = "Error";
  inspector.innerHTML = `<div class="eyebrow">Recoverable error</div><h2>Request failed</h2><p>${escapeHtml(error.message || String(error))}</p><p>Retry the action. If this repeats, open Library or Runs to inspect the latest evidence.</p>`;
  showToast(error.message || String(error), true);
}

document.querySelector("#nav").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (!button) return;
  state.view = button.dataset.view;
  render();
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-view-jump]");
  if (!button) return;
  state.view = button.dataset.viewJump;
  render();
});

const dialog = document.querySelector("#goal-dialog");
const kindSelect = document.querySelector("#goal-kind");
document.querySelector("#new-object").addEventListener("click", () => {
  kindSelect.value = state.projects.length ? "task" : "project";
  document.querySelector("#project-field").hidden = kindSelect.value === "project";
  dialog.showModal();
});
kindSelect.addEventListener("change", () => {
  document.querySelector("#project-field").hidden = kindSelect.value === "project";
});

document.querySelector("#goal-form").addEventListener("submit", async (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  const formEl = event.currentTarget;
  const form = new FormData(formEl);
  try {
    await api("/api/goals", {
      method: "POST",
      body: JSON.stringify({
        kind: form.get("kind"),
        project_id: form.get("project_id") || null,
        title: form.get("title"),
        description: form.get("description"),
        success_criteria: String(form.get("criteria") || "").split("\n").map((x) => x.trim()).filter(Boolean),
      }),
    });
    dialog.close();
    formEl.reset();
    showToast("Goal written to canonical GoalStore.");
    state.product = null;
    await loadAll();
    state.view = "projects";
    render();
  } catch (error) { handleError(error); }
});

document.querySelector("#run-cycle").addEventListener("click", async (event) => {
  if (!confirm("Run one canonical WLS cycle? High risk Actions still enter Inbox.")) return;
  const button = event.currentTarget;
  button.disabled = true;
  const previousLabel = button.textContent;
  button.textContent = "Cycle running...";
  try {
    const result = await api("/api/cycle", { method: "POST", body: "{}" });
    showToast(`Cycle finished: ${result.status || "UNKNOWN"}`);
    state.library = null;
    state.product = null;
    await loadAll();
    state.view = "runs";
    render();
  } catch (error) {
    handleError(error);
  } finally {
    button.disabled = false;
    button.textContent = previousLabel;
  }
});

loadAll().catch(handleError);
