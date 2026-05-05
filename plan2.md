# resman — Research Vault Manager
## System Design Plan v2

> Refined from plan1.md after design interview.
> Interview decisions: local deployment, manual window sync, parent/child ALL-tasks,
> global plugin install, pre-fill re-run, skip cron when idle, click-to-register vaults,
> JSONL event-sourcing for task storage.

---

## 1. Purpose

**resman** is a local web-based command-and-control panel for managing multiple Obsidian research vaults on one machine. Each vault is an independent research project powered by the `claude-obsidian` plugin. resman provides a single browser dashboard to:

- View and navigate all research vaults, each defined by its own path in system.yaml (vaults may be anywhere on the filesystem)
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
│   ├── schedule.yaml        # cron task definitions
│   ├── budget.json          # current window state (written by UI)
│   └── task-logs/           # one .log file per task execution
│
├── control-plane/           # web server (Flask + SocketIO + tmux)
│   ├── server.py
│   ├── requirements.txt
│   ├── modules/
│   │   ├── config_manager.py        # load/save/reload system.yaml
│   │   ├── vault_registry.py        # vault list, metadata, discovery
│   │   ├── vault_runtime.py         # launch Claude/bash sessions per vault
│   │   ├── task_manager.py          # task queue, state machine, parent/child
│   │   ├── window_state.py          # manual window sync + gating logic
│   │   ├── scheduler.py             # cron task firing via APScheduler
│   │   ├── tmux_manager.py          # tmux session lifecycle
│   │   ├── pty_bridge.py            # PTY fork + WebSocket streaming
│   │   ├── routes.py                # REST API
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

> **Note:** `tools/injest.sh` is renamed to `tools/ingest.sh` in this plan to fix the typo. Any existing references should be updated.

### 3.2 Component Map

```
Browser (xterm.js + SPA)
        ↕  WebSocket + REST
Flask + SocketIO (server.py)
    ├── VaultRegistry     — vault list from system.yaml + optional scan_paths discovery
    ├── VaultRuntime      — start/stop Claude and bash tmux sessions
    ├── TaskManager       — priority queue, parent/child tasks, state machine
    ├── WindowState       — manual window sync, gates task execution
    ├── Scheduler         — APScheduler cron, fires tasks if window active
    ├── TmuxManager       — owns all tmux sessions on isolated socket
    └── PtyBridge         — streams tmux PTY output to browser via WebSocket
```

---

## 4. Configuration System

### 4.1 `config/system.yaml` Schema

```yaml
app:
  host: 127.0.0.1
  port: 5090
  tmux_socket: resman           # isolated from user's own tmux
  tmux_prefix: "rsm-"
  scrollback_limit: 10000
  claude_cmd: "claude --dangerously-skip-permissions"
  obsidian_cmd: "flatpak run md.obsidian.Obsidian"

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
# Remove or leave empty to disable discovery.
scan_paths:
  - /data/research
  - /home/user/projects
```

### 4.2 `config/schedule.yaml` Schema

```yaml
cron_tasks:
  - name: weekly-lint-all
    cron: "0 8 * * 0"        # every Sunday 08:00
    vault: ALL
    operation: wiki-lint
    priority: low

  - name: daily-hot-cache-update
    cron: "0 22 * * *"
    vault: ALL
    operation: update-hot-cache
    priority: low
```

### 4.3 `config/budget.json` Schema

Written and read by the server. Edited only through the web UI window controls.

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

### 4.4 Live Config Editing

The web UI includes a YAML editor for both `system.yaml` and `schedule.yaml` (WebTUI Option J pattern). All saves are atomic: write to `.tmp` then `os.replace()`. The vault registry reloads from config on the next API request — no server restart required.

---

## 5. Web TUI (Control Plane)

### 5.1 Server Stack

```
Python:   Flask + Flask-SocketIO + eventlet
Frontend: vanilla JS + xterm.js (CDN) + Socket.IO (CDN) — no build step
Terminal: tmux PTY bridge (adapt from .ref/agent_ccpanBuilder/scripts/core/)
```

Use `eventlet` monkey-patch. Never use threading mode — it cannot handle concurrent WebSocket connections.

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
│                    │                                                   │
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
- Registered vaults: name, colored status dot, tags
- Unregistered vaults (found via `scan_paths` but not in system.yaml): shown below a divider with a `[+ Register]` button — only appears if `scan_paths` is configured
- `[+ New Vault]` — opens scaffold wizard
- `[⚙ Config]` — opens config YAML editor in main panel

**Window Status Bar (bottom of screen):**
- Always visible
- Shows current window state, time remaining, and sync controls (see Section 6)

