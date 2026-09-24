"""Test fixtures and path setup."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))


def solbench_home() -> Path:
    """The solBench checkout: $SOLBENCH_HOME › the sibling ../solBench (bench layout)."""
    env = os.environ.get("SOLBENCH_HOME")
    root = Path(env) if env else ROOT.parent / "solBench"
    if not (root / "webterm" / "pyproject.toml").is_file():
        if env:  # explicitly set but wrong: a broken host, not a missing kit
            pytest.fail(f"SOLBENCH_HOME={env} is not a solBench checkout")
        pytest.skip("solBench checkout not found beside this repo — set SOLBENCH_HOME",
                    allow_module_level=True)
    return root


@pytest.fixture(autouse=True)
def _isolate_user_resman_yaml(monkeypatch, tmp_path):
    """Stop tests from picking up a real ~/.resman.yaml on the host.

    ConfigManager prefers a per-user override at ~/.resman.yaml over the
    repo-shipped config. Without isolation, a developer's real file would
    silently replace the test fixtures — flaky and a privacy footgun.
    """
    from modules import config_manager
    monkeypatch.setattr(
        config_manager,
        "_default_user_override_path",
        lambda: tmp_path / ".no-such-resman.yaml",
    )


@pytest.fixture(autouse=True)
def _isolate_claude_dir(monkeypatch, tmp_path):
    """Point plugin_info at an empty Claude dir, not the host's ~/.claude.

    The Skills tab and the new-vault prompt read the installed claude-obsidian
    plugin from Claude Code's registry; a test must see only the fake install
    it builds itself (tests/test_plugin_info.py). The ~/.claude fallback
    is emptied too.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude-test"))
    from modules import plugin_info
    monkeypatch.setattr(plugin_info, "_home_claude_dirs", lambda: [])


# ----- a fake claude-obsidian install (tests/test_plugin_info.py, test_routes.py) -----
_MARKET = "claude-obsidian-marketplace"


def _claude_dir() -> Path:
    return Path(os.environ["CLAUDE_CONFIG_DIR"])


def _make_plugin(version="1.6.0", skills=("wiki", "wiki-ingest", "wiki-lint", "autoresearch",
                                           "canvas", "wiki-query", "update-hot-cache", "save"),
                commands=("wiki", "canvas"), register=True) -> Path:
    root = _claude_dir() / "plugins" / "cache" / _MARKET / "claude-obsidian" / version
    for name in skills:
        d = root / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: >\n  does {name}\n---\n# {name}\n")
    (root / "skills" / "wiki" / "references").mkdir(exist_ok=True)
    (root / "skills" / "wiki" / "references" / "modes.md").write_text("# modes\n")
    (root / "commands").mkdir(parents=True)
    for name in commands:
        (root / "commands" / f"{name}.md").write_text(f"---\ndescription: run {name}\n---\nbody\n")
    (root / "README.md").write_text("# readme\n")
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(
        {"name": "claude-obsidian", "version": version, "description": "wiki  companion",
         "homepage": "https://example.invalid/claude-obsidian"}))
    if register:
        from modules.plugin_commands import PLUGIN_ID
        _write_registry({PLUGIN_ID: [{
            "scope": "user", "installPath": str(root), "version": version,
            "installedAt": "2026-05-12T06:13:21.600Z", "gitCommitSha": "abc123"}]})
    return root


def _write_registry(plugins: dict) -> None:
    reg = _claude_dir() / "plugins" / "installed_plugins.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"version": 2, "plugins": plugins}))


@pytest.fixture
def claude_dir():
    return _claude_dir


@pytest.fixture
def make_plugin():
    return _make_plugin


@pytest.fixture
def write_registry():
    return _write_registry
