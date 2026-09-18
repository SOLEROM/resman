"""wiki_highlights — colored highlights stored in the page's own markdown.

A highlight is `<mark class="hl-COLOR">…</mark>` inline in the .md file. The
module's promise, pinned here: an operation only ever adds, removes or
recolors such a tag pair — the text with the marks stripped is identical
before and after — and the file is rewritten byte-for-byte otherwise.
"""
from __future__ import annotations

import os
import stat

import pytest

from modules import wiki_highlights as wh

Y = '<mark class="hl-yellow">'
G = '<mark class="hl-green">'
END = "</mark>"


def span(text: str, needle: str) -> tuple[int, int]:
    start = text.index(needle)
    return start, start + len(needle)


def add(text: str, needle: str, color: str = "yellow", **kw) -> str:
    start, end = span(text, needle)
    return wh.add(text, start, end, color, **kw)


# ----- add -----
def test_add_wraps_the_exact_range():
    text = "Attention is computed over all pairs, which costs a lot.\n"
    assert add(text, "computed over all pairs") == \
        f"Attention is {Y}computed over all pairs{END}, which costs a lot.\n"


def test_add_never_changes_the_text_under_the_marks():
    text = "# Title\n\nOne **bold** sentence with a [[Link|alias]].\n"
    out = add(text, "**bold** sentence", "pink")
    assert wh.strip_marks(out) == text


def test_add_hugs_the_text_not_the_whitespace_around_it():
    text = "one two three\n"
    start, end = span(text, " two ")
    assert wh.add(text, start, end, "blue") == 'one <mark class="hl-blue">two</mark> three\n'


def test_add_at_the_start_of_a_heading_and_a_list_item_stays_inline():
    # The tag is followed by text, so CommonMark never reads it as an HTML block.
    assert add("## Results\n", "Results") == f"## {Y}Results{END}\n"
    assert add("- first point\n- second\n", "first point") == f"- {Y}first point{END}\n- second\n"


def test_add_may_span_a_soft_line_break_inside_one_paragraph():
    text = "> quoted line one\n> line two\n"
    out = add(text, "one\n> line")
    assert out == f"> quoted line {Y}one\n> line{END} two\n"


def test_add_counts_offsets_in_code_points():
    text = "🔴 flag and word\n"
    assert add(text, "word", "green") == f"🔴 flag and {G}word{END}\n"


@pytest.mark.parametrize("color", ["", "red", "yellow\" onclick=\"x", "hl-yellow", None])
def test_add_rejects_a_color_outside_the_palette(color):
    with pytest.raises(wh.HighlightError) as exc:
        wh.add("some text\n", 0, 4, color)
    assert exc.value.status == 400


@pytest.mark.parametrize("start,end", [(-1, 3), (3, 3), (5, 2), (0, 999), ("0", 3), (0, None), (True, 3)])
def test_add_rejects_bad_offsets(start, end):
    with pytest.raises(wh.HighlightError) as exc:
        wh.add("some text\n", start, end, "yellow")
    assert exc.value.status == 400


def test_add_rejects_whitespace_only():
    with pytest.raises(wh.HighlightError):
        wh.add("a   b\n", 1, 4, "yellow")


def test_add_checks_the_expected_text():
    text = "alpha beta gamma\n"
    start, end = span(text, "beta")
    assert END in wh.add(text, start, end, "yellow", expect="beta")
    with pytest.raises(wh.HighlightError) as exc:
        wh.add(text, start, end, "yellow", expect="bet")
    assert exc.value.status == 409


def test_add_refuses_to_span_paragraphs():
    text = "first para\n\nsecond para\n"
    with pytest.raises(wh.HighlightError) as exc:
        wh.add(text, 0, len(text) - 1, "yellow")
    assert exc.value.status == 422


@pytest.mark.parametrize("text,needle", [
    ("intro\n\n```python\nprint('hi')\n```\n", "print"),
    ("intro\n\n~~~\nplain fence\n~~~\n", "plain"),
    ("---\ntitle: Paper\n---\nbody\n", "Paper"),
    ("use `the_code` here\n", "the_co"),
    ("a <!-- hidden note --> b\n", "hidden"),
])
def test_add_refuses_code_frontmatter_and_comments(text, needle):
    with pytest.raises(wh.HighlightError) as exc:
        add(text, needle)
    assert exc.value.status == 422


def test_add_may_wrap_a_whole_code_span():
    assert add("call `fit()` twice\n", "call `fit()` twice") == f"{Y}call `fit()` twice{END}\n"


def test_add_refuses_a_boundary_inside_a_tag():
    text = 'see <span title="a b">x</span> there\n'
    start = text.index("a b")
    with pytest.raises(wh.HighlightError) as exc:
        wh.add(text, start, start + 3, "yellow")
    assert exc.value.status == 422