**Center Tabs:**
- **Terminal** — tmux-backed xterm.js; toolbar with `[+ Bash]`, `[+ Claude]`, `[Open Obsidian]`
- **Docs** — markdown viewer/editor for vault README or system docs/
- **Tasks** — task queue for this vault (or all vaults if no vault selected)
- **Config** — YAML editors for system.yaml and schedule.yaml

### 5.4 Vault Status Dot Colors

| Color | Meaning |
|---|---|
| Green | Has an active tmux session |
| Blue | Obsidian is open for this vault |
| Yellow | A task is currently running |
| Red | Last task failed |
| Gray | Idle |

---

## 6. Window State System

The window state is manually managed by the user via the window status bar. resman does not auto-detect when a Claude Code window starts or ends.

### 6.1 Window States

| State | Meaning | Task execution |
|---|---|---|
| `active` | A Claude window is currently open and usable | Tasks run |
| `between` | Previous window ended; next has not started | Tasks queue as deferred |
| `ended` | Weekly period has ended | Tasks queue as deferred |

### 6.2 Sync Controls (Window Status Bar)

The `[ sync ▼ ]` dropdown in the status bar exposes:

| Control | Action |
|---|---|
| **Start window now** | Sets state=active, window_started_at=now, prompts for duration |
| **Ends in N hours** | Sets window_ends_at = now + N hours |
| **End window now** | Sets state=between, window_ends_at=now |
| **Start weekly period** | Sets weekly_synced_at=now, weekly_ends_at = now + configured period |
| **End weekly period** | Sets state=ended |

### 6.3 Window Transition Rules

- `active → between`: user clicks "End window now", or `window_ends_at` is reached
- `between → active`: user sends first command (creates a task or opens a session) — this action is the window start trigger
- `active → ended`: user clicks "End weekly period"
- `ended → active`: user clicks "Start weekly period" then "Start window now"

The server checks `window_ends_at` on a 60-second poll and transitions `active → between` automatically when the end time passes.

### 6.4 Task Gating

- **While `active`:** all tasks run normally
- **While `between` or `ended`:** new tasks are queued as `deferred`; the task list shows "waiting for next window"
- **On window activation:** deferred tasks with priority `high` or `medium` are promoted to `pending` and begin running; `low` priority tasks remain deferred until the user promotes them manually

---

## 7. Vault Management

### 7.1 Vault Discovery

On startup the server does two things:

1. Load vaults from `system.yaml` — these are the registered vaults. Each vault has its own explicit `path` and may be located anywhere on the filesystem.
2. If `scan_paths` is configured, scan each listed directory for subfolders containing `.obsidian/` — these are discovered but unregistered vaults.

Vaults may live at completely different filesystem locations — there is no required common root. The three example vaults in Section 4.1 intentionally show paths under `/data/`, `/home/`, and `/mnt/` to make this explicit.

Discovered-but-unregistered vaults are shown in the sidebar below a divider labelled "Unregistered". Clicking `[+ Register]` opens a short form (name, tags, confirm path) and appends the entry to `system.yaml`. If `scan_paths` is empty or absent, the divider does not appear.

Vaults in `system.yaml` whose `path` no longer exists on disk are shown with a warning icon.

### 7.2 Create New Vault

The `[+ New Vault]` wizard:

1. User enters vault name, target path (full filesystem path for the new vault), and one-sentence purpose
2. resman runs `tools/new-vault.sh <name> <target_path>`:
   - `git clone wikValTemplate <target_path>`
   - `cd <target_path> && bash bin/setup-vault.sh`
   - Appends the new entry to `system.yaml` with `path: <target_path>`
3. resman opens a terminal tab in the new vault
4. Shows instructions: "Run `/wiki` in this Claude session to scaffold the wiki structure"

No per-vault plugin install is needed. The `claude-obsidian` plugin is installed at the user level and is available in all Claude Code sessions automatically.

### 7.3 Launch Sessions

**Open Claude Code in vault:**
- Creates tmux session: `rsm-<vault-name>-claude`
- Runs: `cd <vault_path> && <claude_cmd>`

**Open bash shell in vault:**
- Creates tmux session: `rsm-<vault-name>-shell`
- Runs: `cd <vault_path> && bash`

**Open Obsidian for vault:**
- Fires: `<obsidian_cmd> <vault_path>` (fire-and-forget, no tmux)

Both tmux sessions survive browser reload. Option F tabs allow multiple sessions open simultaneously.

### 7.4 Tools

All tools in `tools/` take vault path as first argument — vault-agnostic:

