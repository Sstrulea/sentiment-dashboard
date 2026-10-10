"""Narrow-screen layout: the navbar never overflows (the segmented page switch scrolls sideways at any width; on the phone the groups are a
3-column category bar under the logo row), and the filter chips wrap instead of scrolling inside a row that cannot shrink. Checked on the stylesheet (no browser in the suite); the widths themselves were measured on every page
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
    seg = rule(".nav-seg")                                                                                             # the page switch: one line, scrolls sideways at any width
    assert "overflow-x: auto" in seg and "display: inline-flex" in seg and "max-width: 100%" in seg
    assert "white-space: nowrap" in rule(".nav-sub-link") and "flex: none" in rule(".nav-seg li")
    assert "white-space: nowrap" in rule(".nav-group")
    assert ".nav-fit-1 .nav-brand-name" in CSS and ".nav-fit-2 .nav-groups .nav-icon { display: none; }" in CSS        # 601-767 px: name, then icons go
    js = (ROOT / "static" / "nav.js").read_text()
    assert js.index('add("nav-fit-1")') < js.index('add("nav-fit-2")')
    b = block("600")                                                                                                   # the phone: a 3-column category bar, 44px+ targets
    for needle in (".nav-groups {", "grid-template-columns: repeat(3, 1fr)", "min-height: 44px",
                   ".nav-seg { display: flex; width: 100%; }", "flex: 1 1 0; min-width: max-content;", "height: 40px"):
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
