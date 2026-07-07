---
noteId: "897576204f5f11f18eaba108b9c533e7"
tags: []

---

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
- [Vaults](vaults.md) — registering, discovering, scaffolding, health; sidebar category tree + filters
- [Sessions](sessions.md) — Ops tab, Claude vs shell, tabs, renaming, Obsidian
- [Tasks](tasks.md) — operations-first trigger, live logs, scheduling, ALL-vault parent/child, cancel/compaction; sidebar `↘` shortcut for URL ingest
- [Wiki](wiki.md) — the per-vault Wiki tab, sidebar page tree, read/unread tracking, search, random, clickable `[[wikilinks]]`
- [Window state](window-state.md) — when work is allowed to run; the **⊞ Windows** tab (daily-window schedule, per-window open/collect marks, collection rate, usage statistics); openers/collectors automation; footer usage meters + limits
- [Activity log](activity-log.md) — the footer **📋 Log** window: live operations + errors, while the app runs
- [Scheduler](scheduler.md) — cron tasks and skip-when-inactive
- [Configuration](config.md) — `resman.yaml` and `schedule.yaml`; the Config tab's settings form + raw YAML modes; categories
- [Mounts](mounts.md) — bind-mounting vaults at host paths; privilege setup; taking changes to effect
- [LAN / `--public`](lan-access.md) — exposing the panel on the local network
- [Troubleshooting](troubleshooting.md) — common issues
- [Remote agent (CLI)](remote-agent.md) — drive resman from a script, cron, or openClaw over SSH
- [Reference / API](reference/api.md) — REST and Socket.IO surface
- [Reference / Keyboard](reference/keyboard.md) — keystrokes inside terminals

## Appearance

The UI is skinned as **Visual Studio Code** (codeGui VS Code kit): activity
bar on the left, vault tree in the sidebar, editor-style tabs, and a status
bar with the usage meters. The top-right **theme switch** offers three
themes: **dark** (VS Code Dark Modern, default), **light** (Light Modern),
and **hc** (Dark High Contrast). Your choice is remembered across reloads;
new terminal sessions pick up the matching terminal palette (terminals
opened before a theme switch keep their old colors until recreated).

## Conventions used in this manual

- Paths in **monospace** are absolute on disk.
- Code blocks marked `bash` are meant to be pasted into a shell.
- **Vault** = an Obsidian vault directory (one with `.obsidian/`).
- **Window** = the global Claude-Code work window. While the window is
  *inactive*, scheduled tasks skip rather than fire.
