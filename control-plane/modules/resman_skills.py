"""resman's own skill provider: the repo's ``skills/`` plugin folder.

The folder is a Claude Code plugin named ``resman`` (``.claude-plugin/
plugin.json`` + ``skills/<name>/SKILL.md``). It is never installed: every
``claude`` process resman spawns gets ``--plugin-dir <resman_root>/skills``
(:func:`plugin_dir_args`), so ``/resman:<skill>`` (:func:`skill_prompt`) works
in tasks, attend sessions and the Ops tab alike. A checkout without the folder
simply gets no flag. Design: docs/design/17-skills.md.

A skill may ship ``settings.yaml`` beside its ``SKILL.md``: the schema of the
knobs the operator edits in the Skills tab. The values live under
``skills.<skill>`` in resman.yaml (validated here on load and save through
config_manager) and reach the skill as ``key=value`` tokens after the slash
command (:func:`render_args`). Precedence: schema defaults ← yaml ← per-task
overrides (:func:`effective_settings`). Spec: docs/deepList-plan.md.
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

PLUGIN_NAME = "resman"
PLUGIN_FOLDER = "skills"
SETTINGS_FILE = "settings.yaml"
SETTING_TYPES = ("int", "text", "bool", "enum", "list")
SETTING_FIELDS = {"key", "type", "default", "help", "min", "max", "max_len",
                  "choices", "item_max_len", "max_items"}
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# Settings are single-line tokens on a command line: printable ASCII only.
PRINTABLE_RE = re.compile(r"^[\x20-\x7E]*$")
MAX_SCHEMA_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 64 * 1024


class SettingsError(ValueError):
    """A settings schema or a settings value that cannot be accepted.
    The message names the offending key (``<skill>.<key>`` when known)."""


# ----- the plugin folder -----

def plugin_dir(resman_root: Path | str) -> Path:
    return Path(resman_root) / PLUGIN_FOLDER


def manifest_path(resman_root: Path | str) -> Path:
    return plugin_dir(resman_root) / ".claude-plugin" / "plugin.json"


def is_present(resman_root: Path | str) -> bool:
    return manifest_path(resman_root).is_file()


def manifest(resman_root: Path | str) -> dict:
    """The plugin manifest, or ``{}`` when the folder is missing or unreadable."""
    path = manifest_path(resman_root)
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def version(resman_root: Path | str) -> str:
    return str(manifest(resman_root).get("version") or "")


def plugin_dir_args(resman_root: Path | str) -> list[str]:
    """The ``claude`` arguments that load the folder, or ``[]`` without it."""
    if not is_present(resman_root):
        return []
    return ["--plugin-dir", str(plugin_dir(resman_root))]


def plugin_dir_suffix(resman_root: Path | str) -> str:
    """The same flag as a shell-quoted string, for the configured ``claude_cmd``."""
    args = plugin_dir_args(resman_root)
    return shlex.join(args) if args else ""


def skill_prompt(name: str, args: str = "") -> str:
    """``/resman:<name> <args>`` — how a task invokes one of our skills."""
    args = (args or "").strip()
    return f"/{PLUGIN_NAME}:{name}" + (f" {args}" if args else "")


def skills_dir(resman_root: Path | str) -> Path:
    return plugin_dir(resman_root) / "skills"


def skill_dir(resman_root: Path | str, skill: str) -> Path:
    return skills_dir(resman_root) / skill


def list_skills(resman_root: Path | str) -> list[str]:
    """Skill folders (those with a SKILL.md), sorted."""
    root = skills_dir(resman_root)
    try:
        return sorted(p.name for p in root.iterdir()
                      if p.is_dir() and not p.is_symlink()
                      and SKILL_NAME_RE.match(p.name) and (p / "SKILL.md").is_file())
    except OSError:
        return []


# ----- settings schema -----

@dataclass(frozen=True)
class Setting:
    key: str
    type: str                  # int | text | bool | enum | list
    default: Any
    help: str = ""
    min: Optional[int] = None  # int
    max: Optional[int] = None  # int
    max_len: int = 200         # text
    choices: tuple = ()        # enum
    item_max_len: int = 80     # list
    max_items: int = 50        # list

    def public(self) -> dict:
        return {"key": self.key, "type": self.type, "default": self.default,
                "help": self.help, "min": self.min, "max": self.max,
                "max_len": self.max_len, "choices": list(self.choices),
                "item_max_len": self.item_max_len, "max_items": self.max_items}


def _int_field(raw: dict, name: str, where: str, default: Optional[int]) -> Optional[int]:
    value = raw.get(name, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"{where}: '{name}' must be an integer")
    return value


def _setting_from(raw: Any, where: str) -> Setting:
    if not isinstance(raw, dict):
        raise SettingsError(f"{where}: each setting must be a mapping")
    unknown = set(raw) - SETTING_FIELDS
    if unknown:
        raise SettingsError(f"{where}: unknown field(s) {sorted(unknown)}")
    key = raw.get("key")
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise SettingsError(f"{where}: 'key' must match [a-z][a-z0-9_]* (got {key!r})")
    where = f"{where}.{key}"
    kind = raw.get("type")
    if kind not in SETTING_TYPES:
        raise SettingsError(f"{where}: type must be one of {SETTING_TYPES} (got {kind!r})")
    if "default" not in raw:
        raise SettingsError(f"{where}: 'default' required")
    help_text = raw.get("help", "")
    if not isinstance(help_text, str):
        raise SettingsError(f"{where}: 'help' must be a string")
    choices = raw.get("choices", ())
    if kind == "enum":
        if not isinstance(choices, list) or not choices \
                or not all(isinstance(c, str) and c for c in choices):
            raise SettingsError(f"{where}: enum needs a non-empty 'choices' list of strings")
    setting = Setting(
        key=key, type=kind, default=raw["default"], help=" ".join(help_text.split()),
        min=_int_field(raw, "min", where, None), max=_int_field(raw, "max", where, None),
        max_len=_int_field(raw, "max_len", where, 200) or 200,
        choices=tuple(choices) if kind == "enum" else (),
        item_max_len=_int_field(raw, "item_max_len", where, 80) or 80,
        max_items=_int_field(raw, "max_items", where, 50) or 50,
    )
    _check_value(setting, setting.default, where_prefix="")  # the default must be valid
    return setting


def load_settings_schema(resman_root: Path | str, skill: str) -> tuple[Setting, ...]:
    """The skill's ``settings.yaml`` as Setting entries; ``()`` when the skill
    has none. Raises SettingsError on a malformed file."""
    path = skill_dir(resman_root, skill) / SETTINGS_FILE
    if not path.is_file():
        return ()
    try:
        if path.stat().st_size > MAX_SCHEMA_BYTES:
            raise SettingsError(f"{skill}: {SETTINGS_FILE} exceeds {MAX_SCHEMA_BYTES} bytes")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SettingsError(f"{skill}: {SETTINGS_FILE} is not valid YAML — {exc}") from exc
    except OSError as exc:
        raise SettingsError(f"{skill}: {SETTINGS_FILE} unreadable — {exc}") from exc
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise SettingsError(f"{skill}: {SETTINGS_FILE} must be a list of settings")
    out: list[Setting] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        setting = _setting_from(item, f"{skill}: setting #{i + 1}")
        if setting.key in seen:
            raise SettingsError(f"{skill}: duplicate setting key {setting.key!r}")
        seen.add(setting.key)
        out.append(setting)
    return tuple(out)


# ----- values -----

def _check_value(setting: Setting, value: Any, where_prefix: str = "") -> Any:
    where = f"{where_prefix}{setting.key}"
    kind = setting.type
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"{where}: must be an integer")
        if setting.min is not None and value < setting.min:
            raise SettingsError(f"{where}: must be ≥ {setting.min}")
        if setting.max is not None and value > setting.max:
            raise SettingsError(f"{where}: must be ≤ {setting.max}")
        return value
    if kind == "text":
        if not isinstance(value, str):
            raise SettingsError(f"{where}: must be a string")
        if len(value) > setting.max_len or not PRINTABLE_RE.match(value):
            raise SettingsError(f"{where}: must be ≤{setting.max_len} chars of printable ASCII")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise SettingsError(f"{where}: must be true or false")
        return value
    if kind == "enum":
        if value not in setting.choices:
            raise SettingsError(f"{where}: must be one of {list(setting.choices)}")
        return value
    if kind == "list":
        if not isinstance(value, list):
            raise SettingsError(f"{where}: must be a list")
        if len(value) > setting.max_items:
            raise SettingsError(f"{where}: at most {setting.max_items} items")
        for item in value:
            if not isinstance(item, str) or len(item) > setting.item_max_len \
                    or not PRINTABLE_RE.match(item):
                raise SettingsError(f"{where}: items must be strings of ≤{setting.item_max_len} "
                                    f"chars of printable ASCII")
        return list(value)
    raise SettingsError(f"{where}: unknown type {kind!r}")


def defaults(schema: tuple[Setting, ...]) -> dict:
    return {s.key: (list(s.default) if s.type == "list" else s.default) for s in schema}


def validate_settings(schema: tuple[Setting, ...], values: Any, where: str = "") -> dict:
    """``values`` (a partial mapping) checked against ``schema``; returns the
    normalized mapping. Raises SettingsError naming ``<where><key>``."""
    if not isinstance(values, dict):
        raise SettingsError(f"{where.rstrip('.') or 'settings'}: must be a mapping")
    by_key = {s.key: s for s in schema}
    out: dict = {}
    for key, value in values.items():
        setting = by_key.get(str(key))
        if setting is None:
            raise SettingsError(f"{where}{key}: unknown setting")
        out[setting.key] = _check_value(setting, value, where)
    return out


def effective_settings(schema: tuple[Setting, ...], stored: Optional[dict],
                       overrides: Optional[dict] = None) -> dict:
    """defaults ← stored (the yaml) ← overrides (a task's params); keys the
    schema does not know are ignored, and so is an empty override (``None`` or
    ``""``): a blank per-task field does not blank the stored value."""
    out = defaults(schema)
    for source in (stored or {}, overrides or {}):
        for key in out:
            if key in source and source[key] is not None and source[key] != "":
                out[key] = source[key]
    return out


def _token(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return '"' + "|".join(_clean(str(v)) for v in value) + '"'
    return '"' + _clean(str(value)) + '"'


def _clean(text: str) -> str:
    """One line, no double quotes or pipes, whitespace collapsed."""
    return " ".join(text.replace('"', "").replace("|", " ").split())


def render_args(schema: tuple[Setting, ...], values: dict) -> str:
    """``key=value`` tokens in schema order, every key present, so a task log
    shows exactly what the skill ran with. Enum values are bare words."""
    parts = []
    for s in schema:
        value = values.get(s.key, s.default)
        token = value if s.type == "enum" else _token(value)
        parts.append(f"{s.key}={token}")
    return " ".join(parts)


def args_for(resman_root: Path | str, skill: str, stored: Optional[dict],
             overrides: Optional[dict] = None) -> str:
    """The rendered settings for one skill run (``""`` for a skill without
    settings)."""
    schema = load_settings_schema(resman_root, skill)
    if not schema:
        return ""
    return render_args(schema, effective_settings(schema, stored, overrides))


def validate_skills_section(resman_root: Path | str, section: Any) -> list[str]:
    """The ``skills:`` mapping of resman.yaml checked against every skill's
    schema. Returns the names of skills the folder does not have (kept, so a
    removed skill does not break the config; the Skills tab reports them).
    Raises SettingsError for a bad value, naming ``<skill>.<key>``."""
    if section is None:
        return []
    if not isinstance(section, dict):
        raise SettingsError("skills: must be a mapping of skill name → settings")
    unknown: list[str] = []
    for name, values in section.items():
        name = str(name)
        if not isinstance(values, dict):
            raise SettingsError(f"skills.{name}: must be a mapping")
        if not skill_dir(resman_root, name).is_dir():
            unknown.append(name)
            continue
        validate_settings(load_settings_schema(resman_root, name), values, where=f"{name}.")
    return unknown


# ----- the Skills tab's view of this provider -----

DOC_NAMES = ("README.md", "CLAUDE.md")


HELPER_ROLE = "helper"


def is_helper(frontmatter: dict) -> bool:
    """A helper skill declares itself with ``metadata: {role: helper}`` in its
    SKILL.md frontmatter (the Agent Skills ``metadata`` map): other skills
    call it on their own plan, it writes nothing and never gets a registry
    entry. The Skills tab lists helpers under *Helpers*, not *Not wired yet*."""
    meta = frontmatter.get("metadata") if isinstance(frontmatter, dict) else None
    if not isinstance(meta, dict):
        return False
    role = meta.get("role")
    return isinstance(role, str) and role.strip().lower() == HELPER_ROLE


def summary(resman_root: Path | str, uses: Optional[dict] = None,
            stored_skills: Optional[list] = None) -> dict:
    """The resman provider in the shape of ``plugin_info.summary()``: the
    folder as ``plugin``, its ``skills`` (with ``used``, ``helper``,
    ``has_settings`` and the ``invoke`` line), ``commands``, ``docs``, the
    registry's ``uses`` of it, and ``warnings`` (the Skills badge). ``uses``
    is skill name → where resman runs it (from the registry's resman
    operations); ``stored_skills`` the names under ``skills:`` in resman.yaml,
    so a setting for a skill the folder lacks is reported."""
    from . import plugin_info  # plugin_info imports plugin_commands only

    uses = dict(uses or {})
    root = plugin_dir(resman_root)
    present = is_present(resman_root)
    plugin = {"id": PLUGIN_NAME, "name": PLUGIN_NAME, "installed": present,
              "located_by": "repo", "path": str(root), "version": version(resman_root),
              "description": "", "searched": [str(root)]}
    if not present:
        return {"plugin": plugin, "skills": [], "commands": [], "docs": [],
                "uses": [{"name": n, "where": w, "provided": False, "kind": None}
                         for n, w in uses.items()],
                "warnings": [f"resman's own skills folder {PLUGIN_FOLDER}/ was not found at "
                             f"{root} — this checkout is missing it (git status?)."]}
    desc = plugin_info.describe(root, docs=DOC_NAMES)
    plugin["description"] = " ".join(str(desc["manifest"].get("description") or "").split())
    skills, warnings = [], []
    for s in desc["skills"]:
        name = s["name"]
        fm = plugin_info.frontmatter(root / s["path"])
        fm_name = fm.get("name")
        if fm_name != name:
            warnings.append(f"{PLUGIN_FOLDER}/skills/{name}/SKILL.md says name: {fm_name!r}; "
                            f"the folder is {name!r}. Claude loads it by the frontmatter name.")
        helper = is_helper(fm)
        if helper and name in uses:
            warnings.append(f"{PLUGIN_FOLDER}/skills/{name}/SKILL.md says it is a helper "
                            f"(metadata.role), but resman runs it ({uses[name]}); a helper "
                            f"has no registry entry.")
        has_settings = (skill_dir(resman_root, name) / SETTINGS_FILE).is_file()
        if has_settings:
            try:
                load_settings_schema(resman_root, name)
            except SettingsError as exc:
                warnings.append(f"{PLUGIN_FOLDER}/skills/{name}/{SETTINGS_FILE} is unusable: {exc}")
        skills.append({**s, "used": name in uses, "helper": helper,
                       "has_settings": has_settings, "invoke": skill_prompt(name)})
    names = {s["name"] for s in skills}
    use_rows = []
    for name, where in uses.items():
        provided = name in names
        use_rows.append({"name": name, "where": where, "provided": provided,
                         "kind": "skill" if provided else None})
        if not provided:
            warnings.append(f"resman runs /{PLUGIN_NAME}:{name} ({where}), but "
                            f"{PLUGIN_FOLDER}/skills/{name}/SKILL.md is missing.")
    for name in sorted(set(stored_skills or [])):
        if name not in names:
            warnings.append(f"resman.yaml has settings for skill {name!r}, which "
                            f"{PLUGIN_FOLDER}/ does not have.")
    return {"plugin": plugin, "skills": skills, "commands": desc["commands"],
            "docs": desc["docs"], "uses": use_rows, "warnings": warnings}
