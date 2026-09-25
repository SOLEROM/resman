"""skills/ — resman's own Claude Code plugin folder (docs/design/17-skills.md).

Phase 0 of docs/custom-skills-plan.md: the folder exists, is a well-formed
plugin, and every skill in it (none yet) follows the authoring contract in
skills/README.md. The app does not load the folder until phase 1; these
tests keep it valid in the meantime and grow with resman_skills.py later.
"""
import json
import re
from pathlib import Path

import pytest
import yaml

from .test_no_host_paths import HOST_PATH

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "skills"
PLUGIN_NAME = "resman"
SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _manifest() -> dict:
    return json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: SKILL.md must start with YAML frontmatter"
    end = text.find("\n---", 4)
    assert end > 0, f"{path}: unterminated frontmatter"
    data = yaml.safe_load(text[4:end])
    assert isinstance(data, dict), f"{path}: frontmatter must be a mapping"
    return data


def _skill_dirs() -> list[Path]:
    root = PLUGIN / "skills"
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def test_manifest_names_the_resman_plugin():
    m = _manifest()
    assert m["name"] == PLUGIN_NAME          # invoked as /resman:<skill>
    assert SEMVER_RE.match(m["version"]), m["version"]
    assert m["description"].strip()


def test_contract_and_rules_are_present():
    assert (PLUGIN / "README.md").is_file()
    assert (PLUGIN / "CLAUDE.md").is_file()
    assert (PLUGIN / "skills").is_dir()


def test_every_skill_folder_is_a_well_formed_skill():
    for d in _skill_dirs():
        assert SKILL_NAME_RE.match(d.name), f"{d.name}: lowercase letters, digits, hyphens"
        skill_md = d / "SKILL.md"
        assert skill_md.is_file(), f"{d.name}: missing SKILL.md"
        fm = _frontmatter(skill_md)
        assert fm.get("name") == d.name, f"{d.name}: frontmatter name must equal the folder"
        desc = fm.get("description")
        assert isinstance(desc, str) and desc.strip(), f"{d.name}: description required"


def test_no_host_paths_in_skill_files():
    # test_no_host_paths.py scans tracked code suffixes; skills are .md, so
    # check them here with the same pattern.
    hits = []
    for p in sorted(PLUGIN.rglob("*.md")):
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if HOST_PATH.search(line):
                hits.append(f"{p.relative_to(REPO)}:{n}: {line.strip()[:100]}")
    assert not hits, "host paths in skills:\n" + "\n".join(hits)


# ----- modules/resman_skills.py: the provider, its flag and its settings -----

import textwrap


def _root(tmp_path, *, plugin=True) -> Path:
    root = tmp_path / "resman"
    if plugin:
        d = root / "skills" / ".claude-plugin"
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(json.dumps({"name": "resman", "version": "0.3.0"}))
    return root


def _skill(root: Path, name: str, settings: str | None = None, body: str = "") -> Path:
    from modules import resman_skills
    d = root / "skills" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: does {name}\n---\n{body}")
    if settings is not None:
        (d / resman_skills.SETTINGS_FILE).write_text(textwrap.dedent(settings))
    return d


SCHEMA = """
    - key: list_size
      type: int
      default: 15
      min: 3
      max: 50
      help: open values kept
    - key: focus
      type: text
      default: ""
      max_len: 40
    - key: exclude
      type: list
      default: []
      item_max_len: 10
      max_items: 3
    - key: strict
      type: bool
      default: false
    - key: mode
      type: enum
      default: fast
      choices: [fast, deep]
"""


def test_plugin_dir_flag_only_when_the_folder_is_present(tmp_path):
    from modules import resman_skills
    root = _root(tmp_path, plugin=False)
    assert resman_skills.plugin_dir_args(root) == []
    assert resman_skills.is_present(root) is False
    root = _root(tmp_path)
    assert resman_skills.plugin_dir_args(root) == ["--plugin-dir", str(root / "skills")]
    assert resman_skills.version(root) == "0.3.0"


def test_the_real_folder_is_the_plugin(tmp_path):
    from modules import resman_skills
    assert resman_skills.is_present(REPO)
    assert resman_skills.plugin_dir(REPO) == PLUGIN
    assert resman_skills.version(REPO) == _manifest()["version"]


def test_skill_prompt_shape():
    from modules import resman_skills
    assert resman_skills.skill_prompt("deep-list") == "/resman:deep-list"
    assert resman_skills.skill_prompt("deep-list", "a=1 b=2") == "/resman:deep-list a=1 b=2"
    assert resman_skills.skill_prompt("deep-list", "  ") == "/resman:deep-list"


def test_list_skills_is_folders_with_a_skill_md(tmp_path):
    from modules import resman_skills
    root = _root(tmp_path)
    _skill(root, "beta"); _skill(root, "alpha")
    (root / "skills" / "skills" / "notaskill").mkdir()
    assert resman_skills.list_skills(root) == ["alpha", "beta"]
    assert resman_skills.list_skills(_root(tmp_path / "x", plugin=False)) == []


