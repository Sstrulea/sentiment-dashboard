"""Registry inputs: incremental refresh with a cache under data/registry/inputs/ (the only place the
registry workflow writes besides data/registry/). Official, free sources; a source that fails keeps
its previous copy and is reported stale — nothing is invented.

  weekly : ECB eurofxref (14:15 CET), BIS WS_CBPOL (15 areas), FRED H.10 (8), CFTC Legacy + TFF
  monthly: OECD STES latest editions (new editions only, components appended), OECD BCI, IMF CTOT

state.json records, per input, the last successful refresh and the last observation date."""
from __future__ import annotations

import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DIR = ROOT / "data" / "registry" / "inputs"
STATE = DIR / "state.json"
UA = "macro-data-analysis registry (+https://github.com/Sstrulea/macro-data-analysis)"
SEED_FILES = {"p5_oecd_components.parquet": ROOT / "data" / "research" / "p5_oecd_components.parquet",
              "p5_bci.parquet": ROOT / "data" / "research" / "p5_bci.parquet",
              "p5_ctot.parquet": ROOT / "data" / "research" / "p5_ctot.parquet",
              "p5_ecb_fx.parquet": ROOT / "data" / "research" / "p5_ecb_fx.parquet",
              "p5_bis_policy.parquet": ROOT / "data" / "research" / "p5_bis_policy.parquet",
              "cftc_legacy.parquet": ROOT / "data" / "cftc_legacy.parquet",
              "cftc_tff.parquet": ROOT / "data" / "cftc_tff.parquet"}
MAX_AGE_DAYS = {"p5_ecb_fx.parquet": 10, "p5_bis_policy.parquet": 60, "h10_fx.parquet": 14,
                "cftc_legacy.parquet": 21, "cftc_tff.parquet": 21, "p5_bci.parquet": 120,
                "p5_ctot.parquet": 270, "p5_oecd_components.parquet": 70}


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")


def seed() -> None:
    """First run: copy the research panels as the starting cache."""
    DIR.mkdir(parents=True, exist_ok=True)
    for name, src in SEED_FILES.items():
        if not (DIR / name).exists() and src.exists():
            (DIR / name).write_bytes(src.read_bytes())


def _last_date(name: str) -> str | None:
    f = DIR / name
    if not f.exists():
        return None
    d = pd.read_parquet(f)
    col = next(c for c in ("date", "period", "edition") if c in d.columns)
    return str(d[col].max())[:10]


def staleness(now: pd.Timestamp | None = None) -> dict:
    """{input: {last, age_days, stale}}"""
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    out = {}
    for name, lim in MAX_AGE_DAYS.items():
        last = _last_date(name)
        if last is None:
            out[name] = {"last": None, "age_days": None, "stale": True}
            continue
        ts = pd.Timestamp(last) if len(last) != 6 else pd.Timestamp(f"{last[:4]}-{last[4:]}-01")
        age = (now - ts).days
        out[name] = {"last": last, "age_days": age, "stale": age > lim}
    return out


# --- fetchers (each returns a frame or raises) ---------------------------------------------------

def _ecb_fx() -> pd.DataFrame:
    from scripts.research.fetch_phase5_inputs import ecb_fx
    return ecb_fx()


def _bis() -> pd.DataFrame:
    from scripts.research.fetch_phase5_inputs import bis_policy
    return bis_policy()


def _bci() -> pd.DataFrame:
    from scripts.research.fetch_phase5_inputs import bci
    return bci()


def _ctot() -> pd.DataFrame:
    from scripts.research.fetch_phase5_inputs import ctot
    return ctot()[0]


def _h10() -> pd.DataFrame:
    from src.rate_sources import FredSeriesSource
    fx = {"EUR": ("DEXUSEU", 1), "GBP": ("DEXUSUK", 1), "AUD": ("DEXUSAL", 1), "NZD": ("DEXUSNZ", 1),
          "JPY": ("DEXJPUS", -1), "CAD": ("DEXCAUS", -1), "CHF": ("DEXSZUS", -1)}
    rows = []
    for c, (sid, sign) in fx.items():
        d = FredSeriesSource(sid).fetch_series()
        if d is None:
            raise RuntimeError(f"FRED {sid} unavailable")
        d = d[d["value"] > 0]
        rows.append(pd.DataFrame({"date": d["date"], "ccy": c, "log_usd_value": sign * np.log(d["value"].astype(float))}))
    return pd.concat(rows, ignore_index=True)


