"""The Tasks view in a real browser: the picker comes from GET /api/operations,
the source switch narrows it, every card carries its source, and the queue's
source filter works (docs/design/17-skills.md; plan phase 2).

Tasks are pre-seeded through config/tasks.jsonl so nothing runs `claude`.
Skips when Playwright or the cached Chromium shell is unavailable.
"""
from __future__ import annotations

import json

import pytest

pw = pytest.importorskip("playwright.sync_api")

from tests.browser_app import chromium_shell, serve  # noqa: E402

CHROME = chromium_shell()
TS = "2026-09-24T10:00:00Z"


def _events(tid, operation, params):
    created = {"ts": TS, "event": "created", "task_id": tid,
               "data": {"name": tid, "vault": "alpha", "operation": operation,
                        "params": params, "priority": "high", "schedule": "background"}}
    done = {"ts": TS, "event": "completed", "task_id": tid, "exit_code": 0}
    return json.dumps(created) + "\n" + json.dumps(done) + "\n"


TASKS_JSONL = (_events("t-lint", "wiki-lint", {})
               + _events("t-shell", "run-shell", {"cmd_parts": ["true"]})
               + _events("t-gone", "wiki-gone", {}))


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    base = tmp_path_factory.mktemp("tasks")
    with serve(base, {"wiki/overview.md": "# Overview\n"},
               config_files={"tasks.jsonl": TASKS_JSONL}) as (url, vault):
        yield url, vault


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        yield b
        b.close()


@pytest.fixture
def page(site, browser):
    url, _vault = site
    page = browser.new_page()
    page.goto(url + "/", wait_until="load")
    page.wait_for_function("() => typeof state !== 'undefined' && state.operations.length > 0 && state.tasks.length > 0")
    page.evaluate("() => { localStorage.clear(); showPanel('tasks'); }")
    page.wait_for_selector("#t-op-list .kind-card")
    yield page
    page.close()


def cards(page):
    return page.eval_on_selector_all("#t-op-list .kind-card", "els => els.map(e => e.dataset.op)")


def test_the_picker_is_the_registry(page):
    keys = cards(page)
    assert len(keys) == len(set(keys)) >= 10
    assert {"wiki-ingest", "wiki-lint", "run-prompt", "run-shell"} <= set(keys)
    providers = page.eval_on_selector_all("#t-op-list .kind-card", "els => new Set(els.map(e => e.dataset.provider)).size")
    assert providers >= 2
    groups = page.eval_on_selector_all("#t-op-list .kind-group-label", "els => els.map(e => e.textContent.trim())")
    assert groups[:3] == ["Research", "Wiki", "Custom"]


def test_the_source_switch_narrows_the_cards_and_all_restores_them(page):
    labels = page.eval_on_selector_all("#t-provider .provider-btn", "els => els.map(e => e.textContent.trim())")
    assert labels[0] == "All" and "claude-obsidian" in labels and "ad hoc" in labels
    page.click("#t-provider .provider-btn[data-provider='adhoc']")
    assert set(cards(page)) == {"run-prompt", "run-shell"}
    assert page.input_value("#t-op") == "run-prompt"        # selection moved to a visible card
    assert page.evaluate("() => localStorage.getItem('resman-task-provider')") == "adhoc"
    page.click("#t-provider .provider-btn[data-provider='']")
    assert len(cards(page)) >= 10


def test_re_running_a_task_from_another_source_widens_the_switch(page):
    page.click("#t-provider .provider-btn[data-provider='adhoc']")
    page.select_option("#task-state-filter", "all")
    page.click(".task-card[data-tid='t-lint'] button[data-act='re-run']")
    assert page.input_value("#t-op") == "wiki-lint"
    assert page.evaluate("() => state.opProvider") == ""
    assert "wiki-lint" in cards(page)


def test_every_card_in_the_queue_carries_its_source(page):
    page.select_option("#task-state-filter", "all")
    pills = page.eval_on_selector_all(
        ".task-card", "els => Object.fromEntries(els.map(e => [e.dataset.tid, e.querySelector('.provider-pill').textContent.trim()]))")
    assert pills == {"t-lint": "obsidian", "t-shell": "ad hoc", "t-gone": "unknown"}
    # an operation the registry no longer has still renders, by its key
    assert page.text_content(".task-card[data-tid='t-gone'] .task-card-op").strip() == "· wiki-gone"


def test_the_queue_source_filter(page):
    page.select_option("#task-state-filter", "all")
    options = page.eval_on_selector_all("#task-source-filter option", "els => els.map(e => e.value)")
    assert options[:2] == ["", "obsidian"] and "adhoc" in options
    page.select_option("#task-source-filter", "adhoc")
    tids = page.eval_on_selector_all(".task-card", "els => els.map(e => e.dataset.tid)")
    assert tids == ["t-shell"]
    assert page.evaluate("() => localStorage.getItem('resman-task-source')") == "adhoc"
    page.select_option("#task-source-filter", "")
    assert page.eval_on_selector_all(".task-card", "els => els.length") == 3


def test_attend_is_offered_for_prompt_operations_only(page):
    page.select_option("#task-state-filter", "all")
    acts = page.eval_on_selector_all(
        ".task-card", "els => Object.fromEntries(els.map(e => [e.dataset.tid, [...e.querySelectorAll('button[data-act]')].map(b => b.dataset.act)]))")
    assert "attend" in acts["t-lint"] and "attend" not in acts["t-shell"] and "attend" not in acts["t-gone"]
