# skills — resman's own vault skills

This folder is a Claude Code plugin named **`resman`**. resman loads it into
every `claude` process it spawns with `--plugin-dir <repo>/skills`, so a
skill here is invoked as `/resman:<skill>` inside a vault, next to the
claude-obsidian plugin's `/claude-obsidian:<skill>`. It is never installed
with `claude plugin install`: it versions with the repo and takes effect on the
next task run.

The Skills tab renders this page as *Custom skill guide*. The design is
`docs/design/17-skills.md`; the plan that wires the folder into the app is
`docs/custom-skills-plan.md`. Skills so far: **deep-list** (wired to the
operation `rs-deep-list`), **vault-brief** (operation `rs-vault-brief`, and the
Deep interview tab of the New Vault form runs it before the plugin scaffolds a
new vault; spec `docs/vaultBrief-plan.md`) and **grilling** (a helper, see
below).

## The golden rule

A skill runs inside one vault and leaves its results as **markdown pages under
`wiki/`**, in the vault's own conventions. resman never edits those files; it
runs the skill and renders the pages. Everything below follows from that.

## Layout

```
skills/
├── .claude-plugin/plugin.json     name "resman", version, description
├── skills/<name>/SKILL.md         one folder per skill
│   └── references/*.md            optional files the skill tells Claude to read
├── commands/<name>.md             optional slash-command wrappers (rarely needed)
├── README.md                      this contract
└── CLAUDE.md                      the rules Claude follows when editing here
```

## Writing a skill

`skills/<name>/SKILL.md`:

```markdown
---
name: <name>                       # must equal the folder name; lowercase, hyphens
description: >
  One paragraph: what the skill does and the pages it writes. End with
  trigger phrases: "Triggers on: …". Keep it short; it is always-on context
  (~50 tokens per skill in every session).
---

# <name>: <one-line title>

<the procedure Claude follows, as numbered steps>
```

Rules every skill obeys:

1. **Writes only under `wiki/`.** A family of pages gets its own folder with an
   `_index.md` (`wiki/<family>/_index.md`), like the plugin's `entities/`,
   `concepts/`, `meta/`. Never touch `.raw/`, `.obsidian/`, `_resman/`, or
   anything outside `wiki/`.
2. **Keeps the shared files current.** Append the operation at the **top** of
   `wiki/log.md`; update `wiki/index.md` when pages are created or removed.
3. **Frontmatter on every page:** `type`, `status`, `created`, `updated`,
   `tags` at minimum, plus whatever the family needs. Obsidian-flavored
   markdown; link with `[[Note Name]]`.
4. **Re-runnable.** Running twice must not duplicate pages: overwrite in place,
   or use dated files (`wiki/meta/<report>-YYYY-MM-DD.md`).
5. **Reads before it writes.** Start from `wiki/hot.md`, then `wiki/index.md`,
   then the pages it needs, the same reading order the plugin's skills use.
6. **No side effects.** No network calls unless the skill is about fetching,
   no shell beyond reading the vault, no writes to the user's home. The skill
   runs with `--dangerously-skip-permissions`.
7. **Sidecars are the exception.** A machine-readable file (like the plugin's
   `wiki/hint.json`) only when resman reads it, and the operation's registry
   entry documents it.
8. **No host paths** in any file here (`tests/test_no_host_paths.py` and
   `tests/test_resman_skills.py` fail otherwise).

## Helper skills

A **helper** is a skill the other skills call, never the operator from the
Tasks tab: it writes nothing under `wiki/` and gets **no registry entry**.
It declares itself with `metadata: {role: helper}` in its frontmatter (the
Agent Skills `metadata` map), which puts it under *Helpers* in the Skills
tab; a folder without that marker and without a registry entry is listed
under *Not wired yet*, and a helper that does get a registry entry is a
warning. Rules 1 and 2 above do not apply to it; 3 to 8 do (nothing outside
the vault, no side effects, no host paths). Say so in its description too.
The first helper is
**grilling** (`skills/grilling/`): a relentless one-question-at-a-time
interview about a plan, each question with a recommended answer, which a
skill runs on its own plan before it acts. A helper has no `settings.yaml`;
the caller's settings are the knobs.

## Wiring a skill into the app

One registry entry in `control-plane/modules/operations.py` (after plan
phase 1):

```python
Operation(key="rs-<name>", label="<Label>", group="Wiki" | "Research",
          provider="resman", kind="prompt", skill="<name>",
          params=(…), desc="<one line>", remote=False,
          build_prompt=lambda p, ctx: resman_skills.skill_prompt("<name>", p.get("…", "")))
```

Then `tests/test_operations.py` and `tests/test_resman_skills.py` check that
the folder and the entry agree. Nothing else: the Tasks view, the Skills view,
the scheduler's `schedule.yaml`, and the Wiki tab pick the skill up from the
registry and from the pages it writes.

## Checking a skill without spending usage

```bash
claude --plugin-dir skills plugin details resman   # lists skills + token cost
.venv-ubuntu24/bin/python -m pytest -q tests/test_resman_skills.py tests/test_operations.py
```

A real run (spends Claude usage) is a task from the Tasks tab against the test
vault, then the page in the Wiki tab.
