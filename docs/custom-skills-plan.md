# Custom skills plan — housekeeping before the first resman skill

> **STATUS (2026-09-24): phases 0–4 done; phase 5 (periphery) open.** The
> design, the plan, the backend registry, the Tasks view on the registry (source
> switch, pill, filter), the per-provider Skills tab with the settings form, and
> the first skill **deepList** (`skills/skills/deep-list/`, operation
> `rs-deep-list`) are built and tested; `skills/skills/grilling/` (same day)
> is a **helper skill** the other skills call, deliberately without an
> operation. Not yet done: `tools/remoteAgent.sh`
> still carries its static operation list, the Config tab's cron Operation
> select is not grouped by provider, and no `claude plugin eval` case exists.
> A real run of deepList on a vault has not been made yet; it spends usage.
> Since 2026-09-26 the Deep interview tab offers it as a stage after a new
> vault's scaffold, with autoresearch on the top values after it.
> The second skill, **vaultBrief** (`skills/skills/vault-brief/`, operation
> `rs-vault-brief`, the New Vault form's Deep interview tab; spec
> `docs/vaultBrief-plan.md`), was built on 2026-09-25 with the same recipe.
> The first real skill is defined only after phase 3;
> its spec is `docs/deepList-plan.md` (approved 2026-09-24), which also
> adds per-skill **settings** (schema in the skill folder, values in
> `resman.yaml`, form in the Skills tab) to phases 1 and 3 below.

**Why.** resman is built on the claude-obsidian plugin: every operation is a
plugin skill that maintains markdown pages in a vault's `wiki/`. That stays the
golden rule (see `docs/design/17-skills.md`). We now want skills of our own, in
the same shape, and the app must keep the two sources apart wherever the
operator looks: the Tasks view (trigger, cards, queue filter) and the Skills
view. This plan is the housekeeping that makes "add a skill" a one-folder,
one-entry change, done **before** the first skill so the skill does not have to
carry the refactor.

**Reference reading, in order:** `docs/design/17-skills.md` (model),
`docs/design/06-task-management.md` (queue), `control-plane/modules/task_manager.py`
(`OPERATIONS`, `_validate_params`, `_build_command`, `build_attend_prompt`),
`plugin_commands.py`, `plugin_info.py`, `static/js/app.js` (`OPERATIONS`,
`renderOpCards`, `renderTasks`, `taskCardHTML`), `static/js/skills.js`,
`tests/test_plugin_info.py::test_every_plugin_command_resman_sends_is_listed_in_uses`.

## Decisions taken in phase 0

| # | decision | why |
|---|---|---|
| D1 | resman's skills live in the repo as a Claude Code plugin folder, `skills/`, plugin name `resman`, invoked as `/resman:<skill>` | same format as claude-obsidian, so `plugin_info`'s reader describes both; versions with the repo |
| D2 | loaded per run with `claude … --plugin-dir <resman_root>/skills`, never installed | no cache copy, no version pin, no drift warning; verified with `claude --plugin-dir skills plugin details resman` |
| D3 | `--plugin-dir` goes on every `claude` resman spawns: `-p` tasks, attend, new-vault bootstrap, and the Ops tab's `+ Claude` | `/resman:*` then works everywhere a resman session runs; cost is ~50 always-on tokens per skill |
| D4 | provider ids `obsidian` / `resman` / `adhoc`; operation key prefixes `wiki-` / `rs-` / `run-` | provider is a registry attribute (what the UI filters on); the prefix keeps logs and yaml readable |
| D5 | one backend registry (`modules/operations.py`) served by `GET /api/operations`; the SPA's `OPERATIONS` mirror is removed | two providers plus skills that come and go make a hand-kept mirror the first place the views drift |
| D6 | `provider` is derived in `Task.to_dict()`, never written to `tasks.jsonl` | no migration, cannot disagree with the registry |
| D7 | a resman skill writes only under `wiki/` (own folder + `_index.md`, `log.md` top-append, `index.md` update), with the vault's frontmatter, re-runnable | the golden rule; the Wiki tab, unread, favorites, highlights and `wiki-lint` cover the pages for free |
| D8 | the first custom skill is defined after phase 3, not before | it should be a folder and a registry entry, not the carrier of the refactor |

## Phase 1 — backend registry (no UI change) — done 2026-09-24

Goal: one source of truth for operations; behaviour of every existing
operation identical; the resman provider wired but with zero operations.

