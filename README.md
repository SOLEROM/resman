# resman

A local web command-and-control panel for managing multiple Obsidian
research vaults. Implements the design in [`docs/design/`](docs/design/).

## Quick start

```bash
./deps.sh                # install host deps (tmux, python3-venv, ttyd) + venv
./run.sh                 # localhost only (http://127.0.0.1:5090)
./run.sh --public        # accessible on the LAN (http://<lan-ip>:5090)
```

The launcher creates a venv on first run, installs dependencies, and starts
the Flask + Socket.IO server on `http://127.0.0.1:5090`.

`--public` binds Flask and ttyd to `0.0.0.0` and relaxes the Socket.IO CORS
allow-list. Resman has no authentication — only run `--public` on a trusted
network.

`config/resman.yaml` is the source of truth — copy from
`config/resman.yaml.example` and edit before first run. A per-user override
at `~/.resman.yaml` (if present) takes priority over the repo file.

### Per-OS virtualenvs (Ubuntu 22 + 24)

resman supports Ubuntu 22.04 (Python 3.10) and 24.04 (Python 3.12). Keep one
venv per interpreter — never commit them. `deps.sh` / `run.sh` take a
`--vname` flag so each host targets its own:

```bash
# Ubuntu 24.04 (Python 3.12)
./deps.sh --vname .venv-ubuntu24 && ./run.sh --vname .venv-ubuntu24

# Ubuntu 22.04 (Python 3.10)
./deps.sh --vname .venv-ubuntu22 && ./run.sh --vname .venv-ubuntu22
```

Plain `./run.sh` (no `--vname`) uses the default `.venv`. All `.venv*`
directories are gitignored.

## Layout

```
resman/
├── config/
│   ├── resman.yaml.example   # copy to resman.yaml and edit (or use ~/.resman.yaml)
│   └── schedule.yaml.example # cron tasks
│   # tasks.jsonl, budget.json, task-logs/ are runtime state (gitignored)
├── control-plane/
│   ├── server.py             # composition root + entrypoint
│   ├── requirements.txt
│   ├── modules/              # one module per subsystem
│   ├── templates/index.html  # SPA shell
│   └── static/               # CSS + JS
├── tools/
│   ├── ingest.sh             # vault-agnostic URL ingest (dispatched by the server)
│   ├── new-vault.sh          # scaffold a new vault
│   ├── remoteAgent.sh        # CLI bridge to drive resman from a script / SSH
│   ├── newValPrefix.md       # new-vault bootstrap prompt prefix
│   └── newValSuffix.md       # new-vault bootstrap prompt suffix
├── prompts/
│   └── urlInjestPrefix.md    # constructive-extraction prefix for URL ingest
├── man/                      # operator manual — rendered live in the Help tab
├── deploy/systemd/           # systemd unit + installer
├── docs/                     # design spec, remote-agent + plugin reference
├── tests/                    # pytest suite (81 tests, ~1.5s)
├── run.sh                    # launcher
└── deps.sh                   # dependency installer
```

## Tests

```bash
.venv/bin/python -m pytest tests/ -v          # or .venv-ubuntu24/bin/python …
```

The suite covers:
- EventBus pub/sub semantics
- ConfigManager: validation, atomic writes, EventBus emission, cron string
  validation
- VaultRegistry: path validation, .obsidian detection, scan_paths discovery
- WindowState: persistence, corruption recovery, transitions, overrun
- TaskManager: JSONL replay, crash recovery (interrupted state), bad-line
  skipping, partial-last-line truncation, ALL-vault parent/child, window
  gating, priority promotion
- Scheduler: skip-when-inactive cron behaviour, skip-count threshold
- Routes: CSRF header enforcement, 503 when ttyd is missing, validation
  rejections, full create/cancel/promote/log lifecycle
- TmuxManager: integration tests on an isolated socket (skipped when tmux
  is absent)
- ObsidianPush: priority rule, atomic dir creation, OSError handling

## Design references

See [`docs/design/`](docs/design/) for the authoritative subsystem documents:
01-architecture, 02-configuration, 03-vault-registry, 04-terminal-sessions,
05-obsidian-push, 06-task-management, 07-window-state, 08-scheduler,
09-api, 10-frontend, 11-security, 12-error-handling.

Operator-facing docs live in [`man/`](man/) (also served in the Help tab);
the remote-agent contract is in [`docs/remote-agent.md`](docs/remote-agent.md)
and the plugin reference in [`docs/obsidian-plugin.md`](docs/obsidian-plugin.md).

## Implementation notes

- The Flask server uses eventlet's monkey-patch (`server.py:11`) so
  Socket.IO can handle concurrent WebSocket connections. Tests bypass this
  by importing `build_app` directly with `async_mode="threading"`.
- The cron scheduler prefers APScheduler's `GeventScheduler` and falls
  back to `BackgroundScheduler` only when gevent is missing — the design
  mandates `GeventScheduler` in production with eventlet to avoid the
  documented BackgroundScheduler/eventlet subprocess deadlock.
- `ttyd` is treated as optional. When missing, `POST /api/sessions` and
  `DELETE /api/sessions/{id}` return HTTP 503; the rest of the server
  functions normally.
- All subprocess calls use the argument-list form (`subprocess.run([...])`).
  `shell=True` and `sh -c` are never used. `params.cmd_parts` for the
  `run-shell` operation must be a pre-validated list.
- The CSRF guard is a single header check (`X-Requested-With: resman`) —
  sufficient for a localhost-only tool, applied uniformly via the SPA
  `api()` fetch wrapper.
