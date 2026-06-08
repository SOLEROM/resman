"""Atomic YAML config loader/saver.

Owns resman.yaml and schedule.yaml. Reads via yaml.safe_load only. Saves are
atomic (.tmp + os.replace) and validated before write. On successful save,
emits `config_reloaded` on the EventBus so subscribers (VaultRegistry,
Scheduler) re-derive their state without a server restart.

Priority load for resman.yaml:
  1. ~/.resman.yaml (per-user override) — if present, this file is the source
     of truth and all UI saves write back to it.
  2. <config_dir>/resman.yaml (repo-shipped default).
The chosen path is captured at `load()` time and reused for every save so the
UI and the loader never disagree on which file is live.

Validation rules:
- File must be ≤ 1 MB
- yaml.safe_load() result must be a dict
- resman.yaml: each vault entry must contain `name` and `path`; vault names
  match [a-zA-Z0-9_-]
- schedule.yaml: each cron entry must contain `name`, `cron`, `vault`,
  `operation`, `priority`; cron string must parse via CronTrigger.from_crontab
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

from .event_bus import EventBus, get_bus

log = logging.getLogger(__name__)

MAX_CONFIG_BYTES = 1024 * 1024  # 1 MB
VAULT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class ConfigError(ValueError):
    """Raised when config is invalid. Always carries a user-facing message."""


def _validate_cron_string(expr: str) -> None:
    """Validate a cron string with APScheduler's CronTrigger.from_crontab."""
    try:
        from apscheduler.triggers.cron import CronTrigger

        CronTrigger.from_crontab(expr)
    except Exception as exc:
        raise ConfigError(f"invalid cron expression {expr!r}: {exc}") from exc


