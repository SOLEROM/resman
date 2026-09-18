"""Reader highlights end to end on the Wiki tab (static/js/wiki-marks.js +
wiki-highlights-glue.js): a real selection in headless Chromium, the palette,
and the markdown file it lands in.

The mapping from rendered text back to markdown is unit-tested under node
(tests/js); these pin what only a browser can show against resman's own
renderer: folded metadata, `breaks: true`, wikilinks rewritten everywhere.

Skipped when Playwright or a headless Chromium shell is unavailable.
"""
from __future__ import annotations

import pytest

pw = pytest.importorskip("playwright.sync_api")

from tests.browser_app import chromium_shell, serve  # noqa: E402

CHROME = chromium_shell()

PAGE_REL = "wiki/concepts/self-attention.md"
PAGE = """---
type: concept
---
Lead prose before the heading.

# Self-attention

Every token looks at **every other** token. See [[Target Page|the paper]] for more.
second line of the same paragraph

The cost is quadratic. The cost is quadratic. Nobody likes that.

- first point about `softmax(QK)` scores
- second point

```python
weights = softmax(q @ k.T)
```

| model | cost |
|---|---|
| attention | quadratic cost |
"""
Y = '<mark class="hl-yellow">'
G = '<mark class="hl-green">'
END = "</mark>"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    base = tmp_path_factory.mktemp("marks")
    with serve(base, {PAGE_REL: PAGE, "wiki/overview.md": "# Overview\n",
                      "CLAUDE.md": "# The vault rules\n"}) as (url, vault):
        yield url, vault


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        yield b
        b.close()


def open_page(page, rel):
    page.evaluate("(rel) => selectVault('alpha', { panel: 'wiki', wikiFile: rel })", rel)


@pytest.fixture
def page(site, browser):
    url, vault = site
    (vault / PAGE_REL).write_text(PAGE)
    page = browser.new_page()
    page.goto(url + "/", wait_until="load")
    page.wait_for_function("() => typeof selectVault === 'function' && !!window.wikiMarks && !!window.DOMPurify")
    open_page(page, PAGE_REL)
    page.wait_for_selector("#wiki-content.hl-on h1")
    yield page
    page.close()


def source(site) -> str:
    return (site[1] / PAGE_REL).read_text()


def select(page, needle: str, nth: int = 0):
    """Select the nth occurrence of `needle` inside one text node of the page
    and let go of the mouse over it, as a reader's drag would."""
    ok = page.evaluate("""([needle, nth]) => {
        const root = document.querySelector('#wiki-content');
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let seen = 0;
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
          for (let at = n.nodeValue.indexOf(needle); at !== -1; at = n.nodeValue.indexOf(needle, at + 1)) {
            if (seen++ !== nth) continue;
            const range = document.createRange();
            range.setStart(n, at); range.setEnd(n, at + needle.length);
            const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
            root.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, button: 0}));
            return true;
          }
        }
        return false;
    }""", [needle, nth])
    assert ok, f"{needle!r} #{nth} not found in one text node"


def select_across(page, start_text: str, end_text: str):
    """Select from the start of `start_text` to the end of `end_text`."""
    page.evaluate("""([a, b]) => {
        const root = document.querySelector('#wiki-content');
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const range = document.createRange();
        let open = false;
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
          if (!open && n.nodeValue.includes(a)) { range.setStart(n, n.nodeValue.indexOf(a)); open = true; }
          if (open && n.nodeValue.includes(b)) { range.setEnd(n, n.nodeValue.indexOf(b) + b.length); break; }
        }
        const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
        root.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, button: 0}));
    }""", [start_text, end_text])


def pick(page, color: str):
    page.wait_for_selector(".hl-pop:not([hidden])")
    page.click(f".hl-pop .hl-dot-{color}")


def test_select_pick_a_color_and_the_markdown_carries_the_mark(page, site):
    select(page, "looks at")
    pick(page, "yellow")
    page.wait_for_selector("#wiki-content mark.hl-yellow")
    assert f"Every token {Y}looks at{END} **every other** token." in source(site)
    assert page.inner_text("#wiki-content mark.hl-yellow") == "looks at"
    assert source(site).replace(Y, "").replace(END, "") == PAGE


def test_the_highlight_survives_a_reload_of_the_page(page, site):
    select(page, "Nobody likes that")
    pick(page, "pink")
    page.wait_for_selector("#wiki-content mark.hl-pink")
    page.evaluate("(rel) => loadWiki(rel)", PAGE_REL)
    page.wait_for_function("() => document.querySelector('#wiki-content mark.hl-pink')?.textContent === 'Nobody likes that'")