| Tool | Invocation | Purpose |
|---|---|---|
| `ingest.sh` | `./ingest.sh <vault_path> <url> [--can]` | Fetch URL and ingest into vault via `claude -p` |
| `new-vault.sh` | `./new-vault.sh <name> <target_path>` | Clone template to target path and register in system.yaml |

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
| `operation` | `wiki-ingest`, `wiki-lint`, `autoresearch`, `update-hot-cache`, `claude-cmd`, `bash-cmd` |
| `priority` | `high` / `medium` / `low` |
| `schedule` | `immediate`, `background`, `deferred` |
| `parent_id` | UUID of parent task (for ALL-vault child tasks), or `null` |

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

Current task state is derived by replaying events for a given `task_id` and taking the latest status event. The server builds an in-memory state index at startup by replaying the full log.

Task execution output is written to `config/task-logs/<task_id>.log`.

### 8.3 Task States

```
pending ──────────► running ──► completed
   ▲                   │
   │ (promoted)        └──────► failed
deferred◄──────────────────────────────── (window not active)
```

### 8.4 Priority and Window Gating

| Priority | Window active | Window between/ended |
|---|---|---|
| `high` | runs immediately | deferred; promoted on next window activation |
| `medium` | runs as background | deferred; promoted on next window activation |
| `low` | runs as background | deferred; user must manually promote |

### 8.5 ALL-Vaults Tasks (Parent/Child)

When a task targets `vault: ALL`:

1. A **parent task** is created with `vault: ALL`
2. On dispatch, the TaskManager creates one **child task** per registered vault, each with `parent_id` set
3. Children run independently (separate tmux sessions, separate log files)
4. Parent state aggregates from children:
   - `running` if any child is running
   - `failed` if any child failed (even if others completed)
   - `completed` only when all children completed successfully

The task UI shows the parent row as expandable. Clicking it reveals each child's individual status, log link, and a re-run button.

### 8.6 Task Re-Run

Re-running a completed or failed task opens the task creation form pre-filled with the original task's params. The user can edit any field before submitting. Submitting creates a new task (new UUID, new event chain) — it does not mutate the original.

### 8.7 Operation-to-Execution Mapping

| Operation | Tmux command |
|---|---|
| `wiki-ingest` | `cd <vault> && ../tools/ingest.sh <vault> <params.url>` |
| `wiki-lint` | `cd <vault> && claude -p "/claude-obsidian:wiki-lint"` |
| `autoresearch` | `cd <vault> && claude -p "/claude-obsidian:autoresearch <params.topic>"` |
| `update-hot-cache` | `cd <vault> && claude -p "update hot cache"` |
| `claude-cmd` | `cd <vault> && claude -p "<params.prompt>"` |
| `bash-cmd` | `cd <vault> && <params.cmd>` |

Session naming: `rsm-<vault-name>-task-<task_id_short>`

### 8.8 Task UI Panel

```
[ ai-agents ▼ ] [ high | medium | low | all ] [ + New Task ]   window: ● ACTIVE

  ▼  ○ running   wiki-ingest     ai-agents     high    1m ago  [ log ]
  ○  ○ pending   autoresearch    llm-bench     high            [ run ] [ ✗ ]
  ○  ○ deferred  weekly-lint     ALL           low     waiting [ run ] [ ✗ ]
  ▶  ✓ completed wiki-lint       ALL           low     2h ago  [ ▶ expand ] [ re-run ]
       ✓  ai-agents-research    ok   [ log ]
       ✓  llm-benchmarks        ok   [ log ]
       ✗  ml-papers             fail [ log ]
  ✗  failed     autoresearch    ml-papers     medium  1d ago  [ re-run ] [ log ]
```

---

## 9. Cron Tasks

Cron tasks are defined in `config/schedule.yaml` and fired by APScheduler running inside the Flask process.

**Fire condition:** A cron task fires only if `window_state == "active"` at the scheduled time. If the window is `between` or `ended`, the cron tick is **skipped**. The task does not queue — it waits for its next scheduled occurrence.

**Execution:** Cron tasks dispatch through the same TaskManager path as manual tasks. They appear in the task list and their output goes to `config/task-logs/`.

**ALL-vault cron tasks:** Follow the same parent/child expansion as manual ALL-vault tasks.

---

## 10. Plugin Command Integration

The `claude-obsidian` plugin is installed at user level once:

```bash
claude plugin marketplace add AgriciDaniel/claude-obsidian
claude plugin install claude-obsidian@claude-obsidian-marketplace
```

All vault sessions automatically have the plugin available. resman invokes plugin commands via `claude -p "<command>"` with `--dangerously-skip-permissions` for unattended background operations.

### 10.1 Quick Command Palette (Option A)

Per-vault command palette buttons:

