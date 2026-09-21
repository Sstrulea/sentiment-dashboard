"""Narrow-screen layout: the navbar folds (brand + toggle, links on a scrolling second row) up to the width its single row needs, and the filter chips wrap
instead of scrolling inside a row that cannot shrink. Checked on the stylesheet (no browser in the suite); the widths themselves were measured on every page
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


def test_the_navbar_folds_up_to_the_width_its_single_row_needs():
    b = block("900")
    for needle in (".nav-container", "flex-wrap: wrap", ".nav-links", "overflow-x: auto", "flex: 1 0 100%", "white-space: nowrap"):
        assert needle in b, needle
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