As built, two deviations from the list below: `plugin_commands.PLUGIN_USES`
stays hand-kept (deriving it would make `plugin_commands` import the registry
that imports it); `tests/test_operations.py` asserts every obsidian entry's
`skill` is in it instead. And per D3 the `--plugin-dir` flag is appended to
**every** prompt-kind operation, not only `rs-*`, so the ten old command lines
are byte-identical only on a checkout without `skills/` (the tests'
temp roots); on this checkout they end with the flag. Verified live: the
server starts on a spare port, `/api/operations` lists the ten operations with
their providers, `/api/tasks?provider=` filters, `/api/config/structured`
carries `operation_providers`.

1. `control-plane/modules/operations.py` (new): `Param`, `Operation`,
   `RunContext`, `REGISTRY: dict[str, Operation]` in today's display order,
   `validate(op, params) -> dict` (moves `_validate_params`' rules onto `Param`
   specs: url scheme check, text ≤ `max_len` printable ASCII, checkbox → bool,
   argv list of str), `for_provider(pid)`, `public(op) -> dict` (everything but
   the builders), `provider_of(key) -> str` (`"unknown"` for a missing key).
   Builders for the ten existing operations move here from `_build_command`
   and `build_attend_prompt`; they call `plugin_commands` exactly as today.
2. `control-plane/modules/resman_skills.py` (new): `PLUGIN_NAME = "resman"`,
   `plugin_dir(resman_root) -> Path`, `manifest(resman_root) -> dict`,
   `plugin_dir_args(resman_root) -> list[str]` (`["--plugin-dir", str(path)]`,
   empty list when the folder is missing so a checkout without it still runs),
   `skill_prompt(name, args="") -> str`.
3. `task_manager.py`: `OPERATIONS = tuple(operations.REGISTRY)`;
   `create_task` validates through `operations.validate`; `_build_command`
   becomes: resolve `op`, build `RunContext`, `op.build_argv(...)` for
   `kind == "shell"`, else `[claude_exe, "-p", op.build_prompt(...),
   "--dangerously-skip-permissions", *plugin_dir_args]`; `build_attend_prompt`
   returns `op.build_prompt(...)` for `kind == "prompt"`. `Task.to_dict()` adds
   `provider`. `list()` gains `provider=`.
4. `session_plan.py` / `webterm_integration.py` / `session_manager.py`: the
   Claude argv for a spawned session appends `plugin_dir_args` (D3). The
   configured `claude_cmd` string is untouched; the flag is appended with
   `shlex.quote`.
5. `routes.py`: `GET /api/operations` → `{"operations": [public(op)…],
   "providers": [{id, label}…]}`; `GET /api/tasks?provider=`;
   `/api/config/structured` keeps `operations` (now from the registry) and adds
   `operation_providers`.
6. `config_manager.validate_schedule_yaml`: reject an `operation` that is not
   a registry key (today only presence is checked). Error text names the key.
7. Per-skill settings (needed by deepList, `docs/deepList-plan.md`):
   `resman_skills.load_settings_schema(skill)` reads
   `skills/skills/<skill>/settings.yaml`; `defaults`, `validate_settings`,
   `effective_settings(skill, config, overrides)` (defaults ← yaml ← per-task),
   `render_args` → `key=value` tokens; `RunContext.settings`.
   `config_manager.validate_resman_yaml` validates a `skills:` section
   against every schema (bad value → error naming the key; unknown skill →
   warning); `ConfigManager.skills` property.
8. `plugin_commands.PLUGIN_USES` becomes derived: obsidian operations' `skill`
   fields plus the two non-operation uses (`wiki` from the bootstrap prompt,
   `wiki-ingest` from `tools/ingest.sh`) kept as explicit entries. The existing
   grep test keeps guarding it.

Tests (write first): `tests/test_operations.py` — every registry key has a
provider in the known set and the matching prefix; every `obsidian` op's
`skill` is in `PLUGIN_USES`; every `resman` op's `skill` has
`skills/skills/<skill>/SKILL.md`; `validate` reproduces each rule the old
`_validate_params` tests assert (port those asserts, do not delete them);
`public()` has no callables. `test_task_manager.py`: the ten command lines
`_build_command` produces are byte-identical to today (capture them in a
table before refactoring), plus `rs-*` runs carry `--plugin-dir <root>/skills`
last. `test_routes.py`: `/api/operations` shape, `?provider=` filter,
`schedule.yaml` with an unknown operation is a 400. `test_session_manager.py` /
`test_webterm_integration.py`: spawned argv ends with the plugin-dir flag.
`tests/test_resman_skills.py` (exists from phase 0) keeps pinning the folder.

