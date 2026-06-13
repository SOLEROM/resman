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

## Window schedule (daily / weekly windows)

Beyond the manual active/inactive gate, resman models a **schedule** of daily
work windows and a weekly cycle, inspired by the cld20 window manager. This is
informational + drives scheduling — it does not override the manual gate.

Open it from the **⊞ Windows** button in the top bar:

- **Daily windows** — a list of windows, each with a start hour (server-local)
  and an optional **night 🌙** flag. The default is five 5-hour windows at
  00:00, 05:00, 10:00, 15:00, 20:00. Add or remove windows freely.
- **Window length** — how many hours each window lasts (default 5).
- **Weekly anchor** — the weekday + hour the weekly cycle resets.
- **Operator hour offset** — a display-only offset if your account's windows
  don't line up with the server clock.
- **Checks & status** — the live current window, next window, next night
  window, and weekly-cycle progress.
- **Manual gate** — the active/between/ended state plus **Start window now**,
  **End window now**, **Start weekly**, **End weekly** controls. (These used to
  live in the footer's "sync" menu; they moved here.)
- **Recent window log** — recent window state changes.

The **footer** shows two **usage meters** and a **⟳ sync** button:

- **Window** (green) and **Week** (blue) — the bar fills with how much *time* has
  elapsed in the current window / weekly cycle, and that % is drawn **inside**
  the bar.
- The number **after** each bar is your **limit used** — the session (5-hour)
  and weekly (7-day) utilization fetched live from claude.ai (the same numbers
  as the official usage view). It shows `?` when you're logged out or it can't
  reach claude.ai; hover for the reset time.
- **⟳ sync** fetches the latest limits on demand (it also refreshes on load and
  every few minutes). The fetch is read-only and spends no tokens.

(The old "Window: …" label and the manual-gate sync menu were removed — those
gate controls now live in the ⊞ Windows modal.)

### Spinning a task for a window

In the Tasks tab, the **When** picker lists the upcoming windows (night windows
are tagged 🌙). Pick the window you want the task to run in — it's scheduled for
that window's start — or leave it on **run now**.

The schedule is saved to `config/window_schedule.json` (corrupt files reset to
defaults). See `docs/design/13-window-schedule.md` for the full model.
