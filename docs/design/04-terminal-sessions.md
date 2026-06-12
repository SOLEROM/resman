---
noteId: "ea0975704f5c11f18eaba108b9c533e7"
tags: []

---

# Terminal Sessions

## Overview

`session_manager.py` owns all browser terminal sessions. Each session is a ttyd process
bound to a specific tmux session on a specific port, embedded in the browser as an
`<iframe src="http://127.0.0.1:{port}">`. resman's only responsibilities are: spawn the
ttyd process pointing at the correct tmux session, track the port, embed the iframe,
and clean up on disconnect. All PTY management, xterm.js protocol, resize events, and
WebSocket streaming are handled by ttyd internally.

## Session Data Model

```python
@dataclass
class Session:
    id: str            # uuid4
    vault: str         # vault name
    session_type: str  # "claude" | "shell"
    tmux_session: str  # rsm-<vault>-<type>-<n>
    port: int          # ttyd port
    proc: Popen        # the ttyd process handle
    created_at: datetime
```

Registry: dict keyed by `session_id`, owned by `SessionManager`.

## Session Spawning (spawn algorithm)

1. Compute tmux session name using a **monotonic counter** per vault+type: `rsm-<vault>-<type>-<n>`
2. Ensure the tmux session exists: `tmux -S <socket> new-session -d -s <name> -c <vault_path>` (`check=False` — ok if already exists)
3. Apply per-session polish via `TmuxManager._apply_session_options()` (see below)
4. For `claude` sessions: send `cd <vault_path> && <claude_cmd>` to the tmux window via `send-keys`
5. Find a free port via `_find_free_port()`
6. Spawn ttyd: `ttyd --port <port> --writable --check-origin=false tmux -L <socket> attach-session -t <tmux_session>`
7. Block in `_wait_for_listen(port, timeout=5s)` until the ttyd process is accepting TCP connections — without this, the browser iframe races ttyd's startup and renders connection-refused
8. Register the `Session` object in the registry
9. If `initial_command` was passed (claude type only): schedule a deferred `tmux send-keys` after a short delay so the slash command lands inside the Claude REPL rather than the bash session that briefly precedes it. If `initial_text` was passed instead (mutually exclusive, claude type only): schedule a deferred `tmux load-buffer` + `paste-buffer -p` + `Enter` so a multi-line block arrives as a single bracketed-paste message
10. Start a `SessionMonitor` greenlet for this session

## tmux Per-Session Options (`_apply_session_options`)

Right after `new-session` we set, on the just-created session:

| option | value | why |
|--------|-------|-----|
| `status` | `off` | Hides the green tmux status bar — the embedded ttyd terminal should look like a plain xterm, not a dev's tmux |
| `mouse` | `on` | tmux intercepts wheel events into copy-mode scrolling; full-screen TUIs (Claude Code, htop) can't hijack the wheel into arrow keys |
| `history-limit` | `50000` | Wheel scrolling has a meaningful buffer to walk through |
| `default-terminal` | `xterm-256color` | Predictable color rendering inside ttyd |
| `allow-rename` / `set-titles` | `off` | The inner shell can't rewrite the session name we display in our tab |
| `aggressive-resize` (window) | `on` | Pane size follows the iframe size when other clients are detached |

These settings are **per-session**, not in a `~/.tmux.conf` we ship — keeps user tmux configs untouched. Set via `tmux -L <socket> set-option -t <name> ...` after the session is created.

## Wiki-bootstrap via initial_command / initial_text

`SessionManager.spawn()` accepts two mutually-exclusive optional fields (both
claude-type only):

- `initial_command: str` (≤200 chars) — typed via `tmux send-keys` after a
  short delay (default 5s). Used for short slash commands.
- `initial_text: str` (multi-line) — delivered via `tmux load-buffer` +
  `paste-buffer -p` (bracketed paste) + `Enter`, so the entire block lands
  as one Claude message instead of being submitted line-by-line. Used by
  the new-vault wizard's `bootstrap_new_vault` flag, which wraps
  `/claude-obsidian:wiki` with `tools/newValPrefix.md` (plugin check) and
  `tools/newValSuffix.md` (visual workspace copy).

