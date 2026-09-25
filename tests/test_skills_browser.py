"""The Skills tab in a real browser: both providers in the tree, a resman
skill's page with its Settings card, and a save/reset round trip into
resman.yaml (docs/design/17-skills.md; plan phase 3).

The app runs against a fake resman_root holding a skills/ folder with
one skill (`demo`, with settings). The claude-obsidian plugin is absent in
the test environment (conftest isolates the Claude dir), so that provider
shows as not found. Skips when Playwright or the Chromium shell is missing.
"""
from __future__ import annotations

import pytest
import yaml

pw = pytest.importorskip("playwright.sync_api")

from tests.browser_app import chromium_shell, serve  # noqa: E402
from tests.conftest import _make_resman_skills  # noqa: E402

CHROME = chromium_shell()


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    base = tmp_path_factory.mktemp("skills")
    root = base / "resman"
    _make_resman_skills(root)
    (root / "skills" / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: does demo\n---\n# demo\n\nThe demo skill body.\n")
    with serve(base, {"wiki/overview.md": "# Overview\n"}, resman_root=root) as (url, vault):
        yield url, base


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        yield b
        b.close()


@pytest.fixture
def page(site, browser):
    url, _base = site
    page = browser.new_page()
    page.goto(url + "/", wait_until="load")
    page.wait_for_function("() => typeof state !== 'undefined' && state.operations.length > 0")
    page.evaluate("() => showPanel('skills')")
    page.wait_for_selector("#skills-tree-list .help-dir")
    yield page
    page.close()


def tree_labels(page):
    return page.eval_on_selector_all("#skills-tree-list .help-label", "els => els.map(e => e.textContent.trim())")


def test_both_providers_are_in_the_tree_and_the_badge_counts_warnings(page):
    labels = tree_labels(page)
    assert labels[:2] == ["Overview", "New vault process"]
    assert any(l.startswith("claude-obsidian") and "not found" in l for l in labels)
    assert "resman skills 0.1.0" in labels
    assert "Wired to an operation" in labels and "deep-list" in labels   # the registry's rs-deep-list
    assert "Not wired yet" in labels and "demo" in labels and "Docs" in labels
    assert labels[-1] == "Custom skill guide"
    # one warning: the plugin is not installed in this environment
    assert page.text_content("#skills-badge").strip() == "1"
    overview = page.text_content("#skills-content")
    assert "resman skills" in overview
    assert "resman:deep-list" in overview and "rs-deep-list task" in overview   # the uses table


def test_the_guide_is_the_folder_readme(page):
    page.click("#skills-tree-list .help-label[data-page='guide']")
    page.wait_for_selector("#skills-content h1")
    assert page.text_content("#skills-content h1").strip() == "Custom skill guide"


def test_a_skill_page_shows_its_settings_and_saves_them(page, site):
    _url, base = site
    page.click("#skills-tree-list .help-label[data-page='resman:skills/demo/SKILL.md']")
    page.wait_for_selector("#skills-settings-form")
    assert "The demo skill body." in page.text_content("#skills-content")
    assert "/resman:demo" in page.text_content("#skills-content")
    assert page.input_value("#sk-n") == "1" and page.input_value("#sk-focus") == ""
    assert page.is_disabled("#btn-skill-save") and page.is_disabled("#btn-skill-reset")
    page.fill("#sk-n", "5")
    page.fill("#sk-focus", "edge inference")
    assert not page.is_disabled("#btn-skill-save")
    page.click("#btn-skill-save")
    page.wait_for_function("() => document.querySelector('#skills-settings-status')?.textContent === 'Saved.'")
    stored = yaml.safe_load((base / "config" / "system.yaml").read_text())["skills"]
    assert stored == {"demo": {"n": 5, "focus": "edge inference"}}
    assert 'n=5 focus="edge inference"' in page.text_content(".skills-args")
    # a value outside the schema is refused with the key named
    page.fill("#sk-n", "50")
    page.click("#btn-skill-save")
    page.wait_for_function("() => document.querySelector('#skills-settings-error')?.textContent.includes('demo.n')")
    assert yaml.safe_load((base / "config" / "system.yaml").read_text())["skills"]["demo"]["n"] == 5
    # reset forgets the stored values
    page.once("dialog", lambda d: d.accept())
    page.click("#btn-skill-reset")
    page.wait_for_function("() => document.querySelector('#sk-n')?.value === '1'")
    assert "skills" not in yaml.safe_load((base / "config" / "system.yaml").read_text())
    assert page.is_disabled("#btn-skill-reset")
