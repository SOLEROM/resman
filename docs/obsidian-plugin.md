# claude-obsidian plugin

resman drives each vault through the **claude-obsidian** Claude Code plugin
(plus the companion **claude-canvas** plugin). The plugin owns all the
wiki-mutating slash commands; resman composes them into window-gated tasks.
This page is the install + command reference. Per-vault authoring workflow
lives in the operator manual ([`man/wiki.md`](../man/wiki.md)).

> Upstream: <https://github.com/AgriciDaniel/claude-obsidian>

## Install

```bash
# 1. add the marketplace
claude plugin marketplace add AgriciDaniel/claude-obsidian

# 2. install the plugin (+ canvas companion)
claude plugin install claude-obsidian@claude-obsidian-marketplace
claude plugin install AgriciDaniel/claude-canvas
```

If `~/.claude/settings.json` already declares the marketplace (under
`extraKnownMarketplaces`, e.g. as `agricidaniel-claude-obsidian`), step 1 is
refused and the plugin is `claude-obsidian@agricidaniel-claude-obsidian`; resman
finds it under any marketplace name.

After install, seed a vault's Obsidian workspace with the plugin's visual
layout (run from inside the vault root):

```bash
cp ~/.claude/plugins/cache/<marketplace>/claude-obsidian/<version>/.obsidian/workspace-visual.json \
   .obsidian/workspace.json
```

resman's new-vault wizard does this automatically — see
[`man/vaults.md`](../man/vaults.md).

## Slash commands

These run in the Claude Code chat and operate on the current vault. They
handle linking, logging, and cross-referencing automatically — prefer them
over manual file editing.

| Command | Purpose |
| --- | --- |
| `/claude-obsidian:wiki-ingest <url\|file\|description>` | Fetch a source, extract entities/concepts, create or update the right note, cross-link, and append to `wiki/log.md`. |
| `/claude-obsidian:wiki-query <question>` | Ask in natural language; good answers are filed back into the vault. |
| `/claude-obsidian:autoresearch <topic>` | Autonomous loop — searches the web, synthesizes findings, files multiple linked pages. |
| `/claude-obsidian:save` | Preserve the useful parts of a conversation as a structured note. |
| `/claude-obsidian:defuddle <url>` | Strip ads/nav/boilerplate from a page before ingesting (40–60% fewer tokens). |
| `/claude-obsidian:wiki-lint` | Health check — broken links, orphans, missing frontmatter, empty sections. |
| `/claude-obsidian:canvas <description>` | Build an Obsidian canvas (visual board) from notes. |
| `/claude-obsidian:wiki-fold` | Roll up old `wiki/log.md` entries into a summary meta-page. |
| `/claude-obsidian:wiki` | Bootstrap or re-check a vault's wiki structure (used by the new-vault wizard). |
| `/claude-obsidian:update-hot-cache` | Refresh the per-vault `hot.md` context cache. |

## How resman uses them

resman never edits vault files directly. The task queue maps operations
(`wiki-ingest`, `wiki-ingest-prefix`, `wiki-lint`, `wiki-autoresearch`,
`wiki-canvas`, `wiki-update-hot-cache`, `wiki-bootstrap`) onto these
commands — see [`docs/design/06-task-management.md`](design/06-task-management.md)
for the operation → command mapping and
[`man/tasks.md`](../man/tasks.md) for the operator-facing view.

## resman's own skills

claude-obsidian is one of two skill **providers**. The other is resman's own
plugin folder, [`skills/`](../skills/README.md) (plugin name
`resman`, invoked as `/resman:<skill>`), loaded into every `claude` process
resman spawns with `--plugin-dir`, never installed. Both follow the same rule:
a skill runs inside a vault and writes markdown pages under `wiki/` in the
vault's conventions; resman renders them. Operations are keyed `wiki-*`
(claude-obsidian), `rs-*` (resman) and `run-*` (ad hoc), and the Tasks and
Skills views separate the providers. Design:
[`docs/design/17-skills.md`](design/17-skills.md); rollout:
[`docs/custom-skills-plan.md`](custom-skills-plan.md). Nothing here changes
how the claude-obsidian plugin is installed, updated or bootstrapped.
