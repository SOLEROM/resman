"""resman server entrypoint.

The Flask + SocketIO server is composed from the module classes.

Socket.IO runs in **threading** mode (the app-family standard — see
solBench/compatibleTest.md §2). Real WebSockets come from simple-websocket;
without it engine.io silently degrades to long-polling. eventlet is
deliberately absent: it is deprecated upstream, its import-time monkey-patch
infected every module in the process, and task dispatch now uses the portable
``socketio.start_background_task`` instead of ``eventlet.spawn``.
"""
from __future__ import annotations

import argparse
import atexit
import logging
import os
import socket
import sys
import time
from pathlib import Path

# Allow running as a script from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, render_template
from flask_socketio import SocketIO

from modules.config_manager import ConfigError, ConfigManager
from modules.event_bus import get_bus
from modules.mount_manager import MountManager
from modules.obsidian_push import ObsidianPush
from modules.routes import bp as api_bp
from modules.scheduler import Scheduler
from modules.session_manager import SessionManager
from modules.task_manager import TaskManager
from modules.tmux_manager import TmuxManager
from modules.vault_registry import VaultRegistry
from modules.websocket_handlers import attach_socketio
from modules.window_schedule import WindowSchedule
from modules.window_stats import WindowStats
from modules.window_sampler import WindowSampler
from modules import claude_usage
from modules.activity_log import ActivityLog, install_logging_bridge
from modules.window_state import WindowState

log = logging.getLogger("resman")

# Socket.IO async mode for the family: threading + simple-websocket.
ASYNC_MODE = "threading"

RESMAN_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = RESMAN_ROOT / "config"

# Fallback port when nothing else specifies one (no --port, no .port file, no
# `app.port` in resman.yaml).
DEFAULT_PORT = 5090
# Optional project-local default: a `.port` file at the repo root holding a
# single line with the port number. Lets an operator pin the listen port for
# both `run.sh` and the systemd service without editing YAML or the unit file.
PORT_FILE = RESMAN_ROOT / ".port"


def _read_port_file(path: Path = PORT_FILE) -> "int | None":
    """Return the port declared in the `.port` file, or None if unusable.

    The file is expected to hold a single line with the port number; blank
    lines are skipped and the first non-empty line is used. Any problem
    (missing file, unreadable, non-numeric, out of the 1–65535 range) is
    treated as "not set" and logged rather than raised — a malformed override
    must never stop the server from starting.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("ignoring .port file at %s — could not read it: %s", path, exc)
        return None
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not line:
        return None
    try:
        port = int(line)
    except ValueError:
        log.warning("ignoring .port file at %s — %r is not a number", path, line)
        return None
    if not (1 <= port <= 65535):
        log.warning("ignoring .port file at %s — %d is out of range 1–65535", path, port)
        return None
    return port


def _resolve_port(
    config: ConfigManager,
    cli_port: "int | None" = None,
    port_file: Path = PORT_FILE,
) -> int:
    """Resolve the effective listen port from all sources, most specific first.

    Precedence: explicit --port on the command line › the `.port` file ›
    `app.port` in resman.yaml › DEFAULT_PORT.
    """
    if cli_port is not None:
        return cli_port
    file_port = _read_port_file(port_file)
    if file_port is not None:
        return file_port
    return int(config.app.get("port", DEFAULT_PORT))


def _print_startup_report(report: dict) -> None:
    sys.stdout.write("\nresman starting...\n")
    for k, v in report.items():
        sys.stdout.write(f"  {k:9}: {v}\n")
    sys.stdout.flush()


def _discover_lan_ip() -> str | None:
    """Best-effort LAN IP discovery — opens a UDP socket to a TEST-NET address.

    The packet is never sent; the kernel just picks the outbound interface so
    we can read its address back. Returns None if no route is available.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 1))  # TEST-NET-1 (RFC 5737)
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def _terminal_line(webterm_state, session_manager) -> str:
    """Which terminal stack answered, and who may reach it."""
    if webterm_state is None:
        return ("ttyd (legacy, RESMAN_WEBTERM=0)" if session_manager.available
                else "ttyd MISSING (terminal sessions disabled)")
    config = webterm_state.config
    if config.allow_insecure_lan:
        gate = "ANY peer (RESMAN_WEBTERM_LAN=1)"
    elif config.trusted_networks:
        gate = "loopback + " + ",".join(config.trusted_networks)
    else:
        gate = "loopback only"
    return f"webterm (shared) — reachable from {gate}"


