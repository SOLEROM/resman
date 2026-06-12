"""Smoke test: build_app composes correctly with a real config + tmux check."""
from pathlib import Path

import pytest


def test_build_app_with_valid_config(tmp_path, monkeypatch):
    from server import build_app
    cfg = tmp_path / "config"
    cfg.mkdir()
    vault = tmp_path / "alpha"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (cfg / "system.yaml").write_text(
        f"app:\n  port: 5090\nvaults:\n  - name: alpha\n    path: {vault}\n"
    )
    app, sio, ctx = build_app(cfg, async_mode="threading")
    client = app.test_client()
    rv = client.get("/api/health")
    assert rv.status_code == 200
    rv = client.get("/")
    assert rv.status_code == 200
    assert b"resman" in rv.data
    rv = client.get("/api/vaults")
    names = [v["name"] for v in rv.get_json()["vaults"]]
    assert "alpha" in names
