"""Integration tests for tools/remoteAgent.sh.

The script is the CLI bridge that the openClaw phone agent calls over SSH
to drive the control plane. The tests stand up a tiny HTTP server on a
free port, run the bash script under subprocess, and assert:
  - request method/path/body shape per operation,
  - CSRF header propagation,
  - exit codes for happy + error paths,
  - --json output is parseable and carries the expected fields,
  - --wait polls until terminal state and reports it.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "remoteAgent.sh"


class _Handler(BaseHTTPRequestHandler):
    """Captures every request on the shared `requests` list, then replies
    from a small in-memory task store. Behaviour is set per-server via the
    `state` dict attached to the server."""

    def log_message(self, *a, **kw):  # quiet
        pass

    def _reply(self, code, body):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        s = self.server.state  # type: ignore[attr-defined]
        s["requests"].append({
            "method": "GET", "path": self.path,
            "headers": dict(self.headers),
        })
        if self.path == "/api/vaults":
            return self._reply(200, {"vaults": [
                {"name": "alpha", "registered": True, "path": "/tmp/alpha"},
                {"name": "beta",  "registered": True, "path": "/tmp/beta"},
                {"name": "found", "registered": False, "path": "/tmp/found"},
            ]})
        if self.path.startswith("/api/tasks/"):
            tid = self.path.rsplit("/", 1)[-1]
            task = s["tasks"].get(tid)
            if not task:
                return self._reply(404, {"error": "not found"})
            return self._reply(200, task)
        if self.path.startswith("/api/tasks"):
            return self._reply(200, {"tasks": list(s["tasks"].values())})
        return self._reply(404, {"error": "unknown"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        body = json.loads(raw) if raw else {}
        s = self.server.state  # type: ignore[attr-defined]
        s["requests"].append({
            "method": "POST", "path": self.path,
            "headers": dict(self.headers), "body": body,
        })
        if self.path == "/api/tasks":
            if self.headers.get("X-Requested-With") != "resman":
                return self._reply(403, {"error": "missing csrf"})
            tid = f"t-{len(s['tasks']) + 1:03d}"
            task = {
                "id": tid,
                "vault": body.get("vault"),
                "operation": body.get("operation"),
                "params": body.get("params") or {},
                "priority": body.get("priority"),
                "state": "pending",
                "exit_code": None,
            }
            s["tasks"][tid] = task
            return self._reply(201, task)
        return self._reply(404, {"error": "unknown"})


@pytest.fixture
def mock_server():
    """Bring up a one-off HTTP server on a free port. The fixture exposes
    a `mark_terminal(task_id, state, exit_code)` helper so individual tests
    can drive the task state transitions that --wait polls for."""
    state = {"requests": [], "tasks": {}}
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    class Ctx:
        url = f"http://127.0.0.1:{port}"
        requests = state["requests"]
        tasks = state["tasks"]
        def mark_terminal(self, tid, state="completed", exit_code=0):
            self.tasks[tid].update(state=state, exit_code=exit_code)

    yield Ctx()
    server.shutdown()
    server.server_close()


def run_script(*args, base_url=None, timeout=15):
    cmd = ["bash", str(SCRIPT)]
    if base_url is not None:
        cmd += ["--base-url", base_url]
    cmd += list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        cwd=str(REPO_ROOT),
    )


# ─── --list-vaults ────────────────────────────────────────────────────────────

def test_list_vaults_via_api(mock_server):
    """--list-vaults must hit /api/vaults and emit only registered names."""
    r = run_script("--list-vaults", base_url=mock_server.url)
    assert r.returncode == 0, r.stderr
    names = r.stdout.strip().splitlines()
    # 'found' is registered:false → must be filtered out
    assert names == ["alpha", "beta"]
    paths = [req["path"] for req in mock_server.requests]
    assert "/api/vaults" in paths


# ─── POST /api/tasks per operation ───────────────────────────────────────────

def test_wiki_ingest_posts_url_param(mock_server):
    r = run_script(
        "--vault", "alpha", "--op", "wiki-ingest",
        "--url", "https://example.com/a", "--json",
        base_url=mock_server.url,
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout.strip())
    assert payload["task_id"].startswith("t-")
    assert payload["operation"] == "wiki-ingest"
    posts = [req for req in mock_server.requests if req["method"] == "POST"]
    assert len(posts) == 1
    body = posts[0]["body"]
    assert body["operation"] == "wiki-ingest"
    assert body["vault"] == "alpha"
    assert body["params"]["url"] == "https://example.com/a"
    assert body["params"]["update_canvas"] is False
    # CSRF header must be present
    assert posts[0]["headers"].get("X-Requested-With") == "resman"


def test_wiki_ingest_update_canvas_flag(mock_server):
    """--update-canvas must surface in params.update_canvas so the backend
    appends --can to ingest.sh."""
    r = run_script(
        "--vault", "alpha", "--op", "wiki-ingest",
        "--url", "https://x.com/y", "--update-canvas", "--json",
        base_url=mock_server.url,
    )
    assert r.returncode == 0, r.stderr
    posts = [req for req in mock_server.requests if req["method"] == "POST"]
    assert posts[-1]["body"]["params"]["update_canvas"] is True


def test_wiki_ingest_prefix_uses_prefix_op(mock_server):
    r = run_script(
        "--vault", "beta", "--op", "wiki-ingest-prefix",
        "--url", "https://x.com/z", "--json",
        base_url=mock_server.url,
    )
    assert r.returncode == 0, r.stderr
    body = [req for req in mock_server.requests if req["method"] == "POST"][-1]["body"]
    assert body["operation"] == "wiki-ingest-prefix"
    assert body["params"]["url"] == "https://x.com/z"


def test_wiki_canvas_blank_description_still_submits(mock_server):
    """wiki-canvas description is optional — blank should yield empty string,
    matching the server-side default behavior."""
    r = run_script(
        "--vault", "alpha", "--op", "wiki-canvas", "--json",
        base_url=mock_server.url,
    )
    assert r.returncode == 0, r.stderr
    body = [req for req in mock_server.requests if req["method"] == "POST"][-1]["body"]
    assert body["operation"] == "wiki-canvas"
    assert body["params"] == {"description": ""}


def test_wiki_autoresearch_requires_topic(mock_server):
    r = run_script(
        "--vault", "alpha", "--op", "wiki-autoresearch",
        base_url=mock_server.url,
    )
    assert r.returncode == 1
    assert "topic" in r.stderr.lower()


def test_wiki_autoresearch_passes_topic(mock_server):
    r = run_script(
        "--vault", "alpha", "--op", "wiki-autoresearch",
        "--topic", "edge computing 2026", "--json",
        base_url=mock_server.url,
    )
    assert r.returncode == 0, r.stderr
    body = [req for req in mock_server.requests if req["method"] == "POST"][-1]["body"]
    assert body["params"]["topic"] == "edge computing 2026"


def test_wiki_lint_sends_empty_params(mock_server):
    r = run_script(
        "--vault", "alpha", "--op", "wiki-lint", "--json",
        base_url=mock_server.url,
    )
    assert r.returncode == 0, r.stderr
    body = [req for req in mock_server.requests if req["method"] == "POST"][-1]["body"]
    assert body["operation"] == "wiki-lint"
    assert body["params"] == {}


# ─── Defaults ────────────────────────────────────────────────────────────────

def test_default_priority_is_high_and_force_is_true(mock_server):
    """A phone user can't open the window from their phone, so the script
    bypasses the window gate by default. Priority defaults to high to match
    the UI's new default."""
    r = run_script(
        "--vault", "alpha", "--op", "wiki-lint", "--json",
        base_url=mock_server.url,
    )
    assert r.returncode == 0, r.stderr
    body = [req for req in mock_server.requests if req["method"] == "POST"][-1]["body"]
    assert body["priority"] == "high"
    assert body["force"] is True


