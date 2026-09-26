"""The Skills tab's facts about the installed claude-obsidian plugin.

A fake Claude dir (CLAUDE_CONFIG_DIR, set per test by conftest) holds the
registry and the plugin cache, so nothing here reads the host's ~/.claude.
`make_plugin` / `write_registry` / `claude_dir` are conftest fixtures.
"""
import json
import re
from pathlib import Path

import pytest

from modules import plugin_commands, plugin_info

REPO = Path(__file__).resolve().parents[1]

# ----- locating the install -----

def test_not_installed_is_one_warning_not_an_error(claude_dir):
    s = plugin_info.summary()
    assert s["plugin"]["installed"] is False
    assert len(s["warnings"]) == 1
    w = s["warnings"][0]
    assert "not found in" in w and str(claude_dir() / "plugins") in w
    assert plugin_commands.PLUGIN_ID in w
    assert all(u["provided"] is False for u in s["uses"])
    assert plugin_info.plugin_dir() is None


def test_registry_entry_wins_and_carries_its_metadata(make_plugin):
    root = make_plugin()
    s = plugin_info.summary()
    p = s["plugin"]
    assert (p["installed"], p["version"], p["path"], p["located_by"]) == (
        True, "1.6.0", str(root), "registry")
    assert p["commit"] == "abc123" and p["scope"] == "user"
    assert p["description"] == "wiki companion"


def test_found_in_home_claude_dir_when_config_dir_lacks_it(
        monkeypatch, tmp_path, make_plugin, claude_dir):
    """A resman started with another CLAUDE_CONFIG_DIR still finds the
    user's own ~/.claude install, and says where it found it."""
    home = claude_dir()
    root = make_plugin("1.6.0")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "other-config"))
    monkeypatch.setattr(plugin_info, "_home_claude_dirs", lambda: [home])
    p = plugin_info.summary()["plugin"]
    assert (p["installed"], p["version"], p["path"]) == (True, "1.6.0", str(root))
    assert p["claude_dir"] == str(home)
    assert p["searched"] == [str(tmp_path / "other-config"), str(home)]


def test_config_dir_wins_over_home(monkeypatch, tmp_path, make_plugin, claude_dir):
    make_plugin("1.6.0")
    home = tmp_path / "home-claude"
    monkeypatch.setattr(plugin_info, "_home_claude_dirs", lambda: [home])
    assert plugin_info.summary()["plugin"]["claude_dir"] == str(claude_dir())


def test_found_under_a_settings_declared_marketplace(make_plugin, write_registry):
    """x1Carbon: settings.json declares the marketplace as
    agricidaniel-claude-obsidian, so the registry key is not PLUGIN_ID."""
    root = make_plugin("2.2.0", register=False)
    write_registry({"claude-obsidian@agricidaniel-claude-obsidian": [
        {"scope": "user", "installPath": str(root), "version": "2.2.0"}]})
    p = plugin_info.summary()["plugin"]
    assert (p["installed"], p["version"], p["id"]) == (
        True, "2.2.0", "claude-obsidian@agricidaniel-claude-obsidian")


def test_the_enabled_marketplace_entry_wins(make_plugin, write_registry, claude_dir):
    old = make_plugin("1.6.0", register=False)
    new = make_plugin("2.2.0", register=False)
    write_registry({
        plugin_commands.PLUGIN_ID: [{"scope": "user", "installPath": str(old), "version": "1.6.0"}],
        "claude-obsidian@agricidaniel-claude-obsidian": [
            {"scope": "user", "installPath": str(new), "version": "2.2.0"}]})
    assert plugin_info.locate().plugin_id == plugin_commands.PLUGIN_ID
    (claude_dir() / "settings.json").write_text(json.dumps(
        {"enabledPlugins": {"claude-obsidian@agricidaniel-claude-obsidian": True}}))
    found = plugin_info.locate()
    assert (found.path, found.plugin_id) == (new, "claude-obsidian@agricidaniel-claude-obsidian")


