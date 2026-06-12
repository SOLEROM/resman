---
noteId: "5b88a2704f5d11f18eaba108b9c533e7"
tags: []

---

# Frontend / UI

## Overview

The resman SPA is served by Flask and built with vanilla JS and CDN-only dependencies
(Socket.IO; no xterm.js — ttyd handles that internally). There is no build step.
Browser terminals are `<iframe>` elements pointing at ttyd processes. The layout has
three regions: a left sidebar (vault list), a main panel (tabs), and a fixed-height
window status bar at the bottom.

## Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│ ⚗ resman — Research Vault Manager   Wiki  Tasks  Config  Help  ● connected ☾ │   ← header bar
├────────────────────┬────────────────────────────────────────────────┤
│ VAULTS         ↻  │ vla6                  ✎  + Shell  + Claude  …  │   ← terminal toolbar
│ [search/filter]    ├────────────────────────────────────────────────┤
│ ● ai-agents  [↘]  │ vla6·claude·08:25 ×                             │   ← per-vault tab strip
│ ○ llm-bench  [↘]  ├────────────────────────────────────────────────┤
│ ─ unregistered    │                                                  │
│   found-vault     │  ttyd iframe  OR  Markdown / Tasks / Config      │
│ [+ New Vault]     │                                                  │
├────────────────────┼────────────────────────────────────────────────┤
│ Window: ● ACTIVE  ends in 3h 12m                  [ sync ▼ ]  ☾    │
└─────────────────────────────────────────────────────────────────────┘
```

Tabs (Wiki / Ops / Tasks / Config / Help) live in the **top header bar** — they
are top-level navigation, not nested under the vault. **Per-vault panel
memory**: each vault remembers its own last-seen panel (Wiki / Ops /
Tasks / Config) and re-selecting the vault restores it. Stored in
`localStorage` under `resman-last-panel-by-vault` so it survives reloads.
Legacy `"terminal"` entries from the pre-Ops-tab build are migrated to
`"ops"` on load.

First-visit resolution order when no panel is remembered (or the
remembered panel is Ops but the vault has no live sessions):
- Land on **Ops** if the vault has at least one live session.
- Otherwise land on **Wiki** (best entry point for a fresh research vault).

Help is treated as vault-independent and is not remembered per-vault —
re-selecting a vault should not surprise the user by dropping them onto
the Help tab.

**Ops** is the terminal-sessions view — one `<iframe>` per ttyd session,
filtered to the current vault. It is a first-class header tab (between
Wiki and Tasks). Clicking the vault-name label in the header is an
equivalent shortcut to the Ops tab.

The per-vault action buttons — `✎` rename, `+ Shell`, `+ Claude`,
`Obsidian` — live in the **header bar**, centered between the tab strip
and the connection indicator. They act on the currently selected vault
(shown as a label next to the buttons) and are hidden when no vault is
selected. The `ttyd not installed` warning appears in the same group when
ttyd is missing.

**Spawn → auto-switch to Ops.** Clicking `+ Shell` or `+ Claude` spawns the
session and immediately switches the main panel to the **Ops** view so
the new iframe is visible — otherwise the buttons would appear to do
nothing while the user is on the Wiki/Tasks/Config/Help tab.

**Return to Ops.** Two equivalent paths: click the **Ops** header tab, or
click the vault-name label in the header. Both are the explicit fast-path
to the terminal sessions for vaults already running.

Layout uses a flex-column root so the status bar is always visible at a fixed height
and never overlaps terminal content.

## Left Sidebar

**Filter bar (top of sidebar):**
- Search field: filters vault list by name (client-side, instant)
- Tag filter: multi-select chips; shows vaults matching any selected tag
- Status filter: dropdown — any / active session / has tasks / has error
- Present in Phase 1; a sidebar with 20+ gray dots is unusable without it

**Vault list:**
- Each row: status dot, vault name, tags (dimmed), `[↘]` button
- Clicking vault name selects the vault and switches main panel to the vault's remembered panel; on first visit, **Ops** if the vault has live sessions, otherwise **Wiki**. The header's vault-action buttons become visible either way
- `[↘]` is the **ingest-URL shortcut** — prompts for a URL, queues a `wiki-ingest` task for that vault, selects the vault, reloads the task list, and switches to the **Tasks** tab so the user immediately sees the task progress. URL validation is light (presence + `http(s)://` prefix); operation-level validation is authoritative on the backend
- A `⚠` (path missing) or `?` (`.obsidian/` missing) icon appears next to the vault name when a validation check failed; clicking the icon opens a **vault health modal** that shows the table from `03-vault-registry.md` (path checks, last session, last completed task, tags). Click propagation is stopped so the icon click does not also select the vault.

