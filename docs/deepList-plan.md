# deepList — the first resman skill

> **STATUS (2026-09-24): IMPLEMENTED, not yet run on a real vault.** All seven
> decisions at the bottom were taken on 2026-09-24 and the skill is built:
> `skills/skills/deep-list/SKILL.md` + `settings.yaml`, operation
> `rs-deep-list` in `modules/operations.py`, the Settings card in the Skills
> tab. The skill is **triggered only from the Tasks view** (and
> `schedule.yaml`); it writes one markdown page and no sidecar. Its first real
> run (on `testVault`, from the Tasks tab) spends Claude usage and is the
> operator's call; a second run should then show a value retired or re-ranked.

## What it does, in one paragraph

Run on a vault's wiki, `deepList` reads what the vault is for and what it
already knows, and writes **one ranked page**, `wiki/meta/deep-list.md`: the
research values (topics, questions, entities) that matter enough to the
vault's objective to deserve their own deep-research run, most valuable first.
On every re-run it re-reads its previous list, **drops the values the wiki has
since filled**, adds new candidates, **re-scores everything that is left** and
writes the page again, so the list is always current. Each open value carries
the exact `/claude-obsidian:autoresearch …` line that would fill it.

## Names

| thing | value | why |
|---|---|---|
| label in the UI | **deepList** | the name you gave it |
| skill folder / skill name | `skills/skills/deep-list/` → `/resman:deep-list` | Claude Code skills and the folder test use lowercase-hyphen names; camelCase is kept for the label only |
| operation key | `rs-deep-list` | provider `resman`, `rs-` prefix (17-skills.md) |
| output page | `wiki/meta/deep-list.md` | `meta/` is where the plugin keeps dashboards and lint reports; one page, overwritten |
| settings in yaml | `skills.deep-list.*` in `resman.yaml` (or `~/.resman.yaml` when it exists) | "stored with our app yaml"; the per-user file wins and is the write target, as for every other setting |

## Inputs the skill reads (reading order)

1. `wiki/hot.md`, then `wiki/index.md`, then `wiki/overview.md`: the plugin's
   own reading order.
2. The vault's objective: `CLAUDE.md` in the vault root (`Purpose:` / `Mode:`
   lines the plugin's bootstrap writes), `wiki/hint.json` (`summary`, `tags`)
   and the `focus` setting (below). The skill writes the objective it derived
   into the page; if the operator edits that section and adds
   `objective_pinned: true` to the frontmatter, later runs keep it verbatim.
3. Coverage signals: every family `_index.md` (`entities/`, `concepts/`,
   `trends/`, `signals/`, `domains/`, `sources/`, whatever the vault has),
   the newest `wiki/meta/lint-report-*.md` (its *missing pages* and *orphans*
   lists), the top of `wiki/log.md` (what changed since the last run).
4. The previous `wiki/meta/deep-list.md`, if any.

If the vault has no `wiki/`, the skill stops with a clear message and writes
nothing. If it cannot find any objective statement, it says so and uses the
`focus` setting alone; with neither, it writes nothing and tells the operator
to set `focus` in Skills → deepList.

## The algorithm

1. **Objective.** Two or three sentences synthesised from the inputs plus
   `focus`. Written to the page's *Objective* section unless pinned.
2. **Load the previous list.** Parse the *Open values* table (value, score,
   first_seen, seen_count), the *Filled* and *Dismissed* sections.
3. **Retire what is filled.** For each open value, search `wiki/**/*.md`
   (title, aliases, tags, headings) for pages about it. It is **filled** when
   at least `filled_min_pages` pages of at least `filled_min_words` body words
   exist that were created or updated after the value's `first_seen`, or when
   the operator ticked its row (`- [x]`) or wrote `done` in its status column.
   Filled values move to *Filled* with the date and links to the pages that
   filled them. Values the operator marked `dismissed`, and anything in the
   `exclude` setting, move to *Dismissed* and are never proposed again.
4. **Generate candidates**, at most `candidates_per_run`, from: concepts or
   entities mentioned in two or more pages that have no page of their own;
   pages thinner than `filled_min_words`; open questions in `overview.md`,
   `questions/` or a `question`-type page; trends and signals with no concept
   coverage; lint's missing pages; and a decomposition of the objective into
   what a domain expert would need the vault to know.
5. **Score** every open value and candidate 0–100 on four axes, weighted by
   the settings: **importance** to the objective, **gap** (how thin the
   current coverage is), **leverage** (how many pages mention or would link
   to it; what it unblocks), **urgency** (recency of signals, time
   sensitivity). Drop below `min_score`, keep the top `list_size`. Existing
   values keep `first_seen`, get `seen_count + 1` and a trend mark
   (↑ ↓ →) against their previous score.
