# Task Management

## Overview

`task_manager.py` implements a prioritized task queue backed by an append-only JSONL
event log (`config/tasks.jsonl`). Tasks represent plugin operations or shell commands
to run against a vault. The system uses event sourcing: every state change is an
appended event; current state is derived by replaying. An in-memory index is built
at startup and maintained incrementally. A dispatch mutex prevents concurrent dispatch
races. ALL-vault tasks spawn per-vault child tasks and aggregate their state via the EventBus.

## Task Data Model

| Field | Values |
|-------|--------|
| `id` | `t-<uuid4>` |
| `vault` | vault name or `ALL` |
| `operation` | `wiki-ingest`, `wiki-lint`, `wiki-autoresearch`, `wiki-update-hot-cache`, `wiki-bootstrap`, `run-prompt`, `run-shell` |
| `priority` | `high` / `medium` / `low` |
| `schedule` | `immediate`, `background`, `deferred` |
| `parent_id` | UUID of parent task or `null` |
| `pid` | OS process PID while running, else `null` |
| `scheduled_for` | optional ISO 8601 time when the task should fire, else `null` |

Operation namespace: all plugin operations use the `wiki-` prefix; ad-hoc execution uses `run-prompt` or `run-shell`.

## JSONL Event Log

One event per line in `config/tasks.jsonl`:

| Event | When |
|-------|------|
| `created` | Task queued; payload contains all task fields |
| `started` | Execution begins; includes `pid` so replay can detect liveness |
| `completed` | Finished successfully (includes `exit_code: 0`) |
| `failed` | Finished with error |
| `interrupted` | Was `running` at server crash; detected on replay (only when PID is gone) |
| `deferred` | Moved to deferred queue (window not active) |
| `scheduled` | Task parked for a future `scheduled_for` ISO timestamp |
| `promoted` | Deferred or scheduled task promoted to pending (manual click, window activation, or one-shot trigger fire) |
| `updated` | Params or priority changed |
| `child_created` | Parent task created a child task for one vault |
| `dispatch_started` | Parent ALL-vault task about to dispatch; includes `expected_child_count` |
| `cron_skipped` | Cron tick fired but window inactive; includes `scheduled_at` |
| `archived` | Soft-deleted from default UI view |
| `cancelled` | Pending / deferred / scheduled / **running** task cancelled by user |

Crash-consistency: each line read through `try/except JSONDecodeError`; bad lines are logged with byte offset and skipped. If `tasks.jsonl` does not end with `\n` at startup, the partial final line is truncated with a warning.

## State Machine

```
                 ┌──────────────────────────► cancelled  ◄─── user cancels
                 │                              ▲ (running)
                 │                              │
pending ─────────┼───► running ──► completed ──► [archived]
   ▲             │       │
   │ (promoted)  │       └──────► failed       ──► [archived]
   │             │       │
deferred◄────────┘       └──────► interrupted   (replay: PID gone)
   ▲
   │ (one-shot
scheduled ──┘    one-shot fire / manual promote)
```

`interrupted` is a terminal state surfaced to the user; it is not retried automatically.
`scheduled` is parked until a `DateTrigger` registered by `scheduler.py` fires —
the trigger calls `promote(task_id)` which routes through the existing
pending → running path. `cancelled` is reachable from `pending`, `deferred`,
`scheduled`, **or** `running`. Cancelling a running task terminates the
subprocess (`SIGTERM`, then `SIGKILL` after a 5 s grace).

## Priority and Window Gating

| Priority | Window active | Window between/ended |
|----------|---------------|----------------------|
| `high` | runs immediately | deferred; promoted on next window activation |
| `medium` | runs as background | deferred; promoted on next window activation |
| `low` | runs as background | deferred; user must manually promote |

`scheduled_for` overrides the priority/window-gating routing: a task created
with `scheduled_for=<future-ISO>` lands directly in `scheduled` state and is
ignored by `_on_window_activated`. It waits for the Scheduler's one-shot
trigger. ALL-vault parents may not be scheduled in v1.

## Live Log Streaming

The production runner uses `subprocess.Popen` with a **pseudo-terminal**
(`pty.openpty()`) as the child's stdout/stderr — not a pipe. This is the
critical detail: most CLIs (claude-code among them) switch to block buffering
when libc detects stdout is a pipe, so a pipe-based runner would sit silent
for the entire task and only flush at exit. With a PTY the child sees a real
TTY, line-buffers normally, and we get usable live output.

