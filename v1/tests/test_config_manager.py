import textwrap
import pytest
from pathlib import Path

from modules.config_manager import ConfigManager, ConfigError
from modules.event_bus import EventBus


def write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content))


@pytest.fixture
def cfg_dir(tmp_path):
    return tmp_path / "config"


def test_load_missing_resman_yaml_raises(cfg_dir):
    bus = EventBus()
    cm = ConfigManager(cfg_dir, bus)
    with pytest.raises(ConfigError):
        cm.load()


def test_load_valid_resman_yaml(cfg_dir):
    write(cfg_dir / "resman.yaml", """
        app:
          host: 127.0.0.1
        vaults:
          - name: alpha
            path: /tmp/alpha
            tags: [a]
    """)
    cm = ConfigManager(cfg_dir, EventBus())
    cm.load()
    assert cm.app["host"] == "127.0.0.1"
    assert cm.vaults[0]["name"] == "alpha"


def test_invalid_vault_name_rejected(cfg_dir):
    write(cfg_dir / "resman.yaml", """
        vaults:
          - name: bad name!
            path: /tmp/x
    """)
    cm = ConfigManager(cfg_dir, EventBus())
    with pytest.raises(ConfigError, match="must match"):
        cm.load()


def test_duplicate_vault_name_rejected(cfg_dir):
    write(cfg_dir / "resman.yaml", """
        vaults:
          - name: alpha
            path: /tmp/a1
          - name: alpha
            path: /tmp/a2
    """)
    cm = ConfigManager(cfg_dir, EventBus())
    with pytest.raises(ConfigError, match="duplicate"):
        cm.load()


def test_save_emits_config_reloaded(cfg_dir):
    write(cfg_dir / "resman.yaml", """
        vaults:
          - name: alpha
            path: /tmp/alpha
    """)
    bus = EventBus()
    received = []
    bus.subscribe("config_reloaded", lambda p: received.append(p))
    cm = ConfigManager(cfg_dir, bus)
    cm.load()
    cm.save_system_yaml("vaults:\n  - name: beta\n    path: /tmp/beta\n")
    assert received and received[0]["file"] == "resman.yaml"
    assert cm.get_vault("beta") is not None
    # File on disk reflects the change
    assert "beta" in (cfg_dir / "resman.yaml").read_text()


def test_save_too_large_rejected(cfg_dir):
    write(cfg_dir / "resman.yaml", "vaults: []\n")
    cm = ConfigManager(cfg_dir, EventBus())
    cm.load()
    big = "vaults: []\n# " + ("a" * (1024 * 1024 + 10))
    with pytest.raises(ConfigError, match="1 MB"):
        cm.save_system_yaml(big)


def test_invalid_cron_string_rejected_on_save(cfg_dir):
    write(cfg_dir / "resman.yaml", "vaults: []\n")
    cm = ConfigManager(cfg_dir, EventBus())
    cm.load()
    with pytest.raises(ConfigError, match="invalid cron"):
        cm.save_schedule_yaml(
            "cron_tasks:\n  - name: bad\n    cron: 'not a cron'\n    vault: ALL\n    operation: wiki-lint\n    priority: low\n"
        )


def test_atomic_write_creates_no_partial_on_error(cfg_dir, monkeypatch):
    write(cfg_dir / "resman.yaml", "vaults: []\n")
    cm = ConfigManager(cfg_dir, EventBus())
    cm.load()
    # A duplicate-name save should fail validation; original file must be untouched.
    bad = "vaults:\n  - name: alpha\n    path: /tmp/a\n  - name: alpha\n    path: /tmp/b\n"
    with pytest.raises(ConfigError):
        cm.save_system_yaml(bad)
    assert (cfg_dir / "resman.yaml").read_text() == "vaults: []\n"


def test_add_vault(cfg_dir):
    write(cfg_dir / "resman.yaml", "vaults:\n  - name: a\n    path: /tmp/a\n")
    cm = ConfigManager(cfg_dir, EventBus())
    cm.load()
    cm.add_vault("b", "/tmp/b", tags=["x"])
    assert cm.get_vault("b") is not None
    assert "name: b" in (cfg_dir / "resman.yaml").read_text()


def test_add_duplicate_vault_raises(cfg_dir):
    write(cfg_dir / "resman.yaml", "vaults:\n  - name: a\n    path: /tmp/a\n")
    cm = ConfigManager(cfg_dir, EventBus())
    cm.load()
    with pytest.raises(ConfigError, match="already registered"):
        cm.add_vault("a", "/tmp/dup")


def test_scan_paths_root_rejected(cfg_dir):
    write(cfg_dir / "resman.yaml", """
        vaults: []
        scan_paths:
          - /
    """)
    cm = ConfigManager(cfg_dir, EventBus())
    with pytest.raises(ConfigError, match="root path"):
        cm.load()


def test_user_override_takes_priority_over_repo(tmp_path):
    """If ~/.resman.yaml exists, it wins over <config_dir>/resman.yaml."""
    cfg_dir = tmp_path / "config"
    write(cfg_dir / "resman.yaml", """
        vaults:
          - name: repo
            path: /tmp/repo
    """)
    user_override = tmp_path / "user-resman.yaml"
    write(user_override, """
        vaults:
          - name: user
            path: /tmp/user
    """)
    cm = ConfigManager(cfg_dir, EventBus(), user_override_path=user_override)
    cm.load()
    assert cm.using_user_override is True
    assert cm.system_path == user_override
    assert cm.get_vault("user") is not None
    assert cm.get_vault("repo") is None
    # Saves go to the override file, not the repo file
    cm.save_resman_yaml("vaults:\n  - name: user2\n    path: /tmp/u2\n")
    assert "user2" in user_override.read_text()
    assert "user2" not in (cfg_dir / "resman.yaml").read_text()


def test_user_override_falls_back_to_repo_when_missing(tmp_path):
    cfg_dir = tmp_path / "config"
    write(cfg_dir / "resman.yaml", """
        vaults:
          - name: repo
            path: /tmp/repo
    """)
    cm = ConfigManager(
        cfg_dir, EventBus(),
        user_override_path=tmp_path / "does-not-exist.yaml",
    )
    cm.load()
    assert cm.using_user_override is False
    assert cm.get_vault("repo") is not None


def test_legacy_system_yaml_still_loads(tmp_path):
    """Old checkouts with config/system.yaml keep working until renamed."""
    cfg_dir = tmp_path / "config"
    write(cfg_dir / "system.yaml", """
        vaults:
          - name: legacy
            path: /tmp/legacy
    """)
    cm = ConfigManager(cfg_dir, EventBus())
    cm.load()
    assert cm.get_vault("legacy") is not None
