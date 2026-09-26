# skills — rules for editing this folder

This folder is the `resman` Claude Code plugin: resman's own vault skills. It
is loaded per run with `--plugin-dir`, never installed. Read `README.md` here
for the authoring contract; this file is the short version Claude follows when
it writes or changes a skill.

- **Golden rule.** A skill writes markdown pages under the vault's `wiki/`
  only, with the vault's frontmatter (`type`, `status`, `created`, `updated`,
  `tags`), appends at the top of `wiki/log.md`, updates `wiki/index.md`, and
  is safe to run twice. Nothing outside `wiki/`; never `.raw/`, `.obsidian/`,
  `_resman/`.
- **One folder per skill:** `skills/<name>/SKILL.md`, frontmatter `name`
  equal to the folder name, a one-paragraph `description` ending in trigger
  phrases. Supporting files go in `skills/<name>/references/`.
- **A skill is not live until it has a registry entry** in
  `control-plane/modules/operations.py` (`key="rs-<name>"`,
  `provider="resman"`, `skill="<name>"`). Adding the folder alone changes
  nothing in the app except a Skills-tab listing under "Not wired yet".
- **Helper skills** (`grilling`) are called by other skills and write nothing;
  they never get a registry entry. `metadata: {role: helper}` in the
  frontmatter lists them under *Helpers* in the Skills tab (without it a
  folder shows under *Not wired yet*). The description says it is a helper;
  no `settings.yaml`.
- **Keep descriptions short.** Every skill's description is always-on context
  in every resman session (about 50 tokens each).
- **Never write a host path** into any file here; `tests/test_no_host_paths.py`
  and `tests/test_resman_skills.py` fail on it.
- **Check for free before spending usage:**
  `claude --plugin-dir skills plugin details resman`, then
  `pytest -q tests/test_resman_skills.py`. A real run is a Tasks-tab task on
  the test vault.
- **Do not add a slash command** (`commands/<name>.md`) unless the skill needs
  arguments the SKILL.md description cannot express; tasks invoke skills
  directly as `/resman:<name> <args>`.
- **Do not commit.** Leave changes in the working tree; the user commits.
