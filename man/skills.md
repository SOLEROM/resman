# Skills

The **Skills** tab (activity bar) shows where resman's operations come from.
resman never edits a vault itself: every operation is a Claude Code **skill**
that runs inside the vault and writes markdown pages under `wiki/`. resman
queues the run ([Tasks](tasks.md)), shows the pages ([Wiki](wiki.md)) and
tracks what you have read.

## Two providers, one rule

| provider | where it lives | invoked as | operation keys |
|---|---|---|---|
| **claude-obsidian** | installed per user with `claude plugin install`; the Skills tab finds the install and shows its version | `/claude-obsidian:<skill>` | `wiki-*` |
| **resman skills** | the repo's own plugin folder `skills/` (plugin name `resman`), loaded into every Claude run resman spawns with `--plugin-dir`, never installed | `/resman:<skill>` | `rs-*` |
| ad hoc | `run-prompt`, `run-shell`: you typed the command | as typed | `run-*` |

The rule both providers follow: a skill writes only under `wiki/`, in the
vault's conventions (frontmatter, `[[wikilinks]]`, `wiki/log.md` appended at
the top, `wiki/index.md` kept current), and can be run twice without
duplicating pages. Because of that, a page written by a resman skill shows up
in the Wiki tree with an unread dot, can be favorited and highlighted, and is
checked by `wiki-lint`, with no per-skill screen in resman.

## What the tab shows

- **Overview** — both providers. For claude-obsidian: version, scope, folder,
  how resman found it (Claude Code's plugin registry or the plugin cache), the
  optional claude-canvas companion. For resman skills: version, folder, how it
  is loaded. Then one table of everything resman sends, with a *Source*
  column and whether each provider provides it, and the exact
  `claude plugin …` commands to update the plugin on this machine.
- **New vault process** — [New vault](new-vault.md) plus the exact messages
  the New Vault form pastes, Basic and Deep interview (with its deepList and
  autoresearch stages at the form's defaults), with the installed plugin's
  folder filled in.
- **claude-obsidian `<version>`** — *Used by resman* / *Other skills* /
  *Commands* / *Plugin docs*: the plugin's files, rendered.
- **resman skills `<version>`** — *Wired to an operation* / *Helpers* /
  *Not wired yet* / *Commands* / *Docs*: the repo's own skills. *Helpers*
  are the skills the other skills call on their own plan, never a task;
  *Not wired yet* holds a folder that has no operation and is not a helper.
  A skill's page shows its
  `SKILL.md`, the line a task invokes it with (`/resman:<skill>`), and, when
  the skill ships `settings.yaml`, a **Settings** card (below).
- **Custom skill guide** — `skills/README.md`, the authoring contract.
- The activity-bar **badge** counts warnings across both providers: a skill
  resman sends that the provider lacks, a provider missing altogether, a
  skill folder whose frontmatter name differs from the folder, an unusable
  `settings.yaml`, or settings in `resman.yaml` for a skill that no longer
  exists. Zero is the normal state.
- **↻** re-reads both providers (after `claude plugin update` or `git pull`).

## Skill settings

A resman skill declares its knobs in `settings.yaml` beside its `SKILL.md`.
The skill's page renders them as a form: numbers with their allowed range,
text with its length limit, yes/no, a choice, or a list (one per line), each
with its help text. **Save** stores only the values that differ from the
defaults, under `skills.<skill>` in `resman.yaml` (or `~/.resman.yaml` when
that override is in use; the card names the file). **Reset to defaults**
forgets them. Like the Config tab's form, this save rewrites the yaml without
its comments. The card also shows the exact `key=value` line the next run
receives, and a task's log shows what a past run got.

The first resman skill is **deepList** (`/resman:deep-list`, task *deepList:
research values*): it ranks the research values worth a deep-research run for
the vault's objective into `wiki/meta/deep-list.md`, retires the values the
wiki has since filled and re-scores the rest on every run. Its settings are
the list size, the number of candidates per run, the minimum score, four
weights, two "filled" thresholds, a `focus` hint and an `exclude` list. A row
you tick ✓ on the page counts as filled on the next run, and so does a row
the research stage ticked; the *Filled* list keeps the ticks and the skill
never clears one. The **Deep interview** tab of the New Vault form runs it
as a stage right after a new vault's scaffold, followed by autoresearch on
the list's open values ([New vault](new-vault.md)). Spec and decisions:
`docs/deepList-plan.md`.

The second is **vaultBrief** (`/resman:vault-brief`, task *vaultBrief: define
the vault*): from your seed and what the vault folder holds it runs the
grilling interview at the chosen depth and writes `wiki/meta/brief.md` (what
the vault is for: purpose, mode, audience, scope, domains, key questions,
sources, entities, cadence, related vaults, the seed verbatim, the interview
record) and `wiki/hint.json`, the vault card's label, summary and tags. Its
settings are the depth (`none` / `short` / `full`), the question cap for a
full interview, and the owner name the scaffold writes. The **Deep interview**
tab of the New Vault form runs it before the plugin scaffolds a new vault
([New vault](new-vault.md)); the *Re-run wiki bootstrap* task runs it with the
interview off. Spec and decisions: `docs/vaultBrief-plan.md`.

**grilling** (`/resman:grilling`) is a helper, not a task: it interviews you
about a plan one question at a time, each with a recommended answer, until
the plan is shared, and writes nothing. The other resman skills call it on
their own plan before they act (vaultBrief's interview is grilling at the
chosen depth), so it is listed under *Helpers*, never as a task.

## Writing a resman skill

Read `skills/README.md` in the repo (the *Custom skill guide* page once
the tab shows it). In short: `skills/<name>/SKILL.md` with frontmatter `name`
equal to the folder and a short `description`; writes only under `wiki/`
(own folder with `_index.md`, `log.md`, `index.md`, frontmatter on every
page); re-runnable; no host paths. Check it for free with

```bash
claude --plugin-dir skills plugin details resman
.venv-ubuntu24/bin/python -m pytest -q tests/test_resman_skills.py
```

before a real run from the Tasks tab on a test vault.
