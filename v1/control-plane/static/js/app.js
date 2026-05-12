// resman SPA — vanilla JS, no build step.

const state = {
  vaults: [],
  discovered: [],
  selectedVault: null,
  sessions: [],
  activeSessionId: null,
  tasks: [],
  ttydAvailable: true,
  window: { state: "between" },
  filter: { search: "", status: "any" },
  // sessionId -> custom display label (set by user via tab click-to-rename).
  // Persisted to localStorage so labels survive reload as long as the
  // session_id does — which it does, since SessionManager keeps sessions
  // alive across page reloads.
  tabLabels: loadTabLabels(),
  // vaultName -> last sessionId the user had active for that vault. Selecting
  // a vault restores its most recently viewed terminal.
  lastSessionByVault: {},
  // vaultName -> last panel the user had open for that vault ("wiki",
  // "ops", "tasks", "config"). Restored on vault re-select so each
  // project has its own UI state. Persisted to localStorage so it
  // survives reload. Help is not remembered (vault-independent).
  lastPanelByVault: loadLastPanelByVault(),
};

function loadTabLabels() {
  try {
    return JSON.parse(localStorage.getItem("resman-tab-labels") || "{}");
  } catch (_) { return {}; }
}
function saveTabLabels() {
  try {
    localStorage.setItem("resman-tab-labels", JSON.stringify(state.tabLabels));
  } catch (_) {}
}
function loadLastPanelByVault() {
  try {
    const raw = JSON.parse(localStorage.getItem("resman-last-panel-by-vault") || "{}");
    // Migrate legacy "terminal" entries to the new "ops" panel name —
    // the panel was renamed when we promoted it to a first-class header
    // tab. Without this, users who pinned the old terminal view on a
    // vault would silently fall back to Wiki on next reload.
    for (const k of Object.keys(raw)) {
      if (raw[k] === "terminal") raw[k] = "ops";
    }
    return raw;
  } catch (_) { return {}; }
}
function saveLastPanelByVault() {
  try {
    localStorage.setItem("resman-last-panel-by-vault", JSON.stringify(state.lastPanelByVault));
  } catch (_) {}
}

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function esc(value) {
  if (value == null) return "";
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

// All mutating fetch goes through here so the CSRF header is always set.
async function api(path, opts = {}) {
  const headers = {
    "Content-Type": "application/json",
    "X-Requested-With": "resman",
    ...(opts.headers || {}),
  };
  const res = await fetch(path, { ...opts, headers });
  let body = null;
  try { body = await res.json(); } catch (_) { body = null; }
  if (!res.ok) {
    const err = new Error((body && body.error) || `HTTP ${res.status}`);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

async function apiText(path) {
  const res = await fetch(path);
  return await res.text();
}

// ----- vault dot color (priority rule) -----
function vaultColor(vault) {
  // Sessions / tasks join here client-side
  const tasksForVault = state.tasks.filter((t) => t.vault === vault.name);
  if (tasksForVault.some((t) => t.state === "failed")) return "red";
  if (tasksForVault.some((t) => t.state === "running")) return "yellow";
  if (state.sessions.some((s) => s.vault === vault.name && s.alive !== false)) return "green";
  return "gray";
}

function vaultDotTitle(vault) {
  const tasksForVault = state.tasks.filter((t) => t.vault === vault.name);
  const flags = [];
  if (tasksForVault.some((t) => t.state === "failed")) flags.push("last task failed");
  if (tasksForVault.some((t) => t.state === "running")) flags.push("task running");
  if (state.sessions.some((s) => s.vault === vault.name)) flags.push("active session");
  if (!flags.length) flags.push("idle");
  return `${vault.name}: ${flags.join(", ")}`;
}

// ----- sidebar render -----
function renderVaultList() {
  const root = $("#vault-list");
  const search = state.filter.search.toLowerCase();
  const status = state.filter.status;
  const filtered = state.vaults.filter((v) => {
    if (search && !v.name.toLowerCase().includes(search)) return false;
    if (status === "session" && !state.sessions.some((s) => s.vault === v.name)) return false;
    if (status === "task" && !state.tasks.some((t) => t.vault === v.name)) return false;
    if (status === "error" && !state.tasks.some((t) => t.vault === v.name && t.state === "failed")) return false;
    return true;
  });
  if (state.vaults.length === 0) {
    root.innerHTML =
      `<div class="muted" style="padding:14px">Add your first vault to get started →</div>`;
    return;
  }
  root.innerHTML = filtered.map((v) => {
    const color = vaultColor(v);
    const tags = (v.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("");
    const warn = !v.path_exists
      ? `<span class="vault-warn" data-warn="${esc(v.name)}" title="path not found — click for details">⚠</span>`
      : (!v.is_obsidian ? `<span class="vault-warn" data-warn="${esc(v.name)}" title="missing .obsidian/ — click for details">?</span>` : "");
    const sel = v.name === state.selectedVault ? "selected" : "";
    const meta = [];
    const sessionsForVault = state.sessions.filter((s) => s.vault === v.name).length;
    if (sessionsForVault) meta.push(`${sessionsForVault} session${sessionsForVault > 1 ? "s" : ""}`);
    const tasksForVault = state.tasks.filter((t) => t.vault === v.name);
    if (tasksForVault.some((t) => t.state === "running")) meta.push("running");
    return `
      <div class="vault-row ${sel}" data-vault="${esc(v.name)}" title="${esc(vaultDotTitle(v))}">
        <span class="vault-dot vault-dot-${color}"></span>
        <div class="vault-info">
          <div class="vault-name">${esc(v.name)}${warn}</div>
          <div class="vault-meta">${meta.map(esc).join(" · ")}</div>
          ${tags ? `<div class="vault-tags">${tags}</div>` : ""}
        </div>
        <button class="play" data-action="play" data-vault="${esc(v.name)}" title="Ingest a URL into this vault's wiki">↘</button>
      </div>`;
  }).join("");
  root.querySelectorAll(".vault-row").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.dataset.action === "play") return;
      // Clicking the warn icon opens the health modal — don't also select.
      if (e.target.dataset.warn) {
        e.stopPropagation();
        showVaultHealth(e.target.dataset.warn);
        return;
      }
      selectVault(row.dataset.vault);
    });
  });
  root.querySelectorAll(".play").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      ingestUrlForVault(btn.dataset.vault);
    });
  });
  // Discovered
  if (state.discovered.length) {
    $("#discovered-section").hidden = false;
    $("#discovered-list").innerHTML = state.discovered.map((v) => `
      <div class="vault-row">
        <span class="vault-dot vault-dot-gray"></span>
        <span class="name">${esc(v.name)}</span>
        <button class="play" data-discover="${esc(v.path)}|${esc(v.name)}">+ Register</button>
      </div>
    `).join("");
    $$("#discovered-list .play").forEach((btn) => {
      btn.addEventListener("click", () => {
        const [path, name] = btn.dataset.discover.split("|");
        registerVault(name, path);
      });
    });
  } else {
    $("#discovered-section").hidden = true;
  }
}

function selectVault(name) {
  state.selectedVault = name;
  // Restore the most recently active session for this vault if we have one
  // remembered; otherwise fall back to whatever live session exists for it.
  const remembered = state.lastSessionByVault[name];
  const candidate =
    (remembered && state.sessions.find((s) => s.id === remembered)) ||
    state.sessions.find((s) => s.vault === name);
  if (candidate) {
    state.activeSessionId = candidate.id;
    state.lastSessionByVault[name] = candidate.id;
  }
  renderVaultList();
  renderVaultContext();
  loadWikiTree();
  loadWiki(WIKI_HOME);
  renderTasks();
  renderTriggerForm(name);
  // Default-panel rule, in priority order:
  //   1. Restore the vault's own last-seen panel (Wiki, Ops, Tasks,
  //      Config) if we remember one — each project has its own UI state.
  //   2. Otherwise, if the vault has a live session, land on Ops.
  //   3. Otherwise, land on Wiki (best entry point for a fresh vault).
  // Ops is only restored if the vault still has at least one live
  // session — a remembered "ops" for a vault whose sessions were since
  // killed would dump the user into an empty terminal panel.
  const hasSession = state.sessions.some((s) => s.vault === name);
  const rememberedPanel = state.lastPanelByVault[name];
  let target;
  if (rememberedPanel === "ops") {
    target = hasSession ? "ops" : "wiki";
  } else if (rememberedPanel) {
    target = rememberedPanel;
  } else {
    target = hasSession ? "ops" : "wiki";
  }
  showPanel(target);
  renderSessions();
  renderActiveSession();
}

