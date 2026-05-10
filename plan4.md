# resman — Research Vault Manager
## System Design Plan v4

> Refined from plan3.md after office-hours architectural review.
> Key changes from v3: custom PTY stack (TmuxOutputStreamer + PtyBridge) replaced
> by ttyd binary; Obsidian-push model added (vault health visible in Obsidian graph
> view via `_resman/status.md`); implementation order reversed (Obsidian-push first,
> terminal second); SessionManager replaces VaultRuntime terminal spawning;
> SessionMonitor greenlet added for crash detection; find_free_port() specified;
> ttyd graceful degradation defined.

---

## 0. Quick Start

```bash
# 1. Clone and install
git clone <resman-repo> resman
cd resman
pip install -r control-plane/requirements.txt

# 2. Install ttyd (required for terminal sessions)
brew install ttyd          # macOS
# or: cargo install ttyd  # any platform with Rust
# or: see https://github.com/tsl0922/ttyd/releases for prebuilt binaries

# 3. Configure
cp config/system.yaml.example config/system.yaml
# Edit config/system.yaml: add at least one vault path

# 4. Run
python control-plane/server.py

# 5. Open browser
# http://127.0.0.1:5090
```

On startup the server prints a structured startup report:

```
resman starting...
  config:    OK (3 vaults loaded)
  tmux:      OK (socket: resman)
  ttyd:      OK (/usr/local/bin/ttyd v1.7.4)
  scheduler: OK (2 cron tasks)
  tasks:     OK (replayed 142 events, 1 bad line skipped)
  plugin:    OK (claude-obsidian v1.4.2, compatible)
  server:    http://127.0.0.1:5090
```

If ttyd is not found: `ttyd: MISSING (terminal sessions disabled — install ttyd to enable)`.
The server still starts. The Obsidian-push cron, task queue, and window system all work
without ttyd. Only terminal sessions are gated on ttyd availability.

If any other required component fails (tmux not found, YAML parse error, corrupt tasks.jsonl),
the server logs a clear error and refuses to start — it does not silently start with broken state.

---

## 1. Purpose

**resman** is a local web-based command-and-control panel for managing multiple Obsidian
research vaults on one machine. Each vault is an independent research project powered by
the `claude-obsidian` plugin. resman provides:

- A single browser dashboard to view and navigate all research vaults
- Browser-based terminals attached to real tmux sessions inside any vault (via ttyd)
- Vault health status pushed into each vault as `_resman/status.md` — visible in Obsidian's graph view without opening resman
- Plugin operations (ingest, lint, autoresearch, canvas) on any vault
- A prioritized task queue gated on an active Claude window
- Scheduled recurring housekeeping tasks via cron
- Live YAML config editing

resman runs locally on `127.0.0.1`. It is not designed for remote or multi-user access.

---

## 2. Reference Projects

All reference repos live in `.ref/`. Consult before building each subsystem.

| Repo | Path | What to learn |
|---|---|---|
| `claude-obsidian` | `.ref/claude-obsidian/` | Vault structure, plugin commands, WIKI.md schema, wiki skills |
| `agent_ccpanBuilder` | `.ref/agent_ccpanBuilder/` | WebTUI architecture — Flask+SocketIO+tmux+xterm.js, option modules A–J |
| `cldlab` | `.ref/cldlab/` | YAML config schema, control-plane server layout, Docker+local-shell agent model |

Key files per subsystem:

| Subsystem | Read first |
|---|---|
| Web TUI server | `WEBTUI_SKILL.md`, `scripts/core/`, `scripts/options/` |
| Control plane modules | `cldlab/control-plane/server.py`, `cldlab/control-plane/modules/` |
| YAML config design | `cldlab/config.yaml`, `cldlab/config_yaml_help.md` |
| Vault structure | `claude-obsidian/WIKI.md`, `claude-obsidian/README.md` |
| Plugin commands | `obsiPlug/commands.md`, `obsiPlug/usageExampels.md` |

---

## 3. System Architecture

### 3.1 Directory Layout

```
resman/
├── config/
│   ├── system.yaml          # app settings + vault registry (source of truth)
│   ├── system.yaml.example  # annotated starter config shipped with repo
│   ├── schedule.yaml        # cron task definitions
│   ├── budget.json          # current window state (written by UI)
│   ├── tasks.jsonl          # append-only task event log
│   └── task-logs/           # one .log file per task execution
│
├── control-plane/           # web server (Flask + SocketIO + eventlet)
│   ├── server.py
│   ├── requirements.txt
│   ├── modules/
│   │   ├── config_manager.py     # load/save/reload system.yaml; emits config_reloaded event
│   │   ├── vault_registry.py     # vault list, metadata, discovery, .obsidian/ validation
│   │   ├── session_manager.py    # spawn/kill ttyd processes; port registry; SessionMonitor
│   │   ├── task_manager.py       # task queue, state machine, parent/child, dispatch mutex
│   │   ├── window_state.py       # manual window sync + tmux heuristic + gating logic
│   │   ├── scheduler.py          # APScheduler (GeventScheduler) cron task firing
│   │   ├── tmux_manager.py       # tmux session lifecycle; reconcile on restart
│   │   ├── obsidian_push.py      # push _resman/status.md into each vault (60s cron)
│   │   ├── event_bus.py          # lightweight internal pub/sub; breaks circular coupling
│   │   ├── plugin_commands.py    # centralized claude-obsidian command map
│   │   ├── routes.py             # REST API (see Section 8.9)
│   │   └── websocket_handlers.py # Socket.IO events
│   ├── templates/
│   │   └── index.html            # SPA shell
│   └── static/
│       ├── js/app.js
│       └── css/style.css
│
├── docs/                    # system documentation (editable in browser)
│   ├── overview.md
│   ├── vaults.md
│   ├── tasks.md
│   └── plugin-commands.md
│
├── tools/                   # vault-agnostic CLI tools
│   ├── ingest.sh            # ingest URL into a vault (was: injest.sh — renamed)
│   └── new-vault.sh         # scaffold a new vault from wikValTemplate
│
├── wikValTemplate/          # vault template (SOLEROM/wikValTemplate)
└── .ref/                    # cloned reference repos (dev/build only)
```

