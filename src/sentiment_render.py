"""Renders P/C Ratio and VIX/VIX3M pages. Stage 3 produces stubs only."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
PUBLIC_DIR = ROOT / "public"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def render_pc_ratio_page() -> Path:
    """Stage 3 stub — renders empty page with navbar."""
    env = _env()
    template = env.get_template("pc_ratio.html.j2")
    html = template.render(active_page="pc-ratio")
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PUBLIC_DIR / "pc-ratio.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


def render_vix_ratio_page() -> Path:
    """Stage 3 stub — renders empty page with navbar."""
    env = _env()
    template = env.get_template("vix_ratio.html.j2")
    html = template.render(active_page="vix-ratio")
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PUBLIC_DIR / "vix-ratio.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    pc_path = render_pc_ratio_page()
    vix_path = render_vix_ratio_page()
    print(f"Rendered: {pc_path}")
    print(f"Rendered: {vix_path}")