def _legacy() -> pd.DataFrame:
    from scripts.fetch_phase4 import legacy
    return legacy()


def _tff() -> pd.DataFrame:
    from scripts.fetch_phase4 import tff
    return tff()


def _oecd_new_editions() -> pd.DataFrame:
    """Download only the editions newer than the cached components and append their components."""
    from scripts.research.build_oecd_editions import KEEP_UNITS, euro_area, period_end
    from src.tree.composite import AREA, EA_CODES, edition_components
    comp = pd.read_parquet(DIR / "p5_oecd_components.parquet")
    have = str(comp["edition"].astype(str).max())
    now = pd.Timestamp.now(tz="UTC")
    eds = [p.strftime("%Y%m") for p in pd.period_range(pd.Period(have, "M") + 1, now.to_period("M"), freq="M")
           if p.strftime("%Y%m") > have]
    if not eds:
        return comp
    base = "https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES_REVISIONS@DF_STES_REVISIONS,4.0"
    frames = []
    for area in list(AREA) + ["EA20", "EA19"]:
        url = f"{base}/{area}..EMP+UNEMP+EX+IM...{'+'.join(eds)}?startPeriod=1985-01&format=csvfile"
        for attempt in range(4):                     # OECD rate limit: back off on 429
            r = requests.get(url, headers={"User-Agent": UA}, timeout=300)
            if r.status_code != 429:
                break
            time.sleep(60 * (attempt + 1))
        time.sleep(3.5)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        d = pd.read_csv(io.StringIO(r.text), dtype=str)
        d = d[[(m, u) in KEEP_UNITS for m, u in zip(d["MEASURE"], d["UNIT_MEASURE"])] & (d["ACTIVITY"] == "_T")]
        frames.append(d)
    if not frames:
        return comp
    d = pd.concat(frames)
    d["period"] = period_end(d["TIME_PERIOD"])
    d["value"] = pd.to_numeric(d["OBS_VALUE"], errors="coerce")
    d = d.dropna(subset=["value"])
    ea = euro_area(d[d["REF_AREA"].isin(EA_CODES)])
    nat = d[d["REF_AREA"].isin(AREA)]
    allr = pd.concat([nat.assign(ccy=nat["REF_AREA"].map(AREA)), ea.assign(ccy="EUR")])
    rows = []
    for (ccy, ed), g in allr.groupby(["ccy", "EDITION"]):
        c = edition_components(g)
        rows.append({"ccy": ccy, "edition": ed, "excess_emp": c["excess_emp"], "unemp_trend": c["unemp_trend"],
                     "external": c["external"], "area_codes": ",".join(sorted(g["REF_AREA"].unique())),
                     **{f"freq_{k}": v for k, v in c["freq"].items()},
                     **{f"last_{k}": v for k, v in c["last_period"].items()}})
    new = pd.DataFrame(rows)
    return pd.concat([comp, new], ignore_index=True).drop_duplicates(["ccy", "edition"], keep="last")


JOBS = {"weekly": {"p5_ecb_fx.parquet": _ecb_fx, "p5_bis_policy.parquet": _bis, "h10_fx.parquet": _h10,
                   "cftc_legacy.parquet": _legacy, "cftc_tff.parquet": _tff},
        "monthly": {"p5_oecd_components.parquet": _oecd_new_editions, "p5_bci.parquet": _bci, "p5_ctot.parquet": _ctot}}


def refresh(which: str = "weekly", force: bool = False, jobs: dict | None = None) -> dict:
    """Run the refresh jobs; monthly ones at most once per calendar month unless force."""
    seed()
    st = load_state()
    now = datetime.now(timezone.utc)
    report = {}
    for name, job in (jobs or JOBS[which]).items():
        last_ok = st.get(name, {}).get("refreshed_at")
        if which == "monthly" and not force and last_ok and last_ok[:7] == now.strftime("%Y-%m"):
            report[name] = "skipped (refreshed this month)"
            continue
        try:
            df = job()
        except Exception as e:  # keep the previous copy; staleness() reports it
            report[name] = f"FAILED {type(e).__name__}: {e}"[:200]
            continue
        df.to_parquet(DIR / name, index=False)
        st[name] = {"refreshed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": int(len(df))}
        report[name] = f"ok ({len(df)} rows)"
    save_state(st)
    return report
