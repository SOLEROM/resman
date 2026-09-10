"""Tests for the wiki favorites model (modules/wiki_favorites.py).

Favorites live in ``<vault>/.favorites.md`` — a hand-editable markdown list of
vault-relative page paths. The module must round-trip its own lines, tolerate
the formats a human would write, and never clobber lines it doesn't own.
"""
from pathlib import Path

import pytest

from modules import wiki_favorites as fav


@pytest.fixture
def vault(tmp_path):
    """A vault with a small wiki: overview.md, concepts/gguf.md, concepts/cmsis.md."""
    root = tmp_path / "vault"
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "wiki" / "overview.md").write_text("# Overview\n\nWelcome.\n")
    (root / "wiki" / "concepts" / "gguf.md").write_text("# GGUF\n\nA tensor format.\n")
    (root / "wiki" / "concepts" / "cmsis.md").write_text("---\ntags: [x]\n---\n# CMSIS-NN\n")
    return root


# ----- normalize -----
@pytest.mark.parametrize("raw, expected", [
    ("wiki/concepts/gguf.md", "wiki/concepts/gguf.md"),
    ("  wiki/concepts/gguf.md  ", "wiki/concepts/gguf.md"),
    ("./wiki/concepts/gguf.md", "wiki/concepts/gguf.md"),
    ("wiki/concepts/gguf", "wiki/concepts/gguf.md"),        # no extension → .md
    ("wiki/Page Name", "wiki/Page Name.md"),                # spaces allowed
    ("wiki/concepts/gguf.MD", "wiki/concepts/gguf.MD"),     # keep an existing ext verbatim
    ("wiki//concepts///gguf.md", "wiki/concepts/gguf.md"),  # collapse duplicate slashes
])
def test_normalize_accepts_and_cleans(raw, expected):
    assert fav.normalize(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "   ", "/etc/passwd", "../secret.md", "wiki/../../x.md", "wiki/a/../b.md",
    "wiki/x\n.md", "..", ".",
    # Characters that terminate the [[wikilink]] line the module writes (and
    # that Obsidian forbids in note names) — accepting them would produce a
    # line that can never be parsed back, i.e. a stuck entry.
    "wiki/a|b.md", "wiki/a]]b.md", "wiki/[a].md", "wiki/a#b.md",
])
def test_normalize_rejects_unsafe(raw):
    assert fav.normalize(raw) is None


def test_add_refuses_page_whose_name_breaks_the_link_line(vault):
    (vault / "wiki" / "a|b]]c.md").write_text("# odd\n")
    with pytest.raises(ValueError):
        fav.add(vault, "wiki/a|b]]c.md")
    assert not (vault / fav.FAVORITES_FILE).exists()


def test_rewrite_preserves_file_permissions(vault):
    path = vault / fav.FAVORITES_FILE
    path.write_text("- wiki/overview.md\n")
    path.chmod(0o664)
    fav.add(vault, "wiki/concepts/gguf.md")
    assert path.stat().st_mode & 0o777 == 0o664
    fav.remove(vault, "wiki/overview.md")
    assert path.stat().st_mode & 0o777 == 0o664


def test_new_file_is_group_and_world_readable(vault):
    fav.add(vault, "wiki/overview.md")
    # Matches the unread markers (0o644): a synced/shared vault stays readable.
    assert (vault / fav.FAVORITES_FILE).stat().st_mode & 0o777 == 0o644


# ----- parse -----
def test_parse_accepts_every_human_format():
    text = "\n".join([
        "# Favorites",
        "",
        "Some prose the user typed — not a path, ignored.",
        "<!-- a comment -->",
        "%% obsidian comment %%",
        "wiki/overview.md",                              # bare path
        "- wiki/concepts/gguf.md",                       # bullet + bare
        "* [[wiki/concepts/cmsis.md]]",                  # wikilink
        "- [[wiki/hot|Hot page]]",                       # wikilink alias, no ext
        "1. [Index](wiki/index.md)",                     # markdown link, numbered
        "- [ ] [Log](<wiki/log.md>)",                    # checkbox + angle-bracket link
        "- [Spaced](wiki/Page%20Name.md)",               # url-encoded space
        "- ![[wiki/embed.md]]",                          # embed → plain link
        "- [[wiki/concepts/gguf.md#Section|dup]]",       # heading anchor + duplicate
        "",
    ])
    assert fav.parse(text) == [
        "wiki/overview.md",
        "wiki/concepts/gguf.md",
        "wiki/concepts/cmsis.md",
        "wiki/hot.md",
        "wiki/index.md",
        "wiki/log.md",
        "wiki/Page Name.md",
        "wiki/embed.md",
    ]


