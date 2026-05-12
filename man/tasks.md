# Tasks

The Tasks tab is where you trigger work against your vaults and watch it
run. A task is one command — usually `claude -p '<plugin command>'` — that
resman executes against a single vault or against every registered vault.
Tasks are persisted to a single append-only JSONL log (`config/tasks.jsonl`);
restarting resman replays the log and rebuilds the in-memory queue.

## The trigger panel

The top of the Tasks tab is a form, not a modal. To run something:

1. **Pick the vault.** Defaults to the vault you have selected in the
   sidebar. Toggle **`all vaults`** to fan the task out across every
   registered vault.
2. **Pick the operation.** The dropdown is grouped Wiki / Research / Custom.
   The form below adjusts to show only the fields that operation needs —
   URL for ingest, topic for autoresearch, prompt for `run-prompt`, argv
   lines for `run-shell`.
3. **Pick a priority.** `high` by default; lower to `medium` or `low` if the
   task is best-effort.
4. **Pick when to run.** Leave **When** empty to run immediately. Set a
   future date/time to park the task in `scheduled` state — resman fires it
   automatically at that moment, even if you close the tab. Scheduling and
   `all vaults` cannot be combined for now.
5. Click **Run task**.

The form is sticky — re-running from a card's `re-run` action prefills it
with the original task's vault, operation, params, and priority.

### Sidebar `↘` shortcut for URL ingest

Each vault row in the left sidebar has a `↘` button that queues a
**wiki-ingest** task in one click: paste the URL into the prompt, and
resman creates the task and jumps to this tab so you can watch it run.
Equivalent to filling the trigger form with `wiki-ingest` + URL.

## Task cards

Below the trigger sits the queue. Each task is a card; the left border is
tinted by state. Click the card head (or the `log` button) to expand it.
Expanded, the card shows operation params, error if any, the scheduled time
if relevant, and a **live-tailing log pane**.

The log pane subscribes to the `task_log_appended` Socket.IO event —
lines are pushed as the task emits them. For a running task this is a real
tail; for a finished task you see the full output that was captured on
disk.

Filters live in the queue toolbar:

- **Priority** filter — narrow to one priority bucket.
- **State** filter — `active` (default) shows running + pending + deferred
  + scheduled; `recent (24h)` adds anything updated in the last day; `all`
  shows everything in memory.

When a vault is selected in the sidebar, the queue is automatically
filtered to its tasks plus any `ALL`-vault tasks that include it.

## Task lifecycle

```
pending → running → completed
                  → failed
                  → cancelled    (user clicked cancel)
                  → interrupted  (process was gone at replay)
scheduled → pending → running → … (one-shot fire)
deferred  → pending → running → … (window activates, or manual promote)
```

A task created while the **window is inactive** lands in `deferred` and
auto-promotes when the window opens. A task created with `scheduled_for`
in the future lands in `scheduled` and waits for its exact moment.

## Cancel — including running

Click `cancel` on any card whose state is `pending`, `deferred`,
`scheduled`, or **`running`**. A running task receives `SIGTERM`; if it
doesn't exit within 5 seconds it is `SIGKILL`'d. The task transitions to
`cancelled` and the audit trail records it.

## Operations

| Group | Operation | What it does |
|---|---|---|
| Wiki | **Lint wiki** | Runs `/claude-obsidian:wiki-lint` against the vault |
| Wiki | **Update canvas (visual map)** | Runs `/claude-obsidian:canvas [description]` to create or update the wiki's visual canvas. Description is optional. |
| Wiki | **Update hot cache** | Runs `/claude-obsidian:update-hot-cache` |
| Wiki | **Re-run wiki bootstrap** | Runs `/claude-obsidian:wiki` non-interactively. Only safe for re-runs — first-time bootstrap must use the wizard. |
| Research | **Ingest a URL** | Runs `tools/ingest.sh <vault> <url>` with optional canvas update. Check **"Update canvas after ingest"** to refresh `wiki/canvases/main.canvas` after ingesting. |
| Research | **Ingest URL + prefix** | Runs `tools/ingest.sh <vault> <url> --prefix <prompts/urlInjestPrefix.md>` to apply constructive-extraction guidance before ingesting. Optional canvas update available. |
| Research | **Autoresearch a topic** | Runs `/claude-obsidian:autoresearch <topic>` |
| Custom | **Run a Claude prompt** | Runs `claude -p '<prompt>'` |
| Custom | **Run shell command** | Runs an explicit argv list (one argument per line) in the vault directory. **Argument list only, not a shell string.** Confirms before submitting. |

## ALL-vault tasks

With `all vaults` toggled on, resman creates one **parent task** plus one
**child task** per registered vault. The parent rolls up:

- `running` while any child is running
- `completed` when every child completes successfully
- `failed` when **any** child fails

The `dispatch_started` event for the parent carries `expected_child_count`
*before* any child events so replay can compute progress correctly.

## Live log tail

For each running task, the streaming runner writes stdout/stderr to
`config/task-logs/<task_id>.log` **and** emits chunks on the bus. The Tasks
tab subscribes; nothing else is required. Logs are capped at 5 MB per
task — once reached, the file ends with `... [output capped]` and further
output is discarded. The cap is in `task_manager.py:LOG_MAX_BYTES`.

## Scheduled tasks

`scheduled_for` is a single one-shot timestamp. Once the moment passes, the
Scheduler fires `promote(task_id)` which transitions `scheduled → pending`
and dispatches via the normal path. Cancel before the moment to abort.

If resman was down at the scheduled moment, the task stays in `scheduled`
state and the card shows an **overdue** badge — click `run-now` to fire it
or `cancel` to abandon. Replay surfaces these in the startup health report.

## Compaction

Click **Compact log** in the queue toolbar. resman snapshots tasks in
terminal states (`completed` / `failed` / `cancelled` / `interrupted` /
`archived`) older than **90 days** into one event per task and rewrites
`tasks.jsonl` atomically. Active tasks are untouched.

## Cron-skip banner

When a recurring cron task tries to fire while the window is inactive, it
emits a `cron_skipped` event. After two skips, the Tasks tab shows a
dismissible banner with the cron name, skip count, and last-attempted time
so you know your schedule is working — just blocked.
