# Window Schedule (cld20 model)

## Overview

`WindowSchedule` (`modules/window_schedule.py`) is an **additive layer** over the
manual [`WindowState`](07-window-state.md) gate. It ports the *concept* of the
garage `cld20` window manager: the day is tiled into fixed-length "windows"
aligned to Claude's session windows, and a weekly cycle is anchored to a chosen
weekday/hour. It drives the footer schedule line, the top-bar **⊞ Windows**
configuration modal, the checks/log view, and night-window task scheduling.

`WindowState` still owns the authoritative active/between/ended gate that defers
tasks; `WindowSchedule` adds *when* — current window, next window, weekly
progress, and the next night window. The two are decoupled: `WindowSchedule`
subscribes to `window_state_changed` only to append to its event log.

The live **usage limits** (session + weekly utilization) are pulled on demand
from claude.ai — see [Usage limits](#usage-limits) below.

> **Out of scope (deliberately):** only cld20's *heavy* usage-sampling pipeline
> (cron → `usage.sh` → JSONL → historical charts) stays out — it is tied to
> garage's install infra (sudo wrappers, bun, fixed paths). The single read-only
> `GET /api/oauth/usage` call that yields the current session/weekly percentages
> **is** ported (`modules/claude_usage.py`); we just don't persist a time series.

## Data model

Persisted to `config/window_schedule.json` (atomic write, corruption-safe — bad
files fall back to defaults):

```json
{
  "windows": [
    { "server_start": 0,  "night_window": false },
    { "server_start": 5,  "night_window": false },
    { "server_start": 10, "night_window": false },
    { "server_start": 15, "night_window": false },
    { "server_start": 20, "night_window": false }
  ],
  "weekly_anchor": { "weekday": 0, "hour": 0 },
  "operator_hour_offset": 0,
  "window_length_hours": 5
}
```

| Field | Meaning | Default | Constraints |
|-------|---------|---------|-------------|
| `windows[].server_start` | Hour (server-local) a window begins | `[0,5,10,15,20]` | 0–23, unique, ≤ 12 windows |
| `windows[].night_window` | Designate this window for overnight work | `false` | boolean |
| `weekly_anchor.weekday` | Day the weekly cycle resets | `0` (Monday) | 0 (Mon) … 6 (Sun) |
| `weekly_anchor.hour` | Hour the weekly cycle resets | `0` | 0–23 |
| `operator_hour_offset` | Display-only offset from server clock | `0` | −12 … +14 |
| `window_length_hours` | Length of each window | `5` | 1–24 |

Times are interpreted in the **server's local time** (the hours a user thinks in).

## Derivation

`status(now)` returns the live view, all timestamps naive-local ISO:

- `current` — the window containing `now` (or `null` between windows).
- `next` — the next window to start.
- `next_night` — the next window flagged `night_window`.
- `upcoming` — the next up-to-8 windows (drives the task **When** picker).
- `weekly` — `{ start, end, fraction, seconds_remaining, weekday_name, hour }`.
- `usage` — `{ window_limit_pct, weekly_limit_pct, session_resets_at,
  weekly_resets_at, synced_at, reason }`. The two `*_pct` fields are the live
  session/weekly utilization (populated by `sync()`); `null` until the first
  sync or on an auth/fetch failure (the footer renders `null` as `?`). `reason`
  is `ok`/`auth_error`/`fetch_error`; `synced_at` is the last sync time.

Each window entry carries `index`/`count`, `server_start`, `night`, `start`,
`end`, `seconds_until_start` / `seconds_until_end` countdowns, and `fraction`
(0–1, the share of the window's clock elapsed at `now` — drives the footer's
green window meter). Windows are generated for the surrounding ~week so
cross-midnight windows and the upcoming list resolve.

## API

```
GET  /api/window/schedule      → { windows, weekly_anchor, operator_hour_offset,
                                    window_length_hours, status, log, weekday_names }
PUT  /api/window/schedule      → validate + persist + emit; returns the same shape
GET  /api/window/next-night    → { at: ISO | null }   (next night-window start)
POST /api/window/sync          → same shape as GET; fetches live usage limits,
                                  stamps usage.synced_at, logs the sync (⟳ button)
```

`PUT` accepts any subset of `windows`, `weekly_anchor`, `operator_hour_offset`,
`window_length_hours`; invalid values return HTTP 400 with a message. CSRF
(`X-Requested-With: resman`) is required on `PUT` and `POST /api/window/sync`.

## Task integration

The task trigger's **When** field is a dropdown built from `status.upcoming`:
"run now" plus each upcoming window (night windows tagged 🌙). Selecting one sets
`scheduled_for` to that window's start (converted local→UTC), so the task runs at
the start of the chosen window via the existing scheduling path.
`GET /api/window/next-night` remains for scripted/CLI use.

## Footer + top bar

- **Footer** shows two side-by-side **usage meters** plus a **⟳ sync** button:
  - **Window** meter (green) and **Week** meter (blue) — the bar fill **and the
    number inside the bar** are the share of *time* elapsed (the window's
    `status.current.fraction` / the cycle's `status.weekly.fraction`).
  - The number printed **after** each bar is the *limit used* — the session
    (`usage.window_limit_pct`) and weekly (`usage.weekly_limit_pct`)
    utilization, the headline figure (bolded). It reads `?` until synced or on
    an auth/fetch error; the reset time + reason are in the tooltip.
  - **⟳ sync** posts `/api/window/sync` to fetch the live limits on demand
    (stamps `synced_at`, logs the sync); the client also auto-syncs once on
    load and re-pulls every 10 min. Falls back to a plain reload if the POST
    fails.
  - The old "Window: …" label and the manual-gate "sync" menu are gone — the
    gate controls live in the ⊞ Windows modal.
- **Top bar** has a **⊞ Windows** button opening the management modal: editable
  windows (start hour + night flag, add/remove), weekly anchor, offset, length,
  a live **Checks & status** panel, a **Manual gate** section (active/between
  state + Start/End window + Start/End weekly — relocated from the footer), and
  a recent **window log**.

## Usage limits

`modules/claude_usage.py` ports cld20's read-only usage probe. `fetch_usage()`
reads the operator's OAuth token from `~/.claude/.credentials.json`
(`claudeAiOauth.accessToken`; override the path with `CLD20_CREDS_PATH`) and
GETs `https://claude.ai/api/oauth/usage`, returning:

- `session_pct` ← `five_hour.utilization` (the rolling 5-hour window)
- `weekly_pct`  ← `seven_day.utilization` (the rolling 7-day window)
- `*_resets_at`, plus a classified `reason` (`ok`/`auth_error`/`fetch_error`)

The call is **read-only and spends no tokens**. It is never fatal — a logged-out
account (401/403) or network failure leaves the percentages `null` (`?` in the
UI). `WindowSchedule` is given `fetch_usage` as its `usage_provider`; `sync()`
calls it and caches the result (`status.usage`).

> **A real `User-Agent` is required:** claude.ai's edge returns 403 to the
> default `Python-urllib/*` (and to `curl`'s TLS fingerprint), so the module
> sends `claude-cli/<ver>`. The shell tool below therefore fetches via this same
> Python module rather than curl.

## Verifying from the shell

`tools/window-status.sh` recomputes the current/next window and weekly-cycle
progress the *same way* `status()` does — reading the same
`config/window_schedule.json` — and fetches the session/weekly limits through
the *same* `claude_usage` module the server uses, so the footer meters can be
checked on demand without the server running:

```
tools/window-status.sh                          # now, default config, live limits
tools/window-status.sh --now "2026-06-12 22:30" # pretend it's this time
tools/window-status.sh --no-fetch               # skip the claude.ai call
tools/window-status.sh --config-dir /tmp/resman-smoke
```

It prints `time` (the bar fill / inside %) for the window and week, and the live
session/weekly `limit` (or `?` when logged out / unreachable). Cross-checked
byte-for-byte against the Python derivation and the server's usage fetch.

## Key decisions

- **Additive, not a rewrite** — `WindowState` and all its tests are untouched;
  the schedule is a separate module + file + endpoints.
- **Server-local time** — windows are wall-clock hours; countdowns are computed
  server-side and refetched every 30 s so browser/server timezone never skews.
- **Corruption-safe** — an unreadable `window_schedule.json` resets to defaults
  rather than crashing the panel (same posture as `budget.json`).