**Unregistered vaults** (from `scan_paths`):
- Shown below a horizontal divider when `scan_paths` is configured
- Each has a `[+ Register]` button
- Divider absent if `scan_paths` is empty or not configured

**Footer:**
- `[+ New Vault]` — opens the **two-step creation wizard** (see below)
- No `[⚙ Config]` button — Config is reachable from the header tabs

**Empty state** (zero registered vaults):
- Sidebar shows: "Add your first vault to get started →" with arrow toward `[+ New Vault]`

## Vault Status Dot — Priority Rule

One color per vault at any time. Hover tooltip lists all true conditions.

| Priority | Color | Condition |
|----------|-------|-----------|
| 1 (highest) | Red | Last task failed |
| 2 | Yellow | A task is currently running |
| 3 | Green | Has an active tmux session |
| 4 | Blue | Obsidian is open for this vault |
| 5 (lowest) | Gray | Idle / none of the above |

Spec: 10px filled circle, CSS class `vault-dot-{color}`.
Hover tooltip: `"{vault-name}: {flag1}, {flag2}, ..."` listing all true conditions.

## Main Panel Tabs

**Ops tab** (terminal sessions; was "Terminal view"):
- Each tab is an independent ttyd session (one `<iframe>` per session)
- The tab strip is **filtered to the currently selected vault** — switching vaults swaps both the tab strip and the visible iframe (every iframe stays in the DOM so its WebSocket persists; only `display` toggles)
- Default tab label: `<vault> · <type> · <HH:MM>` (e.g., `vla6 · claude · 08:25`)
- Tab switching: click anywhere on a tab → switch; click `×` → kill the session; tab labels are persisted to `localStorage` so renames survive reload
- The per-vault toolbar is **the header bar**: `✎` rename active tab, `+ Shell`, `+ Claude`, `Obsidian` (renamed from `Open Obsidian`) — these are global controls now, not panel-specific, and stay visible on every tab while a vault is selected
- The `✎` button opens a modal asking for a new label (blank restores the default); each user's custom labels live only in their browser
- Per-vault session memory: `state.lastSessionByVault` remembers which session was last active for each vault, so revisiting a vault restores the same terminal
- If ttyd unavailable: tab strip shows "ttyd not installed — terminal sessions disabled"

**Wiki tab** (formerly "Docs"):
- Markdown viewer for the vault's Claude-wiki-plugin output. Defaults to `wiki/overview.md` (the landing page resman opens when the tab is shown).
- Two-pane layout: **left sidebar tree** of all `<vault>/wiki/**/*.md` pages + **content pane** for the rendered markdown.
- Endpoints:
  - `GET /api/vaults/{name}/wiki/tree` — recursive tree of `wiki/`; returns `{missing:true, tree:[]}` if the dir is absent. Hidden entries and symlinks are skipped.
  - `GET /api/vaults/{name}/wiki?file=…` — defaults to `wiki/overview.md`, accepts any `.md` under the vault root, traversal blocked.
