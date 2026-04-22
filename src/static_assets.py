"""Sweep static/ into public/.

Single source of truth for which asset files end up at the site root. Both
sentiment_render and main.py call this; the existing COT render.py continues
to do its own style.css copy, which is redundant but harmless.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"
PUBLIC_DIR = ROOT / "public"

# File extensions treated as deployable static assets.
ASSET_SUFFIXES = {".css", ".js", ".map", ".svg", ".png", ".ico", ".woff", ".woff2"}


def copy_static_assets() -> list[Path]:
    """Copy every recognized asset from static/ into public/ (flat).

    Skips dotfiles (.DS_Store etc.) and files with unrecognized suffixes.
    Returns the list of destination paths written.
    """
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    if not STATIC_DIR.exists():
        return []

    copied: list[Path] = []
    for src in sorted(STATIC_DIR.iterdir()):
        if not src.is_file():
            continue
        if src.name.startswith("."):
            continue
        if src.suffix.lower() not in ASSET_SUFFIXES:
            continue
        dst = PUBLIC_DIR / src.name
        shutil.copyfile(src, dst)
        copied.append(dst)
    return copied
