# Scheduler

## Overview

`scheduler.py` uses APScheduler's `GeventScheduler` (the eventlet-compatible variant) to
run two categories of jobs: user-defined cron tasks from `schedule.yaml`, and the built-in
ObsidianPush 60-second job. Cron tasks fire only when the window is active; the push job
fires unconditionally. All cron strings are validated at load time with `CronTrigger.from_crontab()`
before APScheduler receives them, so invalid strings are caught early and surfaced in the
startup report rather than causing silent job registration failures.

## Job Categories

| Job | Source | Fire condition | Cadence |
|-----|--------|----------------|---------|
| User cron tasks | `schedule.yaml` | `is_window_active()` must be true | User-defined cron expression |
| ObsidianPush | Built-in | Always (regardless of window state) | Every 60 seconds |

## Cron Task Fire Logic

When a cron tick fires for a user task:
1. Check `is_window_active()` (function call, not cached)
2. If **active**: dispatch task through `TaskManager` (same path as manual tasks, with dispatch lock acquired)
3. If **not active**: write a `cron_skipped` event to `tasks.jsonl` with `scheduled_at` and `skip_reason`; increment `skip_count`; do not queue the task

Skipped cron tasks do not accumulate in a deferred queue — they simply wait for their next scheduled occurrence.

## Skip Tracking

Per cron task entry (tracked at runtime, not user-editable in schedule.yaml):
- `last_fired_at`: timestamp of last successful dispatch
- `skip_count`: number of consecutive skips since last fire

When `skip_count > 2`: emit SocketIO event → browser shows yellow warning badge on that cron task row: "Skipped N times (last fired: date)".

## GeventScheduler vs BackgroundScheduler

`GeventScheduler` is mandatory. `BackgroundScheduler` runs callbacks in a background thread.
When the callback calls `subprocess.Popen` (eventlet-patched), the thread blocks, causing a
deadlock between the APScheduler thread and the eventlet event loop. `GeventScheduler` runs
callbacks as eventlet greenlets, avoiding this entirely.

## Cron String Validation

At schedule.yaml load time, each cron string is validated with `CronTrigger.from_crontab()`.
If validation fails:
- Server startup: log error in startup report; show browser banner; scheduler does not start
- YAML editor save: return HTTP 400 with the parse error surfaced inline; file is not written

APScheduler must never receive an invalid cron trigger string.

## ALL-Vault Cron Tasks

Cron tasks with `vault: ALL` follow the same parent/child expansion as manual ALL-vault tasks.
See `06-task-management.md` for the parent/child model.

## Key Decisions

- **`GeventScheduler` required** — `BackgroundScheduler` deadlocks with eventlet subprocess
- **Cron tasks skip, not defer** — no deferred-cron complexity; next scheduled occurrence is the retry; skips are visible in the event log
- **ObsidianPush is a separate job** — not gated on window state; always runs
- **Cron strings validated before APScheduler** — invalid strings surface at load time, not at first fire
- **Dispatch goes through TaskManager** — cron tasks appear in the task list, get log files, and participate in the same state machine as manual tasks

## Constraints

- Must use `GeventScheduler`; `BackgroundScheduler` is prohibited
- Cron strings must be validated with `CronTrigger.from_crontab()` before being given to APScheduler
- Cron task callbacks must acquire the `TaskManager` dispatch lock
- ObsidianPush must fire regardless of window state
- `cron_skipped` event must include `scheduled_at` timestamp

## Open Questions

- **skip_count reset** — it is not explicitly stated whether `skip_count` resets to 0 on a successful fire (most natural behavior) or accumulates indefinitely. Treat as: reset to 0 on each successful dispatch.
