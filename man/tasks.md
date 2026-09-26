---
noteId: "f51761f04f5911f18eaba108b9c533e7"
tags: []

---

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
2. **Pick the operation.** The cards on the left are grouped Research / Wiki
   / Custom; the **source switch** above them (All · claude-obsidian · resman
   skills · ad hoc) narrows them to one source. The form on the right adjusts
   to show only the fields that operation needs — URL for ingest, topic for
   autoresearch, prompt for `run-prompt`, argv lines for `run-shell`, an
   optional focus for deepList.
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
- **Source** filter — all sources, or only claude-obsidian / resman skills /
  ad hoc tasks. Every card also carries a small source pill before its
  operation name.

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

## Attend — re-run interactively

Tasks run via `claude -p` (non-interactive), so if the prompt asks the
user a question mid-run the answer never arrives and the task fails or
hangs. Click **`attend`** on any completed / failed / cancelled /
interrupted task to:

1. Rebuild the exact prompt that task ran with
2. Open a fresh Claude session in the task's vault under the **Ops** tab
3. Bracketed-paste the prompt into the REPL as a single message + Enter

You land inside a live interactive run and can answer whatever the
original task couldn't. The button appears only on operations that drive
Claude with a prompt (Lint wiki, Update canvas, Update hot cache, Re-run
wiki bootstrap, Autoresearch, Run a Claude prompt). Shell-based
operations (Ingest a URL, Ingest URL + prefix, Run shell command) don't
have an attendable prompt, so the button is hidden.

This is a re-run, not a true attach — the original `claude -p` process
is gone; resman opens a *new* Claude session and gives it the same
instructions interactively.

## Operations

| Source | Group | Operation | What it does |
|---|---|---|---|
| claude-obsidian | Wiki | **Lint wiki** | Runs `/claude-obsidian:wiki-lint` against the vault |
| claude-obsidian | Wiki | **Update canvas (visual map)** | Runs `/claude-obsidian:canvas [description]` to create or update the wiki's visual canvas. Description is optional. |
| claude-obsidian | Wiki | **Update hot cache** | Runs `/claude-obsidian:update-hot-cache` |
| claude-obsidian | Wiki | **Generate hint** | Inspects the wiki with `/claude-obsidian:wiki-query` and writes `wiki/hint.json` — the label, summary and tags on the vault's landing-page card |
| claude-obsidian | Wiki | **Re-run wiki bootstrap** | Runs `/resman:vault-brief` with the interview off (normalizing what the vault already says into `wiki/meta/brief.md`), then `/claude-obsidian:wiki` told to take Purpose, Mode and Owner from the brief, non-interactively, wrapped with `tools/newValPrefix.md` (plugin check) and `tools/newValSuffix.md` (copy visual `workspace.json`) when those files exist. Only safe for re-runs — first-time bootstrap must use the New Vault form. |
| claude-obsidian | Research | **Ingest a URL** | Runs `tools/ingest.sh <vault> <url>` with optional canvas update. Check **"Update canvas after ingest"** to refresh `wiki/canvases/main.canvas` after ingesting. |
| claude-obsidian | Research | **Ingest URL + prefix** | Runs `tools/ingest.sh <vault> <url> --prefix <prompts/urlInjestPrefix.md>` to apply constructive-extraction guidance before ingesting. Optional canvas update available. |
| claude-obsidian | Research | **Autoresearch a topic** | Runs `/claude-obsidian:autoresearch <topic>` |
| resman skills | Wiki | **vaultBrief: define the vault** | Runs `/resman:vault-brief …` with the settings from **Skills → resman skills → vault-brief**: a grilling interview at the set depth (attend it to answer yourself; a background run takes the recommended answers), then writes `wiki/meta/brief.md` and the vault card's `wiki/hint.json`. Optional per-run *Seed*. The New Vault form's Deep interview tab runs the same skill before the plugin scaffolds a new vault. See [New vault](new-vault.md). |
| resman skills | Research | **deepList: research values** | Runs `/resman:deep-list …` with the settings from **Skills → resman skills → deep-list**: writes `wiki/meta/deep-list.md`, the ranked list of values worth a deep-research run, retiring values the wiki has since filled. Optional per-run *Focus*. The New Vault form's Deep interview tab runs the same line as a stage right after a new vault's scaffold ([New vault](new-vault.md)). See [Skills](skills.md). |
| ad hoc | Custom | **Run a Claude prompt** | Runs `claude -p '<prompt>'` |
| ad hoc | Custom | **Run shell command** | Runs an explicit argv list (one argument per line) in the vault directory. **Argument list only, not a shell string.** Confirms before submitting. |

### Where operations come from

Every operation belongs to one **source**, shown in the first column above:

- **claude-obsidian** — the plugin installed per user (`claude plugin install`);
  the **Skills** tab shows its version and warns when a skill resman sends is
  missing from it. Operation keys start with `wiki-`.
- **resman skills** — resman's own skills in the repo's `skills/` folder,
  loaded into every Claude run with `--plugin-dir` (no install). Operation keys
  start with `rs-`. Their settings are edited in the **Skills** tab and stored
  in `resman.yaml`.
- **ad hoc** — `run-prompt` and `run-shell`: you typed the command yourself.

Whatever the source, a skill runs inside the vault and writes markdown pages
under `wiki/`; read them in the [Wiki](wiki.md) tab. See [Skills](skills.md).

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
