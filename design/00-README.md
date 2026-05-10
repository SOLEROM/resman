# resman — Design Document Index

resman is a local-only web command-and-control panel for managing multiple Obsidian
research vaults on one machine. It runs as a single Flask+SocketIO process, coordinates
tmux sessions via an isolated socket, spawns ttyd processes for browser terminals (one
iframe per session), pushes vault health status into each vault's filesystem every 60
seconds so it appears in Obsidian's graph view, and maintains a prioritized task queue
backed by an append-only JSONL event log — all gated on a manually-managed Claude Code
window state. Design source: plan4.md (authoritative) with plan2.md as structural backup.

---

## Files

| # | File | Concern |
|---|------|---------|
| 01 | [01-architecture.md](01-architecture.md) | System overview, component map, directory layout, technology stack and mandatory constraints |
| 02 | [02-configuration.md](02-configuration.md) | system.yaml / schedule.yaml / budget.json schemas; atomic writes; live editing via YAML editor |
| 03 | [03-vault-registry.md](03-vault-registry.md) | Vault loading, .obsidian/ validation, scan_paths discovery, health check modal |
| 04 | [04-terminal-sessions.md](04-terminal-sessions.md) | ttyd-based sessions, SessionManager, port registry, SessionMonitor greenlet, reconcile on restart |
| 05 | [05-obsidian-push.md](05-obsidian-push.md) | _resman/status.md push into vault graph view, cadence, pre-implementation validation checklist |
| 06 | [06-task-management.md](06-task-management.md) | Task model, JSONL event sourcing, state machine, ALL-vault parent/child, compaction |
| 07 | [07-window-state.md](07-window-state.md) | Window states, transition rules, is_window_active(), EventBus decoupling, task gating |
| 08 | [08-scheduler.md](08-scheduler.md) | GeventScheduler, cron tasks, skip-when-inactive, cron_skipped events, ObsidianPush job |
| 09 | [09-api.md](09-api.md) | Full REST surface, Socket.IO events emitted, CSRF header, ttyd graceful degradation |
| 10 | [10-frontend.md](10-frontend.md) | UI layout, sidebar filter bar, vault dot priority rule, terminal iframes, status bar |
| 11 | [11-security.md](11-security.md) | Subprocess safety, input validation, path traversal prevention, run-shell acknowledgment |
| 12 | [12-error-handling.md](12-error-handling.md) | Startup fail-fast vs graceful degradation, error behavior table, JSONL crash consistency |

---

## Implementation Phases (from plan4.md)

| Phase | Goal | Key deliverable |
|-------|------|-----------------|
| **0** | Pre-implementation validation | Manually verify Obsidian hot-reload + ttyd iframe in browser before writing code |
| **1** | Obsidian Push + Core Shell | Vault sidebar, status dots, ObsidianPush 60s cron, window status bar, YAML editor |
| **2** | Terminal Sessions (ttyd) | SessionManager, iframe terminals, SessionMonitor, orphaned session display |
| **3** | Task Queue | TaskManager, JSONL log, task UI panel, window gating |
| **4** | ALL-Vaults, Cron, Polish | Parent/child tasks, GeventScheduler cron, vault discovery, vault creation wizard |

## Open Questions Summary

| File | Question | Status |
|------|---------|--------|
| 03-vault-registry.md | How to detect Obsidian is open for Blue dot condition | Open |
| 04-terminal-sessions.md | CORS: ttyd iframe cross-origin behavior in Chrome vs Firefox — must test | Verified Firefox; ttyd uses `--check-origin=false` |
| 05-obsidian-push.md | iCloud/Obsidian Sync vault compatibility with 60s write cycles — must test | Open |
| 08-scheduler.md | skip_count: reset to 0 on successful fire (assumed yes) | Open |
| 09-api.md | GET /api/sessions implied but not listed in plan4; likely needed for page reload | **Resolved** — implemented |
| 10-frontend.md | Blue dot detection may be omitted in Phase 1 | Open |

## Post-Phase-4 Additions (built but not in original plan4)

These are additions made during operator feedback after the 4-phase plan was
implemented. Each is documented in the file noted.

### Operator-feedback round 1

| Addition | File |
|----------|------|
| Two-step vault creation (`POST /api/vaults/scaffold` + `POST /api/vaults`) | 03, 09, 10 |
| Server-side folder picker (`GET /api/fs/list`) | 09, 10 |
| Wiki bootstrap via interactive Claude session (`POST /api/sessions` `initial_command`) | 04, 09, 10 |
| `_wait_for_listen` ttyd race fix on session spawn | 04, 12 |
| Per-session tmux polish (`status off`, `mouse on`, scrollback, etc.) | 04 |
| Per-vault terminal tab strip + tab rename + theme toggle | 10 |
| Top-bar header tabs (Docs / Tasks / Config) | 10 |
| `deps.sh` / `run.sh` `--vname` flag and stale-venv recovery | 01, 12 |

### Phase 5 — finish the placeholders

| Addition | File |
|----------|------|
| Markdown-rendered Wiki tab (`GET /api/vaults/{name}/wiki`, marked.js) — defaults to `wiki/overview.md`. Toolbar exposes Hot / Index / Overview buttons for the three canonical plugin pages. Renamed from "Docs" tab; the per-vault `readme:` config field was dropped at the same time | 09, 10, 02 |
| Help tab — `GET /api/help/tree` + `GET /api/help/page` render the repo's `man/` directory as a navigable tree (override path via `app.man_path`) | 09, 10, 02 |
| LAN access — `--public` / `--host` CLI flags bind Flask + ttyd to `0.0.0.0`, relax CORS | 01, 04, 11 |
| Open Obsidian button (`POST /api/vaults/{name}/open`) | 09, 10 |
| Vault health modal — clickable `⚠`/`?` warn icon | 03, 09, 10 |
| Compact-log button in the Tasks toolbar | 06, 10 |
| Cron-skip warning banner in the Tasks panel | 10 |
| Window-overrun "End window now" action button | 10 |

### Phase 6 — verify what's claimed but untested (in-repo items)

| Addition | File |
|----------|------|
| ALL-vault parent/child aggregation tests (rolls up to `failed` when any child fails; `dispatch_started` carries `expected_child_count` before children) | 06 |
| JSONL crash-recovery tests (corrupt line skipped, partial final line truncated, `running`-at-crash → `interrupted`, empty + blank-line tolerant) | 12 |

Phase 6 items still requiring physical verification (Chrome iframe, real ttyd
race under load, iCloud/Sync vault compatibility, Ubuntu 22 host run) live
outside the test suite — see `status.md` for the punch list.