| Button | Operation | Params prompt |
|---|---|---|
| Ingest URL | `wiki-ingest` | Asks for URL |
| Auto Research | `autoresearch` | Asks for topic |
| Lint Vault | `wiki-lint` | None |
| Update Hot Cache | `update-hot-cache` | None |
| + Claude Session | open tmux Claude | None |
| + Shell | open tmux bash | None |
| Open in Obsidian | launch Obsidian | None |

Ingest URL and Auto Research open the task creation form pre-filled with operation and vault; user fills in the URL/topic.

---

## 11. System Documentation

`docs/` contains resman's own documentation. All files are viewable and editable via the Markdown panel (Option C).

Initial files:

| File | Content |
|---|---|
| `docs/overview.md` | What resman is, quick start |
| `docs/vaults.md` | Vault conventions, structure, claude-obsidian schema |
| `docs/tasks.md` | Task system: priorities, scheduling, window gating |
| `docs/plugin-commands.md` | Plugin command cheatsheet (adapted from obsiPlug/commands.md) |

The **Docs** tab in the main panel shows the `docs/` directory tree. Selecting any file renders it in markdown view; clicking Edit switches to raw markdown editing with atomic save.

---

## 12. Security and Safety

- Bind to `127.0.0.1` only — no network exposure
- All file serving: `os.path.normpath` + `startswith(allowed_root)` path traversal check
- YAML: always `yaml.safe_load()`, validate result is `dict` before saving
- HTML injection: `esc()` on all user-controlled values before DOM insertion
- Vault names and task names: validate `[a-zA-Z0-9_-]` only
- File writes: reject over 1 MB
- tmux: isolated socket (`resman`), never shares with user's personal tmux
- Operations: no arbitrary command execution — only the defined operation types in Section 8.7

---

## 13. Phased Implementation

### Phase 1 — Core Control Plane (MVP)

Goal: replace "many local terminals" — open Claude Code in any vault from browser.

1. `config/system.yaml` loader and vault registry
2. Flask + SocketIO server with basic routes
3. Left sidebar: registered vaults + status dots
4. tmux-backed browser terminal (Option F tabs)
5. `[+ Claude]`, `[+ Shell]`, `[Open Obsidian]` session buttons
6. Window status bar: manual sync controls, `active`/`between`/`ended` display
7. Vault README viewer/editor (Option C)
8. YAML config editor (Option J)

### Phase 2 — Task Queue

Goal: queue, run, and track operations on vaults.

1. Task creation form (all operations)
2. TaskManager: state machine, JSONL event log, in-memory index
3. Task UI panel: list, filter, log viewer
4. Window gating: defer tasks when window not active, promote on activation
5. `[+ Bash]` quick command with background execution
6. Task re-run with pre-filled form

### Phase 3 — ALL-Vaults and Cron

Goal: operate across all vaults and automate housekeeping.

1. Parent/child task model for `vault: ALL`
2. Expandable parent rows in task UI
3. APScheduler cron integration with `schedule.yaml`
4. Cron skip-when-inactive logic
5. Unregistered vault discovery and click-to-register flow

### Phase 4 — Vault Creation and Polish

Goal: full vault lifecycle management from the UI.

1. `tools/new-vault.sh` script
2. New vault scaffold wizard in UI
3. Quick command palette per vault (Option A)
4. Drag-to-resize terminal + README split (Option I)
5. Audit event log viewer (Option G)
6. System docs/ browser in Docs tab
7. Low-priority task manual promote controls

---

## 14. Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Deployment | Local only, `127.0.0.1` | Same machine as Obsidian; no remote access needed |
| Window budget | Manual time-sync, not auto-counter | Matches how Claude Code windows actually work in practice |
| Window gating | Manual `[ sync ▼ ]` controls | User knows when a window starts/ends; no heuristic needed |
| ALL-vaults tasks | Parent + N child tasks | Per-vault status, re-run, and log visibility at child level |
| Plugin install | User-level global, once | Plugin available in all vault sessions automatically |
| Task re-run | Pre-fill + edit form | Safer than blind replay; lets user adjust params |
| Cron + no window | Skip cycle | Simple; no deferred-cron complexity; next schedule is the retry |
| Vault paths | Each vault has its own explicit path in system.yaml; no common root required | Vaults may live anywhere on the filesystem |
| Vault discovery | system.yaml is authoritative; optional `scan_paths` list enables unregistered-vault detection | Scan is a convenience, not an override; disabled by default |
| Task storage | JSONL event sourcing | Append-only, greppable, no schema migrations, in-memory index for queries |
| Server stack | Flask + SocketIO + eventlet | Matches cldlab reference; handles concurrent WebSocket correctly |
| Frontend | Vanilla JS + CDN only | No build step; dark terminal aesthetic matches user preference |
| `injest.sh` | Renamed to `ingest.sh` | Fix typo; no backward compat concern (new codebase) |
