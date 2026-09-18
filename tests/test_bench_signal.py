"""The footer's `resman` item asks the mainBench shell for its app bar.

The item is the window-gate indicator (green = active window, red = ended).
Docked in the shell it also posts the frame signal
(`{bench: 1, type: "toggle-strip"}`) through the embed kit's
`bench-signal.js` (solBench/mainBench/embed — a copy-in, never edited
here). Opened on its own there is no shell to ask, so the markup ships it
as a plain indicator. Unlike the sibling apps the vault-tree toggle did not
move to the brand — resman's brand is its Home button — it stays on the
active-view tab.

Source-level contracts; the kit's behavior is tested where it lives.
"""
import re
from pathlib import Path

from tests.conftest import solbench_home

ROOT = Path(__file__).resolve().parent.parent
CONTROL_PLANE = ROOT / "control-plane"


def _read(rel: str) -> str:
    path = CONTROL_PLANE / rel
    assert path.is_file(), f"{rel} is missing"
    return path.read_text(encoding="utf-8")


def _tag(html: str, element_id: str) -> str:
    match = re.search(r"<[a-z]+\b[^>]*\bid=\"%s\"[^>]*>" % re.escape(element_id), html)
    assert match, f"#{element_id} not found in the template"
    return match.group(0)


def test_kit_copy_is_not_forked():
    kit = solbench_home() / "mainBench" / "embed" / "bench-signal.js"
    assert (CONTROL_PLANE / "static/js/bench-signal.js").read_bytes() == kit.read_bytes(), \
        "re-run solBench/mainBench/embed/install.sh — never edit the copy"


def test_the_item_ships_as_a_plain_indicator():
    tag = _tag(_read("templates/index.html"), "win-state-item")
    assert "statusbar-item remote" in tag
    assert "role=" not in tag and "tabindex=" not in tag
    assert "Window gate state" in tag, "it still says what the colour means"
    assert "click" not in tag, "no promise of an action the standalone app lacks"


def test_the_item_has_one_action_the_bench_signal():
    js = _read("static/js/app.js")
    assert "if (window.benchSignal) {" in js, \
        "guarded: a page without the kit file must still boot"
    assert "benchSignal.setupItem(winItem, { title: winItem && winItem.title });" in js, \
        "the gate-state tooltip stays ahead of the kit's hint"
    assert 'toggleOnActivate($("#win-state-item"))' not in js


def test_the_vault_tree_toggle_stays_reachable():
    js = _read("static/js/app.js")
    assert 'toggleOnActivate($("#active-view-tab"));' in js
    tag = _tag(_read("templates/index.html"), "header-brand")
    assert "Home" in tag, "the brand is still the Home button"


def test_the_kit_loads_before_the_app_script():
    html = _read("templates/index.html")
    assert html.count("/static/js/bench-signal.js") == 1
    assert html.index("/static/js/bench-signal.js") < html.index("/static/js/app.js")


def test_a_dead_item_does_not_look_clickable():
    css = _read("static/css/style.css")
    rules = re.findall(r"\.statusbar-item\.remote:not\(\.is-live\)[^{]*\{([^}]*)\}", css)
    assert any("cursor: default" in body for body in rules)
    assert any("background:" in body for body in rules)
