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
| One-shot scheduled tasks | EventBus `task_scheduled` event (i.e., a task created with `scheduled_for`) | Always (window-gating not applied to one-shots) | Single `DateTrigger` per task |
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

## One-Shot Scheduled Tasks

A task created via `POST /api/tasks` with `scheduled_for: <future-ISO>` lands
directly in `scheduled` state (see `06-task-management.md`). The TaskManager
emits a `task_scheduled` event on the bus carrying `{task_id, scheduled_for}`.
The Scheduler subscribes to this event and registers a one-shot
`DateTrigger` keyed `task::<task_id>`:

- Fire time: `scheduled_for` parsed as UTC.
- Callback: `task_manager.promote(task_id)`, which transitions
  `scheduled → pending` and dispatches through the existing path.
- Job is removed automatically once fired.

The Scheduler also subscribes to `task_updated`: if a `scheduled` task
transitions to anything other than `scheduled` (the user cancelled it, or
clicked `run-now`), the pending one-shot job is removed so it can't fire a
second time. The map `_one_shot_jobs: Dict[task_id, job_id]` tracks live
registrations.

At startup, `start()` walks the in-memory task index for tasks already in
`scheduled` state (from replay) and re-arms their triggers. Tasks whose
`scheduled_for` is in the past at that moment are not auto-promoted —
replay surfaces them as **overdue** warnings, and the UI shows a `run-now`
button on the card so the user explicitly chooses to fire or cancel.

One-shot scheduling and `vault: ALL` are mutually exclusive in v1; the
combination is rejected at the API boundary.

## Key Decisions

- **`GeventScheduler` required** — `BackgroundScheduler` deadlocks with eventlet subprocess
- **Cron tasks skip, not defer** — no deferred-cron complexity; next scheduled occurrence is the retry; skips are visible in the event log
- **One-shot scheduled tasks live in APScheduler, not in a separate timer thread** — same scheduler instance handles cron + one-shot; restart re-arms both via replay
- **One-shot triggers re-armed via bus subscription** — TaskManager doesn't import Scheduler. The decoupling pays off here: `scheduled_for` paths just emit `task_scheduled` and the Scheduler reacts.
- **ObsidianPush is a separate job** — not gated on window state; always runs
- **Cron strings validated before APScheduler** — invalid strings surface at load time, not at first fire
- **Dispatch goes through TaskManager** — cron tasks and one-shot scheduled tasks appear in the task list, get log files, and participate in the same state machine as manual tasks

## Constraints

- Must use `GeventScheduler`; `BackgroundScheduler` is prohibited
- Cron strings must be validated with `CronTrigger.from_crontab()` before being given to APScheduler
- Cron task callbacks must acquire the `TaskManager` dispatch lock
- ObsidianPush must fire regardless of window state
- `cron_skipped` event must include `scheduled_at` timestamp

## Open Questions

- **skip_count reset** — it is not explicitly stated whether `skip_count` resets to 0 on a successful fire (most natural behavior) or accumulates indefinitely. Treat as: reset to 0 on each successful dispatch.