def test_add_over_part_of_a_highlight_paints_over_all_of_it():
    text = f"one {Y}two three{END} four five\n"
    out = add(text, "three</mark> four", "green")
    assert out == f"one {G}two three four{END} five\n"


def test_add_inside_a_highlight_recolors_it():
    text = f"one {Y}two three{END} four\n"
    assert add(text, "three", "green") == f"one {G}two three{END} four\n"


def test_add_swallows_the_highlights_it_covers():
    text = f"a {Y}b{END} c {G}d{END} e\n"
    out = wh.add(text, 0, len(text) - 1, "pink")
    assert out == '<mark class="hl-pink">a b c d e</mark>\n'


def test_add_leaves_a_foreign_mark_alone():
    text = "plain <mark>hand made</mark> and more\n"
    out = add(text, "more")
    assert out == f"plain <mark>hand made</mark> and {Y}more{END}\n"


# ----- find / remove / recolor -----
def test_find_marks_in_document_order_with_colors():
    text = f"{Y}a{END} and {G}b{END}\n"
    marks = wh.find_marks(text)
    assert [m.color for m in marks] == ["yellow", "green"]
    first = marks[0]
    assert text[first.open_start:first.open_end] == Y
    assert text[first.close_start:first.close_end] == END


def test_find_marks_reads_hand_written_variants():
    text = "<mark class='hl-blue'>a</mark> <MARK CLASS=\"note hl-pink\">b</MARK> <mark class=hl-green>c</mark>\n"
    assert [m.color for m in wh.find_marks(text)] == ["blue", "pink", "green"]


def test_find_marks_skips_foreign_masked_and_unpaired():
    text = ("<mark>plain</mark> <mark class=\"hl-red\">not ours</mark>\n\n"
            f"```\n{Y}in a fence{END}\n```\n\n"
            f"`{Y}in code{END}` <!-- {Y}in a comment{END} -->\n\n"
            f"{G}real{END} and an unpaired {Y}open\n")
    assert [m.color for m in wh.find_marks(text)] == ["green"]


def test_remove_drops_only_that_pair():
    text = f"{Y}a{END} and {G}b{END}\n"
    assert wh.remove(text, 1) == f"{Y}a{END} and b\n"
    assert wh.remove(text, 0) == f"a and {G}b{END}\n"


def test_recolor_rewrites_the_open_tag_canonically():
    text = "<mark class='note hl-blue'>a</mark>\n"
    assert wh.recolor(text, 0, "orange") == '<mark class="hl-orange">a</mark>\n'


@pytest.mark.parametrize("index", [-1, 1, "0", None, True])
def test_remove_and_recolor_reject_a_bad_index(index):
    text = f"{Y}a{END}\n"
    with pytest.raises(wh.HighlightError):
        wh.remove(text, index)
    with pytest.raises(wh.HighlightError):
        wh.recolor(text, index, "green")


def test_strip_marks_removes_ours_only():
    text = f"<mark>keep</mark> {Y}x{END} `{Y}code{END}`\n"
    assert wh.strip_marks(text) == f"<mark>keep</mark> x `{Y}code{END}`\n"


def test_nested_highlights_are_both_found_and_removed_cleanly():
    text = f"{Y}a {G}b{END} c{END}\n"
    marks = wh.find_marks(text)
    assert [m.color for m in marks] == ["yellow", "green"]
    assert wh.remove(text, 0) == f"a {G}b{END} c\n"
    assert wh.strip_marks(text) == "a b c\n"


# ----- files -----
def _op_add(view: str, needle: str, color: str = "yellow") -> dict:
    start, end = span(view, needle)
    return {"op": "add", "start": start, "end": end, "color": color, "expect": needle}


def test_apply_to_file_writes_and_returns_the_new_view(tmp_path):
    page = tmp_path / "p.md"
    page.write_text("alpha beta gamma\n")
    base = wh.content_sha("alpha beta gamma\n")
    out = wh.apply_to_file(page, _op_add("alpha beta gamma\n", "beta"), base)
    assert out["content"] == f"alpha {Y}beta{END} gamma\n"
    assert page.read_text() == out["content"]
    assert out["sha"] == wh.content_sha(out["content"])


def test_dry_run_leaves_the_file_alone(tmp_path):
    page = tmp_path / "p.md"
    page.write_text("alpha beta\n")
    out = wh.apply_to_file(page, _op_add("alpha beta\n", "beta"), wh.content_sha("alpha beta\n"),
                           dry_run=True)
    assert END in out["content"]
    assert page.read_text() == "alpha beta\n"


def test_a_stale_sha_is_a_conflict_and_nothing_is_written(tmp_path):
    page = tmp_path / "p.md"
    page.write_text("alpha beta\n")
    with pytest.raises(wh.HighlightError) as exc:
        wh.apply_to_file(page, _op_add("alpha beta\n", "beta"), wh.content_sha("older text\n"))
    assert exc.value.status == 409
    assert page.read_text() == "alpha beta\n"


