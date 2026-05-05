# resman — Research Vault Manager
## System Design Plan

---

## 1. Purpose

**resman** is a web-based command-and-control system for managing multiple independent Obsidian research vaults. Each vault is a self-contained research project powered by the `claude-obsidian` plugin. resman provides a single browser UI to:

- View, create, and navigate all research vaults under a configurable root path
- Launch Claude Code sessions inside any vault
- Trigger plugin operations (ingest, lint, research, canvas) on any vault
- Manage a prioritized task queue with Claude window-budget awareness
- Schedule housekeeping tasks via cron
- Edit system configuration and vault docs in the browser

The system does **not** replace the per-vault Claude Code workflow — it wraps it, launching Claude Code sessions in the correct vault directory and keeping track of what ran and what still needs to run.

---

## 2. Reference Projects

All three reference repos live in `.ref/`. Read them before building any component.

| Repo | Path | What to learn from it |
|---|---|---|
| `claude-obsidian` | `.ref/claude-obsidian/` | Vault structure, plugin commands, WIKI.md schema, wiki skills |
| `agent_ccpanBuilder` | `.ref/agent_ccpanBuilder/` | WebTUI architecture: Flask + SocketIO + tmux + xterm.js, all option modules A–J |
| `cldlab` | `.ref/cldlab/` | YAML config schema, control-plane server design, Docker + local-shell agent model |

Key files to read when building each subsystem:

- Web TUI: `.ref/agent_ccpanBuilder/WEBTUI_SKILL.md` + `scripts/core/` + `scripts/options/`
- Control plane server: `.ref/cldlab/control-plane/server.py` + `modules/`
- YAML config format: `.ref/cldlab/config.yaml` + `config_yaml_help.md`
- Vault structure: `.ref/claude-obsidian/WIKI.md` + `README.md`
- Plugin commands: `obsiPlug/commands.md` + `obsiPlug/usageExampels.md`

---

## 3. System Architecture

### 3.1 Directory Layout

```
resman/
├── config/
│   ├── system.yaml          # app settings + vault registry (source of truth)
│   └── schedule.yaml        # cron task definitions
│
├── control-plane/           # web TUI server (Flask + SocketIO + tmux)
│   ├── server.py
│   ├── requirements.txt
│   ├── modules/
│   │   ├── config_manager.py        # load/save system.yaml
│   │   ├── vault_registry.py        # vault list + metadata
│   │   ├── vault_runtime.py         # start/stop Claude sessions per vault
│   │   ├── task_manager.py          # task queue, priority, state
│   │   ├── window_budget.py         # daily/weekly Claude window tracking
│   │   ├── scheduler.py             # cron + deferred task executor
│   │   ├── tmux_manager.py          # tmux session lifecycle
│   │   ├── pty_bridge.py            # PTY fork + WebSocket streaming
│   │   ├── routes.py                # REST API
│   │   └── websocket_handlers.py    # Socket.IO events
│   ├── templates/
│   │   └── index.html               # single-page app shell
│   └── static/
│       ├── js/app.js
│       └── css/style.css
│
├── docs/                    # system documentation (editable via web UI)
│   ├── overview.md
│   └── ...
│
├── tools/                   # vault-agnostic CLI tools
│   ├── injest.sh            # ingest URL into a vault
│   └── new-vault.sh         # scaffold a new vault from wikValTemplate
│
├── wikValTemplate/          # vault template (git submodule from SOLEROM/wikValTemplate)
│   └── ...
│
├── obsiPlug/                # plugin install docs (already present)
│   ├── install.md
│   ├── commands.md
│   └── usageExampels.md
│
└── .ref/                    # cloned reference repos (dev only, not deployed)
```

### 3.2 Component Map

```
Browser (xterm.js + SPA)
        ↕  WebSocket + REST
Flask + SocketIO (control-plane/server.py)
    ├── VaultRegistry     — knows all vaults from config
    ├── VaultRuntime      — launches Claude / bash sessions per vault
    ├── TaskManager       — priority queue + state machine
    ├── WindowBudget      — tracks Claude session usage
    ├── Scheduler         — fires cron + deferred tasks
    ├── TmuxManager       — owns all tmux sessions (isolated socket)
    └── PtyBridge         — streams tmux output to browser via WebSocket
```

---

## 4. Configuration System

### 4.1 `config/system.yaml` Schema

