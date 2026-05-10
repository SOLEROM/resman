# resman — Research Vault Manager
## System Design Plan v3

> Refined from plan2.md after full CEO/Design/Eng/DX auto-review pipeline.
> Key changes from v2: CSRF protection added, session registry added, JSONL
> crash-consistency hardened, operation namespace normalized, status dot
> priority rule defined, startup quick-start added, cron skip visibility added,
> tmux heuristic window fallback added, ../tools path bug fixed, event bus
> added to break circular coupling, compaction strategy defined.

---

## 0. Quick Start

```bash
# 1. Clone and install
git clone <resman-repo> resman
cd resman
pip install -r control-plane/requirements.txt

# 2. Configure
cp config/system.yaml.example config/system.yaml
# Edit config/system.yaml: add at least one vault path

# 3. Run
python control-plane/server.py

# 4. Open browser
# http://127.0.0.1:5090
```

On startup the server prints a structured startup report:

```
resman starting...
  config:    OK (3 vaults loaded)
  tmux:      OK (socket: resman)
  scheduler: OK (2 cron tasks)
  tasks:     OK (replayed 142 events, 1 bad line skipped)
  plugin:    OK (claude-obsidian v1.4.2, compatible)
  server:    http://127.0.0.1:5090
```

If any component fails (tmux not found, YAML parse error, corrupt tasks.jsonl), the server logs a clear error and refuses to start cleanly — it does not silently start with broken state.

---

## 1. Purpose

**resman** is a local web-based command-and-control panel for managing multiple Obsidian research vaults on one machine. Each vault is an independent research project powered by the `claude-obsidian` plugin. resman provides a single browser dashboard to:

- View and navigate all research vaults, each defined by its own path in system.yaml
- Launch Claude Code and bash sessions inside any vault
- Trigger plugin operations (ingest, lint, autoresearch, canvas) on any vault
- Manage a prioritized task queue gated on an active Claude window
- Schedule recurring housekeeping tasks via cron
- Edit system configuration and vault docs in the browser

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
│   │   ├── config_manager.py        # load/save/reload system.yaml; emits config_reloaded event
│   │   ├── vault_registry.py        # vault list, metadata, discovery, .obsidian/ validation
│   │   ├── vault_runtime.py         # launch Claude/bash sessions; owns session registry
│   │   ├── task_manager.py          # task queue, state machine, parent/child, dispatch mutex
│   │   ├── window_state.py          # manual window sync + tmux heuristic + gating logic
│   │   ├── scheduler.py             # APScheduler (GeventScheduler) cron task firing
│   │   ├── tmux_manager.py          # tmux session lifecycle; reconcile on restart
│   │   ├── tmux_output_streamer.py  # one greenlet per session; reads tmux output, emits SocketIO
│   │   ├── pty_bridge.py            # thin WebSocket ↔ stream protocol adapter
│   │   ├── event_bus.py             # lightweight internal pub/sub; breaks circular coupling
│   │   ├── plugin_commands.py       # centralized claude-obsidian command map (single source of truth)
│   │   ├── routes.py                # REST API (see Section 8.9)
│   │   └── websocket_handlers.py    # Socket.IO events
│   ├── templates/
│   │   └── index.html               # SPA shell
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

**RESMAN_ROOT:** the server detects its own install path at startup (`Path(__file__).parent.parent`) and exposes it as `RESMAN_ROOT`. All references to `tools/` use `RESMAN_ROOT / "tools"` — never relative paths like `../tools/`.

### 3.2 Component Map

```
Browser (xterm.js + SPA)
        ↕  WebSocket + REST (CSRF-protected)
Flask + SocketIO (server.py)
    ├── EventBus          — internal pub/sub; decouples WindowState ↔ TaskManager
    ├── VaultRegistry     — vault list from system.yaml + .obsidian/ validation
    ├── VaultRuntime      — start/stop sessions; owns SessionRegistry
    ├── TaskManager       — priority queue, dispatch mutex, parent/child tasks
    ├── WindowState       — manual sync + tmux heuristic; emits window_activated / window_deactivated
    ├── Scheduler         — GeventScheduler cron, fires tasks if window active
    ├── TmuxManager       — tmux session lifecycle; reconcile() on server restart
    ├── TmuxOutputStreamer — one greenlet per session; pipes tmux output to SocketIO
    └── PtyBridge         — thin WebSocket protocol adapter; kills streamers on disconnect
```

**EventBus decoupling:** `WindowState` never imports `TaskManager`. When the window activates, `WindowState` emits `window_activated` on the `EventBus`. `TaskManager` subscribes and promotes deferred tasks. This eliminates the circular import.

---

## 4. Configuration System

### 4.1 `config/system.yaml` Schema

