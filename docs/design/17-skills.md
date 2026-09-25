# Skills and operation providers

## Overview

resman does not touch vault files itself. Everything that reads or writes a
vault is a **Claude Code skill** run inside that vault, and every skill writes
**markdown pages under `<vault>/wiki/`**. resman queues skill runs (tasks),
shows the pages (Wiki tab), and tracks what the operator has read.

Until 2026-09 every skill came from one place: the **claude-obsidian** plugin,
installed per user with `claude plugin install`, inspected by the Skills tab
(`plugin_info.py`), and driven through the fixed operation list in
`task_manager.py` and `plugin_commands.py`. This document adds a second place
for skills, **resman's own plugin folder `skills/`**, and the model that
keeps the two apart in the Tasks and Skills views without forking any code
path. It is the design that `docs/custom-skills-plan.md` executes.

## Golden rule

> A skill, whoever wrote it, is a Claude Code skill that runs inside one vault
> and leaves its results as markdown pages under `wiki/`, in the vault's own
> conventions. resman never edits those files; it runs the skill and renders
> the pages.

What that buys: no per-skill UI. A page written by a resman skill shows up in
the Wiki tree, gets an unread dot, can be favorited, highlighted, searched and
linted by `wiki-lint`, exactly like a page the plugin wrote. Every new
capability is "one skill folder + one registry entry", never "one new tab".

The rule's fine print, binding on every resman skill:

- Write only under `wiki/` (a family of pages gets its own folder with an
  `_index.md`, like the plugin's `entities/`, `concepts/`, `meta/`), plus the
  plugin's shared files: append at the top of `wiki/log.md`, update
  `wiki/index.md` when pages are created or removed.
- Pages carry the vault's frontmatter (`type`, `status`, `created`, `updated`,
  `tags` at minimum) and Obsidian-flavored markdown with `[[wikilinks]]`.
- Never modify `.raw/`, `.obsidian/`, `_resman/` or anything outside `wiki/`.
- Re-runnable: running the skill twice does not duplicate pages (dated report
  files like `wiki/meta/lint-report-YYYY-MM-DD.md`, or overwrite in place).
- A machine-readable sidecar (like the existing `wiki/hint.json`) is allowed
  only when resman itself reads it, and the operation's registry entry says so.

## Providers

Every operation belongs to exactly one **provider**. The provider is a
property of the operation in the registry, not a naming convention, and it is
what the Tasks and Skills views filter and group on.

| provider id | label in the UI | what it is | operation key prefix | invocation |
|---|---|---|---|---|
| `obsidian` | claude-obsidian | the upstream plugin, installed per user; found by `plugin_info.locate()` | `wiki-` | `/claude-obsidian:<skill>` |
| `resman` | resman skills | the repo's own plugin folder `skills/`, loaded per run with `--plugin-dir` | `rs-` | `/resman:<skill>` |
| `adhoc` | ad hoc | `run-prompt`, `run-shell`: no skill, the operator typed the command | `run-` | as typed |

Operation keys keep a provider prefix on top of the registry attribute so that
`tasks.jsonl`, task logs, `schedule.yaml`, `remoteAgent.sh` output and greps
stay readable without a lookup. Existing keys do not change.

## The resman plugin folder

```
skills/                      ← a Claude Code plugin; the folder is the plugin
├── .claude-plugin/plugin.json     ← name "resman", version, description
├── skills/<name>/SKILL.md         ← one folder per skill (frontmatter: name, description)
│   └── references/*.md            ← optional supporting files the skill reads
├── commands/<name>.md             ← optional slash-command wrappers
├── README.md                      ← the authoring contract (rendered in the Skills tab)
└── CLAUDE.md                      ← the rules Claude follows when writing a skill here
```

The folder is a plugin in the same format claude-obsidian uses, so
`plugin_info`'s reader (skills, commands, docs, manifest) describes both with
one code path. It is **not installed**: every `claude` process resman spawns
gets `--plugin-dir <repo>/skills` (verified on this host with
`claude --plugin-dir skills plugin details resman`, which lists the
skills and their token cost without an API call). Consequences:

- The skills version with resman: a `git pull` updates them, no
  `claude plugin update`, no cache folder, no version pin.
- Edits take effect on the next task run and the next spawned session.
- The path comes from `resman_root`, never from a request.
- Each skill costs its always-on description tokens (~50 tokens per skill per
  session per `claude plugin details`); keep descriptions short.

