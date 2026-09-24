"""Render the COT page shell (public/cot.html).

Audit 10B: the page is built in the browser by static/cot.js from the data
payload in public/data/cot/ (src/cot_payload.py); this module only writes the
static skeleton. The per-week HTML archive is gone: /archive/<date> redirects to
/cot?week=<date> (middleware.js).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
PUBLIC = ROOT / "public"


def render_dashboard(snapshot: pd.DataFrame | None = None, history: pd.DataFrame | None = None) -> Path:
    """Write public/cot.html. `snapshot` / `history` are accepted for the callers'
    sake (src.main) and unused: every number comes from public/data/cot/."""
    PUBLIC.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)),
                      autoescape=select_autoescape(["html"]))
    html = env.get_template("cot.html.j2").render(active_page="cot")
    cot_path = PUBLIC / "cot.html"
    cot_path.write_text(html, encoding="utf-8")

    css_src = STATIC / "style.css"
    if css_src.exists():
        shutil.copyfile(css_src, PUBLIC / "style.css")
    return cot_path
