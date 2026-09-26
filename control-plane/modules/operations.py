"""The operation registry — the one place that knows what a task can run.

One :class:`Operation` per operation key: its provider (which skill source it
belongs to), its kind (a ``claude -p`` prompt, or an argv), the params the
trigger form shows and the rules they are validated with, and the builder
that turns validated params into the prompt or the argv. ``task_manager``
validates and builds through it, ``routes`` serves it as ``GET /api/operations``,
``config_manager`` checks ``schedule.yaml`` against it, and the SPA renders
the picker from it. Nothing else may spell an operation key.

Providers (docs/design/17-skills.md):

  obsidian  the claude-obsidian plugin, installed per user     keys ``wiki-*``
  resman    our own skills in skills/, loaded per run     keys ``rs-*``
  adhoc     run-prompt / run-shell, the operator typed it       keys ``run-*``

The provider is an attribute here, never a prefix test elsewhere; the prefix
is a courtesy for whoever reads tasks.jsonl. Adding a resman skill is one
folder under skills/skills/ plus one entry below.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from . import new_vault, plugin_commands, resman_skills

PROVIDERS = {
    "obsidian": "claude-obsidian",
    "resman": "resman skills",
    "adhoc": "ad hoc",
}
PROVIDER_PREFIX = {"obsidian": "wiki-", "resman": "rs-", "adhoc": "run-"}
PARAM_TYPES = ("url", "text", "checkbox", "argv")
URL_INGEST_PREFIX_FILE = "prompts/urlInjestPrefix.md"
PRINTABLE_RE = re.compile(r"^[\x20-\x7E\t\n\r]*$")


@dataclass(frozen=True)
class Param:
    key: str
    type: str            # url | text | checkbox | argv
    label: str
    required: bool = False
    max_len: int = 200   # text: printable ASCII, at most this long
    placeholder: str = ""

    def public(self) -> dict:
        return {"key": self.key, "type": self.type, "label": self.label,
                "required": self.required, "max_len": self.max_len,
                "placeholder": self.placeholder}


@dataclass(frozen=True)
class RunContext:
    """What a builder may use besides the task's params."""
    resman_root: Path
    vault_path: str
    claude_exe: str
    settings: dict = field(default_factory=dict)   # skills.<skill> from the yaml
    # The reader of *any* skill's stored settings (ConfigManager.skill_settings),
    # for an entry that renders another skill's line than its own: the
    # wiki-bootstrap re-run carries vault-brief's (docs/vaultBrief-plan.md).
    skill_settings: Optional[Callable[[str], dict]] = None

    @property
    def plugin_dir_args(self) -> list[str]:
        return resman_skills.plugin_dir_args(self.resman_root)


Builder = Callable[[dict, RunContext], object]


@dataclass(frozen=True)
class Operation:
    key: str
    label: str
    group: str               # picker group: Research | Wiki | Custom
    provider: str            # obsidian | resman | adhoc
    kind: str                # prompt (claude -p, attendable) | shell (argv)
    skill: str = ""          # the provider skill/command it invokes
    params: tuple = ()
    desc: str = ""
    note: str = ""
    icon: str = "codicon-circle-small"
    confirm: str = ""
    remote: bool = False     # offered by tools/remoteAgent.sh
    build_prompt: Optional[Builder] = None
    build_argv: Optional[Builder] = None

    def __post_init__(self) -> None:
        if self.provider not in PROVIDERS:
            raise ValueError(f"{self.key}: unknown provider {self.provider!r}")
        if not self.key.startswith(PROVIDER_PREFIX[self.provider]):
            raise ValueError(f"{self.key}: a {self.provider} operation must start with "
                             f"{PROVIDER_PREFIX[self.provider]!r}")
        if self.kind == "prompt" and (self.build_prompt is None or self.build_argv is not None):
            raise ValueError(f"{self.key}: a prompt operation needs build_prompt only")
        if self.kind == "shell" and (self.build_argv is None or self.build_prompt is not None):
            raise ValueError(f"{self.key}: a shell operation needs build_argv only")
        if self.kind not in ("prompt", "shell"):
            raise ValueError(f"{self.key}: kind must be prompt or shell")
        for p in self.params:
            if p.type not in PARAM_TYPES:
                raise ValueError(f"{self.key}: param {p.key} has unknown type {p.type!r}")

    @property
    def attendable(self) -> bool:
        return self.kind == "prompt"


