"""Weekly point-in-time reconstruction for the tournament — v3_scales.collect
extended to the last Friday with data, plus the per-indicator breakdown B1 needs.

Each Friday 21:00 UTC from factor_scales.START (2023-09-22) to the last Friday
21:00 before now: factor_scales.state_at (calendar through _with_policy_rate +
scoring_view cut at release_dt <= t, rates/COT/P-C cut at t) and the exact record
v3_scales.collect writes, plus `bd`: {currency: {indicator: {category, surprise,
stale, flag, score}}} from the payload breakdown. Cached outside the repo.

    python scripts/measure/tournament/collect.py
"""
from __future__ import annotations

import json
import logging
import pickle
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CACHE, ROOT  # noqa: E402

import factor_scales as fs  # noqa: E402
from src import economic_render as er  # noqa: E402
from src.economic_compute import V3_MACRO_CATS  # noqa: E402

WEEKS = CACHE / "weeks.pkl"
BD_FIELDS = ("category", "surprise", "stale", "flag", "score")


def last_friday(now: pd.Timestamp) -> pd.Timestamp:
    t = now.normalize() - pd.Timedelta(days=(now.weekday() - 4) % 7) + pd.Timedelta(hours=21)
    return t if t <= now else t - pd.Timedelta(days=7)


def inputs() -> fs.Inputs:
    """As v3_scales.collect: the calendar loaded with the as_of of public/data/economic.json."""
    pub_as_of = pd.Timestamp(json.loads((ROOT / "public/data/economic.json").read_text())["as_of"])
    return fs.Inputs(er.INDICATORS_YAML, pub_as_of)


def record(st: dict, t: pd.Timestamp) -> dict:
    """v3_scales.collect's record (same keys, same values) + the slim breakdown."""
    cards = st["payload"]["currencies"]
    return {
        "week": t,
        "cats": {c: {k: {"score_precise": v["score_precise"], "coverage": v["coverage"]}
                     for k, v in cards[c]["categories"].items() if k in V3_MACRO_CATS}
                 for c in cards},
        "fx_cells": dict(st["fx_cells"]),
        "metal_cells": {s: v for s, v in st["sent"].items() if s in ("GOLD", "SILVER")},
        "v2_fx": {x["symbol"]: (x.get("score_v2", x["score"]), x.get("bias_v2", x["bias"]))
                  for x in st["payload"]["instruments"]},
        "v2_cross": {s: (r["score_precise"], r["bias_label"]) for s, r in st["cross"].items()},
        "bd": {c: {k: {f: e.get(f) for f in BD_FIELDS} for k, e in (cards[c].get("breakdown") or {}).items()}
               for c in cards},
    }


def collect(now: pd.Timestamp | None = None, path: Path = WEEKS) -> dict:
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    inp = inputs()
    weeks = fs.fridays(last_friday(now))
    recs = []
    for i, t in enumerate(weeks):
        recs.append(record(fs.state_at(inp, t), t))
        if i % 20 == 0:
            print(f"week {i + 1}/{len(weeks)} {t.date()}", file=sys.stderr, flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {"weeks": weeks, "recs": recs, "collected_utc": str(now)}
    pickle.dump(out, open(path, "wb"))
    return out


def load(path: Path = WEEKS) -> dict:
    return pickle.load(open(path, "rb"))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    d = collect()
    print(len(d["weeks"]), d["weeks"][0], d["weeks"][-1])
