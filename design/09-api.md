# API Surface

## Overview

`routes.py` defines the REST API and `websocket_handlers.py` defines the Socket.IO events.
All mutating REST endpoints require the `X-Requested-With: resman` header; requests without
it are rejected with HTTP 403. The SPA applies this header to every outgoing request via
a shared fetch wrapper, so individual call sites do not need to remember it. When ttyd is
not installed, terminal session endpoints return 503; all other endpoints function normally.

## REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Server health: config, tmux, ttyd, scheduler, task replay status |
| GET | `/api/vaults` | List all registered vaults with status (registered + discovered) |
| POST | `/api/vaults` | Register a vault in `resman.yaml` (name, path, tags). Does not create the directory |
| POST | `/api/vaults/scaffold` | Run `tools/new-vault.sh` to create the directory tree (path, `.obsidian/`, `inbox/`, `_resman/`, README, gitignore). Body: `{name, path}` |
| GET | `/api/vaults/{name}/health` | Vault health check: path, .obsidian/, wiki home (`wiki/overview.md`), last session, last completed task, tags |
| GET | `/api/vaults/{name}/wiki` | Read raw markdown of a wiki page produced by the Claude wiki plugin. Defaults to `wiki/overview.md`; the three canonical pages exposed in the toolbar are `wiki/hot.md`, `wiki/index.md`, `wiki/overview.md`. Other pages reachable via `?file=wiki/<page>.md`. Path-traversal blocked. Used by the **Wiki** tab |
| GET | `/api/vaults/{name}/wiki/tree` | Walk `<vault>/wiki/` recursively, returning sorted dirs + `.md` files for the Wiki tab's sidebar tree. Paths are vault-relative (e.g. `wiki/sources/foo.md`). Hidden entries and symlinks are skipped. Returns `{missing: true, tree: []}` when the vault has no `wiki/` directory yet (fresh / pre-bootstrap) |
| POST | `/api/vaults/{name}/open` | Launch Obsidian for this vault using `obsidian_cmd` from resman.yaml; subprocess detached, vault path appended as a final arg |
| GET | `/api/fs/list?path=…` | Read-only directory listing for the server-side folder picker. Returns `{path, parent, home, entries: [{name, path, is_obsidian}]}`. Hides dotfile directories. No CSRF (read-only GET) |
| GET | `/api/sessions` | List live terminal sessions and `available` flag (ttyd installed) |
| POST | `/api/sessions` | Spawn terminal session (vault, type: `claude`\|`shell`, optional `initial_command`); 503 if ttyd missing |
| DELETE | `/api/sessions/{id}` | Kill a terminal session (terminate ttyd; tmux session survives) |
| GET | `/api/tasks` | List tasks (filters: vault, priority, status, limit, offset) |
| POST | `/api/tasks` | Create a task. Optional `scheduled_for: ISO8601` parks the task in `scheduled` state for one-shot future firing; rejected if combined with `vault: ALL` or with a past timestamp |
| GET | `/api/tasks/{id}` | Get single task state |
| GET | `/api/tasks/{id}/log` | Get task execution log (full backlog; live tail is via the `task_log_appended` Socket.IO event) |
| DELETE | `/api/tasks/{id}` | Cancel a pending / deferred / scheduled / **running** task. Running tasks receive `SIGTERM` then `SIGKILL` after a 5 s grace. Writes `cancelled` event |
| POST | `/api/tasks/{id}/promote` | Promote a deferred **or** scheduled task to pending and dispatch immediately |
| POST | `/api/tasks/{id}/archive` | Archive a terminal-state task |
| POST | `/api/tasks/compact` | Trigger manual JSONL compaction |
| GET | `/api/window` | Get current window state |
| POST | `/api/window` | Set window state (body: `action`, `duration_hours`) |
| GET | `/api/cron` | List cron tasks with `last_fired_at`, `skip_count` |
| GET | `/api/config/yaml?file=…` | Read raw YAML for `resman.yaml` or `schedule.yaml`; returns extra fields for resman.yaml: `resman_path`, `resman_display_path` (tildified, e.g. `~/.resman.yaml`), `using_user_override` (bool). Accepts legacy `file=system.yaml` alias |
| POST | `/api/config/yaml` | Save resman.yaml or schedule.yaml (body: `{file, content}`); accepts legacy `file=system.yaml` alias |
| GET | `/api/help/tree` | Walk the `man/` directory tree (root: `<repo>/man` or `app.man_path`) and return nested `.md` files. Used by the **Help** tab. Returns `{root, missing, tree:[…]}` |
| GET | `/api/help/page?file=…` | Read one help page; default `index.md`. Only `.md` files served. Path-traversal blocked |

