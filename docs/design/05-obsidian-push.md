# Obsidian Push

## Overview

`obsidian_push.py` writes a small status file — `_resman/status.md` — into each
registered vault directory every 60 seconds. Obsidian watches the vault via chokidar
and hot-reloads new files within seconds, so `_resman/status.md` appears in the graph
view as a normal node without any plugin or manual action. This gives the user ambient
health feedback inside Obsidian without opening resman. The push fires regardless of
window state — vault health should always be current.

## Status File Location and Content

- Directory: `<vault_path>/_resman/`
- File: `status.md`
- Created if absent; overwritten every 60s

Content format:

```markdown
# <vault-name> — <health-color>

Updated: 2026-05-05T10:01:00Z
Health: green
Terminal session active

[[_resman/status]]
```

## Health Priority Rule

Same priority as the vault sidebar dot — highest wins:

| Priority | Health | Condition |
|----------|--------|-----------|
| 1 | red | Last task failed |
| 2 | yellow | One or more tasks currently running |
| 3 | green | Active tmux session exists |
| 4 | gray | Idle (none of the above) |

Note: Blue (Obsidian open) is not included in push health — it cannot be reliably
detected from the server side.

## Push Algorithm (push_vault_status)

1. Determine health color from TaskManager + TmuxManager state
2. Build status file content string
3. Create `_resman/` directory if absent (`mkdir exist_ok=True`)
4. Write status file; wrap in `try/except OSError` — failure is non-fatal (log warning, continue)

`push_all_vaults()` calls `push_vault_status()` for every registered vault in sequence.

## Scheduling

ObsidianPush runs as a separate `GeventScheduler` job with a 60-second interval,
independent of the cron task scheduler and window state. It runs whether the window
is `active`, `between`, or `ended`.

## Pre-Implementation Validation (Phase 0 checklist)

Before writing any code, validate:

1. Open a vault in Obsidian. Manually create `_resman/status.md`. Confirm Obsidian
   detects the file without a restart (should appear in graph view within a few seconds).
2. Run a loop that overwrites the file every 60s for 10 minutes. Confirm no sync
   conflicts (particularly if the vault is iCloud or Obsidian Sync backed).
3. Add `_resman/` to the vault's `.gitignore`. Confirm git does not track it.

If steps 1–3 fail, reconsider the Obsidian-push approach before investing further.

## .gitignore Note

Each vault's `.gitignore` must contain `_resman/`. This should be documented in
`docs/overview.md` and added automatically by `tools/new-vault.sh` for new vaults.
Existing vaults require a one-time manual edit.

## Key Decisions

- **Fires regardless of window state** — health is operational data, not research work; should always be current
- **Non-fatal write failure** — OSError is caught, logged, and skipped; the server continues; the next 60s tick retries
- **Priority rule mirrors vault dot** — consistent mental model: same color in resman sidebar and in Obsidian graph
- **No plugin required** — status.md is a plain markdown file; Obsidian's built-in chokidar watch picks it up natively
- **`_resman/` in .gitignore** — prevents 60s write cycles from polluting git history in synced vaults

## Constraints

- Write must always be wrapped in `try/except OSError`
- Push interval is 60 seconds and must not be faster (avoid iCloud/Sync conflicts)
- `_resman/` must be excluded from git tracking

## Open Questions

- **iCloud / Obsidian Sync vaults** — 60s write cycles may conflict with sync services in edge cases. Requires manual validation with the specific sync backend in use before Phase 1 is marked done.
