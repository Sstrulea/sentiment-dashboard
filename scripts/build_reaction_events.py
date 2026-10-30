"""Build the event table of the reaction layer -> data/reaction_events.parquet.

Offline: data/economic_calendar_ff.parquet (the calendar), data/backbone_rates.parquet,
data/tree_inputs.parquet (risk_regime_v0 of phase 1), data/cb/meetings.yaml.

One row per calendar publication of a (currency, indicator) listed in
data/econ_catalog.yml (category from the catalog):
  - actual / consensus / previous as the calendar stored them at release (FF
    first print; data/manual_actuals_overrides.json is NOT applied — the market
    reacted to the number published then); the zero-placeholder quarantine and
    can_be_zero come from src.ff_scoring.to_scoring_frame / scoring_view
  - z_signed = the EXISTING src.economic_compute.compute_indicator_score z
    (direction x surprise / rolling sigma of the last K prints), computed as of
    the release with the calendar cut at the release; z_raw = z_signed x direction
  - prev_revision = previous - the actual we held for the prior print
  - d2y / us_d2y / us_z_sum / fx_usd / fx_basket: pre-registered alignment
    (src/tree/backbone.py), bp
  - days_to_meeting: next decision day of the bank after the release (calendar
    interest_rate_decision rows + data/cb/meetings.yaml)
  - regime: risk_regime_v0 (phase 1 classifier) as of the release day
  - avail: first day a node may use the row = (2Y close date, or the FX fixing
    date when there is no 2Y) + 1 day; CHF: the SNB publication rule
Rows without an actual are kept with released = False (schedule for event_risk_2w).

    python scripts/build_reaction_events.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.economic_compute import compute_indicator_score, effective_direction  # noqa: E402
from src.ff_scoring import load_configs, scoring_view, to_scoring_frame  # noqa: E402
from src.tree import schema  # noqa: E402
from src.tree.backbone import CCYS, Backbone, us_control  # noqa: E402
from src.tree.data import Inputs  # noqa: E402
from src.tree.engine import Engine, classify  # noqa: E402

OUT = ROOT / "data" / "reaction_events.parquet"
CATALOG = ROOT / "data" / "econ_catalog.yml"
MEETINGS = ROOT / "data" / "cb" / "meetings.yaml"
CATEGORY_NAMES = {"inflation": "Inflation", "labor": "Labor", "growth": "Growth", "rates": "Rates"}


def catalog_pairs() -> dict[tuple[str, str], str]:
    cat = yaml.safe_load(CATALOG.read_text())["categories"]
    return {(ccy, e["indicator_key"]): CATEGORY_NAMES[c]
            for c, per in cat.items() for ccy, lst in per.items() for e in lst if e.get("indicator_key")}


def meeting_days(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    out: dict[str, set] = {c: set() for c in CCYS}
    dec = frame[frame["indicator_key"] == "interest_rate_decision"]
    for c, d in zip(dec["currency"], pd.to_datetime(dec["release_dt"])):
        out[c].add(d.normalize())
    m = yaml.safe_load(MEETINGS.read_text())["meetings"]
    for c, rows in m.items():
        for r in rows:
            out.setdefault(c, set()).add(pd.Timestamp(r["date"]))
    return {c: np.array(sorted(v), dtype="datetime64[ns]") for c, v in out.items()}


def score_events(frame: pd.DataFrame, pairs: dict) -> pd.DataFrame:
    ind_cfg, _ = load_configs()
    defaults, inds = ind_cfg["defaults"], ind_cfg["indicators"]
    rows = []
    for (ccy, key), g in frame.groupby(["currency", "indicator_key"], sort=True):
        if (ccy, key) not in pairs or key not in inds:
            continue
        cfg = inds[key]
        direction = effective_direction(cfg, defaults, ccy)
        g = g.sort_values("release_dt").reset_index(drop=True)
        for i, r in g.iterrows():
            t = pd.Timestamp(r["release_dt"])
            base = {"ccy": ccy, "indicator_key": key, "category": pairs[(ccy, key)], "release_dt": t,
                    "actual": r["actual"], "consensus": r["consensus"], "previous": r["previous"],
                    "direction": direction, "released": bool(pd.notna(r["actual"]))}
            if not base["released"]:
                rows.append(base)
                continue
            sc = compute_indicator_score(g[g["release_dt"] <= t], cfg, defaults, t, allow_stale=True,
                                         currency=ccy, indicator_key=key)
            same = sc is not None and pd.Timestamp(sc["release_dt"]) == t
            z = sc["z"] if same else None
            prior = sc.get("prior_actual") if same else None
            base.update({
                "surprise": sc["surprise"] if same else None,
                "z_signed": z, "z_raw": (z * direction) if z is not None else None,
                "score_flag": (sc["flag"] if same else "collapsed_flash_final"),
                "prev_revision": (float(r["previous"]) - prior) if (prior is not None and pd.notna(r["previous"])) else None,
            })
            rows.append(base)
    return pd.DataFrame(rows)


def main() -> int:
    t0 = time.time()
    ff = pd.read_parquet(ROOT / "data" / "economic_calendar_ff.parquet")
    frame = scoring_view(to_scoring_frame(ff))
    frame["release_dt"] = pd.to_datetime(frame["release_dt"])
    pairs = catalog_pairs()
    ev = score_events(frame, pairs)
    print(f"scored {len(ev)} rows ({ev['released'].sum()} released) in {time.time() - t0:.1f}s")

    bb = Backbone.load()
    avail_of = {(s, d): a for s, d, a in zip(bb.frame["series"], bb.frame["date"], bb.frame["avail"])}
    us = bb.y2("USD")
    y2 = {c: bb.y2(c) for c in CCYS}
    fx_b = {c: bb.fx_aligned(c, "basket") for c in CCYS}
    fx_u = {c: bb.fx_aligned(c, "usd") for c in CCYS if c != "USD"}
    meet = meeting_days(frame)

    # US surprises by US 2Y close window (for the same-day US surprise control)
    us_ev = ev[(ev["ccy"] == "USD") & ev["z_raw"].notna()]
    us_k = np.array([us.first_after(t) for t in us_ev["release_dt"]])

    glob, _ = schema.load("jpy")
    eng = Engine(glob, Inputs.load())
    cls = glob["classifiers"]["risk_regime_v0"]
    regime_cache: dict = {}

    out = []
    for r in ev.itertuples(index=False):
        t = r.release_dt
        rec = r._asdict()
        s = y2[r.ccy]
        d2y = us_d2y = us_z = np.nan
        y2_date = None
        if s is not None and r.released:
            k = s.first_after(t)
            d, y2_date = s.move_after(t)
            d2y = d * 100.0
            if r.ccy != "USD":
                us_d2y = us_control(us, s, k) * 100.0
                j = us.nearest(s.instants[k]) if k < len(s) else -1
                us_z = float(us_ev["z_raw"].to_numpy()[us_k == j].sum()) if j >= 0 else np.nan
        fb, fdate = fx_b[r.ccy].move_after(t)
        fu = fx_u[r.ccy].move_after(t)[0] if r.ccy in fx_u else 0.0
        nxt = meet.get(r.ccy, np.array([], dtype="datetime64[ns]"))
        i = np.searchsorted(nxt, np.datetime64(t.normalize(), "ns"), side="right")
        day = t.normalize()
        if day not in regime_cache:
            regime_cache[day] = classify(cls, eng.global_results(day))["regime"]
        if y2_date is not None:
            avail = avail_of.get((f"Y2_{r.ccy}", y2_date), y2_date + pd.Timedelta(days=1))
        elif fdate is not None:
            avail = fdate + pd.Timedelta(days=1)
        else:
            avail = day + pd.Timedelta(days=1)
        rec.update({"d2y": d2y, "y2_date": y2_date, "us_d2y": us_d2y, "us_z_sum": us_z,
                    "fx_basket": fb * 1e4, "fx_usd": fu * 1e4, "fx_date": fdate,
                    "days_to_meeting": int((nxt[i] - np.datetime64(day, "ns")) / np.timedelta64(1, "D"))
                    if i < len(nxt) else None,
                    "regime": regime_cache[day], "avail": avail})
        out.append(rec)
    df = pd.DataFrame(out).sort_values(["release_dt", "ccy", "indicator_key"]).reset_index(drop=True)
    df.insert(0, "event_id", [f"{c}:{k}:{t:%Y-%m-%dT%H%M}" for c, k, t in
                              zip(df["ccy"], df["indicator_key"], df["release_dt"])])
    for c in ("y2_date", "fx_date", "avail", "release_dt"):
        df[c] = pd.to_datetime(df[c]).astype("datetime64[ns]")
    for c in ("actual", "consensus", "previous", "surprise", "z_signed", "z_raw", "prev_revision",
              "days_to_meeting", "direction"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.to_parquet(OUT, index=False)
    print(f"-> {OUT}: {len(df)} rows in {time.time() - t0:.1f}s")
    rel = df[df["released"]]
    print(rel.groupby("ccy").agg(events=("event_id", "size"), with_z=("z_raw", lambda s: s.notna().sum()),
                                 d2y=("d2y", lambda s: s.notna().mean()),
                                 fx=("fx_basket", lambda s: s.notna().mean())).round(3).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
