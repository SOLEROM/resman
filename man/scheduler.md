# Scheduler

resman ships an APScheduler `GeventScheduler` (cooperative with eventlet) that
runs four kinds of jobs:

1. **Recurring cron tasks** defined in `config/schedule.yaml`.
2. **One-shot scheduled tasks** — anything created from the **Tasks** tab
   with a `When` value set. These are individually-registered `DateTrigger`
   jobs that fire once and disappear.
3. **ObsidianPush** — a built-in 60-second loop that writes
   `_resman/status.md` into each vault so the panel's state shows up in
   Obsidian's graph view.
4. **Window openers / collectors** — `window::*` cron jobs derived from the
   per-window **open** / **collect** marks in the **⊞ Windows** tab. See
   [Window state → Openers and collectors](window-state.md).

## Defining a cron task

`schedule.yaml`:

```yaml
- name: nightly-lint
  cron: "0 23 * * *"
  vault: vla6
  operation: wiki-lint
  priority: medium
```

`cron` accepts the standard 5-field syntax. Names must be unique. Validation
runs at save time — bad strings are rejected before the file is written.

## Skip when inactive

When a recurring cron fires while the [window is inactive](window-state.md),
resman:

1. Increments a per-cron `skip_count`.
2. Records the last attempted fire time.
3. Emits a `cron_skipped` Socket.IO event so the **Tasks** tab can show the
   banner.

The task is **not** queued — it just skips this fire.

> Window-gating applies to recurring cron tasks **only**. One-shot scheduled
> tasks (set via `When` on the trigger panel) fire at their exact moment
> regardless of window state — they were chosen explicitly by the operator,
> so resman does not second-guess them.

## One-shot scheduled tasks

When you set **When** on the Tasks trigger panel, resman writes a `scheduled`
event to `tasks.jsonl`, parks the task in `scheduled` state, and registers a
single APScheduler `DateTrigger` keyed `task::<task_id>`. When the moment
arrives the trigger calls `promote(task_id)` and the task flows through the
normal dispatch path (just like clicking Run task with no `When`).

Cancelling a `scheduled` task removes its trigger immediately. If the
control-plane was offline when the trigger should have fired, the task stays
in `scheduled` state with an **overdue** badge on its card — click `run-now`
to fire it or `cancel` to abandon. resman never auto-promotes overdue
tasks; the decision is yours.

`When` and `all vaults` are mutually exclusive in v1.

## ObsidianPush

A separate, hard-coded 60-second job writes `_resman/status.md` into each
vault directory. The file shape is:

```
# resman — vault status (auto-generated)

- last update: 2026-05-10 21:42:00 UTC
- active session: yes/no
- pending tasks: N
```

The point is that Obsidian sees the file via its file watcher and folds it
into the graph view, so you can spot which vault has activity from inside
Obsidian without having the panel open.

If the vault path is missing or unwritable, the push silently no-ops for that
vault — never breaks the loop.

## Window openers and collectors

When you save the **⊞ Windows** schedule, resman re-derives a set of
`window::*` cron jobs from the per-window **open** / **collect** marks (a
`window_schedule_updated` event triggers the refresh — the old `window::*` jobs
are removed and the new set registered, so saving is idempotent):

- `window::opener::<i>` — one per **open** window, fires at the window's start
  and runs `claude -p "hi"` to anchor Claude's rolling window.
- `window::sample::<i>::<slot>` — `collection_rate` jobs per **collect** window,
  spaced through the window, each storing a usage reading.

Unlike cron tasks, these are **not** gated on the manual window state — they're
the mechanism that anchors and samples windows, so they run on their own
schedule. They never spend tokens on the reads; only the opener's
`claude -p "hi"` does. Full detail on the
[Window state](window-state.md) page.

## Skip-count threshold (open question)

Whether `skip_count` resets to zero on a successful fire is an open question
in the design (see `design/08-scheduler.md`). Today: it does not reset.

## Disabling the scheduler

For development:

```bash
./run.sh --no-scheduler
```

The cron tasks defined in `schedule.yaml` won't fire, ObsidianPush won't
push, and **one-shot scheduled tasks won't auto-fire either** — they'll sit
in `scheduled` state until you either re-enable the scheduler or click
`run-now` on the card. Manually-created (run-now) tasks are unaffected.
