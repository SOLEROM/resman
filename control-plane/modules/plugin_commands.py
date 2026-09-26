"""Centralized claude-obsidian plugin command strings.

Per design/06-task-management.md: all plugin command strings come exclusively
from this module. Operation handlers in task_manager.py compose subprocess
argument lists from these constants — never from user-supplied data.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

# The Claude Code plugin every command below belongs to: its registry key in
# ~/.claude/plugins/installed_plugins.json after `claude plugin marketplace add`.
# plugin_info.py also finds it under any other marketplace name
# (e.g. claude-obsidian@agricidaniel-claude-obsidian); commands use only the
# plugin name, so they work either way.
PLUGIN_ID = "claude-obsidian@claude-obsidian-marketplace"

# Every claude-obsidian skill/command resman sends, and where from. The Skills
# tab checks each name against the installed plugin and warns on a miss;
# tests/test_plugin_info.py fails when a new `claude-obsidian:<name>` appears in
# resman without an entry here.
PLUGIN_USES = {
    "wiki": "new-vault wizard bootstrap session and the wiki-bootstrap task",
    "wiki-ingest": "wiki-ingest tasks (tools/ingest.sh)",
    "wiki-lint": "wiki-lint task",
    "update-hot-cache": "wiki-update-hot-cache task",
    "autoresearch": "wiki-autoresearch task and the Deep interview's research stage (modules/new_vault.py)",
    "canvas": "wiki-canvas task and ingest's canvas update",
    "wiki-query": "wiki-hint task (vault card description)",
}

# Written in tools/newValPrefix.md / newValSuffix.md where the plugin's install
# folder is meant; replaced with the resolved folder of whatever version is
# installed, so the prompt files never pin a version.
PLUGIN_DIR_PLACEHOLDER = "{plugin_dir}"
PLUGIN_DIR_FALLBACK = ("<the claude-obsidian install folder: the newest version "
                       "folder under ~/.claude/plugins/cache/<marketplace>/"
                       "claude-obsidian/>")

WIKI_LINT = "/claude-obsidian:wiki-lint"
WIKI_UPDATE_HOT_CACHE = "/claude-obsidian:update-hot-cache"
# One deep-research run on a topic; the wiki-autoresearch task sends it with
# the operator's topic, and every open value of deepList's page carries one.
AUTORESEARCH = "/claude-obsidian:autoresearch"

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
    "Step 1 — inspect. If ./wiki/meta/brief.md exists (resman's vault-brief "
    "skill writes it), read it first: its Purpose and Scope sections are the "
    "vault's topic and its Seed domains and Entities are its tags. Then use the "
    "installed Claude Code plugin skill "
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
    return f"{AUTORESEARCH} {topic}"


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
    plugin_dir: Optional[Path] = None,
    before_command: tuple = (),
    command_note: str = "",
    after_suffix: tuple = (),
) -> str:
    """Combined prompt that wraps /claude-obsidian:wiki with prefix/suffix.

    Reads tools/newValPrefix.md and tools/newValSuffix.md if present and
    sandwiches the bootstrap slash command between them as a single
    natural-language instruction block. Missing files are skipped silently
    so the bootstrap still works on checkouts that don't ship these files.
    ``{plugin_dir}`` in either file becomes ``plugin_dir`` (the installed
    plugin's folder), or a findable description of it when that is unknown.

    ``before_command`` are extra parts placed between the prefix and the
    command (empty ones skipped); ``command_note`` replaces the command
    line's trailer ("…exactly: /claude-obsidian:wiki, <note>");
    ``after_suffix`` are parts placed after the suffix, at the very end (the
    stages that run once the wiki exists: deepList, the research). All three
    are how the Deep interview message (modules/new_vault.py) is built; with
    the defaults the message is the basic one, byte for byte.
    """
    parts: list[str] = []
    prefix = _read_optional_text(prefix_path)
    if prefix:
        parts.append(prefix)
    parts.extend(str(p) for p in before_command if p)
    if command_note:
        parts.append("Now run this slash command exactly: " + WIKI_BOOTSTRAP + ", " + command_note)
    else:
        parts.append(
            "Now run this slash command exactly, and answer any prompts it asks: "
            + WIKI_BOOTSTRAP
        )
    suffix = _read_optional_text(suffix_path)
    if suffix:
        parts.append(suffix)
    parts.extend(str(p) for p in after_suffix if p)
    where = str(plugin_dir) if plugin_dir else PLUGIN_DIR_FALLBACK
    return "\n\n".join(parts).replace(PLUGIN_DIR_PLACEHOLDER, where)


def new_vault_bootstrap_prompt_for(repo_root: Path, before_command: tuple = (),
                                   command_note: str = "", after_suffix: tuple = ()) -> str:
    """The bootstrap prompt from the repo's prefix/suffix files, with the
    installed plugin's folder filled in (whatever version is installed now)."""
    from . import plugin_info  # plugin_info imports this module
    return new_vault_bootstrap_prompt(
        repo_root / NEW_VAULT_PREFIX_FILE,
        repo_root / NEW_VAULT_SUFFIX_FILE,
        plugin_info.plugin_dir(),
        before_command=before_command,
        command_note=command_note,
        after_suffix=after_suffix,
    )
