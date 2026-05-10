# Vaults

A vault is any directory that contains a `.obsidian/` folder. resman tracks
**registered** vaults (listed in `system.yaml`) and **discovered** vaults
(found by scanning paths under `scan_paths`).

## Registering a vault

Edit `config/system.yaml`:

```yaml
vaults:
  - name: vla6
    path: /tmp/val6
    tags: [research]
```

`name` must match `[a-zA-Z0-9_-]+`. `path` must be absolute. `tags` is free-form.

You can also register a vault via the **+ New Vault** button in the sidebar —
it walks you through picking a directory, optionally scaffolding `.obsidian/`,
and (optionally) bootstrapping a Claude session that runs the wiki plugin.

## Discovering vaults

If you set `scan_paths` in `system.yaml`:

```yaml
scan_paths:
  - /tmp
  - ~/Documents/vaults
```

resman walks each path up to two levels deep looking for `.obsidian/`
directories. Discovered-but-unregistered vaults appear in the **Unregistered**
section of the sidebar; click one to register it.

## Vault dot — what does the colour mean?

Beside each vault name in the sidebar:

| Dot | Meaning |
|-----|---------|
| green | active terminal session running for this vault |
| yellow | pending tasks but no active session |
| red | last task ended in `failed` or path missing |
| gray | nothing happening |

Priority is: red > green > yellow > gray (so an error always wins).

## Health modal

Click the `⚠` or `?` icon next to a vault to see:

- **Vault path** — and whether it exists on disk
- **`.obsidian/` present** — is this actually an Obsidian vault?
- **Wiki home found** — does `wiki/overview.md` exist? (See [Wiki](wiki.md).)
- **Last active session** — last time you spawned a terminal here
- **Last completed task** — last time a task finished or failed

## Removing a vault

Delete it from `system.yaml` and reload. resman never deletes vault
directories or files — removal is purely a config change.
