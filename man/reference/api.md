---
noteId: "4fdca8904f5d11f18eaba108b9c533e7"
tags: []

---

# API reference

resman speaks JSON over HTTP plus a Socket.IO channel for live events. All
mutating endpoints (`POST` / `DELETE` / `PATCH`) require the
`X-Requested-With: resman` header.

## Vaults

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/vaults` | List registered + discovered vaults; also returns `vault_default_root` (the optional `app.vault_default_root_path`, or `null`) for the New Vault wizard |
| POST | `/api/vaults` | Register a vault |
| POST | `/api/vaults/scaffold` | Create `.obsidian/` for a path that lacks it |
| GET | `/api/vaults/<name>/health` | Health summary (path, .obsidian, wiki home, …) |
| GET | `/api/vaults/<name>/wiki?file=…` | Read a wiki page; default `wiki/overview.md` (toolbar buttons: Hot / Index / Overview) |
| POST | `/api/vaults/<name>/open` | Launch `obsidian_cmd` on this vault |

## Sessions

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/sessions` | List live sessions + orphaned tmux sessions |
| GET | `/api/sessions/stats` | Sessions-overview payload: ttyd PID + RSS, every tmux pane and its descendant process tree (each with `pid`, `comm`, `ppid`, `rss_kb`), per-session and global `total_rss_kb`, plus `orphaned_tmux_sessions` and `tmux_socket`. Powers the connection-pill modal — read-only, no CSRF |
| POST | `/api/sessions` | Spawn a session. Body: `{vault, type, initial_command?}` |
| POST | `/api/sessions/orphans/kill` | Kill every tmux session matching the resman prefix that is not in the live registry. Returns `{killed:[...], failed:[{name,error}...]}` — best-effort |
| DELETE | `/api/sessions/<id>` | Kill the ttyd process **and** its underlying tmux session. Closing the `×` on a tab is treated as a full "done" so no orphan tmux accumulates |

## Tasks

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/tasks` | List tasks (filters: `vault`, `priority`, `state`, `limit`, `offset`) |
| POST | `/api/tasks` | Create a task. Optional `scheduled_for: <ISO8601>` parks the task in `scheduled` state for a single future fire. Past timestamps and combinations with `vault: ALL` return 400. Optional `force: true` bypasses window-gating (used by the sidebar `↘` ingest shortcut and `tools/remoteAgent.sh`); ignored when `scheduled_for` is also set. |
| POST | `/api/tasks/<id>/promote` | Transition a `deferred` or `scheduled` task to `pending` and dispatch immediately |
| DELETE | `/api/tasks/<id>` | Cancel a `pending` / `deferred` / `scheduled` / **`running`** task. Running tasks receive `SIGTERM`, then `SIGKILL` after a 5 s grace. Writes a `cancelled` event |
| POST | `/api/tasks/<id>/archive` | Soft-delete a terminal-state task (excluded from default view, preserved in log) |
| POST | `/api/tasks/compact` | Snapshot terminal-state tasks > 90 days old |
| GET | `/api/tasks/<id>` | Get a single task's current state |
| GET | `/api/tasks/<id>/log` | Read the task's captured stdout/stderr backlog. Live tailing is via the `task_log_appended` Socket.IO event |

## Window

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/window` | Current window state |
| POST | `/api/window/sync` | `{action: start|end|start_weekly|end_weekly}` |

## Config

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/config/yaml?file=resman.yaml` | Return file contents; also includes `resman_path`, `resman_display_path`, `using_user_override` |
| POST | `/api/config/yaml` | Replace + validate (body: `{file, content}`) |
| GET | `/api/config/yaml?file=schedule.yaml` | Return file contents |
| POST | `/api/config/yaml` | Replace + validate (body: `{file, content}`) |

Legacy: `file=system.yaml` still accepted as an alias for `resman.yaml`.

## Filesystem

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/fs/list?path=…` | List subdirectories. Used by the new-vault picker. |

## Help

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/help/tree` | Walk `man/`, return nested dirs + .md files |
| GET | `/api/help/page?file=…` | Read one page; default `index.md` |

## Socket.IO events (server → client)

| Event | Payload | When |
|-------|---------|------|
| `task_updated` | task dict | Any task state transition |
| `task_log_appended` | `{task_id, chunk}` | A line of stdout/stderr from a running task — the Tasks tab uses this for live tailing without polling |
| `task_scheduled` | `{task_id, scheduled_for}` | A task entered `scheduled` state and the Scheduler should register a one-shot DateTrigger |
| `window_state_changed` | new state | Window transitions between `active` / `between` / `ended` |
| `session_crashed` | `{session_id, vault, message}` | A ttyd process died unexpectedly |
| `session_error` | `{vault, reason}` | `TmuxManager.create_session()` failed |
| `child_state_changed` | `{parent_id, child_id, state}` | ALL-vault child completed/failed; parent re-aggregates |
| `config_reloaded` | `{}` | resman.yaml or schedule.yaml saved successfully |
| `cron_skip_warning` | `{cron_name, skip_count, last_fired_at}` | Cron task skipped > 2 times in a row |

## Errors

All endpoints return `{"error": "<message>"}` on failure with a non-2xx
status. Validation errors are 400; missing resources are 404; CSRF or auth
failures are 403; misconfiguration (no ttyd, no obsidian_cmd) is 503.