def test_cache_fallback_scans_every_marketplace(claude_dir):
    root = claude_dir() / "plugins" / "cache" / "agricidaniel-claude-obsidian" / "claude-obsidian" / "2.2.0"
    root.mkdir(parents=True)
    found = plugin_info.locate()
    assert (found.path, found.located_by, found.plugin_id) == (
        root, "cache", "claude-obsidian@agricidaniel-claude-obsidian")


def test_updated_plugin_is_found_without_any_pinned_version(make_plugin):
    """After `claude plugin update` the registry points at the new folder."""
    make_plugin("1.6.0", register=False)
    new = make_plugin("1.10.0")
    assert plugin_info.plugin_dir() == new
    assert plugin_info.summary()["plugin"]["version"] == "1.10.0"


def test_stale_registry_falls_back_to_newest_cache_folder(make_plugin, write_registry, claude_dir):
    make_plugin("1.6.0", register=False)
    newest = make_plugin("1.10.0", register=False)       # 1.10 > 1.6 numerically
    write_registry({plugin_commands.PLUGIN_ID: [
        {"scope": "user", "installPath": str(claude_dir() / "gone"), "version": "0.9"}]})
    found = plugin_info.locate()
    assert found.path == newest and found.located_by == "cache"


def test_malformed_registry_is_not_installed(claude_dir):
    reg = claude_dir() / "plugins" / "installed_plugins.json"
    reg.parent.mkdir(parents=True)
    reg.write_text("{not json")
    assert plugin_info.summary()["plugin"]["installed"] is False


def test_companion_reported_when_registered(make_plugin, write_registry):
    root = make_plugin()
    write_registry({
        plugin_commands.PLUGIN_ID: [{"scope": "user", "installPath": str(root), "version": "1.6.0"}],
        "claude-canvas@some-market": [{"scope": "user", "installPath": str(root), "version": "0.3"}],
    })
    assert plugin_info.summary()["companion"] == {
        "name": "claude-canvas", "installed": True, "version": "0.3"}


# ----- skills, commands, uses, warnings -----

def test_skills_commands_and_uses(make_plugin):
    make_plugin()
    s = plugin_info.summary()
    names = {k["name"]: k for k in s["skills"]}
    assert names["wiki"]["used"] and not names["save"]["used"]
    assert names["wiki"]["description"] == "does wiki"
    assert names["wiki"]["files"][0] == "skills/wiki/SKILL.md"
    assert "skills/wiki/references/modes.md" in names["wiki"]["files"]
    assert [c["name"] for c in s["commands"]] == ["canvas", "wiki"]
    kinds = {u["name"]: u["kind"] for u in s["uses"]}
    assert kinds["wiki"] == "skill+command" and kinds["wiki-lint"] == "skill"
    assert s["warnings"] == [] and s["docs"] == ["README.md"]


def test_a_command_resman_sends_but_the_plugin_lacks_is_a_warning(make_plugin):
    make_plugin(skills=("wiki", "wiki-ingest", "wiki-lint", "autoresearch", "canvas", "wiki-query"))
    s = plugin_info.summary()
    missing = [u["name"] for u in s["uses"] if not u["provided"]]
    assert missing == ["update-hot-cache"]
    assert len(s["warnings"]) == 1 and "update-hot-cache" in s["warnings"][0]


def test_every_plugin_command_resman_sends_is_listed_in_uses():
    """A new `claude-obsidian:<name>` anywhere in resman needs a PLUGIN_USES entry,
    or the Skills tab could not warn when the plugin drops it."""
    sources = [REPO / "control-plane" / "modules" / "plugin_commands.py",
               REPO / "control-plane" / "modules" / "new_vault.py",
               REPO / "control-plane" / "modules" / "task_manager.py",
               REPO / "control-plane" / "static" / "js" / "app.js",
               REPO / "tools" / "ingest.sh"]
    sent = set()
    for f in sources:
        sent |= set(re.findall(r"claude-obsidian:([a-z][a-z0-9-]*)", f.read_text()))
    assert sent, "no plugin commands found — the scan is broken"
    assert sent <= set(plugin_commands.PLUGIN_USES), sent - set(plugin_commands.PLUGIN_USES)


# ----- reading files -----

def test_read_file_serves_markdown_inside_the_install(make_plugin):
    make_plugin()
    assert plugin_info.read_file("skills/wiki/SKILL.md").startswith("---\nname: wiki")


