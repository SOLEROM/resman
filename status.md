# resman — status snapshot

**Last updated:** 2026-05-12
**Branch:** main
**Tests:** 147 passing (`/tmp/resman-venv/bin/python -m pytest`)
**Server entry:** `./run.sh` (use `--vname /path/to/venv` to point at a non-default Python venv; pass `--public` to expose on LAN)

## Recent additions (post-Phase 6)

### Ops tab promoted to first-class header tab (2026-05-12)

The terminal-sessions view (formerly nameless, accessed by clicking the
vault label) is now a header tab named **Ops**, between Wiki and Tasks.
Header order: Wiki · Ops · Tasks · Config · Help. The vault-name label in
the header still works as an equivalent shortcut. Panel id renamed
`tab-terminal` → `tab-ops`. Legacy `"terminal"` entries in the per-vault
`localStorage` panel-memory are migrated to `"ops"` on load.

Docs updated: `design/10-frontend.md`.

### Sidebar `↘` button → ingest URL shortcut (2026-05-12)

The `[▶]` mini-menu on each sidebar row (which used to ask "claude or
shell?" and spawn a session) is repurposed as a one-click **URL ingest
shortcut**. Click `↘`, paste a URL, and resman:
1. selects that vault,
2. POSTs a `wiki-ingest` task with `params:{url}` at normal priority,
3. reloads the task list and switches to the **Tasks** tab so the new
   task is immediately visible.

Session spawning has moved entirely to the header's `+ Shell` / `+ Claude`
buttons; the sidebar no longer offers that path. Light client-side URL
validation (presence + `http(s)://` prefix); the operation-level params
validator on the backend is authoritative.

Docs updated: `design/10-frontend.md`.

### Header layout + Wiki as default tab (2026-05-12)

- **Per-vault action buttons moved to the header bar**, centered between
  the tab strip and the connection indicator. The group contains the vault
  name label + `✎` rename + `+ Shell` + `+ Claude` + `Obsidian` (renamed
  from `Open Obsidian`) + the `ttyd not installed` warning. Hidden when no
  vault is selected so the header stays clean on first paint.
- **Per-vault panel memory**: each vault remembers its own last-seen
  panel (Wiki / Tasks / Config / Terminal). Re-selecting the vault
  restores its own state. Persisted to `localStorage` under
  `resman-last-panel-by-vault` so it survives reload. Help is vault-
  independent and is not remembered.
- First-visit fallback (or remembered "terminal" but no live sessions):
  vault with a live session → **Terminal**; vault with no sessions →
  **Wiki**. Previously every click went to Terminal regardless.
- Terminal panel no longer carries its own per-vault toolbar — the buttons
  live globally in the header now.
- `+ Shell` / `+ Claude` **auto-switch to the Terminal panel** after
  spawning so the new iframe is visible (otherwise the click would appear
  to do nothing from Wiki/Tasks/Config/Help).
- Vault-name label in the header is **clickable** — jumps back to the
  Terminal view. Restores the "open my sessions" navigation path the old
  `click-vault → Terminal` behavior used to provide.

Docs updated: `design/10-frontend.md`.

### Wiki tab — page tree + clickable wikilinks (2026-05-12)

- **Left sidebar tree** on the Wiki tab listing every `.md` under
  `<vault>/wiki/`, recursively. Click to load; active page highlighted.
  New endpoint `GET /api/vaults/{name}/wiki/tree` walks the directory
  server-side (hidden + symlinks skipped) and returns sorted dirs + files
  with vault-relative paths. Fresh vault → `{missing:true, tree:[]}` so
  the SPA renders a "no wiki/ dir yet" placeholder.
- **Clickable `[[wikilinks]]`** — a regex pass on the markdown source
  before `marked.parse` rewrites `[[Page]]`, `[[Page|alias]]`, and
  `![[Page]]` to inline `<a class="wikilink" data-wiki-target="…">`
  anchors. A delegated click handler on `#wiki-content` resolves the
  target against the cached tree (case-insensitive, `.md` optional,
  subdir tolerated) and re-renders the new page in place — no browser
  navigation. Missing target surfaces as the standard 404 in the content
  pane.
- 4 new tests in `test_routes.py` covering the tree endpoint (recursive
  walk + sort + vault-relative paths, missing dir flag, unknown vault,
  dotfile + symlink exclusion).

Docs updated: `design/00-README.md`, `design/09-api.md`,
`design/10-frontend.md`, `man/wiki.md`.

### Phase 8 — Tasks UX redesign (Phase A from `plan5.md`)

- **Operations-first trigger panel** replacing the old `+ New Task` modal:
  inline form at the top of the Tasks tab with vault selector + `all vaults`
  toggle, operation dropdown grouped by Wiki / Research / Custom, per-op
  parameter fields (no JSON textarea), priority, and a `datetime-local`
  **When** input (empty = run now, future = schedule one-shot).
- **Task cards** replace the flat table. Click the card head or the `log`
  button to expand; the card shows params, error, scheduled time, and a
  **live-tailing log pane** subscribed to `task_log_appended` Socket.IO
  chunks. State filter (`active` default | `recent (24h)` | `all`).
- **Cancel running** — `DELETE /api/tasks/{id}` now terminates running
  subprocesses (`SIGTERM` → 5 s grace → `SIGKILL`) and writes a `cancelled`
  event. Streaming runner tracks Popen handles in `task_manager._procs`.
- **`scheduled` task state** — new discrete state in the lifecycle. The
  Scheduler arms a one-shot APScheduler `DateTrigger` that calls
  `promote(task_id)` at the chosen moment. Cancelling a scheduled task
  removes the trigger. Mixing `scheduled_for` with `vault: ALL` is rejected
  in v1. Overdue scheduled tasks (server was down at fire time) keep the
  state and surface in startup warnings; the UI shows an overdue badge with
  a `run-now` button.
- **PTY-based streaming runner + 5 MB log cap** — production
  `_run_streaming` spawns the child against a `pty.openpty()` master so
  CLIs that block-buffer when stdout is a pipe (`claude -p` among them)
  keep line-buffering and produce live output. A dedicated reader thread
  drains the master fd and emits `task_log_appended` chunks while writing
  the same bytes to `config/task-logs/<task_id>.log`. Output beyond
  `LOG_MAX_BYTES` is dropped with a marker so a runaway claude session
  can't OOM the browser. Pipe fallback if `/dev/ptmx` is unavailable.
- **PID-aware replay** — `started` events carry the PID; replay uses
  `os.kill(pid, 0)` to distinguish "control-plane restarted with subprocess
  still alive" (stays `running`) from "process is gone" (flips to
  `interrupted`).