def test_parse_skips_unsafe_and_non_path_lines():
    text = "- ../escape.md\n- /abs.md\n- just words\n- [[Bare Page]]\n- ok.md\n"
    # "just words" has no slash and no .md → prose, ignored.
    # "[[Bare Page]]" is a wikilink without a path → normalized to Bare Page.md.
    assert fav.parse(text) == ["Bare Page.md", "ok.md"]


def test_parse_empty():
    assert fav.parse("") == []
    assert fav.parse("# Favorites\n\n") == []


# ----- list / is_favorite -----
def test_list_favorites_without_file_is_empty(vault):
    assert fav.list_favorites(vault) == []
    assert fav.is_favorite(vault, "wiki/overview.md") is False


def test_list_favorites_reads_file_in_order(vault):
    (vault / fav.FAVORITES_FILE).write_text(
        "- [[wiki/concepts/gguf.md|GGUF]]\n- wiki/overview.md\n")
    assert fav.list_favorites(vault) == ["wiki/concepts/gguf.md", "wiki/overview.md"]
    assert fav.is_favorite(vault, "wiki/overview.md") is True
    assert fav.is_favorite(vault, "./wiki/overview")   # normalized before lookup
    assert fav.is_favorite(vault, "wiki/concepts/cmsis.md") is False


# ----- add -----
def test_add_creates_file_with_header_and_wikilink_line(vault):
    assert fav.add(vault, "wiki/concepts/gguf.md") is True
    text = (vault / fav.FAVORITES_FILE).read_text()
    assert text.startswith("# Favorites\n")
    # The written line is an Obsidian wikilink with the page's H1 as alias so
    # the file doubles as a working link list inside Obsidian.
    assert "- [[wiki/concepts/gguf.md|GGUF]]\n" in text
    assert fav.list_favorites(vault) == ["wiki/concepts/gguf.md"]


def test_add_is_idempotent(vault):
    assert fav.add(vault, "wiki/overview.md") is True
    assert fav.add(vault, "wiki/overview.md") is False
    assert fav.add(vault, "./wiki/overview") is False   # same page, other spelling
    assert (vault / fav.FAVORITES_FILE).read_text().count("wiki/overview.md") == 1


def test_add_appends_after_existing_hand_written_lines(vault):
    (vault / fav.FAVORITES_FILE).write_text("# Mine\n\nnotes here\n- wiki/overview.md")
    fav.add(vault, "wiki/concepts/cmsis.md")
    text = (vault / fav.FAVORITES_FILE).read_text()
    assert text.startswith("# Mine\n\nnotes here\n- wiki/overview.md\n")
    assert text.endswith("- [[wiki/concepts/cmsis.md|CMSIS-NN]]\n")
    assert fav.list_favorites(vault) == ["wiki/overview.md", "wiki/concepts/cmsis.md"]


def test_add_uses_stem_when_page_has_no_heading(vault):
    (vault / "wiki" / "plain.md").write_text("no heading here\n")
    fav.add(vault, "wiki/plain.md")
    assert "- [[wiki/plain.md|plain]]\n" in (vault / fav.FAVORITES_FILE).read_text()


def test_add_sanitizes_alias_that_would_break_the_wikilink(vault):
    (vault / "wiki" / "weird.md").write_text("# A | B ]] C\n")
    fav.add(vault, "wiki/weird.md")
    text = (vault / fav.FAVORITES_FILE).read_text()
    assert "- [[wiki/weird.md|A B C]]\n" in text
    assert fav.list_favorites(vault) == ["wiki/weird.md"]


def test_add_strips_single_brackets_from_alias(vault):
    """A lone `[` / `]` in the H1 (e.g. "# [Draft] Overview") would end the
    wikilink early and turn the line into garbage on the next parse."""
    (vault / "wiki" / "draft.md").write_text("# [Draft] Overview\n")
    assert fav.add(vault, "wiki/draft.md") is True
    assert "- [[wiki/draft.md|Draft Overview]]\n" in (vault / fav.FAVORITES_FILE).read_text()
    assert fav.is_favorite(vault, "wiki/draft.md") is True
    assert fav.list_favorites(vault) == ["wiki/draft.md"]


