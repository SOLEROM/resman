"""The ★ favorite toggle end to end on the Wiki tab: a real click on the
toolbar button, the tree mark, the pinned row's count, the favorites list
view, and the ``<vault>/.favorites.md`` file it all lands in.

The parsing and the route are unit-tested (test_wiki_favorites.py,
test_routes.py); these pin what only a browser shows: that the button the
operator clicks reaches the route with the page the reader is on, whichever
way the page was opened (tree, wikilink, search hit, Back), and that a
refused toggle is visible without a dialog (a docked resman lives in a
cross-origin iframe, where Chromium drops ``alert()`` silently).

Skipped when Playwright or a headless Chromium shell is unavailable.
"""
from __future__ import annotations

import pytest

pw = pytest.importorskip("playwright.sync_api")

from tests.browser_app import chromium_shell, serve  # noqa: E402

CHROME = chromium_shell()

PAGES = {
    "wiki/overview.md": "# Overview\n\nSee [[Self-attention]] and [[concepts/gguf|GGUF]].\n",
    "wiki/concepts/self-attention.md": "# Self-attention\n\nThe cost is quadratic.\n",
    "wiki/concepts/gguf.md": "# GGUF\n\nA tensor file format.\n",
    "wiki/notes/Ada's notes — draft.md": "# Ada's notes [draft] | v2\n\nprose\n",
}


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        yield b
        b.close()


@pytest.fixture
def site(tmp_path):
    with serve(tmp_path, dict(PAGES)) as (url, vault):
        yield url, vault


@pytest.fixture
def page(site, browser):
    url, _vault = site
    page = browser.new_page()
    page.goto(url + "/", wait_until="load")
    page.wait_for_function("() => typeof selectVault === 'function' && typeof state !== 'undefined'")
    page.evaluate("() => selectVault('alpha', { panel: 'wiki', wikiFile: 'wiki/concepts/gguf.md' })")
    page.wait_for_selector("#wiki-content h1")
    page.wait_for_selector("#btn-wiki-fav:not([hidden])")
    yield page
    page.close()


def fav_button(page) -> str:
    return page.evaluate("() => document.querySelector('#btn-wiki-fav').textContent")


def favorites_file(vault) -> str:
    f = vault / ".favorites.md"
    return f.read_text() if f.is_file() else ""


def test_the_toolbar_button_adds_the_open_page(page, site):
    _url, vault = site
    assert fav_button(page).startswith("☆")
    page.click("#btn-wiki-fav")
    page.wait_for_function("() => document.querySelector('#btn-wiki-fav').textContent.startsWith('★')")
    assert "- [[wiki/concepts/gguf.md|GGUF]]" in favorites_file(vault)
    # The tree marks the page and the pinned row counts it.
    assert page.evaluate("() => document.querySelector('.wiki-file.fav > .wiki-tree-label').dataset.path") \
        == "wiki/concepts/gguf.md"
    assert page.inner_text(".wiki-pinned .wiki-fav-count") == "1"
    # Clicking again removes it.
    page.click("#btn-wiki-fav")
    page.wait_for_function("() => document.querySelector('#btn-wiki-fav').textContent.startsWith('☆')")
    assert "gguf" not in favorites_file(vault)
    assert page.query_selector(".wiki-file.fav") is None


def test_pages_reached_by_wikilink_search_and_back_can_be_favorited(page, site):
    _url, vault = site
    # Through a wikilink on the overview page.
    page.evaluate("() => loadWiki('wiki/overview.md')")
    page.wait_for_selector("#wiki-content a.wikilink")
    page.click("#wiki-content a.wikilink >> nth=0")
    page.wait_for_function("() => state.wikiFile === 'wiki/concepts/self-attention.md'")
    page.click("#btn-wiki-fav")
    page.wait_for_function("() => document.querySelector('#btn-wiki-fav').textContent.startsWith('★')")
    assert "- [[wiki/concepts/self-attention.md|Self-attention]]" in favorites_file(vault)
    # Through a search hit.
    page.evaluate("() => doWikiSearch('tensor')")
    page.click("#wiki-content .wiki-search-hit >> nth=0")
    page.wait_for_function("() => state.wikiFile === 'wiki/concepts/gguf.md'")
    page.wait_for_selector("#btn-wiki-fav:not([hidden])")
    page.click("#btn-wiki-fav")
    page.wait_for_function("() => document.querySelector('#btn-wiki-fav').textContent.startsWith('★')")
    # Back to the wikilinked page: the button still shows it as a favorite.
    page.click("#btn-wiki-back")
    page.wait_for_function("() => state.wikiFile === 'wiki/concepts/self-attention.md'")
    assert fav_button(page).startswith("★")
    assert favorites_file(vault).count("- [[") == 2


def test_a_page_name_with_punctuation_and_a_title_with_link_breakers(page, site):
    _url, vault = site
    rel = "wiki/notes/Ada's notes — draft.md"
    page.evaluate("(rel) => loadWiki(rel)", rel)
    page.wait_for_function("(rel) => state.wikiFile === rel && document.querySelector('#wiki-content h1')", arg=rel)
    page.click("#btn-wiki-fav")
    page.wait_for_function("() => document.querySelector('#btn-wiki-fav').textContent.startsWith('★')")
    text = favorites_file(vault)
    assert f"- [[{rel}|Ada's notes draft v2]]" in text
    # The list opens from the pinned tree row (the toolbar has only the toggle);
    # it shows the page under its title and can drop it again.
    assert page.query_selector(".wiki-toolbar [data-wiki-page='.favorites.md']") is None
    page.click(".wiki-pinned-fav > .wiki-tree-label")
    page.wait_for_selector(".wiki-fav-item")
    assert page.inner_text(".wiki-fav-item .wiki-fav-link") == "Ada's notes [draft] | v2"
    page.click(".wiki-fav-remove")
    page.wait_for_selector(".wiki-empty")
    assert "Ada" not in favorites_file(vault)


def test_a_refused_toggle_is_shown_inline_not_in_a_dialog(page, site):
    _url, vault = site
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    # The page vanished under the reader: the server refuses to favorite it.
    (vault / "wiki" / "concepts" / "gguf.md").unlink()
    page.click("#btn-wiki-fav")
    page.wait_for_selector("#wiki-notice:not([hidden])")
    assert "Could not update favorites: not found: wiki/concepts/gguf.md" in page.inner_text("#wiki-notice")
    assert dialogs == []
    assert fav_button(page).startswith("☆")
    assert favorites_file(vault) == ""
    # The page itself is untouched, and the next navigation clears the notice.
    assert page.inner_text("#wiki-content h1") == "GGUF"
    page.evaluate("() => loadWiki('wiki/overview.md')")
    page.wait_for_selector("#wiki-notice[hidden]", state="attached")
