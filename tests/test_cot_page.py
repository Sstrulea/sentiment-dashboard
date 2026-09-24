"""Audit 10B: the COT page is a static shell filled by static/cot.js."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_render_writes_only_the_shell(tmp_path, monkeypatch):
    from src import render
    monkeypatch.setattr(render, "PUBLIC", tmp_path)
    out = render.render_dashboard()
    html = out.read_text()
    assert out.name == "cot.html"
    assert '<main class="cot-page"' in html and 'src="/cot.js"' in html and 'src="/score-palette.js"' in html
    assert not (tmp_path / "archive").exists()


def test_no_html_archive_committed():
    assert not list((ROOT / "public" / "archive").glob("*.html"))


def test_cot_js_builds_dom_without_innerhtml():
    js = (ROOT / "static" / "cot.js").read_text()
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js
    assert (ROOT / "public" / "cot.js").read_text() == js


def test_old_cot_styles_removed_retail_kept():
    css = (ROOT / "static" / "style.css").read_text()
    for gone in ("table.cot", ".flip-badge", "td.net-top", ".exp-high-long", "--grp-divider", ".topbar"):
        assert gone not in css, gone
    for kept in (".ext-95", ".spark-cell", ".cot-page"):
        assert kept in css, kept
