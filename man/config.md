# Configuration

resman reads two YAML files at startup:

| File | Purpose |
|------|---------|
| `config/resman.yaml` or `~/.resman.yaml` | App settings, vaults, scan paths, window budget |
| `config/schedule.yaml` | Cron tasks |

Both are editable live via the **Config** tab. Saves are atomic
(`tempfile.NamedTemporaryFile` + `os.replace`) so a crash mid-write never
corrupts the file.

If `~/.resman.yaml` exists, resman uses it in preference to `config/resman.yaml`,
and all config saves write back to the user file. This allows per-user configuration
without modifying the repository checkout.

## `resman.yaml` — top-level keys

```yaml
app:
  host: 127.0.0.1                 # bind address (overridden by --public / --host)
  port: 5090                      # HTTP port
  tmux_socket: resman             # isolated tmux socket name
  tmux_prefix: "rsm-"             # prefix for tmux session names
  scrollback_limit: 10000         # tmux history-limit per session
  claude_cmd: "claude --dangerously-skip-permissions"
  obsidian_cmd: "flatpak run md.obsidian.Obsidian"
  ttyd_port_base: 7680            # range for ttyd to bind into
  ttyd_port_max: 7999
  man_path: ""                    # optional override for the Help tree

window_budget:
  weekly_start: "Monday 09:00"
  weekly_end:   "Sunday 23:00"

vaults:
  - name: vla6
    path: /tmp/val6
    tags: [research]

scan_paths:
  - /tmp                          # walked for unregistered vaults
```

### Notes

- `host`/`port` are read at startup. CLI flags `--public` / `--host` /
  `--port` override the file.
- `man_path` defaults to `<repo-root>/man` (the sibling of the `v1/` dir).
- `tmux_socket` lets resman use its own tmux server. Don't set it to your
  default socket — if you do, killing resman would kill your interactive
  sessions.

## `schedule.yaml`

```yaml
- name: nightly-lint
  cron: "0 23 * * *"
  vault: vla6
  operation: wiki-lint
  priority: medium

- name: ingest-everything
  cron: "0 6 * * 1"
  vault: ALL
  operation: wiki-lint
  priority: low
```

`vault: ALL` fans out to every registered vault — see the
[ALL-vault parent/child](tasks.md) section.

## Atomic saves and validation

- Every save runs `yaml.safe_load` first. Parse errors are surfaced inline.
- Cron expressions are validated against APScheduler's parser. Bad expressions
  are rejected — the file is *never* written.
- The total bytes per file is capped at 1 MB.

## What is *not* in config

- Authentication. resman is bound to localhost by default and uses a single
  CSRF header (`X-Requested-With: resman`) for write endpoints. The
  `--public` flag relaxes CORS but does **not** add auth.
- Multi-host / clustering. resman is single-process by design.
