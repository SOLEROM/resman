# resman — rules for working in this repo

resman is the control plane for a set of Obsidian research vaults: a Flask +
Socket.IO server in `control-plane/` with a vanilla-JS SPA, a window-gated task
queue on an append-only JSONL log, browser terminals through the shared
`webterm` library, and a per-vault Wiki reader. Port `6001` (`.port`), unit
`resman`, venv `.venv-ubuntu24` on this laptop. The bench-wide rules in
`../CLAUDE.md` apply first (never commit or push; `/proj/...` paths only; no
host path literals; solBench is the sibling `../solBench`).

## The philosophy: skills write the vault, resman shows it

- **resman never edits vault files.** Every operation is a Claude Code skill run
  inside one vault with `claude -p`; the skill leaves markdown pages under
  `<vault>/wiki/` in the vault's conventions; resman queues the run, renders
  the pages (Wiki tab), and tracks read/unread, favorites and highlights.
- **Two skill providers, one contract** (`docs/design/17-skills.md`):
  - `obsidian` — the **claude-obsidian** plugin, installed per user, found by
    `modules/plugin_info.py`, command strings only in `modules/plugin_commands.py`.
    Operation keys `wiki-*`.
  - `resman` — **our own skills** in `skills/` (a Claude Code plugin
    named `resman`, invoked `/resman:<skill>`), loaded into every Claude run
    resman spawns with `--plugin-dir <repo>/skills`, never installed.
    Operation keys `rs-*`. Authoring contract: `skills/README.md`.
  - `adhoc` — `run-prompt`, `run-shell`. Never how a resman skill runs.
- **A new capability is one skill folder plus one registry entry**, never a
  new tab. If the Wiki tab cannot render what a skill writes, that is a Wiki
  tab feature.
- **The provider is a registry attribute, not a name prefix.** Views filter on
  it; the `wiki-` / `rs-` / `run-` prefixes exist so `tasks.jsonl`,
  `schedule.yaml` and logs stay readable. Never filter by `startsWith` in JS.
- **Rollout state:** `docs/custom-skills-plan.md`. Phases 0–4 are done: the
  registry, the Tasks view on the registry (source switch, pill, filter), the
  per-provider Skills tab with the **Settings** card, and the first skill
  **deepList** (`skills/skills/deep-list/`, operation `rs-deep-list`,
  spec `docs/deepList-plan.md`). Open: phase 5 (remoteAgent reads the
  registry, Config-tab cron select grouped by provider, optional evals) and
  deepList's first real run on a vault (spends usage). A skill folder without
  a registry entry is allowed but inert (listed under *Not wired yet*);
  **grilling** (`skills/skills/grilling/`) is one on purpose: a helper the
  other skills call before they act, never an operation.

## Where things are

| what | where |
|---|---|
| server entry / composition root | `control-plane/server.py` (`RESMAN_ROOT`, `context` dict passed to routes) |
| the operation registry | `modules/operations.py` (`REGISTRY`, `Operation`, `Param`, `RunContext`, `validate`, `public`); `modules/resman_skills.py` (the `skills/` provider, settings schemas) |
| task queue | `modules/task_manager.py` (`_build_command`, `build_attend_prompt`, `set_skill_settings` all go through the registry) |
| plugin command strings, new-vault prompt | `modules/plugin_commands.py` (`PLUGIN_USES` is pinned by `tests/test_plugin_info.py`) |
| installed-plugin facts (Skills tab) | `modules/plugin_info.py`; routes `GET /api/skills/*` |
| REST | `modules/routes.py`; Socket.IO `modules/websocket_handlers.py` |
| SPA | `static/js/app.js` (Tasks: `loadOperations`, `opMeta`, `renderOpCards`, `renderTasks`), `static/js/tasks-core.js` (pure helpers, node-tested), `static/js/skills.js` (both providers, Settings card), `templates/index.html`, `static/css/style.css` |
| skills of our own | `skills/` (`README.md` contract, `CLAUDE.md` rules, `skills/<name>/SKILL.md`) |
| prompts and helpers | `prompts/urlInjestPrefix.md`, `tools/ingest.sh`, `tools/newVal{Prefix,Suffix}.md`, `tools/remoteAgent.sh` |
| design docs | `docs/design/00-README.md` index; `06` tasks, `09` API, `10` frontend, `11` security, `17` skills |
| operator manual (Help tab) | `man/` — `tasks.md`, `skills.md`, `new-vault.md`, `reference/api.md` |
| plans | `docs/custom-skills-plan.md` (current), `docs/reimplementation-plan.md` (done) |

## How to work here

- **Tests first, then code:** `.venv-ubuntu24/bin/python -m pytest -q -rs`
  (whole suite; JS suites under `tests/js/*.test.mjs` run through
  `test_js_behavior.py` when `node` is present). Kit tests
  (`test_cldbar_embed.py`, `test_bench_signal.py`) must not skip on this host.
- **Adding an operation:** one `Operation` in `modules/operations.py` with
  `provider`, `kind`, `Param` specs and a builder; for `provider="resman"`
  also `skills/skills/<skill>/SKILL.md` (+ `settings.yaml` for knobs,
  rendered by the Skills tab and stored under `skills.<skill>` in resman.yaml).
  `tests/test_operations.py` checks the pair. The SPA reads
  `GET /api/operations`; never spell an operation key in JS (a grep test
  fails), the sidebar ↘ ingest button being the one exception.
- **Changing what a plugin command does:** edit `plugin_commands.py` (obsidian)
  or the skill's `SKILL.md` (resman). Never inline a `/claude-obsidian:…` or
  `/resman:…` string elsewhere; the grep test in `test_plugin_info.py` catches
  the former.
- **Subprocesses:** argument lists only, never `shell=True`; free-text params
  stay ≤ 200 printable ASCII; `--plugin-dir` is always `RESMAN_ROOT /
  "skills"`, never from a request.
- **Docs travel with code:** a behaviour change touches the design doc in
  `docs/design/`, the `00-README.md` additions table, and the `man/` page the
  Help tab renders. Plans get a STATUS line at the top when they finish.
- **Run it:** `./run.sh --vname .venv-ubuntu24` (dev) or
  `systemctl --user restart resman`; logs `journalctl --user -u resman`.
  Check a skill folder for free with
  `claude --plugin-dir skills plugin details resman`; a real skill run
  spends the operator's Claude usage — ask before spending it.
- **Never commit.** Report `git status --short` and `git diff --stat`.
