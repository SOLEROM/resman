"""VaultRegistry — owns the in-memory list of vaults.

Loads vaults from system.yaml, validates each path, and optionally scans
scan_paths directories for unregistered vaults. Re-derives state from
config_manager on every config_reloaded EventBus event.

Vault validation produces two distinct warnings:
- path_not_found: the path does not exist on disk
- not_a_vault: the path exists but does not contain .obsidian/
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

from .config_manager import ConfigManager
from .event_bus import EventBus, get_bus

log = logging.getLogger(__name__)

# Maximum recursion depth when scanning scan_paths for unregistered vaults
SCAN_MAX_DEPTH = 2


@dataclass
class Vault:
    name: str
    path: str
    tags: List[str] = field(default_factory=list)
    mount: Optional[str] = None   # bind-mount target path, if configured
    registered: bool = True
    path_exists: bool = True
    is_obsidian: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class VaultRegistry:
    def __init__(self, config: ConfigManager, bus: Optional[EventBus] = None) -> None:
        self.config = config
        self.bus = bus or get_bus()
        self._registered: List[Vault] = []
        self._discovered: List[Vault] = []
        self.bus.subscribe("config_reloaded", self._on_config_reloaded)

    def reload(self) -> None:
        self._registered = self._load_registered()
        self._discovered = self._scan_unregistered()

    def _on_config_reloaded(self, _payload: dict) -> None:
        self.reload()

    def _load_registered(self) -> List[Vault]:
        out: List[Vault] = []
        for entry in self.config.vaults:
            v = Vault(
                name=entry["name"],
                path=entry["path"],
                tags=list(entry.get("tags") or []),
                mount=entry.get("mount") or None,
                registered=True,
            )
            v.path_exists = Path(v.path).exists()
            v.is_obsidian = v.path_exists and (Path(v.path) / ".obsidian").is_dir()
            out.append(v)
        return out

    def _scan_unregistered(self) -> List[Vault]:
        registered_paths = {Path(v.path).resolve() for v in self._registered}
        out: List[Vault] = []
        for sp in self.config.scan_paths:
            base = Path(sp)
            if not base.is_dir():
                continue
            for found in self._walk_for_obsidian(base, depth=0):
                resolved = found.resolve()
                if resolved in registered_paths:
                    continue
                v = Vault(
                    name=found.name,
                    path=str(found),
                    tags=[],
                    registered=False,
                    path_exists=True,
                    is_obsidian=True,
                )
                out.append(v)
        return out

    @staticmethod
    def _walk_for_obsidian(base: Path, depth: int):
        """Yield every directory under base (depth ≤ SCAN_MAX_DEPTH) that contains .obsidian/."""
        if depth > SCAN_MAX_DEPTH:
            return
        try:
            children = list(base.iterdir())
        except OSError:
            return
        for child in children:
            if not child.is_dir():
                continue
            if (child / ".obsidian").is_dir():
                yield child
                continue
            yield from VaultRegistry._walk_for_obsidian(child, depth + 1)

    @property
    def registered(self) -> List[Vault]:
        return list(self._registered)

    @property
    def discovered(self) -> List[Vault]:
        return list(self._discovered)

    def get(self, name: str) -> Optional[Vault]:
        for v in self._registered:
            if v.name == name:
                return v
        return None

    def all_names(self) -> List[str]:
        return [v.name for v in self._registered]

    def to_list(self) -> List[dict]:
        return [v.to_dict() for v in self._registered] + [
            v.to_dict() for v in self._discovered
        ]