function renderVaultContext() {
  const el = $("#vault-context");
  const actions = $("#header-vault-actions");
  if (el) {
    if (state.selectedVault) {
      el.textContent = state.selectedVault;
      el.classList.remove("empty");
      el.title = "Open Ops (terminal sessions) for this vault";
    } else {
      el.textContent = "No vault selected";
      el.classList.add("empty");
      el.title = "";
    }
  }
  // Hide the whole action group (vault label + buttons + ttyd warning)
  // until a vault is selected so the header doesn't show dangling buttons
  // that act on nothing.
  if (actions) actions.classList.toggle("empty", !state.selectedVault);
}

// Show one panel. tabName is "wiki" | "ops" | "tasks" | "config" | "help".
// "ops" is the terminal-sessions view (live ttyd iframes for the current
// vault). It has its own header tab — clicking the vault-name label in
// the header is an equivalent shortcut.
//
// The chosen panel is remembered per-vault in state.lastPanelByVault so
// hopping between vaults restores each one's own last-seen panel. Help is
// vault-independent so we don't persist it (avoid surprising the user with
// a Help landing when they re-select a vault).
function showPanel(tabName) {
  $$(".tab-panel").forEach((p) => p.classList.remove("active"));
  const panel = $("#tab-" + tabName);
  if (panel) panel.classList.add("active");
  $$("#header-tabs .tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === tabName);
  });
  if (tabName === "config") loadConfig();
  if (tabName === "tasks") loadTasks();
  if (tabName === "help") loadHelp();
  if (tabName === "wiki" && state.selectedVault) loadWikiTree();
  if (state.selectedVault && tabName !== "help") {
    state.lastPanelByVault[state.selectedVault] = tabName;
    saveLastPanelByVault();
  }
}

// Sidebar `↘` / `⇲` buttons — queue a wiki-ingest task for the given vault and
// jump to the Tasks tab so the user can watch the task progress. The vault
// selection is updated so the Tasks view is already filtered to the right
// context. URL validation here is intentionally light (presence + http
// scheme) — the operation's own params validator on the backend has the
// authoritative rules. `withPrefix: true` switches the operation to
// `wiki-ingest-prefix`, which runs under the constructive-extraction prefix
// at prompts/urlInjestPrefix.md.
async function ingestUrlForVault(vaultName, opts) {
  const withPrefix = !!(opts && opts.withPrefix);
  const promptLabel = withPrefix
    ? `Ingest a URL into vault '${vaultName}' (with constructive-extraction prefix):`
    : `Ingest a URL into vault '${vaultName}':`;
  const url = window.prompt(promptLabel, "https://");
  if (url == null) return;
  const trimmed = url.trim();
  if (!trimmed || trimmed === "https://" || trimmed === "http://") return;
  if (!/^https?:\/\//i.test(trimmed)) {
    alert("URL must start with http:// or https://");
    return;
  }
  if (state.selectedVault !== vaultName) selectVault(vaultName);
  try {
    await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        name: "task",
        vault: vaultName,
        operation: withPrefix ? "wiki-ingest-prefix" : "wiki-ingest",
        params: { url: trimmed },
        priority: "high",
        force: true,
      }),
    });
  } catch (err) {
    alert("Create failed: " + (err.body?.error || err.message));
    return;
  }
  await loadTasks();
  showPanel("tasks");
}

async function spawnSession(vaultName, type) {
  if (!state.ttydAvailable) {
    alert("ttyd not installed. Install ttyd to enable browser terminals.");
    return;
  }
  try {
    const s = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ vault: vaultName, type }),
    });
    state.sessions.push(s);
    state.activeSessionId = s.id;
    state.lastSessionByVault[vaultName] = s.id;
    renderSessions();
    renderActiveSession();
    // Switch to the Ops panel so the user actually sees the iframe
    // they just spawned. Without this, the +Shell/+Claude buttons appear
    // to do nothing because the user stays on whichever tab they were on.
    showPanel("ops");
  } catch (err) {
    alert("Spawn failed: " + err.message);
  }
}

async function killSession(id) {
  const sess = state.sessions.find((s) => s.id === id);
  await api("/api/sessions/" + encodeURIComponent(id), { method: "DELETE" });
  state.sessions = state.sessions.filter((s) => s.id !== id);
  if (state.activeSessionId === id) state.activeSessionId = null;
  if (sess && state.lastSessionByVault[sess.vault] === id) {
    delete state.lastSessionByVault[sess.vault];
  }
  if (state.tabLabels[id]) {
    delete state.tabLabels[id];
    saveTabLabels();
  }
  renderSessions();
  renderActiveSession();
}

function tabLabelFor(s) {
  return state.tabLabels[s.id] || defaultLabel(s);
}

function visibleSessions() {
  // Each vault has its own terminal tab strip. With no vault selected we
  // show nothing rather than blending all vaults' tabs together.
  if (!state.selectedVault) return [];
  return state.sessions.filter((s) => s.vault === state.selectedVault);
}

function renderSessions() {
  const tabs = $("#term-tabs");
  if (!state.ttydAvailable) {
    tabs.innerHTML = `<span class="muted">ttyd not installed.</span>`;
    return;
  }
  const sessions = visibleSessions();
  if (!sessions.length) {
    const hint = state.selectedVault
      ? "No active sessions — click + Claude or + Shell."
      : "Select a vault to see its terminals.";
    tabs.innerHTML = `<span class="muted" style="padding:4px 8px">${hint}</span>`;
    return;
  }
  tabs.innerHTML = sessions.map((s) => {
    const isActive = s.id === state.activeSessionId;
    return `<span class="term-tab ${isActive ? "active" : ""}" data-sid="${esc(s.id)}" title="Click to switch · use the ✎ button to rename">
      <span class="term-tab-label">${esc(tabLabelFor(s))}</span>
      <span class="x" data-kill="${esc(s.id)}" title="Close">×</span>
    </span>`;
  }).join("");
  $$(".term-tab").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.target.dataset.kill) {
        killSession(e.target.dataset.kill);
        return;
      }
      const sid = el.dataset.sid;
      state.activeSessionId = sid;
      const sess = state.sessions.find((s) => s.id === sid);
      if (sess) {
        state.lastSessionByVault[sess.vault] = sid;
        // Re-sync the vault context so the toolbar label and sidebar
        // highlight match. Matters after a page reload when selectedVault
        // is null but live sessions still exist on the server.
        if (sess.vault !== state.selectedVault) {
          state.selectedVault = sess.vault;
          renderVaultList();
          renderVaultContext();
        }
      }
      renderSessions();
      renderActiveSession();
    });
  });
}

function renameActiveTab() {
  if (!state.activeSessionId) {
    alert("No active terminal tab to rename.");
    return;
  }
  renameTab(state.activeSessionId);
}

function renameTab(sid) {
  const sess = state.sessions.find((s) => s.id === sid);
  if (!sess) return;
  const current = tabLabelFor(sess);
  const body = `
    <p class="muted" style="font-size:11px;margin-bottom:6px">
      Tab labels are local to this browser. Leave blank to restore the
      default <code>${esc(defaultLabel(sess))}</code>.
    </p>
    <label>Tab name</label>
    <input id="rt-name" autocomplete="off" spellcheck="false" value="${esc(current)}" />
  `;
  showModal("Rename terminal tab", body, () => {
    const next = ($("#rt-name").value || "").trim();
    if (!next || next === defaultLabel(sess)) {
      delete state.tabLabels[sid];
    } else {
      state.tabLabels[sid] = next;
    }
    saveTabLabels();
    renderSessions();
    return true;
  });
  // Pre-select the input so the user can just type a new name.
  setTimeout(() => {
    const input = $("#rt-name");
    if (input) { input.focus(); input.select(); }
  }, 0);
}

function defaultLabel(s) {
  return `${s.vault} · ${s.session_type} · ${s.created_at.slice(11, 16)}`;
}

function renderActiveSession() {
  const root = $("#term-frames");
  const sessions = visibleSessions();
  // If the active session belongs to a different vault (or doesn't exist),
  // pick the first one belonging to the selected vault — otherwise we'd
  // render an iframe for a vault the sidebar isn't even pointing at.
  const activeBelongsHere = sessions.some((s) => s.id === state.activeSessionId);
  if (!activeBelongsHere) {
    state.activeSessionId = sessions.length ? sessions[0].id : null;
  }
  if (state.activeSessionId && state.selectedVault) {
    state.lastSessionByVault[state.selectedVault] = state.activeSessionId;
  }
  // Render every live session as an iframe so they stay alive in the
  // background, but only show iframes for the currently selected vault
  // (and only the active one is visible). Iframes for other vaults are
  // hidden — clicking another vault swaps which set is shown.
  root.innerHTML = state.sessions.map((s) => {
    const isActive = s.id === state.activeSessionId;
    const belongsHere = s.vault === state.selectedVault;
    const visible = (isActive && belongsHere) ? "" : "display:none;";
    // Build the ttyd URL from the page hostname so it works whether the user
    // hit the panel via 127.0.0.1, localhost, or a LAN IP (--public mode).
    const src = `${window.location.protocol}//${window.location.hostname}:${s.port}`;
    return `<iframe data-sid="${esc(s.id)}" style="${visible}"
                    src="${esc(src)}"></iframe>`;
  }).join("");
}

