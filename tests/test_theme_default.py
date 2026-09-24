"""Audit 9D — theme before the first paint, from <head>, on every page."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_page_template_applies_the_theme_in_head():
    pages = [p for p in (ROOT / "templates").glob("*.html.j2") if not p.name.startswith("_")]
    assert pages
    for p in pages:
        s = p.read_text()
        head = s[s.index("<head>"):s.index("</head>")]
        assert "{% include '_theme_head.html.j2' %}" in head, p.name


def test_head_snippet_follows_saved_choice_then_system():
    s = (ROOT / "templates" / "_theme_head.html.j2").read_text()
    assert 'localStorage.getItem("cot-theme")' in s and "prefers-color-scheme: dark" in s
    assert "document.documentElement.classList.add" in s


def test_css_dark_rules_key_off_html():
    css = (ROOT / "static" / "style.css").read_text()
    assert "body.dark" not in css and "html.dark body" in css
    assert (ROOT / "public" / "style.css").read_text() == css
    assert (ROOT / "public" / "theme-toggle.js").read_text() == (ROOT / "static" / "theme-toggle.js").read_text()