Both paths schedule with `threading.Timer` (eventlet patches `Timer` to a
cooperative greenlet, so the spawn API call returns immediately).

The bootstrap session must be interactive (the bootstrap may ask questions),
so we cannot use `claude -p`. The user answers prompts in the Terminal tab.
See `09-api.md` for the API field and `docs/plugin-commands.md` for the
wizard-vs-task-queue trade-off.

## Port Management

`_find_free_port()` scans `TTYD_PORT_BASE` to `TTYD_PORT_MAX`:
- Skip ports already in the registry
- Try to bind `127.0.0.1:<port>` with `SO_REUSEADDR`; if bind succeeds, that port is free
- Raise `StartupError` if no port is available in the range

On server restart: old ttyd processes may hold ports in TIME_WAIT. A 10-second grace
period before accepting spawn requests lets the OS release most TIME_WAIT ports;
`SO_REUSEADDR` covers the rest.

## SessionMonitor

One greenlet per session, polling `proc.poll()` every 5 seconds. If the ttyd process
has exited unexpectedly: emit `session_crashed` SocketIO event with `session_id`, `vault`,
and message; remove session from registry; greenlet exits.

Browser response: toast "Terminal session crashed. Click [Restart] to reopen." and
update the sidebar dot.

## Client Disconnect / Tab Close

`DELETE /api/sessions/{id}` → `proc.terminate()` → wait 3s → `proc.kill()` → remove
from registry → `tmux kill-session` on the underlying tmux session. Closing the
`×` on a browser tab is treated as the user's explicit "done with this
terminal" signal, so we tear tmux down at the same time to avoid orphan
accumulation. tmux-kill failures are logged but do not fail the request —
the ttyd kill + registry pop have already succeeded. SessionMonitor greenlet
detects the terminated proc on next poll and exits cleanly.

## Server Restart Reconciliation

`TmuxManager.reconcile()` on startup:
- Calls `tmux -S <socket> ls -F "#{session_name}"` to discover existing tmux sessions
- Sessions matching `rsm-*` that are not in the current registry are **orphaned**
- Orphaned sessions are shown in the sidebar with a warning dot; user must manually reattach or kill
- ttyd processes **do not survive** resman restarts; fresh ttyd processes are spawned on demand

## Key Decisions

- **ttyd eliminates custom PTY code** — no TmuxOutputStreamer, no PtyBridge, no xterm.js WebSocket bridge
- **Monotonic counter** for session names — `rsm-ai-agents-claude-1`, `-2`, etc.; multiple sessions of same type per vault are supported
- **Port range 7680–7999** — 320 ports; far more than any realistic number of concurrent sessions; configurable in resman.yaml
- **Tab close kills tmux too** — closing the `×` on a tab is the user's explicit "done with this terminal" signal; both ttyd and the underlying tmux session are torn down so no orphans accumulate. To preserve a long-running tmux session, attach to it directly from a shell instead of closing the tab
- **Orphans are reclaimable from the UI** — orphan tmux sessions (left over from a crash or a previous run) are listed in the sessions-overview modal with a "Kill all" button; nothing is killed automatically by the server
- **10s startup grace** — before accepting spawn requests; avoids TIME_WAIT port conflicts
- **Block on `_wait_for_listen`** — the spawn API only succeeds once the iframe URL will actually load; eventlet's socket monkey-patch makes the wait cooperative
- **Polish options at the session, not the user's `~/.tmux.conf`** — never touch the user's personal tmux config
- **`initial_command` is for interactive Claude only** — `claude -p` is non-interactive and cannot answer bootstrap prompts; the wizard path drives a real REPL via `tmux send-keys`

## Constraints

- Ports must only be allocated from the configured range (`ttyd_port_base` to `ttyd_port_max`)
- `_find_free_port()` must check both the OS and the in-registry set (race condition prevention)
- ttyd processes must be terminated on server shutdown (not left as orphans consuming ports)
- `VaultRuntime` does not accept session-spawn requests until `TmuxManager.reconcile()` completes

## Open Questions

- **CORS / iframe cross-origin** — Flask runs on port 5090; ttyd runs on port 768x. Both are on `127.0.0.1`. Verified working in Firefox; ttyd is launched with `--check-origin=false` to allow the cross-port iframe. Chrome behavior not yet verified.
