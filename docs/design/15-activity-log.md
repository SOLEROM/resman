# Activity Log (volatile)

## Overview

`ActivityLog` (`modules/activity_log.py`) is a **live view of what the server is
doing** — surfaced by the footer **📋 Log** window. It is deliberately *not* a
durable audit trail:

- It lives in RAM as a bounded ring buffer (`deque`, default 2000 entries).
- It is mirrored to a **volatile file** `/tmp/resman/activity-<pid>.log` —
  created fresh on startup, deleted on clean shutdown (`atexit`), and any
  leftover file from a hard-killed run (SIGKILL/SIGTERM skip `atexit`) is swept
  on the next startup by checking whether its PID is still alive.

So the log is "active only while the app is running", exactly as intended, and a
crash can never leave stale state behind for long.

## Where entries come from

Three channels — only the first needs any caller code:

1. **Explicit** — anything with the bus emits `"activity"` with
   `{level, source, message, detail}` (helper: `emit_activity(bus, …)`, or the
   `_activity(…)` helper in `routes.py`). Used for the ⟳ window sync, session
   spawn/kill, vault register/scaffold, task queue, and config save.
2. **Auto** — a curated set of *existing* bus events is mirrored in, so those
   modules stay untouched:

   | Event | Level | Source |
   |-------|-------|--------|
   | `task_updated` | info | task |
   | `window_state_changed` | info | window |
   | `session_crashed` / `session_error` | error | session |
   | `config_reloaded` | info | config |
   | `cron_skip_warning` | warn | cron |

3. **Errors** — `install_logging_bridge()` attaches an `ActivityLogHandler` to
   resman's `modules` / `server` logger namespaces at `WARNING`, so unexpected
   `log.warning`/`log.error`/`log.exception` calls anywhere also surface in the
   window. The handler has a re-entrancy guard so a failure logged *during*
   recording (e.g. a socket-emit error) cannot recurse.

## Entry shape

```json
{ "seq": 42, "ts": 1781293834.7, "level": "info",
  "source": "window-sync", "message": "window limit sync ok — session 8%, weekly 9%",
  "detail": null }
```

`level ∈ {debug, info, warn, error}` (anything else is coerced to `info`).
Recording appends to the buffer + file and emits `activity_logged` on the bus,
which the socket bridge (`websocket_handlers.EVENT_NAMES`) forwards to the
browser for live streaming.

## API

```
GET  /api/logs?limit=&level=&source=  → { entries: [...] }   # level = minimum level
POST /api/logs/clear                  → { ok: true }         # CSRF required
```

Socket.IO: `activity_logged` carries each new entry.

## Footer window

The **📋 Log** button sits at the footer's lower-right. An unseen `warn`/`error`
entry lights a dot on it (yellow / red) while the window is closed. The window:

- a scrollable, monospace, color-coded list (info = blue level tag; warn = amber
  left border; error = red left border + tint);
- a **level filter** (all / info+ / warn+ / errors) and an entry counter;
- a **Clear** button (`POST /api/logs/clear`);
- **live append** via the `activity_logged` socket event with sticky
  auto-scroll (only snaps to the bottom when you're already there).

The client keeps a mirror (`state.activityLog`) fed by the socket even while the
window is closed, so opening it shows recent history instantly; on open it also
pulls the authoritative recent list from `GET /api/logs`.

## Key decisions

- **Volatile by design** — RAM + `/tmp` (never in the repo). It answers "what is
  the server doing right now?", not "what happened last week?".
- **Decoupled capture** — mirroring existing bus events means new log coverage
  rarely needs touching the module being logged; emit `"activity"` for the rest.
- **Never breaks the operation being logged** — file writes, the logging bridge,
  and socket emits all swallow their own errors.