def build_app(
    config_dir: Path = CONFIG_DIR,
    *,
    async_mode: str = ASYNC_MODE,
    use_webterm: "bool | None" = None,
    public: bool = False,
    port: "int | None" = None,
) -> tuple[Flask, SocketIO, dict]:
    """Compose the Flask app + Socket.IO + module instances.

    ``port`` is the explicit --port override (None when unset); the effective
    port is resolved via :func:`_resolve_port` and exposed as ``ctx["port"]`` so
    the caller binds to the same value used for CORS and the startup report.

    Returns (app, socketio, ctx). ctx is the dict of module instances; tests
    use it directly without going through Flask.
    """
    bus = get_bus()
    bus.clear()

    config = ConfigManager(config_dir, bus)
    config.load()
    resolved_port = _resolve_port(config, port)

    tmux = TmuxManager(
        socket=config.app.get("tmux_socket", "resman"),
        prefix=config.app.get("tmux_prefix", "rsm-"),
    )

    vault_registry = VaultRegistry(config, bus)
    vault_registry.reload()

    mount_manager = MountManager(
        bus=bus,
        get_vaults=lambda: vault_registry.registered,
    )
    mount_manager.sync(vault_registry.registered)
    atexit.register(mount_manager.umount_all)

    window = WindowState(config_dir / "budget.json", bus)
    window.load()

    # The schedule pulls live session/weekly limits from claude.ai on sync,
    # using the operator's local OAuth creds (read-only, no token spend).
    window_schedule = WindowSchedule(config_dir / "window_schedule.json", bus,
                                     usage_provider=claude_usage.fetch_usage)
    window_schedule.load()

    session_manager = SessionManager(
        tmux=tmux,
        port_base=config.app.get("ttyd_port_base", 7680),
        port_max=config.app.get("ttyd_port_max", 7999),
        bind_host="0.0.0.0" if public else "127.0.0.1",
        emit=lambda name, payload: bus.emit(name, payload),
    )

    task_manager = TaskManager(
        log_path=config_dir / "tasks.jsonl",
        log_dir=config_dir / "task-logs",
        resman_root=RESMAN_ROOT,
        is_window_active=window.is_window_active,
        get_vault_path=lambda n: (vault_registry.get(n).path if vault_registry.get(n) else None),
        list_vault_names=vault_registry.all_names,
        bus=bus,
        usage_provider=claude_usage.fetch_usage,
    )
    replay_summary = task_manager.replay()

    obsidian_push = ObsidianPush(
        vault_iter=lambda: vault_registry.registered,
        get_task_states=lambda n: [
            t.state for t in task_manager._tasks.values() if t.vault == n
        ],
        has_session_for=lambda n: any(
            s.vault == n for s in session_manager.list()
        ),
    )
    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        obsidian_push=obsidian_push,
        is_window_active=window.is_window_active,
        bus=bus,
    )

    # cld20-style window automation: a durable usage-reading store + the
    # opener/collector sampler. Both gated by window_schedule config (default
    # OFF). The scheduler derives the cron jobs from the live schedule.
    window_stats = WindowStats(config_dir / "window_samples.jsonl", bus)
    window_sampler = WindowSampler(
        schedule=window_schedule,
        stats=window_stats,
        bus=bus,
        usage_fetch=claude_usage.fetch_usage,
        wakeup=claude_usage.wakeup,
    )
    scheduler.set_window_sampler(window_sampler)

    # Volatile activity log (footer "Log" window). Created last so it captures
    # live operations, not startup replay churn; lives in /tmp and is deleted
    # on exit. install_logging_bridge mirrors WARNING+ from resman loggers.
    activity = ActivityLog(Path("/tmp/resman") / f"activity-{os.getpid()}.log", bus)
    install_logging_bridge(activity)
    atexit.register(activity.close)

    template_dir = Path(__file__).resolve().parent / "templates"
    static_dir = Path(__file__).resolve().parent / "static"
    app = Flask(
        __name__,
        template_folder=str(template_dir),
        static_folder=str(static_dir),
        static_url_path="/static",
    )

    cors_origins = "*" if public else [f"http://127.0.0.1:{resolved_port}"]
    socketio = SocketIO(app, cors_allowed_origins=cors_origins, async_mode=async_mode)

    # Each task runs in its own background worker so the request handler that
    # created it returns immediately while the streaming runner pushes
    # task_log_appended events on the bus. start_background_task is the
    # async-mode-agnostic primitive: a real thread under threading mode, and
    # whatever the async mode provides otherwise.
    task_manager.set_executor(
        lambda task: socketio.start_background_task(task_manager._execute, task)
    )

    ctx = {
        "config": config,
        "tmux": tmux,
        "vault_registry": vault_registry,
        "mount_manager": mount_manager,
        "window": window,
        "window_schedule": window_schedule,
        "window_stats": window_stats,
        "window_sampler": window_sampler,
        "session_manager": session_manager,
        "webterm": None,       # set below when the shared terminal is enabled
        "task_manager": task_manager,
        "obsidian_push": obsidian_push,
        "scheduler": scheduler,
        "activity": activity,
        "bus": bus,
        "socketio": socketio,
        "resman_root": RESMAN_ROOT,
        "port": resolved_port,
    }
    app.config["RESMAN"] = ctx

    @app.get("/")
    def index():
        # The footer's Claude window/week meters are rendered by remdev's
        # embeddable status-bar service (iframe, no native fallback) and the
        # Windows-management UI lives in remdev's Claude tab. remdev_url in
        # resman.yaml's app section overrides discovery; when unset the
        # browser derives it from its own hostname + port 6005 (a server-side
        # 127.0.0.1 default would point remote viewers at *their* machine).
        remdev_url = (config.app.get("remdev_url") or "").rstrip("/")
        return render_template("index.html",
                               use_webterm=app.config.get("WEBTERM_ENABLED", False),
                               remdev_url=remdev_url)

    app.register_blueprint(api_bp)
    attach_socketio(socketio, bus)

    # Terminal stack: the shared webterm library is the default;
    # RESMAN_WEBTERM=0 reverts to the legacy ttyd + iframe stack. Both drive
    # the same tmux socket and prefix, so live sessions survive a flip in
    # either direction.
    if use_webterm is None:
        use_webterm = os.environ.get("RESMAN_WEBTERM", "1") == "1"
    if use_webterm:
        try:
            from modules.webterm_integration import init_webterm
            ctx["webterm"] = init_webterm(app, socketio, ctx)
        except Exception:
            # A missing or broken library must not take resman down — the
            # legacy terminal is still there to fall back on.
            log.exception(
                "webterm unavailable — falling back to the legacy ttyd terminal")
            use_webterm = False
    app.config["WEBTERM_ENABLED"] = use_webterm
    if ctx["webterm"] is not None:
        atexit.register(ctx["webterm"].pty.cleanup_all)

    if public:
        lan_ip = _discover_lan_ip()
        server_line = f"http://0.0.0.0:{resolved_port}"
        if lan_ip:
            server_line += f"  (LAN: http://{lan_ip}:{resolved_port})"
        server_line += "  [PUBLIC — exposed on local network]"
    else:
        server_line = f"http://{config.app.get('host', '127.0.0.1')}:{resolved_port}"
    active_mounts = mount_manager.status()
    mounts_with = sum(1 for v in config.vaults if v.get("mount"))
    if mounts_with:
        mounts_line = f"{len(active_mounts)}/{mounts_with} bound"
        if len(active_mounts) < mounts_with:
            mounts_line += " (some failed — run as root or add sudoers rule)"
    else:
        mounts_line = "none configured"
    report = {
        "config": f"OK ({len(config.vaults)} vaults loaded)",
        "mounts": mounts_line,
        "tmux": "OK" if tmux.is_installed() else "MISSING",
        "terminal": _terminal_line(ctx["webterm"], session_manager),
        "scheduler": f"OK ({len(config.cron_tasks)} cron tasks)",
        "tasks": f"OK (replayed {replay_summary['lines']} events, "
                 f"{replay_summary['bad_lines']} bad lines, {replay_summary['tasks']} tasks)",
        "server": server_line,
    }
    _print_startup_report(report)

    return app, socketio, ctx


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="resman — research vault manager")
    parser.add_argument("--config-dir", type=str, default=str(CONFIG_DIR))
    parser.add_argument("--port", type=int, default=None,
                        help="Listen port (overrides the .port file and resman.yaml).")
    parser.add_argument("--host", type=str, default=None,
                        help="Interface to bind (overrides resman.yaml). Use 0.0.0.0 for LAN.")
    parser.add_argument("--public", action="store_true",
                        help="Bind to 0.0.0.0 and expose on the local network. "
                             "Disables CORS origin restriction; ttyd terminals are reachable from LAN.")
    parser.add_argument("--no-scheduler", action="store_true")
    args = parser.parse_args()

    try:
        app, socketio, ctx = build_app(
            Path(args.config_dir), public=args.public, port=args.port,
        )
    except ConfigError as exc:
        sys.stderr.write(f"\nFATAL: {exc}\n")
        return 2
    if not ctx["tmux"].is_installed():
        sys.stderr.write("\nFATAL: tmux is not installed.\n")
        return 2

    if not args.no_scheduler:
        ctx["scheduler"].start()

    config = ctx["config"]
    if args.host is not None:
        host = args.host
    elif args.public:
        host = "0.0.0.0"
    else:
        host = config.app.get("host", "127.0.0.1")
    socketio.run(app, host=host, port=ctx["port"], allow_unsafe_werkzeug=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
