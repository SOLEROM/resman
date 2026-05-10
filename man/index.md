# resman — operator manual

resman is a local-only web panel for running multiple Obsidian research vaults
on one machine. It coordinates **tmux sessions** (via an isolated socket),
**ttyd processes** (one browser terminal per session), a **task queue** backed
by an append-only JSONL event log, and a **window-state** budget that gates
when scheduled work is allowed to fire.

This is the in-app help tree. Pages live at `/mnt/resman/man/` in the repo and
are rendered straight from disk — edit them and hit ↻ to reload.

## Where to start

- [Getting started](getting-started.md) — first-run setup, deps, launching
- [Vaults](vaults.md) — registering, discovering, scaffolding, health
- [Sessions](sessions.md) — Claude vs shell, tabs, renaming, opening Obsidian
- [Tasks](tasks.md) — the task queue, ALL-vault parent/child, compaction
- [Wiki](wiki.md) — the per-vault Wiki tab and the Claude wiki plugin
- [Window state](window-state.md) — when work is allowed to run
- [Scheduler](scheduler.md) — cron tasks and skip-when-inactive
- [Configuration](config.md) — `system.yaml` and `schedule.yaml`
- [LAN / `--public`](lan-access.md) — exposing the panel on the local network
- [Troubleshooting](troubleshooting.md) — common issues
- [Reference / API](reference/api.md) — REST and Socket.IO surface
- [Reference / Keyboard](reference/keyboard.md) — keystrokes inside terminals

## Conventions used in this manual

- Paths in **monospace** are absolute on disk.
- Code blocks marked `bash` are meant to be pasted into a shell.
- **Vault** = an Obsidian vault directory (one with `.obsidian/`).
- **Window** = the global Claude-Code work window. While the window is
  *inactive*, scheduled tasks skip rather than fire.
