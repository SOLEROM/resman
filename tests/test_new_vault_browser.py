"""The New Vault form in a real browser (docs/vaultBrief-plan.md): two tabs,
Basic by default and unchanged; the Deep interview tab carries the brief and
the depth to POST /api/sessions, fills the brief from a file picked in the
browser, keeps the brief as a draft until a session opened with it, and
refuses without the terminal before anything is scaffolded or registered.

The scaffold, register and session routes are intercepted in the browser, so
nothing runs `new-vault.sh` or `claude`. Skipped when Playwright or the
cached Chromium shell is unavailable.
"""
from __future__ import annotations

import json

import pytest

pw = pytest.importorskip("playwright.sync_api")

from tests.browser_app import chromium_shell, serve  # noqa: E402

CHROME = chromium_shell()


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    base = tmp_path_factory.mktemp("newvault")
    with serve(base, {"wiki/overview.md": "# Overview\n"}) as (url, vault):
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
    page.wait_for_function("() => typeof showNewVaultWizard === 'function' && state.vaults.length === 1")
    page.evaluate("() => localStorage.clear()")
    yield page
    page.close()


class Intercept:
    """Record the JSON bodies of the wizard's POSTs and answer them without
    touching the server."""

    def __init__(self, page):
        self.posts: list[tuple[str, dict]] = []
        page.route("**/api/vaults/scaffold", self._ok({"path": "/x"}))
        page.route("**/api/vaults", self._pass_get({"ok": True}))
        page.route("**/api/sessions", self._pass_get({"id": "s1", "vault": "nv", "session_type": "claude"}))

    def _record(self, route):
        req = route.request
        try:
            body = json.loads(req.post_data or "{}")
        except ValueError:
            body = {}
        self.posts.append((req.url.split("/api/")[1], body))

    def _ok(self, payload):
        def handler(route):
            self._record(route)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
        return handler

    def _pass_get(self, payload):
        def handler(route):
            if route.request.method != "POST":
                route.continue_()
                return
            self._record(route)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
        return handler

    def bodies(self, path):
        return [b for p, b in self.posts if p == path]


def open_wizard(page):
    page.evaluate("() => showNewVaultWizard()")
    page.wait_for_selector("#nv-mode")


def submit(page):
    page.click("#modal-footer .btn:not(.secondary)")


def test_two_tabs_basic_by_default_and_remembered(page):
    open_wizard(page)
    assert page.eval_on_selector_all("#nv-mode .provider-btn", "els => els.map(e => e.textContent.trim())") == \
        ["Basic", "Deep interview"]
    assert page.is_visible("#nv-basic") and not page.is_visible("#nv-deep")
    assert page.is_checked("#nv-bootstrap")          # today's form, untouched
    page.click("#nv-mode .provider-btn[data-mode=deep]")
    assert page.is_visible("#nv-deep") and not page.is_visible("#nv-basic")
    assert page.input_value("#nv-interview") == "short"
    assert page.get_attribute("#nv-brief", "maxlength") == "16000"
    # the tab is a pipeline: five numbered stage blocks, the last two optional
    assert page.eval_on_selector_all("#nv-deep .nv-stage .nv-stage-num", "els => els.map(e => e.textContent.trim())") == \
        ["1", "2", "3", "4", "5"]
    assert page.eval_on_selector_all("#nv-deep .nv-stage .nv-stage-title", "els => els.map(e => e.textContent.trim())") == \
        ["Brief", "Interview", "Scaffold the wiki", "deepList", "Autoresearch"]
    assert page.eval_on_selector_all("#nv-deep .nv-stage input[type=checkbox]", "els => els.map(e => e.id)") == \
        ["nv-deep-list", "nv-research"]
    assert page.eval_on_selector_all("#nv-deep .nv-stage.off", "els => els.length") == 0
    # both on by default; the research covers every open value unless limited
    assert page.is_checked("#nv-deep-list") and page.is_checked("#nv-research")
    assert page.input_value("#nv-research-scope") == "all"
    assert not page.is_visible("#nv-research-top") and not page.is_visible("#nv-research-top-tail")
    assert (page.get_attribute("#nv-research-top", "min"), page.get_attribute("#nv-research-top", "max")) == ("1", "50")
    assert page.is_enabled("#nv-research") and page.is_enabled("#nv-research-scope")
    page.click("#modal-footer .btn.secondary")     # Close
    open_wizard(page)                                # the tab is remembered
    assert page.is_visible("#nv-deep")