def test_settings_schema_loads_and_has_defaults(tmp_path):
    from modules import resman_skills
    root = _root(tmp_path)
    _skill(root, "demo", SCHEMA)
    schema = resman_skills.load_settings_schema(root, "demo")
    assert [s.key for s in schema] == ["list_size", "focus", "exclude", "strict", "mode"]
    assert resman_skills.defaults(schema) == {
        "list_size": 15, "focus": "", "exclude": [], "strict": False, "mode": "fast"}
    assert resman_skills.load_settings_schema(root, "nope") == ()   # no skill, no file
    _skill(root, "plain")
    assert resman_skills.load_settings_schema(root, "plain") == ()  # skill without settings


@pytest.mark.parametrize("bad", [
    "- key: x\n  type: rocket\n  default: 1\n",           # unknown type
    "- type: int\n  default: 1\n",                          # missing key
    "- key: x\n  type: int\n  default: 99\n  min: 0\n  max: 5\n",  # default out of range
    "- key: x\n  type: enum\n  default: a\n  choices: [b]\n",     # default not a choice
    "- key: x\n  type: int\n  default: 1\n- key: x\n  type: int\n  default: 2\n",  # duplicate
    "- key: Bad Key\n  type: int\n  default: 1\n",         # key not an identifier
    "key: x\n",                                             # not a list
])
def test_malformed_schema_is_an_error(tmp_path, bad):
    from modules import resman_skills
    root = _root(tmp_path)
    _skill(root, "demo", bad)
    with pytest.raises(resman_skills.SettingsError):
        resman_skills.load_settings_schema(root, "demo")


@pytest.mark.parametrize("values,key", [
    ({"list_size": 500}, "list_size"),
    ({"list_size": "15"}, "list_size"),
    ({"list_size": True}, "list_size"),
    ({"focus": "x" * 41}, "focus"),
    ({"focus": "é"}, "focus"),
    ({"exclude": "a"}, "exclude"),
    ({"exclude": ["a", 2]}, "exclude"),
    ({"exclude": ["a", "b", "c", "d"]}, "exclude"),
    ({"exclude": ["x" * 11]}, "exclude"),
    ({"strict": "yes"}, "strict"),
    ({"mode": "slow"}, "mode"),
    ({"unknown": 1}, "unknown"),
])
def test_validate_settings_rejects_naming_the_key(tmp_path, values, key):
    from modules import resman_skills
    root = _root(tmp_path)
    _skill(root, "demo", SCHEMA)
    schema = resman_skills.load_settings_schema(root, "demo")
    with pytest.raises(resman_skills.SettingsError, match=key):
        resman_skills.validate_settings(schema, values)


def test_validate_settings_accepts_a_partial_mapping(tmp_path):
    from modules import resman_skills
    root = _root(tmp_path)
    _skill(root, "demo", SCHEMA)
    schema = resman_skills.load_settings_schema(root, "demo")
    assert resman_skills.validate_settings(schema, {"list_size": 3, "exclude": ["a"]}) == {
        "list_size": 3, "exclude": ["a"]}
    assert resman_skills.validate_settings(schema, {}) == {}
    with pytest.raises(resman_skills.SettingsError):
        resman_skills.validate_settings(schema, ["not", "a", "mapping"])


def test_effective_settings_precedence_defaults_yaml_overrides(tmp_path):
    from modules import resman_skills
    root = _root(tmp_path)
    _skill(root, "demo", SCHEMA)
    schema = resman_skills.load_settings_schema(root, "demo")
    eff = resman_skills.effective_settings(schema, {"list_size": 20, "focus": "yaml"},
                                           {"focus": "task", "ignored": 1})
    assert eff == {"list_size": 20, "focus": "task", "exclude": [], "strict": False, "mode": "fast"}
    assert resman_skills.effective_settings(schema, None) == resman_skills.defaults(schema)


def test_render_args_is_deterministic_and_quotes_text(tmp_path):
    from modules import resman_skills
    root = _root(tmp_path)
    _skill(root, "demo", SCHEMA)
    schema = resman_skills.load_settings_schema(root, "demo")
    args = resman_skills.render_args(schema, {
        "list_size": 7, "focus": 'say "hi"\nnow', "exclude": ["a b", "c"],
        "strict": True, "mode": "deep"})
    assert args == 'list_size=7 focus="say hi now" exclude="a b|c" strict=true mode=deep'
    assert resman_skills.render_args((), {}) == ""


def test_args_for_merges_yaml_and_overrides_then_renders(tmp_path):
    from modules import resman_skills
    root = _root(tmp_path)
    _skill(root, "demo", SCHEMA)
    out = resman_skills.args_for(root, "demo", {"list_size": 20}, {"focus": "edge"})
    assert out == 'list_size=20 focus="edge" exclude="" strict=false mode=fast'
    assert resman_skills.args_for(root, "plain-missing", None, None) == ""


