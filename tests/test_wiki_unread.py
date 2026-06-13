"""Tests for the wiki read/unread marker model (modules/wiki_unread.py)."""
import time
from pathlib import Path

import pytest

from modules import wiki_unread


@pytest.fixture
def wiki(tmp_path):
    """A small wiki tree:  overview.md, concepts/gguf.md, concepts/cmsis.md."""
    root = tmp_path / "wiki"
    (root / "concepts").mkdir(parents=True)
    (root / "overview.md").write_text("# Overview\n\nWelcome to the vault.\n")
    (root / "concepts" / "gguf.md").write_text("# GGUF\n\nA tensor file format.\n")
    (root / "concepts" / "cmsis.md").write_text("# CMSIS-NN\n\nNeural network kernels.\n")
    return root


def test_first_reconcile_marks_everything_unread(wiki):
    unread = wiki_unread.reconcile(wiki)
    assert unread == {"overview.md", "concepts/gguf.md", "concepts/cmsis.md"}
    # Markers are sidecar dotfiles next to each page.
    assert (wiki / ".overview.unrd").exists()
    assert (wiki / "concepts" / ".gguf.unrd").exists()
    # Baseline file was created.
    assert (wiki / wiki_unread.BASELINE).exists()


def test_mark_read_removes_marker_and_sticks_across_reconcile(wiki):
    wiki_unread.reconcile(wiki)
    assert wiki_unread.mark_read(wiki, "concepts/gguf.md") is True
    assert not (wiki / "concepts" / ".gguf.unrd").exists()
    assert wiki_unread.is_unread(wiki, "concepts/gguf.md") is False
    # A second reconcile must not resurrect a read page (ctime older than baseline).
    unread = wiki_unread.reconcile(wiki)
    assert "concepts/gguf.md" not in unread


def test_mark_unread_recreates_marker(wiki):
    wiki_unread.reconcile(wiki)
    wiki_unread.mark_read(wiki, "overview.md")
    assert wiki_unread.is_unread(wiki, "overview.md") is False
    assert wiki_unread.mark_unread(wiki, "overview.md") is True
    assert wiki_unread.is_unread(wiki, "overview.md") is True


def test_reconcile_flags_newly_added_page(wiki):
    # First reconcile stamps the baseline at "now"; the existing fixture
    # pages were created just before, so their ctime predates the baseline.
    wiki_unread.reconcile(wiki)
    for rel in ("overview.md", "concepts/gguf.md", "concepts/cmsis.md"):
        wiki_unread.mark_read(wiki, rel)
    # A page created strictly after the baseline must surface as unread.
    time.sleep(0.05)
    (wiki / "newpage.md").write_text("# New\n\nFresh content.\n")
    unread = wiki_unread.reconcile(wiki)
    assert "newpage.md" in unread
    # Untouched, already-read pages stay read (ctime older than baseline).
    assert "overview.md" not in unread


def test_reconcile_prunes_orphan_markers(wiki):
    wiki_unread.reconcile(wiki)
    (wiki / "concepts" / "gguf.md").unlink()
    wiki_unread.reconcile(wiki)
    assert not (wiki / "concepts" / ".gguf.unrd").exists()


def test_pick_random_unread_returns_member_or_none(wiki):
    pick = wiki_unread.pick_random_unread(wiki)
    assert pick in {"overview.md", "concepts/gguf.md", "concepts/cmsis.md"}
    for rel in ("overview.md", "concepts/gguf.md", "concepts/cmsis.md"):
        wiki_unread.mark_read(wiki, rel)
    assert wiki_unread.pick_random_unread(wiki) is None


def test_path_traversal_is_rejected(wiki):
    assert wiki_unread.mark_read(wiki, "../escape.md") is False
    assert wiki_unread.mark_unread(wiki, "/etc/passwd") is False
    assert wiki_unread.is_unread(wiki, "../../x.md") is False


def test_search_ranks_title_above_body(wiki):
    (wiki / "body-hit.md").write_text("# Misc\n\nThis page mentions gguf once.\n")
    hits = wiki_unread.search(wiki, "gguf")
    rels = [h["rel"] for h in hits]
    assert "concepts/gguf.md" in rels
    # Title match (concepts/gguf.md) outranks the body-only mention.
    assert rels.index("concepts/gguf.md") < rels.index("body-hit.md")
    # Snippet is plain text (highlighting is client-side); it contains the term.
    assert "gguf" in hits[0]["snippet"].lower()
    assert "<mark>" not in hits[0]["snippet"]


def test_search_requires_all_tokens(wiki):
    hits = wiki_unread.search(wiki, "gguf nonexistentword")
    assert hits == []


def test_search_empty_query_returns_nothing(wiki):
    assert wiki_unread.search(wiki, "") == []
    assert wiki_unread.search(wiki, "a") == []  # single char below min length


def test_missing_wiki_dir_is_safe(tmp_path):
    missing = tmp_path / "nope"
    assert wiki_unread.reconcile(missing) == set()
    assert wiki_unread.list_unread(missing) == set()
    assert wiki_unread.pick_random_unread(missing) is None
    assert wiki_unread.search(missing, "x") == []
