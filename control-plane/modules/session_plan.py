"""SessionPlan — what a terminal session will be, decided before it is spawned.

Both terminal stacks go through :func:`build_session_plan`: the ttyd route in
``routes.py`` and the webterm spawn resolver in ``webterm_integration.py``. The
rules about which vault a session may open in, and what is delivered into the
Claude prompt once it is up, therefore live in exactly one place.

Nothing here takes a filesystem path or a command line from a request body.
The caller names a registered vault and a session type; resman decides what
actually runs and where.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass

from . import new_vault, resman_skills

MAX_INITIAL_COMMAND = 200
SESSION_TYPES = ("claude", "shell")


class SessionPlanError(ValueError):
    """A session request that cannot be honoured, with its HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class SessionPlan:
    vault: str
    vault_path: str
    session_type: str
    claude_cmd: str = "claude"
    # Typed into the Claude prompt once it is ready (a slash command).
    initial_command: "str | None" = None
    # Pasted into the Claude prompt as one block once it is ready. Both are
    # *submitted* — they are messages for Claude, not text left for a human.
    initial_text: "str | None" = None


def _claude_cmd(context: dict) -> str:
    """The configured ``claude_cmd`` plus ``--plugin-dir <root>/skills``
    when the repo has that folder (docs/design/17-skills.md, D3), so
    ``/resman:<skill>`` works in every session resman opens. The flag is
    appended shell-quoted because ``claude_cmd`` is a shell-style string."""
    cmd = str(context["config"].app.get("claude_cmd", "claude"))
    root = context.get("resman_root")
    suffix = resman_skills.plugin_dir_suffix(root) if root else ""
    return f"{cmd} {suffix}" if suffix else cmd


def build_session_plan(context: dict, body: dict) -> SessionPlan:
    """Validate a session request and resolve everything it refers to."""
    vault_name = body.get("vault")
    session_type = body.get("type") or body.get("session_type")
    if session_type == "bash":
        session_type = "shell"
    vault = context["vault_registry"].get(vault_name) if vault_name else None
    if not vault:
        raise SessionPlanError("unknown vault")
    if session_type not in SESSION_TYPES:
        raise SessionPlanError("type must be 'claude' or 'shell'")

    plan = SessionPlan(
        vault=vault.name, vault_path=vault.path, session_type=session_type,
        claude_cmd=_claude_cmd(context))

    # Optional: type a slash command into the Claude prompt once it's ready.
    # Used by the new-vault wizard to send /claude-obsidian:wiki so the user
    # answers prompts inside the terminal tab instead of running the command
    # blindly via `claude -p`.
    initial_command = body.get("initial_command")
    if initial_command is not None:
        if (not isinstance(initial_command, str)
                or len(initial_command) > MAX_INITIAL_COMMAND):
            raise SessionPlanError(
                f"initial_command must be a string ≤{MAX_INITIAL_COMMAND} chars")
        if session_type != "claude":
            raise SessionPlanError("initial_command requires type='claude'")
        plan.initial_command = initial_command

    # Optional: the new-vault bootstrap message, pasted into the Claude prompt
    # as a single block (modules/new_vault.py). Alone, `bootstrap_new_vault`
    # is the Basic tab: prefix, /claude-obsidian:wiki, suffix. With a `brief`
    # and/or an `interview` depth it is the Deep interview tab: the brief
    # between markers, /resman:vault-brief at that depth, then the scaffold
    # told to take the answers from wiki/meta/brief.md, then the stages the
    # form checked — `deep_list`, `autoresearch` with `autoresearch_top` —
    # after the suffix (docs/vaultBrief-plan.md). Absent stage fields mean
    # no stage; a stage on the Basic message is refused.
    brief = body.get("brief")
    interview = body.get("interview")
    stage_fields = {k: body.get(k) for k in ("deep_list", "autoresearch", "autoresearch_top")}
    deep_given = brief is not None or interview is not None
    if ((deep_given or any(v is not None for v in stage_fields.values()))
            and not body.get("bootstrap_new_vault")):
        raise SessionPlanError(
            "brief, interview, deep_list and autoresearch require bootstrap_new_vault")
    if body.get("bootstrap_new_vault"):
        if session_type != "claude":
            raise SessionPlanError("bootstrap_new_vault requires type='claude'")
        if plan.initial_command:
            raise SessionPlanError(
                "bootstrap_new_vault and initial_command are mutually exclusive")
        root = context["resman_root"]
        try:
            stages = new_vault.check_stages(**stage_fields)
            if not deep_given:
                plan.initial_text = new_vault.bootstrap_message(root, mode="basic", stages=stages)
            else:
                depth = new_vault.check_interview(
                    "short" if interview is None else interview,
                    allowed=new_vault.FORM_INTERVIEWS)
                plan.initial_text = new_vault.bootstrap_message(
                    root, mode="deep", brief=brief, interview=depth,
                    skill_settings=context["config"].skill_settings, stages=stages)
        except new_vault.BriefError as exc:
            raise SessionPlanError(str(exc)) from exc
    return plan


def build_attend_plan(context: dict, task_id: str) -> SessionPlan:
    """Re-run a task's Claude prompt in an interactive REPL (routes.attend_task).

    The original task ran via ``claude -p`` and so could not accept input; this
    rebuilds the same prompt and delivers it into a live REPL instead.
    """
    task = context["task_manager"].get(task_id)
    if not task:
        raise SessionPlanError("task not found", 404)
    if task.vault == "ALL":
        raise SessionPlanError("cannot attend a parent ALL-vault task")
    prompt = context["task_manager"].build_attend_prompt(task)
    if not prompt:
        raise SessionPlanError(
            f"operation {task.operation!r} is not attendable "
            "(no Claude prompt to re-run)")
    vault = context["vault_registry"].get(task.vault)
    if not vault:
        raise SessionPlanError(
            f"vault {task.vault!r} is no longer registered")
    return SessionPlan(
        vault=vault.name, vault_path=vault.path, session_type="claude",
        claude_cmd=_claude_cmd(context),
        initial_text=prompt)
