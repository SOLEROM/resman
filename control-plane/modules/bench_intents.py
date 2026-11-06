"""ipcapps adoption (solBench/ipcapps): resman's intents on /api/bench/* + the sender client.

Fail-soft: without the kit in the venv the app starts with no /api/bench/*
routes, and senders see resman as unavailable (ipcappsPlan I11).

Intents (ipcappsPlan §5) wrap TaskManager.create_task, the same call the UI's
POST /api/tasks makes (I4):
  wiki.ingest@1    vault (registered), url    → operation wiki-ingest
  wiki.research@1  vault (registered), topic  → operation wiki-autoresearch
The task name carries the origin (`<app>-<ref>-<verb>`), and the job status
is the task's state mapped onto the protocol's seven.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

APP_ID = "resman"          # the bench.toml id (ipcappsPlan I9)

# task state → protocol status (ipcappsPlan §3.4)
STATUS = {
    "pending": "queued",
    "deferred": "queued",
    "scheduled": "queued",
    "running": "running",
    "completed": "done",
    "failed": "failed",
    "interrupted": "failed",
    "cancelled": "cancelled",
}
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def task_name(origin: dict, verb: str) -> str:
    """The origin in a task name (TaskManager allows only [A-Za-z0-9_-])."""
    raw = f"{origin['app']}-{origin['ref']}-{verb}"
    return (_UNSAFE.sub("-", raw).strip("-") or verb)[:80]


def task_status(task) -> dict:
    if task is None:
        return {"status": "unknown", "detail": "the task is gone from resman's log"}
    status = STATUS.get(task.state, "unknown")
    detail = task.error or task.state
    return {"status": status, "detail": detail}


def register(bench, ctx) -> None:
    from ipcapps import BadParams, Param

    def vaults():
        return ctx["vault_registry"].all_names()

    def start(operation, verb, params, origin):
        try:
            task = ctx["task_manager"].create_task(
                name=task_name(origin, verb), vault=params["vault"], operation=operation,
                params={k: v for k, v in params.items() if k != "vault"},
                priority="medium", schedule="background", run_now=True)
        except ValueError as exc:
            raise BadParams(str(exc)) from None
        bus = ctx.get("bus")      # the footer activity log, as routes._activity does
        if bus:
            bus.emit("activity", {"level": "info", "source": "bench", "detail": task.id,
                                  "message": f"task queued by {origin['app']}: "
                                             f"{operation} on {params['vault']}"})
        return {"native_id": task.id, "status": STATUS.get(task.state, "queued"), "link": "/"}

    def status(native_id):
        return task_status(ctx["task_manager"].get(native_id))

    @bench.intent("wiki.ingest", title="Ingest a URL into a vault",
                  desc="Fetches the page and files it as wiki pages with provenance (wiki-ingest task).",
                  params={"vault": Param.enum(vaults, required=True),
                          "url": Param.url(required=True)})
    def wiki_ingest(params, origin):
        return start("wiki-ingest", "ingest", params, origin)

    @bench.intent("wiki.research", title="Research a topic into a vault",
                  desc="Runs autoresearch on the topic and files the findings (wiki-autoresearch task).",
                  params={"vault": Param.enum(vaults, required=True),
                          "topic": Param.string(required=True)})
    def wiki_research(params, origin):
        return start("wiki-autoresearch", "research", params, origin)

    wiki_ingest.status(status)
    wiki_research.status(status)


def init_bench(app, ctx, data_dir):
    """Mount the kit's blueprint and keep a client on the app; None when the kit is absent."""
    try:
        from ipcapps import BenchClient, BenchIntents
    except ImportError as exc:
        log.warning("ipcapps not installed (%s): no /api/bench/* routes this run", exc)
        return None
    bench = BenchIntents(APP_ID, data_dir)
    register(bench, ctx)
    app.register_blueprint(bench.blueprint())
    app.extensions["ipcapps"] = bench
    app.extensions["ipcapps_client"] = BenchClient(APP_ID)
    return bench
