"""webterm integration — the shared native terminal, in place of ttyd.

Feature flag: ``RESMAN_WEBTERM=0`` reverts to the legacy ttyd + iframe stack
(see ``session_manager.py``). Both stacks drive the *same* tmux socket and
session prefix, so live terminals survive flipping the flag either way.

What moves where: which vault a session may open in and what is delivered into
the Claude prompt is :mod:`session_plan`, shared by both stacks. This module
turns a plan into a :class:`~webterm.SpawnSpec`; the library owns everything
after that (PTY, transport, xterm rendering, the network gate).

The delay this replaces was the riskiest of the family. resman delivers its
bootstrap block and its attend prompt with a **trailing Enter** — they are
messages for Claude, not text left in a box. Under the old fixed delay, a
Claude REPL that was slower than the timer meant that text was typed and
*submitted* into the bash shell that briefly precedes it. The library polls
the pane for the REPL's input box instead, so the paste only happens once
there is a REPL to receive it, and a timeout is reported rather than fired
blindly at whatever is on screen.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import webterm
from webterm import ReadinessProbe, SpawnError, SpawnSpec, TerminalConfig

from .session_plan import (
    SessionPlan, SessionPlanError, build_attend_plan, build_session_plan,
)

log = logging.getLogger(__name__)

# Default terminal trust: loopback (always accepted by the library) plus the
# tailnet, which is resman's actual perimeter under --public. The LAN the box
# also sits on is deliberately excluded — override per host with
# RESMAN_WEBTERM_TRUSTED_NETS="cidr,cidr" (empty value = loopback-only), or
# RESMAN_WEBTERM_LAN=1 to accept every peer.
DEFAULT_TRUSTED_NETS = webterm.TAILSCALE_CGNAT

# Matches the legacy stack's terminal look so flipping the flag isn't visible.
TERMINAL_FONT_FAMILY = "JetBrains Mono,DejaVu Sans Mono,Symbola,monospace"
TERMINAL_FONT_SIZE = 13

# Claude Code is ready for input once it has drawn its input box: a long
# horizontal rule followed by the prompt character. Verified against v2.1.235
# in tmux (ready at ~1.3s). Neither a shell prompt nor the still-loading
# banner can match it — which is the whole point here, because what follows
# the paste is an Enter.
CLAUDE_REPL_READY = r"─{20,}\n❯"
PROMPT_READY_TIMEOUT = 25.0

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")
_THEMES_FILE = Path(__file__).resolve().parent.parent / "terminal-themes.json"


def safe_tmux_name(vault: str, session_type: str) -> str:
    """`<vault>-<type>`, reduced to what tmux (and SpawnSpec) accepts.

    The library adds its own prefix and a ``-2``/``-3`` disambiguator, so this
    only has to produce a valid stem.
    """
    stem = _UNSAFE_NAME.sub("-", vault or "").strip("-") or "vault"
    return f"{stem}-{_UNSAFE_NAME.sub('-', session_type).strip('-')}"


def trusted_networks() -> tuple:
    raw = os.environ.get("RESMAN_WEBTERM_TRUSTED_NETS", DEFAULT_TRUSTED_NETS)
    return tuple(net.strip() for net in raw.split(",") if net.strip())


def _load_themes() -> dict:
    """Reuse resman's own palettes so the terminal tracks the app theme."""
    try:
        raw = json.loads(_THEMES_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items()
                if isinstance(v, dict) and not k.startswith("_")}
    except (OSError, ValueError) as exc:
        log.warning("terminal-themes.json unusable (%s); library defaults apply", exc)
        return {}


def spec_for(plan: SessionPlan) -> SpawnSpec:
    """Turn a resolved plan into the spec the library spawns."""
    argv = None
    if plan.session_type == "claude":
        # Same shape the legacy stack used: the configured claude_cmd is a
        # shell-style string, so it is handed to `sh -c` rather than being
        # split into argv and losing its shell semantics. cwd is already the
        # vault, so the old `cd <vault> &&` prefix is gone.
        argv = ["sh", "-c", plan.claude_cmd]
    # A slash command and a bootstrap block are both *messages for Claude*, so
    # both go in as a bracketed paste followed by Enter. Delivering the slash
    # command this way (rather than as typed keys) also keeps shell quoting
    # out of a path that never touches a shell.
    payload = plan.initial_text or plan.initial_command
    return SpawnSpec(
        tmux_name=safe_tmux_name(plan.vault, plan.session_type),
        cwd=plan.vault_path,
        # command stays None: tmux starts the default shell and claude is
        # *typed into* it, so quitting the REPL leaves the terminal behind.
        initial_command=argv,
        initial_paste=payload,
        submit_after_paste=bool(payload),
        paste_when_ready=(
            ReadinessProbe(pattern=CLAUDE_REPL_READY,
                           timeout_seconds=PROMPT_READY_TIMEOUT)
            if payload else None),
        label=f"{plan.vault} · {plan.session_type}",
        metadata={"vault": plan.vault, "session_type": plan.session_type,
                  "path": plan.vault_path,
                  "seeded": bool(payload)},
    )


def make_spawn_resolver(context: dict):
    """(payload) -> SpawnSpec. The whole of resman's terminal policy.

    ``{"attend_task": "<id>"}`` re-runs a finished task's prompt in a live
    REPL; anything else is an ordinary session request. Both resolve entirely
    server-side — the client names a vault or a task, never a path, a command
    line or the prompt text.
    """

    def resolver(payload: dict) -> SpawnSpec:
        try:
            task_id = payload.get("attend_task")
            if task_id:
                return spec_for(build_attend_plan(context, str(task_id)))
            return spec_for(build_session_plan(context, payload))
        except SessionPlanError as exc:
            raise SpawnError(str(exc)) from exc

    return resolver


def init_webterm(app, socketio, context: dict):
    insecure_lan = os.environ.get("RESMAN_WEBTERM_LAN", "0") == "1"
    nets = trusted_networks()
    app_cfg = context["config"].app
    config = TerminalConfig(
        tmux_socket=str(app_cfg.get("tmux_socket", "resman")),
        tmux_prefix=str(app_cfg.get("tmux_prefix", "rsm-")),
        font_family=TERMINAL_FONT_FAMILY,
        font_size=TERMINAL_FONT_SIZE,
        themes=_load_themes(),
        # --public binds the app to the LAN but does NOT hand out shells to it:
        # that stays an explicit, separately named opt-in.
        allow_insecure_lan=insecure_lan,
        trusted_networks=nets,
    )
    state = webterm.init_app(app, socketio, config=config,
                             spawn_resolver=make_spawn_resolver(context))
    if insecure_lan:
        gate = "insecure-lan (every peer)"
    elif nets:
        gate = "loopback+" + ",".join(nets)
    else:
        gate = "loopback-only"
    log.info("webterm enabled (socket=%s prefix=%s auth=%s)",
             config.tmux_socket, config.tmux_prefix, gate)
    return state
