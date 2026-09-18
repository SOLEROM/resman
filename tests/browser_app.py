"""The real resman app on an ephemeral port, for the Playwright suites
(test_wiki_renderer.py, test_wiki_marks_browser.py).

Not a test module: helpers only. Skips cleanly when Playwright or a cached
headless Chromium shell is unavailable.
"""
from __future__ import annotations

import glob
import os
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest


def chromium_shell() -> str:
    shells = sorted(glob.glob(os.path.expanduser(
        "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")))
    if not shells:
        pytest.skip("no headless chromium shell cached", allow_module_level=True)
    return shells[-1]


@contextmanager
def serve(base: Path, pages: dict[str, str]):
    """Yield (url, vault) for one registered vault `alpha` holding `pages`
    (vault-relative path → text). A developer's ~/.resman.yaml is kept out."""
    from werkzeug.serving import make_server
    from modules import config_manager
    from server import build_app

    vault = base / "alpha"
    (vault / ".obsidian").mkdir(parents=True)
    for rel, text in pages.items():
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text(text)
    cfg = base / "config"
    cfg.mkdir()
    (cfg / "system.yaml").write_text(
        f"app:\n  host: 127.0.0.1\n  port: 5090\nvaults:\n  - name: alpha\n    path: {vault}\n")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config_manager, "_default_user_override_path",
                   lambda: base / ".no-such-resman.yaml")
        app, _sio, _ctx = build_app(cfg, async_mode="threading", use_webterm=False)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}", vault
    finally:
        srv.shutdown()
