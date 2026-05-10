import pytest
from pathlib import Path

from modules.config_manager import ConfigManager
from modules.event_bus import EventBus
from modules.vault_registry import VaultRegistry


def make_vault(base: Path, name: str, with_obsidian: bool = True) -> Path:
    p = base / name
    p.mkdir(parents=True, exist_ok=True)
    if with_obsidian:
        (p / ".obsidian").mkdir(exist_ok=True)
    return p


def write_system(cfg_dir: Path, body: str) -> None:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "system.yaml").write_text(body)


def test_validates_path_exists(tmp_path):
    vp = tmp_path / "not-here"
    write_system(tmp_path / "config", f"vaults:\n  - name: ghost\n    path: {vp}\n")
    cm = ConfigManager(tmp_path / "config", EventBus())
    cm.load()
    reg = VaultRegistry(cm)
    reg.reload()
    v = reg.get("ghost")
    assert v is not None
    assert v.path_exists is False
    assert v.is_obsidian is False


def test_validates_obsidian_dir(tmp_path):
    p = tmp_path / "vaults" / "alpha"
    p.mkdir(parents=True, exist_ok=True)
    write_system(tmp_path / "config", f"vaults:\n  - name: alpha\n    path: {p}\n")
    cm = ConfigManager(tmp_path / "config", EventBus())
    cm.load()
    reg = VaultRegistry(cm)
    reg.reload()
    v = reg.get("alpha")
    assert v.path_exists is True
    assert v.is_obsidian is False  # no .obsidian/


def test_full_valid_vault(tmp_path):
    p = make_vault(tmp_path / "vaults", "alpha")
    write_system(tmp_path / "config", f"vaults:\n  - name: alpha\n    path: {p}\n")
    cm = ConfigManager(tmp_path / "config", EventBus())
    cm.load()
    reg = VaultRegistry(cm)
    reg.reload()
    v = reg.get("alpha")
    assert v.path_exists and v.is_obsidian


def test_scan_paths_discovers_unregistered(tmp_path):
    base = tmp_path / "research"
    a = make_vault(base, "registered")
    b = make_vault(base, "discovered")
    write_system(
        tmp_path / "config",
        f"vaults:\n  - name: registered\n    path: {a}\nscan_paths:\n  - {base}\n",
    )
    cm = ConfigManager(tmp_path / "config", EventBus())
    cm.load()
    reg = VaultRegistry(cm)
    reg.reload()
    assert "registered" in reg.all_names()
    discovered_names = [v.name for v in reg.discovered]
    assert "discovered" in discovered_names


def test_reload_on_config_event(tmp_path):
    p = make_vault(tmp_path / "vaults", "alpha")
    write_system(tmp_path / "config", f"vaults:\n  - name: alpha\n    path: {p}\n")
    bus = EventBus()
    cm = ConfigManager(tmp_path / "config", bus)
    cm.load()
    reg = VaultRegistry(cm, bus)
    reg.reload()
    assert reg.get("alpha") is not None
    # Add new vault via config save
    p2 = make_vault(tmp_path / "vaults", "beta")
    cm.save_system_yaml(
        f"vaults:\n  - name: alpha\n    path: {p}\n  - name: beta\n    path: {p2}\n"
    )
    # Subscriber should have re-derived
    assert reg.get("beta") is not None
