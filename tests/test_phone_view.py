"""Faza 11: the phone view's shared pieces (the rendered pages are checked with Playwright, outside the suite)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "style.css").read_text()


def test_served_copies_are_in_step():
    for f in ("phone.js", "style.css", "economic-chart.js", "cb.js", "carry.js", "strength.js", "cot.js"):
        assert (ROOT / "public" / f).read_text() == (ROOT / "static" / f).read_text(), f


def test_navbar_carries_the_banner_and_phone_js_and_the_head_decides_the_banner():
    nav = (ROOT / "templates" / "_navbar.html.j2").read_text()
    assert 'class="phone-banner"' in nav and "Best on desktop or tablet." in nav and 'src="/phone.js"' in nav
    assert '<div class="nav-tabs">' in nav
    head = (ROOT / "templates" / "_theme_head.html.j2").read_text()
    assert 'localStorage.getItem("phone-banner-closed")' in head and "banner-closed" in head


def test_no_table_turns_into_stacked_cards():
    assert not re.search(r"table\.retail-table[^{]*\{[^}]*display:\s*block", CSS)
    assert "table.retail-table thead { display: none; }" not in CSS


def test_breakpoints_are_unified():
    # the old phone breakpoints are gone; what stays only tightens the desktop (760 modal grid, 768 chip wrap, 900 nav/strength)
    widths = set(re.findall(r"@media \((?:max|min)-width: (\d+)px\)", CSS))
    assert not widths & {"480", "640", "700", "701", "767"}, widths
    assert "@media (max-width: 600px)" in CSS and "@media (max-width: 900px)" in CSS


def test_every_page_folds_its_long_description():
    for t in ("economic", "carry", "strength", "history", "vix_ratio"):
        assert "data-htr" in (ROOT / "templates" / f"{t}.html.j2").read_text(), t
    assert '"data-htr": true' in (ROOT / "static" / "cot.js").read_text()
    assert "data-htr" in (ROOT / "static" / "cb.js").read_text()


def test_phone_js_builds_dom_without_html_strings():
    js = (ROOT / "static" / "phone.js").read_text()
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js
    assert 'role: "dialog"' in js and '"aria-modal": "true"' in js


def test_ui_text_is_english():
    """The last Romanian UI strings (Strength drilldown) are gone: "not scored", "Revised from ... to ..."."""
    for f in ("strength.js", "economic-chart.js", "cb.js", "cot.js", "carry.js", "phone.js"):
        js = (ROOT / "static" / f).read_text()
        for word in ("nescorat", "favorabil", "Revizuit"):
            assert word not in js, (f, word)
    assert "not scored" in (ROOT / "static" / "strength.js").read_text()


def test_cot_default_order_is_the_score():
    js = (ROOT / "static" / "cot.js").read_text()
    assert 'const isDefault = !state.sort && col.key === "cot";' in js
    assert "function defaultOrder(a, b)" in js
