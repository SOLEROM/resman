"""resman server entrypoint.

eventlet.monkey_patch() is called as the first step, before any other imports
that might pull in stdlib socket / select / threading state. After that the
Flask + SocketIO server is composed from the module classes.
"""
from __future__ import annotations

# eventlet must monkey-patch before anything else
try:
    import eventlet
    eventlet.monkey_patch()
    EVENTLET_OK = True
except Exception:
    EVENTLET_OK = False

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
from modules import claude_usage
from modules.activity_log import ActivityLog, install_logging_bridge
from modules.window_state import WindowState

log = logging.getLogger("resman")

RESMAN_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = RESMAN_ROOT / "config"


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


def build_app(
    config_dir: Path = CONFIG_DIR,
    *,
    async_mode: str = "eventlet",
    public: bool = False,
) -> tuple[Flask, SocketIO, dict]:
    """Compose the Flask app + Socket.IO + module instances.

    Returns (app, socketio, ctx). ctx is the dict of module instances; tests
    use it directly without going through Flask.
    """
    bus = get_bus()
    bus.clear()

    config = ConfigManager(config_dir, bus)
    config.load()

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
    )
    replay_summary = task_manager.replay()

    # When running under eventlet, spawn each task in its own greenlet so the
    # request handler that created the task returns immediately while the
    # streaming runner pushes task_log_appended events on the bus.
    if EVENTLET_OK:
        task_manager.set_executor(
            lambda task: eventlet.spawn(task_manager._execute, task)
        )

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

    cors_origins = "*" if public else [f"http://127.0.0.1:{config.app.get('port', 5090)}"]
    socketio = SocketIO(app, cors_allowed_origins=cors_origins, async_mode=async_mode)

    ctx = {
        "config": config,
        "tmux": tmux,
        "vault_registry": vault_registry,
        "mount_manager": mount_manager,
        "window": window,
        "window_schedule": window_schedule,
        "session_manager": session_manager,
        "task_manager": task_manager,
        "obsidian_push": obsidian_push,
        "scheduler": scheduler,
        "activity": activity,
        "bus": bus,
        "socketio": socketio,
        "resman_root": RESMAN_ROOT,
    }
    app.config["RESMAN"] = ctx

    @app.get("/")
    def index():
        return render_template("index.html")

    app.register_blueprint(api_bp)
    attach_socketio(socketio, bus)

    port = config.app.get("port", 5090)
    if public:
        lan_ip = _discover_lan_ip()
        server_line = f"http://0.0.0.0:{port}"
        if lan_ip:
            server_line += f"  (LAN: http://{lan_ip}:{port})"
        server_line += "  [PUBLIC — exposed on local network]"
    else:
        server_line = f"http://{config.app.get('host', '127.0.0.1')}:{port}"
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
        "ttyd": "OK" if session_manager.available else "MISSING (terminal sessions disabled)",
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
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", type=str, default=None,
                        help="Interface to bind (overrides resman.yaml). Use 0.0.0.0 for LAN.")
    parser.add_argument("--public", action="store_true",
                        help="Bind to 0.0.0.0 and expose on the local network. "
                             "Disables CORS origin restriction; ttyd terminals are reachable from LAN.")
    parser.add_argument("--no-scheduler", action="store_true")
    args = parser.parse_args()

    try:
        app, socketio, ctx = build_app(Path(args.config_dir), public=args.public)
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
    port = args.port or config.app.get("port", 5090)
    socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
