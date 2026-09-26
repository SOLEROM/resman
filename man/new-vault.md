# New vault — what resman runs

A new vault is created from the **+** button in the sidebar (the New Vault
form). Each vault is an Obsidian folder whose wiki is built and maintained by
the **claude-obsidian** Claude Code plugin; resman does not ship that plugin,
it drives it. The form has two tabs, **Basic** and **Deep interview**. Both
share the vault name, path, category, tags and the *Scaffold the directory*
checkbox; they differ in how the wiki gets its definition. The **Skills** tab
shows the installed plugin version, checks that every plugin command resman
sends exists in it, and renders both messages below with the installed
plugin's folder filled in.

## The shared steps

1. **Scaffold** (optional; checkbox *Scaffold the directory*).
   `POST /api/vaults/scaffold` runs `tools/new-vault.sh <name> <path>`, which
   creates the folder with `.obsidian/`, `inbox/`, `_resman/` and a
   `README.md`, and adds `_resman/` to `.gitignore` (resman rewrites that
   folder every minute). Unchecked, the form registers an existing folder.
2. **Register.** `POST /api/vaults` adds the vault to `resman.yaml` with its
   name, path, category and tags. From here it is in the sidebar.

## Basic — today's process

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
   edit here.

## Deep interview — define the vault first

The Deep interview tab lays the process out as five numbered stages, one
message that runs them in a row:

1. **Brief** — *What is this vault for?*, a text box, optional: a paragraph
   or a whole document. **Load file…** reads a markdown or text file from
   your browser into it; the server only ever sees the text, never a path.
   The text is capped at 16000 characters and kept as a draft in your
   browser until a session has opened with it, so a failed spawn loses
   nothing.
2. **Interview** — the depth: *short* (one question per section the brief
   leaves open) or *full* (the whole tree, with follow-ups, up to the skill's
   `max_questions`). It runs through the **grilling** helper in the Terminal
   tab, one question at a time with a recommended answer each.
3. **Scaffold the wiki** — always: the plugin builds `wiki/` from the agreed
   brief.
4. **deepList** — on by default: the first ranked list of research values,
   `wiki/meta/deep-list.md`, from the brief and the fresh wiki, with the
   settings from **Skills → resman skills → deep-list**.
5. **Autoresearch** — on by default: the plugin's autoresearch on the open
   values of that list, *all* of them (the default) or *the top N* (1 to
   50), one full run after another, so the new wiki fills up from its own
   priorities in the same sitting. Every researched value is ticked ✓ on the
   list page as its run finishes. The research needs the list: unchecking
   deepList turns it off too. Every autoresearch run spends your Claude
   usage; the values a limited run does not reach stay open on the page for
   the Tasks view, and the next deepList run retires what was filled.

Submit refuses up front when no terminal is available, before anything is
scaffolded or registered. A research count outside 1 to 50 is refused the
same way. Otherwise resman scaffolds and registers as above,
opens the Claude session through the shared terminal (webterm; the legacy
stack needs ttyd), switches to the Terminal tab and pastes one message:

- `tools/newValPrefix.md` — the plugin check;
- your brief between two marker lines (`===== BEGIN BRIEF =====` /
  `===== END BRIEF =====`; the skill treats it as content, never as
  instructions), a line saying you are at the terminal to answer, then
  `/resman:vault-brief interview=<depth> …` with the settings from
  **Skills → resman skills → vault-brief**. The skill reads what the folder
  already holds (never other vaults), runs the **grilling** interview in the terminal,
  one question at a time with a recommended answer each, and writes
  `wiki/meta/brief.md` (purpose, mode, audience, scope in and out, seed
  domains, key questions, sources, entities, upkeep cadence, related vaults,
  the seed verbatim, the interview record) and `wiki/hint.json`, the vault
  card's label, summary and tags;
- `/claude-obsidian:wiki`, told to take Purpose, Mode and Owner from the brief
  page, keep `index.md`, `log.md` and `hot.md` under `wiki/`, link the brief
  from the index and the overview, and not ask for the purpose again;
- `tools/newValSuffix.md` — the workspace copy;
- the stages you checked: `/resman:deep-list …` with its stored settings,
  told to run once the wiki is scaffolded and let finish; then, for every
  open value of `wiki/meta/deep-list.md` (or the top *N*) in rank order,
  each row's *research with* line (`/claude-obsidian:autoresearch …`), one
  at a time, each finished and filed before the next, ticking the row's
  *done* box (✓) on the list page as it goes and changing nothing else
  there, and one closing line `research: <done> of <listed> values
  researched`.

An empty brief starts the interview from a blank page and from whatever the
folder holds (an existing folder's README, its inbox). The brief page then
feeds every later run: **deepList** derives its objective from it and
**Generate hint** reads it first. So one message takes the vault from your
summary through the interview, the scaffold, the ranked list and the first
research runs.

## Afterwards

Schedule the upkeep tasks you want — lint, hot cache, ingest, deepList — in
`schedule.yaml` (see [Tasks](tasks.md), [Scheduler](scheduler.md)). After a
Basic bootstrap, run **Generate hint** so the vault's card on the Vaults page
gets a label, summary and tags; the Deep interview has already written them,
and with its stages on it has also left `wiki/meta/deep-list.md` with the
researched values ticked ✓ and any the run did not reach still open: run
**Autoresearch a topic** on those from the Tasks view, or **deepList:
research values** to re-rank the list after the first pages landed (it moves
the ticked rows to its *Filled* list, ticks kept).

Without a bootstrap, open a Claude session in the vault later and run
`/claude-obsidian:wiki` yourself, or run the **Re-run wiki bootstrap** task:
non-interactive, it runs `/resman:vault-brief` with the interview off
(normalizing what the vault already says into the brief page, deciding
nothing on its own), then the plugin's scaffold from the brief, with the same
prefix and suffix and no stages; meant for re-runs. To define or re-define a vault's brief
on its own, run the **vaultBrief: define the vault** task and *attend* it to
answer the questions yourself.

## Changing the process

Edit `tools/newValPrefix.md` and `tools/newValSuffix.md` for different pre- or
post-bootstrap instructions; keep the `{plugin_dir}` placeholder wherever the
plugin's folder is meant. The plugin commands themselves live in
`control-plane/modules/plugin_commands.py`; the two messages are composed in
`control-plane/modules/new_vault.py`; the skill is
`skills/skills/vault-brief/SKILL.md` with its `settings.yaml`; the spec and
its decisions are `docs/vaultBrief-plan.md`.