```yaml
app:
  host: 127.0.0.1
  port: 5090
  # resman uses its own isolated tmux socket so it never interferes with your personal tmux.
  tmux_socket: resman
  tmux_prefix: "rsm-"
  scrollback_limit: 10000
  # --dangerously-skip-permissions is required for unattended background claude -p invocations.
  # Interactive sessions (terminal tab) do NOT use this flag — Claude manages its own prompts.
  claude_cmd: "claude --dangerously-skip-permissions"
  # Change to "obsidian" or the full path to your Obsidian binary if not using Flatpak.
  obsidian_cmd: "flatpak run md.obsidian.Obsidian"
  # Path to resman's own tools/ directory. Auto-detected at startup; override if needed.
  # tools_path: /opt/resman/tools

window_budget:
  weekly_start: "Monday 09:00"  # user-defined weekly period start
  weekly_end:   "Sunday 23:00"  # user-defined weekly period end

# Each vault has its own explicit path — vaults may be anywhere on the filesystem.
# There is no required common root folder.
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

# Optional: directories to scan for unregistered vaults (folders with .obsidian/).
# Remove or leave empty to disable discovery. Max scan depth: 2 levels.
scan_paths:
  - /data/research
  - /home/user/projects
```

**system.yaml.example** ships in the repo with all fields present and the same inline comments shown above. Users copy it; they never write YAML from scratch.

### 4.2 `config/schedule.yaml` Schema

```yaml
cron_tasks:
  - name: weekly-lint-all
    cron: "0 8 * * 0"        # every Sunday 08:00 — validated by APScheduler at load time
    vault: ALL
    operation: wiki-lint
    priority: low
    # Tracked automatically by scheduler; not user-editable:
    # last_fired_at: null
    # skip_count: 0

  - name: daily-hot-cache-update
    cron: "0 22 * * *"
    vault: ALL
    operation: wiki-update-hot-cache
    priority: low
```

Cron strings are validated with `CronTrigger.from_crontab()` before saving. Invalid strings return an HTTP 400 with the parse error surfaced inline in the YAML editor.

### 4.3 `config/budget.json` Schema

Written and read by the server. Edited only through the web UI window controls. **Write order: always write the file first, then update in-memory state.** Never the reverse.

```json
{
  "window_state": "active",
  "window_started_at": "2026-05-05T10:00:00",
  "window_ends_at":    "2026-05-05T15:00:00",
  "weekly_synced_at":  "2026-05-05T09:00:00",
  "weekly_ends_at":    "2026-05-11T23:00:00"
}
```

`window_state` values: `active` | `between` | `ended`

Startup validation: if the file is missing, create it with `window_state: "between"`. If it contains invalid JSON, log an error and reset to `between`. Never crash on corrupt budget.json.

### 4.4 Live Config Editing

The web UI includes a YAML editor for both `system.yaml` and `schedule.yaml` (WebTUI Option J pattern). All saves are atomic: write to `.tmp` then `os.replace()`. Before the atomic commit, the server validates:

- YAML parses without error
- Result is a dict
- Required vault fields present (`name`, `path`)
- Cron strings parse with `CronTrigger.from_crontab()`
- File size ≤ 1 MB

If any check fails, return HTTP 400 with the specific error — never write the file. On successful save, `config_manager.py` emits `config_reloaded` on the `EventBus`. Subscribers (`VaultRegistry`, `Scheduler`) re-derive their state from the new config via the `get_vault(name)` accessor — they do not cache the config dict directly.

---

## 5. Web TUI (Control Plane)

### 5.1 Server Stack

```
Python:   Flask + Flask-SocketIO + eventlet
Frontend: vanilla JS + xterm.js (CDN) + Socket.IO (CDN) — no build step
Terminal: TmuxOutputStreamer greenlets + PtyBridge protocol adapter
```

Use `eventlet` monkey-patch. Never use threading mode — it cannot handle concurrent WebSocket connections. **APScheduler must use `GeventScheduler` (eventlet-compatible), not `BackgroundScheduler`.** Using `BackgroundScheduler` causes a deadlock when the cron callback spawns a tmux session (eventlet-patched subprocess module blocks the scheduler thread).

### 5.2 WebTUI Option Modules to Enable

Adapt directly from `.ref/agent_ccpanBuilder/scripts/options/`:

| Option | Module | Purpose in resman |
|---|---|---|
| **A** | `opt_a_commands` | Per-vault quick command palette (ingest, lint, autoresearch, open Obsidian) |
| **C** | `opt_c_markdown` | Markdown viewer/editor for vault README and system docs/ |
| **F** | `opt_f_tabs` | Multiple xterm.js terminals per vault (bash + claude sessions) |
| **G** | `opt_g_eventlog` | Append-only JSONL audit trail of all operations |
| **I** | `opt_i_splitter` | Drag-to-resize terminal + README panel side by side |
| **J** | `opt_j_config_editor` | Live YAML editor for system.yaml and schedule.yaml |

### 5.3 UI Layout

```
┌────────────────────┬─────────────────────────────────────────────────┐
│                    │  [ Terminal ] [ Docs ] [ Tasks ] [ Config ]      │
│  Vault Sidebar     ├─────────────────────────────────────────────────┤
│  [search/filter]   │                                                   │
│  ● ai-agents  [▶] │  xterm.js terminal   OR   Markdown panel         │
│  ○ llm-bench  [▶] │                      OR   Task queue panel        │
│  ─ unregistered   │                      OR   YAML config editor      │
│    found-vault    │                                                   │
│                   │                                                   │
│  [+ New Vault]    ├─────────────────────────────────────────────────┤
│  [⚙ Config]      │  Window: ● ACTIVE  ends in 3h 12m   [ sync ▼ ]  │
└────────────────────┴─────────────────────────────────────────────────┘
```