```yaml
app:
  host: 127.0.0.1
  port: 5090
  tmux_socket: resman          # isolated from user's own tmux
  tmux_prefix: "rsm-"
  scrollback_limit: 10000
  top_root: /data/research     # parent folder that contains all vault folders
  claude_cmd: "claude --dangerously-skip-permissions"
  obsidian_cmd: "flatpak run md.obsidian.Obsidian"

window_budget:
  daily_limit: 10              # max Claude Code windows per day
  weekly_limit: 50             # max per week
  reset_hour: 0                # hour (UTC) when daily counter resets
  low_priority_window: "22:00-06:00"  # hours when deferred tasks may run

vaults:
  - name: ai-agents-research
    path: /data/research/ai-agents-research
    tags: [ai, agents]
    auto_open_obsidian: false
    readme: README.md

  - name: llm-benchmarks
    path: /data/research/llm-benchmarks
    tags: [llm, eval]
    auto_open_obsidian: false
    readme: README.md
```

### 4.2 `config/schedule.yaml` Schema

Defines recurring cron tasks (housekeeping, lint, etc.):

```yaml
cron_tasks:
  - name: weekly-lint-all
    cron: "0 8 * * 0"        # every Sunday 08:00
    vault: ALL               # run on all vaults
    operation: wiki-lint
    priority: low

  - name: daily-hot-cache
    cron: "0 22 * * *"       # every night 22:00
    vault: ALL
    operation: update-hot-cache
    priority: low
```

### 4.3 Live Config Editing

The web UI includes a YAML config editor (WebTUI Option J pattern). Edits are saved atomically (`.tmp` → `os.replace()`). The server reloads the vault registry on next request without full restart.

---

## 5. Web TUI (Control Plane)

The web server follows the `cldlab` control-plane architecture and uses the `WEBTUI_SKILL.md` pattern from `agent_ccpanBuilder` as a direct blueprint.

### 5.1 Server Stack

```
Python: Flask + Flask-SocketIO + eventlet
Frontend: vanilla JS + xterm.js (CDN, no build step) + Socket.IO
Terminal: tmux-backed PTY bridge (copy pty_bridge.py from .ref/agent_ccpanBuilder/scripts/core/)
```

### 5.2 WebTUI Options to Enable

From `agent_ccpanBuilder`'s option modules:

| Option | Purpose in resman |
|---|---|
| **A — Quick Commands** | Per-vault command palette (ingest, lint, autoresearch, open Obsidian) |
| **C — Markdown Viewer/Editor** | View/edit vault README and system docs/ folder |
| **E — Agent/Service Registry** | Vault registry backed by system.yaml |
| **F — Multi-Session Tabs** | Multiple xterm.js terminals per vault (bash + claude sessions) |
| **G — Audit Event Log** | Append-only JSONL log of all operations |
| **I — Resizable Split Panel** | Terminal + README panel side by side |
| **J — Config YAML Editor** | Edit system.yaml and schedule.yaml in browser |

### 5.3 UI Layout

```
┌─────────────────┬──────────────────────────────────────┐
│                 │  [ Terminal ] [ Docs ] [ Tasks ] [⚙]  │
│  Vault Sidebar  │                                        │
│                 │  xterm.js browser terminal             │
│  ● ai-agents   │         or                             │
│  ○ llm-bench   │  Markdown viewer/editor                │
│  ○ ml-papers   │         or                             │
│  [+ New Vault] │  Task queue panel                      │
│  [⚙ Config]   │         or                             │
│                 │  System config YAML editor             │
└─────────────────┴──────────────────────────────────────┘
```

**Left Sidebar:**
- List of all vaults from registry (name, status dot, tags)
- `[+ New Vault]` button → scaffold wizard
- `[⚙ Config]` → opens config YAML editor in main panel

**Center Tabs:**
- **Terminal** — tmux-backed xterm.js session; buttons for `bash`, `+ Claude`, `Obsidian`
- **Docs** — markdown viewer/editor for vault README or system docs/
- **Tasks** — task queue for this vault (see Section 7)
- **Config** (system-level tab) — YAML editors for system.yaml and schedule.yaml

### 5.4 Vault Status Indicators

| Color | Meaning |
|---|---|
| Green | Has an active tmux session |
| Blue | Obsidian is open |
| Gray | Idle |
| Yellow | Task running |
| Red | Last task failed |

---

## 6. Vault Management

### 6.1 Vault Lifecycle

**Discover existing vaults:**
- On startup, scan `top_root` for folders containing `.obsidian/`
- Cross-reference with `system.yaml` vault list
- Add new discoveries to the registry automatically

**Create new vault:**
1. UI: user enters vault name and purpose
2. `tools/new-vault.sh <name> <top_root>`:
   - `git clone wikValTemplate <top_root>/<name>`
   - Run `bin/setup-vault.sh` inside the new folder
   - Register vault in `system.yaml`
3. Optionally run `/wiki` to scaffold the vault structure (via Claude Code)

**Open vault in Obsidian:**
- Button in sidebar triggers: `<obsidian_cmd> <vault_path>`
- This is fire-and-forget; Obsidian opens outside the browser

