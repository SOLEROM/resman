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

Operation namespace: all plugin operations use the `wiki-` prefix; ad-hoc execution uses `run-prompt` or `run-shell`.

## JSONL Event Log

One event per line in `config/tasks.jsonl`:

| Event | When |
|-------|------|
| `created` | Task queued; payload contains all task fields |
| `started` | Execution begins |
| `completed` | Finished successfully (includes `exit_code: 0`) |
| `failed` | Finished with error |
| `interrupted` | Was `running` at server crash; detected on replay |
| `deferred` | Moved to deferred queue (window not active) |
| `promoted` | Deferred task promoted to pending |
| `updated` | Params or priority changed |
| `child_created` | Parent task created a child task for one vault |
| `dispatch_started` | Parent ALL-vault task about to dispatch; includes `expected_child_count` |
| `cron_skipped` | Cron tick fired but window inactive; includes `scheduled_at` |
| `archived` | Soft-deleted from default UI view |
| `cancelled` | Pending/deferred task cancelled by user |

Crash-consistency: each line read through `try/except JSONDecodeError`; bad lines are logged with byte offset and skipped. If `tasks.jsonl` does not end with `\n` at startup, the partial final line is truncated with a warning.

## State Machine

```
pending ──────────► running ──► completed ──► [archived]
   ▲                   │
   │ (promoted)        └──────► failed ──────► [archived]
   │                   │
deferred◄──────────────┘ (window not active)
                        └──────► interrupted   (crash-recovery)
```

`interrupted` is a terminal state surfaced to the user; it is not retried automatically.

## Priority and Window Gating

| Priority | Window active | Window between/ended |
|----------|---------------|----------------------|
| `high` | runs immediately | deferred; promoted on next window activation |
| `medium` | runs as background | deferred; promoted on next window activation |
| `low` | runs as background | deferred; user must manually promote |

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
- **`interrupted` state** — tasks running at crash get `interrupted` (not `failed`, not auto-retried); user sees them and decides
- **`dispatch_started` before children** — enables crash-recovery integrity check on restart
- **Dispatch mutex** — prevents double-dispatch from concurrent user action + cron tick
- **Task re-run** — pre-fills creation form with original params; submitting creates a new task with a new UUID; original is never mutated
- **Archived = soft-delete** — excluded from default view but preserved in log for audit

## Constraints

- All subprocess calls must use argument-list form; `shell=True` and `sh -c` are prohibited
- `params.url` must be validated as HTTP/HTTPS via `urllib.parse.urlparse()`
- `params.topic` and `params.prompt`: max 200 chars, printable ASCII only
- `params.cmd_parts` must be a pre-validated list; never a shell string
- Task execution output goes to `config/task-logs/<task_id>.log`

## Open Questions

- **Child task re-run** — when a parent ALL-vault task is re-run, it creates a new parent + new children with new UUIDs. What happens to the original children? They remain visible (archivable separately) but are not affected by the re-run.