- **Async dispatch under eventlet** — `server.py` wires
  `task_manager.set_executor(eventlet.spawn)` so `POST /api/tasks` returns
  immediately while the streaming runner runs in its own greenlet; cancel
  and live-log subscriptions reach the live process via the bus.
- 17 new tests across `test_task_manager.py` and `test_routes.py` covering
  scheduling, cancel-running, streaming chunks, log cap, PID-aware replay,
  and route-level `scheduled_for` validation.

Design + man docs updated for the redesign:
- `design/00-README.md` — added Phase 8 table.
- `design/01-architecture.md` — TaskManager/Scheduler component notes.
- `design/06-task-management.md` — PTY note, async dispatch, scheduled
  state, cancel-running semantics, key decisions section.
- `design/08-scheduler.md` — one-shot DateTrigger model, `task_scheduled`
  bus subscription, overdue-replay rules.
- `design/09-api.md` — `scheduled_for`, `task_log_appended`,
  `task_scheduled`, cancel-running on DELETE.
- `design/10-frontend.md` — operations-first Tasks tab rewrite.
- `design/12-error-handling.md` — PID-aware replay, overdue-scheduled,
  PTY-fallback, log-cap, cancel-running rows.
- `man/index.md` — Tasks bullet updated.
- `man/tasks.md` — full rewrite.
- `man/scheduler.md` — recurring vs one-shot distinction.
- `man/troubleshooting.md` — added live-log/buffering, log-cap,
  cancel-stuck, and "connecting…" entries.
