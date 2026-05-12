# Window state

The **window** is the global Claude-Code work window. Tasks (especially
scheduled cron tasks) are gated on whether the window is *active*.

## States

```
inactive  →  active   (start window now)
active    →  inactive (end window now / window time expired)
active    →  overrun  (you ran past the configured end time)
overrun   →  inactive (end window now)
```

Show in the bottom status bar:

- **Window: active** — green dot, work is allowed
- **Window: inactive** — gray dot, scheduled work skips
- **Window: overrun** — red dot + an "End window now" button appears

## Configuring the window budget

`resman.yaml` has a `window_budget` section:

```yaml
window_budget:
  weekly_start: "Monday 09:00"
  weekly_end:   "Sunday 23:00"
```

These are weekly anchors used to display the current period in the UI. The
**actual** active/inactive state is controlled by the **sync** menu in the
status bar:

- **Start window now** — set state to active
- **End window now** — set state to inactive
- **Start weekly period** — reset weekly counters
- **End weekly period** — close out the weekly period

## Persistence

Window state is persisted to `config/budget.json`. Corrupt files are
recovered to "inactive" with no error so the panel always boots.

## Why "skip rather than queue"?

Scheduled tasks (cron) **skip** when the window is inactive — they do not
queue up and fire later. The reason is that resman runs interactively under
your supervision; queuing means a flood of tasks fires the moment you start a
new window. Skipping makes the schedule respect your work cadence.

Manually-created tasks are different — they sit in `pending` until the
window opens.

## Overrun behaviour

If the configured weekly end has passed but the window is still active, the
status bar turns red and the "End window now" button appears. resman doesn't
auto-end the window — overrun is informational, but it tells you that your
schedule isn't matching reality.