6. **Write `wiki/meta/deep-list.md`** in full (frontmatter: `type: meta`,
   `title`, `created` kept from the first run, `updated`, `tags:
   [deep-list, meta]`, `run_count`, `settings` snapshot). Sections, in order:
   *Objective*; *Open values*, an eight-column table — rank, the operator's
   `done` box, value (a `[[wikilink]]` when a page exists, else the page name
   it would get), the combined score with its trend mark (`87 ↑`), `since`
   (first-seen date `×` runs seen), one-line *why*, related pages, and the
   suggested `/claude-obsidian:autoresearch <topic>` line; *Filled* (newest
   first, last 50); *Dismissed*; *Run log* (last 10 runs: date, open / filled
   / new counts). The four axis scores are working values and are **not**
   written to the page (decision 8).
7. **Bookkeeping.** Prepend one line to `wiki/log.md`
   (`deep-list: N open, M filled, K new`), add the page to `wiki/index.md`
   under meta on the first run, print a one-line summary so the task log and
   the Tasks card show the counts.

Re-runnable (one page, overwritten), writes only under `wiki/`, frontmatter on
the page, never touches `.raw/`, `.obsidian/` or `_resman/`: the golden rule
holds.

## Settings: editable in the Skills tab, stored in the app yaml

### The schema lives with the skill

`skills/skills/deep-list/settings.yaml` declares the knobs. The app
reads it to build the form, validate saves and render the invocation; adding a
knob later is a change to the skill folder, not to Python.

```yaml
# deepList settings — shown as a form on the skill's page in the Skills tab,
# stored under skills.deep-list in resman.yaml (or ~/.resman.yaml).
- key: list_size
  type: int
  default: 15
  min: 3
  max: 50
  help: open values kept after ranking
- key: candidates_per_run
  type: int
  default: 30
  min: 5
  max: 100
  help: new candidates considered per run before pruning
- key: min_score
  type: int
  default: 40
  min: 0
  max: 100
  help: values scoring below this are dropped
- key: w_importance
  type: int
  default: 40
  min: 0
  max: 100
  help: weight of "importance to the objective"
- key: w_gap
  type: int
  default: 30
  min: 0
  max: 100
  help: weight of "how thin the coverage is"
- key: w_leverage
  type: int
  default: 20
  min: 0
  max: 100
  help: weight of "how much it unblocks / how often it is mentioned"
- key: w_urgency
  type: int
  default: 10
  min: 0
  max: 100
  help: weight of "time sensitivity"
- key: filled_min_pages
  type: int
  default: 2
  min: 1
  max: 10
  help: pages needed before a value counts as filled
- key: filled_min_words
  type: int
  default: 300
  min: 50
  max: 5000
  help: body words a page needs to count toward "filled"
- key: focus
  type: text
  default: ""
  max_len: 200
  help: extra objective hint, appended to what the vault says about itself
- key: exclude
  type: list
  default: []
  item_max_len: 80
  max_items: 50
  help: values never to propose
```

Types allowed in a `settings.yaml`: `int` (min/max), `text` (max_len,
printable ASCII), `bool`, `enum` (choices), `list` of text. The weights are
four plain ints and the skill normalises them; no sum rule to trip on.

### Where the values are stored

```yaml
# resman.yaml (or ~/.resman.yaml, which wins and is the write target)
skills:
  deep-list:
    list_size: 15
    min_score: 40
    focus: "edge inference on constrained hardware"
    exclude: [crypto payroll]
```

Only keys that differ from the defaults need to be present; missing keys take
the schema default. `validate_resman_yaml` validates the whole `skills:`
section against every skill's schema, so a hand-edited yaml with a bad value
is rejected with a message naming the key. A key for a skill that no longer
exists is a Skills-tab warning, not an error.

### How the values reach the skill

The registry entry renders them as `key=value` tokens after the slash command,
all of them, so a run is reproducible from its task log:

```
/resman:deep-list list_size=15 candidates_per_run=30 min_score=40 w_importance=40 w_gap=30 w_leverage=20 w_urgency=10 filled_min_pages=2 filled_min_words=300 focus="edge inference on constrained hardware" exclude="crypto payroll"
```

Precedence: schema defaults ← `skills.deep-list` in the yaml ← per-task
overrides. The task form offers one optional per-task field, `focus`, so a
single run can be aimed without changing the saved setting. `SKILL.md`
documents the same tokens and defaults in a *Parameters* table; a test checks
the table names every key in `settings.yaml`.

### The form in the Skills tab

On the `resman skills → deep-list` page, under the rendered `SKILL.md`, a
**Settings** card: one field per schema entry (number inputs with min/max,
text with maxlength, a textarea one-item-per-line for lists), the help text
under each, **Save** and **Reset to defaults**, and a line saying which file
it writes (`resman_display_path`, the same value the Config tab shows).
Saving goes through `ConfigManager.save_resman_data`, the structured save the
Config tab's Form mode already uses; like that form, it drops YAML comments in
the file and says so.

API:

| method | path | body / result |
|---|---|---|
| GET | `/api/skills/settings?skill=deep-list` | `{skill, schema, defaults, values, file, display_path, warnings}` |
| POST | `/api/skills/settings` | `{skill, values}` → validated against the schema → merged into `skills.<skill>` → saved → `{ok, values}`; 400 names the bad key; CSRF header required |

## Wiring (the registry entry)

