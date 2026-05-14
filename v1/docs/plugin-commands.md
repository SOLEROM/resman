---
noteId: "cac81cd04f5611f18eaba108b9c533e7"
tags: []

---

# claude-obsidian plugin command cheatsheet

resman composes plugin command strings from `modules/plugin_commands.py` —
never from user-supplied data. The mapping from operation to command is:

| Operation | Plugin command |
|---|---|
| `wiki-ingest` | (delegated to `tools/ingest.sh`) |
| `wiki-lint` | `/claude-obsidian:wiki-lint` |
| `wiki-autoresearch` | `/claude-obsidian:autoresearch <topic>` |
| `wiki-update-hot-cache` | `/claude-obsidian:update-hot-cache` |
| `wiki-bootstrap` | `/claude-obsidian:wiki` — bootstrap or check the vault's wiki structure, wrapped with `tools/newValPrefix.md` (plugin check) and `tools/newValSuffix.md` (copy visual `workspace.json`) |
| `run-prompt` | user-provided prompt (max 200 chars, printable ASCII) |
| `run-shell` | user-provided argument list (no shell expansion) |

### Two ways to bootstrap a vault

The `claude-obsidian:wiki` command is interactive — it can ask the user
questions about how the vault should be set up. resman exposes two ways
to run it, and they have different trade-offs.

**Wizard path (recommended for new vaults):** when the "Bootstrap wiki"
checkbox is left on, the new-vault wizard opens a Claude session inside
the new vault and pastes a single multi-line block — `tools/newValPrefix.md`
+ `/claude-obsidian:wiki` + `tools/newValSuffix.md` — into the prompt via
tmux's bracketed-paste mode. Claude reads the entire block as one message,
checks the plugin first, runs the bootstrap (any prompts it asks appear in
the Terminal tab so you can answer them), then copies the visual
`workspace.json`. Success is what you see in the session — there's no
auto-detected exit code.

**Task-queue path (`wiki-bootstrap` operation):** runs `claude -p` with the
same prefix-wrapped prompt non-interactively. This is one-shot — there's no
back-and-forth. If the command needs input, you won't be able to provide
it and the result may be incomplete. Use this only for re-runs on
already-bootstrapped vaults where you know no further input is needed
(e.g. as a periodic cron task to re-validate structure).

The prefix/suffix instruction files live at `tools/newValPrefix.md` and
`tools/newValSuffix.md` (sibling of `v1/`). Both are optional — missing
files are skipped silently so the bare bootstrap still runs on a stripped
checkout. Edit them to change what Claude does before or after the
bootstrap; no code change is needed.

The plugin is installed at the user level once:

```
claude plugin marketplace add AgriciDaniel/claude-obsidian
claude plugin install claude-obsidian@claude-obsidian-marketplace
```

Once installed it is available in every Claude Code session. resman never
installs the plugin per-vault.
