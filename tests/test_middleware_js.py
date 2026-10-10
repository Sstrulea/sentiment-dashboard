"""Audit 9A — middleware.js login flow (/login?next=..., internal paths only).
Plain Node (stubbed @vercel/functions); skipped when node is not on PATH."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS_TEST = ROOT / "tests" / "middleware_js" / "test_next_path.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_middleware_next_path():
    result = subprocess.run(["node", str(JS_TEST)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_cot_renders_to_cot_html_and_navbar_links():
    from jinja2 import Environment, FileSystemLoader
    nav = Environment(loader=FileSystemLoader(str(ROOT / "templates"))).get_template("_navbar.html.j2").render(active_page="cot")
    assert '<a class="nav-brand" href="/economic">' in nav and '<span class="nav-brand-name">Dashboard</span>' in nav  # "/" itself stays the middleware's 307
    assert '<a class="nav-sub-link active" href="/cot" aria-current="page">COT</a>' in nav
    src = (ROOT / "src" / "render.py").read_text()
    assert 'PUBLIC / "cot.html"' in src and 'PUBLIC / "index.html"' not in src