// ----- tasks -----
// Operation registry. This is the single source of truth that the trigger
// form reads to render per-op fields. Operations are hard-coded in
// plugin_commands.py + task_manager.py; mirrored here so we don't pay a
// round-trip just to learn what's next door.
const OPERATIONS = {
  "wiki-lint": {
    label: "Lint wiki", group: "Wiki", params: [],
  },
  "wiki-update-hot-cache": {
    label: "Update hot cache", group: "Wiki", params: [],
  },
  "wiki-bootstrap": {
    label: "Re-run wiki bootstrap", group: "Wiki", params: [],
    note: "Non-interactive re-run only; new vaults must use the wizard.",
  },
  "wiki-ingest": {
    label: "Ingest a URL", group: "Research",
    params: [
      { key: "url", type: "url", required: true, label: "URL", placeholder: "https://…" },
      { key: "update_canvas", type: "checkbox", required: false, label: "Update canvas after ingest (wiki/canvases/main.canvas)" },
    ],
  },
  "wiki-ingest-prefix": {
    label: "Ingest URL + prefix", group: "Research",
    params: [
      { key: "url", type: "url", required: true, label: "URL", placeholder: "https://…" },
      { key: "update_canvas", type: "checkbox", required: false, label: "Update canvas after ingest (wiki/canvases/main.canvas)" },
    ],
    note: "Runs the URL ingest under prompts/urlInjestPrefix.md — extracts technological substance from sources that discuss harmful applications and re-frames it for constructive use.",
  },
  "wiki-autoresearch": {
    label: "Autoresearch a topic", group: "Research",
    params: [{ key: "topic", type: "text", required: true, label: "Topic", maxLength: 200, placeholder: "topic to research" }],
  },
  "wiki-canvas": {
    label: "Update canvas (visual map)", group: "Wiki",
    params: [{ key: "description", type: "text", required: false, label: "Description (optional)", maxLength: 200, placeholder: "leave blank to use plugin defaults" }],
    note: "Runs /claude-obsidian:canvas. Description is optional — leave it blank and the plugin uses its own defaults.",
  },
  "run-prompt": {
    label: "Run a Claude prompt", group: "Custom",
    params: [{ key: "prompt", type: "text", required: true, label: "Prompt", maxLength: 200, placeholder: "/your-command or free text" }],
  },
  "run-shell": {
    label: "Run shell command", group: "Custom",
    params: [{ key: "cmd_parts", type: "argv", required: true, label: "Command (one argument per line)", placeholder: "echo\nhello" }],
    confirm: "run-shell executes an arbitrary command in the vault directory. Proceed?",
  },
};

// Tracks the inline-log subscription state per task_id.
// { open: bool, seeded: bool, autoscroll: bool }
state.taskLogs = state.taskLogs || {};

async function loadTasks() {
  const data = await api("/api/tasks");
  state.tasks = data.tasks || [];
  renderTasks();
  renderVaultList();
}

function operationIcon(op) {
  if (op === "wiki-ingest")        return "↘";
  if (op === "wiki-ingest-prefix") return "⇲";
  if (op === "wiki-lint")          return "✓";
  if (op === "wiki-update-hot-cache") return "⟳";
  if (op === "wiki-bootstrap")     return "★";
  if (op === "wiki-autoresearch")  return "🔎";
  if (op === "wiki-canvas")        return "▦";
  if (op === "run-prompt")         return "›";
  if (op === "run-shell")          return "$";
  return "•";
}

function taskStateIcon(s) {
  if (s === "running")      return "▶";
  if (s === "pending")      return "⌛";
  if (s === "scheduled")    return "⏰";
  if (s === "deferred")     return "⏸";
  if (s === "completed")    return "✓";
  if (s === "failed")       return "✗";
  if (s === "cancelled")    return "⊘";
  if (s === "interrupted")  return "⚠";
  if (s === "archived")     return "·";
  return "•";
}

function formatAge(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (!t) return "";
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (sec < 60) return sec + "s ago";
  const min = Math.round(sec / 60);
  if (min < 60) return min + "m ago";
  const hr = Math.round(min / 60);
  if (hr < 48) return hr + "h ago";
  return Math.round(hr / 24) + "d ago";
}

function isOverdueScheduled(t) {
  return t.state === "scheduled" && t.scheduled_for &&
    new Date(t.scheduled_for).getTime() <= Date.now();
}

function taskActions(t) {
  const acts = [];
  if (t.state === "scheduled") acts.push("run-now", "cancel");
  else if (t.state === "deferred") acts.push("promote", "cancel");
  else if (t.state === "pending") acts.push("cancel");
  else if (t.state === "running") acts.push("cancel");
  else if (["completed", "failed", "cancelled", "interrupted"].includes(t.state)) acts.push("re-run");
  return acts;
}

function renderTasks() {
  const root = $("#task-list");
  if (!root) return;
  const pf = ($("#task-priority-filter") || {}).value || "";
  const sf = ($("#task-state-filter") || {}).value || "active";
  let items = state.tasks.slice();
  if (pf) items = items.filter((t) => t.priority === pf);
  if (state.selectedVault) {
    items = items.filter((t) => t.vault === state.selectedVault || t.vault === "ALL");
  }
  if (sf === "active") {
    items = items.filter((t) => ["running", "pending", "deferred", "scheduled"].includes(t.state));
  } else if (sf === "recent") {
    const cutoff = Date.now() - 24 * 3600 * 1000;
    items = items.filter((t) => {
      const updated = new Date(t.updated_at).getTime();
      return updated && updated >= cutoff;
    });
  }
  if (!items.length) {
    root.innerHTML = `<p class="muted" style="padding:18px">No tasks match. Use the trigger above to run one.</p>`;
    return;
  }
  root.innerHTML = items.map((t) => taskCardHTML(t)).join("");
  $$(".task-card").forEach(wireTaskCard);
}

function taskCardHTML(t) {
  const tid = esc(t.id);
  const opMeta = OPERATIONS[t.operation] || { label: t.operation };
  const opLabel = esc(opMeta.label || t.operation);
  const icon = taskStateIcon(t.state);
  const vault = esc(t.vault) + (t.parent_id ? " ↳" : "");
  const overdue = isOverdueScheduled(t);
  let when = "";
  if (t.state === "running" && t.started_at)         when = "started " + esc(formatAge(t.started_at));
  else if (t.state === "scheduled" && t.scheduled_for) when = "fires " + esc(t.scheduled_for) + (overdue ? " · overdue" : "");
  else if (t.finished_at)                              when = esc(formatAge(t.finished_at));
  else if (t.updated_at)                               when = esc(formatAge(t.updated_at));

  const actions = taskActions(t).map((a) => {
    const cls = a === "cancel" ? "btn btn-xs btn-danger" : "btn btn-xs";
    return `<button class="${cls}" data-act="${esc(a)}" data-tid="${tid}">${esc(a)}</button>`;
  }).join("");

  const log = state.taskLogs[t.id] || {};
  const expanded = log.open ? "expanded" : "";
  const logBody = log.open
    ? `<div class="task-card-meta-row">
         <span>operation</span><span class="v">${esc(t.operation)}</span>
         <span>params</span><span class="v"><code>${esc(JSON.stringify(t.params || {}))}</code></span>
         <span>started</span><span class="v">${esc(t.started_at || "—")}</span>
         <span>finished</span><span class="v">${esc(t.finished_at || "—")}</span>
         ${t.scheduled_for ? `<span>scheduled for</span><span class="v ${overdue ? "task-overdue" : ""}">${esc(t.scheduled_for)}</span>` : ""}
         ${t.error ? `<span>error</span><span class="v" style="color:var(--danger)">${esc(t.error)}</span>` : ""}
       </div>
       <pre class="task-log-pane" id="log-${tid}" data-tid="${tid}"><span class="task-log-empty">loading log…</span></pre>`
    : "";

  return `<div class="task-card state-${esc(t.state)} ${expanded}" data-tid="${tid}">
    <div class="task-card-head" data-tid="${tid}">
      <span class="task-card-icon">${esc(icon)}</span>
      <span class="state-pill state-${esc(t.state)}">${esc(t.state)}</span>
      <span class="task-card-vault">${vault}</span>
      <span class="task-card-op">· ${opLabel}</span>
      <span class="task-card-meta">${when ? "· " + when : ""}</span>
      <span class="task-card-spacer"></span>
      <div class="task-card-actions">
        <button class="btn btn-xs" data-act="toggle-log" data-tid="${tid}">${log.open ? "hide log" : "log"}</button>
        ${actions}
      </div>
    </div>
    <div class="task-card-body">${logBody}</div>
  </div>`;
}

