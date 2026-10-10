"""Narrow-screen layout: the navbar never overflows (the sub-page row scrolls sideways at any width; on the phone the groups move to a fixed bottom bar
the body pads for), and the filter chips wrap instead of scrolling inside a row that cannot shrink. Checked on the stylesheet (no browser in the suite); the widths themselves were measured on every page
at 360-1440 px when this was written."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "style.css").read_text()


def block(media: str) -> str:
    """The bodies of every `@media (max-width: <media>px) { ... }` block of the stylesheet, joined."""
    out = []
    for m in re.finditer(r"@media \(max-width: %spx\) \{" % media, CSS):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(CSS[i], 0)
            i += 1
        out.append(CSS[m.end():i - 1])
    assert out, media
    return "\n".join(out)


def rule(selector: str) -> str:
    """The body of the top-level (outside any @media) rule `selector { ... }`."""
    m = re.search(r"^%s \{([^}]*)\}" % re.escape(selector), CSS, re.M)
    assert m, selector
    return m.group(1)


def test_the_navbar_never_overflows_narrow_screens():
    sub = rule(".nav-sub ul")                                                                                          # the sub-page row: one line, scrolls sideways at any width
    assert "overflow-x: auto" in sub and "display: flex" in sub
    assert "white-space: nowrap" in rule(".nav-sub-link") and "flex: none" in rule(".nav-sub li")
    assert "white-space: nowrap" in rule(".nav-group")
    b = block("600")                                                                                                   # the phone: groups in a fixed bottom bar, 3 equal columns
    for needle in (".nav-groups {", "position: fixed", "bottom: 0", "env(safe-area-inset-bottom, 0px)", "grid-template-columns: repeat(3, 1fr)",
                   "min-height: 44px", "padding-bottom: calc(var(--tabbar-h) + env(safe-area-inset-bottom, 0px))"):
        assert needle in b, needle
    assert ".nav-" not in block("900")                                                                                 # the two-row 900 px navbar is gone
    assert "@media (max-width: 640px) {\n  .nav-container" not in CSS                                                    # the old breakpoint left 641-866 px overflowing


def test_the_filter_chips_wrap_on_narrow_screens_and_override_the_nowrap_rules():
    b = block("768")                                                                                                   # the last 768 px block is the wrap rule
    for sel in (".controls", ".cat-filter", ".cat-chips", ".econ-controls .cat-chips", ".retail-controls .cat-chips"):
        assert sel in b, sel
    assert "flex-wrap: wrap" in b and "overflow-x: visible" in b and "min-width: 0" in b
    assert CSS.rindex("@media (max-width: 768px)") > CSS.rindex(".econ-controls .cat-chips { overflow-x: auto")           # last in the file: it wins over the nowrap rules it overrides
    assert "flex-wrap: nowrap" in CSS                                                                                  # (those rules still exist, above 768 px they do not apply)


def test_the_served_stylesheet_is_the_source():
    assert (ROOT / "public" / "style.css").read_text() == CSS
