"""ipcapps adoption (solBench/ipcapps/ipcappsPlan.md Phase 2): resman is a target.

wiki.ingest and wiki.research queue the same tasks the UI's POST /api/tasks
does; the origin lands in the task name; job status is the task state.
"""
import pytest

from tests.conftest import solbench_home
from tests.test_routes import make_test_app

HDR = {"X-Requested-With": "bench"}
LO = {"REMOTE_ADDR": "127.0.0.1"}
ORIGIN = {"app": "soldo", "ref": "k3x9", "link": "/#item=k3x9"}


def _kit():
    try:
        import ipcapps.testing
    except ImportError:
        if (solbench_home() / "ipcapps" / "pyproject.toml").is_file():
            pytest.fail("ipcapps is not installed in this venv: run.sh installs it, "
                        "or run ../solBench/ipcapps/install.sh <venv>")
        pytest.skip("this solBench checkout has no ipcapps kit")
    return ipcapps.testing


@pytest.fixture
def target(tmp_path):
    _kit()
    from modules.bench_intents import init_bench
    app, ctx, runner_calls = make_test_app(tmp_path)
    init_bench(app, ctx, tmp_path / "config")
    return app, ctx


def _invoke(app, name, params, rid):
    return app.test_client().post(f"/api/bench/intents/{name}", headers=HDR, environ_base=LO,
                                  json={"version": 1, "request_id": rid, "params": params,
                                        "origin": ORIGIN})


def test_conforms_with_live_vault_choices(target):
    app, _ = target
    man = _kit().assert_conforms(app, "resman")
    by = {i["name"]: i for i in man["intents"]}
    assert set(by) == {"wiki.ingest", "wiki.research"}
    assert by["wiki.ingest"]["params"]["vault"]["choices"] == ["alpha"]
    assert by["wiki.research"]["params"]["topic"]["required"] is True


def test_wiki_ingest_queues_a_task_named_for_its_origin(target):
    app, ctx = target
    r = _invoke(app, "wiki.ingest", {"vault": "alpha", "url": "https://example.org/a"}, "req-resman-01")
    assert r.status_code == 201, r.get_json()
    out = r.get_json()
    native = out["job_id"].split(":", 1)[1]
    task = ctx["task_manager"].get(native)
    assert task.operation == "wiki-ingest" and task.vault == "alpha"
    assert task.params["url"] == "https://example.org/a"
    assert task.name == "soldo-k3x9-ingest"


def test_wiki_research_queues_autoresearch(target):
    app, ctx = target
    r = _invoke(app, "wiki.research", {"vault": "alpha", "topic": "rust embedded"}, "req-resman-02")
    assert r.status_code == 201, r.get_json()
    task = ctx["task_manager"].get(r.get_json()["job_id"].split(":", 1)[1])
    assert task.operation == "wiki-autoresearch" and task.params["topic"] == "rust embedded"
    assert task.name == "soldo-k3x9-research"


def test_unknown_vault_is_400(target):
    app, ctx = target
    r = _invoke(app, "wiki.ingest", {"vault": "nope", "url": "https://x.example"}, "req-resman-03")
    assert r.status_code == 400 and "alpha" in r.get_json()["error"]


def test_status_maps_task_state(target):
    from modules.bench_intents import STATUS, task_status
    app, ctx = target
    r = _invoke(app, "wiki.ingest", {"vault": "alpha", "url": "https://example.org/b"}, "req-resman-04")
    job_id = r.get_json()["job_id"]
    job = app.test_client().get(f"/api/bench/jobs/{job_id}", headers=HDR, environ_base=LO).get_json()
    task = ctx["task_manager"].get(job_id.split(":", 1)[1])
    assert job["status"] == STATUS[task.state]
    assert job["origin"] == {"app": "soldo", "ref": "k3x9", "link": "/#item=k3x9", "hops": 0}
    assert task_status(None)["status"] == "unknown"
    assert STATUS["completed"] == "done" and STATUS["interrupted"] == "failed"


def test_repeat_queues_once(target):
    app, ctx = target
    body = {"vault": "alpha", "url": "https://example.org/c"}
    first = _invoke(app, "wiki.ingest", body, "req-resman-05").get_json()
    again = _invoke(app, "wiki.ingest", body, "req-resman-05")
    assert again.status_code == 200 and again.get_json() == first
    named = [t for t in ctx["task_manager"]._tasks.values() if t.params.get("url") == body["url"]]
    assert len(named) == 1


def test_build_app_mounts_it(tmp_path):
    _kit()
    from server import build_app
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    vault = tmp_path / "alpha"
    (vault / ".obsidian").mkdir(parents=True)
    (cfg / "system.yaml").write_text(f"app:\n  port: 5090\nvaults:\n  - name: alpha\n    path: {vault}\n")
    app, _sio, _ctx = build_app(cfg, async_mode="threading", use_webterm=False)
    _kit().assert_conforms(app, "resman")
    assert app.extensions["ipcapps_client"].app_id == "resman"