function wireTaskCard(card) {
  const tid = card.dataset.tid;
  card.querySelectorAll("button[data-act]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      taskAction(btn.dataset.act, btn.dataset.tid);
    });
  });
  const head = card.querySelector(".task-card-head");
  if (head) {
    head.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      toggleTaskLog(tid);
    });
  }
  // If a log was open before re-render, hydrate it from the cached buffer
  // so we don't lose streamed chunks across renders.
  if (state.taskLogs[tid]?.open) {
    const pre = card.querySelector(".task-log-pane");
    if (pre && state.taskLogs[tid].buffer != null) {
      pre.textContent = state.taskLogs[tid].buffer || "";
    } else if (pre) {
      seedLogPane(tid);
    }
  }
}

async function seedLogPane(tid) {
  const pre = document.querySelector(`#log-${CSS.escape(tid)}`);
  if (!pre) return;
  const log = state.taskLogs[tid] || (state.taskLogs[tid] = {});
  log.seeded = true;
  try {
    const txt = await apiText("/api/tasks/" + encodeURIComponent(tid) + "/log");
    log.buffer = txt || "";
    pre.textContent = log.buffer || "(empty)";
    pre.scrollTop = pre.scrollHeight;
  } catch (err) {
    pre.textContent = "log unavailable: " + err.message;
  }
}

function toggleTaskLog(tid) {
  const log = state.taskLogs[tid] || (state.taskLogs[tid] = {});
  log.open = !log.open;
  if (log.open) log.autoscroll = true;
  renderTasks();
  if (log.open && !log.seeded) seedLogPane(tid);
}

async function taskAction(act, tid) {
  if (act === "toggle-log") {
    toggleTaskLog(tid);
    return;
  }
  if (act === "cancel") {
    await api("/api/tasks/" + encodeURIComponent(tid), { method: "DELETE" });
    await loadTasks();
    return;
  }
  if (act === "promote" || act === "run-now") {
    await api("/api/tasks/" + encodeURIComponent(tid) + "/promote", { method: "POST" });
    await loadTasks();
    return;
  }
  if (act === "re-run") {
    const orig = state.tasks.find((t) => t.id === tid);
    if (orig) prefillTrigger(orig);
    return;
  }
}

// ----- task trigger panel -----
// `forceVault`: when provided (e.g., from selectVault), override any
// preserved dropdown value so clicking a sidebar vault activates it here too.
function renderTriggerForm(forceVault) {
  const vSel = $("#t-vault");
  const oSel = $("#t-op");
  if (!vSel || !oSel) return;
  const allEl = $("#t-all");
  const allChecked = !!(allEl && allEl.checked);
  const prevVault = vSel.value;
  vSel.innerHTML = state.vaults.map(
    (v) => `<option value="${esc(v.name)}">${esc(v.name)}</option>`
  ).join("");
  if (forceVault && state.vaults.find((v) => v.name === forceVault)) {
    vSel.value = forceVault;
  } else if (state.vaults.find((v) => v.name === prevVault)) {
    vSel.value = prevVault;
  } else if (state.selectedVault) {
    vSel.value = state.selectedVault;
  }
  vSel.disabled = allChecked;

  if (!oSel.options.length) {
    oSel.innerHTML = renderOpOptions();
  }
  renderOpFields();
}

function renderOpOptions(selected) {
  const groups = {};
  for (const [op, meta] of Object.entries(OPERATIONS)) {
    (groups[meta.group] ||= []).push([op, meta.label]);
  }
  return Object.entries(groups).map(([group, ops]) => {
    const opts = ops.map(([op, label]) =>
      `<option value="${esc(op)}" ${op === selected ? "selected" : ""}>${esc(label)}</option>`
    ).join("");
    return `<optgroup label="${esc(group)}">${opts}</optgroup>`;
  }).join("");
}

function renderOpFields(prefillParams) {
  const root = $("#t-params-row");
  if (!root) return;
  const opKey = $("#t-op").value;
  const meta = OPERATIONS[opKey];
  if (!meta) { root.innerHTML = ""; return; }
  const fields = (meta.params || []).map((p) => {
    const id = "t-p-" + p.key;
    const v = (prefillParams && prefillParams[p.key] != null) ? prefillParams[p.key] : "";
    if (p.type === "argv") {
      const val = Array.isArray(v) ? v.join("\n") : (v || "");
      return `<div class="param-row">
        <label for="${esc(id)}">${esc(p.label)}</label>
        <textarea id="${esc(id)}" data-key="${esc(p.key)}" data-type="argv"
                  placeholder="${esc(p.placeholder || "")}">${esc(val)}</textarea>
      </div>`;
    }
    if (p.type === "checkbox") {
      const checked = v === true || v === "true" || v === 1 || v === "1";
      return `<div class="param-row param-row-checkbox">
        <label for="${esc(id)}">
          <input id="${esc(id)}" type="checkbox" data-key="${esc(p.key)}" data-type="checkbox" ${checked ? "checked" : ""}>
          ${esc(p.label)}
        </label>
      </div>`;
    }
    const type = p.type === "url" ? "url" : "text";
    return `<div class="param-row">
      <label for="${esc(id)}">${esc(p.label)}</label>
      <input id="${esc(id)}" type="${esc(type)}" data-key="${esc(p.key)}" data-type="${esc(p.type)}"
             ${p.maxLength ? `maxlength="${p.maxLength}"` : ""}
             placeholder="${esc(p.placeholder || "")}"
             value="${esc(String(v))}">
    </div>`;
  }).join("");
  const note = meta.note ? `<div class="op-note">${esc(meta.note)}</div>` : "";
  root.innerHTML = fields + note;
}

function collectTriggerParams() {
  const params = {};
  $$("#t-params-row [data-key]").forEach((el) => {
    const key = el.dataset.key;
    if (el.dataset.type === "argv") {
      params[key] = el.value.split("\n").map((s) => s.trim()).filter(Boolean);
    } else if (el.dataset.type === "checkbox") {
      params[key] = !!el.checked;
    } else {
      params[key] = el.value;
    }
  });
  return params;
}

function prefillTrigger(orig) {
  // Re-run: populate the trigger form with the original task's settings,
  // pick the right vault/ALL state, and scroll the form into view.
  const allEl = $("#t-all");
  const vSel = $("#t-vault");
  const oSel = $("#t-op");
  const priSel = $("#t-pri");
  const whenInput = $("#t-when");
  if (orig.vault === "ALL") {
    if (allEl) allEl.checked = true;
    if (vSel) vSel.disabled = true;
  } else {
    if (allEl) allEl.checked = false;
    if (vSel) { vSel.disabled = false; vSel.value = orig.vault; }
  }
  if (oSel) oSel.value = orig.operation;
  if (priSel) priSel.value = orig.priority;
  if (whenInput) whenInput.value = "";  // re-run defaults to run-now
  renderOpFields(orig.params || {});
  const trigger = $("#task-trigger");
  if (trigger) trigger.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function submitTriggerForm() {
  const errEl = $("#t-error");
  if (errEl) errEl.textContent = "";
  const opKey = $("#t-op").value;
  const meta = OPERATIONS[opKey];
  if (!meta) return;

  const allChecked = $("#t-all").checked;
  const vault = allChecked ? "ALL" : $("#t-vault").value;
  if (!vault) { errEl.textContent = "Pick a vault first."; return; }

  const params = collectTriggerParams();
  for (const p of (meta.params || [])) {
    const v = params[p.key];
    if (p.required && (v == null || (Array.isArray(v) ? !v.length : !String(v).trim()))) {
      errEl.textContent = `Field '${p.label}' is required.`;
      return;
    }
  }

  let scheduled_for = null;
  const whenRaw = $("#t-when").value;
  if (whenRaw) {
    const dt = new Date(whenRaw);
    if (isNaN(dt.getTime())) {
      errEl.textContent = "Invalid datetime."; return;
    }
    if (dt.getTime() <= Date.now()) {
      errEl.textContent = "Scheduled time must be in the future."; return;
    }
    if (allChecked) {
      errEl.textContent = "Scheduling all-vault tasks is not supported yet."; return;
    }
    scheduled_for = dt.toISOString();
  }

  if (meta.confirm && !confirm(meta.confirm)) return;

  const body = {
    name: "task",
    vault,
    operation: opKey,
    params,
    priority: $("#t-pri").value,
  };
  if (scheduled_for) body.scheduled_for = scheduled_for;

  try {
    await api("/api/tasks", { method: "POST", body: JSON.stringify(body) });
    if ($("#t-when")) $("#t-when").value = "";
    await loadTasks();
  } catch (err) {
    errEl.textContent = "Create failed: " + (err.body?.error || err.message);
  }
}

// ----- wiki -----
const WIKI_HOME = "wiki/overview.md";
state.wikiFile = WIKI_HOME;
state.wikiTree = null;
state.wikiTreeMissing = false;

// Rewrite Obsidian wikilinks ([[Page]] or [[Page|alias]]) to inline anchors
// before marked.parse() runs. We emit a plain <a class="wikilink"> with the
// target stored on a data attribute; a delegated click handler on
// #wiki-content intercepts the navigation so it stays SPA-internal. The
// target is a page name (no .md, no leading wiki/) — resolved on click by
// loadWikiTarget(). Embeds (![[Foo]]) collapse to a link, which is good
// enough for v1; nobody embeds in claude-obsidian output today.
function rewriteWikilinks(md) {
  return (md || "").replace(/!?\[\[([^\]\|\n]+?)(?:\|([^\]\n]+?))?\]\]/g, (_m, target, alias) => {
    const t = (target || "").trim();
    const a = (alias || target || "").trim();
    return `<a href="#" class="wikilink" data-wiki-target="${esc(t)}">${esc(a)}</a>`;
  });
}