def validate_resman_yaml(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ConfigError("resman.yaml: top-level value must be a mapping")
    vaults = data.get("vaults") or []
    if not isinstance(vaults, list):
        raise ConfigError("resman.yaml: 'vaults' must be a list")
    seen: set[str] = set()
    for entry in vaults:
        if not isinstance(entry, dict):
            raise ConfigError("resman.yaml: each vault entry must be a mapping")
        name = entry.get("name")
        path = entry.get("path")
        if not name or not isinstance(name, str):
            raise ConfigError("resman.yaml: vault entry missing 'name'")
        if not VAULT_NAME_RE.match(name):
            raise ConfigError(
                f"resman.yaml: vault name {name!r} must match [a-zA-Z0-9_-]"
            )
        if name in seen:
            raise ConfigError(f"resman.yaml: duplicate vault name {name!r}")
        seen.add(name)
        if not path or not isinstance(path, str):
            raise ConfigError(f"resman.yaml: vault {name!r} missing 'path'")
        mount = entry.get("mount")
        if mount is not None:
            if not isinstance(mount, str) or not mount:
                raise ConfigError(
                    f"resman.yaml: vault {name!r} mount must be a non-empty string"
                )
            if not mount.startswith("/"):
                raise ConfigError(
                    f"resman.yaml: vault {name!r} mount must be an absolute path "
                    f"(got {mount!r})"
                )
    app = data.get("app") or {}
    if not isinstance(app, dict):
        raise ConfigError("resman.yaml: 'app' must be a mapping")
    default_root = app.get("vault_default_root_path")
    if default_root is not None:
        if not isinstance(default_root, str) or not default_root:
            raise ConfigError(
                "resman.yaml: app.vault_default_root_path must be a non-empty string"
            )
        if not default_root.startswith("/"):
            raise ConfigError(
                f"resman.yaml: app.vault_default_root_path must be an absolute path "
                f"(got {default_root!r})"
            )
    scan_paths = data.get("scan_paths") or []
    if not isinstance(scan_paths, list):
        raise ConfigError("resman.yaml: 'scan_paths' must be a list")
    for sp in scan_paths:
        if not isinstance(sp, str) or not sp:
            raise ConfigError("resman.yaml: scan_paths entries must be non-empty strings")
        # Reject filesystem-root scans
        normalized = os.path.normpath(sp)
        if normalized in ("/", "/home", "/Users", "/mnt", "/data") or normalized == "":
            raise ConfigError(f"resman.yaml: scan_paths cannot be a root path ({sp})")
    return data


# Backward-compat alias — kept so callers/tests that imported the old name
# don't break during the rename. Prefer validate_resman_yaml in new code.
validate_system_yaml = validate_resman_yaml


def validate_schedule_yaml(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ConfigError("schedule.yaml: top-level value must be a mapping")
    tasks = data.get("cron_tasks") or []
    if not isinstance(tasks, list):
        raise ConfigError("schedule.yaml: 'cron_tasks' must be a list")
    required = ("name", "cron", "vault", "operation", "priority")
    for entry in tasks:
        if not isinstance(entry, dict):
            raise ConfigError("schedule.yaml: each cron entry must be a mapping")
        for k in required:
            if k not in entry:
                raise ConfigError(f"schedule.yaml: cron entry missing '{k}'")
        _validate_cron_string(entry["cron"])
        if entry["priority"] not in ("high", "medium", "low"):
            raise ConfigError(
                f"schedule.yaml: priority must be high/medium/low (got {entry['priority']!r})"
            )
    return data


def _default_user_override_path() -> Path:
    return Path.home() / ".resman.yaml"


class ConfigManager:
    def __init__(
        self,
        config_dir: Path,
        bus: Optional[EventBus] = None,
        user_override_path: Optional[Path] = None,
    ) -> None:
        self.config_dir = Path(config_dir)
        # Resolved at load() time so we know which file is actually live.
        self.resman_path = self.config_dir / "resman.yaml"
        self.schedule_path = self.config_dir / "schedule.yaml"
        self.bus = bus or get_bus()
        self._system: dict = {}
        self._schedule: dict = {"cron_tasks": []}
        # True once load() picked the user override.
        self._using_user_override: bool = False
        # Test seam: tests pass a non-existent path so the real user file
        # at ~/.resman.yaml cannot leak into the test environment.
        self._user_override_path: Path = (
            Path(user_override_path) if user_override_path is not None
            else _default_user_override_path()
        )

    # Backward-compat: existing callers still read `system_path`. Keep it as
    # an alias for the live resman.yaml path.
    @property
    def system_path(self) -> Path:
        return self.resman_path

    @property
    def using_user_override(self) -> bool:
        return self._using_user_override

    def load(self) -> None:
        # Priority: ~/.resman.yaml first; fall back to <config_dir>/resman.yaml.
        # Legacy: if neither exists but <config_dir>/system.yaml does, use it
        # so existing checkouts keep working until the user renames the file.
        repo_default = self.config_dir / "resman.yaml"
        legacy = self.config_dir / "system.yaml"
        if self._user_override_path.exists():
            self.resman_path = self._user_override_path
            self._using_user_override = True
            log.info("config: using user override at %s", self._user_override_path)
        elif repo_default.exists():
            self.resman_path = repo_default
            self._using_user_override = False
        elif legacy.exists():
            self.resman_path = legacy
            self._using_user_override = False
            log.warning(
                "config: loading legacy system.yaml at %s — rename it to resman.yaml",
                legacy,
            )
        else:
            raise ConfigError(
                f"resman.yaml not found. Looked in:\n"
                f"  {self._user_override_path}\n"
                f"  {repo_default}\n"
                f"Copy resman.yaml.example to resman.yaml and edit it, "
                f"or place a ~/.resman.yaml."
            )
        self._system = self._load_yaml(self.resman_path, validate_resman_yaml)
        if self.schedule_path.exists():
            self._schedule = self._load_yaml(self.schedule_path, validate_schedule_yaml)
        else:
            self._schedule = {"cron_tasks": []}

    @staticmethod
    def _load_yaml(path: Path, validator) -> dict:
        size = path.stat().st_size
        if size > MAX_CONFIG_BYTES:
            raise ConfigError(f"{path.name} is {size} bytes — exceeds 1 MB limit")
        try:
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path.name}: invalid YAML — {exc}") from exc
        if data is None:
            data = {}
        return validator(data)

    @property
    def system(self) -> dict:
        return self._system

    @property
    def schedule(self) -> dict:
        return self._schedule

    @property
    def app(self) -> dict:
        return self._system.get("app", {}) or {}

    @property
    def vaults(self) -> list[dict]:
        return list(self._system.get("vaults") or [])

    @property
    def scan_paths(self) -> list[str]:
        return list(self._system.get("scan_paths") or [])

    @property
    def cron_tasks(self) -> list[dict]:
        return list(self._schedule.get("cron_tasks") or [])

    def get_vault(self, name: str) -> Optional[dict]:
        for entry in self.vaults:
            if entry.get("name") == name:
                return entry
        return None

    def save_resman_yaml(self, content: str) -> dict:
        # Write back to whichever file load() selected so the user override
        # at ~/.resman.yaml stays authoritative across edits.
        return self._save_to(self.resman_path, "resman.yaml", content, validate_resman_yaml)

    # Back-compat alias. Existing routes still call save_system_yaml.
    def save_system_yaml(self, content: str) -> dict:
        return self.save_resman_yaml(content)

    def save_schedule_yaml(self, content: str) -> dict:
        return self._save_to(
            self.schedule_path, "schedule.yaml", content, validate_schedule_yaml,
        )

    def add_vault(self, name: str, path: str, tags: list[str] | None = None) -> None:
        if not VAULT_NAME_RE.match(name or ""):
            raise ConfigError(f"vault name {name!r} must match [a-zA-Z0-9_-]")
        if self.get_vault(name) is not None:
            raise ConfigError(f"vault {name!r} already registered")
        new = {"name": name, "path": path, "tags": list(tags or [])}
        data = dict(self._system)
        vaults = list(self.vaults)
        vaults.append(new)
        data["vaults"] = vaults
        text = yaml.safe_dump(data, sort_keys=False)
        self.save_resman_yaml(text)

    def _save_to(self, path: Path, logical_name: str, content: str, validator) -> dict:
        if len(content.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ConfigError(f"{logical_name}: content exceeds 1 MB")
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ConfigError(f"{logical_name}: invalid YAML — {exc}") from exc
        if data is None:
            data = {}
        validated = validator(data)
        self._atomic_write(path, content)
        if logical_name == "resman.yaml":
            self._system = validated
        else:
            self._schedule = validated
        self.bus.emit("config_reloaded", {"file": logical_name})
        return validated

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