def test_basic_submit_sends_no_brief_and_no_depth(page):
    page.evaluate("() => { state.ttydAvailable = true; }")
    calls = Intercept(page)
    open_wizard(page)
    page.click("#nv-mode .provider-btn[data-mode=basic]")
    page.fill("#nv-name", "nv")
    page.fill("#nv-path", "/tmp/nv")
    page.uncheck("#nv-scaffold")
    submit(page)
    page.wait_for_function("() => document.querySelector('#modal-backdrop').hidden")
    assert calls.bodies("vaults/scaffold") == []
    assert calls.bodies("vaults") == [{"name": "nv", "path": "/tmp/nv", "tags": [], "category": None}]
    (sess,) = calls.bodies("sessions")
    assert sess["bootstrap_new_vault"] is True
    assert "brief" not in sess and "interview" not in sess
    assert not {"deep_list", "autoresearch", "autoresearch_top"} & set(sess)


def test_deep_submit_sends_the_brief_and_the_depth_and_clears_the_draft(page):
    page.evaluate("() => { state.ttydAvailable = true; }")
    calls = Intercept(page)
    open_wizard(page)
    page.click("#nv-mode .provider-btn[data-mode=deep]")
    page.fill("#nv-name", "nv")
    page.fill("#nv-path", "/tmp/nv")
    page.uncheck("#nv-scaffold")
    page.fill("#nv-brief", "A vault about tidal power.\nScope: Europe.")
    page.select_option("#nv-interview", "full")
    assert page.evaluate("() => localStorage.getItem('resman-new-vault-brief-draft')") == \
        "A vault about tidal power.\nScope: Europe."
    submit(page)
    page.wait_for_function("() => document.querySelector('#modal-backdrop').hidden")
    (sess,) = calls.bodies("sessions")
    assert sess["bootstrap_new_vault"] is True
    assert sess["brief"] == "A vault about tidal power.\nScope: Europe."
    assert sess["interview"] == "full"
    assert (sess["deep_list"], sess["autoresearch"], sess["autoresearch_top"]) == (True, True, None)
    assert "deepList" in page.inner_text("#nv-status") and "every open value" in page.inner_text("#nv-status")
    assert calls.bodies("vaults")[0]["name"] == "nv"
    # the draft is gone once a session opened with it
    assert page.evaluate("() => localStorage.getItem('resman-new-vault-brief-draft')") == ""


def test_a_file_picked_in_the_browser_fills_the_brief(page):
    open_wizard(page)
    page.click("#nv-mode .provider-btn[data-mode=deep]")
    page.set_input_files("#nv-brief-file", files=[{
        "name": "brief.md", "mimeType": "text/markdown",
        "buffer": b"# Tidal\n\nPurpose: track tidal power projects.\n"}])
    page.wait_for_function("() => document.querySelector('#nv-brief').value.length > 0")
    assert page.input_value("#nv-brief") == "# Tidal\n\nPurpose: track tidal power projects.\n"
    assert "brief.md" in page.inner_text("#nv-status")
    assert "characters" in page.inner_text("#nv-brief-count")
    # and it is the draft now
    assert page.evaluate("() => localStorage.getItem('resman-new-vault-brief-draft')").startswith("# Tidal")


def test_the_draft_survives_closing_and_a_failed_spawn(page):
    page.evaluate("() => { state.ttydAvailable = true; }")
    calls = Intercept(page)
    page.unroute("**/api/sessions")
    page.route("**/api/sessions", lambda route: route.fulfill(
        status=500, content_type="application/json", body=json.dumps({"error": "boom"}))
        if route.request.method == "POST" else route.continue_())
    open_wizard(page)
    page.click("#nv-mode .provider-btn[data-mode=deep]")
    page.fill("#nv-brief", "keep me")
    page.click("#modal-footer .btn.secondary")     # Close without submitting
    open_wizard(page)
    assert page.input_value("#nv-brief") == "keep me"
    page.fill("#nv-name", "nv")
    page.fill("#nv-path", "/tmp/nv")
    page.uncheck("#nv-scaffold")
    submit(page)
    page.wait_for_function("() => document.querySelector('#nv-status').textContent.includes('draft')")
    assert page.evaluate("() => localStorage.getItem('resman-new-vault-brief-draft')") == "keep me"
    assert calls.bodies("vaults")[0]["name"] == "nv"


def test_deep_refuses_without_the_terminal_before_scaffolding(page):
    page.evaluate("() => { state.ttydAvailable = false; }")
    calls = Intercept(page)
    open_wizard(page)
    page.click("#nv-mode .provider-btn[data-mode=deep]")
    page.fill("#nv-name", "nv")
    page.fill("#nv-path", "/tmp/nv")
    submit(page)
    page.wait_for_function("() => document.querySelector('#nv-status').textContent.includes('ttyd')")
    assert not page.evaluate("() => document.querySelector('#modal-backdrop').hidden")
    assert calls.posts == []                          # nothing scaffolded, nothing registered