**Dropped from plan3:** `tmux_output_streamer.py`, `pty_bridge.py`, `vault_runtime.py`.
These are replaced by `session_manager.py` (ttyd-based terminal spawning) and
`obsidian_push.py` (vault health push). The custom PTY/WebSocket layer is gone.

**RESMAN_ROOT:** detected at startup (`Path(__file__).parent.parent`). All references
to `tools/` use `RESMAN_ROOT / "tools"` — never relative paths like `../tools/`.

### 3.2 Component Map

```
Browser
  └── resman SPA (Flask serves static)
       ├── Vault sidebar — status dots, session launcher
       ├── ttyd iframe  — per-vault terminal (WebSocket direct to ttyd process)
       ├── Task panel   — JSONL-backed task queue, cron controls
       └── Config panel — live YAML editors

Flask + eventlet (port 5090)
  ├── EventBus          — internal pub/sub; decouples WindowState ↔ TaskManager
  ├── VaultRegistry     — vault list from system.yaml + .obsidian/ validation
  ├── SessionManager    — spawn/kill ttyd processes; port registry; SessionMonitor greenlet
  ├── TaskManager       — priority queue, dispatch mutex, parent/child, JSONL log
  ├── WindowState       — is_window_active() gate (function, not cached field)
  ├── Scheduler         — GeventScheduler cron; fires ObsidianPush every 60s
  ├── TmuxManager       — tmux session lifecycle; reconcile() on restart
  └── ObsidianPush      — writes _resman/status.md into each vault directory

ttyd processes (one per active terminal session)
  └── attaches to: tmux attach-session -t rsm-<vault>-<type>-<n>
       └── browser iframe points to http://127.0.0.1:<port>
```

**EventBus decoupling:** `WindowState` never imports `TaskManager`. When the window
activates, `WindowState` emits `window_activated` on the `EventBus`. `TaskManager`
subscribes and promotes deferred tasks. This eliminates the circular import.

**ttyd as PTY backend:** ttyd is a mature C binary (used by JupyterHub, Cloud Shell)
that handles PTY management, xterm.js protocol, resize events, and WebSocket streaming.
resman's only responsibility is: spawn the ttyd process pointing at the right tmux session,
track the port, and embed the iframe. No custom PTY code.

---

## 4. Configuration System

### 4.1 `config/system.yaml` Schema

```yaml
app:
  host: 127.0.0.1
  port: 5090
  tmux_socket: resman
  tmux_prefix: "rsm-"
  scrollback_limit: 10000
  claude_cmd: "claude --dangerously-skip-permissions"
  obsidian_cmd: "flatpak run md.obsidian.Obsidian"
  # Port range for ttyd processes. Defaults to 7680-7999.
  ttyd_port_base: 7680
  ttyd_port_max: 7999

window_budget:
  weekly_start: "Monday 09:00"
  weekly_end:   "Sunday 23:00"

vaults:
  - name: ai-agents-research
    path: /data/research/ai-agents-research
    tags: [ai, agents]
    readme: README.md

  - name: llm-benchmarks
    path: /home/user/projects/llm-benchmarks
    tags: [llm, eval]
    readme: README.md

  - name: ml-papers
    path: /mnt/nas/research/ml-papers
    tags: [ml, papers]
    readme: README.md

scan_paths:
  - /data/research
  - /home/user/projects
```

**system.yaml.example** ships with all fields present and inline comments. Users copy it.

### 4.2 `config/schedule.yaml` Schema

```yaml
cron_tasks:
  - name: weekly-lint-all
    cron: "0 8 * * 0"
    vault: ALL
    operation: wiki-lint
    priority: low

  - name: daily-hot-cache-update
    cron: "0 22 * * *"
    vault: ALL
    operation: wiki-update-hot-cache
    priority: low
```

Cron strings validated with `CronTrigger.from_crontab()` at load time.

### 4.3 `config/budget.json` Schema

```json
{
  "window_state": "active",
  "window_started_at": "2026-05-05T10:00:00",
  "window_ends_at":    "2026-05-05T15:00:00",
  "weekly_synced_at":  "2026-05-05T09:00:00",
  "weekly_ends_at":    "2026-05-11T23:00:00"
}
```

Startup validation: missing → create with `window_state: between`. Invalid JSON → reset
to `between`. Never crash on corrupt budget.json. Write order: always write file first,
then update in-memory state.

### 4.4 Live Config Editing

YAML editor (Option J pattern) for both `system.yaml` and `schedule.yaml`. All saves
are atomic: write to `.tmp` then `os.replace()`. Validation before commit: YAML parses,
result is dict, required vault fields present, cron strings parse, file size ≤ 1 MB.
On success, `config_reloaded` emitted on EventBus; subscribers re-derive state.

---

## 5. Web TUI (Control Plane)

### 5.1 Server Stack

```
Python:   Flask + Flask-SocketIO + eventlet
Frontend: vanilla JS + Socket.IO (CDN) — no build step, no xterm.js (ttyd handles this)
Terminal: ttyd process per session, embedded as iframe
```

Use `eventlet` monkey-patch. Never use threading mode. **APScheduler must use
`GeventScheduler` (eventlet-compatible), not `BackgroundScheduler`.** `BackgroundScheduler`
deadlocks when the cron callback spawns a tmux session (eventlet-patched subprocess
blocks the scheduler thread).

### 5.2 WebTUI Option Modules to Enable

Adapt from `.ref/agent_ccpanBuilder/scripts/options/`:

