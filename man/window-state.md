# Window state

resman has **two** related but distinct notions of a "window":

1. The **manual gate** — a single active/inactive flag that decides whether
   scheduled cron tasks are allowed to fire.
2. The **window schedule** — a cld20-style model of recurring daily work
   windows and a weekly cycle. It drives the footer usage meters, the task
   **When** picker, and the optional **openers / collectors** that anchor
   Claude's rolling window and sample usage. You configure it from the **⊞
   Windows** tab.

## The manual gate

Tasks (especially scheduled cron tasks) are gated on whether the gate is
*active*.

```
between/ended  →  active   (start window)
active         →  between  (end window / window time expired)
active         →  overrun  (ran past the configured end time)
overrun        →  between  (end window)
```

The gate state is persisted to `config/budget.json`. Corrupt files recover to a
safe `between` state with no error, so the panel always boots. `is_window_active()`
is checked live on every cron tick: the state must be `active` **and** the
window must not have ended.

### Setting the gate

The old status-bar **sync** menu (Start/End window now, Start/End weekly) was
retired when window management moved to the ⊞ Windows tab. The gate is now set
programmatically via `POST /api/window` with an `action` of `start`, `end`,
`start_weekly`, or `end_weekly` — handy from cron or the
[remote agent](remote-agent.md). `resman.yaml`'s `window_budget` section still
holds the weekly anchors used to display the current period:

```yaml
window_budget:
  weekly_start: "Monday 09:00"
  weekly_end:   "Sunday 23:00"
```

### Why "skip rather than queue"?

Scheduled cron tasks **skip** when the gate is inactive — they do not queue up
and fire later. resman runs interactively under your supervision; queuing would
mean a flood of tasks fires the moment you start a new window. Skipping makes
the schedule respect your work cadence. (Manually-created tasks are different —
they sit in `pending` until the window opens.)

### Overrun

If the configured weekly end has passed but the gate is still active, the status
bar turns red. resman never auto-ends the gate — overrun is purely
informational, telling you your schedule isn't matching reality.

## Window schedule — the ⊞ Windows tab

Open the **⊞ Windows** tab from the top menu (between **Config** and **Help**).
It is a normal full view — no longer a popup — laid out as three cards.

### Settings (left card)

General knobs for the whole schedule; each field has a compact input and an
inline explanation:

- **Window length (hours)** — how long each window lasts. Claude's rolling
  window is 5 hours (the default).
- **Operator hour offset** — display-only; the hours your local time leads the
  server clock, for when your account's windows don't line up with the server.
- **Weekly anchor** — the weekday + hour the weekly cycle resets.
- **Collection rate** — how many usage reads to take inside each *collecting*
  window. `0` is off; the max is 12. Reads are evenly spaced through the window,
  the last one ~5 minutes before it closes.
- **Status refresh (minutes)** — how often the footer bars redraw from cached
  state (no claude.ai call).
- **Limit sync (minutes)** — how often resman pulls fresh session/weekly limits
  from claude.ai.

### Daily windows (right card)

The list of windows plus their per-window automation **marks**. The default is
five 5-hour windows at 00:00, 05:00, 10:00, 15:00, 20:00 (server-local). Add or
remove windows freely; each row has:

- **start** — the start hour (0–23, server-local).
- **night 🌙** — flags a night window (shown in the task **When** picker).
- **open** — tick to have resman *anchor* that window: at its start it runs
  `claude -p "hi"` so Claude's rolling 5-hour window begins on schedule, then
  records a ~0 % reading.
- **collect** — tick to take usage reads during that window (at the **Collection
  rate** set on the left).

Both marks default **off** on every window, so resman spends **no tokens** until
you opt a window in. The card also shows a **Recent window log**.

> Either card's **Save configuration** button saves *everything* — all settings
> and all window marks. The two buttons are equivalent; use whichever is in
> front of you.

### Usage statistics (wide card)

A history of stored usage readings, drawn as two hand-rolled SVG charts —
**Session %** and **Weekly %** over the selected range (**7d / 30d / 90d**):

- **Collect now** takes one reading immediately and stores it (read-only; spends
  no tokens).
- **Clear** wipes the stored readings.

Readings carry their source — **opener** (the anchor, ~0 % at window start),
**auto** (scheduled collectors), or **manual** (Collect now) — and are stored
durably in `config/window_samples.jsonl`, self-pruned to 90 days / 5000 rows.

## Openers and collectors (automation)

Saving a schedule with any **open** or **collect** marks registers in-process
APScheduler jobs — no system crontab, no sudo:

- **Opener** — for each **open** window, a job at the window's start runs
  `claude -p "hi"` to anchor Claude's rolling window, then records a ~0 %
  reading.
- **Collector** — for each **collect** window (when **Collection rate** > 0),
  *rate* jobs spaced through the window each fetch current usage and store it.

The usage *reads* are read-only and spend no tokens; the only token cost is the
opener's `claude -p "hi"`, and only for windows you tick **open**. The
stale-token wakeup can be turned off with `RESMAN_USAGE_WAKEUP=0`, in which case
openers skip and log that they were skipped.

Every opener/collector action — successes, at-limit warnings, and fetch errors
alike — is written to the **Activity log** (footer **📋 Log**). See
[Activity log](activity-log.md).

## Footer meters (on-demand current state)

The **footer** shows two **usage meters** and a **⟳ sync** button — unchanged by
the schedule work:

- **Window** (green) and **Week** (blue) — each bar fills with how much *time*
  has elapsed in the current window / weekly cycle, with that % drawn **inside**
  the bar.
- The number **after** each bar is your **limit used** — the session (5-hour)
  and weekly (7-day) utilization fetched live from claude.ai (the same numbers
  as the official usage view). It shows `?` when you're logged out or it can't
  reach claude.ai; hover for the reset time.
- **⟳ sync** fetches the latest limits on demand (it also refreshes on load and
  every few minutes). The fetch is read-only and spends no tokens.

## Spinning a task for a window

In the Tasks tab, the **When** picker lists the upcoming windows (night windows
are tagged 🌙). Pick the window you want the task to run in — it's scheduled for
that window's start — or leave it on **run now**.

## Files

- `config/budget.json` — the manual gate state.
- `config/window_schedule.json` — windows, marks, and schedule settings
  (corrupt files reset to defaults).
- `config/window_samples.jsonl` — stored usage readings (a gitignored runtime
  artifact).

See `docs/design/13-window-schedule.md` for the full model.
