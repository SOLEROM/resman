# Tasks

The Task tab is a per-vault (or all-vaults) queue of jobs that resman runs on
your behalf. Tasks are persisted to a single append-only JSONL log
(`config/tasks.jsonl`) — restarting resman replays the log and rebuilds the
in-memory state.

## Task lifecycle

```
pending → running → completed
                  → failed
                  → interrupted   (process was alive at restart)
```

Tasks created while the **window is inactive** stay in `pending` and are
dispatched as soon as the window opens.

## Priorities

`high`, `medium`, `low`. Higher priorities are dispatched first. You can
**promote** a pending task via the row menu — useful when a low-priority task
needs to jump the queue.

## Operations

The current operations are:

- **wiki-ingest** — accepts `{"url": "..."}`. Runs `tools/ingest.sh` against
  the vault.
- **wiki-lint** — runs the wiki linter against the vault.
- **run-shell** — runs a pre-validated argv list inside the vault path. The
  argv list is checked at task-create time; **shell strings are not accepted**.

## ALL-vault tasks

Specifying vault `ALL` creates a **parent task** that fans out one **child
task** per registered vault. The parent rolls up:

- `running` while any child is running
- `completed` when every child completes successfully
- `failed` when **any** child fails

The `dispatch_started` event for the parent carries `expected_child_count`
*before* the children events, so consumers can compute progress correctly
during replay.

## Compaction

Click **Compact log** in the toolbar. resman snapshots tasks in terminal
states (`completed` / `failed` / `cancelled`) older than **90 days** into a
single line per task and rewrites the JSONL atomically. Active tasks are
untouched.

The button shows a count of how many tasks were compacted; if the log is
already tight, it shows `0`.

## Cron-skip banner

When a scheduled task tries to fire while the window is inactive, it emits a
`cron_skipped` event. The Tasks tab shows a dismissible banner with the cron
name, skip count, and last-attempted time so you know your schedule is
working — just blocked.

## Cancel

A `pending` task can be cancelled directly. A `running` task that hasn't been
dispatched yet is also cancellable; once it's actually executing in tmux, the
cancel just marks it cancelled in the log — it does **not** kill the process.
Use the terminal tab and `Ctrl-C` for that.
