"""Centralized claude-obsidian plugin command strings.

Per design/06-task-management.md: all plugin command strings come exclusively
from this module. Operation handlers in task_manager.py compose subprocess
argument lists from these constants — never from user-supplied data.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

WIKI_LINT = "/claude-obsidian:wiki-lint"
WIKI_UPDATE_HOT_CACHE = "/claude-obsidian:update-hot-cache"
# Bootstrap or check the claude-obsidian wiki structure inside a vault.
# Used by the new-vault wizard right after scaffolding, and exposed as a
# standalone operation so users can re-run it on existing vaults.
WIKI_BOOTSTRAP = "/claude-obsidian:wiki"

# Repo-root-relative paths — same convention as prompts/urlInjestPrefix.md.
# Resolved by callers with `resman_root / NEW_VAULT_PREFIX_FILE`.
NEW_VAULT_PREFIX_FILE = "tools/newValPrefix.md"
NEW_VAULT_SUFFIX_FILE = "tools/newValSuffix.md"


def autoresearch_prompt(topic: str) -> str:
    return f"/claude-obsidian:autoresearch {topic}"


def canvas_prompt(description: str = "") -> str:
    description = (description or "").strip()
    if not description:
        return "/claude-obsidian:canvas"
    return f"/claude-obsidian:canvas {description}"


def _read_optional_text(p: Optional[Path]) -> str:
    if p is None:
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def new_vault_bootstrap_prompt(
    prefix_path: Optional[Path] = None,
    suffix_path: Optional[Path] = None,
) -> str:
    """Combined prompt that wraps /claude-obsidian:wiki with prefix/suffix.

    Reads tools/newValPrefix.md and tools/newValSuffix.md if present and
    sandwiches the bootstrap slash command between them as a single
    natural-language instruction block. Missing files are skipped silently
    so the bootstrap still works on checkouts that don't ship these files.
    """
    parts: list[str] = []
    prefix = _read_optional_text(prefix_path)
    if prefix:
        parts.append(prefix)
    parts.append(
        "Now run this slash command exactly, and answer any prompts it asks: "
        + WIKI_BOOTSTRAP
    )
    suffix = _read_optional_text(suffix_path)
    if suffix:
        parts.append(suffix)
    return "\n\n".join(parts)