| Option | Module | Purpose in resman |
|---|---|---|
| **A** | `opt_a_commands` | Per-vault quick command palette (ingest, lint, autoresearch, open Obsidian) |
| **C** | `opt_c_markdown` | Markdown viewer/editor for vault README and system docs/ |
| **F** | `opt_f_tabs` | Multiple terminal tabs per vault (iframe-based; each tab is a ttyd session) |
| **G** | `opt_g_eventlog` | Append-only JSONL audit trail of all operations |
| **I** | `opt_i_splitter` | Drag-to-resize terminal + README panel side by side |
| **J** | `opt_j_config_editor` | Live YAML editor for system.yaml and schedule.yaml |

**Note on Option F:** The original xterm.js WebSocket implementation is replaced by
ttyd iframes. Each terminal tab is an `<iframe src="http://127.0.0.1:{port}">`. Tab
switching just toggles iframe visibility. ttyd handles resize, color codes, and all
xterm.js protocol internally.

### 5.3 UI Layout

```
┌────────────────────┬─────────────────────────────────────────────────┐
│                    │  [ Terminal ] [ Docs ] [ Tasks ] [ Config ]      │
│  Vault Sidebar     ├─────────────────────────────────────────────────┤
│  [search/filter]   │                                                   │
│  ● ai-agents  [▶] │  ttyd iframe   OR   Markdown panel               │
│  ○ llm-bench  [▶] │                OR   Task queue panel             │
│  ─ unregistered   │                OR   YAML config editor            │
│    found-vault    │                                                   │
│                   │                                                   │
│  [+ New Vault]    ├─────────────────────────────────────────────────┤
│  [⚙ Config]      │  Window: ● ACTIVE  ends in 3h 12m   [ sync ▼ ]  │
└────────────────────┴─────────────────────────────────────────────────┘
```

**Left Sidebar:**
- Filter bar at top: search by name, filter by tag, filter by status
- Registered vaults: name, status dot (single color, priority rule in 5.4), tags (dimmed)
- `[▶]` mini-menu: "Open Claude" and "Open Shell" — no hidden default
- Clicking vault name selects it and switches to that vault's Terminal tab
- Unregistered vaults (from `scan_paths`): shown below divider with `[+ Register]`
- `[+ New Vault]` — scaffold wizard
- `[⚙ Config]` — opens YAML editor in main panel
- Empty state: "Add your first vault to get started →"

**Terminal tab with ttyd unavailable:** the tab header shows grayed-out text "Terminal
(ttyd not installed)". The main area shows: "Install ttyd to enable browser terminals.
See docs/overview.md for install instructions." The rest of the UI works normally.

**Window Status Bar (bottom of screen):** always visible, fixed height. Color: green =
`active`, gray = `between`, red = `ended`. Final 60s: "ending in Xs..." (yellow).

### 5.4 Vault Status Dot — Priority Rule

One dot color at any time. Highest-priority wins. Hover for all active flags.

| Priority | Color | Condition |
|---|---|---|
| 1 (highest) | Red | Last task failed |
| 2 | Yellow | A task is currently running |
| 3 | Green | Has an active tmux session |
| 4 | Blue | Obsidian is open for this vault |
| 5 (lowest) | Gray | Idle / none of the above |

**Dot spec:** 10px filled circle, CSS class `vault-dot-{color}`. Hover tooltip: lists
all true conditions.

---

## 6. Window State System

Same as plan3.md — manual management via window status bar. resman does not auto-detect
Claude Code windows.

### 6.1 Window States

| State | Meaning | Task execution |
|---|---|---|
| `active` | Claude window open and usable | Tasks run |
| `between` | Previous window ended | Tasks queue as deferred |
| `ended` | Weekly period ended | Tasks queue as deferred |

### 6.2 Sync Controls

| Control | Action |
|---|---|
| **Start window now** | Prompts for duration (required, 1–12h). Sets state=active. |
| **End window now** | Sets state=between. |
| **Start weekly period** | Sets weekly_synced_at=now, weekly_ends_at=now+period. |
| **End weekly period** | Sets state=ended. |

### 6.3 Window Transition Rules

- `active → between`: user clicks "End window now" OR `window_ends_at` reached
- `between → active`: explicit "Start window now" with duration — only path to `active`
- `active → ended`: "End weekly period"
- `ended → active`: "Start weekly period" then "Start window now"

**`is_window_active()` is a function, not a cached field.** Compares `datetime.utcnow()`
against `window_ends_at` inline on every call. No 60-second lag.

**Tmux heuristic fallback:** if `window_state == "between"` and `rsm-*-claude` session
found alive, show amber indicator: "Claude session detected — did you forget to start
the window?" Informational only. Does not change authoritative state.

On window state flip, emit `window_state_changed` SocketIO event to all clients.

### 6.4 Task Gating

- **While `active`:** tasks run normally
- **While `between` or `ended`:** new tasks queued as `deferred`
- **On `window_activated` (EventBus):** `high` and `medium` priority deferred tasks
  promoted to `pending`; `low` priority tasks stay deferred (manual promote)

---

## 7. Vault Management

### 7.1 Vault Discovery

On startup:
1. Load vaults from `system.yaml` — registered vaults. Each has explicit `path`.
2. Validate each: path exists AND contains `.obsidian/`. Show distinct warnings.
3. If `scan_paths` configured, scan (depth ≤ 2) for unregistered vaults with `.obsidian/`.

Vault health check modal: path exists, `.obsidian/` present, readme found, last active
session, last completed task.

### 7.2 Create New Vault

`[+ New Vault]` wizard: name (`[a-zA-Z0-9_-]`), target path, purpose →
runs `tools/new-vault.sh <name> <target_path>` with progress view → on success,
vault in sidebar, terminal tab opens automatically.

### 7.3 Terminal Sessions (ttyd-based)