Done when: the full suite passes, `git diff` of `_build_command` output for
the ten operations is empty, and the SPA still works unchanged (it has not
been touched yet).

## Phase 2 — Tasks view: provider everywhere — done 2026-09-24

As built: the pure helpers live in `static/js/tasks-core.js` (`tasksCore`),
`app.js` loads `GET /api/operations` first in `init()`, and
`tests/test_tasks_browser.py` drives the switch, the pill and the filter in a
real browser against a pre-seeded `tasks.jsonl`.

1. `app.js`: at boot, `state.operations = await api("/api/operations")`;
   delete the `OPERATIONS` const, `operationIcon`, `ATTENDABLE_OPERATIONS`,
   `OP_GROUP_ORDER`; `orderedOpGroups`, `renderOpCards`, `selectOp`,
   `renderOpFields`, `submitTriggerForm`, `taskActions`, `taskCardHTML` read
   `state.operations` (`op.icon`, `op.kind === "prompt"`, `op.group`,
   `op.provider`). Keep the functions' names so `tests/test_js_*` and the
   browser tests keep their hooks.
2. Picker: a segmented provider switch `#t-provider` above `#t-op-list`
   (`All · claude-obsidian · resman skills · ad hoc`); cards filtered by it;
   selection remembered in `localStorage` (`resman-task-provider`).
3. Cards: a provider pill before the operation label; CSS classes
   `.provider-obsidian`, `.provider-resman`, `.provider-adhoc` with muted
   colours from the theme tokens (works in all four themes).
4. Queue: third select `#task-source-filter`; `renderTasks` applies it with
   the two existing filters; remembered in `localStorage`.
5. `index.html`: the new controls; `style.css`: the pills and switch.

Tests: `tests/js/tasks-provider.test.mjs` (node, dependency-free like
`wiki-marks-core.test.mjs`) for the pure filter/group helpers extracted into
`static/js/tasks-core.js`; `tests/test_wiki_marks_browser.py`-style browser
test that the picker switch hides cards and the queue filter hides cards, and
that a re-run pre-fills the right provider's card. A grep test asserts
`app.js` no longer contains `"wiki-lint"` or any other operation key literal
(the sidebar `↘` ingest shortcut is the one allowed exception, listed).

Done when: no operation key is hard-coded in JS except the listed exception,
and a queue with tasks from both providers can be filtered to either.

## Phase 3 — Skills view: two providers, one layout — done 2026-09-24

As built: `plugin_info.describe(root)` / `read_file_from(root, rel)` are the
provider-agnostic reader, `resman_skills.summary(root, uses, stored_skills)`
the resman side; `GET /api/skills/summary` returns `providers: [obsidian,
resman]` plus the union `warnings`; `GET /api/skills/file?provider=`;
`GET/POST /api/skills/settings`; `tests/test_skills_browser.py` covers the
tree and a save/reset round trip into resman.yaml.

1. `plugin_info.py`: split `summary()` into `describe(path) -> {manifest,
   skills, commands, docs}` (the reader, provider-agnostic) and
   `obsidian_summary()` (locate + describe + uses + warnings, today's
   behaviour). `read_file(rel, root)` takes the root explicitly.
2. `resman_skills.py`: `summary(resman_root)` → same shape as
   `obsidian_summary()`: `installed` (folder + manifest present), `version`
   from the manifest, `located_by: "repo"`, `uses` from the registry's
   `resman` operations, warnings for a registry skill without a folder and
   for a `SKILL.md` whose frontmatter `name` differs from its folder.
3. `routes.py`: `/api/skills/summary` → `{providers: [obsidian, resman],
   warnings: union}`; `/api/skills/file?provider=&path=` (default
   `obsidian`); `/api/skills/guide` → `skills/README.md`.
4. `skills.js`: tree per the layout in `17-skills.md`; Overview renders one
   "Installed" table per provider and a Source column in "What resman sends";
   badge counts the union; "Custom skill guide" leaf renders the README.
5. `index.html` tab title/tooltip: "Skills — claude-obsidian and resman's own
   skills, and the new-vault process".
6. Per-skill **Settings** card on a resman skill's page (schema from its
   `settings.yaml`, values from `skills.<skill>` in the yaml): `GET
   /api/skills/settings?skill=`, `POST /api/skills/settings` (validate → merge →
   `save_resman_data`, CSRF); Save / Reset to defaults; shows the file it
   writes. Spec: `docs/deepList-plan.md` → Settings.