A dedicated reader thread does the blocking `os.read()` on the PTY master so
the streaming runner works identically under eventlet (production) and under
threading (tests) without depending on eventlet's monkey-patching of
file-descriptor I/O. If `pty.openpty()` fails (rare; e.g., a container with
no `/dev/ptmx`), the runner falls back to a pipe with a warning — the task
still runs, but live tailing degrades for block-buffered children.

Each chunk read from the PTY is written to `config/task-logs/<task_id>.log`
and emitted on the EventBus as a `task_log_appended` event:

```json
{ "task_id": "t-abc123", "chunk": "compiling page index...\n" }
```

`websocket_handlers.py` re-broadcasts the event over Socket.IO so the SPA's
Tasks tab can tail a running task without polling. Output is capped at
`LOG_MAX_BYTES` (5 MB) per task; once reached the runner writes a single
`... [output capped at N bytes; tail discarded]\n` marker and discards
further output. Subprocess return code and the JSONL `completed`/`failed`
event are unaffected by truncation.

Cancellation reaches the live process via a `_procs: Dict[task_id, Popen]`
map maintained by the runner. `cancel()` calls `proc.terminate()`, waits up
to 5 s, then `proc.kill()` if the child is still alive. A `cancelled` event
is written; the streaming finalization path checks for `task.state ==
"cancelled"` before writing `completed`/`failed` so cancellation always
wins the race.

## Async Dispatch

