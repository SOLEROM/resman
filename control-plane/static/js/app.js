// resman SPA — vanilla JS, no build step.

const state = {
  vaults: [],
  discovered: [],
  // Landing-page cards: one entry per registered vault paired with its
  // generated wiki/hint.json (fetched from /api/landing). Powers the home
  // screen the logo opens.
  landing: [],
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
  // Volatile activity log — a client mirror of the server's /tmp log, fed by
  // the "activity_logged" socket event. `logOpen` tracks the window; `logFilter`
  // is the current minimum level; `logUnseenError` lights the footer dot.
  activityLog: [],
  logOpen: false,
  logFilter: "",
  logUnseenError: false,
  // ⊞ Windows tab: cached schedule draft + usage-stats payload + chart range.
  windowSchedule: null,
  windowStats: null,
  windowStatsRange: 30,  // days shown in the usage charts (7 | 30 | 90)
};

const ACTIVITY_LOG_MAX = 2000;

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
  // History is per-vault — drop the previous vault's trail before landing.
  state.wikiHistory = [];
  state.wikiHistoryIdx = -1;
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
  if (tabName === "windows") loadWindowsTab();
  if (tabName === "home") loadLanding();
  if (tabName === "wiki" && state.selectedVault) loadWikiTree();
  // "home" and "help" are vault-independent global views — don't pin them as
  // a vault's remembered panel (would dump the user back onto the landing /
  // help screen the next time they re-select that vault).
  if (state.selectedVault && tabName !== "help" && tabName !== "home") {
    state.lastPanelByVault[state.selectedVault] = tabName;
    saveLastPanelByVault();
  }
}

// ----- landing / home page (vault thumbnails) -----
function isHomeActive() {
  const p = $("#tab-home");
  return !!(p && p.classList.contains("active"));
}

async function loadLanding() {
  try {
    const data = await api("/api/landing");
    state.landing = data.vaults || [];
  } catch (_) {
    state.landing = [];
  }
  renderLanding();
}

function renderLanding() {
  const root = $("#landing-grid");
  if (!root) return;
  const count = $("#landing-count");
  const vaults = state.landing;
  if (count) {
    count.textContent = vaults.length
      ? `${vaults.length} vault${vaults.length > 1 ? "s" : ""}` : "";
  }
  if (!vaults.length) {
    root.innerHTML =
      `<div class="landing-empty muted">No vaults configured yet — add one with ` +
      `<strong>+ New Vault</strong> in the sidebar and it will appear here.</div>`;
    return;
  }
  root.innerHTML = vaults.map(landingCardHTML).join("");
  root.querySelectorAll(".vault-card").forEach((card) => {
    const open = () => openVaultFromLanding(card.dataset.vault);
    card.addEventListener("click", open);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });
  });
}

function landingCardHTML(v) {
  const hint = v.hint || {};
  const title = (hint.label && hint.label.trim()) || v.name;
  const showId = title !== v.name;
  // Status dot reuses the sidebar's session/task colour rule.
  const color = vaultColor({ name: v.name });
  const tags = (hint.tags && hint.tags.length) ? hint.tags : (v.tags || []);
  const shown = tags.slice(0, 8);
  const tagsHTML = shown.map((t) => `<span class="tag">${esc(t)}</span>`).join("") +
    (tags.length > shown.length
      ? `<span class="tag tag-more">+${tags.length - shown.length}</span>` : "");
  const summary = (hint.summary && hint.summary.trim())
    ? esc(hint.summary)
    : `<span class="muted">No description yet — run the wiki bootstrap to generate one.</span>`;
  const warn = !v.path_exists
    ? `<span class="vault-warn" title="path not found">⚠</span>`
    : (!v.is_obsidian ? `<span class="vault-warn" title="missing .obsidian/">?</span>` : "");
  const foot = [];
  if (hint.updatedBy) foot.push(`<span class="lc-by">${esc(hint.updatedBy)}</span>`);
  if (hint.updatedAt) {
    const rel = formatAge(hint.updatedAt);
    if (rel) foot.push(`<span class="lc-when" title="${esc(hint.updatedAt)}">${esc(rel)}</span>`);
  }
  return `
    <article class="vault-card" data-vault="${esc(v.name)}" role="button" tabindex="0"
             title="Open ${esc(v.name)} wiki">
      <header class="lc-head">
        <span class="vault-dot vault-dot-${color}" title="${esc(vaultDotTitle({ name: v.name }))}"></span>
        <h3 class="lc-title">${esc(title)}</h3>
        ${warn}
      </header>
      ${showId ? `<div class="lc-id">${esc(v.name)}</div>` : ""}
      <p class="lc-summary">${summary}</p>
      ${tagsHTML ? `<div class="lc-tags">${tagsHTML}</div>` : ""}
      ${foot.length ? `<footer class="lc-foot">${foot.join('<span class="lc-sep">·</span>')}</footer>` : ""}
    </article>`;
}

// Clicking a landing card selects the vault and drops the user straight onto
// its wiki (the explicit ask — a card is a shortcut to "read this vault").
function openVaultFromLanding(name) {
  selectVault(name);
  showPanel("wiki");
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
    desc: "Scan for orphans, dead links and frontmatter gaps; emit a report.",
  },
  "wiki-update-hot-cache": {
    label: "Update hot cache", group: "Wiki", params: [],
    desc: "Refresh the wiki's hot-cache index of recently active pages.",
  },
  "wiki-bootstrap": {
    label: "Re-run wiki bootstrap", group: "Wiki", params: [],
    desc: "Re-run the wiki bootstrap for an existing vault.",
    note: "Non-interactive re-run only; new vaults must use the wizard.",
  },
  "wiki-hint": {
    label: "Generate hint (vault description)", group: "Wiki", params: [],
    desc: "Write wiki/hint.json — the label, summary and tags on the vault card.",
    note: "Inspects the wiki and writes wiki/hint.json — the label, summary and tags shown on this vault's landing-page card.",
  },
  "wiki-ingest": {
    label: "Ingest a URL", group: "Research",
    desc: "Fetch a URL and write structured pages into the wiki.",
    params: [
      { key: "url", type: "url", required: true, label: "URL", placeholder: "https://…" },
      { key: "update_canvas", type: "checkbox", required: false, label: "Update canvas after ingest (wiki/canvases/main.canvas)" },
    ],
  },
  "wiki-ingest-prefix": {
    label: "Ingest URL + prefix", group: "Research",
    desc: "Ingest a URL and re-frame harmful framing around constructive uses.",
    params: [
      { key: "url", type: "url", required: true, label: "URL", placeholder: "https://…" },
      { key: "update_canvas", type: "checkbox", required: false, label: "Update canvas after ingest (wiki/canvases/main.canvas)" },
    ],
    note: "Runs the URL ingest under prompts/urlInjestPrefix.md — extracts technological substance from sources that discuss harmful applications and re-frames it for constructive use.",
  },
  "wiki-autoresearch": {
    label: "Autoresearch a topic", group: "Research",
    desc: "Plan searches, fetch sources, and synthesize new wiki pages on a topic.",
    params: [{ key: "topic", type: "text", required: true, label: "Topic", maxLength: 200, placeholder: "topic to research" }],
  },
  "wiki-canvas": {
    label: "Update canvas (visual map)", group: "Wiki",
    desc: "Re-organize wiki/canvases/main.canvas around active topics.",
    params: [{ key: "description", type: "text", required: false, label: "Description (optional)", maxLength: 200, placeholder: "leave blank to use plugin defaults" }],
    note: "Runs /claude-obsidian:canvas. Description is optional — leave it blank and the plugin uses its own defaults.",
  },
  "run-prompt": {
    label: "Run a Claude prompt", group: "Custom",
    desc: "Run an arbitrary Claude prompt or slash-command against the vault.",
    params: [{ key: "prompt", type: "text", required: true, label: "Prompt", maxLength: 200, placeholder: "/your-command or free text" }],
  },
  "run-shell": {
    label: "Run shell command", group: "Custom",
    desc: "Run a shell command in the vault directory.",
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
  // Keep landing-card status dots live while the home screen is open.
  if (isHomeActive()) renderLanding();
}

