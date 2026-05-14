---
noteId: "c2298a004f5611f18eaba108b9c533e7"
tags: []

---

# Vaults

A vault is any directory that contains a `.obsidian/` folder. resman tracks
**registered** vaults (listed in `resman.yaml`) and **discovered** vaults
(found by scanning paths under `scan_paths`).

## Registering a vault

Edit `config/resman.yaml`:

```yaml
vaults:
  - name: vla6
    path: /tmp/val6
    tags: [research]
```

`name` must match `[a-zA-Z0-9_-]+`. `path` must be absolute. `tags` is free-form.

You can also register a vault via the **+ New Vault** button in the sidebar —
it walks you through picking a directory, optionally scaffolding `.obsidian/`,
and (optionally) bootstrapping a Claude session. If `app.vault_default_root_path`
is set in `resman.yaml`, the wizard pre-fills the path input with that root
and starts the Browse picker there, so adding a vault that lives under the
common root only takes typing its folder name. The bootstrap session is
pasted with the contents of `tools/newValPrefix.md` (a plugin-presence check),
then `/claude-obsidian:wiki`, then `tools/newValSuffix.md` (copy of the
visual `workspace-visual.json` into the new vault's `.obsidian/`). Edit
those two files if you want different pre- or post-bootstrap behavior.

## Discovering vaults

If you set `scan_paths` in `resman.yaml`:

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

## The `↘` button (ingest URL shortcut)

Each vault row has a small `↘` button on the right. Click it to queue a
**wiki-ingest** task for that vault — resman prompts for a URL, posts a
task with `operation: wiki-ingest, params: {url}`, and switches to the
**Tasks** tab so you can watch it run. Equivalent to filling the trigger
form on the Tasks tab with `wiki-ingest` and the URL, but a single click
away.

## Removing a vault

Delete it from `resman.yaml` and reload. resman never deletes vault
directories or files — removal is purely a config change.