def test_add_refuses_symlink_that_escapes_the_vault(tmp_path, vault):
    """The title lookup must go through the same escape check as page_path():
    a symlink inside the vault must not leak an outside file's heading into
    .favorites.md."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("# TOP SECRET\n")
    (vault / "wiki" / "evil").symlink_to(outside)
    with pytest.raises(ValueError):
        fav.add(vault, "wiki/evil/secret.md")
    assert not (vault / fav.FAVORITES_FILE).exists()


def test_concurrent_adds_do_not_lose_updates(vault):
    import threading
    pages = [f"wiki/p{i}.md" for i in range(20)]
    for rel in pages:
        (vault / rel).write_text(f"# {rel}\n")
    start = threading.Barrier(len(pages))

    def worker(rel):
        start.wait()
        fav.add(vault, rel)

    threads = [threading.Thread(target=worker, args=(rel,)) for rel in pages]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(fav.list_favorites(vault)) == sorted(pages)


def test_crlf_file_keeps_its_line_endings(vault):
    path = vault / fav.FAVORITES_FILE
    path.write_bytes(b"# Favorites\r\n\r\n- wiki/overview.md\r\n- wiki/concepts/gguf.md\r\n")
    fav.remove(vault, "wiki/overview.md")
    assert path.read_bytes() == b"# Favorites\r\n\r\n- wiki/concepts/gguf.md\r\n"
    fav.add(vault, "wiki/concepts/cmsis.md")
    assert path.read_bytes().endswith(b"- wiki/concepts/gguf.md\r\n- [[wiki/concepts/cmsis.md|CMSIS-NN]]\r\n")
    assert b"\n" not in path.read_bytes().replace(b"\r\n", b"")


def test_add_rejects_unsafe_path(vault):
    with pytest.raises(ValueError):
        fav.add(vault, "../outside.md")
    assert not (vault / fav.FAVORITES_FILE).exists()


# ----- remove -----
def test_remove_drops_only_matching_lines_and_keeps_the_rest_verbatim(vault):
    original = ("# Favorites\n\n<!-- keep me -->\n- [[wiki/overview.md|Overview]]\n"
                "- [GGUF](wiki/concepts/gguf.md)\n- wiki/concepts/cmsis.md\n")
    (vault / fav.FAVORITES_FILE).write_text(original)
    assert fav.remove(vault, "wiki/concepts/gguf") is True   # normalized match
    assert (vault / fav.FAVORITES_FILE).read_text() == (
        "# Favorites\n\n<!-- keep me -->\n- [[wiki/overview.md|Overview]]\n"
        "- wiki/concepts/cmsis.md\n")
    assert fav.list_favorites(vault) == ["wiki/overview.md", "wiki/concepts/cmsis.md"]


def test_remove_absent_is_noop(vault):
    assert fav.remove(vault, "wiki/overview.md") is False   # no file at all
    (vault / fav.FAVORITES_FILE).write_text("- wiki/overview.md\n")
    assert fav.remove(vault, "wiki/concepts/gguf.md") is False
    assert (vault / fav.FAVORITES_FILE).read_text() == "- wiki/overview.md\n"


def test_remove_rejects_unsafe_path(vault):
    with pytest.raises(ValueError):
        fav.remove(vault, "/abs.md")


def test_writes_are_atomic_and_leave_no_temp_files(vault):
    fav.add(vault, "wiki/overview.md")
    fav.remove(vault, "wiki/overview.md")
    leftovers = [p.name for p in vault.iterdir() if "favorites" in p.name and p.name != fav.FAVORITES_FILE]
    assert leftovers == []


# ----- entries (what the API returns) -----
def test_entries_carry_title_and_existence(vault):
    (vault / fav.FAVORITES_FILE).write_text(
        "- wiki/concepts/cmsis.md\n- wiki/gone.md\n- wiki/overview.md\n")
    rows = fav.entries(vault)
    assert rows == [
        {"file": "wiki/concepts/cmsis.md", "title": "CMSIS-NN", "exists": True},
        {"file": "wiki/gone.md", "title": "gone", "exists": False},
        {"file": "wiki/overview.md", "title": "Overview", "exists": True},
    ]


def test_entries_without_file(vault):
    assert fav.entries(vault) == []