def validate(op: Operation, params: Optional[dict]) -> dict:
    """The task's params checked against ``op.params``; returns a normalized
    copy (checkboxes → bool, optional text → ""). Unknown keys are kept."""
    out = dict(params or {})
    for p in op.params:
        raw = out.get(p.key)
        if p.type == "url":
            if not isinstance(raw, str) or not raw:
                raise ValueError(f"{op.key}: '{p.key}' required")
            if urlparse(raw).scheme not in ("http", "https"):
                raise ValueError(f"{op.key}: '{p.key}' must be http or https")
        elif p.type == "text":
            if raw is None:
                raw = ""
            if not isinstance(raw, str):
                raise ValueError(f"{op.key}: '{p.key}' must be a string")
            if p.required and not raw:
                raise ValueError(f"{op.key}: '{p.key}' required")
            if len(raw) > p.max_len or not PRINTABLE_RE.match(raw):
                raise ValueError(f"{op.key}: {p.key} must be ≤{p.max_len} chars printable ASCII")
            out[p.key] = raw
        elif p.type == "checkbox":
            out[p.key] = bool(raw)
        elif p.type == "argv":
            if not isinstance(raw, list) or not raw:
                raise ValueError(f"{op.key}: '{p.key}' must be a non-empty list")
            if not all(isinstance(x, str) for x in raw):
                raise ValueError(f"{op.key}: {p.key} must all be strings")
    return out


# ----- builders for the claude-obsidian and ad-hoc operations -----

def _ingest_argv(params: dict, ctx: RunContext, prefix: bool) -> list[str]:
    cmd = [str(ctx.resman_root / "tools" / "ingest.sh"), ctx.vault_path, params["url"]]
    if prefix:
        cmd += ["--prefix", str(ctx.resman_root / URL_INGEST_PREFIX_FILE)]
    if params.get("update_canvas"):
        cmd.append("--can")
    return cmd


_URL = Param("url", "url", "URL", required=True, placeholder="https://…")
_CANVAS = Param("update_canvas", "checkbox",
                "Update canvas after ingest (wiki/canvases/main.canvas)")

