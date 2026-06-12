"""Tests for MountManager.

All subprocess calls are mocked — no actual mounting happens. /proc/mounts
reads are also mocked so tests are hermetic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
from unittest.mock import MagicMock, mock_open, patch

import pytest

from modules.event_bus import EventBus
from modules.mount_manager import MountManager, _is_mounted


# ---------------------------------------------------------------------------
# Minimal Vault stand-in (mirrors vault_registry.Vault fields we use)
# ---------------------------------------------------------------------------

@dataclass
class _Vault:
    name: str
    path: str
    mount: Optional[str] = None
    tags: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# _is_mounted helper
# ---------------------------------------------------------------------------

def test_is_mounted_true_when_path_in_proc_mounts(tmp_path):
    target = str(tmp_path)
    content = f"overlay {target} overlay rw 0 0\n"
    with patch("builtins.open", mock_open(read_data=content)):
        with patch("os.path.realpath", return_value=target):
            assert _is_mounted(target) is True


def test_is_mounted_false_when_path_absent(tmp_path):
    content = "/dev/sda1 /boot ext4 rw 0 0\n"
    with patch("builtins.open", mock_open(read_data=content)):
        with patch("os.path.realpath", return_value=str(tmp_path)):
            assert _is_mounted(str(tmp_path)) is False


def test_is_mounted_false_on_oserror():
    with patch("builtins.open", side_effect=OSError("no /proc")):
        assert _is_mounted("/some/path") is False


# ---------------------------------------------------------------------------
# MountManager.sync
# ---------------------------------------------------------------------------

def _make_mm(**kwargs):
    bus = EventBus()
    return MountManager(bus=bus, **kwargs), bus


def _mock_run_ok(monkeypatch):
    monkeypatch.setattr(
        "modules.mount_manager._run_mount", lambda src, tgt: (True, "")
    )
    monkeypatch.setattr(
        "modules.mount_manager._run_umount", lambda tgt: (True, "")
    )
    monkeypatch.setattr(
        "modules.mount_manager._is_mounted", lambda tgt: False
    )


def test_sync_mounts_vault_with_mount_field(tmp_path, monkeypatch):
    _mock_run_ok(monkeypatch)
    mm, _ = _make_mm()
    vault = _Vault(name="v1", path="/src/v1", mount=str(tmp_path / "mnt"))
    mm.sync([vault])
    assert mm.is_mounted("v1")
    assert mm.status() == {"v1": str(tmp_path / "mnt")}


def test_sync_skips_vault_without_mount(monkeypatch):
    _mock_run_ok(monkeypatch)
    mm, _ = _make_mm()
    vault = _Vault(name="v1", path="/src/v1", mount=None)
    mm.sync([vault])
    assert not mm.is_mounted("v1")


def test_sync_tracks_already_mounted(monkeypatch):
    monkeypatch.setattr("modules.mount_manager._run_mount", lambda s, t: (True, ""))
    monkeypatch.setattr("modules.mount_manager._run_umount", lambda t: (True, ""))
    # Pretend /mnt/v1 is already mounted
    monkeypatch.setattr("modules.mount_manager._is_mounted", lambda t: True)
    mm, _ = _make_mm()
    vault = _Vault(name="v1", path="/src/v1", mount="/mnt/v1")
    mm.sync([vault])
    # Should track it without calling mount again
    assert mm.is_mounted("v1")


def test_sync_unmounts_removed_vault(tmp_path, monkeypatch):
    _mock_run_ok(monkeypatch)
    mm, _ = _make_mm()
    vault = _Vault(name="v1", path="/src/v1", mount=str(tmp_path / "mnt"))
    mm.sync([vault])
    assert mm.is_mounted("v1")
    # Second sync without that vault — should unmount
    mm.sync([])
    assert not mm.is_mounted("v1")


def test_sync_remounts_on_mount_point_change(tmp_path, monkeypatch):
    umounted = []
    mounted = []
    monkeypatch.setattr(
        "modules.mount_manager._run_mount",
        lambda s, t: (mounted.append(t), (True, ""))[1],
    )
    monkeypatch.setattr(
        "modules.mount_manager._run_umount",
        lambda t: (umounted.append(t), (True, ""))[1],
    )
    monkeypatch.setattr("modules.mount_manager._is_mounted", lambda t: False)
    mm, _ = _make_mm()
    old_mnt = str(tmp_path / "old")
    new_mnt = str(tmp_path / "new")
    vault_v1 = _Vault(name="v1", path="/src/v1", mount=old_mnt)
    mm.sync([vault_v1])
    assert old_mnt in mounted
    # Change mount point
    vault_v2 = _Vault(name="v1", path="/src/v1", mount=new_mnt)
    mm.sync([vault_v2])
    assert old_mnt in umounted
    assert new_mnt in mounted


# ---------------------------------------------------------------------------
# mount_one / umount_one
# ---------------------------------------------------------------------------

def test_mount_one_returns_true_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr("modules.mount_manager._run_mount", lambda s, t: (True, ""))
    monkeypatch.setattr("modules.mount_manager._is_mounted", lambda t: False)
    mm, _ = _make_mm()
    vault = _Vault(name="v1", path="/src/v1", mount=str(tmp_path / "mnt"))
    assert mm.mount_one(vault) is True
    assert mm.is_mounted("v1")


def test_mount_one_returns_false_when_no_mount_configured(monkeypatch):
    mm, _ = _make_mm()
    vault = _Vault(name="v1", path="/src/v1", mount=None)
    assert mm.mount_one(vault) is False


def test_umount_one_returns_true_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr("modules.mount_manager._run_mount", lambda s, t: (True, ""))
    monkeypatch.setattr("modules.mount_manager._run_umount", lambda t: (True, ""))
    monkeypatch.setattr("modules.mount_manager._is_mounted", lambda t: False)
    mm, _ = _make_mm()
    vault = _Vault(name="v1", path="/src/v1", mount=str(tmp_path / "mnt"))
    mm.mount_one(vault)
    assert mm.umount_one("v1") is True
    assert not mm.is_mounted("v1")


def test_umount_one_returns_false_when_not_mounted():
    mm, _ = _make_mm()
    assert mm.umount_one("nonexistent") is False


# ---------------------------------------------------------------------------
# umount_all
# ---------------------------------------------------------------------------

def test_umount_all_clears_active(tmp_path, monkeypatch):
    _mock_run_ok(monkeypatch)
    mm, _ = _make_mm()
    vaults = [
        _Vault(name=f"v{i}", path=f"/src/v{i}", mount=str(tmp_path / f"m{i}"))
        for i in range(3)
    ]
    mm.sync(vaults)
    assert len(mm.status()) == 3
    mm.umount_all()
    assert mm.status() == {}


# ---------------------------------------------------------------------------
# Mount failure handling
# ---------------------------------------------------------------------------

def test_mount_failure_logged_not_raised(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(
        "modules.mount_manager._run_mount", lambda s, t: (False, "Permission denied")
    )
    monkeypatch.setattr("modules.mount_manager._is_mounted", lambda t: False)
    mm, _ = _make_mm()
    vault = _Vault(name="v1", path="/src/v1", mount=str(tmp_path / "mnt"))
    # Must not raise
    mm.sync([vault])
    assert not mm.is_mounted("v1")


def test_umount_failure_removes_from_tracking(tmp_path, monkeypatch):
    monkeypatch.setattr("modules.mount_manager._run_mount", lambda s, t: (True, ""))
    monkeypatch.setattr("modules.mount_manager._is_mounted", lambda t: False)
    mm, _ = _make_mm()
    vault = _Vault(name="v1", path="/src/v1", mount=str(tmp_path / "mnt"))
    mm.sync([vault])
    assert mm.is_mounted("v1")
    # Now umount fails
    monkeypatch.setattr("modules.mount_manager._run_umount", lambda t: (False, "device busy"))
    mm.umount_all()
    # Removed from tracking even on failure so config reloads aren't blocked
    assert not mm.is_mounted("v1")


# ---------------------------------------------------------------------------
# config_reloaded integration
# ---------------------------------------------------------------------------

def test_config_reloaded_triggers_sync(tmp_path, monkeypatch):
    _mock_run_ok(monkeypatch)
    vault = _Vault(name="v1", path="/src/v1", mount=str(tmp_path / "mnt"))
    mm, bus = _make_mm(get_vaults=lambda: [vault])
    # Fire event — should sync and pick up the new vault
    bus.emit("config_reloaded", {"file": "resman.yaml"})
    assert mm.is_mounted("v1")