def test_validate_skills_section_checks_every_known_skill(tmp_path):
    from modules import resman_skills
    root = _root(tmp_path)
    _skill(root, "demo", SCHEMA)
    ok = resman_skills.validate_skills_section(root, {"demo": {"list_size": 5}, "gone": {"x": 1}})
    assert ok == ["gone"]                      # unknown skills are reported, not refused
    with pytest.raises(resman_skills.SettingsError, match="demo.list_size"):
        resman_skills.validate_skills_section(root, {"demo": {"list_size": 500}})
    with pytest.raises(resman_skills.SettingsError):
        resman_skills.validate_skills_section(root, {"demo": "not a mapping"})
    assert resman_skills.validate_skills_section(root, None) == []


# ----- summary(): the Skills tab's view of the provider -----

def test_summary_without_the_folder_is_one_warning(tmp_path):
    from modules import resman_skills
    s = resman_skills.summary(_root(tmp_path, plugin=False), {"echo": "rs-echo task"})
    assert s["plugin"]["installed"] is False and s["skills"] == []
    assert s["uses"] == [{"name": "echo", "where": "rs-echo task", "provided": False, "kind": None}]
    assert len(s["warnings"]) == 1 and "skills" in s["warnings"][0]


def test_summary_describes_skills_uses_and_settings(tmp_path):
    from modules import resman_skills
    root = _root(tmp_path)
    _skill(root, "demo", SCHEMA)
    _skill(root, "plain")
    (root / "skills" / "README.md").write_text("# guide\n")
    s = resman_skills.summary(root, {"demo": "rs-demo task"}, ["demo"])
    assert (s["plugin"]["installed"], s["plugin"]["version"], s["plugin"]["located_by"]) == (True, "0.3.0", "repo")
    by = {k["name"]: k for k in s["skills"]}
    assert by["demo"]["used"] and by["demo"]["has_settings"] and by["demo"]["invoke"] == "/resman:demo"
    assert not by["plain"]["used"] and not by["plain"]["has_settings"]
    assert s["uses"] == [{"name": "demo", "where": "rs-demo task", "provided": True, "kind": "skill"}]
    assert s["docs"] == ["README.md"] and s["warnings"] == []


def test_summary_warnings(tmp_path):
    from modules import resman_skills
    root = _root(tmp_path)
    d = _skill(root, "demo", "- key: x\n  type: rocket\n  default: 1\n")   # bad schema
    (d / "SKILL.md").write_text("---\nname: other\ndescription: d\n---\n")    # name mismatch
    s = resman_skills.summary(root, {"ghost": "rs-ghost task"}, ["gone"])
    text = "\n".join(s["warnings"])
    assert len(s["warnings"]) == 4
    assert "name: 'other'" in text and "settings.yaml is unusable" in text
    assert "/resman:ghost" in text and "'gone'" in text


# ----- the shipped deep-list skill -----

def test_deep_list_skill_folder_matches_its_settings_and_the_spec():
    from modules import resman_skills
    schema = resman_skills.load_settings_schema(REPO, "deep-list")
    keys = [s.key for s in schema]
    assert keys == ["list_size", "candidates_per_run", "min_score", "w_importance", "w_gap",
                    "w_leverage", "w_urgency", "filled_min_pages", "filled_min_words", "focus", "exclude"]
    skill_md = (PLUGIN / "skills" / "deep-list" / "SKILL.md").read_text()
    for key in keys:                       # the Parameters table names every setting
        assert f"`{key}`" in skill_md, key
    for s in schema:                       # with the same default
        shown = '""' if s.default == "" else ("" if s.default == [] else str(s.default))
        if shown:
            assert f"| `{s.key}` | {shown} |" in skill_md, s.key
    assert "wiki/meta/deep-list.md" in skill_md and "/claude-obsidian:autoresearch" in skill_md
    assert "deep-list.json" not in skill_md         # no sidecar (decision 6)
    # the page table shows one score column, no per-axis scores (decision 8)
    assert "| # | done | value | score | since | why | related | research with |" in skill_md
    assert "| imp |" not in skill_md and "| urg |" not in skill_md
    assert resman_skills.list_skills(REPO) == ["deep-list", "grilling"]


def test_effective_settings_ignore_empty_overrides(tmp_path):
    from modules import resman_skills
    root = _root(tmp_path)
    _skill(root, "demo", SCHEMA)
    schema = resman_skills.load_settings_schema(root, "demo")
    eff = resman_skills.effective_settings(schema, {"focus": "yaml"}, {"focus": "", "list_size": None})
    assert eff["focus"] == "yaml" and eff["list_size"] == 15


# ----- the shipped grilling skill: a helper, never an operation -----

def test_grilling_is_a_helper_skill_without_an_operation():
    """grilling is called by other resman skills (or by the operator in an
    interactive session); it writes no pages and has no registry entry, so it
    sits under *Not wired yet* on purpose."""
    from modules import operations, resman_skills
    skill_md = PLUGIN / "skills" / "grilling" / "SKILL.md"
    assert skill_md.is_file()
    fm = _frontmatter(skill_md)
    assert fm["name"] == "grilling"
    assert "Triggers on:" in fm["description"]
    assert resman_skills.load_settings_schema(REPO, "grilling") == ()      # no knobs
    assert not [op.key for op in operations.REGISTRY.values() if op.skill == "grilling"]
    text = skill_md.read_text(encoding="utf-8")
    assert "one at a time" in text and "recommended answer" in text
