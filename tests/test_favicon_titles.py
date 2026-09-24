"""Audit 9E — one favicon and "<Page> | Dashboard" tab titles on every page."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_page_links_the_favicon_and_has_a_dashboard_title():
    for p in (ROOT / "templates").glob("*.html.j2"):
        if p.name.startswith("_"):
            continue
        s = p.read_text()
        head = s[s.index("<head>"):s.index("</head>")]
        assert '<link rel="icon" type="image/svg+xml" href="/favicon.svg">' in head, p.name
        title = re.search(r"<title>(.*?)</title>", head).group(1)
        assert title.endswith(" | Dashboard"), (p.name, title)
    assert "<title>COT | Dashboard</title>" in (ROOT / "templates" / "cot.html.j2").read_text()


def test_favicon_is_a_small_svg_in_the_accent_color():
    svg = (ROOT / "static" / "favicon.svg").read_text()
    assert svg.startswith("<svg") and "#64b5f6" in svg and len(svg) < 1000
    assert (ROOT / "public" / "favicon.svg").read_text() == svg
