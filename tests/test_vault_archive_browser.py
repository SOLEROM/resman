"""The sidebar ARCHIVE folder end to end: the header archive button moves the
selected vault out of the tree into a folder under it (above the tag filter),
the folder is folded by default and remembers being opened, and unarchiving
brings the vault back. The flag and route are unit-tested (test_routes.py,
test_config_manager.py); this pins what only a browser shows.

Skipped when Playwright or a headless Chromium shell is unavailable.
"""
from __future__ import annotations

import pytest
import yaml

pw = pytest.importorskip("playwright.sync_api")

from tests.browser_app import chromium_shell, serve  # noqa: E402

CHROME = chromium_shell()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        yield b
        b.close()


@pytest.fixture
def site(tmp_path):
    beta = tmp_path / "beta"
    (beta / ".obsidian").mkdir(parents=True)
    system = (
        "app:\n  host: 127.0.0.1\n  port: 5090\nvaults:\n"
        f"  - name: alpha\n    path: {tmp_path / 'alpha'}\n"
        f"  - name: beta\n    path: {beta}\n    category: old\n    archived: true\n")
    with serve(tmp_path, {"wiki/overview.md": "# Overview\n"},
               config_files={"system.yaml": system}) as (url, _vault):
        yield url, tmp_path / "config" / "system.yaml"


@pytest.fixture
def page(site, browser):
    url, _cfg = site
    page = browser.new_page()
    page.goto(url + "/", wait_until="load")
    page.wait_for_function("() => typeof selectVault === 'function' && state.vaults.length === 2")
    yield page
    page.close()


def archived_in_yaml(cfg) -> dict:
    return {v["name"]: v.get("archived") for v in yaml.safe_load(cfg.read_text())["vaults"]}


def tree_vaults(page, sel):
    return page.evaluate(
        f"() => [...document.querySelectorAll('{sel} .vault-row')].map(r => r.dataset.vault)")


def test_archived_vault_sits_in_a_folded_folder_below_the_tree(page):
    assert tree_vaults(page, "#vault-list") == ["alpha"]
    assert page.is_visible("#archive-section")
    # Folded by default: the header counts it, the rows are not rendered.
    assert page.inner_text("#archive-list .archive-row .cat-count") == "1"
    assert tree_vaults(page, "#archive-list") == []
    # The folder sits between the tree and the tag filter.
    order = page.evaluate("""() => {
        const ids = ['vault-list', 'archive-section', 'vault-tags-bar'];
        return ids.map(id => [...document.querySelector('.sidebar').querySelectorAll('*')]
                              .indexOf(document.getElementById(id)));
    }""")
    assert order == sorted(order)
    page.click("#archive-list .archive-row")
    assert tree_vaults(page, "#archive-list") == ["beta"]
    # Opening is remembered across a reload.
    page.reload(wait_until="load")
    page.wait_for_function("() => typeof state !== 'undefined' && state.vaults.length === 2")
    assert tree_vaults(page, "#archive-list") == ["beta"]


def test_header_button_archives_and_unarchives_the_selected_vault(page, site):
    _url, cfg = site
    page.evaluate("() => selectVault('alpha', { panel: 'wiki' })")
    page.click("#btn-archive")
    page.wait_for_function(
        "() => !document.querySelector('#vault-list .vault-row[data-vault=\"alpha\"]')")
    assert sorted(tree_vaults(page, "#archive-list")) == ["alpha", "beta"]
    assert archived_in_yaml(cfg) == {"alpha": True, "beta": True}
    assert page.evaluate("() => document.querySelector('#btn-archive').classList.contains('active')")
    page.click("#btn-archive")
    page.wait_for_function(
        "() => !!document.querySelector('#vault-list .vault-row[data-vault=\"alpha\"]')")
    assert tree_vaults(page, "#archive-list") == ["beta"]
    assert archived_in_yaml(cfg) == {"alpha": None, "beta": True}


def test_home_hides_archived_vaults_and_counts_them(page):
    page.evaluate("() => showPanel('home')")
    page.wait_for_selector("#landing-grid .vault-card")
    cards = page.evaluate(
        "() => [...document.querySelectorAll('#landing-grid .vault-card')].map(c => c.dataset.vault)")
    assert cards == ["alpha"]
    assert page.inner_text("#landing-count") == "1 vault · 1 archived"


def test_config_checkbox_archives_a_vault(page, site):
    _url, cfg = site
    page.evaluate("() => showPanel('config')")
    page.wait_for_selector('.cfg-card[data-kind="vault"]')
    alpha = '.cfg-card[data-kind="vault"][data-idx="0"]'
    beta = '.cfg-card[data-kind="vault"][data-idx="1"]'
    assert "archived" in page.inner_text(f"{beta} .cfg-card-sub")
    page.click(f"{alpha} .cfg-card-head")
    box = f'{alpha} input[data-vfield="archived"]'
    assert not page.is_checked(box)
    page.check(box)
    assert "archived" in page.inner_text(f"{alpha} .cfg-card-sub")
    page.click("#btn-config-save")
    page.wait_for_function("() => document.querySelector('#btn-config-save').disabled")
    assert archived_in_yaml(cfg) == {"alpha": True, "beta": True}
    page.wait_for_function(
        "() => !document.querySelector('#vault-list .vault-row[data-vault=\"alpha\"]')")
    # Unchecking drops the key again.
    page.uncheck(box)
    page.click("#btn-config-save")
    page.wait_for_function(
        "() => !!document.querySelector('#vault-list .vault-row[data-vault=\"alpha\"]')")
    assert archived_in_yaml(cfg) == {"alpha": None, "beta": True}
