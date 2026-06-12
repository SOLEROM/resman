---
noteId: "6c771bc04f7111f18eaba108b9c533e7"
tags: []

---

# Configuration

## Overview

resman uses three config files: `resman.yaml` (app settings and vault registry),
`schedule.yaml` (cron task definitions), and `budget.json` (runtime window state).
`resman.yaml` is the single source of truth for vault paths — there is no implicit
discovery. All config saves are atomic and validated before the file is written.
On successful save, `config_manager.py` emits `config_reloaded` on the EventBus so
subscribers re-derive their state without a server restart.

The `ConfigManager` checks for `~/.resman.yaml` (per-user override) first; if present,
it becomes the authoritative config file and all UI saves write back to it. Otherwise,
it falls back to `<config_dir>/resman.yaml` (repo default). Legacy `system.yaml` is
still accepted with a deprecation warning for backward compatibility.

## resman.yaml Schema

```yaml
app:
  host: 127.0.0.1
  port: 5090
  tmux_socket: resman          # isolated tmux socket name
  tmux_prefix: "rsm-"
  scrollback_limit: 10000
  claude_cmd: "claude --dangerously-skip-permissions"
  obsidian_cmd: "flatpak run md.obsidian.Obsidian"
  ttyd_port_base: 7680         # port range for ttyd processes
  ttyd_port_max: 7999
  vault_default_root_path: /home/user/vaults   # optional; see below

window_budget:
  weekly_start: "Monday 09:00"
  weekly_end:   "Sunday 23:00"

vaults:
  - name: ai-agents-research
    path: /data/research/ai-agents-research
    tags: [ai, agents]

scan_paths:                    # optional; remove to disable vault discovery
  - /data/research
```

> The legacy `readme:` per-vault override was removed when the Docs tab was
> renamed to **Wiki**. Wiki content is now read from `<vault>/wiki/overview.md`
> by convention (with `hot.md` and `index.md` reachable from the toolbar);
> nothing in `resman.yaml` overrides it.

The `app:` block also accepts an optional `man_path:` — an absolute path to a
directory of `.md` files served by the **Help** tab. Defaults to `<repo>/man`
(i.e. `RESMAN_ROOT/man`, the manual shipped at the repo root).

`app.vault_default_root_path` (optional, absolute) is the starting point the
**New Vault** wizard uses to pre-fill its path input and seed the Browse
folder picker. Set it when every vault on this host lives under a common
parent (e.g. `/home/user/vaults`) so the wizard only requires the new
vault's folder name. The validator rejects relative paths and empty
strings at load time. Surfaced to the frontend via the extra
`vault_default_root` field on `GET /api/vaults` so the wizard doesn't need
a second round-trip.

`resman.yaml.example` ships with all fields present and inline comments. Users copy it; they never write YAML from scratch.

## schedule.yaml Schema

```yaml
cron_tasks:
  - name: weekly-lint-all
    cron: "0 8 * * 0"         # validated with CronTrigger.from_crontab() at load time
    vault: ALL
    operation: wiki-lint
    priority: low
```

## budget.json Schema

Written exclusively by the server in response to UI actions. Never edited manually.

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

## Key Decisions

- **Atomic writes** — all YAML saves use `.tmp` → `os.replace()` pattern; partial writes cannot corrupt live config
- **Validation before commit** — YAML must parse, result must be a dict, required vault fields must be present (`name`, `path`), cron strings must parse via `CronTrigger.from_crontab()`, file size must be ≤ 1 MB; any failure returns HTTP 400 without writing the file
- **EventBus on save** — `config_manager.py` emits `config_reloaded` after successful write; subscribers (`VaultRegistry`, `Scheduler`) re-derive state via the `get_vault(name)` accessor — they do not cache the raw config dict
- **budget.json write order** — always write file first, then update in-memory state; never the reverse
- **budget.json startup resilience** — missing → create with `window_state: between`; invalid JSON → reset to `between`; never crash
- **`resman.yaml.example`** — ships with repo; users copy it; annotated inline
- **Per-user override (`~/.resman.yaml`)** — if present, takes priority over repo default and becomes the write target for all config saves

## Constraints

- `yaml.safe_load()` required — never `yaml.load()` with arbitrary loader
- Config result must be validated as `dict` before use
- File writes must be rejected if content exceeds 1 MB
- Cron strings must be validated before APScheduler ever receives them
- `budget.json` corruption must never crash the server

## Open Questions

- None