**Left Sidebar:**
- Filter bar at top: search by name, filter by tag (multi-select chips), filter by status (dropdown: any / active session / has tasks / has error). Present in Phase 1 — a sidebar with 20+ gray dots is useless without it.
- Registered vaults: name, status dot (single color per priority rule in 5.4), tags (dimmed)
- `[▶]` button: opens a mini-menu with two options — "Open Claude" and "Open Shell." No hidden default. Never guess.
- Clicking the vault name selects it and switches the main panel to that vault's Terminal tab
- Unregistered vaults (found via `scan_paths` but not in system.yaml): shown below a divider with a `[+ Register]` button — only appears if `scan_paths` is configured
- `[+ New Vault]` — opens scaffold wizard
- `[⚙ Config]` — opens config YAML editor in main panel
- Empty state (zero registered vaults): sidebar shows "Add your first vault to get started →" with arrow pointing to `[+ New Vault]`

**Window Status Bar (bottom of screen):**
- Always visible; fixed height; does not overlap terminal content (layout uses flex column)
- Shows current window state, time remaining, and sync controls
- Color coding: green bar = `active`, gray bar = `between`, red bar = `ended`
- When in the final 60 seconds of an active window: label changes to "ending in Xs..." (yellow text)
- `[ sync ▼ ]` dropdown opens upward (bar is at bottom of screen)

**Center Tabs:**
- **Terminal** — tmux-backed xterm.js; toolbar with session tabs showing `[+ Bash]`, `[+ Claude]`, `[Open Obsidian]`; each tab labeled with session type and creation time
- **Docs** — markdown viewer/editor for vault README or system docs/
- **Tasks** — task queue for this vault (or all vaults if no vault selected)
- **Config** — YAML editors for system.yaml and schedule.yaml

### 5.4 Vault Status Dot — Priority Rule

A vault has exactly one dot color at any time. When multiple states apply simultaneously, the highest-priority color wins. Hover the dot to see all active flags in a tooltip.

| Priority | Color | Condition |
|---|---|---|
| 1 (highest) | Red | Last task failed |
| 2 | Yellow | A task is currently running |
| 3 | Green | Has an active tmux session |
| 4 | Blue | Obsidian is open for this vault |
| 5 (lowest) | Gray | Idle / none of the above |

**Dot spec:** 10px filled circle, CSS class `vault-dot-{color}`. No light/dark mode variant in Phase 1. Hover tooltip: `"{vault-name}: {flag1}, {flag2}, ..."` listing all true conditions.

---

## 6. Window State System

The window state is manually managed by the user via the window status bar. resman does not auto-detect when a Claude Code window starts or ends.

**Tmux heuristic fallback (inferred state only):** If `window_state == "between"` and a tmux session matching `rsm-*-claude` is found alive on the resman socket, the server shows an amber indicator in the status bar: "Claude session detected — did you forget to start the window?" This is informational only and does not change the authoritative `window_state`. The user can click "Start window now" to act on it.

### 6.1 Window States

| State | Meaning | Task execution |
|---|---|---|
| `active` | A Claude window is currently open and usable | Tasks run |
| `between` | Previous window ended; next has not started | Tasks queue as deferred |
| `ended` | Weekly period has ended | Tasks queue as deferred |

### 6.2 Sync Controls (Window Status Bar)

The `[ sync ▼ ]` dropdown exposes:

| Control | Action |
|---|---|
| **Start window now** | Prompts for duration (required, 1–12 hours). Sets state=active, window_started_at=now, window_ends_at=now+duration |
| **End window now** | Sets state=between, window_ends_at=now |
| **Start weekly period** | Sets weekly_synced_at=now, weekly_ends_at = now + configured period |
| **End weekly period** | Sets state=ended |

Duration is required on "Start window now" — no open-ended windows. Maximum 12 hours. If the active window runs past its `window_ends_at` (e.g., server was stopped and restarted later), the status bar shows "Window overrun by Xh — end it?" as a persistent prompt.

### 6.3 Window Transition Rules

- `active → between`: user clicks "End window now", OR `window_ends_at` is reached per `is_window_active()` check
- `between → active`: user explicitly clicks "Start window now" and enters a duration — this is the **only** path to `active`. A task created while `between` goes to `deferred` immediately.
- `active → ended`: user clicks "End weekly period"
- `ended → active`: user clicks "Start weekly period" then "Start window now"

**`is_window_active()` is a function, not a cached field.** It compares `datetime.utcnow()` against `window_ends_at` inline on every call. There is no 60-second lag on the gate check. The 60-second server poll only exists to emit SocketIO status-bar updates to the browser — it never sets authoritative state.

On window state flip (either direction), emit a `window_state_changed` SocketIO event to all connected clients. The browser status bar updates immediately. If tasks are being deferred due to a flip to `between`, show a dismissable banner: "Window ended — new tasks are now deferred."

### 6.4 Task Gating

- **While `active`:** all tasks run normally
- **While `between` or `ended`:** new tasks are queued as `deferred`; the task list shows "waiting for next window"
- **On window activation** (`window_activated` event via EventBus): deferred tasks with priority `high` or `medium` are promoted to `pending` and begin running; `low` priority tasks remain deferred until the user promotes them manually

---

## 7. Vault Management

### 7.1 Vault Discovery

On startup the server does three things:

1. Load vaults from `system.yaml` — these are the registered vaults. Each vault has its own explicit `path`.
2. For each registered vault, validate: path exists on disk AND contains `.obsidian/`. Show distinct warnings: "path not found" (gray dot + warning icon) vs. "not an Obsidian vault" (gray dot + different warning icon).
3. If `scan_paths` is configured, scan each listed directory (max depth: 2) for subfolders containing `.obsidian/` — these are discovered but unregistered vaults.

Vault health check: clicking a vault's warning icon opens a modal showing:
- Path exists on disk: ✓/✗
- `.obsidian/` present: ✓/✗
- Readme file found: ✓/✗
- Last active session: timestamp or "never"
- Last completed task: timestamp or "none"

Discovered-but-unregistered vaults appear in the sidebar below a divider. Clicking `[+ Register]` opens a form (name, tags, confirm path) and appends to `system.yaml`. If `scan_paths` is empty or absent, the divider does not appear.

### 7.2 Create New Vault

The `[+ New Vault]` wizard:

1. User enters vault name (`[a-zA-Z0-9_-]` only), target path, and one-sentence purpose
2. Wizard shows a progress view while running `tools/new-vault.sh <name> <target_path>`:
   - Step 1/3: Cloning template... (git clone — may take 30s)
   - Step 2/3: Running setup script...
   - Step 3/3: Registering vault...
   - On error at any step: show the error output from the script, offer "Retry" and "Cancel." Do not close the wizard silently on failure.
3. On success: wizard closes, vault appears in sidebar, a terminal tab opens automatically in the new vault
4. A dismissable in-terminal banner shows: "Run `/wiki` in this Claude session to scaffold the wiki structure"

### 7.3 Launch Sessions

**Session Registry:** `VaultRuntime` owns a `SessionRegistry` — a dict keyed by `session_id` (not vault name). Each entry stores: vault name, session type (claude/bash/task), tmux session name, pid, creation time. Multiple sessions of the same type per vault are supported. Session names use a monotonic counter: `rsm-<vault-name>-<type>-<n>`.

**On server restart:** `TmuxManager.reconcile()` calls `tmux -S <socket> ls -F "#{session_name}"`, parses existing session names, and rebuilds the `SessionRegistry`. VaultRuntime accepts no requests until reconcile completes.

**On `TmuxManager.create_session()` failure:** raises `TmuxSessionError` (typed exception). `VaultRuntime` catches it and emits a `session_error` SocketIO event to the browser with a human-readable message. The browser shows a toast: "Failed to open session: {reason}."

**Open Claude Code in vault:**
- Creates tmux session: `rsm-<vault-name>-claude-<n>`
- Runs: `cd <vault_path> && <claude_cmd>`

**Open bash shell in vault:**
- Creates tmux session: `rsm-<vault-name>-shell-<n>`
- Runs: `cd <vault_path> && bash`

**Open Obsidian for vault:**
- Fires: `<obsidian_cmd> <vault_path>` (fire-and-forget, no tmux)

**On client WebSocket disconnect:** `PtyBridge` looks up the greenlet for each session the client was subscribed to and calls `greenlet.kill()`. This prevents FD leaks and orphan streamers.

### 7.4 Tools

All tools in `tools/` take vault path as first argument — vault-agnostic. Always referenced via `RESMAN_ROOT / "tools"` (absolute path), never as `../tools/`.

| Tool | Invocation | Purpose |
|---|---|---|
| `ingest.sh` | `$RESMAN_ROOT/tools/ingest.sh <vault_path> <url> [--can]` | Fetch URL and ingest into vault via `claude -p` |
| `new-vault.sh` | `$RESMAN_ROOT/tools/new-vault.sh <name> <target_path>` | Clone template to target path and register in system.yaml |

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
| `parent_id` | UUID of parent task (for ALL-vault child tasks), or `null` |

**Operation namespace:** all plugin operations use the `wiki-` prefix. Ad-hoc execution uses `run-prompt` (sends a prompt to `claude -p`) and `run-shell` (runs a shell command in the vault directory). See Section 8.7 for the full operation-to-execution mapping.

### 8.2 Task Storage — JSONL Event Sourcing

Tasks are stored in `config/tasks.jsonl` as an append-only event log. Each line is one event:

```json
{"ts": "2026-05-05T10:01:00Z", "event": "created",   "task_id": "t-abc", "data": {<full task fields>}}
{"ts": "2026-05-05T10:01:05Z", "event": "started",    "task_id": "t-abc"}
{"ts": "2026-05-05T10:08:33Z", "event": "completed",  "task_id": "t-abc", "exit_code": 0}
```

| Event | When emitted |
|---|---|
| `created` | Task first queued; payload contains all task fields |
| `started` | Execution begins |
| `completed` | Execution finished successfully |
| `failed` | Execution finished with error |
| `deferred` | Task moved to deferred queue (window not active) |
| `promoted` | Deferred task promoted to pending |
| `updated` | Params or priority changed (re-run edit) |
| `child_created` | Parent task created a child task for one vault |
| `dispatch_started` | Parent ALL-vault task about to create children; includes `expected_child_count` |
| `cron_skipped` | Cron task tick fired but window was not active; includes `scheduled_at` |
| `archived` | Task marked archived; excluded from UI by default but preserved in log |