- Renders client-side with marked.js loaded from CDN; falls back to raw `<pre>` if the CDN is blocked.
- **Wikilinks**: `[[Page]]` and `[[Page|alias]]` are rewritten to inline `<a class="wikilink" data-wiki-target="…">` anchors *before* marked.parse runs. A delegated click handler on `#wiki-content` intercepts the click, resolves the target against the cached tree (case-insensitive, `.md` optional, subdir tolerated), and re-renders the new page in place. Embeds (`![[Foo]]`) collapse to a regular link in v1.
- Toolbar: vault context label + current file name + three canonical-page buttons — **Hot** (`wiki/hot.md`), **Index** (`wiki/index.md`), **Overview** (`wiki/overview.md`) — and `↻` reload (reloads both the tree and the current page). Buttons are wired declaratively via `data-wiki-page` so adding another canonical page is HTML-only.
- Sidebar mirrors the Help tab's tree styling: dirs sort before files, alpha within their bucket, active page highlighted.
- When `wiki/overview.md` does not exist, the panel shows a "no wiki yet" empty state nudging the user to spawn a Claude session and run the plugin.
- Edit mode is not yet built (read-only).

**Tasks tab:**
- Operations-first layout. The top of the tab is a **trigger panel** with: vault selector + `all vaults` toggle, operation dropdown grouped by Wiki / Research / Custom, per-operation parameter fields (URL, topic, prompt, argv, checkbox — no JSON textarea), priority, and a `When` `datetime-local` input (empty = run now). One **Run task** button submits.
- Operation registry lives client-side in `OPERATIONS` (`app.js`). It mirrors the operation list in `plugin_commands.py` + `task_manager.py`; no `/api/operations` endpoint.
- The queue below the trigger renders **task cards** (one card per task; left border tinted by state). Clicking the card head or the `log` button expands the card to reveal params, error, scheduled time, and a **live-tailing log pane** that subscribes to `task_log_appended` Socket.IO chunks. The pane is seeded from `GET /api/tasks/{id}/log` on first open.
- Queue filters: priority + state (`active` default | `recent (24h)` | `all`). When a vault is selected, the queue is filtered to its tasks plus all `ALL`-vault tasks.
- Per-card actions vary by state:
  - `running` → `cancel` (sends SIGTERM via `DELETE /api/tasks/{id}`)
  - `pending` / `deferred` → `cancel`, plus `promote` on deferred
  - `scheduled` → `run-now` (promotes immediately) and `cancel`; an **overdue** badge appears when `scheduled_for` is past
  - `completed` / `failed` / `cancelled` / `interrupted` → `re-run` (pre-fills the trigger panel with the original task's params)
- A dismissible **cron-skip banner** at the top of the tab shows up when a `cron_skip_warning` SocketIO event arrives (cron task fired but the window is inactive).
- **Compact log** button stays in the queue toolbar (snapshots terminal-state tasks > 90 days old via `POST /api/tasks/compact`).
- See `06-task-management.md` for the live-log streaming, log size cap, and cancel-running semantics.

**Config tab:**
- Live YAML editors for `resman.yaml` and `schedule.yaml` (Option J pattern)

**Help tab:**
- Two-pane layout: a file-tree sidebar on the left (the `man/` directory at the repo root), markdown content on the right.
- The tree is walked server-side via `GET /api/help/tree`; only directories and `.md` files are exposed (other extensions are rejected to keep the surface tight).
- Pages render through the same `marked.js` pipeline as the Wiki tab via `GET /api/help/page?file=…`.
- In-page links to relative `.md` paths are intercepted client-side and re-routed through the help tree (no full navigation away from the SPA).
- Override the source path with `app.man_path` in `resman.yaml` if the docs ship somewhere else.

## New-Vault Wizard

`+ New Vault` opens a modal with three optional steps:

1. **Vault name** + **Vault path** — the path field has a `Browse…` button that opens a stacked, server-side **folder picker** (z-index 300, above the wizard). The picker is rendered by the SPA and walks the filesystem via `GET /api/fs/list`; the user can navigate, jump to home, or type a new directory name to be created under the current folder.
2. **Scaffold the directory** (checkbox, default on) — calls `POST /api/vaults/scaffold` which runs `tools/new-vault.sh`. Uncheck to register an existing vault.
3. **Bootstrap wiki** (checkbox, default on) — after registering, opens a Claude session via `POST /api/sessions` with `bootstrap_new_vault: true`. The server pastes `tools/newValPrefix.md` + `/claude-obsidian:wiki` + `tools/newValSuffix.md` into the REPL as a single bracketed-paste message — so Claude checks the plugin first, runs the bootstrap (which may ask interactive questions the user answers in the Terminal tab), then copies the visual `workspace.json` into the new vault's `.obsidian/`.

Each step's status (info / ok / error) is reported inside the wizard so a partial failure (e.g., scaffold succeeds, registration fails) is visible without losing context.

## Theme Toggle

A `☾` / `☀` button in the header (and a duplicate in the status bar) flips
`document.documentElement[data-theme]` between `dark` and `light`. The choice
is persisted to `localStorage` under `resman-theme`, and an inline script in
`index.html` applies the saved theme **before render** so the page never
flashes the wrong palette.

## Connection Pill + Sessions Overview Modal

The `● connected` / `disconnected` indicator on the right of the header is a
clickable pill (`#conn-pill`). Clicking it opens a modal backed by
`GET /api/sessions/stats` that audits every tracked ttyd + tmux session and
its memory footprint, so the operator can spot heavy or stale terminals
without leaving the browser.

- **Per-session card** — vault, session type, port, age, tmux session name,
  alive flag, rolled-up RSS. Below the head: a 4-column table (role · pid ·
  command · rss) listing the ttyd process, every tmux pane, and the full
  descendant tree rooted at each pane. Child processes are indented under
  their pane so a runaway Claude is visible at a glance.
- **Orphans section** — tmux sessions matching the resman prefix that the
  control plane is not tracking (typically left over from a previous run).
  A red **Kill all** button runs `POST /api/sessions/orphans/kill`, then
  re-fetches `/api/sessions/stats` and re-renders the modal so the list
  reflects the new state. The action is best-effort — failures are listed
  per-name; a single failure does not abort the rest.
- The modal is read-only besides that one Kill-all action; no streaming. A
  user who wants live updates can re-open it. Closing the modal does
  nothing else.

## Window Status Bar (bottom, always visible)

- Fixed height; flex layout prevents overlap with terminal
- Color: green bar = `active`, gray = `between`, red = `ended`
- Shows: state label, time remaining (for `active`)
- Final 60 seconds of active window: label changes to "ending in Xs..." (yellow text)
- `[ sync ▼ ]` dropdown opens **upward** (bar is at screen bottom)
- Window overrun: time text reads "overrun by Xh Ym" and a persistent red **End window now** button appears next to it; clicking it ends the window (same as `sync ▾ → End window now`)

## Key Decisions

- **Vanilla JS + CDN** — no build step; no npm; dark terminal aesthetic
- **ttyd iframes** — xterm.js is not loaded by the SPA; ttyd serves its own xterm.js internally
- **Tab switching via display:none** — iframes persist their WebSocket connections; toggling visibility is instant and reconnection-free
- **Filter bar in Phase 1** — sidebar is unusable at scale without it; not deferred to later phases
- **`[↘]` ingest-URL shortcut** — single-purpose action; one click + URL prompt queues a `wiki-ingest` task and jumps to the Tasks tab. Spawning sessions has moved entirely to the header's `+ Shell` / `+ Claude` buttons
- **CSRF header via fetch wrapper** — single function in `app.js` wraps all fetch calls
- **Header tabs, not panel tabs** — Docs / Tasks / Config are top-level navigation in the header bar; the terminal is the default and has no tab
- **Per-vault tab strip** — vaults are independent workspaces; mixing all vaults' tabs in one strip turned out to be confusing in practice
- **Server-side folder picker** — browsers cannot expose absolute host paths from `<input type="file">`; we render our own picker over `GET /api/fs/list`
- **Tab labels in localStorage** — labels are operator-personal hints; not server state, not synced

## Constraints

- No npm, webpack, or build step — all JS and CSS served as static files
- `esc()` must be applied to all user-controlled values before DOM insertion
- Status bar must not overlap terminal content (use flex-column layout, not absolute positioning)
- `[ sync ▼ ]` dropdown must open upward
- Tab switching must not reload iframes

## Open Questions

- **Blue dot detection** — detecting whether Obsidian has a vault open requires process inspection, which is platform-specific. May be omitted in Phase 1 and replaced with "always gray unless another condition is true."
