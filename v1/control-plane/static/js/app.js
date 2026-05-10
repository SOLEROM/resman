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
        <button class="play" data-action="play" data-vault="${esc(v.name)}">▶</button>
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
      openSessionMenu(btn, btn.dataset.vault);
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
  loadWiki(WIKI_HOME);
  renderTasks();
  // Per UX spec: clicking a vault always returns to the Terminal view
  // and clears the active tab in the header.
  showPanel("terminal");
  renderSessions();
  renderActiveSession();
}

function renderVaultContext() {
  const el = $("#vault-context");
  if (!el) return;
  if (state.selectedVault) {
    el.textContent = state.selectedVault;
    el.classList.remove("empty");
  } else {
    el.textContent = "No vault selected";
    el.classList.add("empty");
  }
}

// Show one panel. tabName is "terminal" | "docs" | "tasks" | "config".
// "terminal" is the default panel and never has a tab in the header — its
// active state is "no header tab is active".
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
}

// ----- terminal sessions -----
function openSessionMenu(anchor, vaultName) {
  const choice = window.prompt("Open which session for vault '" + vaultName + "'?\nType 'claude' or 'shell':", "claude");
  if (!choice) return;
  if (choice !== "claude" && choice !== "shell") {
    alert("type must be 'claude' or 'shell'");
    return;
  }
  spawnSession(vaultName, choice);
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
async function loadTasks() {
  const data = await api("/api/tasks");
  state.tasks = data.tasks || [];
  renderTasks();
  renderVaultList();
}

function renderTasks() {
  const root = $("#task-rows");
  const pf = $("#task-priority-filter").value;
  let items = state.tasks.slice();
  if (pf) items = items.filter((t) => t.priority === pf);
  if (state.selectedVault) {
    items = items.filter((t) => t.vault === state.selectedVault || t.vault === "ALL");
  }
  if (!items.length) {
    root.innerHTML = `<tr><td colspan="7" class="muted">No tasks.</td></tr>`;
    return;
  }
  root.innerHTML = items.map((t) => {
    const updated = t.updated_at ? t.updated_at.slice(11, 19) : "";
    const actions = ["log"];
    if (t.state === "deferred") actions.unshift("promote");
    if (["pending", "deferred"].includes(t.state)) actions.unshift("cancel");
    if (["completed", "failed"].includes(t.state)) actions.unshift("re-run");
    return `<tr data-tid="${esc(t.id)}">
      <td></td>
      <td><span class="state-pill state-${esc(t.state)}">${esc(t.state)}</span></td>
      <td>${esc(t.operation)}</td>
      <td>${esc(t.vault)}${t.parent_id ? " ↳" : ""}</td>
      <td>${esc(t.priority)}</td>
      <td>${esc(updated)}</td>
      <td>${actions.map((a) => `<button data-act="${a}" data-tid="${esc(t.id)}">${a}</button>`).join(" ")}</td>
    </tr>`;
  }).join("");
  $$('#task-rows button').forEach((btn) => {
    btn.addEventListener("click", () => taskAction(btn.dataset.act, btn.dataset.tid));
  });
}

async function taskAction(act, tid) {
  if (act === "log") {
    const txt = await apiText("/api/tasks/" + encodeURIComponent(tid) + "/log");
    showModal("Task log: " + tid,
      `<pre class="log-pane">${esc(txt || '(empty)')}</pre>`);
    return;
  }
  if (act === "cancel") {
    await api("/api/tasks/" + encodeURIComponent(tid), { method: "DELETE" });
    await loadTasks();
    return;
  }
  if (act === "promote") {
    await api("/api/tasks/" + encodeURIComponent(tid) + "/promote", { method: "POST" });
    await loadTasks();
    return;
  }
  if (act === "re-run") {
    const orig = state.tasks.find((t) => t.id === tid);
    if (orig) showNewTaskModal(orig);
    return;
  }
}

function showNewTaskModal(prefill) {
  const vaults = state.vaults.map((v) => `<option>${esc(v.name)}</option>`).join("");
  const ops = ["wiki-ingest", "wiki-lint", "wiki-autoresearch", "wiki-update-hot-cache",
               "wiki-bootstrap", "run-prompt", "run-shell"];
  const opts = ops.map((o) => `<option ${prefill && prefill.operation === o ? "selected" : ""}>${o}</option>`).join("");
  const body = `
    <label>name</label><input id="t-name" value="${esc(prefill?.name || 'task')}">
    <label>vault</label>
    <select id="t-vault">
      <option ${prefill?.vault === 'ALL' ? 'selected' : ''}>ALL</option>${vaults}
    </select>
    <label>operation</label><select id="t-op">${opts}</select>
    <label>priority</label>
    <select id="t-pri">
      <option value="high" ${prefill?.priority === "high" ? "selected" : ""}>high</option>
      <option value="medium" ${(!prefill || prefill?.priority === "medium") ? "selected" : ""}>medium</option>
      <option value="low" ${prefill?.priority === "low" ? "selected" : ""}>low</option>
    </select>
    <label>params (JSON)</label>
    <textarea id="t-params" rows="3">${esc(JSON.stringify(prefill?.params || {}, null, 2))}</textarea>`;
  showModal("New task", body, async () => {
    let params;
    try { params = JSON.parse($("#t-params").value || "{}"); }
    catch (e) { alert("params: invalid JSON"); return false; }
    if ($("#t-op").value === "run-shell" && !confirm(
      "run-shell executes an arbitrary command in the vault directory. Proceed?"
    )) return false;
    try {
      await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify({
          name: $("#t-name").value,
          vault: $("#t-vault").value,
          operation: $("#t-op").value,
          params,
          priority: $("#t-pri").value,
        }),
      });
      await loadTasks();
      return true;
    } catch (err) {
      alert("Create failed: " + err.message);
      return false;
    }
  });
}

