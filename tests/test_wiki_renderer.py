"""The browser-side wiki renderer (app.js renderWikiMarkdown): every render
goes through DOMPurify — wiki pages, their folded metadata prose, and help
pages — and the Wiki tab's own features still work on the sanitized output.

Runs in the real headless Chromium shell through Playwright against the real
app; skipped when either is unavailable.
"""
from __future__ import annotations

import re

import pytest

pw = pytest.importorskip("playwright.sync_api")

from tests.browser_app import chromium_shell, serve  # noqa: E402

CHROME = chromium_shell()

PAYLOADS = {
    "script": "hello <script>window.pwned=1</script> world",
    "img-onerror": '<img src=x onerror="window.pwned=1">',
    "wikilink-attr": '[[x" onmouseover="window.pwned=1|alias]]',
    "wikilink-alias": "[[x|<img src=x onerror=window.pwned=1>]]",
    "javascript-href": "[click](javascript:window.pwned=1)",
    "svg": '<svg onload="window.pwned=1"><a xlink:href="javascript:window.pwned=1">x</a></svg>',
    "iframe": '<iframe src="http://evil/"></iframe>',
    "style": "<style>body{display:none}</style>text",
    "form": '<form action="http://evil/"><button>go</button></form>',
    "folded-prose": 'lead <img src=x onerror="window.pwned=1">\n\n# Heading\n\nbody',
    "frontmatter": '---\ntitle: "<img src=x onerror=window.pwned=1>"\n---\n# T\n',
}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    base = tmp_path_factory.mktemp("renderer")
    with serve(base, {
        "wiki/overview.md": "# Overview\n\nSee [[Target Page]] and [[Target Page|the alias]].\n",
        "wiki/Target Page.md": "# Target\n\nArrived.\n",
    }) as (url, vault):
        yield url, vault


@pytest.fixture(scope="module")
def page(site):
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = browser.new_page()
        page.goto(site[0] + "/", wait_until="load")
        page.wait_for_function("() => typeof renderWikiMarkdown === 'function' && !!window.DOMPurify && !!window.marked")
        yield page
        browser.close()


def render(page, text, wikilinks=True):
    return page.evaluate("""([text, wikilinks]) => {
        window.pwned = 0;
        const box = document.createElement('div');
        document.body.appendChild(box);
        box.innerHTML = renderWikiMarkdown(text, { wikilinks });
        const html = box.innerHTML;
        // event-handler attributes that survived (an escaped one inside
        // another attribute's value is just text)
        const handlers = [...box.querySelectorAll('*')].flatMap((el) =>
            [...el.attributes].filter((a) => /^on/i.test(a.name)).map((a) => a.name));
        box.remove();
        return { html, handlers };
    }""", [text, wikilinks])


@pytest.mark.parametrize("wikilinks", [True, False], ids=["wiki", "help"])
@pytest.mark.parametrize("name", sorted(PAYLOADS))
def test_payload_is_neutralised(page, name, wikilinks):
    out = render(page, PAYLOADS[name], wikilinks)
    html = out["html"]
    page.wait_for_timeout(150)       # give a deferred handler a chance to fire
    assert page.evaluate("() => window.pwned") == 0, html
    assert out["handlers"] == [], html
    assert not re.search(r"<(script|iframe|style|form|button)\b", html), html
    assert not re.search(r"href\s*=\s*[\"']?\s*javascript:", html, re.I), html


def test_wikilinks_keep_their_target(page):
    html = render(page, "see [[Target Page|the alias]] and `code`")["html"]
    assert 'data-wiki-target="Target Page"' in html and ">the alias<" in html


def test_metadata_still_folds(page):
    html = render(page, "---\ntype: concept\n---\nstray lead\n\n# Heading\n\nbody\n")["html"]
    assert html.startswith('<details class="wiki-frontmatter">')
    assert "type: concept" in html and "stray lead" in html and "<h1" in html


def test_single_newlines_still_break(page):
    assert "<br>" in render(page, "one\ntwo")["html"]


def test_missing_sanitizer_degrades_to_escaped_text(page):
    html = page.evaluate("""() => {
        const keep = window.DOMPurify;
        window.DOMPurify = undefined;
        try { return renderWikiMarkdown('<b>bold</b>'); } finally { window.DOMPurify = keep; }
    }""")
    assert html == "<pre>&lt;b&gt;bold&lt;/b&gt;</pre>"


def test_a_wikilink_click_opens_its_page(page):
    page.evaluate("() => selectVault('alpha', { panel: 'wiki', wikiFile: 'wiki/overview.md' })")
    page.wait_for_selector("#wiki-content a.wikilink")
    page.click("#wiki-content a.wikilink >> nth=1")
    page.wait_for_function("() => document.querySelector('#wiki-content').textContent.includes('Arrived')")