// Resolve a wikilink target ("Foo Bar" or "subdir/Foo") to a vault-relative
// file path. We try a few sensible candidates so authors don't need to be
// pedantic about the `.md` suffix or the `wiki/` prefix. The first hit in
// the cached tree wins; if nothing matches we still pass a best-effort path
// to loadWiki(), which surfaces the 404 in the content pane.
function resolveWikiTarget(target) {
  const t = (target || "").trim();
  if (!t) return null;
  if (t.endsWith(".md") && t.startsWith("wiki/")) return t;
  if (t.endsWith(".md")) return "wiki/" + t;
  const wantBase = (t.startsWith("wiki/") ? t.slice(5) : t).toLowerCase();
  const flat = flattenWikiTree(state.wikiTree || []);
  const hit = flat.find((f) => {
    const noExt = f.path.replace(/\.md$/i, "").toLowerCase();
    return noExt === "wiki/" + wantBase || noExt.endsWith("/" + wantBase) || noExt === wantBase;
  });
  if (hit) return hit.path;
  return "wiki/" + t + ".md";
}

function flattenWikiTree(nodes) {
  const out = [];
  const walk = (ns) => ns.forEach((n) => {
    if (n.type === "file") out.push(n);
    else if (n.children) walk(n.children);
  });
  walk(nodes);
  return out;
}

function renderWikiTree() {
  const root = $("#wiki-tree-list");
  if (!root) return;
  if (!state.selectedVault) {
    root.innerHTML = `<p class="muted" style="padding:8px 12px">Select a vault.</p>`;
    return;
  }
  if (state.wikiTreeMissing) {
    root.innerHTML = `<p class="muted" style="padding:8px 12px">
      No <code>wiki/</code> directory yet.</p>`;
    return;
  }
  const tree = state.wikiTree;
  if (!tree || !tree.length) {
    root.innerHTML = `<p class="muted" style="padding:8px 12px">Empty.</p>`;
    return;
  }
  const renderNodes = (nodes) => {
    return `<ul>` + nodes.map((n) => {
      if (n.type === "dir") {
        return `<li class="wiki-dir">
          <span class="wiki-tree-label">${esc(n.name)}/</span>
          ${renderNodes(n.children || [])}
        </li>`;
      }
      const label = n.name.replace(/\.md$/, "");
      const isActive = n.path === state.wikiFile;
      return `<li class="wiki-file ${isActive ? "active" : ""}">
        <span class="wiki-tree-label" data-path="${esc(n.path)}" title="${esc(n.path)}">${esc(label)}</span>
      </li>`;
    }).join("") + `</ul>`;
  };
  root.innerHTML = renderNodes(tree);
  root.querySelectorAll(".wiki-file > .wiki-tree-label").forEach((el) => {
    el.addEventListener("click", () => loadWiki(el.dataset.path));
  });
}

async function loadWikiTree() {
  if (!state.selectedVault) {
    state.wikiTree = null;
    state.wikiTreeMissing = false;
    renderWikiTree();
    return;
  }
  try {
    const data = await api("/api/vaults/" + encodeURIComponent(state.selectedVault) + "/wiki/tree");
    state.wikiTree = data.tree || [];
    state.wikiTreeMissing = !!data.missing;
  } catch (err) {
    state.wikiTree = [];
    state.wikiTreeMissing = false;
    const root = $("#wiki-tree-list");
    if (root) root.innerHTML = `<p class="wiki-error" style="padding:8px 12px">${esc(err.message)}</p>`;
    return;
  }
  renderWikiTree();
}

async function loadWiki(file) {
  const ctxEl = $("#wiki-context");
  const fileEl = $("#wiki-file");
  const root = $("#wiki-content");
  if (file) state.wikiFile = file;
  if (!state.selectedVault) {
    if (ctxEl) {
      ctxEl.textContent = "No vault selected";
      ctxEl.classList.add("empty");
    }
    if (fileEl) fileEl.textContent = "";
    root.innerHTML = `<p class="muted">Select a vault to view its wiki.</p>`;
    renderWikiTree();
    return;
  }
  if (ctxEl) {
    ctxEl.textContent = state.selectedVault;
    ctxEl.classList.remove("empty");
  }
  if (fileEl) fileEl.textContent = state.wikiFile;
  root.innerHTML = `<p class="muted">Loading…</p>`;
  renderWikiTree();
  const url = "/api/vaults/" + encodeURIComponent(state.selectedVault)
            + "/wiki?file=" + encodeURIComponent(state.wikiFile);
  let data;
  try {
    data = await api(url);
  } catch (err) {
    // Distinguish "no wiki yet" (404 on default home) from other errors so the
    // user is nudged toward generating one rather than chasing a bug.
    if (state.wikiFile === WIKI_HOME && /not found/i.test(err.message)) {
      root.innerHTML = `
        <div class="wiki-empty">
          <p>No wiki page found at <code>${esc(WIKI_HOME)}</code> yet.</p>
          <p class="muted">Open a Claude session for this vault and run the
          wiki plugin to generate one.</p>
        </div>`;
    } else {
      root.innerHTML = `<div class="wiki-error">${esc(err.message)}</div>`;
    }
    return;
  }
  if (!window.marked) {
    root.innerHTML = `<pre>${esc(data.content || "")}</pre>`;
    return;
  }
  const html = window.marked.parse(rewriteWikilinks(data.content || ""), { breaks: true });
  root.innerHTML = html;
}

// ----- help (in-app docs from man/) -----
state.helpTree = null;
state.helpFile = "index.md";

function renderHelpTree() {
  const root = $("#help-tree-list");
  if (!root) return;
  const tree = state.helpTree;
  if (!tree || !tree.length) {
    root.innerHTML = `<p class="muted" style="padding:8px 12px">
      No help pages found in <code>man/</code>.</p>`;
    return;
  }
  const renderNodes = (nodes) => {
    return `<ul>` + nodes.map((n) => {
      if (n.type === "dir") {
        return `<li class="help-dir">
          <span class="help-label">${esc(n.name)}/</span>
          ${renderNodes(n.children || [])}
        </li>`;
      }
      const label = n.name.replace(/\.md$/, "");
      const isActive = n.path === state.helpFile;
      return `<li class="help-file ${isActive ? "active" : ""}">
        <span class="help-label" data-path="${esc(n.path)}">${esc(label)}</span>
      </li>`;
    }).join("") + `</ul>`;
  };
  root.innerHTML = renderNodes(tree);
  root.querySelectorAll(".help-file > .help-label").forEach((el) => {
    el.addEventListener("click", () => {
      state.helpFile = el.dataset.path;
      renderHelpTree();
      loadHelpPage(state.helpFile);
    });
  });
}

async function loadHelp() {
  const content = $("#help-content");
  try {
    const data = await api("/api/help/tree");
    state.helpTree = data.tree || [];
    if (data.missing) {
      content.innerHTML = `
        <div class="wiki-empty">
          <p>The <code>man/</code> directory was not found at
          <code>${esc(data.root || "")}</code>.</p>
          <p class="muted">Set <code>app.man_path</code> in
          <code>resman.yaml</code> to point at your help tree, or create the
          directory and drop in some <code>.md</code> files.</p>
        </div>`;
      renderHelpTree();
      return;
    }
  } catch (err) {
    content.innerHTML = `<div class="wiki-error">${esc(err.message)}</div>`;
    return;
  }
  renderHelpTree();
  await loadHelpPage(state.helpFile);
}

