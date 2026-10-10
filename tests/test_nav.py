"""Grouped navigation: the menu (templates/_nav_config.html.j2, read through Jinja - never duplicated here)
and the navbar every page carries (templates/_navbar.html.j2).

The rendered-page check reads public/ as render-all leaves it (like test_served_copies_are_in_step); the
Central Banks pages come from src.cb_render, not render-all, so they are checked on the template itself,
for every active_page value cb_render passes."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
ENV = Environment(loader=FileSystemLoader(str(ROOT / "templates")))
GROUPS = ENV.get_template("_nav_config.html.j2").module.NAV_GROUPS
PAGES = [(g, p) for g in GROUPS for p in g["pages"]]
VISIBLE = [(g, p) for g, p in PAGES if not p.get("hidden")]


def nav_for(active_page: str) -> str:
    return ENV.get_template("_navbar.html.j2").render(active_page=active_page)


def renderer_active_pages() -> set[str]:
    """Every active_page value a renderer in src/ passes to its template."""
    found = set()
    for f in (ROOT / "src").glob("*.py"):
        found |= set(re.findall(r'active_page="([^"]+)"', f.read_text()))
    return found


def check_nav(html: str, group: str, page_url: str | None) -> None:
    """The navbar of a page: the landmarks, `group` the one active group, `page_url` the one current sub-page."""
    assert html.count('<nav class="nav-groups" aria-label="Sections">') == 1
    assert len(re.findall(r'class="nav-group active"', html)) == 1
    assert re.search(r'class="nav-group active" href="[^"]+" data-nav-group="%s"' % group, html), group
    current = re.findall(r'<a class="nav-sub-link active" href="([^"]+)" aria-current="page">', html)
    assert current == ([page_url] if page_url else []), (group, current)
    assert html.count('aria-current="page"') == len(current)
    assert '<nav class="nav-sub" aria-label=' in html and 'src="/nav.js"' in html


# ---------------------------------------------------------------- the menu


def test_the_menu_is_the_spec():
    shown = [(g["label"], [p["label"] for p in g["pages"] if not p.get("hidden")]) for g in GROUPS]
    assert shown == [
        ("Signal", ["Pairs", "Currency Strength"]),
        ("Macro", ["Carry", "Central Banks", "History"]),
        ("Sentiment", ["COT", "P/C Ratio", "VIX/VIX3M"]),
    ]
    assert GROUPS[0]["pages"][0]["url"] == "/economic"                                                                # "/" -> /economic opens Signal -> Pairs
    assert [g["icon"] for g in GROUPS] == ["target", "bank", "gauge"]


def test_every_visible_page_is_in_exactly_one_group_and_every_key_marks_one_page():
    keys = [p["key"] for _, p in PAGES]
    urls = [p["url"] for _, p in PAGES]
    aliases = [a for _, p in PAGES for a in p["active"]]
    assert len(keys) == len(set(keys)) and len(urls) == len(set(urls)) and len(aliases) == len(set(aliases))
    assert len({g["key"] for g in GROUPS}) == len(GROUPS)
    for g in GROUPS:
        assert any(not p.get("hidden") for p in g["pages"]), g["key"]                                                 # a group's link = its first visible page


def test_every_menu_url_is_a_rendered_page():
    for _, p in PAGES:
        assert re.fullmatch(r"/[a-z0-9-]+", p["url"]), p["url"]                                                       # clean URLs (vercel.json cleanUrls)
        assert (ROOT / "public" / (p["url"][1:] + ".html")).is_file(), p["url"]


def test_every_active_page_a_renderer_passes_is_in_the_menu():
    aliases = {a for _, p in PAGES for a in p["active"]}
    passed = renderer_active_pages()
    assert {"economic", "central-banks", "retail-sentiment"} <= passed                                               # the scan found the renderers
    assert passed <= aliases, passed - aliases


def test_retail_sentiment_is_hidden_but_marks_sentiment():
    (g, p), = [(g, p) for g, p in PAGES if p["key"] == "retail-sentiment"]
    assert p.get("hidden") is True and g["key"] == "sentiment"
    for a in sorted({a for _, q in PAGES for a in q["active"]}):
        html = nav_for(a)
        assert "/retail-sentiment" not in html and "Retail" not in html, a
    check_nav(nav_for("retail-sentiment"), "sentiment", None)


def test_group_links_fall_back_to_their_first_page_and_list_their_pages_for_nav_js():
    html = nav_for("cot")
    for g in GROUPS:
        shown = [p["url"] for p in g["pages"] if not p.get("hidden")]
        assert re.search(r'href="%s" data-nav-group="%s"\s+data-nav-pages="%s"' % (shown[0], g["key"], " ".join(shown)), html), g["key"]


# ---------------------------------------------------------------- the pages


@pytest.mark.parametrize("group,page", [(g["key"], p) for g, p in PAGES if p["key"] != "central-banks"],
                         ids=lambda x: x["key"] if isinstance(x, dict) else x)
def test_every_rendered_page_carries_the_nav_with_its_group_and_page_active(group, page):
    html = (ROOT / "public" / (page["url"][1:] + ".html")).read_text()
    check_nav(html, group, None if page.get("hidden") else page["url"])
    assert "viewport-fit=cover" in html


@pytest.mark.parametrize("active", sorted(set(re.findall(r'active_page="([^"]+)"', (ROOT / "src" / "cb_render.py").read_text()))))
def test_every_central_banks_page_marks_macro_central_banks(active):
    """Landing, per bank and pair pages all pass the active_page(s) found in cb_render.py."""
    html = ENV.get_template("central_banks.html.j2").render(title="t", cfg={"page": "overview"}, active_page=active)
    check_nav(html, "macro", "/central-banks")


def test_every_page_template_has_the_navbar_and_a_notch_safe_viewport():
    for t in sorted((ROOT / "templates").glob("*.html.j2")):
        if t.name.startswith("_"):
            continue
        s = t.read_text()
        assert "{% include '_navbar.html.j2' %}" in s and "viewport-fit=cover" in s, t.name


def test_economic_is_titled_signal():
    s = (ROOT / "templates" / "economic.html.j2").read_text()
    assert "<title>Signal | Dashboard</title>" in s and "<h1>Signal</h1>" in s and "Economic Dashboard" not in s


def test_nav_js_remembers_the_last_page_per_group_safely():
    js = (ROOT / "static" / "nav.js").read_text()
    assert js.count("try {") >= 2 and "localStorage.setItem" in js and "localStorage.getItem" in js                 # storage only inside try/catch
    assert "innerHTML" not in js and "scrollIntoView(" not in js
    assert (ROOT / "public" / "nav.js").read_text() == js


# ---------------------------------------------------------------- the redesign: logo slot, top category bar


def css_block(media: str) -> str:
    css = (ROOT / "static" / "style.css").read_text()
    out = []
    for m in re.finditer(r"@media \(max-width: %spx\) \{" % media, css):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        out.append(css[m.end():i - 1])
    return "\n".join(out)


def test_the_logo_name_and_link_come_from_the_config():
    brand = ENV.get_template("_nav_config.html.j2").module.NAV_BRAND
    assert brand == {"name": "Dashboard", "url": "/economic"}
    html = nav_for("cot")
    assert '<a class="nav-brand" href="%s">' % brand["url"] in html and '<span class="nav-brand-name">%s</span>' % brand["name"] in html
    src = (ROOT / "templates" / "_navbar.html.j2").read_text()
    assert "Dashboard" not in src and 'href="/economic"' not in src                                                    # nothing hardcoded in the partial
    assert 'class="nav-mark" aria-hidden="true"' in html and "<svg" in html.split('class="nav-mark"')[1].split("</span>")[0]


def test_no_fixed_bottom_bar_and_no_padding_for_it():
    css = (ROOT / "static" / "style.css").read_text()
    assert "--tabbar-h" not in css and "padding-bottom: calc(" not in css
    nav = css[css.index("/* === NAVBAR"):css.index("/* === P/C Ratio page")]
    assert "position: fixed" not in nav and "bottom: 0" not in nav and "safe-area-inset-bottom" not in nav
    for f in (ROOT / "public").rglob("*.html"):                                                                         # no trace in the rendered pages either
        assert "nav-tabs" not in f.read_text() and "main-nav" not in f.read_text(), f


def test_the_category_bar_sticks_on_top_on_the_phone():
    css = (ROOT / "static" / "style.css").read_text()
    head = re.search(r"^\.site-head \{([^}]*)\}", css, re.M).group(1)
    assert "position: sticky" in head and "top: 0" in head                                                            # one sticky header
    b = css_block("600")
    assert ".site-head { top: calc(-1 * var(--logo-row-h)); }" in b                                                    # its logo row scrolls away...
    assert ":root { --nav-h: calc(var(--cat-bar-h) + env(safe-area-inset-top, 0px)); }" in b                          # ...the category bar stays: --nav-h
    assert 'grid-template-areas: "brand theme" "groups groups"' in b and "padding: env(safe-area-inset-top, 0px) 0 0" in b
    html = nav_for("cot")                                                                                               # one markup for the groups (no copy for the phone)
    assert html.count('class="nav-groups"') == 1 and html.index('class="nav-groups"') < html.index("</header>")
