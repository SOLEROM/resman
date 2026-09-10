"""webterm's one host requirement: the viewport must shrink for the keyboard.

The library ships no on-screen key bar — every key a soft keyboard lacks is
translated from a real keyboard instead (solBench/webterm/readme.md § "Every
key a real keyboard sends"), so there is no glue for this app to carry.

What the library still cannot do from inside is ask Chrome to shrink the
*layout* viewport when the soft keyboard opens. Without that, a
fixed-position element — which is what .wt-root becomes in fullscreen —
stays behind the keyboard, hiding the last terminal rows and the line being
typed. Only this template can set it, so this is where it is pinned.
"""
from pathlib import Path
import re

TEMPLATE = Path(__file__).resolve().parent.parent / "control-plane" / "templates" / "index.html"


def test_the_viewport_shrinks_for_the_soft_keyboard():
    html = TEMPLATE.read_text(encoding="utf-8")
    meta = re.search(r"<meta[^>]*name=[\"']viewport[\"'][^>]*>", html)
    assert meta, "no viewport meta tag — the terminal cannot be used on a phone"
    assert "interactive-widget=resizes-content" in meta.group(0), (
        "soft keyboard will cover the bottom terminal rows in fullscreen")