**SessionManager** owns all terminal session state. A session is a ttyd process
bound to a specific tmux session on a specific port.

#### Session Registry

Dict keyed by `session_id`. Each entry:
```python
@dataclass
class Session:
    id: str           # uuid4
    vault: str        # vault name
    session_type: str # "claude" | "shell"
    tmux_session: str # rsm-<vault>-<type>-<n>
    port: int         # ttyd port
    proc: Popen       # the ttyd process
    created_at: datetime
```

#### Session Spawning

```python
# SessionManager.spawn(vault_name, session_type) -> Session
def spawn(self, vault: VaultConfig, session_type: str) -> Session:
    # 1. Name the tmux session (monotonic counter per vault+type)
    counter = self._next_counter(vault.name, session_type)
    tmux_session = f"rsm-{vault.name}-{session_type}-{counter}"

    # 2. Ensure tmux session exists (create if not)
    subprocess.run(
        ["tmux", "-S", TMUX_SOCKET, "new-session", "-d", "-s", tmux_session,
         "-c", str(vault.path)],
        check=False  # ok if already exists
    )
    # For claude sessions, send the claude command to the tmux window:
    if session_type == "claude":
        subprocess.run(
            ["tmux", "-S", TMUX_SOCKET, "send-keys", "-t", tmux_session,
             f"cd {vault.path} && {CLAUDE_CMD}", "Enter"],
        )

    # 3. Find a free port
    port = self._find_free_port()

    # 4. Spawn ttyd
    proc = subprocess.Popen([
        "ttyd", "--port", str(port), "--writable",
        "tmux", "-S", TMUX_SOCKET, "attach-session", "-t", tmux_session
    ])

    session = Session(id=str(uuid4()), vault=vault.name,
                      session_type=session_type, tmux_session=tmux_session,
                      port=port, proc=proc, created_at=datetime.utcnow())
    self._registry[session.id] = session
    self._monitor_session(session)  # start SessionMonitor greenlet
    return session
```

#### Port Management

```python
def _find_free_port(self) -> int:
    """Scan TTYD_PORT_BASE to TTYD_PORT_MAX for an available port."""
    occupied = {s.port for s in self._registry.values()}
    for port in range(TTYD_PORT_BASE, TTYD_PORT_MAX + 1):
        if port in occupied:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise StartupError("No free port available in ttyd port range")
```

On server restart after crash: old ttyd processes may hold ports in TIME_WAIT state.
The 10s grace period at startup (before accepting session-spawn requests) lets the OS
release most TIME_WAIT ports. `SO_REUSEADDR` covers the rest.

#### SessionMonitor

One greenlet per session, polling `proc.poll()` every 5 seconds:

```python
def _monitor_session(self, session: Session):
    def monitor():
        while True:
            eventlet.sleep(5)
            if session.proc.poll() is not None:
                # ttyd process died unexpectedly
                socketio.emit("session_crashed", {
                    "session_id": session.id,
                    "vault": session.vault,
                    "message": f"Terminal session crashed (ttyd exited)"
                })
                self._registry.pop(session.id, None)
                return
    eventlet.spawn(monitor)
```

Browser receives `session_crashed` event and shows a toast: "Terminal session crashed.
Click [Restart] to reopen." The sidebar dot is updated immediately.

#### On Client Disconnect

When the user closes a terminal tab, the SPA sends a `DELETE /api/sessions/{id}` request.
SessionManager calls `proc.terminate()`, waits up to 3s, then `proc.kill()`. Removes
from registry. The tmux session itself is **not killed** — the user may want to reattach.
To kill the tmux session, the user can run `tmux kill-session` from inside the terminal.

#### On Server Restart

`TmuxManager.reconcile()` on startup:
- Calls `tmux -S <socket> ls -F "#{session_name}"` to discover existing tmux sessions
- Sessions found in tmux but not in the registry are "orphaned" — shown in sidebar with
  a warning dot ("Orphaned session — click to reattach or kill")
- Orphaned sessions are NOT auto-killed; the user decides
- ttyd processes do NOT survive resman restarts; fresh ttyd processes are spawned on demand

#### CORS Note

Flask (port 5090) and ttyd (port 768x) are both on `127.0.0.1`. Iframes loading content
from a different port are cross-origin, but browsers do not block this by default for
localhost-to-localhost iframes. No CORS headers needed. Verify in Chrome and Firefox
before declaring victory — some browser security policies differ.

### 7.4 Obsidian Push

**ObsidianPush** runs as a GeventScheduler cron every 60 seconds, writing a status file
into each configured vault directory:

```python
# obsidian_push.py
STATUS_SUBDIR = "_resman"
STATUS_FILE = "status.md"

def push_vault_status(vault: VaultConfig, task_manager: TaskManager,
                      tmux_manager: TmuxManager) -> None:
    status_dir = Path(vault.path) / STATUS_SUBDIR
    status_dir.mkdir(exist_ok=True)
    status_file = status_dir / STATUS_FILE

    last_task = task_manager.last_completed(vault.name)
    running = task_manager.running_count(vault.name)
    tmux_active = tmux_manager.session_exists_pattern(f"rsm-{vault.name}-*")

    # Priority rule matches vault dot: red > yellow > green > gray
    if last_task and last_task.status == "failed":
        health = "red"
        summary = f"Last task failed: {last_task.operation} at {last_task.ended_at}"
    elif running > 0:
        health = "yellow"
        summary = f"{running} task(s) running"
    elif tmux_active:
        health = "green"
        summary = "Terminal session active"
    else:
        health = "gray"
        summary = "Idle"

    try:
        status_file.write_text(
            f"# {vault.name} — {health}\n\n"
            f"Updated: {datetime.utcnow().isoformat()}Z\n"
            f"Health: {health}\n"
            f"{summary}\n\n"
            f"[[_resman/status]]\n",
            encoding="utf-8"
        )
    except OSError as e:
        logger.warning(f"Obsidian push failed for {vault.name}: {e}")
        # Non-fatal — log and continue

def push_all_vaults(vault_registry, task_manager, tmux_manager):
    for vault in vault_registry.all():
        push_vault_status(vault, task_manager, tmux_manager)
```