def test_a_selection_cutting_into_bold_takes_the_whole_bold_run(page, site):
    select_across(page, "other", "token. See")
    pick(page, "green")
    page.wait_for_selector("#wiki-content mark.hl-green")
    assert f"{G}**every other** token. See{END}" in source(site)


def test_wikilinks_and_code_spans_are_wrapped_whole(page, site):
    select(page, "the pap")
    pick(page, "blue")
    page.wait_for_selector("#wiki-content mark.hl-blue")
    assert '<mark class="hl-blue">[[Target Page|the paper]]</mark>' in source(site)
    select(page, "softmax(QK)")
    pick(page, "orange")
    page.wait_for_selector("#wiki-content mark.hl-orange")
    assert '<mark class="hl-orange">`softmax(QK)`</mark>' in source(site)


def test_a_highlight_may_cross_a_single_line_break(page, site):
    select_across(page, "for more.", "second line")
    pick(page, "purple")
    page.wait_for_selector("#wiki-content mark.hl-purple")
    assert '<mark class="hl-purple">for more.\nsecond line</mark> of the same' in source(site)


def test_the_second_of_two_identical_sentences_is_the_one_marked(page, site):
    select(page, "The cost is quadratic.", nth=1)
    pick(page, "purple")
    page.wait_for_selector("#wiki-content mark.hl-purple")
    assert 'The cost is quadratic. <mark class="hl-purple">The cost is quadratic.</mark> Nobody' in source(site)


def test_a_selection_across_blocks_becomes_one_mark_per_block(page, site):
    select_across(page, "Nobody likes", "second point")
    pick(page, "yellow")
    page.wait_for_function("() => document.querySelectorAll('#wiki-content mark.hl-yellow').length === 3")
    text = source(site)
    assert f"{Y}Nobody likes that.{END}" in text
    assert f"- {Y}first point about `softmax(QK)` scores{END}" in text
    assert f"- {Y}second point{END}" in text


def test_code_blocks_are_refused_with_a_message(page, site):
    select(page, "weights = softmax")
    pick(page, "yellow")
    page.wait_for_selector(".hl-flash:not([hidden])")
    assert source(site) == PAGE


def test_the_folded_metadata_is_not_page_text(page, site):
    page.evaluate("() => document.querySelector('#wiki-content details.wiki-frontmatter').open = true")
    select(page, "Lead prose")
    page.wait_for_selector(".hl-pop:not([hidden])")
    page.click(".hl-pop .hl-dot-yellow")
    page.wait_for_selector(".hl-flash:not([hidden])")
    assert source(site) == PAGE


def test_table_cells_can_be_highlighted(page, site):
    select(page, "quadratic cost")
    pick(page, "green")
    page.wait_for_selector("#wiki-content td mark.hl-green")
    assert f"| attention | {G}quadratic cost{END} |" in source(site)


def test_click_a_highlight_to_recolor_then_remove_it(page, site):
    select(page, "looks at")
    pick(page, "yellow")
    page.wait_for_selector("#wiki-content mark.hl-yellow")
    page.click("#wiki-content mark.hl-yellow")
    pick(page, "green")
    page.wait_for_selector("#wiki-content mark.hl-green")
    assert f"{G}looks at{END}" in source(site) and Y not in source(site)
    page.click("#wiki-content mark.hl-green")
    page.wait_for_selector(".hl-pop:not([hidden]) .hl-erase:not([hidden])")
    page.click(".hl-pop .hl-erase")
    page.wait_for_function("() => !document.querySelector('#wiki-content mark')")
    assert source(site) == PAGE


def test_number_keys_pick_the_color(page, site):
    select(page, "looks at")
    page.wait_for_selector(".hl-pop:not([hidden])")
    page.keyboard.press("3")
    page.wait_for_selector("#wiki-content mark.hl-blue")
    assert '<mark class="hl-blue">looks at</mark>' in source(site)


def test_ctrl_click_highlights_the_whole_sentence(page, site):
    box = page.evaluate("""() => {
        const p = [...document.querySelectorAll('#wiki-content p')].find((el) => el.textContent.includes('Nobody'));
        const n = document.createTreeWalker(p, NodeFilter.SHOW_TEXT).nextNode();
        const r = document.createRange();
        const at = n.nodeValue.indexOf('Nobody') + 2;
        r.setStart(n, at); r.setEnd(n, at + 1);
        const b = r.getBoundingClientRect();
        return {x: b.left + b.width / 2, y: b.top + b.height / 2};
    }""")
    page.keyboard.down("Control")
    page.mouse.click(box["x"], box["y"])
    page.keyboard.up("Control")
    pick(page, "orange")
    page.wait_for_selector("#wiki-content mark.hl-orange")
    assert '<mark class="hl-orange">Nobody likes that.</mark>' in source(site)