Tests: `test_plugin_info.py` keeps passing against `obsidian_summary()`;
`test_resman_skills.py` grows `summary()` cases (empty folder → installed,
zero skills, zero warnings; registry skill without folder → one warning;
name/folder mismatch → one warning); `test_routes.py` covers the new shape,
`?provider=resman` file reads and the traversal rejections for both roots.

Done when: with the plugin installed and `skills/` holding no skills,
the Skills tab shows both providers, zero warnings, and the guide page.

## Phase 4 — the first resman skill: deepList — done 2026-09-24

Built per `docs/deepList-plan.md`: `skills/skills/deep-list/SKILL.md` +
`settings.yaml`, operation `rs-deep-list` (Research group, one optional
per-task `focus`), tests in `test_operations.py` and `test_resman_skills.py`.
The generic recipe for the next skill:

1. `skills/skills/<name>/SKILL.md` per `skills/README.md`
   (frontmatter `name` = folder, one-paragraph `description` with trigger
   phrases, then the procedure). Pages it writes: `wiki/<family>/…` with
   `_index.md`, frontmatter, `log.md` top-append, `index.md` update.
2. One `Operation` in `operations.py`: `key="rs-<name>"`, `provider="resman"`,
   `kind="prompt"`, `skill="<name>"`, `build_prompt` →
   `resman_skills.skill_prompt("<name>", params…)`, `remote=` as decided.
3. `python -m pytest tests/test_operations.py tests/test_resman_skills.py`
   (folder/registry consistency), then the whole suite.
4. Free check: `claude --plugin-dir skills plugin details resman`
   lists the skill. Paid check (one run, in a test vault): the `rs-<name>`
   task from the Tasks tab, then the page in the Wiki tab.
5. Docs: a row in `man/tasks.md`'s Operations table (Source column), a line
   in `man/skills.md`, `00-README.md` additions table.

## Phase 5 — periphery

1. `tools/remoteAgent.sh`: `ALLOWED_OPS` from `GET /api/operations` filtered
   by `remote == true` (curl + python3, both already required); `--list-tasks`
   prints the provider; `--source <id>` filter. `run-prompt`/`run-shell` stay
   `remote: false`. `docs/remote-agent.md` and `man/remote-agent.md` updated.
2. Config tab: the cron entry's Operation select groups options by provider
   (`operation_providers` from `/api/config/structured`).
3. Optional, opt-in: `skills/evals/` with `claude plugin eval` cases for
   each skill against a fixture vault; a `tests/` marker that skips unless
   `RESMAN_RUN_SKILL_EVALS=1` (spends real usage).
4. `tests/test_no_host_paths.py` already covers `.json`/`.md`? It covers
   `.json`; `.md` is not scanned. Add `skills/**/*.md` to a small
   dedicated check so a skill never bakes a host path into its instructions.

## Test and verification summary

| phase | new tests | must still pass |
|---|---|---|
| 0 | `tests/test_resman_skills.py` (manifest parses, name `resman`, skill folders well-formed, none yet) | whole suite (`.venv-ubuntu24/bin/python -m pytest -q -rs`) |
| 1 | `test_operations.py`; command-line snapshot table in `test_task_manager.py`; `/api/operations`; schedule validation; spawn argv | `test_plugin_info.py` grep test, `test_remote_agent.py` |
| 2 | `tests/js/tasks-provider.test.mjs`; browser filter test; no-op-literal grep | `test_js_behavior.py`, `test_wiki_marks_browser.py` |
| 3 | `resman_skills.summary()` cases; routes with `?provider=` | `test_plugin_info.py`, `test_routes.py::test_skills_routes_*` |
| 4 | folder/registry consistency for the new skill | everything |
| 5 | remoteAgent contract with dynamic ops | `test_remote_agent.py` |

Manual verification after each phase: restart the unit
(`systemctl --user restart resman`), open the Tasks and Skills tabs, run one
`wiki-lint` on the test vault, and check `journalctl --user -u resman` for
warnings.

## Out of scope

- Editing skills from the browser. Skills are code; they change through git.
- Per-skill UI pages. A skill's output is wiki pages; if the Wiki tab cannot
  render something a skill needs, that is a Wiki tab feature.
- Installing `skills/` as a plugin. It is loaded from the repo (D2).
- Changing the claude-obsidian bootstrap or the new-vault wizard.