// ----- wiki -----
const WIKI_HOME = "wiki/overview.md";
state.wikiFile = WIKI_HOME;

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
    return;
  }
  if (ctxEl) {
    ctxEl.textContent = state.selectedVault;
    ctxEl.classList.remove("empty");
  }
  if (fileEl) fileEl.textContent = state.wikiFile;
  root.innerHTML = `<p class="muted">Loading…</p>`;
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
  const html = window.marked.parse(data.content || "", { breaks: true });
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
          <code>system.yaml</code> to point at your help tree, or create the
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
      "\n\nCheck that obsidian_cmd is set correctly in config/system.yaml.");
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
  const file = $("#config-file").value;
  const data = await api("/api/config/yaml?file=" + encodeURIComponent(file));
  $("#config-editor").value = data.content || "";
  $("#config-status").textContent = "";
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
//   2. POST /api/vaults           — appends the entry to system.yaml.
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

    setWizardStatus("Registering in system.yaml…", "info");
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
  const btnRename = $("#btn-rename-tab");
  if (btnRename) btnRename.addEventListener("click", renameActiveTab);
  const btnObs = $("#btn-obsidian");
  if (btnObs) btnObs.addEventListener("click", openVaultInObsidian);
  const btnCompact = $("#btn-task-compact");
  if (btnCompact) btnCompact.addEventListener("click", compactTasksLog);
  const btnWikiRefresh = $("#btn-wiki-refresh");
  if (btnWikiRefresh) btnWikiRefresh.addEventListener("click", () => loadWiki());
  // Hot / Index / Overview — declarative wiring via data-wiki-page so adding
  // another canonical page later is just an HTML edit.
  document.querySelectorAll("[data-wiki-page]").forEach((btn) => {
    btn.addEventListener("click", () => loadWiki(btn.dataset.wikiPage));
  });
  const btnHelpRefresh = $("#btn-help-refresh");
  if (btnHelpRefresh) btnHelpRefresh.addEventListener("click", loadHelp);
  const cronDismiss = $("#cron-skip-dismiss");
  if (cronDismiss) cronDismiss.addEventListener("click", () => {
    $("#cron-skip-banner").hidden = true;
  });
  $("#btn-new-task").addEventListener("click", () => showNewTaskModal());
  $("#task-priority-filter").addEventListener("change", renderTasks);
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