def test_no_force_opts_out_of_window_bypass(mock_server):
    r = run_script(
        "--vault", "alpha", "--op", "wiki-lint", "--no-force", "--json",
        base_url=mock_server.url,
    )
    assert r.returncode == 0, r.stderr
    body = [req for req in mock_server.requests if req["method"] == "POST"][-1]["body"]
    assert body["force"] is False


# ─── Safety: dangerous ops are rejected ──────────────────────────────────────

def test_run_shell_is_blocked(mock_server):
    """run-shell is intentionally not exposed — a phone-driven path
    shouldn't be able to execute arbitrary commands."""
    r = run_script(
        "--vault", "alpha", "--op", "run-shell",
        base_url=mock_server.url,
    )
    assert r.returncode == 1
    assert "not exposed" in r.stderr.lower()


def test_run_prompt_is_blocked(mock_server):
    r = run_script(
        "--vault", "alpha", "--op", "run-prompt",
        base_url=mock_server.url,
    )
    assert r.returncode == 1


# ─── URL validation ──────────────────────────────────────────────────────────

def test_non_http_url_rejected(mock_server):
    r = run_script(
        "--vault", "alpha", "--op", "wiki-ingest",
        "--url", "ftp://x", base_url=mock_server.url,
    )
    assert r.returncode == 1