**Obsidian hot-reload:** Obsidian watches the vault directory via `chokidar` and
hot-reloads file changes within a few seconds. The `_resman/status.md` node appears
in the graph view as a normal node — no plugin required.

**Pre-implementation validation (do this before writing code):** Open a vault, manually
write `_resman/status.md`, and confirm Obsidian detects it without a restart. If the
vault is git-synced, write `_resman/` to `.gitignore` and confirm sync doesn't conflict
with 60s write cycles.

**`.gitignore` note:** add `_resman/` to each vault's `.gitignore`. Document this in
`docs/overview.md`.

### 7.5 Tools

All tools in `tools/` take vault path as first argument. Always referenced via
`RESMAN_ROOT / "tools"`.

| Tool | Invocation | Purpose |
|---|---|---|
| `ingest.sh` | `$RESMAN_ROOT/tools/ingest.sh <vault_path> <url>` | Fetch URL and ingest into vault |
| `new-vault.sh` | `$RESMAN_ROOT/tools/new-vault.sh <name> <target_path>` | Clone template and register |

---

## 8. Task Management System

### 8.1 Task Data Model

```json
{
  "id": "t-uuid4",
  "name": "Ingest AI paper",
  "vault": "ai-agents-research",
  "operation": "wiki-ingest",
  "params": { "url": "https://arxiv.org/abs/2503.12345" },
  "priority": "high",
  "schedule": "background",
  "parent_id": null
}
```

| Field | Values |
|---|---|
| `vault` | vault name or `ALL` |
| `operation` | `wiki-ingest`, `wiki-lint`, `wiki-autoresearch`, `wiki-update-hot-cache`, `run-prompt`, `run-shell` |
| `priority` | `high` / `medium` / `low` |
| `schedule` | `immediate`, `background`, `deferred` |
| `parent_id` | UUID of parent task (ALL-vault children) or `null` |

### 8.2 Task Storage — JSONL Event Sourcing

Tasks stored in `config/tasks.jsonl` as an append-only event log.

```json
{"ts": "2026-05-05T10:01:00Z", "event": "created",         "task_id": "t-abc", "data": {<full task fields>}}
{"ts": "2026-05-05T10:01:05Z", "event": "started",         "task_id": "t-abc"}
{"ts": "2026-05-05T10:08:33Z", "event": "completed",       "task_id": "t-abc", "exit_code": 0}
{"ts": "2026-05-05T10:00:00Z", "event": "cron_skipped",    "task_id": "t-cron", "scheduled_at": "..."}
{"ts": "2026-05-05T10:01:00Z", "event": "dispatch_started","task_id": "t-all",  "data": {"expected_child_count": 3}}
{"ts": "2026-05-05T10:09:00Z", "event": "archived",        "task_id": "t-abc"}
```

| Event | When emitted |
|---|---|
| `created` | Task first queued; payload contains all task fields |
| `started` | Execution begins |
| `completed` | Finished successfully |
| `failed` | Finished with error |
| `interrupted` | Was `running` at server crash; discovered on replay |
| `deferred` | Moved to deferred queue (window not active) |
| `promoted` | Deferred task promoted to pending |
| `updated` | Params or priority changed |
| `child_created` | Parent task created a child |
| `dispatch_started` | Parent ALL-vault task about to create children; includes `expected_child_count` |
| `cron_skipped` | Cron tick fired but window not active; includes `scheduled_at` |
| `archived` | Soft-deleted from default UI view |

**Crash-consistency:** Each line wrapped in `try/except JSONDecodeError`. Bad lines
logged with byte offset, skipped. Partial final line truncated on startup. Integrity
check warns on mismatch between `dispatch_started.expected_child_count` and actual children.

**Running-at-crash handling:** A task replaying as `started` with no subsequent terminal
event is marked `interrupted` (not `failed`, not retried) and surfaced to the user.

**Compaction:** when `tasks.jsonl` exceeds 50,000 lines (or manual `/api/tasks/compact`):
1. Replay full log into in-memory state
2. Terminal-state tasks (`completed`, `failed`, `archived`) older than 90 days → `snapshot` event
3. Rewrite log: snapshots first, then remaining events

### 8.3 Task States

```
pending ──────────► running ──► completed ──► [archived]
   ▲                   │
   │ (promoted)        └──────► failed ──────► [archived]
   │                   │
deferred◄──────────────┘ (window not active)
                        └──────► interrupted   (crash-recovery)
```

### 8.4 Priority and Window Gating

| Priority | Window active | Window between/ended |
|---|---|---|
| `high` | runs immediately | deferred; promoted on next window activation |
| `medium` | runs as background | deferred; promoted on next window activation |
| `low` | runs as background | deferred; user must manually promote |

### 8.5 ALL-Vaults Tasks (Parent/Child)

1. Parent task created with `vault: ALL`
2. `dispatch_started` event written with `expected_child_count: N`
3. Under dispatch lock (`eventlet.semaphore.Semaphore(1)`): one child per vault, each
   writes `child_created` event
4. Crash mid-dispatch: startup warns on child count mismatch
5. Children run independently; parent state aggregated via EventBus:
   - `running` if any child running
   - `failed` if any child failed
   - `completed` only when all children complete successfully

### 8.6 Task Re-Run

Opens pre-filled task creation form. Submitting creates a new task (new UUID).
Does not mutate the original.

### 8.7 Operation-to-Execution Mapping

All commands constructed as **argument lists** via `subprocess.run([...])`.
Shell is never invoked directly.

