# Error Handling and Failure Modes

## Overview

resman distinguishes between required components (tmux, system.yaml) that cause a
hard startup failure and optional components (ttyd) that degrade gracefully. Every
system boundary has a defined error behavior — nothing fails silently. Startup
produces a structured report to stdout listing the status of each component. The
JSONL task log is crash-consistent: bad lines are skipped, partial final lines are
truncated, and tasks that were running at crash are surfaced with an `interrupted`
state rather than being silently dropped or auto-retried.

## Startup Report Format

```
resman starting...
  config:    OK (3 vaults loaded)
  tmux:      OK (socket: resman)
  ttyd:      OK (/usr/local/bin/ttyd v1.7.4)
  scheduler: OK (2 cron tasks)
  tasks:     OK (replayed 142 events, 1 bad line skipped)
  plugin:    OK (claude-obsidian v1.4.2, compatible)
  server:    http://127.0.0.1:5090
```

On failure: `ttyd: MISSING (terminal sessions disabled — install ttyd to enable)`.

## Error Behavior Table

| Scenario | Behavior |
|----------|----------|
| `system.yaml` missing | Fail loudly: print error to stdout + show browser banner; do not start |
| `system.yaml` invalid YAML | Same |
| `tasks.jsonl` corrupt line | Skip line; log byte offset and error; continue replay; report skipped count in startup output |
| `tasks.jsonl` partial final line | Truncate; emit warning; continue |
| Task in `started` state with no terminal event | Replay as `interrupted`; surface to user |
| `dispatch_started` child count mismatch | Warn in startup report: "Partial dispatch detected for task {id} — {N} of {M} children created" |
| tmux not installed | Fail loudly on startup; do not start |
| ttyd not installed | Warn in startup report; disable terminal session endpoints (503); rest of server starts |
| ttyd process crashes mid-session | SessionMonitor detects within 5s; emit `session_crashed` SocketIO event; browser shows toast with [Restart] |
| ttyd process spawns but never accepts connections | `_wait_for_listen()` times out after 5s, terminates the process, and returns 503 — avoids handing the browser an iframe URL that responds with connection-refused |
| No free port in ttyd range | Log clearly; refuse to spawn session; return 503 |
| `tools/new-vault.sh` fails (path collision, permission, etc.) | `POST /api/vaults/scaffold` returns 400 with `stderr` in the body; the SPA wizard shows the message and lets the user fix and retry without losing other field values |
| Stale Python venv (broken pip shebang after copy across hosts) | `deps.sh` / `run.sh` detect via `--vname`-aware probe; for the default `.venv` path the venv is recreated automatically; for a user-supplied `--vname` path the script refuses to delete and asks the user to fix or repoint |
| `TmuxManager.create_session()` fails | Raise `TmuxSessionError`; emit `session_error` SocketIO event with human-readable reason |
| Task execution non-zero exit | Write `failed` event; capture stderr in task log; vault dot goes red |
| Vault path missing at dispatch time | Write `failed` event immediately with reason "vault path not found" |
| Config save fails validation | Return HTTP 400 with specific error message; never write the file |
| Config save fails on disk write | Return HTTP 500 with detail; never swallow silently |
| Cron string invalid at load | Reject; surface in startup report and browser banner; scheduler does not start |
| `budget.json` missing | Create with `window_state: between`; log info; continue |
| `budget.json` corrupt / invalid JSON | Reset to `window_state: between`; log warning; continue; never crash |
| `window_ends_at` in past at startup | Treat as `between`; show "Window overrun by Xh — end it?" in status bar |
| claude-obsidian version out of range | Log warning; show dismissable browser banner; do not block operations |
| ObsidianPush write fails | Log warning; continue; next 60s tick retries |

## Hard Failure vs Graceful Degradation

**Hard failure (server refuses to start):**
- tmux not installed
- `system.yaml` missing or invalid YAML

**Graceful degradation (server starts, feature disabled or warning shown):**
- ttyd not installed → terminal endpoints return 503; everything else works
- `tasks.jsonl` has bad lines → skip and continue; report count
- claude-obsidian version mismatch → banner only; operations not blocked
- `budget.json` corrupt → reset to safe state; log warning

## JSONL Crash Consistency

- Every line read through `try/except JSONDecodeError`; bad lines logged with byte offset
- Partial final line (no trailing `\n`): truncated on startup with warning
- Tasks that were `started` at crash and have no subsequent terminal event: replayed as `interrupted`
- `dispatch_started` / `child_created` count mismatch: surfaced as a startup warning, not an error
- Empty file and blank lines tolerated without raising; blank lines are not counted as `bad_lines`

These behaviors are exercised by the Phase-6 crash-recovery tests in
`tests/test_task_manager.py` (corrupt-line skip, partial-final-line truncate,
running-at-crash → interrupted, empty-file replay, blank-line tolerance).

## Key Decisions

- **Structured startup report** — every component explicitly checked and reported; no silent omissions
- **`interrupted` state** — tasks at crash are surfaced (not failed, not auto-retried); user decides
- **Budget.json never crashes server** — it is a best-effort state cache; any corruption resets safely
- **ObsidianPush failures are non-fatal** — a write failure does not cascade; subsequent ticks retry
- **Hard fail on tmux, not ttyd** — tmux is required for all task execution; ttyd is only for browser terminals

## Constraints

- The server must never silently start with broken required state (tmux, system.yaml)
- `budget.json` corruption must never raise an unhandled exception
- Every error scenario in the table above must produce a log entry
- ObsidianPush must never propagate an OSError outside the write call

## Open Questions

- None