```python
Operation(
    key="rs-deep-list", label="deepList: research values", group="Research",
    provider="resman", kind="prompt", skill="deep-list",
    params=(Param("focus", "text", "Focus for this run (optional)", max_len=200),),
    desc="Rank the research values worth a deep-research run; refresh the list.",
    note="Writes wiki/meta/deep-list.md. Settings: Skills → resman skills → deep-list.",
    icon="codicon-list-ordered", remote=True,
    build_prompt=lambda p, ctx: resman_skills.skill_prompt(
        "deep-list", resman_skills.render_args("deep-list", ctx.settings, p)),
)
```

`kind="prompt"` makes it attendable (run it in a REPL and watch), schedulable
from `schedule.yaml` (`operation: rs-deep-list`, e.g. weekly), and runnable on
`ALL` vaults (one child per vault, each with its own page).

## Files, per plan phase

| phase | file | change |
|---|---|---|
| 1 ✓ | `modules/resman_skills.py` | done 2026-09-24: `load_settings_schema(root, skill)`, `defaults`, `validate_settings`, `effective_settings(schema, stored, overrides)`, `render_args`, `args_for(root, skill, stored, overrides)`, `validate_skills_section`; `RunContext.settings` |
| 1 ✓ | `modules/config_manager.py` | done 2026-09-24: `validate_resman_yaml(data, resman_root)` checks `skills:`; `ConfigManager.skills`, `.skill_settings(skill)`, `.save_skill_settings(skill, values)` |
| 3 | `modules/routes.py` | `GET/POST /api/skills/settings`; provider summary marks skills with `has_settings` |
| 3 | `static/js/skills.js`, `style.css` | the Settings card on a skill page |
| 4 | `skills/skills/deep-list/SKILL.md` | the procedure above as numbered steps, the *Parameters* table, the page template |
| 4 | `skills/skills/deep-list/settings.yaml` | the schema above |
| 4 | `modules/operations.py` | the entry above |
| 4 | `man/tasks.md`, `man/skills.md`, `docs/design/00-README.md` | the row, the page, the additions table |
| 5 | `skills/evals/deep-list/` | optional `claude plugin eval` case against `testVault` (spends usage; opt-in) |

## Tests

- `tests/test_resman_skills.py`: `settings.yaml` parses; every key has a
  known type and a default that validates; `SKILL.md`'s Parameters table
  names every key; the skill folder is well-formed (already there).
- `tests/test_config_manager.py`: `skills.deep-list.list_size: 500` is
  rejected naming the key; unknown skill key is accepted with a warning;
  missing keys resolve to defaults.
- `tests/test_operations.py`: `rs-deep-list` builds
  `[claude, -p, "/resman:deep-list list_size=15 …", --dangerously-skip-permissions, --plugin-dir, <root>/skills]`;
  a yaml value and a per-task `focus` override both show in the prompt, in
  that precedence; `focus` longer than 200 chars is rejected.
- `tests/test_routes.py`: GET returns schema + effective values; POST with a
  bad value is 400 and writes nothing; POST with a good value rewrites the
  active yaml (temp dir) and the next GET reflects it; CSRF enforced.
- Browser test: the Settings card renders one control per key, Save enables
  on change, Reset restores defaults.
- Free check: `claude --plugin-dir skills plugin details resman` lists
  `deep-list`. Paid check, once, with your go-ahead: `rs-deep-list` on
  `testVault` from the Tasks tab, then `wiki/meta/deep-list.md` in the Wiki
  tab; then a second run to see one value retired or re-ranked.

## How the list gets acted on

The skill is triggered from the Tasks view (or a `schedule.yaml` cron entry)
and nothing else. Acting on a value is the operator's move: open
`wiki/meta/deep-list.md` in the Wiki tab, copy the suggested
`/claude-obsidian:autoresearch …` line of the value into a **Run a Claude
prompt** task, or type the topic into **Autoresearch a topic**. No sidecar, no
button on the page, no reading of the list by resman (decision 6).

## Decisions (taken 2026-09-24)

1. **Machine name `deep-list`**, label "deepList". Approved.
2. **Output at `wiki/meta/deep-list.md`**, one page overwritten each run, with
   *Filled* and *Dismissed* history kept on the same page. Approved.
3. **Settings schema in the skill folder** (`settings.yaml`). Approved.
4. **All eleven settings** above, with those defaults, in the first version.
   Approved.
5. **Operator control through the page** (tick a row, or `done` / `dismissed`
   in the status column, honoured on the next run) is in the first version.
   Approved.
6. **No sidecar and no stage 2.** The skill is triggered only from the Tasks
   view; resman never reads the list. Decided.
7. **Order of work** as in the main plan: phase 1 registry → phase 2 Tasks
   reads the registry → phase 3 Skills providers + the settings form →
   phase 4 this skill. Approved.
8. **Only the combined score is shown** (2026-09-24, after review of the
   table): the per-axis scores were noise on the page and took the table's
   width. The score cell carries the trend mark; a `since` cell carries the
   first-seen date and the runs-seen count the retire rule needs. Decided.