| Operation | Execution |
|---|---|
| `wiki-ingest` | `[RESMAN_ROOT/tools/ingest.sh, vault_path, params.url]` |
| `wiki-lint` | `["claude", "-p", plugin_commands.LINT, "--dangerously-skip-permissions"]` in vault dir |
| `wiki-autoresearch` | `["claude", "-p", plugin_commands.autoresearch(params.topic), "--dangerously-skip-permissions"]` in vault dir |
| `wiki-update-hot-cache` | `["claude", "-p", plugin_commands.UPDATE_HOT_CACHE, "--dangerously-skip-permissions"]` in vault dir |
| `run-prompt` | `["claude", "-p", params.prompt, "--dangerously-skip-permissions"]` in vault dir |
| `run-shell` | `[params.cmd_parts[0], *params.cmd_parts[1:]]` in vault dir — pre-validated list, never shell string |

**`run-shell` is a privileged operation.** UI shows warning icon; requires explicit user
acknowledgment before first use. Runs as argument list (no shell metacharacter expansion).

**Validation:** `params.url`: valid HTTP/HTTPS via `urllib.parse.urlparse()`.
`params.topic` / `params.prompt`: max 200 chars, printable ASCII.

### 8.8 Task UI Panel

```
[ ai-agents ▼ ] [● high] [● medium] [● low] [all]  [ + New Task ]   window: ● ACTIVE

  [↕] [●] status     operation         vault          priority  age      actions
  ──────────────────────────────────────────────────────────────────────────────
  [↕] [●] running    wiki-ingest       ai-agents      high      1m ago   [ log ]
      [○] pending    wiki-autoresearch llm-bench       high               [ ✗ ]
      [○] deferred   weekly-lint       ALL             low       waiting  [ promote ] [ ✗ ]
  [▼] [✓] completed  wiki-lint         ALL             low       2h ago   [ re-run ] [ archive ]
       ✓  ai-agents-research    ok   [ log ]
       ✓  llm-benchmarks        ok   [ log ]
       ✗  ml-papers             fail [ log ] [ re-run ]
  [×] [✗] failed     wiki-autoresearch ml-papers       medium    1d ago   [ re-run ] [ archive ] [ log ]
```

**Button semantics:**
- `[ promote ]` — on deferred tasks only; manual override regardless of window state
- `[ ✗ ]` on pending/deferred = cancel (writes `cancelled` event); not shown on terminal states
- `[ archive ]` — soft-delete from default view
- `[ re-run ]` — pre-filled form, new task

**Running indicator:** elapsed time updated every 5s via SocketIO. If `running` for
>10 minutes with no log output: "possibly hung" shown next to elapsed time.

**Cron rows:** `skip_count > 2` shows yellow warning badge.

### 8.9 REST API Surface

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Server health: config, tmux, ttyd, scheduler, task replay |
| GET | `/api/vaults` | List all registered vaults with status |
| POST | `/api/vaults` | Register a vault (name, path, tags) |
| GET | `/api/vaults/{name}/health` | Vault health: path, .obsidian/, readme, last session |
| POST | `/api/sessions` | Spawn a terminal session (vault, type: claude\|bash) |
| DELETE | `/api/sessions/{id}` | Kill a terminal session |
| GET | `/api/tasks` | List tasks (filters: vault, priority, status, limit, offset) |
| POST | `/api/tasks` | Create a task |
| GET | `/api/tasks/{id}` | Get single task state |
| GET | `/api/tasks/{id}/log` | Get task execution log |
| DELETE | `/api/tasks/{id}` | Cancel a pending/deferred task |
| POST | `/api/tasks/{id}/promote` | Promote deferred task to pending |
| POST | `/api/tasks/{id}/archive` | Archive a terminal-state task |
| POST | `/api/tasks/compact` | Trigger manual log compaction |
| GET | `/api/window` | Get current window state |
| POST | `/api/window` | Set window state (action: start\|end\|start_weekly\|end_weekly) |
| GET | `/api/cron` | List cron tasks with last_fired_at, skip_count |
| POST | `/api/config/yaml` | Save system.yaml or schedule.yaml |

All mutating endpoints require `X-Requested-With: resman` header. Rejected with HTTP 403
without it. SPA sends this header via a fetch wrapper on all requests.

---

## 9. Cron Tasks

Fired by `GeventScheduler`. Fires only if `is_window_active()` true at scheduled time.
If not active: `cron_skipped` event written, task does not queue.

ObsidianPush runs on a separate 60s GeventScheduler job — it fires regardless of window
state (vault health should always be current).

**Skip tracking:** `last_fired_at` and `skip_count` tracked per task. `skip_count > 2`
triggers SocketIO event and UI warning badge.

**ALL-vault cron tasks:** same parent/child expansion as manual tasks.

---

## 10. Plugin Command Integration

```bash
claude plugin marketplace add AgriciDaniel/claude-obsidian
claude plugin install claude-obsidian@claude-obsidian-marketplace
```

### 10.1 Plugin Command Map (`plugin_commands.py`)

```python
TESTED_VERSION_RANGE = ("1.0.0", "2.0.0")  # inclusive lower, exclusive upper

LINT = "/claude-obsidian:wiki-lint"
UPDATE_HOT_CACHE = "update hot cache"

def autoresearch(topic: str) -> str:
    return f"/claude-obsidian:autoresearch {topic}"

def ingest_canvas(url: str) -> str:
    return f"/claude-obsidian:wiki-ingest {url} --canvas"
```

**Startup version check:** reads installed version via `claude plugin list --json`. Warns
in startup report and browser banner if outside `TESTED_VERSION_RANGE`. Does not block.

### 10.2 Quick Command Palette (Option A)