async function loadHelpPage(file) {
  const content = $("#help-content");
  if (!file) file = "index.md";
  state.helpFile = file;
  content.innerHTML = `<p class="muted">Loading…</p>`;
  let data;
  try {
    data = await api("/api/help/page?file=" + encodeURIComponent(file));
  } catch (err) {
    content.innerHTML = `<div class="wiki-empty">
      <p>${esc(err.message)}</p>
    </div>`;
    return;
  }
  if (!window.marked) {
    content.innerHTML = `<pre>${esc(data.content || "")}</pre>`;
    return;
  }
  content.innerHTML = window.marked.parse(data.content || "", { breaks: true });
  // In-page links to other .md files re-route through the help tree rather
  // than navigate the browser away from the SPA.
  content.querySelectorAll("a[href]").forEach((a) => {
    const href = a.getAttribute("href") || "";
    if (!/\.md(?:#|$)/i.test(href)) return;
    if (/^https?:/i.test(href)) return;
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      const [path] = href.split("#");
      // Resolve relative to the current file's directory.
      const base = state.helpFile.split("/").slice(0, -1).join("/");
      const target = base ? base + "/" + path : path;
      // Collapse any "./" segments.
      const cleaned = target.split("/").filter((s) => s && s !== ".").join("/");
      state.helpFile = cleaned;
      renderHelpTree();
      loadHelpPage(cleaned);
    });
  });
}

// ----- vault health modal -----
async function showVaultHealth(vaultName) {
  try {
    const h = await api("/api/vaults/" + encodeURIComponent(vaultName) + "/health");
    const row = (label, value, kind) => {
      const cls = kind === "ok" ? "health-ok"
        : kind === "fail" ? "health-fail"
        : "health-empty";
      return `<tr><td>${esc(label)}</td><td class="${cls}">${esc(value)}</td></tr>`;
    };
    const yn = (b) => b ? "✓" : "✗";
    const body = `
      <table class="health-table"><tbody>
        ${row("Vault path", h.path, h.path_exists ? "ok" : "fail")}
        ${row("Path exists on disk", yn(h.path_exists), h.path_exists ? "ok" : "fail")}
        ${row(".obsidian/ present", yn(h.obsidian_dir), h.obsidian_dir ? "ok" : "fail")}
        ${row("Wiki home found", yn(h.wiki_home_exists), h.wiki_home_exists ? "ok" : "fail")}
        ${row("Last active session", h.last_session_at || "never",
              h.last_session_at ? "ok" : "empty")}
        ${row("Last completed task", h.last_completed_task_at || "none",
              h.last_completed_task_at ? "ok" : "empty")}
        ${row("Tags", (h.tags || []).join(", ") || "—",
              (h.tags && h.tags.length) ? "ok" : "empty")}
      </tbody></table>
    `;
    showModal("Vault health: " + vaultName, body);
  } catch (err) {
    alert("Health check failed: " + err.message);
  }
}

// ----- open in obsidian -----
async function openVaultInObsidian() {
  if (!state.selectedVault) {
    alert("Select a vault first.");
    return;
  }
  try {
    await api("/api/vaults/" + encodeURIComponent(state.selectedVault) + "/open", {
      method: "POST",
    });
  } catch (err) {
    alert("Could not launch Obsidian: " + err.message +
      "\n\nCheck that obsidian_cmd is set correctly in config/resman.yaml.");
  }
}

// ----- compact tasks log -----
async function compactTasksLog() {
  if (!confirm("Snapshot terminal-state tasks older than 90 days and rewrite tasks.jsonl?")) return;
  try {
    const r = await api("/api/tasks/compact", { method: "POST" });
    alert("Compacted " + (r.compacted || 0) + " tasks.");
    await loadTasks();
  } catch (err) {
    alert("Compact failed: " + err.message);
  }
}

// ----- cron skip warning banner -----
function showCronSkipBanner(payload) {
  const banner = $("#cron-skip-banner");
  const text = $("#cron-skip-text");
  if (!banner || !text) return;
  const when = payload?.last_fired_at
    ? new Date(payload.last_fired_at).toLocaleString()
    : "(never)";
  text.textContent = `Cron task "${payload?.cron_name || "?"}" has been skipped ${
    payload?.skip_count ?? "?"
  } times (window not active). Last fire attempt: ${when}.`;
  banner.hidden = false;
}

// ----- config -----
async function loadConfig() {
  const sel = $("#config-file");
  const file = sel.value;
  const data = await api("/api/config/yaml?file=" + encodeURIComponent(file));
  $("#config-editor").value = data.content || "";
  $("#config-status").textContent = "";
  // For the resman.yaml entry: relabel the dropdown option to reflect
  // whichever file is actually live (e.g. `~/.resman.yaml` when the
  // per-user override is in use), and surface it as a tooltip too.
  const resmanOpt = Array.from(sel.options).find((o) => o.value === "resman.yaml");
  if (resmanOpt && data.resman_display_path) {
    const label = data.using_user_override
      ? `${data.resman_display_path} (user override)`
      : data.resman_display_path;
    resmanOpt.textContent = label;
    resmanOpt.title = data.resman_path || "";
  }
}

async function saveConfig() {
  try {
    await api("/api/config/yaml", {
      method: "POST",
      body: JSON.stringify({
        file: $("#config-file").value,
        content: $("#config-editor").value,
      }),
    });
    $("#config-status").textContent = "saved";
    await loadVaults();
  } catch (err) {
    $("#config-status").textContent = "error: " + err.message;
  }
}

// ----- window status bar -----
async function loadWindow() {
  const w = await api("/api/window");
  state.window = w;
  renderWindow();
}

function renderWindow() {
  const w = state.window;
  const bar = $("#status-bar");
  bar.classList.remove("active", "between", "ended");
  bar.classList.add(w.window_state || "between");
  let label = "Window: " + (w.window_state || "between").toUpperCase();
  let time = "";
  let overrun = false;
  if (w.window_state === "active" && w.window_ends_at) {
    const ends = new Date(w.window_ends_at);
    const now = new Date();
    const diffSec = Math.floor((ends - now) / 1000);
    if (diffSec > 0) {
      const h = Math.floor(diffSec / 3600);
      const m = Math.floor((diffSec % 3600) / 60);
      time = `ends in ${h}h ${m}m`;
    } else {
      const overSec = -diffSec;
      const oh = Math.floor(overSec / 3600);
      const om = Math.floor((overSec % 3600) / 60);
      time = `overrun by ${oh}h ${om}m`;
      overrun = true;
    }
  }
  $("#window-label").textContent = label;
  $("#window-time").textContent = time;
  const overBtn = $("#btn-window-overrun");
  if (overBtn) overBtn.hidden = !overrun;
}

async function windowAction(action) {
  let payload = { action };
  if (action === "start") {
    const d = window.prompt("Window duration in hours (1-12)?", "5");
    if (!d) return;
    payload.duration_hours = parseFloat(d);
  }
  try {
    const w = await api("/api/window", { method: "POST", body: JSON.stringify(payload) });
    state.window = w;
    renderWindow();
    await loadTasks();
  } catch (err) {
    alert("Window action failed: " + err.message);
  }
}

// ----- modal helpers -----
function showModal(title, html, onSubmit) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = html;
  const footer = $("#modal-footer");
  footer.innerHTML = "";
  if (onSubmit) {
    const ok = document.createElement("button");
    ok.className = "primary"; ok.textContent = "OK";
    ok.addEventListener("click", async () => {
      const result = await onSubmit();
      if (result !== false) closeModal();
    });
    footer.appendChild(ok);
  }
  const cancel = document.createElement("button");
  cancel.textContent = "Close";
  cancel.addEventListener("click", closeModal);
  footer.appendChild(cancel);
  $("#modal-backdrop").hidden = false;
}
function closeModal() { $("#modal-backdrop").hidden = true; }

// ----- top-level loaders -----
async function loadVaults() {
  const data = await api("/api/vaults");
  const all = data.vaults || [];
  state.vaults = all.filter((v) => v.registered !== false);
  state.discovered = all.filter((v) => v.registered === false);
  renderVaultList();
  renderTriggerForm();
}

async function loadSessions() {
  const data = await api("/api/sessions");
  state.sessions = data.sessions || [];
  state.ttydAvailable = data.available;
  $("#ttyd-warning").hidden = data.available;
  renderSessions();
  renderActiveSession();
}