def test_pen_mode_highlights_without_the_palette(page, site):
    select(page, "looks at")
    page.wait_for_selector(".hl-pop:not([hidden])")
    page.click(".hl-pop .hl-pen")
    page.wait_for_selector("#wiki-content mark.hl-yellow")
    page.wait_for_selector(".hl-penchip:not([hidden])")
    select(page, "second point")
    page.wait_for_function("() => document.querySelectorAll('#wiki-content mark.hl-yellow').length === 2")
    assert f"- {Y}second point{END}" in source(site)
    page.click(".hl-penchip .hl-penchip-off")
    page.wait_for_selector(".hl-penchip", state="hidden")


def test_a_page_changed_on_disk_is_not_overwritten(page, site):
    changed = PAGE + "\nAn agent appended this line.\n"
    (site[1] / PAGE_REL).write_text(changed)
    select(page, "looks at")
    pick(page, "yellow")
    page.wait_for_selector(".hl-flash:not([hidden])")
    assert source(site) == changed
    page.wait_for_function("() => document.querySelector('#wiki-content').textContent.includes('An agent appended')")
    select(page, "looks at")
    pick(page, "yellow")
    page.wait_for_selector("#wiki-content mark.hl-yellow")
    assert f"{Y}looks at{END}" in source(site)


def test_pages_outside_the_wiki_offer_no_palette(page, site):
    page.evaluate("() => loadWiki('CLAUDE.md')")
    page.wait_for_function("() => !document.querySelector('#wiki-content').classList.contains('hl-on')")
    page.wait_for_selector("#wiki-content h1")
    select(page, "vault rules")
    page.wait_for_timeout(700)
    assert page.is_hidden(".hl-pop")


def test_search_results_and_favorites_turn_the_palette_off(page, site):
    page.evaluate("() => doWikiSearch('quadratic')")
    page.wait_for_selector("#wiki-content .wiki-search-hit")
    assert not page.evaluate("() => document.querySelector('#wiki-content').classList.contains('hl-on')")
    select(page, "quadratic")
    page.wait_for_timeout(700)
    assert page.is_hidden(".hl-pop")


def test_foreign_mark_classes_never_reach_the_page(page, site):
    (site[1] / PAGE_REL).write_text('# T\n\nA <mark class="modal hl-pink danger">b</mark> <mark class="toolbar">c</mark>\n')
    page.evaluate("(rel) => loadWiki(rel)", PAGE_REL)
    page.wait_for_selector("#wiki-content mark.hl-pink")
    classes = page.evaluate("() => [...document.querySelectorAll('#wiki-content mark')].map((m) => m.className)")
    assert classes == ["hl-pink", ""]


CORPUS = [
    "outcome ~61% infection (~1% spreader-fail, ~38% target-fail) done\n",
    "versus (~4.5x on pass). Cost was\n**$4.39/run (~$24 each)**, all\n",
    "---\ntype: t\n---\nlead prose [[Folded|x]]\n\n## Head\n\nbody [[A|b]] and `[[in code]]` and ![[Embed]]\n",
    "﻿---\ntitle: bom\n---\n# H\none\ntwo  \nthree\\\nfour\n",
    "no heading at all\njust lines\n\n> quote [[Q]]\n> > nested\n",
    "- point\n\n   > [!stale] External caveat\n   > body line\n",
    "> - one **two**\n>   three\n>\n> - four\n\n| k | v |\n|---|---|\n| a \\| b | c [[L|l]] |\n",
    '<div class="x">\nraw <b>html</b> &hellip; &alpha; text\n</div>\n\n1. one\n2. two ~~gone~~ *em*\n',
    "# Title\n```\n# not a heading\n```\ntext after [[x|y|z]] [[a]]]\n",
]


@pytest.mark.parametrize("n", range(len(CORPUS)))
def test_the_core_reads_a_page_exactly_as_the_renderer_shows_it(page, n):
    diff = page.evaluate("""(src) => {
        const body = new DOMParser().parseFromString('<!doctype html><body>' + renderWikiMarkdown(src, { wikilinks: true }), 'text/html').body;
        wikiMarks.cleanMarks(body);
        const dom = wikiMarks._internals.stream(body).chars;
        const core = wikiMarksCore.squash(wikiMarksCore.project(src, wikiMarksProfile())).text;
        return dom === core ? '' : `dom : ${dom}\\ncore: ${core}`;
    }""", CORPUS[n])
    assert diff == ""
