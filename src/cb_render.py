"""Render the Central Banks pages (phase 3a): JSON payloads + the static HTML shells.

    python -m src.cb_render [--asof YYYY-MM-DD] [--data-dir data/cb] [--public-dir public]

Reads data/cb through `cb_loader`, computes with `cb_compute` and writes ONLY

    public/data/cb/overview.json, public/data/cb/<ccy>.json (8), public/data/cb/pairs.json
    public/central-banks.html, public/central-banks/<ccy>.html (8), public/central-banks/pair/<pair>.html (28)

All 37 pages come from ONE template; the browser fills them from the JSON (nothing is hardcoded in the HTML). Deterministic: the
payloads depend only on the data and the as-of date (no clock), and a file is rewritten only when its bytes change.
Static assets (cb.js, style.css) are NOT copied here: they live in static/ and are committed with their public/ copies.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .cb_compute import payload as P
from .cb_loader import load_context, load_pair_defs

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
PUBLIC_DIR = ROOT / "public"
DATA_URL = "/data/cb"


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=select_autoescape(["html"]))


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"


def write_if_changed(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def page_config(page: str, ccy: Optional[str] = None, pair: Optional[str] = None) -> dict:
    return {"page": page, "ccy": ccy, "pair": pair, "urls": {"overview": f"{DATA_URL}/overview.json", "pairs": f"{DATA_URL}/pairs.json",
                                                            "bank": f"{DATA_URL}/{{ccy}}.json", "overview_page": P.BASE_URL + ".html"}}


def render(data_dir: Path | str | None = None, public_dir: Path | str | None = None, asof: Optional[date] = None) -> dict:
    """Write everything; returns {"asof": date, "files": [paths written], "unchanged": n}."""
    ctx = load_context(data_dir)
    asof = asof or ctx.market.newest()
    if asof is None:
        raise SystemExit("no market quotes on record: nothing to render")
    pub = Path(public_dir) if public_dir else PUBLIC_DIR
    out = P.build(ctx, asof, load_pair_defs())
    tpl = _env().get_template("central_banks.html.j2")
    written, same = [], 0

    def put(path: Path, text: str) -> None:
        nonlocal same
        if write_if_changed(path, text):
            written.append(path)
        else:
            same += 1

    put(pub / "data" / "cb" / "overview.json", dumps(out["overview"]))
    put(pub / "data" / "cb" / "pairs.json", dumps(out["pairs"]))
    for ccy, page in out["banks"].items():
        put(pub / "data" / "cb" / f"{ccy.lower()}.json", dumps(page))

    put(pub / "central-banks.html", tpl.render(title="Central Banks", cfg=page_config("overview"), active_page="central-banks"))
    for ccy, page in out["banks"].items():
        title = f"{ccy} · {page['bank']['short']}"
        put(pub / "central-banks" / f"{ccy.lower()}.html", tpl.render(title=f"{title} | Central Banks", cfg=page_config("bank", ccy=ccy), active_page="central-banks"))
    for p in out["pairs"]["pairs"]:
        put(pub / "central-banks" / "pair" / f"{p['slug']}.html",
            tpl.render(title=f"{p['display']} | Central Banks", cfg=page_config("pair", pair=p["pair"]), active_page="central-banks"))
    log.info("Central Banks rendered as of %s: %d files written, %d unchanged", asof, len(written), same)
    return {"asof": asof, "files": written, "unchanged": same}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--asof", type=date.fromisoformat, default=None, help="as-of date (default: the newest market snapshot)")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--public-dir", default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    res = render(a.data_dir, a.public_dir, a.asof)
    print(f"Rendered as of {res['asof']}: {len(res['files'])} written, {res['unchanged']} unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
