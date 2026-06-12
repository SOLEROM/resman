"""MountManager — bind-mounts vaults at optional host paths.

Each vault entry in resman.yaml may carry an optional ``mount: /abs/path``
key. When present, MountManager runs ``mount --bind <vault.path> <mount>``
so the vault's files are accessible at the target path on the host.

Privileges: ``mount --bind`` requires root or a matching NOPASSWD sudoers
entry. See ``man/mounts.md`` for the recommended setup and how to take
config changes to effect.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

from .event_bus import EventBus, get_bus

log = logging.getLogger(__name__)


def _is_mounted(target: str) -> bool:
    """Return True if *target* appears as a mount point in /proc/mounts."""
    resolved = os.path.realpath(target)
    try:
        with open("/proc/mounts", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == resolved:
                    return True
    except OSError:
        pass
    return False


def _sudo_prefix() -> list[str]:
    """Return ['sudo'] when not running as root, empty list otherwise."""
    return [] if os.geteuid() == 0 else ["sudo"]


def _run_mount(source: str, target: str) -> tuple[bool, str]:
    """Execute ``(sudo) mount --bind source target``. Returns (success, error_msg)."""
    cmd = _sudo_prefix() + ["mount", "--bind", source, target]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return True, ""
        return False, (r.stderr or r.stdout or "").strip()
    except FileNotFoundError:
        return False, f"{cmd[0]!r} not found"
    except subprocess.TimeoutExpired:
        return False, "mount timed out after 10 s"
    except OSError as exc:
        return False, str(exc)


def _run_umount(target: str) -> tuple[bool, str]:
    """Execute ``(sudo) umount target``. Returns (success, error_msg)."""
    cmd = _sudo_prefix() + ["umount", target]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return True, ""
        return False, (r.stderr or r.stdout or "").strip()
    except FileNotFoundError:
        return False, f"{cmd[0]!r} not found"
    except subprocess.TimeoutExpired:
        return False, "umount timed out after 10 s"
    except OSError as exc:
        return False, str(exc)


class MountManager:
    """Reconcile running bind-mounts against the vault list.

    Usage::

        mm = MountManager(bus=bus, get_vaults=lambda: vault_registry.registered)
        mm.sync(vault_registry.registered)   # called once at startup
        # … config_reloaded events are handled automatically …
        mm.umount_all()                       # called at shutdown via atexit
    """

    def __init__(
        self,
        bus: Optional[EventBus] = None,
        get_vaults: Optional[Callable] = None,
    ) -> None:
        self.bus = bus or get_bus()
        self._get_vaults = get_vaults
        # vault_name -> mount_point for every mount WE established this run
        self._active: dict[str, str] = {}
        self.bus.subscribe("config_reloaded", self._on_config_reloaded)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync(self, vaults) -> None:
        """Reconcile mounts against *vaults*.

        - Vaults with ``mount`` set that are not yet mounted → mount them.
        - Previously-mounted vaults whose mount is gone or changed → unmount.
        """
        wanted: dict[str, object] = {v.name: v for v in vaults if v.mount}

        for name, point in list(self._active.items()):
            configured = getattr(wanted.get(name), "mount", None)
            if configured != point:
                self._do_umount(name, point)

        for name, v in wanted.items():
            if name in self._active:
                continue
            if _is_mounted(v.mount):
                log.info("mount: %s already mounted at %s (tracking)", name, v.mount)
                self._active[name] = v.mount
                continue
            self._do_mount(name, v.path, v.mount)

    def mount_one(self, vault) -> bool:
        """Bind-mount a single vault. Returns True on success."""
        if not vault.mount:
            return False
        if _is_mounted(vault.mount):
            self._active[vault.name] = vault.mount
            return True
        self._do_mount(vault.name, vault.path, vault.mount)
        return vault.name in self._active

    def umount_one(self, vault_name: str) -> bool:
        """Unmount a single vault by name. Returns True on success."""
        point = self._active.get(vault_name)
        if not point:
            return False
        self._do_umount(vault_name, point)
        return vault_name not in self._active

    def umount_all(self) -> None:
        """Unmount everything we mounted — call at shutdown."""
        for name, point in list(self._active.items()):
            self._do_umount(name, point)

    def is_mounted(self, vault_name: str) -> bool:
        return vault_name in self._active

    def status(self) -> dict[str, str]:
        """Snapshot of active mounts: vault_name -> mount_point."""
        return dict(self._active)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _do_mount(self, vault_name: str, source: str, target: str) -> None:
        target_path = Path(target)
        if not target_path.exists():
            try:
                target_path.mkdir(parents=True, exist_ok=True)
                log.info("mount: created mount-point directory %s", target)
            except OSError as exc:
                log.error("mount: cannot create mount-point %s: %s", target, exc)
                return
        ok, msg = _run_mount(source, target)
        if ok:
            self._active[vault_name] = target
            log.info("mount: bound %s -> %s", source, target)
        else:
            log.error(
                "mount: failed to bind %s -> %s: %s. "
                "Add a NOPASSWD sudoers entry for 'sudo mount --bind' and 'sudo umount' "
                "— see Help > Mounts.",
                source, target, msg,
            )

    def _do_umount(self, vault_name: str, target: str) -> None:
        ok, msg = _run_umount(target)
        if ok:
            self._active.pop(vault_name, None)
            log.info("mount: unmounted %s", target)
        else:
            log.error("mount: failed to unmount %s: %s", target, msg)
            # Remove from tracking regardless — don't block config reloads.
            self._active.pop(vault_name, None)

    def _on_config_reloaded(self, _payload: dict) -> None:
        if self._get_vaults is not None:
            self.sync(self._get_vaults())