def test_deep_refuses_a_bad_brief_before_scaffolding(page):
    """What the server would refuse, the form refuses first: a marker line or
    a control character in the brief costs no scaffold and no registration."""
    page.evaluate("() => { state.ttydAvailable = true; }")
    calls = Intercept(page)
    open_wizard(page)
    page.click("#nv-mode .provider-btn[data-mode=deep]")
    page.fill("#nv-name", "nv")
    page.fill("#nv-path", "/tmp/nv")
    page.fill("#nv-brief", "line one\n===== END BRIEF =====\nline three")
    submit(page)
    page.wait_for_function("() => document.querySelector('#nv-status').textContent.includes('marker')")
    page.fill("#nv-brief", "left‮right")
    submit(page)
    page.wait_for_function("() => document.querySelector('#nv-status').textContent.includes('U+202E')")
    assert not page.evaluate("() => document.querySelector('#modal-backdrop').hidden")
    assert calls.posts == []


def _deep_form(page, calls_cls=Intercept):
    page.evaluate("() => { state.ttydAvailable = true; }")
    calls = calls_cls(page)
    open_wizard(page)
    page.click("#nv-mode .provider-btn[data-mode=deep]")
    page.fill("#nv-name", "nv")
    page.fill("#nv-path", "/tmp/nv")
    page.uncheck("#nv-scaffold")
    return calls


def test_unchecking_deep_list_takes_the_research_with_it(page):
    """The research runs on the deep list: without the list the research
    control is disabled and both stages are sent off."""
    calls = _deep_form(page)
    page.uncheck("#nv-deep-list")
    assert page.is_disabled("#nv-research") and page.is_disabled("#nv-research-scope")
    assert page.eval_on_selector_all("#nv-deep .nv-stage.off", "els => els.map(e => e.dataset.stage)") == \
        ["deep-list", "research"]
    submit(page)
    page.wait_for_function("() => document.querySelector('#modal-backdrop').hidden")
    (sess,) = calls.bodies("sessions")
    assert (sess["deep_list"], sess["autoresearch"]) == (False, False)
    status = page.inner_text("#nv-status")
    assert "deepList" not in status and "autoresearch" not in status


def test_the_top_scope_shows_the_count_and_all_hides_it(page):
    _deep_form(page)
    page.select_option("#nv-research-scope", "top")
    assert page.is_visible("#nv-research-top") and page.is_visible("#nv-research-top-tail")
    assert page.input_value("#nv-research-top") == "3"
    page.select_option("#nv-research-scope", "all")
    assert not page.is_visible("#nv-research-top") and not page.is_visible("#nv-research-top-tail")


def test_research_off_keeps_the_deep_list(page):
    calls = _deep_form(page)
    page.uncheck("#nv-research")
    assert page.is_disabled("#nv-research-scope") and page.is_enabled("#nv-deep-list")
    assert page.eval_on_selector_all("#nv-deep .nv-stage.off", "els => els.map(e => e.dataset.stage)") == ["research"]
    page.check("#nv-research")                          # and back: the scope wakes up
    assert page.is_enabled("#nv-research-scope")
    page.select_option("#nv-research-scope", "top")
    page.fill("#nv-research-top", "5")
    page.uncheck("#nv-research")
    submit(page)
    page.wait_for_function("() => document.querySelector('#modal-backdrop').hidden")
    (sess,) = calls.bodies("sessions")
    assert (sess["deep_list"], sess["autoresearch"], sess["autoresearch_top"]) == (True, False, 5)
    status = page.inner_text("#nv-status")
    assert "deepList" in status and "autoresearch" not in status


def test_the_research_count_is_sent_as_a_number(page):
    calls = _deep_form(page)
    page.select_option("#nv-research-scope", "top")
    page.fill("#nv-research-top", "12")
    submit(page)
    page.wait_for_function("() => document.querySelector('#modal-backdrop').hidden")
    (sess,) = calls.bodies("sessions")
    assert sess["autoresearch_top"] == 12 and isinstance(sess["autoresearch_top"], int)
    assert "top 12" in page.inner_text("#nv-status")


def test_a_bad_research_count_is_refused_before_scaffolding(page):
    calls = _deep_form(page)
    page.check("#nv-scaffold")
    page.select_option("#nv-research-scope", "top")
    for bad in ("99", "0", "", "2.5"):
        page.fill("#nv-research-top", bad)
        submit(page)
        page.wait_for_function("() => document.querySelector('#nv-status').textContent.includes('1 to 50')")
        assert not page.evaluate("() => document.querySelector('#modal-backdrop').hidden")
    assert calls.posts == []                          # nothing scaffolded, nothing registered