`--plugin-dir` is added to non-interactive runs (`claude -p …`) and to the
interactive sessions resman spawns (Ops tab `+ Claude`, attend, new-vault
bootstrap), so `/resman:<skill>` works everywhere a resman session runs. It is
not added to sessions the operator opens outside resman.

## Operation registry

`modules/operations.py` becomes the single source of truth that
`task_manager.py`, `routes.py`, `scheduler.py`/`config_manager.py`,
`tools/remoteAgent.sh` and the SPA all read. One entry per operation:

```python
@dataclass(frozen=True)
class Param:
    key: str            # params[key]
    type: str           # "url" | "text" | "checkbox" | "argv"
    label: str
    required: bool = False
    max_len: int = 200  # text: printable ASCII, ≤ max_len
    placeholder: str = ""

@dataclass(frozen=True)
class Operation:
    key: str            # "wiki-lint", "rs-<skill>", "run-prompt"
    label: str          # "Lint wiki"
    group: str          # picker group: "Research" | "Wiki" | "Custom"
    provider: str       # "obsidian" | "resman" | "adhoc"
    kind: str           # "prompt" (claude -p, attendable) | "shell" (argv, not attendable)
    params: tuple[Param, ...] = ()
    skill: str = ""     # the provider skill/command it invokes ("wiki-lint", "vault-brief")
    desc: str = ""      # one line under the picker card
    note: str = ""      # longer note under the fields
    icon: str = "codicon-circle-small"
    confirm: str = ""   # confirm() text before submit (run-shell)
    remote: bool = False   # offered by tools/remoteAgent.sh
    build_prompt: Callable[[dict, "RunContext"], str] | None = None
    build_argv: Callable[[dict, "RunContext"], list[str]] | None = None
```

`RunContext` carries what builders need: `resman_root`, `vault_path`,
`claude_exe`, `settings` (the `skills.<skill>` mapping from resman.yaml for the
entry's skill, via `TaskManager.set_skill_settings`) and the `--plugin-dir`
arguments as a property. Built 2026-09-24 (plan phase 1); the SPA still reads
its own mirror until phase 2.

What the registry replaces:

| today | after |
|---|---|
| `task_manager.OPERATIONS` tuple | `tuple(operations.REGISTRY)` (same name kept for imports) |
| `task_manager._validate_params` if-chain | `operations.validate(op, params)` from `Param` specs |
| `task_manager._build_command` if-chain | `op.build_argv` / `[claude_exe, "-p", op.build_prompt(...), "--dangerously-skip-permissions", *plugin_dir_args]` |
| `task_manager.build_attend_prompt` if-chain | `op.build_prompt` when `op.kind == "prompt"` |
| `app.js` `OPERATIONS`, `operationIcon`, `ATTENDABLE_OPERATIONS`, `OP_GROUP_ORDER` | `GET /api/operations`, loaded once at boot into `state.operations` |
| `remoteAgent.sh` `ALLOWED_OPS` | `GET /api/operations` filtered by `remote: true` |
| `plugin_commands.PLUGIN_USES` (hand-kept) | still hand-kept (deriving it would make `plugin_commands` import the registry that imports it); `tests/test_operations.py` asserts every `obsidian` entry's `skill` is in it |

`plugin_commands.py` stays: it still owns the claude-obsidian command strings
and the new-vault prefix/suffix prompt. A sibling `resman_skills.py` owns the
resman provider: the plugin folder path, its manifest version,
`plugin_dir_args()` and `skill_prompt(name, args)` → `/resman:<name> <args>`.

### Task payloads

`Task.to_dict()` adds a derived `provider` field looked up from the registry
by `operation` (`"unknown"` when the key is gone from the registry, so old
tasks still render). Nothing new is written to `tasks.jsonl`; the event schema
is unchanged and needs no migration. `GET /api/tasks?provider=<id>` filters
server-side by the same lookup.

## Tasks view

Built 2026-09-24 (plan phase 2); the specifics are in 10-frontend.md.

- **Picker.** A segmented provider switch above the operation cards
  (`All · claude-obsidian · resman skills · ad hoc`) filters the cards; the
  functional group labels (Research / Wiki / Custom) stay inside each
  provider. Each card shows the provider's small glyph before its icon.
- **Cards.** A provider pill (`obsidian` / `resman` / `adhoc`, muted, one
  colour per provider) sits before the operation label, so a queue mixing
  both providers reads at a glance.
- **Queue filter.** A third select in the queue toolbar, `all sources /
  claude-obsidian / resman skills / ad hoc`, next to priority and state; it
  filters client-side like the other two and is remembered in `localStorage`.
- **Params and attend.** Unchanged: fields render from `op.params`, attend is
  offered when `op.kind == "prompt"`; both now read `state.operations`.

## Skills view

Built 2026-09-24 (plan phase 3), plus a **Settings** card on a resman skill's
page for its `settings.yaml` knobs (`GET`/`POST /api/skills/settings`,
values under `skills.<skill>` in resman.yaml; spec in `docs/deepList-plan.md`).
The first skill, **deepList**, shipped the same day (phase 4), followed by
the first **helper skill**, `grilling`: called by other skills on their own
plan, writes nothing, has no operation, so *Not wired yet* is its permanent
home rather than a to-do.

The tab becomes a view over **providers**, same page structure for each:

```
Overview                          ← both providers; warnings merged; the badge counts all
New vault process                 ← unchanged (claude-obsidian bootstrap)
▸ claude-obsidian 1.6.0           ← Used by resman · Other skills · Commands · Plugin docs
▸ resman skills 0.1.0             ← Wired to an operation · Not wired yet · Commands · Docs
Custom skill guide                ← renders skills/README.md (the authoring contract)
```

`GET /api/skills/summary` returns `providers: [{id, label, installed, version,
path, located_by, skills, commands, docs, uses, warnings}, …]` and a top-level
`warnings` union. `GET /api/skills/file?provider=<id>&path=<rel>` reads a
markdown file inside that provider's folder (same traversal rules; `provider`
defaults to `obsidian` so existing links keep working). The Overview's "What
resman sends" table gains a Source column and lists the resman operations
too; a registry entry whose skill folder is missing is a warning exactly like a
plugin command the installed plugin lacks.

