# Getting started

## Prerequisites

resman expects a Linux host with:

- **tmux** — required. resman creates an isolated socket so it never collides
  with your interactive tmux.
- **ttyd** — optional. Without it, terminal sessions are disabled but the rest
  of the panel works fine.
- **Python 3.10+** — for the venv that runs Flask + Socket.IO.
- **Obsidian** — optional. Only needed if you want the *Open Obsidian* button
  to do anything; otherwise resman just reads files from your vault paths.

Run `./deps.sh --check` from `v1/` to verify what's installed.

## First run

```bash
cd v1
cp config/system.yaml.example config/system.yaml
# edit system.yaml — at minimum, list one vault
./run.sh
```

Open `http://127.0.0.1:5090`. The startup banner prints which subsystems are
healthy.

## Running on a non-default Python

Pass `--vname` to both `deps.sh` and `run.sh` so they target the same venv:

```bash
./deps.sh --vname /tmp/resman-venv
./run.sh  --vname /tmp/resman-venv
```

## Exposing on the LAN

```bash
./run.sh --public
```

This binds Flask **and** the ttyd terminals to `0.0.0.0`, and relaxes the
Socket.IO CORS allow-list. resman has **no authentication** — only run
`--public` on a trusted network. See [LAN / --public](lan-access.md).

## Stopping

`Ctrl-C` in the terminal where `run.sh` is running. Active tmux sessions are
intentionally **not killed** so you can reattach manually:

```bash
tmux -L resman ls
tmux -L resman attach -t rsm-vla6-claude-1
```
