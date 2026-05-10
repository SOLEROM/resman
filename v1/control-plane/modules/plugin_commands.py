"""Centralized claude-obsidian plugin command strings.

Per design/06-task-management.md: all plugin command strings come exclusively
from this module. Operation handlers in task_manager.py compose subprocess
argument lists from these constants — never from user-supplied data.
"""
from __future__ import annotations

WIKI_LINT = "/claude-obsidian:wiki-lint"
WIKI_UPDATE_HOT_CACHE = "/claude-obsidian:update-hot-cache"
# Bootstrap or check the claude-obsidian wiki structure inside a vault.
# Used by the new-vault wizard right after scaffolding, and exposed as a
# standalone operation so users can re-run it on existing vaults.
WIKI_BOOTSTRAP = "/claude-obsidian:wiki"


def autoresearch_prompt(topic: str) -> str:
    return f"/claude-obsidian:autoresearch {topic}"
