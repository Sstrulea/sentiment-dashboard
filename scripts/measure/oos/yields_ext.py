"""EUR and GBP 2Y history before data/rates.parquet starts, from the SAME sources the
repo uses (src.rate_sources: EcbSource key B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y, BoeSource =
BoE GLC nominal spot 2.0y from glcnominalddata.zip) — evaluation only, cached in
~/.cache/macro-tournament/yields, never in the repo.

Acceptance (before the pre-registration): the source covers 2014-01 (at least the 68
observations Δ63 with 5-observation ends needs before 2014-01-03) and, on the days in
common with data/rates.parquet, max |difference| <= 0.01 pp. A currency that fails is
n/a in H2 (its index drops out).

    python scripts/measure/oos/yields_ext.py
"""
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.rate_sources import BoeSource, EcbSource  # noqa: E402

DIR = Path.home() / ".cache" / "macro-tournament" / "yields"
SINCE = date(2013, 1, 1)
MAX_DIFF_PP = 0.01
NEED_BEFORE = pd.Timestamp("2014-01-03")
NEED_OBS = 63 + 5


def fetch_eur() -> pd.Series:
    p = DIR / "eur_2y_ecb.csv"
    if not p.exists():
        src = EcbSource()
        r = src._get(f"https://data-api.ecb.europa.eu/service/data/YC/{EcbSource.KEY}"
                     f"?format=csvdata&startPeriod={SINCE.isoformat()}")
        if r is None:
            raise RuntimeError(f"ECB download failed: {src.last_note}")
        df = pd.read_csv(io.StringIO(r.text))
        dtypes = set(df["DATA_TYPE_FM"].dropna().astype(str))
        if dtypes != {"SR_2Y"}:
            raise RuntimeError(f"ECB DATA_TYPE_FM={sorted(dtypes)}")
        out = pd.DataFrame({"date": pd.to_datetime(df["TIME_PERIOD"]), "yield_pct": pd.to_numeric(df["OBS_VALUE"])})
        DIR.mkdir(parents=True, exist_ok=True)
        out.dropna().sort_values("date").to_csv(p, index=False)
    d = pd.read_csv(p, parse_dates=["date"])
    return pd.Series(d["yield_pct"].values, index=d["date"]).sort_index()


def fetch_gbp() -> pd.Series:
    p = DIR / "gbp_2y_boe_glc.csv"
    if not p.exists():
        pts, _ = BoeSource().fetch_history_with_coverage(SINCE)
        out = pd.DataFrame(pts, columns=["date", "yield_pct"])
        out["date"] = pd.to_datetime(out["date"])
        DIR.mkdir(parents=True, exist_ok=True)
        out.sort_values("date").to_csv(p, index=False)
    d = pd.read_csv(p, parse_dates=["date"])
    return pd.Series(d["yield_pct"].values, index=d["date"]).sort_index()


def check() -> dict:
    rates = pd.read_parquet(ROOT / "data" / "rates.parquet")
    out = {}
    for ccy, fn, source in (("EUR", fetch_eur, "ecb"), ("GBP", fetch_gbp, "boe_glc")):
        res = {"source": source}
        try:
            s = fn()
        except Exception as e:  # noqa: BLE001 — reported, fail-closed
            out[ccy] = {**res, "ok": False, "why": f"download failed: {e}"}
            continue
        repo = rates[(rates["currency"] == ccy)].set_index("date")["yield_pct"].sort_index()
        j = pd.concat([s.rename("ext"), repo.rename("repo")], axis=1, join="inner").dropna()
        diff = (j["ext"] - j["repo"]).abs()
        n_before = int((s.index < NEED_BEFORE).sum())
        res.update({"first": str(s.index.min().date()), "last": str(s.index.max().date()), "n": int(len(s)),
                    "obs_before_2014_01_03": n_before, "repo_first": str(repo.index.min().date()),
                    "common_days": int(len(j)), "max_abs_diff_pp": float(diff.max()) if len(j) else None,
                    "gaps_gt_7d": [(str(a.date()), int(g)) for a, g in
                                   zip(s.index[:-1], (s.index[1:] - s.index[:-1]).days) if g > 7]})
        res["ok"] = bool(n_before >= NEED_OBS and len(j) > 0 and diff.max() <= MAX_DIFF_PP)
        if not res["ok"]:
            res["why"] = "coverage" if n_before < NEED_OBS else f"max diff {diff.max():.4f} pp > {MAX_DIFF_PP}"
        out[ccy] = res
    return out


def extended(ccy: str) -> pd.Series:
    """data/rates.parquet where it exists, the external history before its first day."""
    rates = pd.read_parquet(ROOT / "data" / "rates.parquet")
    repo = rates[rates["currency"] == ccy].set_index("date")["yield_pct"].sort_index()
    ext = fetch_eur() if ccy == "EUR" else fetch_gbp()
    return pd.concat([ext[ext.index < repo.index.min()], repo]).sort_index()


if __name__ == "__main__":
    r = check()
    (DIR / "check.json").write_text(json.dumps(r, indent=1))
    print(json.dumps(r, indent=1))