async function registerVault(name, path) {
  const goName = window.prompt("Confirm vault name:", name);
  if (!goName) return;
  try {
    await api("/api/vaults", {
      method: "POST",
      body: JSON.stringify({ name: goName, path, tags: [] }),
    });
    await loadVaults();
  } catch (err) {
    alert("Register failed: " + err.message);
  }
}

// Server-side folder picker. Browsers can't expose absolute host paths from
// <input type="file"> reliably, so we render our own using GET /api/fs/list.
// Returns a Promise resolving to the chosen path string, or null on cancel.
function pickFolder(initialPath) {
  return new Promise((resolve) => {
    const wrapper = document.createElement("div");
    wrapper.className = "modal-backdrop folder-picker-backdrop";
    wrapper.innerHTML = `
      <div class="modal folder-picker">
        <header>
          <h3>Choose folder</h3>
          <button class="close" data-act="cancel" aria-label="Cancel">×</button>
        </header>
        <div class="folder-picker-toolbar">
          <button class="btn btn-sm" data-act="up" title="Parent directory">↑ Up</button>
          <button class="btn btn-sm" data-act="home" title="Home">~ Home</button>
          <span class="folder-picker-path" id="fp-path">…</span>
        </div>
        <div class="folder-picker-list" id="fp-list">
          <div class="muted" style="padding:14px">Loading…</div>
        </div>
        <div class="folder-picker-newname">
          <label>Or create a new folder here (leave blank to use the current folder):</label>
          <input id="fp-new" placeholder="new-vault-name" autocomplete="off" spellcheck="false" />
        </div>
        <div class="folder-picker-result">
          <strong>Selected:</strong> <span id="fp-selected" class="muted">—</span>
        </div>
        <footer>
          <button class="btn btn-sm" data-act="cancel">Cancel</button>
          <button class="btn btn-sm btn-accent" data-act="ok">Use this path</button>
        </footer>
      </div>
    `;
    document.body.appendChild(wrapper);

    let currentPath = null;
    let newName = "";

    function selectedPath() {
      if (!currentPath) return null;
      const trimmed = newName.trim();
      if (!trimmed) return currentPath;
      const base = currentPath.replace(/\/+$/, "") || "";
      return base + "/" + trimmed;
    }

    function refreshSelected() {
      const sp = selectedPath();
      const el = wrapper.querySelector("#fp-selected");
      el.textContent = sp || "—";
      el.classList.toggle("muted", !sp);
    }

    async function navigate(path) {
      const list = wrapper.querySelector("#fp-list");
      list.innerHTML = `<div class="muted" style="padding:14px">Loading…</div>`;
      try {
        const url = "/api/fs/list" + (path ? "?path=" + encodeURIComponent(path) : "");
        const data = await api(url);
        currentPath = data.path;
        wrapper.querySelector("#fp-path").textContent = currentPath;
        if (!data.entries.length) {
          list.innerHTML = `<div class="muted" style="padding:14px">(no subdirectories — pick this folder, or create a new one below)</div>`;
        } else {
          list.innerHTML = data.entries.map((e) => `
            <button class="folder-row" data-path="${esc(e.path)}">
              <span class="folder-icon">📁</span>
              <span class="folder-name">${esc(e.name)}</span>
              ${e.is_obsidian ? '<span class="tag">vault</span>' : ""}
            </button>
          `).join("");
        }
        wrapper.querySelector("#fp-new").value = "";
        newName = "";
        refreshSelected();
      } catch (err) {
        list.innerHTML = `<div style="color:var(--danger);padding:14px">${esc(err.message)}</div>`;
      }
    }

    function cleanup(result) {
      wrapper.remove();
      resolve(result);
    }

    wrapper.addEventListener("click", (e) => {
      // Click outside the modal box itself = cancel.
      if (e.target === wrapper) return cleanup(null);
      const btn = e.target.closest("[data-act]");
      if (btn) {
        const act = btn.dataset.act;
        if (act === "cancel") return cleanup(null);
        if (act === "ok") {
          const sp = selectedPath();
          if (sp) cleanup(sp);
          return;
        }
        if (act === "up") {
          if (currentPath) {
            const parent = currentPath === "/" ? "/" :
              currentPath.replace(/\/[^/]+\/?$/, "") || "/";
            navigate(parent);
          }
          return;
        }
        if (act === "home") {
          navigate("~");
          return;
        }
      }
      const folder = e.target.closest(".folder-row");
      if (folder) navigate(folder.dataset.path);
    });

    wrapper.querySelector("#fp-new").addEventListener("input", (e) => {
      newName = e.target.value;
      refreshSelected();
    });

    // Allow Esc to cancel
    function escHandler(e) {
      if (e.key === "Escape") {
        document.removeEventListener("keydown", escHandler);
        cleanup(null);
      }
    }
    document.addEventListener("keydown", escHandler);

    navigate(initialPath || "~");
  });
}

// Two-step vault creation wizard:
//   1. POST /api/vaults/scaffold  — runs tools/new-vault.sh to create the
//      directory tree and `.obsidian/` placeholder.
//   2. POST /api/vaults           — appends the entry to resman.yaml.
// "Register existing" mode skips step 1 for a vault that already exists.
function showNewVaultWizard() {
  const body = `
    <label>Vault name <span class="muted">— letters, numbers, _ -</span></label>
    <input id="nv-name" autocomplete="off" />
    <label>Vault path <span class="muted">— absolute, e.g. /data/research/foo</span></label>
    <div class="path-input-row">
      <input id="nv-path" placeholder="/path/to/vault" autocomplete="off" />
      <button type="button" class="btn btn-sm" id="nv-browse">Browse…</button>
    </div>
    <label>Tags <span class="muted">— comma-separated, optional</span></label>
    <input id="nv-tags" placeholder="ai, llm" autocomplete="off" />
    <label style="display:flex;align-items:center;gap:6px;margin-top:14px">
      <input type="checkbox" id="nv-scaffold" checked style="width:auto;margin:0" />
      Scaffold the directory (run tools/new-vault.sh)
    </label>
    <p class="muted" style="font-size:11px;margin-top:2px;margin-left:22px">
      Creates path, .obsidian/, inbox/, README.md, and adds _resman/ to
      .gitignore. Uncheck to register an existing vault.
    </p>
    <label style="display:flex;align-items:center;gap:6px;margin-top:8px">
      <input type="checkbox" id="nv-bootstrap" checked style="width:auto;margin:0" />
      Bootstrap wiki — open Claude session and type <code>/claude-obsidian:wiki</code>
    </label>
    <p class="muted" style="font-size:11px;margin-top:2px;margin-left:22px">
      Opens an interactive Claude session in the new vault and types the
      slash command into it. The bootstrap command may ask questions —
      answer them in the Terminal tab. Requires ttyd installed locally.
    </p>
    <div id="nv-status"
         style="margin-top:14px;padding:8px 10px;border-radius:4px;
                background:var(--bg-elevated);font-size:12px;color:var(--text-secondary);
                display:none;"></div>
  `;
  showModal("New Vault", body, async () => {
    const name = $("#nv-name").value.trim();
    const path = $("#nv-path").value.trim();
    const tagsInput = $("#nv-tags").value.trim();
    const scaffold = $("#nv-scaffold").checked;
    const bootstrap = $("#nv-bootstrap").checked;
    const tags = tagsInput
      ? tagsInput.split(",").map((t) => t.trim()).filter(Boolean)
      : [];

    if (!name || !path) {
      setWizardStatus("Name and path are required.", "error");
      return false;
    }

    if (scaffold) {
      setWizardStatus("Scaffolding directory…", "info");
      try {
        const r = await api("/api/vaults/scaffold", {
          method: "POST",
          body: JSON.stringify({ name, path }),
        });
        setWizardStatus("Directory created at " + r.path, "info");
      } catch (err) {
        const detail = err.body && err.body.stderr ? "\n" + err.body.stderr : "";
        setWizardStatus("Scaffold failed: " + err.message + detail, "error");
        return false;
      }
    }

    setWizardStatus("Registering in resman.yaml…", "info");
    try {
      await api("/api/vaults", {
        method: "POST",
        body: JSON.stringify({ name, path, tags }),
      });
    } catch (err) {
      setWizardStatus("Register failed: " + err.message, "error");
      return false;
    }

    if (bootstrap) {
      if (!state.ttydAvailable) {
        setWizardStatus(
          "Vault registered. ttyd is not installed — install ttyd, open a " +
          "Claude session, then type /claude-obsidian:wiki",
          "error",
        );
      } else {
        setWizardStatus("Opening Claude session and sending /claude-obsidian:wiki…", "info");
        try {
          const sess = await api("/api/sessions", {
            method: "POST",
            body: JSON.stringify({
              vault: name,
              type: "claude",
              initial_command: "/claude-obsidian:wiki",
            }),
          });
          state.sessions.push(sess);
          state.activeSessionId = sess.id;
          setWizardStatus(
            "Vault ready. Claude session open in the Terminal tab — " +
            "the slash command is sent automatically; answer any prompts there.",
            "ok",
          );
        } catch (err) {
          setWizardStatus(
            "Vault registered, but failed to open Claude session: " + err.message +
            ". Open one manually and run /claude-obsidian:wiki.",
            "error",
          );
        }
      }
    } else {
      setWizardStatus(
        "Vault ready. Open a Claude session and run /claude-obsidian:wiki when you're ready to bootstrap.",
        "ok",
      );
    }

    await Promise.all([loadVaults(), loadSessions()]);
    selectVault(name);
    return true;
  });
  // Wire the Browse button after showModal renders the body. The picker
  // opens stacked above the wizard via a higher z-index so the wizard is
  // not destroyed — picking returns control here.
  const browseBtn = $("#nv-browse");
  if (browseBtn) {
    browseBtn.addEventListener("click", async () => {
      const start = $("#nv-path").value.trim() || null;
      const picked = await pickFolder(start);
      if (picked) $("#nv-path").value = picked;
    });
  }
}