**Crash-consistency:** Every line is wrapped in `try/except JSONDecodeError` during replay. Bad lines are logged with byte offset and skipped — replay does not abort. On startup, if `tasks.jsonl` does not end with `\n`, the partial final line is truncated and a warning is emitted. A startup integrity check counts events vs. expected terminal states and warns on mismatch.

**Compaction:** When `tasks.jsonl` exceeds 50,000 lines (or on explicit user request via `/api/tasks/compact`), the server runs an offline compaction procedure:

1. Replay the full log into in-memory state
2. For all tasks in a terminal state (`completed`, `failed`, `archived`) older than 90 days: write a single `snapshot` event capturing their final state
3. Drop all pre-snapshot events for those tasks
4. Rewrite `tasks.jsonl` with: snapshot events first, then all remaining events

Current task state is derived by replaying events for a given `task_id` and taking the latest status event. The server builds an in-memory state index at startup.

Task execution output is written to `config/task-logs/<task_id>.log`.

### 8.3 Task States

```
pending ──────────► running ──► completed ──► [archived]
   ▲                   │
   │ (promoted)        └──────► failed ──────► [archived]
deferred◄──────────────────────────────── (window not active)
```

The `archived` state is a soft-delete: the task is excluded from the default task list view but preserved in the JSONL log for audit purposes. Users can archive tasks manually; the compaction procedure archives terminal-state tasks older than 90 days automatically.

### 8.4 Priority and Window Gating

| Priority | Window active | Window between/ended |
|---|---|---|
| `high` | runs immediately | deferred; promoted on next window activation |
| `medium` | runs as background | deferred; promoted on next window activation |
| `low` | runs as background | deferred; user must manually promote |

### 8.5 ALL-Vaults Tasks (Parent/Child)

When a task targets `vault: ALL`:

1. A **parent task** is created with `vault: ALL`
2. Before dispatching children, write a `dispatch_started` event with `expected_child_count: N`
3. On dispatch, `TaskManager` creates one **child task** per registered vault inside a dispatch lock (`eventlet.semaphore.Semaphore(1)`), each with `parent_id` set. Each child creation writes a `child_created` event atomically.
4. If the server crashes mid-loop (after child 3 of 7), the startup integrity check detects the mismatch between `expected_child_count` and actual child count and surfaces a warning: "Partial dispatch detected for task {id} — {N} of {M} children created."
5. Children run independently (separate tmux sessions, separate log files)
6. Parent state aggregates via the EventBus: when a child emits `completed` or `failed`, the child's TaskManager handler emits `child_state_changed` on the EventBus. The parent's subscriber re-aggregates:
   - `running` if any child is running
   - `failed` if any child failed (even if others completed)
   - `completed` only when all children completed successfully

The task UI shows the parent row as expandable. Clicking it reveals each child's individual status, log link, and a re-run button.

### 8.6 Task Re-Run

Re-running a completed or failed task opens the task creation form pre-filled with the original task's params. The user can edit any field before submitting. Submitting creates a new task (new UUID, new event chain) — it does not mutate the original.

### 8.7 Operation-to-Execution Mapping

All shell commands are constructed as **argument lists** (not interpolated strings) and executed via `subprocess.run([...], ...)` — the shell is never invoked directly. This eliminates shell injection from `params` fields.

All paths use `RESMAN_ROOT / "tools"` (absolute path resolved at startup).

Plugin commands are defined in `plugin_commands.py` — the single source of truth for claude-obsidian invocations. On startup, `plugin_commands.py` checks the installed claude-obsidian version and warns if it does not match the tested version range.

| Operation | Execution |
|---|---|
| `wiki-ingest` | `[RESMAN_ROOT/tools/ingest.sh, vault_path, params.url]` |
| `wiki-lint` | `["claude", "-p", plugin_commands.LINT, "--dangerously-skip-permissions"]` in vault dir |
| `wiki-autoresearch` | `["claude", "-p", plugin_commands.autoresearch(params.topic), "--dangerously-skip-permissions"]` in vault dir |
| `wiki-update-hot-cache` | `["claude", "-p", plugin_commands.UPDATE_HOT_CACHE, "--dangerously-skip-permissions"]` in vault dir |
| `run-prompt` | `["claude", "-p", params.prompt, "--dangerously-skip-permissions"]` in vault dir |
| `run-shell` | `[params.cmd_parts[0], *params.cmd_parts[1:]]` in vault dir — `cmd_parts` is a pre-validated list, never a shell string |

**`run-shell` (formerly `bash-cmd`) is a privileged operation.** It is marked in the UI with a warning icon and requires an explicit user acknowledgment before first use. It runs the command as a list (via `execvp`, no shell), in the vault directory, with no shell metacharacter expansion. This is not arbitrary shell execution — it is a pre-parsed argument list. The security section's claim of "no arbitrary command execution" now refers to "no shell string execution."

**`params.url` validation:** must parse as a valid HTTP/HTTPS URL via `urllib.parse.urlparse()`. Reject URLs with non-http schemes.

**`params.topic` validation:** max 200 characters, printable ASCII only.

Session naming: `rsm-<vault-name>-task-<task_id_short>`

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