_ENTRIES = (
    # Research
    Operation(
        key="wiki-ingest", label="Ingest a URL", group="Research", provider="obsidian",
        kind="shell", skill="wiki-ingest", params=(_URL, _CANVAS),
        desc="Fetch a URL into the wiki.", icon="codicon-cloud-download", remote=True,
        build_argv=lambda p, c: _ingest_argv(p, c, prefix=False),
    ),
    Operation(
        key="wiki-ingest-prefix", label="Ingest URL + prefix", group="Research",
        provider="obsidian", kind="shell", skill="wiki-ingest", params=(_URL, _CANVAS),
        desc="Ingest a URL, re-framed constructively.",
        note="Runs the URL ingest under prompts/urlInjestPrefix.md — extracts "
             "technological substance from sources that discuss harmful applications "
             "and re-frames it for constructive use.",
        icon="codicon-arrow-swap", remote=True,
        build_argv=lambda p, c: _ingest_argv(p, c, prefix=True),
    ),
    Operation(
        key="wiki-autoresearch", label="Autoresearch a topic", group="Research",
        provider="obsidian", kind="prompt", skill="autoresearch",
        params=(Param("topic", "text", "Topic", required=True, placeholder="topic to research"),),
        desc="Research a topic into new pages.", icon="codicon-search", remote=True,
        build_prompt=lambda p, c: plugin_commands.autoresearch_prompt(p.get("topic", "")),
    ),
    # Wiki
    Operation(
        key="wiki-lint", label="Lint wiki", group="Wiki", provider="obsidian",
        kind="prompt", skill="wiki-lint", desc="Find orphans, dead links and gaps.",
        icon="codicon-checklist", remote=True,
        build_prompt=lambda p, c: plugin_commands.WIKI_LINT,
    ),
    Operation(
        key="wiki-update-hot-cache", label="Update hot cache", group="Wiki",
        provider="obsidian", kind="prompt", skill="update-hot-cache",
        desc="Refresh the hot-cache index.", icon="codicon-sync", remote=True,
        build_prompt=lambda p, c: plugin_commands.WIKI_UPDATE_HOT_CACHE,
    ),
    Operation(
        key="wiki-bootstrap", label="Re-run wiki bootstrap", group="Wiki",
        provider="obsidian", kind="prompt", skill="wiki",
        desc="Re-run the wiki bootstrap.",
        note="Non-interactive re-run only; new vaults must use the wizard. Normalizes "
             "the brief first (/resman:vault-brief with the interview off, from what "
             "the vault already says), then the plugin's scaffold takes Purpose, Mode "
             "and Owner from wiki/meta/brief.md.",
        icon="codicon-rocket", remote=True,
        build_prompt=lambda p, c: new_vault.bootstrap_message(
            c.resman_root, mode="deep", interview="none", skill_settings=c.skill_settings),
    ),
    Operation(
        key="wiki-hint", label="Generate hint", group="Wiki", provider="obsidian",
        kind="prompt", skill="wiki-query", desc="Write the vault card's label & tags.",
        note="Inspects the wiki and writes wiki/hint.json — the label, summary and "
             "tags shown on this vault's landing-page card.",
        icon="codicon-info",
        build_prompt=lambda p, c: plugin_commands.WIKI_HINT,
    ),
    Operation(
        key="wiki-canvas", label="Update canvas", group="Wiki", provider="obsidian",
        kind="prompt", skill="canvas",
        params=(Param("description", "text", "Description (optional)",
                      placeholder="leave blank to use plugin defaults"),),
        desc="Re-organize the visual canvas.",
        note="Runs /claude-obsidian:canvas. Description is optional — leave it blank "
             "and the plugin uses its own defaults.",
        icon="codicon-layout", remote=True,
        build_prompt=lambda p, c: plugin_commands.canvas_prompt(p.get("description", "")),
    ),
    # resman's own skills (skills/)
    Operation(
        key="rs-deep-list", label="deepList: research values", group="Research",
        provider="resman", kind="prompt", skill="deep-list",
        params=(Param("focus", "text", "Focus for this run (optional)",
                      placeholder="extra objective hint; overrides the stored focus"),),
        desc="Rank the research values worth a deep-research run.",
        note="Writes wiki/meta/deep-list.md: retires values the wiki has filled, adds "
             "new gaps, re-scores the rest; each value carries its autoresearch line. "
             "Settings: Skills → resman skills → deep-list.",
        icon="codicon-list-ordered", remote=True,
        build_prompt=lambda p, c: resman_skills.skill_prompt(
            "deep-list", resman_skills.args_for(c.resman_root, "deep-list", c.settings, p)),
    ),
    Operation(
        key="rs-vault-brief", label="vaultBrief: define the vault", group="Wiki",
        provider="resman", kind="prompt", skill="vault-brief",
        params=(Param("seed", "text", "Seed (optional)",
                      placeholder="what this vault is for, in a line"),),
        desc="Interview, then write wiki/meta/brief.md and the vault card's hint.",
        note="Writes wiki/meta/brief.md (purpose, mode, scope, domains, questions, "
             "sources, cadence) after a grilling interview at the depth set in "
             "Skills → resman skills → vault-brief, and the sidecar wiki/hint.json "
             "(label, summary, tags, source \"brief\") that the Vaults page card reads; "
             "a hand-written hint is left alone. Attend it to answer the questions "
             "yourself. The New Vault form's Deep interview tab runs the same skill "
             "before the plugin scaffolds a new vault.",
        icon="codicon-comment-discussion", remote=True,
        build_prompt=lambda p, c: new_vault.brief_task_prompt(
            c.resman_root, c.settings, p.get("seed", "")),
    ),
    # Custom (ad hoc)
    Operation(
        key="run-prompt", label="Run a Claude prompt", group="Custom", provider="adhoc",
        kind="prompt",
        params=(Param("prompt", "text", "Prompt", required=True,
                      placeholder="/your-command or free text"),),
        desc="Run a Claude prompt or command.", icon="codicon-zap",
        build_prompt=lambda p, c: p.get("prompt") or "",
    ),
    Operation(
        key="run-shell", label="Run shell command", group="Custom", provider="adhoc",
        kind="shell",
        params=(Param("cmd_parts", "argv", "Command (one argument per line)",
                      required=True, placeholder="echo\nhello"),),
        desc="Run a shell command in the vault.", icon="codicon-terminal",
        confirm="run-shell executes an arbitrary command in the vault directory. Proceed?",
        build_argv=lambda p, c: list(p["cmd_parts"]),
    ),
)

REGISTRY: dict[str, Operation] = {op.key: op for op in _ENTRIES}


def get(key: str) -> Optional[Operation]:
    return REGISTRY.get(key)


def for_provider(provider: str) -> list[Operation]:
    return [op for op in REGISTRY.values() if op.provider == provider]


def provider_of(key: str) -> str:
    """The provider of an operation key, ``"unknown"`` for one the registry no
    longer has (old tasks keep rendering)."""
    op = REGISTRY.get(key)
    return op.provider if op else "unknown"


def providers() -> list[dict]:
    return [{"id": pid, "label": label} for pid, label in PROVIDERS.items()]


def public(op: Operation) -> dict:
    """The entry without its builders: what ``GET /api/operations`` serves."""
    return {
        "key": op.key, "label": op.label, "group": op.group, "provider": op.provider,
        "kind": op.kind, "attendable": op.attendable, "skill": op.skill,
        "params": [p.public() for p in op.params], "desc": op.desc, "note": op.note,
        "icon": op.icon, "confirm": op.confirm, "remote": op.remote,
    }