def test_crlf_files_stay_byte_for_byte_outside_the_tags(tmp_path):
    page = tmp_path / "p.md"
    raw = b"# T\r\n\r\nalpha beta\r\nlone\nend\r\n"
    page.write_bytes(raw)
    view = "# T\n\nalpha beta\nlone\nend\n"
    wh.apply_to_file(page, _op_add(view, "beta"), wh.content_sha(view))
    assert page.read_bytes() == b"# T\r\n\r\nalpha " + Y.encode() + b"beta" + END.encode() + b"\r\nlone\nend\r\n"
    # and back again
    view2 = wh.to_view(page.read_bytes().decode())
    wh.apply_to_file(page, {"op": "remove", "index": 0}, wh.content_sha(view2))
    assert page.read_bytes() == raw


def test_the_file_mode_survives_and_no_temp_file_is_left(tmp_path):
    page = tmp_path / "p.md"
    page.write_text("alpha beta\n")
    os.chmod(page, 0o640)
    wh.apply_to_file(page, _op_add("alpha beta\n", "beta"), wh.content_sha("alpha beta\n"))
    assert stat.S_IMODE(page.stat().st_mode) == 0o640
    assert sorted(p.name for p in tmp_path.iterdir()) == ["p.md"]


def test_invalid_utf8_is_refused_not_rewritten(tmp_path):
    page = tmp_path / "p.md"
    page.write_bytes(b"caf\xe9 beta\n")
    view = page.read_text(encoding="utf-8", errors="replace")
    with pytest.raises(wh.HighlightError) as exc:
        wh.apply_to_file(page, _op_add(view, "beta"), wh.content_sha(view))
    assert exc.value.status == 422
    assert page.read_bytes() == b"caf\xe9 beta\n"


@pytest.mark.parametrize("op", [
    {}, {"op": "paint"}, {"op": "add"}, {"op": "remove"}, {"op": "recolor", "index": 0},
    {"op": "add", "start": 0, "end": 5, "color": "yellow", "expect": 7},
])
def test_malformed_ops_are_400(tmp_path, op):
    page = tmp_path / "p.md"
    page.write_text(f"{Y}alpha{END} beta\n")
    with pytest.raises(wh.HighlightError) as exc:
        wh.apply_to_file(page, op, wh.content_sha(page.read_text()))
    assert exc.value.status == 400


def test_recolor_and_remove_through_the_file_api(tmp_path):
    page = tmp_path / "p.md"
    page.write_text(f"{Y}alpha{END} beta\n")
    out = wh.apply_to_file(page, {"op": "recolor", "index": 0, "color": "purple"},
                           wh.content_sha(page.read_text()))
    assert out["content"] == '<mark class="hl-purple">alpha</mark> beta\n'
    out = wh.apply_to_file(page, {"op": "remove", "index": 0}, out["sha"])
    assert page.read_text() == "alpha beta\n"


# ----- hostile input stays linear -----
@pytest.mark.parametrize("name,unit", [
    ("unterminated comments", "<!--x"),
    ("mark tags without >", "<mark"),
    ("tags without >", "<a b"),
    ("backtick runs", "`a ``b ```c "),
    ("code spans between marks", '`c` <mark class="hl-yellow">m</mark> '),
    ("crlf lines", "word\r\n"),
])
def test_a_large_hostile_page_is_handled_quickly(tmp_path, name, unit):
    import time
    body = unit * (400_000 // len(unit))
    page = tmp_path / "p.md"
    page.write_bytes((body + "\nplain words here\n").encode())
    view = wh.to_view(page.read_bytes().decode())
    start = view.rindex("plain")
    began = time.monotonic()
    try:
        wh.apply_to_file(page, {"op": "add", "start": start, "end": start + 5, "color": "yellow"},
                         wh.content_sha(view))
    except wh.HighlightError:
        pass                          # refusing is fine; hanging is not
    assert time.monotonic() - began < 3, name


def test_an_oversized_page_is_refused_before_it_is_read(tmp_path, monkeypatch):
    page = tmp_path / "p.md"
    page.write_text("x\n")
    monkeypatch.setattr(wh, "MAX_FILE_BYTES", 1)
    monkeypatch.setattr(type(page), "read_bytes", lambda self: pytest.fail("read before the size check"))
    with pytest.raises(wh.HighlightError) as exc:
        wh.apply_to_file(page, {"op": "remove", "index": 0}, "x")
    assert exc.value.status == 422


def test_backticks_in_separate_quoted_paragraphs_do_not_pair_up():
    text = "> price is ` odd\n>\n> highlight this word normally\n>\n> another ` end\n"
    assert add(text, "word") == f"> price is ` odd\n>\n> highlight this {Y}word{END} normally\n>\n> another ` end\n"