**Column semantics legend:**
- Column 1 `[↕]`/`[▼]`/`[×]`: expand-toggle icon — `[↕]` = expandable (ALL-vault parent), `[▼]` = expanded, `[×]` = no children / not expandable. Only present on parent tasks.
- Column 2 `[●]`/`[○]`/`[✓]`/`[✗]`: status icon — filled circle = running, empty circle = pending/deferred, checkmark = completed, X = failed.

**Button disambiguation:**
- `[ promote ]` — appears only on `deferred` tasks. Moves the task to `pending` regardless of window state (manual override). Does not appear on `pending` tasks.
- `[ run ]` does not exist in this UI. Running starts automatically when the task is promoted and the window is active.
- `[ ✗ ]` on pending/deferred = **cancel** (removes from queue, writes `cancelled` event). On completed/failed = not shown; use `[ archive ]` instead.
- `[ archive ]` — soft-deletes a terminal-state task from the default view.
- `[ re-run ]` — opens pre-filled task creation form.

**Filter bar:** radio buttons (single active filter at a time). `[all]` is the default.

**Running indicator:** the `running` row shows elapsed time, updated every 5 seconds via SocketIO. If a task has been `running` for >10 minutes with no log output, append a warning to the log and show "possibly hung" next to the elapsed time.

**Cron task rows:** shown in the task list with a clock icon. Rows with `skip_count > 2` show a yellow warning badge: "Skipped N times (last fired: date)."

### 8.9 REST API Surface

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Server health: config, tmux, scheduler, task replay status |
| GET | `/api/vaults` | List all registered vaults with status |
| POST | `/api/vaults` | Register a vault (name, path, tags) |
| GET | `/api/vaults/{name}/health` | Vault health: path exists, .obsidian/, readme, last session |
| POST | `/api/sessions` | Start a session (vault, type: claude\|bash) |
| DELETE | `/api/sessions/{id}` | End a session |
| GET | `/api/tasks` | List tasks (filters: vault, priority, status, limit, offset) |
| POST | `/api/tasks` | Create a task |
| GET | `/api/tasks/{id}` | Get single task state |
| GET | `/api/tasks/{id}/log` | Get task execution log |
| DELETE | `/api/tasks/{id}` | Cancel a pending/deferred task |
| POST | `/api/tasks/{id}/promote` | Promote a deferred task to pending |
| POST | `/api/tasks/{id}/archive` | Archive a terminal-state task |
| POST | `/api/tasks/compact` | Trigger manual log compaction |
| GET | `/api/window` | Get current window state |
| POST | `/api/window` | Set window state (action: start\|end\|start_weekly\|end_weekly; duration_hours for start) |
| GET | `/api/cron` | List cron tasks with last_fired_at, skip_count |
| POST | `/api/config/yaml` | Save system.yaml or schedule.yaml (body: {file, content}) |

All mutating endpoints require the `X-Requested-With: resman` header. Requests without this header are rejected with HTTP 403. This is a lightweight CSRF mitigation sufficient for a local-only tool. The SPA always sends this header via a fetch wrapper.

---

## 9. Cron Tasks