@pytest.mark.parametrize("rel,status", [
    ("../../../../installed_plugins.json", 400),
    ("/etc/passwd", 400),
    (".claude-plugin/plugin.json", 400),      # not markdown
    ("skills/nope/SKILL.md", 404),
    ("", 400),
])
def test_read_file_rejects(rel, status, make_plugin):
    make_plugin()
    with pytest.raises(plugin_info.PluginFileError) as exc:
        plugin_info.read_file(rel)
    assert exc.value.status == status


def test_read_file_when_not_installed_is_404():
    with pytest.raises(plugin_info.PluginFileError) as exc:
        plugin_info.read_file("README.md")
    assert exc.value.status == 404


# ----- the new-vault prompt no longer pins a plugin version -----

def test_bootstrap_prompt_fills_the_installed_plugin_folder(tmp_path, make_plugin):
    root = make_plugin("2.0.1")
    (tmp_path / "tools").mkdir()
    (tmp_path / plugin_commands.NEW_VAULT_SUFFIX_FILE).write_text(
        "cp {plugin_dir}/.obsidian/workspace-visual.json <root>/.obsidian/workspace.json")
    out = plugin_commands.new_vault_bootstrap_prompt_for(tmp_path)
    assert f"cp {root}/.obsidian/workspace-visual.json" in out
    assert "{plugin_dir}" not in out


def test_bootstrap_prompt_without_the_plugin_says_where_to_look(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / plugin_commands.NEW_VAULT_SUFFIX_FILE).write_text("cp {plugin_dir}/x y")
    out = plugin_commands.new_vault_bootstrap_prompt_for(tmp_path)
    assert plugin_commands.PLUGIN_DIR_FALLBACK in out and "{plugin_dir}" not in out


def test_bootstrap_prompt_takes_parts_before_the_command_and_a_note(tmp_path):
    """The deep new-vault message (docs/vaultBrief-plan.md) inserts parts
    between the prefix and the command and replaces the command's trailer;
    with the defaults the message is today's, byte for byte."""
    (tmp_path / "tools").mkdir()
    (tmp_path / plugin_commands.NEW_VAULT_PREFIX_FILE).write_text("PRE")
    (tmp_path / plugin_commands.NEW_VAULT_SUFFIX_FILE).write_text("SUF {plugin_dir}")
    plain = plugin_commands.new_vault_bootstrap_prompt_for(tmp_path)
    assert plain == plugin_commands.new_vault_bootstrap_prompt_for(
        tmp_path, before_command=(), command_note="", after_suffix=())
    assert "answer any prompts it asks: /claude-obsidian:wiki" in plain
    out = plugin_commands.new_vault_bootstrap_prompt_for(
        tmp_path, before_command=("ONE", "", "TWO"), command_note="with care.")
    assert out.index("PRE") < out.index("ONE") < out.index("TWO") < out.index("/claude-obsidian:wiki") < out.index("SUF")
    assert "Now run this slash command exactly: /claude-obsidian:wiki, with care." in out
    assert "answer any prompts it asks" not in out and "\n\n\n" not in out
    # the stages after the scaffold (deepList, autoresearch) come after the suffix
    out = plugin_commands.new_vault_bootstrap_prompt_for(
        tmp_path, before_command=("ONE",), command_note="with care.", after_suffix=("THREE", "", "FOUR"))
    assert out.index("SUF") < out.index("THREE") < out.index("FOUR") and out.endswith("FOUR")
    assert "\n\n\n" not in out
    assert plugin_commands.autoresearch_prompt("x y") == plugin_commands.AUTORESEARCH + " x y"


def test_shipped_prompt_files_pin_no_plugin_version():
    for rel in (plugin_commands.NEW_VAULT_PREFIX_FILE, plugin_commands.NEW_VAULT_SUFFIX_FILE):
        text = (REPO / rel).read_text()
        assert not re.search(r"claude-obsidian/\d+\.\d+", text), rel
    assert "{plugin_dir}" in (REPO / plugin_commands.NEW_VAULT_SUFFIX_FILE).read_text()
