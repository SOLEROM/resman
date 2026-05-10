# resman — overview

resman is a local web command-and-control panel for managing multiple
Obsidian research vaults from a single browser dashboard.

## What resman does

- Loads your vaults from `config/system.yaml` — each vault is an
  independent Obsidian directory, anywhere on the filesystem.
- Spawns Claude Code and bash sessions inside any vault as `tmux` sessions
  visible in the browser via `ttyd` iframes.
- Runs plugin operations (`wiki-ingest`, `wiki-lint`, `wiki-autoresearch`,
  `wiki-update-hot-cache`) through a prioritized task queue gated on a
  manually-managed Claude window.
- Schedules recurring housekeeping with cron tasks (`config/schedule.yaml`)
  that fire only while the window is active.
- Pushes a `_resman/status.md` health node into each vault every 60s so
  status appears in Obsidian's graph view.

## Quick start

```bash
cd v1
python3 -m venv .venv
.venv/bin/pip install -r control-plane/requirements.txt
cp config/system.yaml.example config/system.yaml
# edit config/system.yaml — replace the placeholder vault entries
.venv/bin/python control-plane/server.py
```

Open `http://127.0.0.1:5090` in your browser.

## Window state

The status bar at the bottom of the browser shows the current Claude
window state. Tasks created while the window is `between` or `ended`
are deferred and run when you click **Start window now**.

## .gitignore for vaults

Each vault's `.gitignore` should contain `_resman/` so the 60-second
ObsidianPush write loop does not pollute git history. `tools/new-vault.sh`
adds this automatically; existing vaults need a one-time edit.
