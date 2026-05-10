"""Atomic YAML config loader/saver.

Owns system.yaml and schedule.yaml. Reads via yaml.safe_load only. Saves are
atomic (.tmp + os.replace) and validated before write. On successful save,
emits `config_reloaded` on the EventBus so subscribers (VaultRegistry,
Scheduler) re-derive their state without a server restart.

Validation rules:
- File must be ≤ 1 MB
- yaml.safe_load() result must be a dict
- system.yaml: each vault entry must contain `name` and `path`; vault names
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


def validate_system_yaml(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ConfigError("system.yaml: top-level value must be a mapping")
    vaults = data.get("vaults") or []
    if not isinstance(vaults, list):
        raise ConfigError("system.yaml: 'vaults' must be a list")
    seen: set[str] = set()
    for entry in vaults:
        if not isinstance(entry, dict):
            raise ConfigError("system.yaml: each vault entry must be a mapping")
        name = entry.get("name")
        path = entry.get("path")
        if not name or not isinstance(name, str):
            raise ConfigError("system.yaml: vault entry missing 'name'")
        if not VAULT_NAME_RE.match(name):
            raise ConfigError(
                f"system.yaml: vault name {name!r} must match [a-zA-Z0-9_-]"
            )
        if name in seen:
            raise ConfigError(f"system.yaml: duplicate vault name {name!r}")
        seen.add(name)
        if not path or not isinstance(path, str):
            raise ConfigError(f"system.yaml: vault {name!r} missing 'path'")
    scan_paths = data.get("scan_paths") or []
    if not isinstance(scan_paths, list):
        raise ConfigError("system.yaml: 'scan_paths' must be a list")
    for sp in scan_paths:
        if not isinstance(sp, str) or not sp:
            raise ConfigError("system.yaml: scan_paths entries must be non-empty strings")
        # Reject filesystem-root scans
        normalized = os.path.normpath(sp)
        if normalized in ("/", "/home", "/Users", "/mnt", "/data") or normalized == "":
            raise ConfigError(f"system.yaml: scan_paths cannot be a root path ({sp})")
    return data


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


class ConfigManager:
    def __init__(self, config_dir: Path, bus: Optional[EventBus] = None) -> None:
        self.config_dir = Path(config_dir)
        self.system_path = self.config_dir / "system.yaml"
        self.schedule_path = self.config_dir / "schedule.yaml"
        self.bus = bus or get_bus()
        self._system: dict = {}
        self._schedule: dict = {"cron_tasks": []}

    def load(self) -> None:
        if not self.system_path.exists():
            raise ConfigError(
                f"system.yaml not found at {self.system_path}. "
                f"Copy system.yaml.example to system.yaml and edit it."
            )
        self._system = self._load_yaml(self.system_path, validate_system_yaml)
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

    def save_system_yaml(self, content: str) -> dict:
        return self._save("system.yaml", content, validate_system_yaml)

    def save_schedule_yaml(self, content: str) -> dict:
        return self._save("schedule.yaml", content, validate_schedule_yaml)

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
        self.save_system_yaml(text)

    def _save(self, name: str, content: str, validator) -> dict:
        if len(content.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ConfigError(f"{name}: content exceeds 1 MB")
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ConfigError(f"{name}: invalid YAML — {exc}") from exc
        if data is None:
            data = {}
        validated = validator(data)
        path = self.config_dir / name
        self._atomic_write(path, content)
        if name == "system.yaml":
            self._system = validated
        else:
            self._schedule = validated
        self.bus.emit("config_reloaded", {"file": name})
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