# ─── --wait polling ──────────────────────────────────────────────────────────

def test_wait_polls_until_completed(mock_server):
    """When the task moves to completed before timeout, --wait should
    exit 0 and emit the final task object in --json mode."""
    def flip_after_delay():
        # The script polls every 2s; flip after a short window.
        import time
        time.sleep(2.5)
        for tid in list(mock_server.tasks.keys()):
            mock_server.mark_terminal(tid, "completed", 0)
    threading.Thread(target=flip_after_delay, daemon=True).start()

    r = run_script(
        "--vault", "alpha", "--op", "wiki-lint",
        "--wait", "--timeout", "10", "--json",
        base_url=mock_server.url, timeout=25,
    )
    assert r.returncode == 0, r.stderr
    # --json with --wait emits two lines: create result then final task.
    lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    assert len(lines) >= 2
    final = json.loads(lines[-1])
    assert final["state"] == "completed"


def test_wait_exits_4_on_failed_task(mock_server):
    def flip_after_delay():
        import time
        time.sleep(2.5)
        for tid in list(mock_server.tasks.keys()):
            mock_server.mark_terminal(tid, "failed", 1)
    threading.Thread(target=flip_after_delay, daemon=True).start()

    r = run_script(
        "--vault", "alpha", "--op", "wiki-lint",
        "--wait", "--timeout", "10",
        base_url=mock_server.url, timeout=25,
    )
    assert r.returncode == 4


# ─── --list-tasks ─────────────────────────────────────────────────────────────

def test_list_tasks_prints_recent(mock_server):
    # Seed two tasks then call --list-tasks
    mock_server.tasks["t-001"] = {
        "id": "t-001", "vault": "alpha", "operation": "wiki-lint",
        "state": "completed", "created_at": "2026-05-14T08:00:00Z",
    }
    mock_server.tasks["t-002"] = {
        "id": "t-002", "vault": "beta", "operation": "wiki-ingest",
        "state": "running", "created_at": "2026-05-14T08:01:00Z",
    }
    r = run_script("--list-tasks", base_url=mock_server.url)
    assert r.returncode == 0, r.stderr
    assert "t-001" in r.stdout
    assert "t-002" in r.stdout
    assert "wiki-lint" in r.stdout
    assert "wiki-ingest" in r.stdout


# ─── Server unreachable ───────────────────────────────────────────────────────

def test_unreachable_server_exits_2():
    # Port 1 is reserved and won't accept connections.
    r = run_script(
        "--vault", "alpha", "--op", "wiki-lint",
        base_url="http://127.0.0.1:1",
    )
    assert r.returncode == 2
    assert "cannot reach" in r.stderr.lower()


def test_server_returns_400_propagates_as_exit_3():
    """If the server rejects (e.g. validation), the script must surface
    that as exit 3 so an openClaw retry loop doesn't keep poking."""
    # Stand up a server that returns 400 on POST /api/tasks
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a, **kw): pass
        def do_POST(self):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"vault not registered"}')
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        r = run_script(
            "--vault", "ghost", "--op", "wiki-lint",
            base_url=f"http://127.0.0.1:{port}",
        )
        assert r.returncode == 3
        assert "vault not registered" in r.stderr
    finally:
        server.shutdown()
        server.server_close()


# ─── Non-interactive guard ────────────────────────────────────────────────────

def test_interactive_without_tty_and_flags_fails_cleanly(mock_server):
    """If we're not on a TTY and neither --vault nor --op was passed, the
    script must abort with a usage error instead of hanging on a read."""
    r = run_script(base_url=mock_server.url)
    assert r.returncode == 1
    assert "required" in r.stderr.lower() or "tty" in r.stderr.lower()
