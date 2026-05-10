# Scheduler

resman ships an APScheduler `GeventScheduler` (cooperative with eventlet) that
runs cron-style tasks defined in `config/schedule.yaml`. It also runs the
**ObsidianPush** job — a 60-second loop that writes `_resman/status.md` into
each vault so the panel's state shows up in Obsidian's graph view.

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

When a cron fires while the [window is inactive](window-state.md), resman:

1. Increments a per-cron `skip_count`.
2. Records the last attempted fire time.
3. Emits a `cron_skipped` Socket.IO event so the **Tasks** tab can show the
   banner.

The task is **not** queued — it just skips this fire.

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

## Skip-count threshold (open question)

Whether `skip_count` resets to zero on a successful fire is an open question
in the design (see `design/08-scheduler.md`). Today: it does not reset.

## Disabling the scheduler

For development:

```bash
./run.sh --no-scheduler
```

The cron tasks defined in `schedule.yaml` won't fire, and ObsidianPush won't
push. Manually-created tasks are unaffected.
