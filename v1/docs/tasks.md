# Task system

Tasks represent operations to run against a vault. The list of supported
operations is fixed: `wiki-ingest`, `wiki-lint`, `wiki-autoresearch`,
`wiki-update-hot-cache`, `run-prompt`, `run-shell`.

## Storage

Tasks are stored as JSONL events in `config/tasks.jsonl`. Each state change
appends a line; current state is derived by replaying. The log is
crash-consistent: bad lines are skipped, partial last lines are truncated
with a warning, and tasks left in `running` across restarts are surfaced as
`interrupted`.

## States

```
pending ──► running ──► completed
   ▲           │
   │           └────► failed
   │           └────► interrupted (server crash)
deferred ◄── (window not active)
```

## Priority and window gating

| Priority | Window active | Window between/ended |
|---|---|---|
| high | runs immediately | deferred; promoted on next window activation |
| medium | runs as background | deferred; promoted on next window activation |
| low | runs as background | deferred; manual promote required |

## ALL-vaults tasks

Tasks with `vault: ALL` create a parent task plus one child task per
registered vault. The parent's state aggregates from its children.

## run-shell

`run-shell` executes an arbitrary argument list. The shell is never
invoked. The browser surfaces a confirmation modal before the first
`run-shell` task per session.
