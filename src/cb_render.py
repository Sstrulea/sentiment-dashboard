"""Render the Central Banks pages (phase 3a): JSON payloads + the static HTML shells.

    python -m src.cb_render [--asof YYYY-MM-DD] [--data-dir data/cb] [--public-dir public]

Reads data/cb through `cb_loader`, computes with `cb_compute` and writes ONLY

    public/data/cb/overview.json, public/data/cb/<ccy>.json (8), public/data/cb/pairs.json   (+ the `stance` block of each, S2)
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


FF_RAW = ROOT / "data" / "ff_raw"
STANCE_UI_YAML = ROOT / "config" / "cb_stance_ui.yaml"


def ff_week_events(asof: date, raw_dir: Path = FF_RAW) -> list:
    """The ForexFactory weekly calendar the site already collects (read only): the newest data/ff_raw/ff_weekly_<day>.json on or
    before the as-of -> [{currency, name_raw, datetime_utc}]."""
    from datetime import datetime, timezone
    files = sorted(f for f in raw_dir.glob("ff_weekly_*.json") if f.stem[len("ff_weekly_"):] <= asof.isoformat())
    if not files:
        return []
    out = []
    for e in json.loads(files[-1].read_text(encoding="utf-8")):
        try:
            dt = datetime.fromisoformat(str(e.get("date"))).astimezone(timezone.utc)
        except ValueError:
            continue
        out.append({"currency": str(e.get("country", "")).strip(), "name_raw": str(e.get("title", "")).strip(), "datetime_utc": dt})
    return out


def stance_blocks(data_dir, ctx, asof: date, out: dict) -> dict:
    """{ccy: bank stance block} (S1 computation, S2 texts) or {"na": reason}. A failure never blocks the rest of the render."""
    import yaml
    from . import cb_datasets as ds
    from . import cb_stance_history as SH
    from .cb_collect import Paths
    from .cb_compute.stance_inputs import current_version
    from .cb_compute.stance_ui import history_points, mandate_card, next_test  # noqa: F401
    from .cb_loader import load_stance
    from .cb_sources.official import load_official
    try:
        _, params, inputs, res = load_stance(data_dir, asof, ctx)
        paths = Paths(data_dir)
        vday = max(asof, date.today())                                   # as in load_stance: validation against the real day
        mand = ds.load_mandates(paths, today=vday, validate=True)
        fc = ds.load_forecasts(paths, today=vday, validate=True, mandates=mand)
        hist = SH.load(paths)
    except Exception as e:                                               # noqa: BLE001 - logged, the page says why
        log.exception("stance payload failed")
        return {"na": f"stance unavailable: {type(e).__name__}: {e}"}
    try:
        return _stance_banks(asof, out, params, res, mand, fc, hist)
    except Exception as e:                                               # noqa: BLE001 - logged, the page says why
        log.exception("stance payload failed")
        return {"na": f"stance unavailable: {type(e).__name__}: {e}"}


def _stance_banks(asof, out, params, res, mand, fc, hist) -> dict:
    import yaml
    from . import cb_datasets as ds
    from .cb_compute.stance_inputs import current_version
    from .cb_compute.stance_ui import how_to_read, history_points, mandate_card, next_test
    from .cb_sources.official import load_official
    names = {sid: s["name"] for sid, s in load_official()["macro"]["series"].items()}
    ui_cfg = yaml.safe_load(STANCE_UI_YAML.read_text(encoding="utf-8")) or {}
    mapping, shorts = ui_cfg.get("next_test") or {}, ui_cfg.get("measure_short") or {}
    events = ff_week_events(asof)
    versions = ds.mandate_versions(mand)
    from .cb_collect import Paths
    meetings = ds.load_meetings(Paths(None).meetings)
    from .cb_compute.framework_watch import last_projection_dates
    lastp = last_projection_dates(meetings, asof)
    banks = {}
    for c, r in res.items():
        path = out["overview"]["paths"].get(c)
        cur = current_version(versions.get(c) or [], asof)
        raw = next((v for v in ((mand.get("banks") or {}).get(c) or {}).get("versions") or [] if cur and str(v["effective_from"]) == str(cur.effective_from)), None)
        rate = (path or {}).get("current") or {}
        prim = (raw or {}).get("underlying", {}).get("primary")
        prim = prim if isinstance(prim, list) else [prim]
        ui = {"measure_short": shorts.get(c, "Core"), "measure_name": " and ".join(str(names.get(x, x)) for x in prim if x)}
        from .cb_compute.stance_v5 import SHORT
        notes = P.stance_notes(r, SHORT.get(c, c), lastp.get(c), [x for x in prim if x])
        banks[c] = P.stance_page_v5(r, ui, {k: rate.get(k) for k in ("rate", "lower", "upper", "range")},
                                    mandate_card(raw, names, params["underlying_rule"]) if raw else None, next_test(c, events, mapping, asof),
                                    history_points([h for (b, _), h in hist.items() if b == c]), neutral_reason(c, r, meetings, asof), notes)
    scale = yaml.safe_load((ROOT / "config" / "cb_stance.yaml").read_text(encoding="utf-8"))["v5"]["scale_pp"]
    return {"banks": banks, "how_to_read": how_to_read(scale), "scale_pp": 2.0, "na": None}


def neutral_reason(c: str, r: dict, meetings: dict, asof: date) -> Optional[str]:
    """Why a bank has no Position: 'SNB publishes no neutral' / 'RBNZ neutral due <next projection meeting>'."""
    from .cb_compute.stance_v5 import SHORT
    if r.get("N"):
        return None
    status = ((r.get("inputs") or {}).get("neutral_status") or "")
    if status == "not_published":
        return f"{SHORT.get(c, c)} publishes no neutral"
    nxt = min((m["date"] for m in meetings.get(c, []) if m.get("has_projections") and m["date"] > asof), default=None)
    if status == "pending" and nxt:
        return f"{SHORT.get(c, c)} neutral due {nxt.day} {P.MONTHS_SHORT[nxt.month - 1]} {nxt.year}"
    return f"{SHORT.get(c, c)}: no neutral estimate ({status or 'missing'})"


def render(data_dir: Path | str | None = None, public_dir: Path | str | None = None, asof: Optional[date] = None) -> dict:
    """Write everything; returns {"asof": date, "files": [paths written], "unchanged": n}."""
    ctx = load_context(data_dir)
    asof = asof or ctx.market.newest()
    if asof is None:
        raise SystemExit("no market quotes on record: nothing to render")
    pub = Path(public_dir) if public_dir else PUBLIC_DIR
    out = P.build(ctx, asof, load_pair_defs())
    st = stance_blocks(data_dir, ctx, asof, out)                         # S2: the stance on the overview (tab Stance) and each bank page
    out["overview"]["stance"] = ({"na": st["na"]} if st.get("na") else
                                 {**{k: v for k, v in st.items() if k != "banks"},
                                  "banks": {c: {k: v for k, v in b.items() if k not in ("history", "mandate", "next_test")} for c, b in st["banks"].items()}})
    for c, page in out["banks"].items():
        page["stance"] = ({"na": st["na"]} if st.get("na") else
                          {**st["banks"][c], "how_to_read": st["how_to_read"], "scale_pp": st["scale_pp"]} if c in st["banks"] else {"na": "no stance for this bank"})
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
