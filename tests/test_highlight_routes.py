"""Reader highlights on the Wiki tab — the only route that writes a wiki page.

The store is modules/wiki_highlights.py (tests/test_wiki_highlights.py); these
pin what the API lets through: which files are writable, the sha handshake with
the page GET, dry-run, CSRF, and that the reader's own edit leaves the page's
read/unread state alone.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from modules import wiki_unread
from tests.test_routes import make_test_app

CSRF = {"X-Requested-With": "resman"}
HL = "/api/vaults/alpha/wiki/highlight"
PAGE = "wiki/concepts/attention.md"
TEXT = "---\ntype: concept\n---\n# Attention\n\nEvery token looks at every other token.\n"
MARKED = TEXT.replace("looks at", '<mark class="hl-green">looks at</mark>')


@pytest.fixture
def app_vault(tmp_path):
    app, ctx, _ = make_test_app(tmp_path)
    from modules.routes_highlights import bp as highlights_bp
    app.register_blueprint(highlights_bp)
    vault = Path(ctx["vault_registry"].get("alpha").path)
    (vault / "wiki" / "concepts").mkdir(parents=True)
    (vault / "wiki" / "overview.md").write_text("# Overview\n")
    (vault / PAGE).write_text(TEXT)
    (vault / "CLAUDE.md").write_text("# vault\n")
    (vault / "wiki" / ".hidden.md").write_text("# hidden\n")
    (vault / "wiki" / "hint.json").write_text("{}")
    return app.test_client(), vault


def get_page(client, path=PAGE):
    return client.get(f"/api/vaults/alpha/wiki?file={path}").get_json()


def add_body(page: dict, needle: str, color: str = "green", **extra) -> dict:
    start = page["content"].index(needle)
    return {"file": page["file"], "base_sha": page["sha"], "op": "add", "start": start,
            "end": start + len(needle), "expect": needle, "color": color, **extra}


def test_page_payload_carries_sha_and_highlightable(app_vault):
    client, _vault = app_vault
    page = get_page(client)
    assert page["highlightable"] is True and len(page["sha"]) == 64
    assert get_page(client, "CLAUDE.md")["highlightable"] is False
    assert get_page(client, "wiki/.hidden.md")["highlightable"] is False
    assert get_page(client, "wiki/hint.json")["highlightable"] is False
    assert get_page(client, "wiki/../CLAUDE.md")["highlightable"] is False


def test_add_writes_the_mark_and_returns_the_fresh_page(app_vault):
    client, vault = app_vault
    res = client.post(HL, headers=CSRF, json=add_body(get_page(client), "looks at"))
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["content"] == MARKED and (vault / PAGE).read_text() == MARKED
    assert body["file"] == PAGE and body["highlightable"] is True
    assert body["sha"] == get_page(client)["sha"]


def test_the_client_path_key_is_accepted_too(app_vault):
    client, vault = app_vault
    body = add_body(get_page(client), "looks at")
    body["path"] = body.pop("file")
    assert client.post(HL, headers=CSRF, json=body).status_code == 200
    assert (vault / PAGE).read_text() == MARKED


def test_dry_run_returns_the_result_but_writes_nothing(app_vault):
    client, vault = app_vault
    res = client.post(HL, headers=CSRF, json=add_body(get_page(client), "looks at", dry_run=True))
    assert res.status_code == 200
    assert res.get_json()["content"] == MARKED and res.get_json()["dry_run"] is True
    assert (vault / PAGE).read_text() == TEXT


def test_remove_and_recolor_by_index(app_vault):
    client, vault = app_vault
    (vault / PAGE).write_text(MARKED)
    page = get_page(client)
    res = client.post(HL, headers=CSRF, json={
        "file": PAGE, "base_sha": page["sha"], "op": "recolor", "index": 0, "color": "pink"})
    assert 'class="hl-pink"' in (vault / PAGE).read_text()
    res = client.post(HL, headers=CSRF, json={
        "file": PAGE, "base_sha": res.get_json()["sha"], "op": "remove", "index": 0})
    assert res.status_code == 200 and (vault / PAGE).read_text() == TEXT


def test_csrf_header_is_required(app_vault):
    client, vault = app_vault
    res = client.post(HL, json=add_body(get_page(client), "looks at"))
    assert res.status_code == 403 and (vault / PAGE).read_text() == TEXT


def test_a_stale_sha_is_409(app_vault):
    client, vault = app_vault
    body = add_body(get_page(client), "looks at")
    (vault / PAGE).write_text(TEXT + "\nAn agent appended this.\n")
    res = client.post(HL, headers=CSRF, json=body)
    assert res.status_code == 409 and "changed" in res.get_json()["error"]
    assert "<mark" not in (vault / PAGE).read_text()


@pytest.mark.parametrize("path", [
    "CLAUDE.md", "wiki/../CLAUDE.md", "../outside.md", "/etc/passwd", "wiki/.hidden.md",
    "wiki/hint.json", "wiki/missing.md", "wiki", "wiki/concepts",
])
def test_only_visible_wiki_markdown_pages_are_writable(app_vault, path):
    client, vault = app_vault
    res = client.post(HL, headers=CSRF, json={
        "file": path, "base_sha": "0" * 64, "op": "add", "start": 0, "end": 3, "color": "yellow"})
    assert res.status_code in (400, 404), res.get_json()
    assert "<mark" not in (vault / "CLAUDE.md").read_text()
    assert "<mark" not in (vault / "wiki" / ".hidden.md").read_text()


@pytest.mark.parametrize("body", [[], {"file": 5}, {"file": PAGE},
                                  {"file": PAGE, "base_sha": 7, "op": "add"}])
def test_malformed_bodies_are_400(app_vault, body):
    client, _vault = app_vault
    assert client.post(HL, headers=CSRF, json=body).status_code == 400


def test_refusals_carry_the_store_status_and_message(app_vault):
    client, _vault = app_vault
    page = get_page(client)
    res = client.post(HL, headers=CSRF, json={
        "file": PAGE, "base_sha": page["sha"], "op": "add", "start": 0, "end": 12, "color": "yellow"})
    assert res.status_code == 422, res.get_json()


def test_unknown_vault_is_404(app_vault):
    client, _vault = app_vault
    assert client.post("/api/vaults/nope/wiki/highlight", headers=CSRF, json={}).status_code == 404


# ----- read/unread state survives the reader's own edit -----
def tree_unread(client) -> dict:
    flags = {}

    def walk(nodes):
        for n in nodes:
            if n["type"] == "file":
                flags[n["path"]] = n["unread"]
            else:
                walk(n.get("children", []))
    walk(client.get("/api/vaults/alpha/wiki/tree").get_json()["tree"])
    return flags


def test_a_highlight_does_not_make_a_read_page_unread(app_vault):
    client, vault = app_vault
    tree_unread(client)                                   # first scan: all unread
    client.post("/api/vaults/alpha/wiki/read", headers=CSRF, json={"file": PAGE, "read": True})
    client.post("/api/vaults/alpha/wiki/read", headers=CSRF, json={"file": "wiki/overview.md", "read": True})
    time.sleep(0.05)
    (vault / "wiki" / "overview.md").write_text("# Overview\n\nsynced from elsewhere\n")
    res = client.post(HL, headers=CSRF, json=add_body(get_page(client), "looks at"))
    assert res.status_code == 200
    flags = tree_unread(client)
    assert flags[PAGE] is False                           # the reader's edit: still read
    assert flags["wiki/overview.md"] is True              # a real change elsewhere: flagged


def test_a_highlight_keeps_an_unread_page_unread(app_vault):
    client, _vault = app_vault
    assert tree_unread(client)[PAGE] is True
    time.sleep(0.05)
    assert client.post(HL, headers=CSRF, json=add_body(get_page(client), "looks at")).status_code == 200
    assert tree_unread(client)[PAGE] is True


def test_a_dry_run_touches_no_unread_marker(app_vault):
    client, vault = app_vault
    tree_unread(client)
    client.post("/api/vaults/alpha/wiki/read", headers=CSRF, json={"file": PAGE, "read": True})
    baseline = (vault / "wiki" / wiki_unread.BASELINE).stat().st_mtime
    client.post(HL, headers=CSRF, json=add_body(get_page(client), "looks at", dry_run=True))
    assert (vault / "wiki" / wiki_unread.BASELINE).stat().st_mtime == baseline
    assert tree_unread(client)[PAGE] is False