| Button | Operation | Params prompt |
|---|---|---|
| Ingest URL | `wiki-ingest` | URL input |
| Auto Research | `wiki-autoresearch` | Topic input |
| Lint Vault | `wiki-lint` | None |
| Update Hot Cache | `wiki-update-hot-cache` | None |
| + Claude Session | spawn ttyd claude session | None |
| + Shell | spawn ttyd shell session | None |
| Open in Obsidian | `obsidian_cmd vault_path` | None |

---

## 11. System Documentation

| File | Content |
|---|---|
| `docs/overview.md` | What resman is, quick start, ttyd install, Obsidian-push setup |
| `docs/vaults.md` | Vault conventions, structure, claude-obsidian schema, `_resman/` gitignore note |
| `docs/tasks.md` | Task system: priorities, scheduling, window gating |
| `docs/plugin-commands.md` | Plugin command cheatsheet |

---

## 12. Security and Safety

- Bind to `127.0.0.1` only
- **CSRF:** `X-Requested-With: resman` on all mutating endpoints; HTTP 403 without it
- Path traversal: `os.path.normpath` + `startswith(allowed_root)` on all file serving
- YAML: `yaml.safe_load()` + dict validation before saving
- HTML injection: `esc()` on all user-controlled values before DOM insertion
- Vault/task names: `[a-zA-Z0-9_-]` only
- `params.url`: validated as HTTP/HTTPS before use
- `params.topic`, `params.prompt`: max 200 chars, printable ASCII
- `run-shell`: argument list via `subprocess.run()` — never `sh -c`; requires UI acknowledgment
- File writes: reject over 1 MB
- tmux: isolated socket (`resman`), never shares with user's personal tmux
- `scan_paths`: depth capped at 2; reject paths resolving to filesystem roots
- **Subprocess construction:** all operations use argument-list form — shell never invoked
- **Obsidian push writes:** wrapped in try/except OSError — never crash the server on write failure

---

## 13. Error Handling

| Scenario | Behavior |
|---|---|
| system.yaml missing on startup | Fail loudly; print error + browser banner |
| system.yaml invalid YAML | Same |
| tasks.jsonl corrupt | Skip bad lines; warn; continue replay |
| tmux not installed | Fail loudly on startup |
| ttyd not installed | Warn in startup report; disable terminal endpoints (503); rest of server works |
| ttyd process crash mid-session | SessionMonitor detects within 5s; SocketIO `session_crashed` event; toast in browser |
| No free port in ttyd range | `StartupError`; log clearly; refuse to spawn session |
| `TmuxManager.create_session()` fails | Raise `TmuxSessionError`; emit `session_error` SocketIO event |
| Task execution non-zero exit | `failed` event; stderr in task log; red dot |
| Vault path missing at dispatch | Immediate `failed` event with reason |
| Config save fails validation | HTTP 400 with specific error; never write file |
| Config save fails disk write | HTTP 500 with detail |
| Cron string invalid | Reject at load; surface in startup report and browser banner |
| budget.json missing or corrupt | Reset to `between`; log warning; never crash |
| claude-obsidian version out of range | Warn; browser banner; do not block operation |
| Obsidian push write fails | Log warning; continue; non-fatal |

---

## 14. Phased Implementation

Build in this order: **Obsidian-push first (Phase 1), terminals second (Phase 2).**
Phase 1 delivers value immediately (vault health in graph view) with minimal complexity.
Phase 2 is independently addable. Phase 3 adds the task queue. Phase 4 polishes.

### Phase 0 — Pre-Implementation Validation

Before writing code:
1. Open a vault in Obsidian. Manually write `_resman/status.md`. Confirm Obsidian detects
   the new file without manual restart (within a few seconds via chokidar file watch).
2. Write a loop that updates the file every 60s for 10 minutes. Confirm no sync conflicts
   (especially if vault is iCloud or Obsidian Sync backed).
3. Add `_resman/` to the vault's `.gitignore`. Confirm git doesn't track it.
4. Install ttyd. Run: `ttyd --port 7681 --writable bash`. Open `http://127.0.0.1:7681`
   in browser. Confirm the terminal works. Type `ls`. Done.

If step 1-3 fail: reconsider Obsidian-push approach before investing further.
If step 4 fails: check ttyd install. This is the terminal foundation.

### Phase 1 — Obsidian Push + Core Shell

Goal: vault health visible in Obsidian graph view. Working Flask server. No terminals yet.

1. `config/system.yaml.example` and loader; VaultRegistry with `.obsidian/` validation
2. Flask + SocketIO server; EventBus; `plugin_commands.py` with version check
3. `TmuxManager` with `reconcile()` on restart
4. `/api/health` endpoint; startup report (config, tmux, ttyd, scheduler, tasks, plugin)
5. **`obsidian_push.py`**: `push_vault_status()` + GeventScheduler 60s cron
6. Left sidebar: registered vaults + status dots; filter bar (search, tag, status)
7. Window status bar: manual sync controls + tmux heuristic; color coding
8. Vault README viewer/editor (Option C)
9. YAML config editor with validation (Option J)
10. `X-Requested-With: resman` CSRF on all mutating endpoints

**Phase 1 done when:** open resman, three vaults in sidebar, dots showing correct colors,
`_resman/status.md` visible in Obsidian graph view for each vault, updates within 60s.

### Phase 2 — Terminal Sessions (ttyd)

Goal: click a vault → browser terminal attached to a real tmux session.

1. `session_manager.py`: `SessionManager.spawn()` as specified in Section 7.3
2. `_find_free_port()` with SO_REUSEADDR and range scan
3. `SessionMonitor` greenlet: polls `proc.poll()` every 5s, emits `session_crashed`
4. `[▶]` mini-menu in sidebar (Claude or Shell); spawns ttyd session
5. Terminal tab with iframe embedding: `<iframe src="http://127.0.0.1:{port}">`
6. Option F tabs: multiple terminal tabs per vault; each tab is an independent ttyd session
7. `DELETE /api/sessions/{id}`: `proc.terminate()` → wait 3s → `proc.kill()`
8. Orphaned session display on startup (tmux sessions not in registry)
9. ttyd unavailable graceful degradation: grayed-out tab + install message