function operationIcon(op) {
  if (op === "wiki-ingest")        return "↘";
  if (op === "wiki-ingest-prefix") return "⇲";
  if (op === "wiki-lint")          return "✓";
  if (op === "wiki-update-hot-cache") return "⟳";
  if (op === "wiki-bootstrap")     return "★";
  if (op === "wiki-hint")          return "ℹ";
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

// Operations whose execution is "claude -p <prompt>" — those can be reopened
// in a live Claude REPL via POST /api/tasks/<id>/attend so the user can
// answer prompts the original non-interactive run couldn't. Shell-based ops
// (wiki-ingest, wiki-ingest-prefix, run-shell) aren't attendable.
const ATTENDABLE_OPERATIONS = new Set([
  "wiki-lint",
  "wiki-autoresearch",
  "wiki-canvas",
  "wiki-update-hot-cache",
  "wiki-bootstrap",
  "wiki-hint",
  "run-prompt",
]);

function taskActions(t) {
  const acts = [];
  if (t.state === "scheduled") acts.push("run-now", "cancel");
  else if (t.state === "deferred") acts.push("promote", "cancel");
  else if (t.state === "pending") acts.push("cancel");
  else if (t.state === "running") acts.push("cancel");
  else if (["completed", "failed", "cancelled", "interrupted"].includes(t.state)) {
    acts.push("re-run");
    if (ATTENDABLE_OPERATIONS.has(t.operation) && t.vault !== "ALL") {
      acts.push("attend");
    }
  }
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
  if (act === "attend") {
    try {
      const sess = await api("/api/tasks/" + encodeURIComponent(tid) + "/attend", {
        method: "POST",
      });
      state.sessions.push(sess);
      state.activeSessionId = sess.id;
      state.lastSessionByVault[sess.vault] = sess.id;
      if (state.selectedVault !== sess.vault) selectVault(sess.vault);
      renderSessions();
      renderActiveSession();
      showPanel("ops");
    } catch (err) {
      alert("Attend failed: " + (err.body?.error || err.message));
    }
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

  const list = $("#t-op-list");
  if (list && !list.children.length) renderOpCards();
  // Ensure an operation is selected (default to the first). selectOp renders
  // the detail header/summary + parameter fields for the chosen op.
  selectOp(oSel.value || Object.keys(OPERATIONS)[0]);
  renderTriggerWindowOptions();
}

// Populate the "When" picker with the upcoming windows from the schedule, so a
// task can be aimed at a specific window (or "run now"). A still-valid prior
// selection is preserved across rebuilds (the list refreshes as countdowns tick).
function renderTriggerWindowOptions() {
  const sel = $("#t-window");
  if (!sel) return;
  const prev = sel.value;
  const up = (state.windowSchedule && state.windowSchedule.status
              && state.windowSchedule.status.upcoming) || [];
  const opts = [`<option value="">run now</option>`];
  for (const w of up) {
    const label = `Window ${w.index}/${w.count} · ${clockOf(w.start)}`
      + (w.night ? " 🌙" : "")
      + ` · in ${fmtCountdown(w.seconds_until_start)}`;
    opts.push(`<option value="${esc(w.start)}">${esc(label)}</option>`);
  }
  sel.innerHTML = opts.join("");
  if (prev && up.some((w) => w.start === prev)) sel.value = prev;
}

// Build the left-hand operation picker: garage-style cards grouped by
// OPERATIONS[].group. Each card carries its op key in data-op; clicking one
// routes through selectOp (delegated handler wired in bindEvents).
function renderOpCards() {
  const list = $("#t-op-list");
  if (!list) return;
  const groups = {};
  for (const [op, meta] of Object.entries(OPERATIONS)) {
    (groups[meta.group] ||= []).push(op);
  }
  list.innerHTML = Object.entries(groups).map(([group, ops]) => {
    const cards = ops.map((op) => {
      const meta = OPERATIONS[op];
      return `<li>
        <button type="button" role="radio" aria-checked="false"
                class="kind-card" data-op="${esc(op)}">
          <span class="kind-card-title">
            <span class="kind-card-icon" aria-hidden="true">${esc(operationIcon(op))}</span>
            ${esc(meta.label)}
          </span>
          <span class="kind-card-help">${esc(meta.desc || "")}</span>
        </button>
      </li>`;
    }).join("");
    return `<li class="kind-group-label" aria-hidden="true">${esc(group)}</li>${cards}`;
  }).join("");
}

// Select an operation: highlight its card, fill the detail header/summary, and
// render its parameter fields. `#t-op` (a hidden input) holds the value the
// rest of the trigger code reads. `prefillParams` re-populates fields on re-run.
function selectOp(opKey, prefillParams) {
  const meta = OPERATIONS[opKey];
  if (!meta) return;
  const oSel = $("#t-op");
  if (oSel) oSel.value = opKey;
  $$("#t-op-list .kind-card").forEach((btn) => {
    const on = btn.dataset.op === opKey;
    btn.classList.toggle("is-selected", on);
    btn.setAttribute("aria-checked", on ? "true" : "false");
  });
  const titleEl = $("#t-op-title");
  if (titleEl) titleEl.textContent = meta.label;
  const summaryEl = $("#t-op-summary");
  if (summaryEl) summaryEl.textContent = meta.desc || "";
  renderOpFields(prefillParams);
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
  const priSel = $("#t-pri");
  const whenSel = $("#t-window");
  if (orig.vault === "ALL") {
    if (allEl) allEl.checked = true;
    if (vSel) vSel.disabled = true;
  } else {
    if (allEl) allEl.checked = false;
    if (vSel) { vSel.disabled = false; vSel.value = orig.vault; }
  }
  if (priSel) priSel.value = orig.priority;
  if (whenSel) whenSel.value = "";  // re-run defaults to run-now
  selectOp(orig.operation, orig.params || {});
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
  const whenSel = $("#t-window");
  const whenRaw = whenSel ? whenSel.value : "";
  if (whenRaw) {
    // Value is the chosen window's naive-local start ISO → convert to UTC.
    const dt = new Date(whenRaw);
    if (isNaN(dt.getTime())) {
      errEl.textContent = "Invalid window selection."; return;
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
  if (scheduled_for) {
    body.scheduled_for = scheduled_for;
  } else {
    // "run now" must dispatch immediately. Without this the backend gates the
    // task on the active work window and parks it as `deferred` when the window
    // is closed, so it sits idle until the user manually promotes it (▶).
    body.force = true;
  }

  try {
    await api("/api/tasks", { method: "POST", body: JSON.stringify(body) });
    if (whenSel) whenSel.value = "";
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
// Vault-relative paths (e.g. "wiki/concepts/gguf.md") that are currently
// unread, derived from the tree response. Drives the tree dots + read toggle.
state.wikiUnread = new Set();

// Browser-style back/forward history of visited pages, scoped to the current
// vault (reset on vault switch). wikiHistory is the ordered list of files;
// wikiHistoryIdx points at the page currently shown. Navigating to a new page
// truncates anything ahead of the pointer (same as a browser); the Back/Forward
// buttons just walk the pointer without recording a new entry.
state.wikiHistory = [];
state.wikiHistoryIdx = -1;

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

// Render a markdown page, folding its leading metadata into a collapsed
// <details> so the page leads with its first heading instead of a wall of
// `key: value` frontmatter lines. "Metadata" means the YAML frontmatter block
// (--- ... ---) plus any stray prose that sits before the first heading. We
// detect the frontmatter first, then look for the heading in what's left, so a
// `# comment` line inside the YAML can't be mistaken for the heading. Pages
// with no frontmatter and no pre-heading prose render straight through.
// `wikilinks` toggles Obsidian [[link]] rewriting (on for the wiki, off for
// help/man pages, matching the previous per-call behaviour).
function renderWikiMarkdown(raw, { wikilinks = true } = {}) {
  const text = raw || "";
  const render = (s) =>
    window.marked.parse(wikilinks ? rewriteWikilinks(s) : s, { breaks: true });

  const fm = text.match(/^\uFEFF?---\r?\n([\s\S]*?)\r?\n---[ \t]*\r?\n?/);
  const afterFm = fm ? text.slice(fm[0].length) : text;
  const headingIdx = afterFm.search(/^#{1,6}\s/m);

  // Pre-heading prose only exists (and only folds) when a heading follows it;
  // with no heading the remainder is the body and stays visible.
  const preProse = headingIdx > 0 ? afterFm.slice(0, headingIdx) : "";
  const body = headingIdx > 0 ? afterFm.slice(headingIdx) : afterFm;

  const foldProse = preProse.trim();
  if (!fm && !foldProse) return render(body);

  let head = "";
  if (fm) head += `<pre class="wiki-fm">${esc(fm[1])}</pre>`;
  if (foldProse) head += render(preProse);

  return `<details class="wiki-frontmatter"><summary>metadata</summary>`
       + `<div class="wiki-fm-body">${head}</div></details>`
       + render(body);
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
  const fileUnread = (n) => state.wikiUnread.has(n.path);
  const dirHasUnread = (n) => {
    let any = false;
    const walk = (ns) => ns.forEach((c) => {
      if (any) return;
      if (c.type === "file") { if (fileUnread(c)) any = true; }
      else if (c.children) walk(c.children);
    });
    walk(n.children || []);
    return any;
  };
  const renderNodes = (nodes) => {
    return `<ul>` + nodes.map((n) => {
      if (n.type === "dir") {
        const u = dirHasUnread(n) ? "has-unread" : "";
        return `<li class="wiki-dir ${u}">
          <span class="wiki-tree-label">${esc(n.name)}/</span>
          ${renderNodes(n.children || [])}
        </li>`;
      }
      const label = n.name.replace(/\.md$/, "");
      const isActive = n.path === state.wikiFile;
      const unread = fileUnread(n) ? "unread" : "";
      return `<li class="wiki-file ${isActive ? "active" : ""} ${unread}">
        <span class="wiki-tree-label" data-path="${esc(n.path)}" title="${esc(n.path)}"
              ><span class="wiki-unread-dot" aria-hidden="true"></span>${esc(label)}</span>
      </li>`;
    }).join("") + `</ul>`;
  };
  root.innerHTML = renderNodes(tree);
  root.querySelectorAll(".wiki-file > .wiki-tree-label").forEach((el) => {
    el.addEventListener("click", () => loadWiki(el.dataset.path));
  });
  // Keep the selected page visible in the sidebar after a jump.
  const activeEl = root.querySelector(".wiki-file.active");
  if (activeEl) activeEl.scrollIntoView({ block: "nearest" });
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
    rebuildWikiUnread();
  } catch (err) {
    state.wikiTree = [];
    state.wikiTreeMissing = false;
    state.wikiUnread = new Set();
    const root = $("#wiki-tree-list");
    if (root) root.innerHTML = `<p class="wiki-error" style="padding:8px 12px">${esc(err.message)}</p>`;
    return;
  }
  renderWikiTree();
  updateReadToggle();
}

// Recompute the unread set from the cached tree (each file node carries an
// `unread` flag the backend set during reconcile).
function rebuildWikiUnread() {
  const set = new Set();
  const walk = (nodes) => (nodes || []).forEach((n) => {
    if (n.type === "file") { if (n.unread) set.add(n.path); }
    else if (n.children) walk(n.children);
  });
  walk(state.wikiTree || []);
  state.wikiUnread = set;
}

// Record a freshly-navigated page onto the history stack. No-op reloads (the
// refresh button, restoring after a cleared search) land on the same file as
// the current pointer and are collapsed so Back doesn't bounce in place.
function pushWikiHistory(file) {
  if (!file) return;
  if (state.wikiHistory[state.wikiHistoryIdx] === file) return;
  state.wikiHistory = state.wikiHistory.slice(0, state.wikiHistoryIdx + 1);
  state.wikiHistory.push(file);
  state.wikiHistoryIdx = state.wikiHistory.length - 1;
}

// Walk the history pointer by delta (-1 = Back, +1 = Forward) and load the
// page there without re-recording it. Out-of-range deltas are ignored.
function goWikiHistory(delta) {
  const idx = state.wikiHistoryIdx + delta;
  if (idx < 0 || idx >= state.wikiHistory.length) return;
  state.wikiHistoryIdx = idx;
  loadWiki(state.wikiHistory[idx], { fromHistory: true });
}

// Enable/disable the Back/Forward buttons to reflect where the pointer sits.
function updateWikiNavButtons() {
  const back = $("#btn-wiki-back");
  const fwd = $("#btn-wiki-fwd");
  if (back) back.disabled = state.wikiHistoryIdx <= 0;
  if (fwd) fwd.disabled = state.wikiHistoryIdx >= state.wikiHistory.length - 1;
}

// opts.fromHistory: true when invoked by the Back/Forward buttons, so the
// page isn't re-recorded into history (which would defeat the navigation).
async function loadWiki(file, opts = {}) {
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
    updateWikiNavButtons();
    return;
  }
  if (!opts.fromHistory) pushWikiHistory(state.wikiFile);
  updateWikiNavButtons();
  if (ctxEl) {
    ctxEl.textContent = state.selectedVault;
    ctxEl.classList.remove("empty");
  }
  if (fileEl) fileEl.textContent = state.wikiFile;
  root.innerHTML = `<p class="muted">Loading…</p>`;
  renderWikiTree();
  updateReadToggle();
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
  root.innerHTML = renderWikiMarkdown(data.content || "", { wikilinks: true });
}

// ----- wiki read/unread + random + search -----
// Reading a page does NOT auto-mark it read (matches the garage reference);
// the user toggles state explicitly via this button.
function updateReadToggle() {
  const btn = $("#btn-wiki-read");
  if (!btn) return;
  if (!state.selectedVault || !state.wikiFile || state.wikiTreeMissing) {
    btn.hidden = true;
    return;
  }
  btn.hidden = false;
  const unread = state.wikiUnread.has(state.wikiFile);
  btn.textContent = unread ? "Mark read ✓" : "Mark unread";
  btn.title = unread ? "Mark this page as read" : "Mark this page as unread";
  btn.classList.toggle("btn-accent", unread);
}

async function toggleReadCurrent() {
  if (!state.selectedVault || !state.wikiFile) return;
  // If the page is currently unread, the action marks it read (and vice versa).
  const read = state.wikiUnread.has(state.wikiFile);
  try {
    const r = await api("/api/vaults/" + encodeURIComponent(state.selectedVault) + "/wiki/read", {
      method: "POST",
      body: JSON.stringify({ file: state.wikiFile, read }),
    });
    if (r.unread) state.wikiUnread.add(state.wikiFile);
    else state.wikiUnread.delete(state.wikiFile);
    renderWikiTree();
    updateReadToggle();
  } catch (err) {
    alert("Could not update read state: " + (err.body?.error || err.message));
  }
}

async function loadRandomWiki() {
  if (!state.selectedVault) { alert("Select a vault first."); return; }
  try {
    const r = await api("/api/vaults/" + encodeURIComponent(state.selectedVault) + "/wiki/random");
    if (!r.file) {
      alert("Nothing unread — every wiki page is marked read.");
      return;
    }
    // The random endpoint reconciles server-side, so reload the tree to pick
    // up any newly-flagged pages before jumping to the chosen one.
    await loadWikiTree();
    loadWiki(r.file);
  } catch (err) {
    alert("Random page failed: " + (err.body?.error || err.message));
  }
}

async function doWikiSearch(q) {
  q = (q || "").trim();
  if (!state.selectedVault) return;
  if (!q) { loadWiki(state.wikiFile); return; }  // cleared box restores the page
  const root = $("#wiki-content");
  root.innerHTML = `<p class="muted">Searching…</p>`;
  let data;
  try {
    data = await api("/api/vaults/" + encodeURIComponent(state.selectedVault)
                     + "/wiki/search?q=" + encodeURIComponent(q));
  } catch (err) {
    root.innerHTML = `<div class="wiki-error">${esc(err.message)}</div>`;
    return;
  }
  renderWikiSearchResults(data);
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Build the snippet HTML entirely on the client: escape the plain server text
// first, then wrap matched query tokens in <mark>. The only HTML introduced is
// the <mark> we add, so there is no path for server text to become markup.
function highlightSnippet(text, query) {
  let out = esc(text || "");
  const tokens = (query || "").toLowerCase().split(/\s+/)
    .filter((t) => t.length >= 2)
    .sort((a, b) => b.length - a.length);
  for (const t of tokens) {
    out = out.replace(new RegExp("(" + escapeRegExp(esc(t)) + ")", "ig"), "<mark>$1</mark>");
  }
  return out;
}

function renderWikiSearchResults(data) {
  const root = $("#wiki-content");
  const hits = data.hits || [];
  if (!hits.length) {
    root.innerHTML = `<div class="wiki-search-results">
      <p class="muted">No matches for “${esc(data.query)}”.</p></div>`;
    return;
  }
  root.innerHTML = `<div class="wiki-search-results">
    <p class="muted">${hits.length} result${hits.length === 1 ? "" : "s"} for “${esc(data.query)}”</p>
    ${hits.map((h) => `
      <div class="wiki-search-hit" data-path="${esc(h.file)}">
        <div class="hit-title">${esc(h.title)}</div>
        <div class="hit-path">${esc(h.rel)}</div>
        <div class="hit-snippet">${highlightSnippet(h.snippet, data.query)}</div>
      </div>`).join("")}
  </div>`;
  root.querySelectorAll(".wiki-search-hit").forEach((el) => {
    el.addEventListener("click", () => {
      const search = $("#wiki-search");
      if (search) search.value = "";
      loadWiki(el.dataset.path);
    });
  });
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
  content.innerHTML = renderWikiMarkdown(data.content || "", { wikilinks: false });
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

// The manual window gate (active/between/ended) is not surfaced in the UI — the
// schedule drives the windows. renderWindow just keeps the footer status-bar
// class in sync with the gate state so the bar styling stays correct.
function renderWindow() {
  const w = state.window || {};
  const bar = $("#status-bar");
  if (bar) {
    bar.classList.remove("active", "between", "ended");
    bar.classList.add(w.window_state || "between");
  }
}

// ----- window schedule (cld20-style daily/weekly windows) -----
const WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
                       "Friday", "Saturday", "Sunday"];

function fmtCountdown(sec) {
  if (sec == null) return "";
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${sec}s`;
}

// Window timestamps are naive server-local ISO ("YYYY-MM-DDTHH:MM:SS").
function clockOf(iso) {
  const m = /T(\d{2}:\d{2})/.exec(iso || "");
  return m ? m[1] : "";
}

async function loadWindowSchedule() {
  try {
    state.windowSchedule = await api("/api/window/schedule");
  } catch (_) {
    return;
  }
  renderWindowSchedule();
  renderTriggerWindowOptions();  // keep the task "When" picker in sync
  // Keep the ⊞ Windows tab's read-only panels live without clobbering the
  // (possibly half-edited) config inputs in the Management/Configuration cards.
  if (isWindowsTabActive() && $("#wc-log-body")) {
    renderWindowLog(state.windowSchedule);
  }
}

// The footer shows two meters — the local window (green) and the weekly cycle
// (blue). For each, the bar fills with the *time* elapsed and that same time %
// is shown INSIDE the bar; the number AFTER the bar is the *limit* used
// (session / weekly utilization from claude.ai), or "?" until synced.
function renderWindowSchedule() {
  const sched = state.windowSchedule;
  const st = sched && sched.status;
  const usage = (st && st.usage) || {};
  // --- Window meter (green): fill + inside = window time; after = session limit. ---
  const wlabel = $("#window-meter-label"), wfill = $("#window-bar-fill"),
        wtime = $("#window-time"), wmeter = $("#meter-window");
  if (wlabel && wfill && wtime) {
    const c = st && st.current;
    const pct = c ? Math.round((c.fraction || 0) * 100) : null;
    wlabel.textContent = c ? `Window ${c.index}/${c.count}` : "Window";
    wfill.style.width = (pct == null ? 0 : pct) + "%";
    wtime.textContent = pct == null ? "—" : pct + "%";
    // After the limit %, the wall-clock time this window ends (schedule-based).
    const wreset = $("#window-reset");
    if (wreset) {
      const wend = c ? clockOf(c.end) : "";
      wreset.textContent = wend ? `· ends ${wend}` : "";
    }
    let title;
    if (c) {
      title = `Window ${c.index}/${c.count} ${clockOf(c.start)}–${clockOf(c.end)}`
        + (c.night ? " 🌙" : "")
        + ` · ${pct}% elapsed · ends in ${fmtCountdown(c.seconds_until_end)}`;
    } else {
      const n = st && st.next;
      title = n
        ? `Between windows · next ${clockOf(n.start)}`
          + (n.night ? " 🌙" : "") + ` in ${fmtCountdown(n.seconds_until_start)}`
        : "No windows configured";
    }
    if (wmeter) wmeter.title = title + "\n"
      + limitNote("Session", usage.window_limit_pct, usage.session_resets_at, usage);
  }
  // --- Week meter (blue): fill + inside = week time; after = weekly limit. ---
  const kfill = $("#weekly-bar-fill"), ktime = $("#weekly-time"),
        kmeter = $("#meter-week");
  if (kfill && ktime) {
    const wk = st && st.weekly;
    const pct = wk ? Math.round((wk.fraction || 0) * 100) : null;
    kfill.style.width = (pct == null ? 0 : pct) + "%";
    ktime.textContent = pct == null ? "—" : pct + "%";
    // After the limit %, the day + time the weekly cycle resets (e.g. "Mon 09:00").
    const kreset = $("#weekly-reset");
    if (kreset) {
      kreset.textContent = wk
        ? `· ${String(wk.weekday_name).slice(0, 3)} ${String(wk.hour).padStart(2, "0")}:00`
        : "";
    }
    if (kmeter) {
      const base = wk
        ? `Weekly cycle ${pct}% elapsed — resets in ${fmtCountdown(wk.seconds_remaining)}`
          + ` (${esc(wk.weekday_name)} ${String(wk.hour).padStart(2, "0")}:00)`
        : "Weekly cycle";
      kmeter.title = base + "\n"
        + limitNote("Weekly", usage.weekly_limit_pct, usage.weekly_resets_at, usage);
    }
  }
  // --- Limit used (after each bar): real % once synced, else "?". ---
  // `limit_reached` means claude.ai (or the wakeup canary) reports the account
  // is over its limit — flag the figure red so 100% reads as "blocked", not "fine".
  const atLimit = usage.reason === "limit_reached";
  setLimitText($("#window-limit"), usage.window_limit_pct, atLimit);
  setLimitText($("#weekly-limit"), usage.weekly_limit_pct, atLimit);
  // Sync button tooltip carries last-sync time + any auth/fetch hint.
  const btn = $("#btn-window-sync");
  if (btn) btn.title = syncTooltip(usage);
}

function setLimitText(el, pct, atLimit) {
  if (!el) return;
  el.textContent = (pct == null) ? "?" : Math.round(pct) + "%";
  el.classList.toggle("at-limit", !!atLimit && pct != null);
}

// One tooltip line describing a limit readout + its reset, or why it's unknown.
function limitNote(label, pct, resetsAt, usage) {
  if (pct == null) {
    const reason = usage.reason;
    if (reason === "auth_error")
      return `${label} limit: ? — logged out / token rejected (use Claude, then ⟳)`;
    if (reason === "fetch_error")
      return `${label} limit: ? — couldn't reach claude.ai (click ⟳ to retry)`;
    return `${label} limit: ? — click ⟳ to fetch usage`;
  }
  const atLimit = usage.reason === "limit_reached";
  let s = atLimit
    ? `${label} limit ${Math.round(pct)}% — at usage limit`
    : `${label} limit ${Math.round(pct)}% used`;
  if (resetsAt) {
    const left = secsUntil(resetsAt);
    if (left != null) s += ` · resets in ${fmtCountdown(left)}`;
  }
  return s;
}

function syncTooltip(usage) {
  const base = "Sync — fetch session/weekly usage from claude.ai";
  if (usage.synced_at) return base + `\nlast synced ${clockOf(usage.synced_at)}`;
  return base;
}

// Seconds from now until an ISO timestamp (claude.ai returns UTC "…Z"); null if
// unparseable.
function secsUntil(iso) {
  const t = Date.parse(iso);
  if (isNaN(t)) return null;
  return Math.max(0, Math.round((t - Date.now()) / 1000));
}

// Footer ⟳ sync button — re-pull window state + live usage limits. Falls back
// to a plain schedule reload if the sync endpoint is unavailable.
async function syncWindowState() {
  const btn = $("#btn-window-sync");
  if (btn) { btn.disabled = true; btn.classList.add("spinning"); }
  try {
    state.windowSchedule = await api("/api/window/sync", { method: "POST" });
    renderWindowSchedule();
    renderTriggerWindowOptions();
  } catch (_) {
    await loadWindowSchedule();
  } finally {
    if (btn) { btn.disabled = false; btn.classList.remove("spinning"); }
  }
}

// ----- activity log (footer "Log" window) -----
const LOG_LEVELS = ["debug", "info", "warn", "error"];

function fmtLogTime(ts) {
  const d = new Date((ts || 0) * 1000);
  if (isNaN(d.getTime())) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function activityRowHtml(e) {
  const lvl = LOG_LEVELS.includes(e.level) ? e.level : "info";
  const detail = e.detail
    ? `<span class="log-detail">${esc(e.detail)}</span>` : "";
  return `<div class="log-row log-${lvl}" data-seq="${e.seq}">`
    + `<span class="log-time">${esc(fmtLogTime(e.ts))}</span>`
    + `<span class="log-level">${esc(lvl)}</span>`
    + `<span class="log-source">${esc(e.source || "app")}</span>`
    + `<span class="log-msg">${esc(e.message)}${detail}</span>`
    + `</div>`;
}

// A socket "activity_logged" entry arrived. Keep the client mirror current even
// when the window is closed (so opening it shows recent history instantly), and
// append live when it's open.
function onActivityLogged(entry) {
  if (!entry || typeof entry !== "object") return;
  state.activityLog.push(entry);
  if (state.activityLog.length > ACTIVITY_LOG_MAX) {
    state.activityLog.splice(0, state.activityLog.length - ACTIVITY_LOG_MAX);
  }
  if (state.logOpen) {
    appendActivityRow(entry);
  } else if (entry.level === "error" || entry.level === "warn") {
    state.logUnseenError = true;
    const dot = $("#activity-log-dot");
    if (dot) { dot.hidden = false; dot.classList.toggle("error", entry.level === "error"); }
  }
}

function appendActivityRow(entry) {
  const list = $("#log-list");
  if (!list) return;
  // Respect the active level filter.
  if (state.logFilter && LOG_LEVELS.indexOf(entry.level) < LOG_LEVELS.indexOf(state.logFilter)) {
    return;
  }
  const empty = list.querySelector(".log-empty");
  if (empty) empty.remove();
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  list.insertAdjacentHTML("beforeend", activityRowHtml(entry));
  if (atBottom) list.scrollTop = list.scrollHeight;  // sticky auto-scroll
}

function renderActivityLogList() {
  const list = $("#log-list");
  if (!list) return;
  const min = state.logFilter ? LOG_LEVELS.indexOf(state.logFilter) : 0;
  const rows = state.activityLog.filter((e) => LOG_LEVELS.indexOf(e.level) >= min);
  list.innerHTML = rows.length
    ? rows.map(activityRowHtml).join("")
    : `<div class="log-empty muted">No log entries${state.logFilter ? " at this level" : ""} yet.</div>`;
  list.scrollTop = list.scrollHeight;
  const c = $("#log-count");
  if (c) {
    c.textContent = state.logFilter
      ? `${rows.length} of ${state.activityLog.length} entries`
      : `${state.activityLog.length} entries`;
  }
}

async function openActivityLog() {
  state.logOpen = true;
  state.logUnseenError = false;
  const dot = $("#activity-log-dot");
  if (dot) { dot.hidden = true; dot.classList.remove("error"); }
  const body = `
    <div class="log-window">
      <div class="log-toolbar">
        <label class="log-filter">level
          <select id="log-level-filter">
            <option value="">all</option>
            <option value="info">info+</option>
            <option value="warn">warn+</option>
            <option value="error">errors</option>
          </select>
        </label>
        <span class="muted small" id="log-count"></span>
        <div class="spacer"></div>
        <button type="button" class="btn btn-xs" id="log-clear">Clear</button>
      </div>
      <div id="log-list" class="log-list"></div>
    </div>`;
  showModal("Activity log", body);
  // Restore the saved filter into the select.
  const sel = $("#log-level-filter");
  if (sel) {
    sel.value = state.logFilter;
    sel.addEventListener("change", () => {
      state.logFilter = sel.value;
      renderActivityLogList();
    });
  }
  const clr = $("#log-clear");
  if (clr) clr.addEventListener("click", clearActivityLog);
  // Pull the authoritative recent list from the server, then render.
  try {
    const data = await api("/api/logs?limit=1000");
    state.activityLog = data.entries || [];
  } catch (_) { /* keep the socket-fed mirror */ }
  renderActivityLogList();  // also refreshes #log-count
  // Closing the modal clears the open flag (modal-close + backdrop click).
  hookModalCloseOnce(() => { state.logOpen = false; });
}

async function clearActivityLog() {
  try {
    await api("/api/logs/clear", { method: "POST" });
  } catch (_) { /* still clear the client mirror */ }
  state.activityLog = [];
  renderActivityLogList();  // also refreshes #log-count
}

// Run `fn` once when the modal next closes (via the × button or backdrop).
function hookModalCloseOnce(fn) {
  const backdrop = $("#modal-backdrop");
  if (!backdrop) { fn(); return; }
  const obs = new MutationObserver(() => {
    if (backdrop.hidden) { obs.disconnect(); fn(); }
  });
  obs.observe(backdrop, { attributes: true, attributeFilter: ["hidden"] });
}

// ----- ⊞ Windows tab: management · automation · usage statistics -----
// Replaces the old top-bar popup. The same #wc-* render helpers drive the
// Management card; two new cards add automation config + a usage-stats view.
async function loadWindowsTab() {
  const root = $("#windows-root");
  if (!root) return;
  let sched;
  try {
    sched = await api("/api/window/schedule");
  } catch (err) {
    root.innerHTML = `<p class="trigger-error" style="padding:16px">Could not load window schedule: ${esc(err.message)}</p>`;
    return;
  }
  state.windowSchedule = sched;
  renderWindowSchedule();
  const names = sched.weekday_names || WEEKDAY_NAMES;
  state.windowDraft = {
    windows: (sched.windows || []).map((w) => ({
      server_start: w.server_start, night_window: !!w.night_window,
      open: !!w.open, collect: !!w.collect,
    })),
    weekly_anchor: {
      weekday: (sched.weekly_anchor || {}).weekday ?? 0,
      hour: (sched.weekly_anchor || {}).hour ?? 0,
    },
    operator_hour_offset: sched.operator_hour_offset ?? 0,
    window_length_hours: sched.window_length_hours ?? 5,
    refresh_interval_minutes: sched.refresh_interval_minutes ?? 1,
    sync_interval_minutes: sched.sync_interval_minutes ?? 10,
    collection_rate: sched.collection_rate ?? 0,
  };
  const d = state.windowDraft;
  const maxRate = sched.max_collection_rate ?? 12;
  const dayOpts = names.map((n, i) =>
    `<option value="${i}" ${i === d.weekly_anchor.weekday ? "selected" : ""}>${esc(n)}</option>`
  ).join("");
  const saveRow = `
    <div class="wc-save-row">
      <button type="button" class="btn btn-sm btn-success wc-save">Save configuration</button>
      <span class="wc-status muted small"></span>
      <span class="wc-error trigger-error"></span>
    </div>`;
  root.innerHTML = `
    <div class="win-card">
      <div class="win-card-head"><strong>Settings</strong></div>
      <div class="wc">
        <div class="wc-grid">
          <label for="wc-length">Window length (hours)</label>
          <div class="wc-anchor">
            <input type="number" id="wc-length" min="1" max="24" value="${d.window_length_hours}" style="width:72px">
            <span class="muted small">length of each session window (Claude's is 5h)</span>
          </div>
          <label for="wc-offset">Operator hour offset</label>
          <div class="wc-anchor">
            <input type="number" id="wc-offset" min="-12" max="14" value="${d.operator_hour_offset}" style="width:72px">
            <span class="muted small">hours your local time leads the server clock (display only)</span>
          </div>
          <label for="wc-weekday">Weekly anchor</label>
          <div class="wc-anchor">
            <select id="wc-weekday">${dayOpts}</select>
            <input type="number" id="wc-anchor-hour" min="0" max="23" value="${d.weekly_anchor.hour}"
                   title="hour (0–23)" style="width:64px">
            <span class="muted small">day + hour the weekly cycle resets</span>
          </div>
          <label for="wc-collection-rate">Collection rate</label>
          <div class="wc-anchor">
            <input type="number" id="wc-collection-rate" min="0" max="${maxRate}" value="${d.collection_rate}" style="width:72px">
            <span class="muted small">reads per <em>collecting</em> window (0 = off, max ${maxRate}); evenly spaced, last ~5 min before close</span>
          </div>
          <label for="wc-refresh">Status refresh (minutes)</label>
          <div class="wc-anchor">
            <input type="number" id="wc-refresh" min="1" max="60" value="${d.refresh_interval_minutes}" style="width:80px">
            <span class="muted small">redraw the footer bars from cached state — no claude.ai call</span>
          </div>
          <label for="wc-sync">Limit sync (minutes)</label>
          <div class="wc-anchor">
            <input type="number" id="wc-sync" min="1" max="1440" value="${d.sync_interval_minutes}" style="width:80px">
            <span class="muted small">pull fresh session/weekly limits from claude.ai</span>
          </div>
        </div>
        ${saveRow}
      </div>
    </div>

    <div class="win-card">
      <div class="win-card-head">
        <strong>Daily windows</strong>
        <div class="spacer"></div>
        <button type="button" class="btn btn-xs" id="wc-add">+ Add window</button>
      </div>
      <div class="wc">
        <p class="muted small wc-marks-hint">Tick <strong>open</strong> to have resman open/anchor that window
          (<code>claude -p "hi"</code> at its start), and <strong>collect</strong> to take usage reads during it.</p>
        <div id="wc-windows"></div>
        <div class="wc-section">
          <strong>Recent window log</strong>
          <div id="wc-log-body"></div>
        </div>
        ${saveRow}
      </div>
    </div>

    <div class="win-card win-card-wide">
      <div class="win-card-head">
        <strong>Usage statistics</strong>
        <div class="win-range" id="win-range">
          <button type="button" class="btn btn-xs" data-range="7">7d</button>
          <button type="button" class="btn btn-xs" data-range="30">30d</button>
          <button type="button" class="btn btn-xs" data-range="90">90d</button>
        </div>
        <div class="spacer"></div>
        <button type="button" class="btn btn-xs" id="wc-collect-now" title="Take one usage reading now and store it">Collect now</button>
        <button type="button" class="btn btn-xs" id="wc-clear-stats" title="Clear stored readings">Clear</button>
      </div>
      <div id="windows-stats"><p class="muted small" style="padding:8px">Loading statistics…</p></div>
    </div>`;

  renderWindowRows();
  renderWindowLog(sched);

  const add = $("#wc-add");
  if (add) add.addEventListener("click", () => {
    const used = new Set(state.windowDraft.windows.map((w) => w.server_start));
    let h = 0;
    while (used.has(h) && h < 23) h++;
    state.windowDraft.windows.push({ server_start: h, night_window: false, open: false, collect: false });
    renderWindowRows();
  });
  // Both cards carry a Save button; either saves all settings + window marks.
  $$("#windows-root .wc-save").forEach((b) => b.addEventListener("click", saveWindowsConfig));
  const rangeBox = $("#win-range");
  if (rangeBox) rangeBox.addEventListener("click", (e) => {
    const b = e.target.closest("[data-range]");
    if (!b) return;
    state.windowStatsRange = parseInt(b.dataset.range, 10);
    renderWindowStats();
  });
  const collectNow = $("#wc-collect-now");
  if (collectNow) collectNow.addEventListener("click", collectUsageNow);
  const clearStats = $("#wc-clear-stats");
  if (clearStats) clearStats.addEventListener("click", clearWindowStats);

  loadWindowStats();
}

function isWindowsTabActive() {
  const p = $("#tab-windows");
  return !!(p && p.classList.contains("active"));
}

function renderWindowRows() {
  const root = $("#wc-windows");
  if (!root) return;
  const draft = state.windowDraft;
  root.innerHTML = draft.windows.map((w, i) => `
    <div class="wc-window-row" data-i="${i}">
      <span class="muted small">#${i + 1}</span>
      <label for="wc-start-${i}">start</label>
      <input type="number" id="wc-start-${i}" class="wc-start" data-i="${i}" min="0" max="23" value="${w.server_start}">
      <label class="wc-mark"><input type="checkbox" class="wc-nightbox" data-i="${i}" ${w.night_window ? "checked" : ""}> night 🌙</label>
      <label class="wc-mark wc-mark-open"><input type="checkbox" class="wc-openbox" data-i="${i}" ${w.open ? "checked" : ""}> open</label>
      <label class="wc-mark wc-mark-collect"><input type="checkbox" class="wc-collectbox" data-i="${i}" ${w.collect ? "checked" : ""}> collect</label>
      <button type="button" class="btn btn-xs btn-danger wc-remove" data-i="${i}" title="Remove window">×</button>
    </div>`).join("") || `<p class="muted small">No windows — add at least one.</p>`;
  root.querySelectorAll(".wc-start").forEach((el) => el.addEventListener("change", (e) => {
    state.windowDraft.windows[+e.target.dataset.i].server_start = parseInt(e.target.value, 10);
  }));
  root.querySelectorAll(".wc-nightbox").forEach((el) => el.addEventListener("change", (e) => {
    state.windowDraft.windows[+e.target.dataset.i].night_window = e.target.checked;
  }));
  root.querySelectorAll(".wc-openbox").forEach((el) => el.addEventListener("change", (e) => {
    state.windowDraft.windows[+e.target.dataset.i].open = e.target.checked;
  }));
  root.querySelectorAll(".wc-collectbox").forEach((el) => el.addEventListener("change", (e) => {
    state.windowDraft.windows[+e.target.dataset.i].collect = e.target.checked;
  }));
  root.querySelectorAll(".wc-remove").forEach((el) => el.addEventListener("click", (e) => {
    state.windowDraft.windows.splice(+e.target.dataset.i, 1);
    renderWindowRows();
  }));
}

function renderWindowLog(sched) {
  const root = $("#wc-log-body");
  if (!root) return;
  const log = sched.log || [];
  if (!log.length) { root.innerHTML = `<p class="muted small">No events recorded yet.</p>`; return; }
  root.innerHTML = log.map((e) =>
    `<div class="wc-log-row"><span class="muted small">${esc(clockOf(e.at) || e.at || "")}</span> ${esc(e.message)}</div>`
  ).join("");
}

async function saveWindowsConfig() {
  // Both cards carry a status/error span; update all of them.
  const setStatus = (s) => $$("#windows-root .wc-status").forEach((e) => (e.textContent = s));
  const setError = (s) => $$("#windows-root .wc-error").forEach((e) => (e.textContent = s));
  setStatus(""); setError("");
  const lengthEl = $("#wc-length"), offsetEl = $("#wc-offset"),
        weekdayEl = $("#wc-weekday"), hourEl = $("#wc-anchor-hour"),
        refreshEl = $("#wc-refresh"), syncEl = $("#wc-sync"),
        rateEl = $("#wc-collection-rate");
  if (!lengthEl || !offsetEl || !weekdayEl || !hourEl || !refreshEl || !syncEl ||
      !rateEl) return false;  // tab re-rendered out from under us
  const draft = state.windowDraft;
  const payload = {
    windows: draft.windows,
    weekly_anchor: {
      weekday: parseInt(weekdayEl.value, 10),
      hour: parseInt(hourEl.value, 10),
    },
    operator_hour_offset: parseInt(offsetEl.value, 10),
    window_length_hours: parseInt(lengthEl.value, 10),
    refresh_interval_minutes: parseInt(refreshEl.value, 10),
    sync_interval_minutes: parseInt(syncEl.value, 10),
    collection_rate: parseInt(rateEl.value, 10),
  };
  try {
    const sched = await api("/api/window/schedule", {
      method: "PUT", body: JSON.stringify(payload),
    });
    state.windowSchedule = sched;
    renderWindowSchedule();
    renderWindowLog(sched);
    applyWindowTimers();  // re-arm with the new cadences immediately
    loadWindowStats();    // next-sample may have shifted
    setStatus("saved");
    return true;
  } catch (err) {
    setError(err.body?.error || err.message);
    return false;
  }
}

// ----- ⊞ Windows tab: usage statistics (hand-rolled SVG, no chart lib) -----
async function loadWindowStats() {
  try {
    state.windowStats = await api("/api/window/stats?limit=1000");
  } catch (err) {
    const root = $("#windows-stats");
    if (root) root.innerHTML = `<p class="trigger-error" style="padding:8px">Could not load statistics: ${esc(err.message)}</p>`;
    return;
  }
  renderWindowStats();
}

function renderWindowStats() {
  const root = $("#windows-stats");
  if (!root) return;
  const data = state.windowStats || {};
  const all = data.samples || [];
  const range = state.windowStatsRange || 30;
  $$("#win-range [data-range]").forEach((b) =>
    b.classList.toggle("active", parseInt(b.dataset.range, 10) === range));
  const cutoff = Date.now() / 1000 - range * 86400;
  const samples = all.filter((s) => (s.ts || 0) >= cutoff);
  const sessionPts = samples.filter((s) => s.session_pct != null)
    .map((s) => ({ ts: s.ts, pct: s.session_pct }));
  // Openers carry no weekly half (cld20) — exclude defensively anyway.
  const weeklyPts = samples.filter((s) => s.weekly_pct != null && s.source !== "opener")
    .map((s) => ({ ts: s.ts, pct: s.weekly_pct }));

  const latest = (data.summary || {}).latest;
  const a = data.automation || {};
  const sp = latest && latest.session_pct != null ? Math.round(latest.session_pct) + "%" : "?";
  const wp = latest && latest.weekly_pct != null ? Math.round(latest.weekly_pct) + "%" : "?";
  const summary = `
    <div class="win-summary">
      <span class="win-sum-item">latest · <strong>session ${sp}</strong> · <strong>weekly ${wp}</strong></span>
      <span class="win-sum-item muted">${(data.summary || {}).count || 0} readings stored</span>
      ${a.next_opener ? `<span class="win-sum-item muted">next opener ${clockOf(a.next_opener)}</span>` : ""}
      ${a.next_sample ? `<span class="win-sum-item muted">next sample ${clockOf(a.next_sample)}</span>` : ""}
    </div>`;

  root.innerHTML = `
    ${summary}
    <div class="win-charts">
      <div class="win-chart">
        <div class="win-chart-title">Session (5-hour) utilization</div>
        ${svgLineChart(sessionPts, { color: "var(--success, #3fb950)", label: "session" })}
      </div>
      <div class="win-chart">
        <div class="win-chart-title">Weekly utilization</div>
        ${svgLineChart(weeklyPts, { color: "var(--accent, #4493f8)", label: "weekly" })}
      </div>
    </div>`;
}

// Minimal line chart: a 0–100 SVG with gridlines, area fill, and points. Scales
// to container width via viewBox; uniform aspect so dots/text don't distort.
function svgLineChart(points, opts) {
  if (!points.length)
    return `<div class="win-chart-empty muted small">no readings in this range</div>`;
  const W = 640, H = 150, padL = 26, padR = 8, padT = 10, padB = 6;
  const xs = points.map((p) => p.ts);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const spanX = (maxX - minX) || 1;
  const sx = (t) => padL + ((t - minX) / spanX) * (W - padL - padR);
  const sy = (v) => padT + (1 - Math.max(0, Math.min(100, v)) / 100) * (H - padT - padB);
  const coords = points.map((p) => [sx(p.ts), sy(p.pct)]);
  const line = coords.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  const lastX = coords[coords.length - 1][0].toFixed(1);
  const firstX = coords[0][0].toFixed(1);
  const area = `${line}L${lastX},${sy(0).toFixed(1)}L${firstX},${sy(0).toFixed(1)}Z`;
  const grid = [0, 50, 100].map((v) => {
    const y = sy(v).toFixed(1);
    return `<line class="win-grid" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}"></line>` +
           `<text class="win-axis" x="2" y="${(+y + 3).toFixed(1)}">${v}</text>`;
  }).join("");
  const dots = coords.map(([x, y]) =>
    `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.1"></circle>`).join("");
  const last = points[points.length - 1];
  return `<svg class="win-chart-svg" viewBox="0 0 ${W} ${H}" style="--ch:${opts.color}">
      ${grid}
      <path class="win-area" d="${area}"></path>
      <path class="win-line" d="${line}"></path>
      <g class="win-dots">${dots}</g>
    </svg>
    <div class="win-chart-foot muted small">latest ${Math.round(last.pct)}% · ${points.length} pts</div>`;
}

async function collectUsageNow() {
  const btn = $("#wc-collect-now");
  if (btn) { btn.disabled = true; btn.textContent = "Collecting…"; }
  try {
    await api("/api/window/sample", { method: "POST" });
    await loadWindowStats();
  } catch (err) {
    alert("Collect failed: " + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Collect now"; }
  }
}

async function clearWindowStats() {
  if (!window.confirm("Clear all stored usage readings?")) return;
  try {
    await api("/api/window/stats/clear", { method: "POST" });
    await loadWindowStats();
  } catch (err) {
    alert("Clear failed: " + err.message);
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
  // Optional app.vault_default_root_path — the new-vault wizard uses it as
  // the starting point for the path input and the Browse picker.
  state.vaultDefaultRoot = data.vault_default_root || null;
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
  if (isHomeActive()) renderLanding();
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
  // Pre-fill the path input with the configured default root when present,
  // so the user only needs to append the vault folder name. Falls back to
  // an empty input when app.vault_default_root_path is not configured.
  const defaultRoot = state.vaultDefaultRoot || "";
  const pathSeed = defaultRoot
    ? (defaultRoot.endsWith("/") ? defaultRoot : defaultRoot + "/")
    : "";
  const pathHint = defaultRoot
    ? `— absolute, defaults to <code>${esc(defaultRoot)}</code>`
    : "— absolute, e.g. /data/research/foo";
  const body = `
    <label>Vault name <span class="muted">— letters, numbers, _ -</span></label>
    <input id="nv-name" autocomplete="off" />
    <label>Vault path <span class="muted">${pathHint}</span></label>
    <div class="path-input-row">
      <input id="nv-path" placeholder="/path/to/vault" autocomplete="off" value="${esc(pathSeed)}" />
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
        setWizardStatus(
          "Opening Claude session — bootstrap wraps plugin check, " +
          "/claude-obsidian:wiki, and visual-workspace copy…",
          "info",
        );
        try {
          const sess = await api("/api/sessions", {
            method: "POST",
            body: JSON.stringify({
              vault: name,
              type: "claude",
              bootstrap_new_vault: true,
            }),
          });
          state.sessions.push(sess);
          state.activeSessionId = sess.id;
          setWizardStatus(
            "Vault ready. Claude session open in the Terminal tab — " +
            "instructions from tools/newValPrefix.md and tools/newValSuffix.md " +
            "are pasted automatically; answer any prompts there.",
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
      // Seed the picker with whatever the user has typed; if still blank,
      // fall back to the configured default root so they don't have to
      // navigate from `/` every time.
      const typed = $("#nv-path").value.trim();
      const start = typed || state.vaultDefaultRoot || null;
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
  // The brand/logo is the home button — it opens the global vault landing
  // page. role="button"/tabindex make it keyboard-operable too.
  const brand = $("#header-brand");
  if (brand) {
    const goHome = () => showPanel("home");
    brand.addEventListener("click", goHome);
    brand.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goHome(); }
    });
  }
  const btnLandingRefresh = $("#btn-landing-refresh");
  if (btnLandingRefresh) btnLandingRefresh.addEventListener("click", loadLanding);
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
  const btnWikiBack = $("#btn-wiki-back");
  if (btnWikiBack) btnWikiBack.addEventListener("click", () => goWikiHistory(-1));
  const btnWikiFwd = $("#btn-wiki-fwd");
  if (btnWikiFwd) btnWikiFwd.addEventListener("click", () => goWikiHistory(1));
  const btnWikiRefresh = $("#btn-wiki-refresh");
  if (btnWikiRefresh) btnWikiRefresh.addEventListener("click", () => {
    loadWikiTree();
    loadWiki();
  });
  const btnWikiTreeRefresh = $("#btn-wiki-tree-refresh");
  if (btnWikiTreeRefresh) btnWikiTreeRefresh.addEventListener("click", loadWikiTree);
  const btnWikiRandom = $("#btn-wiki-random");
  if (btnWikiRandom) btnWikiRandom.addEventListener("click", loadRandomWiki);
  const btnWikiRead = $("#btn-wiki-read");
  if (btnWikiRead) btnWikiRead.addEventListener("click", toggleReadCurrent);
  const wikiSearch = $("#wiki-search");
  if (wikiSearch) {
    wikiSearch.addEventListener("keydown", (e) => {
      if (e.key === "Enter") doWikiSearch(e.target.value);
    });
    // Native clear (×) on type=search fires an empty "search" event.
    wikiSearch.addEventListener("search", (e) => doWikiSearch(e.target.value));
  }
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
  const opList = $("#t-op-list");
  if (opList) opList.addEventListener("click", (e) => {
    const btn = e.target.closest(".kind-card");
    if (btn && btn.dataset.op) selectOp(btn.dataset.op);
  });
  $("#config-file").addEventListener("change", loadConfig);
  $("#btn-config-save").addEventListener("click", saveConfig);
  $("#btn-new-vault").addEventListener("click", showNewVaultWizard);
  const refresh = $("#btn-refresh");
  if (refresh) refresh.addEventListener("click",
    () => Promise.all([loadVaults(), loadSessions(), loadTasks()]));
  $("#modal-close").addEventListener("click", closeModal);
}

function setupStatusBar() {
  // Window state + its start/end/weekly controls now live in the ⊞ Windows
  // modal (the footer just shows the schedule), so there is no sync menu here.
  $$("#theme-switch button[data-theme-set]").forEach((btn) => {
    btn.addEventListener("click", () => setTheme(btn.dataset.themeSet));
  });
  renderThemeSwitch();
  const connPill = $("#conn-pill");
  if (connPill) connPill.addEventListener("click", openSessionsOverview);
  // Window management now lives in its own ⊞ Windows tab (not a top-bar popup).
  const btnWinRefresh = $("#btn-windows-refresh");
  if (btnWinRefresh) btnWinRefresh.addEventListener("click", () => loadWindowsTab());
  const btnSync = $("#btn-window-sync");
  if (btnSync) btnSync.addEventListener("click", syncWindowState);
  const btnLog = $("#btn-activity-log");
  if (btnLog) btnLog.addEventListener("click", openActivityLog);
}

// Three-way theme switch (green / dark / light) — mirrors the garage
// reference. The chosen theme is written to <html data-theme> and persisted
// under "resman-theme" so the FOUC inline script can restore it pre-paint.
const THEMES = ["green", "dark", "light"];

function currentTheme() {
  const t = document.documentElement.getAttribute("data-theme");
  return THEMES.includes(t) ? t : "dark";
}

function setTheme(name) {
  if (!THEMES.includes(name)) return;
  document.documentElement.setAttribute("data-theme", name);
  try { localStorage.setItem("resman-theme", name); } catch (_) {}
  renderThemeSwitch();
}

function renderThemeSwitch() {
  const active = currentTheme();
  $$("#theme-switch button[data-theme-set]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.themeSet === active);
  });
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

function formatRss(kb) {
  if (!kb || kb <= 0) return "—";
  if (kb < 1024) return kb + " KB";
  const mb = kb / 1024;
  if (mb < 1024) return mb.toFixed(1) + " MB";
  return (mb / 1024).toFixed(2) + " GB";
}

function formatAgeSeconds(sec) {
  if (sec == null) return "—";
  if (sec < 60) return sec + "s";
  const min = Math.floor(sec / 60);
  if (min < 60) return min + "m";
  const hr = Math.floor(min / 60);
  if (hr < 48) return hr + "h " + (min % 60) + "m";
  return Math.floor(hr / 24) + "d";
}

function renderSessionsOverview(stats) {
  if (!stats.available) {
    return `<p class="muted">ttyd is not installed, so resman isn't tracking any
              terminal sessions. Install ttyd to enable browser terminals.</p>`;
  }
  const summary = `
    <div class="sessions-overview-summary">
      <span><strong>${stats.session_count}</strong> tracked session${stats.session_count === 1 ? "" : "s"}</span>
      <span>Total RSS · <strong>${esc(formatRss(stats.total_rss_kb))}</strong></span>
      <span>tmux socket · <code>${esc(stats.tmux_socket || "")}</code></span>
    </div>`;
  let list;
  if (!stats.sessions.length) {
    list = `<p class="muted">No live ttyd sessions. Spawn one from a vault's <em>+ Claude</em> or <em>+ Shell</em> button.</p>`;
  } else {
    list = `<div class="sessions-overview-list">` +
      stats.sessions.map(renderSessionRow).join("") +
      `</div>`;
  }
  let orphans = "";
  if (stats.orphaned_tmux_sessions && stats.orphaned_tmux_sessions.length) {
    const count = stats.orphaned_tmux_sessions.length;
    orphans = `<div class="session-orphans">
      <div class="session-orphans-head">
        <strong>Orphaned tmux sessions (${count})</strong>
        <button id="btn-kill-orphans" class="btn btn-sm btn-danger"
                title="Run tmux kill-session on every orphan listed below">Kill all</button>
      </div>
      <p>Matching our prefix but not tracked by the running control plane —
      typically left over from a previous run. Reattach by spawning a new
      terminal in the matching vault, or use the <em>Kill all</em> button to
      reclaim resources.</p>
      <ul>${stats.orphaned_tmux_sessions.map((n) => `<li><code>${esc(n)}</code></li>`).join("")}</ul>
    </div>`;
  }
  return `<div class="sessions-overview">${summary}${list}${orphans}</div>`;
}

async function killOrphanSessions() {
  const btn = $("#btn-kill-orphans");
  if (btn) { btn.disabled = true; btn.textContent = "Killing…"; }
  try {
    const result = await api("/api/sessions/orphans/kill", { method: "POST" });
    const killed = (result.killed || []).length;
    const failed = (result.failed || []).length;
    let msg = `Killed ${killed} orphan${killed === 1 ? "" : "s"}`;
    if (failed) msg += `, ${failed} failed`;
    // Refresh the modal so the orphan list reflects the new state.
    await openSessionsOverview();
    const status = document.createElement("p");
    status.className = "muted";
    status.style.marginTop = "8px";
    status.textContent = msg;
    $("#modal-body").appendChild(status);
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = "Kill all"; }
    alert("Kill orphans failed: " + (err && err.message ? err.message : err));
  }
}

function renderSessionRow(s) {
  const head = `
    <div class="session-row-head">
      <span class="sess-name">${esc(s.vault)}</span>
      <span class="state-pill state-${esc(s.alive ? "running" : "ended")}">${esc(s.alive ? "alive" : "dead")}</span>
      <span class="sess-meta">${esc(s.session_type)} · port ${esc(String(s.port))} · age ${esc(formatAgeSeconds(s.age_seconds))}</span>
      <span class="sess-meta"><code>${esc(s.tmux_session)}</code></span>
      <span class="sess-rss">${esc(formatRss(s.total_rss_kb))}</span>
    </div>`;
  const ttyd = s.ttyd || {};
  const rows = [
    procRow("ttyd", ttyd.pid, ttyd.comm || "ttyd", ttyd.rss_kb, false),
  ];
  for (const pane of s.panes || []) {
    rows.push(procRow("pane", pane.pane_pid, "(tmux pane)", pane.rss_kb, false));
    for (const proc of pane.processes || []) {
      const label = proc.pid === pane.pane_pid ? proc.comm : proc.comm;
      const indent = proc.pid !== pane.pane_pid;
      rows.push(procRow("", proc.pid, label, proc.rss_kb, indent));
    }
  }
  const table = `<div class="session-procs">
    <span class="hdr">role</span>
    <span class="hdr">pid</span>
    <span class="hdr">command</span>
    <span class="hdr">rss</span>
    ${rows.join("")}
  </div>`;
  return `<div class="session-row ${s.alive ? "" : "dead"}">${head}${table}</div>`;
}

function procRow(role, pid, comm, rss, indent) {
  return `<span>${esc(role || "")}</span>
          <span>${esc(pid == null ? "—" : String(pid))}</span>
          <span class="${indent ? "indent" : ""}">${esc(comm || "")}</span>
          <span>${esc(formatRss(rss))}</span>`;
}

async function openSessionsOverview() {
  showModal("Live ttyd + tmux sessions",
            `<p class="muted">Loading…</p>`);
  let stats;
  try {
    stats = await api("/api/sessions/stats");
  } catch (err) {
    $("#modal-body").innerHTML =
      `<p style="color:var(--danger)">Failed to load: ${esc(err.message)}</p>`;
    return;
  }
  $("#modal-body").innerHTML = renderSessionsOverview(stats);
  const killBtn = $("#btn-kill-orphans");
  if (killBtn) killBtn.addEventListener("click", killOrphanSessions);
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
    sock.on("window_state_changed", () => { loadWindow(); loadWindowSchedule(); });
    sock.on("window_sample_added", () => { if (isWindowsTabActive()) loadWindowStats(); });
    sock.on("activity_logged", (e) => onActivityLogged(e));
    sock.on("session_crashed", (p) => {
      alert("Terminal session crashed: " + (p?.message || ""));
      loadSessions();
    });
    sock.on("config_reloaded", () => {
      loadVaults();
      // A vault may have been added/removed — refresh the grid if it's open.
      if (isHomeActive()) loadLanding();
    });
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
  await Promise.all([loadVaults(), loadTasks(), loadSessions(), loadWindow(), loadWindowSchedule()]);
  // Home is the default panel on boot — populate the vault grid now that
  // tasks/sessions are loaded so each card's status dot is accurate.
  loadLanding();
  // Fetch live usage limits once on load (the time bars are already showing
  // from loadWindowSchedule); then refresh time every 30s and re-pull the
  // limits every 10 min so they stay current without hammering claude.ai.
  syncWindowState();
  applyWindowTimers();
}

// Footer poll timers, driven by the ⊞ Windows config (refresh_interval_minutes
// redraws the bars from cached state; sync_interval_minutes re-pulls live limits
// from claude.ai). Re-armed on save so a changed cadence takes effect at once.
let _windowRefreshTimer = null, _windowSyncTimer = null;
function applyWindowTimers() {
  const sched = state.windowSchedule || {};
  const refreshMs = clampInt(sched.refresh_interval_minutes, 1, 60, 1) * 60 * 1000;
  const syncMs = clampInt(sched.sync_interval_minutes, 1, 1440, 10) * 60 * 1000;
  if (_windowRefreshTimer) clearInterval(_windowRefreshTimer);
  if (_windowSyncTimer) clearInterval(_windowSyncTimer);
  _windowRefreshTimer = setInterval(() => { renderWindow(); loadWindowSchedule(); }, refreshMs);
  _windowSyncTimer = setInterval(syncWindowState, syncMs);
}

function clampInt(v, lo, hi, fallback) {
  const n = parseInt(v, 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(hi, Math.max(lo, n));
}

document.addEventListener("DOMContentLoaded", init);
