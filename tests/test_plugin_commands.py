"""Tests for plugin_commands prompt builders."""
from pathlib import Path

from modules import plugin_commands


def test_new_vault_bootstrap_prompt_wraps_prefix_and_suffix(tmp_path: Path):
    pre = tmp_path / "newValPrefix.md"
    suf = tmp_path / "newValSuffix.md"
    pre.write_text("CHECK PLUGIN INSTALLED\n")
    suf.write_text("COPY workspace-visual.json -> workspace.json\n")

    out = plugin_commands.new_vault_bootstrap_prompt(pre, suf)

    assert "CHECK PLUGIN INSTALLED" in out
    assert "/claude-obsidian:wiki" in out
    assert "COPY workspace-visual.json" in out
    assert out.index("CHECK PLUGIN INSTALLED") < out.index("/claude-obsidian:wiki")
    assert out.index("/claude-obsidian:wiki") < out.index("COPY workspace-visual.json")


def test_new_vault_bootstrap_prompt_falls_back_when_files_missing(tmp_path: Path):
    out = plugin_commands.new_vault_bootstrap_prompt(
        tmp_path / "no-such-prefix.md", tmp_path / "no-such-suffix.md",
    )
    # Builder must not crash and must still contain the slash command so the
    # bootstrap step has a meaningful instruction even on a stripped checkout.
    assert "/claude-obsidian:wiki" in out


def test_new_vault_bootstrap_prompt_accepts_none_paths():
    out = plugin_commands.new_vault_bootstrap_prompt(None, None)
    assert "/claude-obsidian:wiki" in out


def test_new_vault_prefix_suffix_constants_point_into_tools():
    assert plugin_commands.NEW_VAULT_PREFIX_FILE == "tools/newValPrefix.md"
    assert plugin_commands.NEW_VAULT_SUFFIX_FILE == "tools/newValSuffix.md"