function setWizardStatus(text, kind) {
  const el = $("#nv-status");
  if (!el) return;
  el.style.display = "block";
  el.textContent = text;
  if (kind === "error") {
    el.style.color = "var(--danger)";
    el.style.borderLeft = "3px solid var(--danger)";
  } else if (kind === "ok") {
    el.style.color = "var(--success)";
    el.style.borderLeft = "3px solid var(--success)";
  } else {
    el.style.color = "var(--text-secondary)";
    el.style.borderLeft = "3px solid var(--accent)";
  }
}

// ----- wiring -----
function setupTabs() {
  $$("#header-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => showPanel(tab.dataset.tab));
  });
}

function setupFilters() {
  $("#vault-search").addEventListener("input", (e) => {
    state.filter.search = e.target.value;
    renderVaultList();
  });
  $("#status-filter").addEventListener("change", (e) => {
    state.filter.status = e.target.value;
    renderVaultList();
  });
}

function setupToolbar() {
  $("#btn-claude").addEventListener("click", () => {
    if (state.selectedVault) spawnSession(state.selectedVault, "claude");
    else alert("Select a vault first.");
  });
  $("#btn-shell").addEventListener("click", () => {
    if (state.selectedVault) spawnSession(state.selectedVault, "shell");
    else alert("Select a vault first.");
  });
  // Clicking the vault-name label in the header jumps to the Ops panel
  // (terminal sessions). Equivalent to clicking the Ops header tab — the
  // label is the fast path when the user is reading wiki content.
  const vctx = $("#vault-context");
  if (vctx) vctx.addEventListener("click", () => {
    if (state.selectedVault) showPanel("ops");
  });
  const btnRename = $("#btn-rename-tab");
  if (btnRename) btnRename.addEventListener("click", renameActiveTab);
  const btnObs = $("#btn-obsidian");
  if (btnObs) btnObs.addEventListener("click", openVaultInObsidian);
  const btnCompact = $("#btn-task-compact");
  if (btnCompact) btnCompact.addEventListener("click", compactTasksLog);
  const btnWikiRefresh = $("#btn-wiki-refresh");
  if (btnWikiRefresh) btnWikiRefresh.addEventListener("click", () => {
    loadWikiTree();
    loadWiki();
  });
  const btnWikiTreeRefresh = $("#btn-wiki-tree-refresh");
  if (btnWikiTreeRefresh) btnWikiTreeRefresh.addEventListener("click", loadWikiTree);
  // Hot / Index / Overview — declarative wiring via data-wiki-page so adding
  // another canonical page later is just an HTML edit.
  document.querySelectorAll("[data-wiki-page]").forEach((btn) => {
    btn.addEventListener("click", () => loadWiki(btn.dataset.wikiPage));
  });
  // Delegated click handler for [[wikilink]] anchors rendered inside the
  // wiki content pane. Keeps navigation SPA-internal — the markdown is
  // re-rendered without a browser nav.
  const wikiContent = $("#wiki-content");
  if (wikiContent) {
    wikiContent.addEventListener("click", (e) => {
      const a = e.target.closest("a.wikilink");
      if (!a) return;
      e.preventDefault();
      const target = a.dataset.wikiTarget;
      const resolved = resolveWikiTarget(target);
      if (resolved) loadWiki(resolved);
    });
  }
  const btnHelpRefresh = $("#btn-help-refresh");
  if (btnHelpRefresh) btnHelpRefresh.addEventListener("click", loadHelp);
  const cronDismiss = $("#cron-skip-dismiss");
  if (cronDismiss) cronDismiss.addEventListener("click", () => {
    $("#cron-skip-banner").hidden = true;
  });
  $("#task-priority-filter").addEventListener("change", renderTasks);
  const stateFilter = $("#task-state-filter");
  if (stateFilter) stateFilter.addEventListener("change", renderTasks);
  const btnRun = $("#btn-task-run");
  if (btnRun) btnRun.addEventListener("click", submitTriggerForm);
  const allBox = $("#t-all");
  if (allBox) allBox.addEventListener("change", renderTriggerForm);
  const opSel = $("#t-op");
  if (opSel) opSel.addEventListener("change", () => renderOpFields());
  $("#config-file").addEventListener("change", loadConfig);
  $("#btn-config-save").addEventListener("click", saveConfig);
  $("#btn-new-vault").addEventListener("click", showNewVaultWizard);
  const refresh = $("#btn-refresh");
  if (refresh) refresh.addEventListener("click",
    () => Promise.all([loadVaults(), loadSessions(), loadTasks()]));
  $("#modal-close").addEventListener("click", closeModal);
}

function setupStatusBar() {
  $("#btn-sync").addEventListener("click", () => {
    const m = $("#sync-menu");
    m.hidden = !m.hidden;
  });
  $$("#sync-menu button").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("#sync-menu").hidden = true;
      windowAction(btn.dataset.action);
    });
  });
  $("#btn-theme").addEventListener("click", toggleTheme);
  const overBtn = $("#btn-window-overrun");
  if (overBtn) overBtn.addEventListener("click", () => windowAction("end"));
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "dark";
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("resman-theme", next); } catch (_) {}
}

function setConn(state) {
  const dot = $("#conn-dot"); const lbl = $("#conn-label");
  if (!dot || !lbl) return;
  dot.classList.remove("connected", "disconnected");
  if (state === "connected") {
    dot.classList.add("connected");
    lbl.textContent = "connected";
  } else if (state === "disconnected") {
    dot.classList.add("disconnected");
    lbl.textContent = "disconnected";
  } else {
    lbl.textContent = "connecting...";
  }
}

function setupSocket() {
  try {
    const sock = io();
    sock.on("connect", () => setConn("connected"));
    sock.on("disconnect", () => setConn("disconnected"));
    sock.on("task_updated", () => loadTasks());
    sock.on("task_log_appended", (msg) => {
      if (!msg || !msg.task_id) return;
      const log = state.taskLogs[msg.task_id] || (state.taskLogs[msg.task_id] = {});
      log.buffer = (log.buffer || "") + (msg.chunk || "");
      if (!log.open) return;
      const pre = document.querySelector(`#log-${CSS.escape(msg.task_id)}`);
      if (!pre) return;
      // Clear placeholder on first chunk
      if (pre.firstElementChild && pre.firstElementChild.classList.contains("task-log-empty")) {
        pre.textContent = "";
      }
      pre.textContent += msg.chunk || "";
      if (log.autoscroll !== false) pre.scrollTop = pre.scrollHeight;
    });
    sock.on("window_state_changed", () => loadWindow());
    sock.on("session_crashed", (p) => {
      alert("Terminal session crashed: " + (p?.message || ""));
      loadSessions();
    });
    sock.on("config_reloaded", () => loadVaults());
    sock.on("cron_skip_warning", (p) => {
      console.warn("cron skip warning", p);
      showCronSkipBanner(p);
    });
  } catch (e) {
    console.warn("Socket.IO unavailable:", e);
    setConn("disconnected");
  }
}

async function init() {
  setupTabs();
  setupFilters();
  setupToolbar();
  setupStatusBar();
  setupSocket();
  await Promise.all([loadVaults(), loadTasks(), loadSessions(), loadWindow()]);
  setInterval(renderWindow, 30 * 1000);
}

document.addEventListener("DOMContentLoaded", init);