**Launch Claude Code in vault:**
- Creates a tmux session: `rsm-<vault-name>-claude`
- Runs: `cd <vault_path> && <claude_cmd>`
- Session survives browser reload

**Open bash shell in vault:**
- Creates tmux session: `rsm-<vault-name>-shell`
- Runs: `cd <vault_path> && bash`

### 6.2 Tools

All tools in `tools/` accept vault path as the first argument so they are vault-agnostic:

| Tool | Usage | What it does |
|---|---|---|
| `injest.sh` | `./injest.sh <vault_path> <url> [--can]` | Fetch URL + ingest into vault via Claude |
| `new-vault.sh` | `./new-vault.sh <name> <top_root>` | Clone template + configure new vault |

Quick-command buttons in the UI map directly to these tools with the selected vault's path pre-filled.

---

## 7. Task Management System

This is the most complex new component relative to the reference projects.

### 7.1 Concepts

**Task** — a named operation to run on one or all vaults.

Each task has:

| Field | Values |
|---|---|
| `id` | UUID |
| `name` | human label |
| `vault` | vault name or `ALL` |
| `operation` | `wiki-ingest`, `wiki-lint`, `autoresearch`, `claude-cmd`, `bash-cmd`, etc. |
| `params` | dict of operation parameters (e.g. `{url: "..."}`) |
| `priority` | `high` / `medium` / `low` |
| `schedule` | `immediate`, `background`, `deferred`, `cron` |
| `state` | `pending` → `running` → `completed` / `failed` |
| `created_at` | timestamp |
| `started_at` | timestamp |
| `finished_at` | timestamp |
| `log_path` | path to output log file |
| `exit_code` | integer when finished |
| `retryable` | bool — completed tasks can be re-queued |

### 7.2 Task States

```
pending  →  running  →  completed
               ↓
             failed  →  (re-queue as pending)
```

### 7.3 Priority and Scheduling

**Immediate:** Run now. Blocks until done (shown in terminal tab).

**Background:** Run now in a background tmux session. Non-blocking. Result appears in task log.

**Deferred:** Hold until one of these conditions:
- Current time is within the `low_priority_window`
- Enough window budget remains to justify spinning a session
- User manually promotes to immediate via UI

**Cron:** Defined in `schedule.yaml`. Fires on schedule via the Scheduler module.

### 7.4 Window Budget Tracking

Claude Code windows are a finite resource. `window_budget.py` tracks:

```python
{
  "date": "2026-05-05",
  "windows_used_today": 3,
  "windows_used_this_week": 12,
  "daily_limit": 10,
  "weekly_limit": 50,
  "last_window_opened": "2026-05-05T14:32:00Z"
}
```

Budget state is persisted in `config/budget.json`.

Rules:
- Spinning a Claude Code window increments `windows_used_today` and `windows_used_this_week`
- When `windows_used_today >= daily_limit`, new high/medium priority tasks queue as deferred
- Low-priority tasks are automatically deferred regardless of budget
- At `reset_hour`, daily counter resets; the scheduler checks the deferred queue
- At the end of the week, if weekly budget has unused windows, the scheduler promotes deferred tasks

### 7.5 Task Execution

Operations map to tmux commands:

| Operation | Tmux command |
|---|---|
| `bash-cmd` | `cd <vault_path> && <cmd>` |
| `claude-cmd` | `cd <vault_path> && claude -p "<prompt>"` |
| `wiki-ingest` | `cd <vault_path> && ../tools/injest.sh <vault_path> <url>` |
| `wiki-lint` | `cd <vault_path> && claude -p "/claude-obsidian:wiki-lint"` |
| `autoresearch` | `cd <vault_path> && claude -p "/claude-obsidian:autoresearch <topic>"` |
| `update-hot-cache` | `cd <vault_path> && claude -p "update hot cache"` |

All task output is captured to `config/task-logs/<task-id>.log`.

### 7.6 Task UI

The **Tasks** tab in the main panel shows:

```
[ All Vaults ▼ ] [ high | medium | low | all ] [ + New Task ]

  ● running   wiki-ingest        ai-agents     2m ago   [ log ]
  ○ pending   autoresearch       llm-bench     high     [ run ] [ ✗ ]
  ○ deferred  weekly-lint-all    ALL           low      [ run ] [ ✗ ]
  ✓ completed wiki-ingest        ai-agents     5h ago   [ re-run ] [ log ]
  ✗ failed    autoresearch       ml-papers     1d ago   [ re-run ] [ log ]
```

Tasks are persisted in `config/tasks.json` as an append-only JSONL log. The UI reads and filters this file.

---

## 8. Plugin Command Integration

The `claude-obsidian` plugin is installed per-vault via:

```bash
claude plugin marketplace add AgriciDaniel/claude-obsidian
claude plugin install claude-obsidian@claude-obsidian-marketplace
```