Cron tasks are defined in `config/schedule.yaml` and fired by `GeventScheduler` (APScheduler's eventlet-compatible scheduler) running inside the Flask process.

**Fire condition:** A cron task fires only if `is_window_active()` returns true at the scheduled time. If the window is `between` or `ended`, the cron tick is **skipped** — a `cron_skipped` event is written to `tasks.jsonl` with `scheduled_at` and `skip_reason`. The task does not queue; it waits for its next scheduled occurrence.

**Skip tracking:** each cron task entry tracks `last_fired_at` and `skip_count` at runtime. When `skip_count > 2`, the scheduler emits a SocketIO event that causes the UI to show a warning badge on that cron task row.

**Execution:** Cron tasks dispatch through the same TaskManager path as manual tasks, with the dispatch lock acquired. They appear in the task list and their output goes to `config/task-logs/`.

**ALL-vault cron tasks:** Follow the same parent/child expansion as manual ALL-vault tasks.

**Cron string validation:** validated with `CronTrigger.from_crontab()` at schedule.yaml load time. Invalid strings prevent the scheduler from starting and surface the error in the startup report and browser banner.

---

## 10. Plugin Command Integration

The `claude-obsidian` plugin is installed at user level once:

```bash
claude plugin marketplace add AgriciDaniel/claude-obsidian
claude plugin install claude-obsidian@claude-obsidian-marketplace
```

All vault sessions automatically have the plugin available. resman invokes plugin commands via `["claude", "-p", command, "--dangerously-skip-permissions"]` as a subprocess argument list (no shell interpolation) for unattended background operations.

### 10.1 Plugin Command Map (`plugin_commands.py`)

All plugin command strings live in one module. No command string appears elsewhere in the codebase.

```python
# plugin_commands.py
TESTED_VERSION_RANGE = ("1.0.0", "2.0.0")  # inclusive lower, exclusive upper

LINT = "/claude-obsidian:wiki-lint"
UPDATE_HOT_CACHE = "update hot cache"

def autoresearch(topic: str) -> str:
    return f"/claude-obsidian:autoresearch {topic}"

def ingest_canvas(url: str) -> str:
    return f"/claude-obsidian:wiki-ingest {url} --canvas"
```

**Startup version check:** On startup, `plugin_commands.py` reads the installed claude-obsidian version via `claude plugin list --json` and warns if it falls outside `TESTED_VERSION_RANGE`. The warning appears in the startup report and as a dismissable browser banner.

### 10.2 Quick Command Palette (Option A)

Per-vault command palette buttons:

| Button | Operation | Params prompt |
|---|---|---|
| Ingest URL | `wiki-ingest` | Asks for URL |
| Auto Research | `wiki-autoresearch` | Asks for topic |
| Lint Vault | `wiki-lint` | None |
| Update Hot Cache | `wiki-update-hot-cache` | None |
| + Claude Session | open tmux Claude | None |
| + Shell | open tmux bash | None |
| Open in Obsidian | launch Obsidian | None |

Ingest URL and Auto Research open the task creation form pre-filled with operation and vault; user fills in the URL/topic.

---

## 11. System Documentation

`docs/` contains resman's own documentation. All files are viewable and editable via the Markdown panel (Option C).

| File | Content |
|---|---|
| `docs/overview.md` | What resman is, quick start |
| `docs/vaults.md` | Vault conventions, structure, claude-obsidian schema |
| `docs/tasks.md` | Task system: priorities, scheduling, window gating |
| `docs/plugin-commands.md` | Plugin command cheatsheet (adapted from obsiPlug/commands.md) |

---

## 12. Security and Safety

- Bind to `127.0.0.1` only — no network exposure
- **CSRF mitigation:** all mutating REST endpoints require `X-Requested-With: resman` header; SocketIO event handlers verify the header on connection. Requests without the header are rejected with HTTP 403. The SPA sends this header via a fetch wrapper applied to all outgoing requests.
- All file serving: `os.path.normpath` + `startswith(allowed_root)` path traversal check
- YAML: always `yaml.safe_load()`, validate result is `dict` before saving
- HTML injection: `esc()` on all user-controlled values before DOM insertion
- Vault names and task names: validate `[a-zA-Z0-9_-]` only
- `params.url`: validated as HTTP/HTTPS URL before use
- `params.topic`, `params.prompt`: max 200 chars, printable ASCII
- `run-shell` (`run_shell` operation): executes as an argument list via `subprocess.run([cmd_parts])` — never via `sh -c`. Requires explicit user acknowledgment in the UI on first use. **Note:** this is still powerful — it runs arbitrary programs in the vault directory. The safety guarantee is no shell metacharacter expansion, not no-code-execution.
- File writes: reject over 1 MB
- tmux: isolated socket (`resman`), never shares with user's personal tmux
- `scan_paths`: depth capped at 2 levels; reject paths that resolve to filesystem roots
- **Subprocess construction:** all operations use the argument-list form of `subprocess.run()` — the shell is never invoked for task execution

---

## 13. Error Handling

Every system boundary has a defined error behavior:

| Scenario | Behavior |
|---|---|
| system.yaml missing on startup | Fail loudly: print error + browser banner; do not start with empty vault list |
| system.yaml invalid YAML | Same as above |
| tasks.jsonl corrupt (partial line) | Skip bad lines, log warning, continue replay; show count in startup report |
| tmux not installed | Fail loudly on startup |
| `TmuxManager.create_session()` fails | Raise `TmuxSessionError`; emit `session_error` SocketIO event to browser with reason |
| Task execution non-zero exit | Write `failed` event; capture stderr in task log; show red dot |
| Vault path missing at dispatch time | Fail task immediately; write `failed` event with reason "vault path not found" |
| Config save (YAML editor) fails validation | Return HTTP 400 with specific error; never write file |
| Config save fails on disk write | Return HTTP 500 with detail; never silently swallow |
| Cron string invalid | Reject at load time; surface in startup report and browser banner |
| budget.json missing or corrupt | Reset to `window_state: between`; log warning; never crash |
| claude-obsidian version out of range | Log warning; show browser banner; do not block operation |

---

## 14. Phased Implementation

### Phase 1 — Core Control Plane (MVP)

Goal: replace "many local terminals" — open Claude Code in any vault from browser.

1. `config/system.yaml.example` and loader; vault registry with `.obsidian/` validation
2. Flask + SocketIO server; EventBus; `plugin_commands.py` with version check
3. `TmuxManager` with `reconcile()` on restart; `TmuxOutputStreamer` + `PtyBridge` split
4. SessionRegistry in `VaultRuntime`; `TmuxSessionError` typed exception
5. `/api/health` endpoint; structured startup report to stdout
6. Left sidebar: registered vaults + status dots (priority rule); filter bar (search, tag, status)
7. `[▶]` mini-menu (Claude or Shell); vault health check modal
8. tmux-backed browser terminal (Option F tabs)
9. Window status bar: manual sync controls + tmux heuristic indicator; color coding; "ending..." state
10. Vault README viewer/editor (Option C)
11. YAML config editor with validation (Option J)
12. `X-Requested-With: resman` CSRF header on all mutating endpoints

### Phase 2 — Task Queue

Goal: queue, run, and track operations on vaults.

1. Task creation form (all operations; `run-shell` with acknowledgment gate)
2. `TaskManager`: state machine, JSONL event log with crash-consistency, in-memory index, dispatch mutex
3. Task UI panel with column semantics, `[promote]` vs no run button, filter bar
4. Window gating: defer tasks when window not active, promote on `window_activated` event
5. Task re-run with pre-filled form
6. Task compaction: manual trigger via `/api/tasks/compact`; auto-trigger at 50k lines

### Phase 3 — ALL-Vaults and Cron

Goal: operate across all vaults and automate housekeeping.

1. Parent/child task model: `dispatch_started` event, EventBus-driven aggregation
2. Expandable parent rows in task UI; mixed-result parent state display
3. `GeventScheduler` cron integration with `schedule.yaml`; cron string validation
4. Cron skip-when-inactive + `cron_skipped` event; `skip_count` + `last_fired_at` tracking; skip warning badge
5. Unregistered vault discovery (max depth 2) and click-to-register flow
6. Vault health check API + modal

### Phase 4 — Vault Creation and Polish

Goal: full vault lifecycle management from the UI.

1. `tools/new-vault.sh` script; wizard with progress view and error handling
2. Quick command palette per vault (Option A)
3. Drag-to-resize terminal + README split (Option I)
4. Audit event log viewer (Option G); `cron_skipped` shown in event log
5. System docs/ browser in Docs tab
6. Low-priority task manual promote controls
7. JSONL compaction UI in Config panel

---

## 15. Testing

No code ships without tests for the following:

### Critical (must pass before Phase 2 merge)

- `tasks.jsonl` replay: parametrized over partial final line, duplicate task_id, unknown event type, out-of-order timestamps, events for nonexistent parent
- `TmuxManager.create_session()` failure path: verify `TmuxSessionError` is raised, SocketIO `session_error` event is emitted
- Dispatch mutex: concurrent dispatch from user action + cron tick does not double-dispatch the same task (simulate with two greenlets hitting `dispatch()` simultaneously)
- CSRF: mutating endpoints reject requests without `X-Requested-With: resman`
- `run-shell` subprocess: verify the shell is never invoked; argument list is passed verbatim to `subprocess.run`

### High (must pass before Phase 3 merge)

- EventBus: `window_activated` promotes deferred high/medium tasks; `low` tasks stay deferred
- `between → active` only via explicit "Start window now" with duration; never via implicit task creation
- APScheduler/GeventScheduler: cron tick with window inactive writes `cron_skipped` event; does not create a task; does not deadlock
- `budget.json` corruption: missing file → reset to `between`; invalid JSON → reset to `between`; `window_ends_at` in past at startup → treat as `between`
- `is_window_active()`: returns false immediately when `window_ends_at` passes (no 60s lag)
- ALL-vault child count integrity: `dispatch_started` + N `child_created` events; startup warns on mismatch

### Medium (must pass before Phase 4 merge)

- Vault discovery: scan_paths depth capped at 2; filesystem root paths rejected
- Cron string validation: invalid cron → HTTP 400 with error; APScheduler never receives invalid trigger
- `TmuxManager.reconcile()`: existing sessions from prior server run are re-registered; no name collision on re-open
- Orphan greenlet cleanup: client disconnect kills all associated streamers; FD count does not grow across reconnect cycles

---

## 16. Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Deployment | Local only, `127.0.0.1` | Same machine as Obsidian; no remote access needed |
| CSRF protection | `X-Requested-With: resman` header | Sufficient for localhost-only tool; no token storage complexity |
| Window budget | Manual time-sync, not auto-counter | Matches how Claude Code windows work; tmux heuristic as informational fallback only |
| Window gating | Manual `[ sync ▼ ]` controls; duration required | User knows when a window starts/ends; no implicit transitions |
| ALL-vaults tasks | Parent + N child tasks + EventBus aggregation | Per-vault status, re-run, and log visibility at child level |
| Plugin install | User-level global, once | Plugin available in all vault sessions automatically |
| Task re-run | Pre-fill + edit form | Safer than blind replay; lets user adjust params |
| Cron + no window | Skip cycle + log `cron_skipped` + track skip_count | Simple; no deferred-cron complexity; next schedule is the retry; skips are now visible |
| Vault paths | Each vault has its own explicit path; no common root | Vaults may live anywhere on the filesystem |
| Vault discovery | system.yaml is authoritative; optional `scan_paths` (depth ≤ 2) | Convenience, not override |
| Task storage | JSONL event sourcing + compaction at 50k lines | Append-only, greppable, no schema migrations, in-memory index; compaction prevents unbounded growth |
| Server stack | Flask + SocketIO + eventlet + GeventScheduler | Handles concurrent WebSocket; GeventScheduler avoids APScheduler+eventlet deadlock |
| Frontend | Vanilla JS + CDN only | No build step; dark terminal aesthetic |
| Session registry | VaultRuntime owns SessionRegistry (dict keyed by session_id) | Multiple sessions per vault; survives server restart via reconcile() |
| Event bus | Lightweight internal pub/sub in event_bus.py | Breaks circular coupling between WindowState and TaskManager |
| Subprocess calls | Always argument-list form; never shell string | Eliminates shell injection from params fields |
| Plugin commands | Centralized in plugin_commands.py | Single source of truth; startup version check; easy to update on API change |
| Operation names | `wiki-*` prefix for plugin ops; `run-prompt`/`run-shell` for ad-hoc | Consistent namespace; type is clear from name |
| Tools path | Absolute via RESMAN_ROOT | `../tools/ingest.sh` would resolve incorrectly relative to vault paths |
| `injest.sh` | Renamed to `ingest.sh` | Fix typo; no backward compat concern |
