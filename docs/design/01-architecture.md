# Architecture

## Overview

resman is a local-only web command-and-control panel for managing multiple Obsidian
research vaults. It runs as a single Flask+SocketIO process on `127.0.0.1:5090`,
coordinates tmux sessions via an isolated tmux socket, spawns ttyd processes for
browser terminals, and pushes vault health status directly into each vault's filesystem.
All major subsystems communicate through an internal EventBus to avoid circular imports.

## Directory Layout

```
resman/
├── deps.sh                  # host-dep installer (tmux, ttyd, python venv)
├── run.sh                   # entry point; activates venv and starts server.py
├── config/
│   ├── resman.yaml          # app settings + vault registry (source of truth)
│   ├── resman.yaml.example  # annotated starter config shipped with repo
│   ├── schedule.yaml        # cron task definitions
│   ├── budget.json          # window state (written by UI only)
│   ├── tasks.jsonl          # append-only task event log
│   └── task-logs/           # one .log file per task execution
├── control-plane/
│   ├── server.py
│   ├── requirements.txt
│   └── modules/
│       ├── config_manager.py     # load/save/reload resman.yaml; emits config_reloaded
│       ├── vault_registry.py     # vault list, .obsidian/ validation, discovery
│       ├── session_manager.py    # spawn/kill ttyd processes; port registry; SessionMonitor
│       ├── task_manager.py       # task queue, state machine, parent/child, JSONL log
│       ├── window_state.py       # window gate; emits window_activated on EventBus
│       ├── scheduler.py          # GeventScheduler cron + ObsidianPush 60s job
│       ├── tmux_manager.py       # tmux session lifecycle; reconcile() on restart
│       ├── obsidian_push.py      # push _resman/status.md into each vault
│       ├── event_bus.py          # internal pub/sub; breaks circular coupling
│       ├── plugin_commands.py    # centralized claude-obsidian command strings
│       ├── routes.py             # REST API
│       └── websocket_handlers.py # Socket.IO events
├── docs/                    # system documentation (editable in browser)
├── tests/                   # pytest suite — full module coverage
├── tools/
│   ├── ingest.sh
│   └── new-vault.sh
├── wikValTemplate/
└── .ref/                    # reference repos (dev only)
```

## Host Bootstrap Scripts

`deps.sh` and `run.sh` are the only operator-facing entry points.

- `deps.sh` detects the package manager (apt/dnf/pacman/snap), installs `tmux`, ttyd (with a fallback chain: apt → snap → prebuilt GitHub binary on Ubuntu 22 where ttyd is not in apt), and creates the Python venv. Flags: `--vname <path>` (custom venv path), `--check` (probe-only), `--no-sudo`.
- `run.sh` activates the venv and runs `server.py`. Same `--vname` flag; remaining args forward to `server.py`. If the venv has a broken `pip` shebang from being copied across hosts, the script auto-recreates the **default** `.venv` only — for user-supplied `--vname` paths it refuses to delete and surfaces the problem so the operator decides.

## Component Map

```
Browser
  └── resman SPA (Flask serves static)
       ├── Vault sidebar — status dots, session launcher
       ├── ttyd iframe  — per-vault terminal (WebSocket direct to ttyd)
       ├── Task panel   — JSONL-backed task queue
       └── Config panel — live YAML editors

Flask + eventlet (port 5090)
  ├── EventBus          — internal pub/sub; decouples WindowState ↔ TaskManager
  ├── VaultRegistry     — vault list from resman.yaml + .obsidian/ validation
  ├── SessionManager    — spawn/kill ttyd processes; port registry; SessionMonitor
  ├── TaskManager       — priority queue, dispatch mutex, parent/child, JSONL log,
  │                        PTY-based streaming runner (live log chunks on bus),
  │                        scheduled state + Popen tracking for cancel-running
  ├── WindowState       — is_window_active() gate
  ├── Scheduler         — GeventScheduler cron; ObsidianPush 60s job;
  │                        one-shot DateTrigger per scheduled task
  ├── TmuxManager       — tmux session lifecycle; reconcile() on restart
  └── ObsidianPush      — writes _resman/status.md into each vault

ttyd processes (one per active terminal session)
  └── attaches to: tmux attach-session -t rsm-<vault>-<type>-<n>
       └── browser iframe: http://127.0.0.1:<port>
```

## Key Decisions

- **ttyd replaces custom PTY stack** — eliminates ~400 lines of TmuxOutputStreamer + PtyBridge code; ttyd handles PTY management, xterm.js protocol, resize, and WebSocket streaming
- **eventlet monkey-patch** — required for concurrent WebSocket connections; threading mode cannot be used
- **GeventScheduler** — APScheduler's eventlet-compatible scheduler; `BackgroundScheduler` deadlocks when cron callbacks call eventlet-patched subprocess
- **EventBus** — `WindowState` never imports `TaskManager`; activation is communicated via `window_activated` event; eliminates circular import
- **RESMAN_ROOT** — detected at startup via `Path(__file__).parent.parent`; all `tools/` references use absolute path; never `../tools/`
- **Isolated tmux socket** — `resman` socket name; never shares with user's personal tmux

## Constraints

- Server must bind to `127.0.0.1` only — no network exposure
- Must use `eventlet.monkey_patch()` before any other imports
- `GeventScheduler` is mandatory; `BackgroundScheduler` is prohibited
- All `tools/` invocations must use `RESMAN_ROOT / "tools"` (absolute path)
- tmux must be installed; failure is fatal on startup
- ttyd absence is non-fatal; server degrades gracefully (terminals disabled)

## Open Questions

- None — all architectural decisions are resolved in this design set.
