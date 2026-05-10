# Window State

## Overview

The window state models whether a Claude Code window is currently active and usable.
It is managed exclusively by the user via the window status bar — resman never sets
state implicitly. `is_window_active()` is a function, not a cached field: it compares
`datetime.utcnow()` against `window_ends_at` on every call so there is no lag on the
gate check. State is persisted to `budget.json`. When the window activates, `WindowState`
emits `window_activated` on the EventBus; `TaskManager` subscribes and promotes deferred tasks.
This decoupling eliminates the circular import between the two modules.

## Window States

| State | Meaning | Task execution |
|-------|---------|----------------|
| `active` | Claude window open and usable | Tasks run normally |
| `between` | Previous window ended; next not started | New tasks queued as `deferred` |
| `ended` | Weekly period has ended | New tasks queued as `deferred` |

## Transition Rules

| Transition | Trigger |
|-----------|---------|
| `active → between` | User clicks "End window now" OR `window_ends_at` is reached |
| `between → active` | User explicitly clicks "Start window now" and enters a duration — **only** path to `active` |
| `active → ended` | User clicks "End weekly period" |
| `ended → active` | User clicks "Start weekly period" then "Start window now" |

**There is no implicit transition to `active`.** Creating a task or opening a session does not activate the window.

## Sync Controls (Window Status Bar)

| Control | Action |
|---------|--------|
| **Start window now** | Prompts for duration (required; 1–12 hours); sets `state=active`, `window_started_at=now`, `window_ends_at=now+duration` |
| **End window now** | Sets `state=between`, `window_ends_at=now` |
| **Start weekly period** | Sets `weekly_synced_at=now`, `weekly_ends_at=now+period` |
| **End weekly period** | Sets `state=ended` |

Duration is required — no open-ended windows. Maximum 12 hours.

## is_window_active()

```
is_window_active() := window_state == "active" AND datetime.utcnow() < window_ends_at
```

This is computed inline on every call. There is no cached boolean that can go stale.
The 60-second server poll emits SocketIO `window_state_changed` events to update the browser
status bar — it never sets authoritative state.

## Window Overrun

If `window_ends_at` is in the past at startup (e.g., server was stopped then restarted),
the status bar shows: "Window overrun by Xh — end it?" as a persistent prompt. The user
must explicitly end the window. The system does not auto-transition.

## Tmux Heuristic Fallback

If `window_state == "between"` and a tmux session matching `rsm-*-claude` is found alive
on the resman socket, an amber indicator appears: "Claude session detected — did you forget
to start the window?" This is informational only — it does not change `window_state`.
The user can click "Start window now" to act on it.

## Task Gating

- **While `active`:** tasks run normally
- **While `between` or `ended`:** new tasks are queued with state `deferred`
- **On `window_activated` (EventBus):**
  - `high` and `medium` priority deferred tasks are promoted to `pending` and begin running
  - `low` priority tasks remain `deferred` until the user manually promotes them

## EventBus Decoupling

`WindowState` never imports `TaskManager`. The coupling flows only via the EventBus:
- `WindowState` → emits `window_activated` on state flip to `active`
- `TaskManager` → subscribes to `window_activated` and runs the promotion logic

On window state flip in either direction, emit `window_state_changed` SocketIO event
to all connected browser clients.

## Key Decisions

- **`is_window_active()` is a function** — prevents stale gate state; no 60-second lag
- **Duration required** — prevents accidental open-ended windows; max 12 hours
- **EventBus decoupling** — `WindowState` ↔ `TaskManager` coupling would be circular; EventBus resolves it
- **budget.json write order** — file written first, then in-memory state updated; ensures persistence even if process crashes mid-update
- **Tmux heuristic is informational** — never changes authoritative state; avoids false automatic window activations

## Constraints

- Window state must never be set except via explicit user action through the sync controls
- `is_window_active()` must not cache its result
- The 60s poll must only emit SocketIO events; it must not write to `budget.json`
- Duration on "Start window now" is mandatory; missing duration must be rejected with a clear UI error

## Open Questions

- None