**Phase 2 done when:** open resman, click "Open Claude" for a vault, terminal appears in
browser, typing works, tmux session is real (`tmux ls` shows it), close tab → ttyd process
gone, tmux session still alive.

### Phase 3 — Task Queue

Goal: queue, run, and track operations on vaults.

1. Task creation form (all operations; `run-shell` with acknowledgment gate)
2. `TaskManager`: state machine, JSONL event log with crash-consistency, in-memory index,
   dispatch mutex
3. Task UI panel with column semantics, filter bar
4. Window gating: defer when not active, promote on `window_activated`
5. Task re-run with pre-filled form
6. Task compaction: manual trigger + auto at 50k lines

### Phase 4 — ALL-Vaults, Cron, and Polish

Goal: cross-vault operations, automation, full lifecycle management.

1. Parent/child task model: `dispatch_started`, EventBus aggregation
2. Expandable parent rows in task UI
3. `GeventScheduler` cron with `schedule.yaml`; cron string validation
4. Cron skip-when-inactive + `cron_skipped` event; `skip_count` warning badge
5. Unregistered vault discovery (depth ≤ 2) and click-to-register flow
6. `tools/new-vault.sh`; wizard with progress view
7. Quick command palette (Option A)
8. Drag-to-resize terminal + README split (Option I)
9. Audit event log viewer (Option G)
10. System docs/ browser in Docs tab
11. JSONL compaction UI in Config panel

---

## 15. Testing

### Critical (must pass before Phase 2 merge)

- `tasks.jsonl` replay: partial final line, duplicate task_id, unknown event type,
  out-of-order timestamps, events for nonexistent parent, `started` with no terminal event
  (→ verify replays as `interrupted`)
- `TmuxManager.create_session()` failure: verify `TmuxSessionError` raised, SocketIO
  `session_error` emitted
- SessionManager dispatch mutex: concurrent spawn requests do not double-allocate a port
- CSRF: mutating endpoints reject without `X-Requested-With: resman`
- `run-shell` subprocess: shell never invoked; argument list passed verbatim
- ttyd unavailable: startup report shows `ttyd: MISSING`; `/api/sessions` returns 503;
  rest of server starts and responds normally

### High (must pass before Phase 3 merge)

- EventBus: `window_activated` promotes deferred high/medium; `low` stays deferred
- `between → active` only via explicit "Start window now" with duration
- GeventScheduler: cron tick with window inactive writes `cron_skipped`; no deadlock
- `budget.json` corruption: missing → `between`; invalid JSON → `between`; past
  `window_ends_at` → `between`
- `is_window_active()`: false immediately when `window_ends_at` passes (no 60s lag)
- SessionMonitor: kills ttyd, emits `session_crashed` within 5s of proc exit
- `find_free_port()`: skips in-use OS ports and in-registry ports; raises on range exhaustion
- ObsidianPush: write failure is non-fatal; server continues; subsequent push retries

### Medium (must pass before Phase 4 merge)

- ALL-vault child count integrity: `dispatch_started` + N `child_created` + startup mismatch warning
- Vault discovery: scan_paths depth ≤ 2; filesystem root paths rejected
- Cron string validation: invalid → HTTP 400; APScheduler never receives invalid trigger
- `TmuxManager.reconcile()`: prior sessions re-registered; orphaned sessions shown in sidebar
- Session cleanup: `DELETE /api/sessions/{id}` kills ttyd process; tmux session survives

---

## 16. Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Terminal backend | ttyd (C binary) | Handles PTY, xterm.js, WebSocket — eliminates ~400 lines of custom PTY code |
| Vault health visibility | Push `_resman/status.md` into each vault | Health visible inside Obsidian graph view without opening resman; ambient feedback |
| Implementation order | Obsidian-push first, terminals second | Push delivers value alone; lower risk; terminals are additive |
| Session management | SessionManager with port registry | ttyd processes need ports; explicit registry prevents races |
| SessionMonitor | Greenlet polling proc.poll() every 5s | Detects unexpected ttyd crashes; no polling overhead at scale |
| Port range | 7680-7999 (configurable) | 320 ports; far more than 3-5 vaults ever need |
| ttyd unavailability | Graceful degradation (503 on terminal endpoints) | Obsidian-push and task queue work without ttyd |
| Deployment | Local only, 127.0.0.1 | Same machine as Obsidian; no remote access needed |
| CSRF protection | `X-Requested-With: resman` header | Sufficient for localhost; no token complexity |
| Window budget | Manual time-sync | Matches how Claude Code windows work |
| ALL-vaults tasks | Parent + N child + EventBus aggregation | Per-vault status, re-run, log visibility |
| Plugin install | User-level global, once | Available in all vault sessions |
| Task storage | JSONL event sourcing + compaction at 50k lines | Append-only, greppable, no schema migrations |
| Server stack | Flask + SocketIO + eventlet + GeventScheduler | Concurrent WebSocket; avoids APScheduler deadlock |
| Frontend | Vanilla JS + CDN | No build step; iframes for terminals (ttyd handles xterm.js) |
| Subprocess calls | Always argument-list form | Eliminates shell injection |
| Plugin commands | Centralized in plugin_commands.py | Single source of truth; startup version check |
| Operation names | `wiki-*` for plugin ops; `run-prompt`/`run-shell` for ad-hoc | Consistent namespace |
| Tools path | Absolute via RESMAN_ROOT | Relative `../tools/` breaks relative to vault paths |
| `injest.sh` | Renamed to `ingest.sh` | Fix typo |
| Orphaned tmux sessions | Show warning dot; never auto-kill | User may have live work in them |
| ObsidianPush cadence | 60s regardless of window state | Vault health should always be current |
