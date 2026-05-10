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
│ ● ai-agents  [▶]  │ vla6·claude·08:25 ×                             │   ← per-vault tab strip
│ ○ llm-bench  [▶]  ├────────────────────────────────────────────────┤
│ ─ unregistered    │                                                  │
│   found-vault     │  ttyd iframe  OR  Markdown / Tasks / Config      │
│ [+ New Vault]     │                                                  │
├────────────────────┼────────────────────────────────────────────────┤
│ Window: ● ACTIVE  ends in 3h 12m                  [ sync ▼ ]  ☾    │
└─────────────────────────────────────────────────────────────────────┘
```

Tabs (Wiki / Tasks / Config / Help) live in the **top header bar** — they are
top-level navigation, not nested under the vault. The default panel is
the Terminal view; clicking a vault always returns to the Terminal view
and clears the active header tab. There is no separate "Terminal" tab in
the header; "no header tab active" is the terminal state.

Layout uses a flex-column root so the status bar is always visible at a fixed height
and never overlaps terminal content.

## Left Sidebar

**Filter bar (top of sidebar):**
- Search field: filters vault list by name (client-side, instant)
- Tag filter: multi-select chips; shows vaults matching any selected tag
- Status filter: dropdown — any / active session / has tasks / has error
- Present in Phase 1; a sidebar with 20+ gray dots is unusable without it

**Vault list:**
- Each row: status dot, vault name, tags (dimmed), `[▶]` button
- Clicking vault name selects the vault and switches main panel to Terminal tab
- `[▶]` opens a mini-menu with exactly two options: "Open Claude" and "Open Shell" — no hidden default
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

**Terminal view (default):**
- Each tab is an independent ttyd session (one `<iframe>` per session)
- The tab strip is **filtered to the currently selected vault** — switching vaults swaps both the tab strip and the visible iframe (every iframe stays in the DOM so its WebSocket persists; only `display` toggles)
- Default tab label: `<vault> · <type> · <HH:MM>` (e.g., `vla6 · claude · 08:25`)
- Tab switching: click anywhere on a tab → switch; click `×` → kill the session; tab labels are persisted to `localStorage` so renames survive reload
- Toolbar buttons (left → right): `✎` rename active tab, `+ Shell`, `+ Claude`, `Open Obsidian`
- The `✎` button opens a modal asking for a new label (blank restores the default); each user's custom labels live only in their browser
- Per-vault session memory: `state.lastSessionByVault` remembers which session was last active for each vault, so revisiting a vault restores the same terminal
- If ttyd unavailable: tab strip shows "ttyd not installed — terminal sessions disabled"

**Wiki tab** (formerly "Docs"):
- Markdown viewer for the vault's Claude-wiki-plugin output. Defaults to `wiki/overview.md` (the landing page resman opens when the tab is shown).
- Endpoint: `GET /api/vaults/{name}/wiki?file=…` — defaults to `wiki/overview.md`, accepts any `.md` under the vault root, traversal blocked.
- Renders client-side with marked.js loaded from CDN; falls back to raw `<pre>` if the CDN is blocked.
- Toolbar: vault context label + current file name + three canonical-page buttons — **Hot** (`wiki/hot.md`), **Index** (`wiki/index.md`), **Overview** (`wiki/overview.md`) — and `↻` reload. Buttons are wired declaratively via `data-wiki-page` so adding another canonical page is HTML-only.
- When `wiki/overview.md` does not exist, the panel shows a "no wiki yet" empty state nudging the user to spawn a Claude session and run the plugin.
- Edit mode is not yet built (read-only).

**Tasks tab:**
- Task queue panel for selected vault, or all vaults if no vault selected
- Toolbar: priority filter, `Compact log` (snapshots terminal-state tasks > 90 days old), `+ New Task`
- A dismissible **cron-skip banner** appears at the top when a `cron_skip_warning` SocketIO event arrives (cron task fired but the window is inactive); it shows cron name, skip count, and the last attempted fire time
- See `06-task-management.md` for the task UI panel details

**Config tab:**
- Live YAML editors for `system.yaml` and `schedule.yaml` (Option J pattern)

**Help tab:**
- Two-pane layout: a file-tree sidebar on the left (the `man/` directory at the repo root), markdown content on the right.
- The tree is walked server-side via `GET /api/help/tree`; only directories and `.md` files are exposed (other extensions are rejected to keep the surface tight).
- Pages render through the same `marked.js` pipeline as the Wiki tab via `GET /api/help/page?file=…`.
- In-page links to relative `.md` paths are intercepted client-side and re-routed through the help tree (no full navigation away from the SPA).
- Override the source path with `app.man_path` in `system.yaml` if the docs ship somewhere else.

## New-Vault Wizard

`+ New Vault` opens a modal with three optional steps:

1. **Vault name** + **Vault path** — the path field has a `Browse…` button that opens a stacked, server-side **folder picker** (z-index 300, above the wizard). The picker is rendered by the SPA and walks the filesystem via `GET /api/fs/list`; the user can navigate, jump to home, or type a new directory name to be created under the current folder.
2. **Scaffold the directory** (checkbox, default on) — calls `POST /api/vaults/scaffold` which runs `tools/new-vault.sh`. Uncheck to register an existing vault.
3. **Bootstrap wiki** (checkbox, default on) — after registering, opens a Claude session via `POST /api/sessions` with `initial_command: "/claude-obsidian:wiki"`. The bootstrap may ask interactive questions; the user answers them in the Terminal tab.

Each step's status (info / ok / error) is reported inside the wizard so a partial failure (e.g., scaffold succeeds, registration fails) is visible without losing context.

## Theme Toggle

A `☾` / `☀` button in the header (and a duplicate in the status bar) flips
`document.documentElement[data-theme]` between `dark` and `light`. The choice
is persisted to `localStorage` under `resman-theme`, and an inline script in
`index.html` applies the saved theme **before render** so the page never
flashes the wrong palette.

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
- **`[▶]` mini-menu** — two explicit choices; no implicit default session type
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
