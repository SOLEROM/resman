/**
 * webterm glue — loaded only when the shared terminal is enabled (server sets
 * use_webterm; RESMAN_WEBTERM=0 reverts to the legacy ttyd + iframe stack).
 *
 * Runs after app.js (module scripts execute after classic ones) but before
 * DOMContentLoaded, so it replaces app.js's terminal globals before the app
 * boots. Everything non-terminal — vaults, tasks, the wiki tree, the window
 * budget — is untouched.
 *
 * resman scopes its tab strip per vault on purpose ("rather than blending all
 * vaults' tabs together"). webterm draws one strip for every session, so the
 * glue hides the tabs that don't belong to the selected vault instead of
 * changing that behaviour.
 */
import { WebTerm } from '/webterm/static/webterm.js';

const wt = new WebTerm({
  root: '#webterm-root',
  theme: () => (typeof currentTheme === 'function' ? currentTheme() : 'dark'),
  placeholder: 'Select a vault, then click + Claude or + Shell.',
});

/* ---- session list: webterm is the source of truth ---- */
const known = new Map();   // id -> {id, vault, session_type, path}

function asResmanSession(s) {
  const meta = s.metadata || {};
  return {
    id: s.id,
    vault: meta.vault || '',
    session_type: meta.session_type || 'shell',
    path: meta.path || '',
    tmux_session: s.id,
    alive: true,
    created_at: s.created_at,
  };
}

async function refreshKnown() {
  let listed = [];
  try {
    listed = await wt.listSessions();
  } catch (_) { return; }
  known.clear();
  for (const s of listed) {
    known.set(s.id, asResmanSession(s));
    // A session resman started for itself, or one left from a previous run,
    // exists on the server without this page having opened it — give it a tab.
    if (!wt.sessions.has(s.id)) wt.openSession(s.id, { label: s.label });
  }
  state.sessions = [...known.values()];
}

/* Per-vault scoping: hide the tabs that belong to another vault, and make
 * sure the visible one is the active one. */
function applyVaultScope() {
  const vault = state.selectedVault;
  let firstVisible = null;
  for (const [id, s] of wt.sessions) {
    const meta = known.get(id);
    const belongs = !!vault && meta && meta.vault === vault;
    if (s.tabEl) s.tabEl.style.display = belongs ? '' : 'none';
    if (belongs && !firstVisible) firstVisible = id;
  }
  const activeBelongs = firstVisible !== null
    && known.get(wt.activeId) && known.get(wt.activeId).vault === vault;
  if (!activeBelongs && firstVisible) wt.activate(firstVisible);
  if (firstVisible) {
    state.activeSessionId = wt.activeId;
    if (vault) state.lastSessionByVault[vault] = wt.activeId;
  } else {
    state.activeSessionId = null;
  }
}

window.loadSessions = async () => {
  state.ttydAvailable = true;   // webterm needs no ttyd
  await refreshKnown();
  renderSessions();
  renderActiveSession();
};

// webterm draws the tab strip and the terminals itself.
window.renderSessions = () => {};
window.renderActiveSession = () => applyVaultScope();

/* ---- opening ---- */
window.spawnSession = async (vaultName, type) => {
  // The client names a vault and a type — never a path or a command line.
  const created = await wt.createSession({ vault: vaultName, type });
  if (!created) return;   // createSession reported the error itself
  await refreshKnown();
  state.activeSessionId = created.id;
  state.lastSessionByVault[vaultName] = created.id;
  wt.activate(created.id);
  applyVaultScope();
  showPanel('ops');
};

window.killSession = async (id) => {
  const sess = known.get(id);
  await wt.killSession(id);
  known.delete(id);
  if (sess && state.lastSessionByVault[sess.vault] === id) {
    delete state.lastSessionByVault[sess.vault];
  }
  if (state.tabLabels && state.tabLabels[id]) {
    delete state.tabLabels[id];
    saveTabLabels();
  }
  await loadSessions();
};

/* ---- orphans ---- */
// Every prefixed tmux session is a live tab under webterm, so there is nothing
// to reap — and the legacy reaper would have killed all of them.
window.killOrphanSessions = async () => {
  alert('No orphans on the shared terminal — every tmux session on resman\'s '
      + 'socket is a live tab. Close the ones you don\'t want.');
};

/* ---- theme ---- */
const legacySetTheme = window.setTheme;
if (typeof legacySetTheme === 'function') {
  window.setTheme = (name) => { legacySetTheme(name); wt.refreshTheme(); };
}

/* ---- boot ---- */
await wt.mount();
await loadSessions();
window.wt = wt;   // console access for debugging