- `man/reference/api.md` — full Socket.IO event table including the two
  new events; Tasks endpoint table reflects scheduled_for + cancel.

### Earlier in 2026-05-10

- **`--public` / `--host` flags** — expose Flask + ttyd on the LAN; CORS
  relaxed; iframe URLs derived from `window.location.hostname` so they work
  whether you load via 127.0.0.1, localhost, or LAN IP.
- **Wiki tab** (renamed from Docs) — defaults to `<vault>/wiki/overview.md`,
  with three canonical-page buttons in the toolbar (**Hot** / **Index** /
  **Overview**) plus reload. Endpoint: `GET /api/vaults/{name}/wiki`.
  Health check renamed: `wiki_home_exists` (`wiki/overview.md`) replaces the
  old `readme_exists` row. The per-vault `readme:` config field was removed.
- **Help tab** — file-tree of `<repo>/man/*.md` rendered with `marked.js`.
  Endpoints: `GET /api/help/tree` + `GET /api/help/page?file=…`. Pages live
  at `/mnt/resman/man/` and travel with the source.

---

## Where we are

The 4-phase plan from `design/00-README.md` is implemented end-to-end. Two
rounds of operator-feedback work and a Phase 5 polish pass are in. Phase 6
(verification) is half-done — the items that fit inside the test suite are
done; the items requiring a real browser or a different host machine are
not.

```
Phase 0  Pre-implementation validation         ✅
Phase 1  Obsidian Push + Core Shell            ✅
Phase 2  Terminal Sessions (ttyd)              ✅
Phase 3  Task Queue                            ✅
Phase 4  ALL-Vaults, Cron, Polish              ✅
Operator round 1 (folder picker, wizard,
  bootstrap-via-session, ttyd race, tab
  rename, theme, header tabs, --vname, …)     ✅
Phase 5  Finish placeholders                   ✅
Phase 6  Verify what's claimed                 🟡 in-repo done; live items pending
Phase 7  Production hygiene                    ⛔ not started
```

---

## What got built in Phase 5 (the most recent block)

| Feature | Files |
|---|---|
| Markdown-rendered Docs tab (marked.js from CDN) | `static/js/app.js` `loadDocs()`, `templates/index.html`, `static/css/style.css` `.docs-body` |
| `GET /api/vaults/{name}/readme` (path-traversal blocked via `Path.relative_to()`) | `modules/routes.py` |
| Open Obsidian button → `POST /api/vaults/{name}/open` (detached subprocess, vault path appended) | `modules/routes.py`, `static/js/app.js` `openVaultInObsidian()` |
| Vault health modal — clickable `⚠`/`?` warn icons | `static/js/app.js` `showVaultHealth()`, `style.css` `.health-table` |
| Compact-log button in Tasks toolbar | `static/js/app.js` `compactTasksLog()` |
| Cron-skip warning banner | `templates/index.html` `#cron-skip-banner`, `app.js` `showCronSkipBanner()` |
| Window-overrun "End window now" red button | `templates/index.html`, `app.js` `renderWindow()` |
| 9 new route tests (README + open-obsidian + health-extras) | `tests/test_routes.py` |

## What got built in Phase 6 (in-repo verification)

| Feature | Files |
|---|---|
| ALL-vault parent/child aggregation tests | `tests/test_task_manager.py` 3 new tests — fan-out, parent-fails-when-any-child-fails, dispatch_started ordering |
| JSONL crash-recovery tests | `tests/test_task_manager.py` 5 new tests — corrupt line, partial final line, running→interrupted, empty file, blank lines |