## Security

Same constraints as `11-security.md`, restated for the new provider:

- Prompts are composed from registry code plus validated params only. Free
  text params stay ≤ 200 printable ASCII characters.
- `--plugin-dir` always points at `resman_root / "skills"`; the request
  body cannot name a plugin folder, a skill file, or a command line.
- resman skills run with `--dangerously-skip-permissions` inside vaults, like
  the plugin's. A skill is code: it goes through review and the test suite
  (frontmatter valid, name matches folder, no host paths, no writes outside
  `wiki/` in its instructions) before it gets a registry entry.
- `run-shell` keeps its acknowledgment; it is never how a resman skill runs.

## Key decisions

- **A provider is a registry attribute, prefixes are a courtesy.** Filtering
  by `startsWith("wiki-")` in the SPA would have been faster to write and
  wrong the first time an operation moves. The prefix stays because humans
  read `tasks.jsonl`.
- **`--plugin-dir`, not `claude plugin install`.** Installing copies the
  folder into `~/.claude/plugins/cache/` and pins a version; the Skills tab
  would then warn about drift between the repo and the install. Loading from
  the repo per run has no install state at all. Cost: one flag on every spawn.
- **Plugin name `resman`, folder `skills/`.** `/resman:<skill>` is what
  the operator types in a REPL. The folder was first named `resmanSkills/` to
  avoid the nested `skills/skills/<name>/` that the plugin format imposes; it
  was renamed to `skills/` on 2026-09-24 at the operator's request, so a
  skill now lives at `skills/skills/<name>/SKILL.md` and the folder is loaded
  with `--plugin-dir skills`.
- **Derived `provider`, unchanged JSONL.** The operation key already identifies
  the provider through the registry; storing it twice would need a migration
  and could disagree with the registry later.
- **One registry, served to the SPA.** The client-side `OPERATIONS` mirror
  was a documented shortcut ("no round-trip to learn what's next door"); with
  two providers and skills that come and go, the mirror is where the two
  views would drift apart first.
- **No new tab for a new skill.** If a resman skill needs a renderer the Wiki
  tab does not have, that is a Wiki tab feature (like highlights or
  favorites), not a skill-specific page.

## Open questions

- Should `--plugin-dir skills` also be added to the Ops tab's plain
  `+ Claude` sessions (recommended, so `/resman:*` is always available), or
  only to task runs and attend? Decide in plan phase 1; default yes.
- `claude plugin eval` can run a skill's eval cases (`skills/evals/`)
  against a vault fixture. Worth wiring as an opt-in test once the first skill
  exists; it spends real Claude usage, so never in the default suite.
- Whether `schedule.yaml` cron entries should reject unknown operation keys at
  save time (today only the key's presence is validated). Recommended yes, via
  the registry, in phase 1.
