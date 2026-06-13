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

# Generate (or refresh) the vault's wiki/hint.json — the short description the
# landing page reads to render a vault's thumbnail card. Unlike garage, which
# parses Claude's stdout and writes the file from the web layer, a resman task
# runs `claude -p` directly in the vault with write access, so the prompt
# instructs Claude to inspect the wiki via the wiki-query skill AND write the
# hint.json itself. The schema mirrors modules/vault_hints.py: label, summary,
# tags[], updatedBy, updatedAt, source.
WIKI_HINT = (
    "You are running inside an Obsidian wiki vault. The current working "
    "directory is the vault root; its wiki lives in ./wiki/.\n\n"
    "Goal: generate (or refresh) ./wiki/hint.json — a short, machine-readable "
    "description of what THIS vault is about. resman's landing page reads it to "
    "render the vault's thumbnail card, and URL classifiers read it to decide "
    "whether an incoming link belongs to this vault. So the summary must "
    "describe the TOPIC of the vault, not the structure of the wiki.\n\n"
    "Step 1 — inspect. Use the installed Claude Code plugin skill "
    "`claude-obsidian:wiki-query` to inspect the wiki: read wiki/index.md and a "
    "representative sample of page titles and frontmatter. Invoke the skill "
    "directly; do not re-implement its workflow yourself. If the wiki is empty "
    "or unbuilt, fall back to reading wiki/hot.md, wiki/overview.md and the "
    "vault README, and summarise from those.\n\n"
    "Step 2 — decide. From what you read, choose:\n"
    "  - label:   a short display name, 1-3 words.\n"
    "  - summary: one line, at most 300 characters, of what the vault covers.\n"
    "  - tags:    3-8 lowercase topical tags.\n\n"
    "Step 3 — write. Write the file ./wiki/hint.json (overwrite it if it "
    "already exists) with EXACTLY this JSON shape, 2-space indented:\n"
    "{\n"
    '  "label": "<label>",\n'
    '  "summary": "<summary>",\n'
    '  "tags": ["tag1", "tag2", "..."],\n'
    '  "updatedBy": "resman-auto",\n'
    '  "updatedAt": "<current UTC time, ISO-8601 with a trailing Z, '
    'e.g. 2026-01-01T00:00:00Z>",\n'
    '  "source": "auto"\n'
    "}\n"
    "Use the Write tool, and write only that one file. When done, print a "
    "single line confirming the label and summary you saved."
)
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