resman invokes plugin commands by launching `claude -p "<command>"` inside the vault's tmux session using `--dangerously-skip-permissions` for unattended operation.

### 8.1 Quick Command Palette (WebTUI Option A)

Each vault in the UI has a command palette with buttons:

| Button | Operation | Mode |
|---|---|---|
| Ingest URL | `wiki-ingest <url>` | immediate, asks for URL |
| Auto Research | `autoresearch <topic>` | background, asks for topic |
| Lint Vault | `wiki-lint` | background |
| Update Hot Cache | `update hot cache` | background |
| Open Canvas | `canvas` | immediate (opens Obsidian canvas) |
| Open Obsidian | launch Obsidian | fire-and-forget |
| + Claude | open Claude session | immediate (interactive) |
| + Shell | open bash | immediate (interactive) |

### 8.2 Vault Context in Sessions

When Claude Code opens inside a vault, it automatically loads the vault's `CLAUDE.md`. This makes the plugin commands and wiki schema available without any extra setup.

---

## 9. System Documentation

`docs/` folder contains resman's own documentation — how to use the system, vault conventions, plugin cheatsheet.

- All `.md` files in `docs/` are viewable and editable via the web UI's Markdown panel (WebTUI Option C)
- A **Docs** tab in the main panel shows the system `docs/` directory tree
- Selecting any file renders it; clicking Edit switches to raw markdown editing
- Saves are atomic (`.tmp` → `os.replace()`)

Initial docs to create at scaffold time:
- `docs/overview.md` — what resman is
- `docs/vaults.md` — vault conventions and structure
- `docs/tasks.md` — task system howto
- `docs/plugin-commands.md` — copy of `obsiPlug/commands.md` adapted for resman

---

## 10. Security and Safety Constraints

Mirrors the constraints from the `WEBTUI_SKILL.md` security checklist:

- Default bind: `127.0.0.1` — explicit `--public` flag required for LAN
- All file serving: `os.path.normpath` + `startswith(allowed_root)` path traversal check
- YAML: always `yaml.safe_load()`, validate result is `dict` before saving
- HTML injections: `esc()` on all user-controlled values
- Task names and vault names: validate `[a-zA-Z0-9_-]` only
- Reject file writes over 1 MB
- tmux: isolated custom socket (`resman`), never shares with user's own tmux
- No arbitrary command execution outside the defined operation types

---

## 11. Phased Implementation

### Phase 1 — MVP (Core Control Plane)

Deliverables:
1. `config/system.yaml` loader with vault registry
2. Flask + SocketIO server with basic routes
3. Left sidebar showing vault list from config
4. tmux-backed browser terminal (xterm.js) per vault
5. `+ Claude` and `+ Shell` session buttons
6. Vault README viewer/editor (Option C)
7. YAML config editor (Option J)
8. Basic task queue: create, list, run immediate/background, view log
9. Window budget counter (simple increment/display, no auto-deferral logic)

**Goal:** Replace the "many local terminals" workflow. User can open Claude Code in any vault from the browser.

### Phase 2 — Task Scheduler

Deliverables:
1. Full task state machine (pending → running → completed/failed)
2. Deferred task logic + `low_priority_window` enforcement
3. Window budget auto-deferral and end-of-week promotion
4. Cron tasks via `schedule.yaml` + APScheduler
5. Task log viewer in UI

### Phase 3 — Vault Creation + Multi-Vault Operations

Deliverables:
1. New vault scaffold wizard (clone wikValTemplate + plugin setup)
2. `tools/new-vault.sh` script
3. `ALL` vaults task target (fan-out to all vaults)
4. Vault discovery from `top_root` scan
5. Quick command palette per vault (Option A)

### Phase 4 — Polish and Extended Features

Deliverables:
1. Multi-tab terminals per vault (Option F)
2. Resizable split panel: terminal + README side by side (Option I)
3. Audit event log (Option G)
4. System docs/ browser
5. Obsidian launch integration (`obsidian_cmd`)
6. Task re-run and retry UI

---

## 12. Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Server stack | Flask + Flask-SocketIO + eventlet | Same as cldlab reference; eventlet handles concurrent WebSocket correctly |
| Terminal | tmux + PTY bridge | Sessions survive browser reload; multiple clients can share same session |
| Config | YAML files | Human-readable, editable via UI, no database needed in v1 |
| Task persistence | Append-only JSONL | Simple, queryable, no schema migrations |
| New vault template | git clone wikValTemplate | User already has the template; keeps vault structure consistent |
| Plugin invocation | `claude -p "<cmd>"` in tmux | Unattended operation; output captured; session stays alive for inspection |
| Docs editing | Markdown in-browser (Option C) | Same codebase, no separate editor tool |
| Frontend | Vanilla JS + CDN only | Follows cldlab preference: no build step, no npm/webpack |