One real subtlety surfaced and is captured: `make_tm()` calls `replay()` on
the default log path during fixture setup, which would silently truncate any
hand-crafted partial line before the test's own replay() saw it. The new
helper `_write_corrupt_log()` writes to a sibling subdir to avoid the
collision.

---

## Where to pick up next time

### Phase 6 — physical verification still pending

These cannot be done from inside the test suite. They need a real browser
or a different host. Each one is independent of the others.

1. **Chrome iframe behavior** — only Firefox is verified. Open the app in
   Chrome, spawn a Claude session, watch for silent reconnect loops or 403s
   on the ttyd iframe. ttyd is launched with `--check-origin=false`; that
   should be enough, but Chrome's SameSite/SecFetch rules differ.
2. **Live ttyd race** — the `_wait_for_listen()` fix is unit-tested with a
   mock. To stress it, spawn 5+ sessions back-to-back on a slow VM and
   confirm none of the iframes show "connection refused".
3. **iCloud / Obsidian Sync vault** — the 60-second `_resman/status.md`
   write may cause sync churn or conflicts. Open question in
   `design/05-obsidian-push.md`. Needs an actual CloudKit-backed vault.
4. **Ubuntu 22 host run** — the snap → prebuilt-binary fallback chain in
   `deps.sh` and the broken-shebang detection in `run.sh` are written but
   only verified on Ubuntu 24. Run `./deps.sh --check && ./run.sh` on the
   Ubuntu 22 box.

### Phase 7 — production hygiene (only if you'll leave it running 24/7)

In rough priority order:

- `systemd --user` unit so `run.sh` survives reboot
- Server log file with rotation (currently goes to stdout; lost when the
  shell that started it closes)
- Automatic `system.yaml` backup before each save (atomic write protects
  against partial writes, not against bad edits)
- Surface task replay summary on the Health page UI (the data is in
  `/api/health` already; just not rendered)

### Phase 8 — open design questions still unresolved

- Blue dot (Obsidian-open per vault) — process inspection, platform-specific.
  Probably fine to drop the requirement entirely. Mark in
  `design/03-vault-registry.md` if so.
- `skip_count` reset semantics on successful cron fire — assumed yes in
  `design/08-scheduler.md` but not actually wired. Trivial to settle once
  decided.

---

## Things probably **not** worth building

- Auth — it's bound to `127.0.0.1`; the CSRF header is sufficient.
- Multi-host or clustering — explicitly out of scope.
- E2E browser test suite — useful but heavy. Manual smoke on Chrome covers
  the highest-value scenarios.

---

## Useful pointers

| Want to … | Look at |
|---|---|
| Understand the architecture top-down | `design/00-README.md` (index), `design/01-architecture.md` |
| Add a new endpoint | `control-plane/modules/routes.py` + a test in `tests/test_routes.py` |
| Change the SPA | `control-plane/static/js/app.js`, `templates/index.html`, `static/css/style.css` |
| Trace how a task event flows | `control-plane/modules/task_manager.py` `_apply()` (replay) and `_finalize()` (live) |
| Trace how a session is spawned | `control-plane/modules/session_manager.py` `spawn()` — note the `_wait_for_listen()` race fix and `initial_command` for the new-vault wizard |
| See what tmux options resman applies per session | `control-plane/modules/tmux_manager.py` `_apply_session_options()` |
| Run on a fresh host | `./deps.sh` then `./run.sh` (both accept `--vname /custom/venv`) |
| Run the test suite | `/tmp/resman-venv/bin/python -m pytest` |

---

## Recommended next single step

If picking this up cold, the highest-value 30-minute task is **Chrome
iframe verification** — it's the only blocker that could falsify the whole
ttyd-iframe approach. Open `http://127.0.0.1:5090` in Chrome with the
DevTools network panel open, spawn a Claude session, look for 4xx on the
ttyd port. If Chrome behaves, declare Phase 6 done and decide whether
Phase 7 is worth doing now or later.
