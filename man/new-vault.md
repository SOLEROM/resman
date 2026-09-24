# New vault — what resman runs

A new vault is created from the **+** button in the sidebar (the New Vault
wizard). Each vault is an Obsidian folder whose wiki is built and maintained
by the **claude-obsidian** Claude Code plugin; resman does not ship that
plugin, it drives it. The **Skills** tab shows the installed version and
checks that every plugin command resman sends exists in it.

## The steps

1. **Scaffold** (optional; checkbox *Scaffold the directory*).
   `POST /api/vaults/scaffold` runs `tools/new-vault.sh <name> <path>`, which
   creates the folder with `.obsidian/`, `inbox/`, `_resman/` and a
   `README.md`, and adds `_resman/` to `.gitignore` (resman rewrites that
   folder every minute). Unchecked, the wizard registers an existing folder.
2. **Register.** `POST /api/vaults` adds the vault to `resman.yaml` with its
   name, path, category and tags. From here it is in the sidebar.
3. **Bootstrap the wiki** (optional; checkbox *Bootstrap wiki*). resman opens
   an interactive Claude session in the vault (Ops tab) and pastes one
   message built from three parts:
   - `tools/newValPrefix.md` — check the claude-obsidian plugin is installed;
   - `/claude-obsidian:wiki` — the plugin's setup: it asks what the vault is
     about and scaffolds `wiki/` (index, hot cache, overview, log);
   - `tools/newValSuffix.md` — copy the plugin's `workspace-visual.json` into
     the vault as `.obsidian/workspace.json`, so Obsidian opens with the
     plugin's layout.

   The session is interactive: answer the plugin's questions in the terminal.
   Where the prompt files write `{plugin_dir}`, resman fills in the folder of
   the plugin version installed **now**, so an update of the plugin needs no
   edit here. The Skills tab's *New vault process* page shows the exact
   message with that folder filled in.
4. **Afterwards.** Run the **wiki-hint** task on the vault so its card on
   the Vaults page gets a label, summary and tags (it uses the plugin's
   `wiki-query` skill), and schedule the upkeep tasks you want — lint, hot
   cache, ingest — in `schedule.yaml` (see [Tasks](tasks.md),
   [Scheduler](scheduler.md)).

Without step 3, open a Claude session in the vault later and run
`/claude-obsidian:wiki` yourself, or run the **Re-run wiki bootstrap** task
(non-interactive, with the same prefix and suffix; meant for re-runs).

## Changing the process

Edit `tools/newValPrefix.md` and `tools/newValSuffix.md` for different pre- or
post-bootstrap instructions; keep the `{plugin_dir}` placeholder wherever the
plugin's folder is meant. The plugin commands themselves live in
`control-plane/modules/plugin_commands.py`.
