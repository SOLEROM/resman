"""cldBar embed — remdev's Claude status bar in resman's footer.

resman was the first consumer of remdev's bar and hand-rolled the embed
before the shared kit existed. It now uses the kit like every other app in
the family (solBench/cldBar): `cldbar.js` is copied into static/js and owns
the iframe; resman owns only *policy* — where the bar mounts, the optional
`app.remdev_url` pin, and how its four themes map onto remdev's slugs.

Note the direction of the dependency: resman is remdev's *data backend*
(window model, schedule, claude.ai probe) but talks to the bar the same way
any embedding app does — through remdev, never to itself.

These tests pin the pitfalls the kit's readme calls out, above all that no
server-rendered `127.0.0.1` reaches the page: an iframe src is fetched by
the *viewer's* browser, so a loopback default renders a blank strip on
every device that is not the station itself.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from modules.config_manager import ConfigError, validate_resman_yaml
from server import build_app

STATIC_JS = Path(__file__).resolve().parents[1] / "control-plane" / "static" / "js"
# Same checkout path run.sh uses for the shared webterm install.
KIT_DIR = Path("/data/proj/agents/solBench/cldBar")


def build_client(tmp_path, app_block: str = ""):
    """A minimal resman whose config carries the given `app:` body."""
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    vault = tmp_path / "alpha"
    (vault / ".obsidian").mkdir(parents=True, exist_ok=True)
    (cfg / "resman.yaml").write_text(
        f"app:\n  port: 5090\n{app_block}vaults:\n"
        f"  - name: alpha\n    path: {vault}\n"
    )
    app, _sio, _ctx = build_app(cfg, async_mode="threading")
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def client(tmp_path):
    return build_client(tmp_path)


# ----- the footer slot -----

def test_the_footer_carries_a_cldbar_mount_slot(client):
    body = client.get("/").get_data(as_text=True)
    assert 'id="cldbar-slot"' in body
    # In the status bar, not somewhere else in the SPA shell.
    footer = body.split('<footer class="statusbar"', 1)[1].split("</footer>", 1)[0]
    assert 'id="cldbar-slot"' in footer


def test_the_kit_helper_and_the_app_glue_are_both_loaded(client):
    body = client.get("/").get_data(as_text=True)
    assert "/static/js/cldbar.js" in body
    assert "/static/js/cldbar-glue.js" in body
    # The kit defines window.cldBar; the glue consumes it.
    assert body.index("/static/js/cldbar.js") < body.index("/static/js/cldbar-glue.js")


# ----- the 127.0.0.1 pitfall (readme: "this bit us") -----

def test_the_page_never_hardcodes_the_station_address(client):
    """With no pin, the origin must be derived browser-side by the kit.

    Comments are stripped first: the markup explains the pitfall by name,
    and only live markup can actually point a viewer at the wrong host.
    """
    body = re.sub(r"<!--.*?-->", "", client.get("/").get_data(as_text=True), flags=re.S)
    assert "6005" not in body
    assert 'data-remdev-url=""' in body


def test_the_glue_derives_the_origin_from_the_viewer_address():
    src = re.sub(r"//.*", "", (STATIC_JS / "cldbar-glue.js").read_text())
    assert "127.0.0.1" not in src
    assert "6005" not in src
    # The pin is read off the slot; with none, the kit derives the origin.
    assert "remdevUrl" in src


def test_the_shipped_example_config_does_not_pin_a_loopback_origin():
    """A pinned http://127.0.0.1:6005 in the example is a trap: copied into a
    live config it points every remote viewer at their *own* machine, and the
    footer goes blank everywhere except the station."""
    example = (Path(__file__).resolve().parents[1]
               / "config" / "resman.yaml.example").read_text()
    live = [ln for ln in example.splitlines()
            if "remdev_url" in ln and not ln.strip().startswith("#")]
    assert live == [], f"example pins remdev_url: {live}"


# ----- the remdev_url pin (app policy) -----

def test_a_pinned_remdev_url_reaches_the_slot(tmp_path):
    client = build_client(tmp_path, "  remdev_url: http://station:6005\n")
    body = client.get("/").get_data(as_text=True)
    assert 'data-remdev-url="http://station:6005"' in body


def test_a_pinned_url_is_stripped_of_its_trailing_slash(tmp_path):
    client = build_client(tmp_path, "  remdev_url: https://station.ts.net/\n")
    body = client.get("/").get_data(as_text=True)
    assert 'data-remdev-url="https://station.ts.net"' in body


@pytest.mark.parametrize("url", ["http://127.0.0.1:6005", "https://box.ts.net"])
def test_config_accepts_an_http_or_https_remdev_url(url):
    validate_resman_yaml({"app": {"remdev_url": url}})


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_an_empty_remdev_url_means_no_pin_rather_than_an_error(blank):
    """A hand-written `remdev_url:` means "derive it" — not a config the app
    refuses to load."""
    validate_resman_yaml({"app": {"remdev_url": blank}})


@pytest.mark.parametrize("bad", ["station:6005", "//station:6005", 6005,
                                 "javascript:alert(1)"])
def test_config_refuses_a_remdev_url_that_is_not_an_http_origin(bad):
    # The value lands in an iframe src, and the kit throws on a bad origin —
    # which would leave a silently empty footer. Fail at config load instead.
    with pytest.raises(ConfigError):
        validate_resman_yaml({"app": {"remdev_url": bad}})


# ----- theming (readme: "the transparency contract") -----

def test_the_glue_maps_every_app_theme_onto_a_remdev_slug():
    """A theme missing from the map would silently fall back to the dark
    slug — a dark opaque strip that is invisible on the dark themes and
    obvious only on Light Modern."""
    app_js = (STATIC_JS / "app.js").read_text()
    themes = re.search(r"const THEMES = \[(.*?)\]", app_js, re.S).group(1)
    app_themes = set(re.findall(r'"([a-z]+)"', themes))

    glue = (STATIC_JS / "cldbar-glue.js").read_text()
    block = re.search(r"CLDBAR_THEME = \{(.*?)\}", glue, re.S).group(1)
    mapped = dict(re.findall(r'(\w+):\s*"([a-z0-9-]+)"', block))

    assert set(mapped) == app_themes
    assert mapped["light"].endswith("light")   # drives the iframe color-scheme


def test_the_app_theme_switch_re_points_the_embed():
    app_js = (STATIC_JS / "app.js").read_text()
    set_theme = app_js.split("function setTheme(", 1)[1].split("\nfunction ", 1)[0]
    assert "syncCldBar" in set_theme


# ----- the copied-in kit file -----

def test_the_kit_copy_is_not_a_local_fork():
    """cldbar.js is a copy-in, refreshed by cldBar/install.sh. A local edit
    (or a stale copy) is drift, not an integration."""
    if not KIT_DIR.exists():
        pytest.skip(f"solBench checkout not at {KIT_DIR}")
    assert (STATIC_JS / "cldbar.js").read_bytes() == (KIT_DIR / "cldbar.js").read_bytes()


def test_the_embed_mechanism_is_not_hand_rolled_a_second_time():
    """The kit owns the iframe. resman kept its own copy of that mechanism
    until this migration; a re-inlined src builder is the drift to catch."""
    app_js = (STATIC_JS / "app.js").read_text()
    assert "/statusbar?theme=" not in app_js
    assert "statusbar-embed" not in app_js
