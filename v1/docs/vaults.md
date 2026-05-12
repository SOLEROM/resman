# Vault conventions

A resman vault is any directory containing a `.obsidian/` subdirectory.
Vaults live at any path on the filesystem; resman's `resman.yaml` is the
authoritative list.

## Required structure

```
<vault_path>/
├── .obsidian/        — Obsidian's own state directory
├── README.md         — vault summary (rendered in Docs tab)
└── _resman/          — written by ObsidianPush every 60s
    └── status.md
```

## Vault states

resman computes one **dot color** per vault using the priority rule:

| Priority | Color | Condition |
|---|---|---|
| 1 (highest) | red | Last task failed |
| 2 | yellow | A task is currently running |
| 3 | green | Has an active tmux session |
| 4 | blue | Obsidian is open for this vault |
| 5 (lowest) | gray | Idle / none of the above |

Hover the dot for a tooltip listing every condition that is currently true.

## Discovery (optional)

`scan_paths` in `resman.yaml` lists directories to scan (max depth 2) for
unregistered vaults. Found vaults appear in the sidebar below a divider
labelled "Unregistered" with a `[+ Register]` button. The scan is a
convenience layer; `resman.yaml` always remains authoritative.