`/api/window` actions: `start` | `end` | `start_weekly` | `end_weekly`

### POST /api/sessions body

```json
{
  "vault": "vla6",
  "type": "claude",
  "initial_command": "/claude-obsidian:wiki"
}
```

`initial_command` is optional, ≤200 chars, claude-type only. The server fires
the slash command into the freshly-launched Claude REPL after a short delay
via `tmux send-keys`. Used by the new-vault wizard for interactive bootstrap
(the bootstrap may ask questions; the user answers them in the Terminal tab).

### Two-step vault creation

The browser SPA calls these in order:
1. `POST /api/vaults/scaffold` (only when "Scaffold the directory" is checked) → runs `tools/new-vault.sh` to materialize the tree
2. `POST /api/vaults` → appends the vault entry to `resman.yaml` (or the active `~/.resman.yaml` override)
3. `POST /api/sessions` with `initial_command: "/claude-obsidian:wiki"` (only when "Bootstrap wiki" is checked)

Each step is independently failable; the wizard reports per-step status.

### GET /api/fs/list

Backs the server-side folder picker. Browsers can't expose absolute host paths
from `<input type="file">`, so the SPA renders its own picker and walks the
host filesystem via this endpoint. Read-only — no CSRF requirement. `path` is
optional; defaults to the user's home. Special value `~` resolves to home.

## Mutating Endpoints

All POST, DELETE, and PATCH endpoints require:
```
X-Requested-With: resman
```
Missing header → HTTP 403. This applies to all endpoints in the table marked POST or DELETE.

The SPA applies this header via a single fetch wrapper used for all outgoing requests.
Individual call sites do not check or set the header directly.

## Socket.IO Events (server → browser)

| Event | When | Payload |
|-------|------|---------|
| `window_state_changed` | Window state flips in any direction | `{state, ends_at}` |
| `session_crashed` | ttyd process exits unexpectedly | `{session_id, vault, message}` |
| `session_error` | TmuxManager.create_session() fails | `{vault, reason}` |
| `child_state_changed` | A child task changes state | `{parent_id, child_id, state}` |
| `config_reloaded` | Config saved successfully | `{}` |
| `task_updated` | Any task state change | `{task_id, state}` |
| `task_log_appended` | A line of stdout/stderr from a running task | `{task_id, chunk}` |
| `task_scheduled` | A task entered `scheduled` state and needs a one-shot trigger | `{task_id, scheduled_for}` |
| `cron_skip_warning` | cron task skip_count > 2 | `{cron_name, skip_count, last_fired_at}` |

## ttyd Graceful Degradation

When ttyd is not found at startup:
- `POST /api/sessions` returns HTTP 503 with body: `{"error": "ttyd not installed"}`
- `DELETE /api/sessions/{id}` returns HTTP 503
- All other endpoints function normally
- Startup report shows: `ttyd: MISSING (terminal sessions disabled — install ttyd to enable)`

## Key Decisions

- **CSRF via header** — `X-Requested-With: resman` is sufficient for a localhost-only tool; no token storage or double-submit pattern needed
- **SPA fetch wrapper** — single point for applying the CSRF header; prevents per-call omissions
- **503 on terminal endpoints** — clear, actionable error when ttyd is absent; does not block the rest of the system
- **`cancelled` event** — `DELETE /api/tasks/{id}` writes a `cancelled` event to the JSONL log rather than deleting the task record; the audit trail is preserved

## Constraints

- Every POST and DELETE endpoint must check `X-Requested-With: resman` before processing
- HTTP 403 must be returned before any state mutation occurs
- `POST /api/sessions` must return 503 (not 500) when ttyd is absent
- `POST /api/window` must validate `duration_hours` (required for `start` action; must be 1–12)

## Open Questions

- None — `GET /api/sessions` is implemented (browser uses it to restore session state on reload).
