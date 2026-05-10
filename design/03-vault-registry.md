# Vault Registry

## Overview

`vault_registry.py` owns the authoritative in-memory list of vaults. On startup it
loads vaults from `system.yaml`, validates each path, and optionally scans `scan_paths`
directories for unregistered vaults. It re-derives its state from `config_manager`
on every `config_reloaded` EventBus event, so vault changes made via the YAML editor
take effect without a server restart. The registry is the single point of truth for
vault status data consumed by the sidebar, status dots, and ObsidianPush.

## Vault Loading and Validation

On startup (and on `config_reloaded`):

1. Load all entries from `system.yaml` → these are **registered vaults**
2. For each registered vault, validate:
   - Path exists on disk → if not: show "path not found" warning (gray dot + distinct icon)
   - Path contains `.obsidian/` → if not: show "not an Obsidian vault" warning (gray dot + different icon)
   - These are **distinct** warnings with distinct icons in the UI
3. If `scan_paths` is configured, scan each listed directory (max depth: 2) for subfolders containing `.obsidian/` → these are **discovered but unregistered** vaults

## Vault Discovery (scan_paths)

- Unregistered vaults appear in the sidebar below a divider
- Each has a `[+ Register]` button that opens a form (name, tags, confirm path) and appends to `system.yaml`
- If `scan_paths` is empty or absent, the divider does not appear
- Scan depth is capped at 2 levels; paths resolving to filesystem roots are rejected

## New Vault Creation (two-step + optional bootstrap)

Vault creation is split so each side-effect is independently failable and
visible. The SPA wizard performs them in order; any step can be skipped:

| Step | Endpoint | Effect |
|------|----------|--------|
| 1. Scaffold | `POST /api/vaults/scaffold` | Runs `tools/new-vault.sh`; creates the directory, `.obsidian/`, `inbox/`, `_resman/`, README, and `_resman/` line in `.gitignore`. Skipped when registering a pre-existing vault |
| 2. Register | `POST /api/vaults` | Atomic-write append to `system.yaml`. Independent of step 1; succeeds even if the path already existed |
| 3. Bootstrap | `POST /api/sessions` (claude, with `initial_command: "/claude-obsidian:wiki"`) | Opens an interactive Claude REPL in the new vault and types the slash command after a short delay. The bootstrap may prompt — the user answers in the Terminal tab |

The wizard reports per-step status (info / ok / error) inside the same modal,
so partial failure (e.g., scaffold ok, register fails) is visible without
losing the entered values.

## Vault Health Check Modal

Triggered by clicking the `⚠` / `?` warn icon on a vault row. Backed by
`GET /api/vaults/{name}/health`; rendered as a modal table. Click
propagation is stopped on the icon so the row click does not also select
the vault.

| Check | Status |
|-------|--------|
| Vault path | absolute path string |
| Path exists on disk | ✓ / ✗ |
| `.obsidian/` present | ✓ / ✗ |
| Wiki home found (`wiki/overview.md`) | ✓ / ✗ |
| Last active session | ISO timestamp or "never" |
| Last completed task | ISO timestamp or "none" |
| Tags | comma-separated or "—" |

## Session Status for Status Dots

VaultRegistry does not own session state directly. The vault dot color is computed
from a combination of sources:
- Task status: from `TaskManager` (running, failed)
- tmux session: from `TmuxManager.session_exists_pattern(f"rsm-{vault.name}-*")`
- Obsidian open: platform-dependent detection (see Open Questions)

Priority rule (highest wins): Red > Yellow > Green > Blue > Gray.
See `10-frontend.md` for the full dot priority table.

## Key Decisions

- **system.yaml is authoritative** — vault list comes only from `system.yaml`; `scan_paths` is a convenience layer that surfaces discovered vaults but never auto-registers them
- **Distinct validation warnings** — path-not-found and not-an-obsidian-vault use different icons so the user knows which problem to fix
- **Re-derives on config_reloaded** — vault registry does not cache the config dict; it calls `config_manager.get_vault(name)` so edits via the YAML editor take effect immediately
- **No common root required** — vaults may be at any path on the filesystem (e.g., `/data/`, `/home/`, `/mnt/` simultaneously)
- **Vault name validation** — `[a-zA-Z0-9_-]` only; enforced at registration time

## Constraints

- `scan_paths` depth: maximum 2 levels
- `scan_paths` entries that resolve to filesystem roots must be rejected
- Vault names must match `[a-zA-Z0-9_-]`
- Registration always appends to `system.yaml` via the atomic write path in `config_manager`

## Open Questions

- **Blue dot (Obsidian open)** — detecting whether Obsidian has a specific vault open requires inspecting the process list (e.g., `ps aux | grep obsidian`). The approach for this detection is not specified in the plans; may need to be omitted or approximated in Phase 1.