The streaming runner blocks the calling greenlet/thread for the full task
duration (it's reading the PTY until EOF). Without async dispatch, the
`POST /api/tasks` request handler would block too. `server.py` wires
`task_manager.set_executor(eventlet.spawn)` after replay so each task runs
in its own greenlet:

- `POST /api/tasks` returns immediately with `state: pending` (or
  `running`/`scheduled`/`deferred`).
- Live log chunks reach the browser via the Socket.IO passthrough.
- `DELETE /api/tasks/{id}` runs in a separate greenlet and reaches the
  live `Popen` handle via `_procs[task_id]`.

Tests inject a `threading.Thread`-based executor for the same async
semantics without eventlet. The legacy 3-arg runner injection
(`runner(cmd, cwd, log_file) -> int`) still runs synchronously — used by
tests that don't need a live process.

## PID-Aware Replay

The `started` event includes the PID. At replay, tasks still in `running`
state are checked with `os.kill(pid, 0)`:

- Process alive → task stays `running` (control-plane restarted but the
  subprocess survived).
- Process gone or PID missing → task flips to `interrupted` with a warning.

This avoids the v0 behavior of unconditionally interrupting any
crash-recovered `running` task.

A `scheduled` task whose `scheduled_for` is already past at replay time
keeps its `scheduled` state and surfaces an "overdue" warning. The Tasks UI
shows an overdue badge on the card; the user clicks `run-now` to promote, or
`cancel` to abandon.

## ALL-Vaults Tasks (Parent/Child)

1. Parent task created with `vault: ALL`
2. Write `dispatch_started` event with `expected_child_count: N` (before any children created)
3. Under dispatch lock (`eventlet.semaphore.Semaphore(1)`): create one child per registered vault; each writes `child_created` event
4. On server crash mid-dispatch: startup integrity check detects mismatch between `expected_child_count` and actual child count; surfaces warning in startup report
5. Children run independently (separate tmux sessions, separate log files)
6. Parent state aggregated via EventBus: when a child emits `completed` or `failed`, parent re-aggregates:
   - `running` if any child running
   - `failed` if any child failed
   - `completed` only when all children completed successfully

## Operation-to-Execution Mapping

All commands are **argument lists** passed to `subprocess.run([...])` — the shell is never invoked.
Plugin command strings come exclusively from `plugin_commands.py`.

| Operation | Execution |
|-----------|-----------|
| `wiki-ingest` | `[RESMAN_ROOT/tools/ingest.sh, vault_path, params.url]` |
| `wiki-lint` | `["claude", "-p", LINT, "--dangerously-skip-permissions"]` in vault dir |
| `wiki-autoresearch` | `["claude", "-p", autoresearch(topic), "--dangerously-skip-permissions"]` in vault dir |
| `wiki-update-hot-cache` | `["claude", "-p", UPDATE_HOT_CACHE, "--dangerously-skip-permissions"]` in vault dir |
| `wiki-bootstrap` | `["claude", "-p", WIKI_BOOTSTRAP, "--dangerously-skip-permissions"]` in vault dir — non-interactive re-run only |
| `run-prompt` | `["claude", "-p", params.prompt, "--dangerously-skip-permissions"]` in vault dir |
| `run-shell` | `[params.cmd_parts[0], *params.cmd_parts[1:]]` in vault dir |

`run-shell` is privileged: requires explicit UI acknowledgment before first use.

`wiki-bootstrap` is **non-interactive** because the task runner uses `claude -p`.
That means it cannot answer prompts the bootstrap command may ask, so it is
only safe for re-runs against an already-bootstrapped vault (e.g., as a
periodic cron task to re-validate structure). First-time bootstrap must use
the **wizard path** — see `10-frontend.md` (New-Vault Wizard) and
`docs/plugin-commands.md`.

## Compaction

When `tasks.jsonl` exceeds 50,000 lines, or on explicit `POST /api/tasks/compact`:
1. Replay full log into in-memory state
2. Terminal-state tasks (`completed`, `failed`, `archived`, `interrupted`, `cancelled`) older than 90 days → write single `snapshot` event capturing final state
3. Drop all pre-snapshot events for those tasks
4. Rewrite `tasks.jsonl`: snapshot events first, then all remaining events

The Tasks panel exposes a **Compact log** button that calls the endpoint
with a confirm prompt and reports the count of compacted tasks back to
the user.

## Key Decisions

- **Event sourcing** — append-only log; in-memory index rebuilt at startup; greppable; no schema migrations
- **PTY, not pipe, for the streaming runner** — `claude -p` and most CLIs block-buffer when libc detects stdout is a pipe. A PTY makes them line-buffer, which is what live-tail demands. The pipe fallback exists only for environments without `/dev/ptmx`.
- **`scheduled` is a state, not a field** — a `scheduled_for` field on a `pending` task would let `promote()` fire early past the schedule; a discrete state forces every transition through `scheduler._fire_scheduled_task` or an explicit `run-now` click.
- **No `scheduled_for` with `vault: ALL`** — parent/child fan-out combined with one-shot scheduling adds combinatorics (when does the parent's `dispatch_started` fire? do children inherit the parent's schedule?) that aren't worth solving until someone asks. Rejected at the API boundary.
- **`interrupted` state** — tasks running at crash get `interrupted` (not `failed`, not auto-retried); user sees them and decides. Replay uses `os.kill(pid, 0)` so subprocesses that survived a control-plane restart stay `running` instead of being mislabelled.
- **Cancellation wins races** — `_finalize` checks `task.state == "cancelled"` before writing `completed`/`failed`. Without this guard, a process that exits naturally a few ms after a user cancel would overwrite the cancel.
- **5 MB log cap** — a runaway claude session can produce GB of output; tailing it over Socket.IO would OOM the browser. Cap, marker, drop tail. The subprocess return code and JSONL `completed`/`failed` event are unaffected.
- **`dispatch_started` before children** — enables crash-recovery integrity check on restart
- **Dispatch mutex** — prevents double-dispatch from concurrent user action + cron tick
- **Task re-run** — pre-fills the trigger form with original params; submitting creates a new task with a new UUID; original is never mutated. `scheduled_for` is **not** preserved on re-run.
- **Archived = soft-delete** — excluded from default view but preserved in log for audit

## Constraints

- All subprocess calls must use argument-list form; `shell=True` and `sh -c` are prohibited
- `params.url` must be validated as HTTP/HTTPS via `urllib.parse.urlparse()`
- `params.topic` and `params.prompt`: max 200 chars, printable ASCII only
- `params.cmd_parts` must be a pre-validated list; never a shell string
- Task execution output goes to `config/task-logs/<task_id>.log`
- `scheduled_for`, if provided, must be (a) a parseable ISO 8601 timestamp, (b) strictly in the future, and (c) not combined with `vault: ALL`. Violations return HTTP 400 from `POST /api/tasks`.
- The `started` event must include the OS PID; replay relies on it to differentiate live vs. dead subprocesses.

## Open Questions

- **Child task re-run** — when a parent ALL-vault task is re-run, it creates a new parent + new children with new UUIDs. What happens to the original children? They remain visible (archivable separately) but are not affected by the re-run.
