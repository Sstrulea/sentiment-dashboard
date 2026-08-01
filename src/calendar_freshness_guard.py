"""fix/calendar-freshness-per-ccy — per-currency calendar freshness.

Closes the SAME aggregation blindness `price_freshness_guard.py` closed for
price, but NOT by mirroring its raw-gap-P95 method — that was tried first
and rejected on real data: a currency's "any indicator" gap distribution
mixes several independently-scheduled indicators (CPI, PPI, GDP, PMI...)
into one statistic, and for a sparse currency (CHF, 6 indicators) the
derived threshold self-relaxes to ~25 days, wide enough to swallow a
perfectly normal gap. Checked against CHF specifically: its 18-day silence
(as of 2026-08-01) is NOT stale by that method, and — separately confirmed —
none of its 6 indicators are stale by the EXISTING per-indicator recency
gate either. CHF is not the example; see docs/faza-calendar-freshness-*.md.

Design actually used: reuse `economic_compute.effective_frequency` +
`_max_age_for` — the SAME per-(currency, indicator) recency window already
used by scoring (`compute_indicator_score`'s own staleness gate) — instead
of deriving a new, weaker statistic. A currency is `stale` iff AT LEAST ONE
of its scored (weight > 0) indicators exceeds ITS OWN threshold. This
correctly flags USD `core_cpi` (51d/45d) and AUD `core_cpi` (275d/110d)
today, and correctly does not flag CHF.

Pure — no I/O beyond what's passed in.
"""
from __future__ import annotations

import pandas as pd

from src.economic_compute import effective_frequency, _max_age_for, _indicator_applies


def per_currency_indicator_freshness(
    calendar_df: pd.DataFrame,
    indicators_cfg: dict,
    as_of: pd.Timestamp,
    currencies: list[str] | None = None,
) -> pd.DataFrame:
    """One row per (currency, indicator_key) that applies to that currency
    and is actually SCORED (weight > 0 — display-only slots are excluded,
    same convention as compute_currency_scorecard's own aggregation and the
    prior audit). `status` ∈ {stale, fresh, no_data}. Mirrors
    `_max_age_for`'s existing, already-validated per-frequency window —
    not a new statistic.
    """
    defaults = indicators_cfg.get("defaults", {}) or {}
    indicators = indicators_cfg.get("indicators", {}) or {}
    as_of = pd.Timestamp(as_of)
    ccys = currencies or sorted({c for ind in indicators.values()
                                 for c in (ind.get("currencies") or
                                          ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"])})

    rows = []
    for ccy in ccys:
        for key, ind_cfg in indicators.items():
            if float(ind_cfg.get("weight", 1.0)) == 0.0:
                continue  # display-only slot, not scored — excluded, same as the audit
            if not _indicator_applies(ccy, ind_cfg):
                continue
            sub = calendar_df[(calendar_df["currency"] == ccy)
                              & (calendar_df["indicator_key"] == key)
                              & calendar_df["actual"].notna()]
            if sub.empty:
                rows.append({"currency": ccy, "indicator_key": key, "status": "no_data",
                            "last_date": None, "age_days": None, "threshold_days": None})
                continue
            last_date = pd.to_datetime(sub["release_dt"]).max()
            age_days = (as_of.normalize() - last_date.normalize()).days
            freq = effective_frequency(ind_cfg, defaults, ccy)
            threshold = _max_age_for(ind_cfg, defaults, freq)
            status = "stale" if age_days > threshold else "fresh"
            rows.append({"currency": ccy, "indicator_key": key, "status": status,
                        "last_date": last_date, "age_days": age_days, "threshold_days": threshold})
    return pd.DataFrame(rows, columns=["currency", "indicator_key", "status",
                                       "last_date", "age_days", "threshold_days"])


def currency_freshness_report(per_indicator: pd.DataFrame) -> dict:
    """Collapse to ONE grouped report per currency — never one alert per
    indicator. A currency is `stale` iff any of its own indicators is."""
    out = {}
    for ccy, g in per_indicator.groupby("currency"):
        stale = g[g["status"] == "stale"]
        no_data = g[g["status"] == "no_data"]
        out[ccy] = {
            "stale": bool(len(stale)),
            "stale_indicators": [
                {"indicator_key": r["indicator_key"],
                 "last_date": r["last_date"].isoformat() if r["last_date"] is not None else None,
                 "age_days": r["age_days"], "threshold_days": r["threshold_days"]}
                for _, r in stale.iterrows()
            ],
            "no_data_indicators": sorted(no_data["indicator_key"].tolist()),
            "checked_count": int(len(g)),
        }
    any_stale = any(v["stale"] for v in out.values())
    return {"any_stale": any_stale, "by_currency": out,
           "stale_currencies": sorted([c for c, v in out.items() if v["stale"]])}


def check_pending_actuals_in_jb_raw(
    jb_raw_dir,
    parse_fn,
    build_matcher_fn,
    stale_rows: pd.DataFrame,
    parquet_last_dates: dict[tuple[str, str], pd.Timestamp],
) -> dict[tuple[str, str], dict]:
    """For each stale (currency, indicator_key), reparse every retained
    data/jb_raw/ payload through the PRODUCTION parser (parse_fn = e.g.
    econ_calendar_ff.parse_jblanked_range) + matcher, and check whether any
    row for that indicator has a release date NEWER than what's already in
    the parquet. Distinguishes:
      - "actual_available_not_ingested": a newer row exists WITH a valid
        actual that never reached the parquet — real ingest gap, source
        data exists, source of truth for "e defect de sursă, nu absență de
        program".
      - "no_newer_data": nothing newer found in the retained window — either
        genuinely nothing new, or the gap predates the window (can't tell
        which from this alone).
    Read-only; does not distinguish "scheduled-not-released" specifically
    (JBlanked's range payloads are actuals-focused, see proposal doc) — see
    the caveat in the freshness doc: a definitive scheduled/missing
    distinction would need the ff_weekly (schedule) feed, which is not
    cached anywhere in the repo.
    """
    import json

    payloads = sorted(jb_raw_dir.glob("jb_range_*.json"))
    matcher = build_matcher_fn()
    result = {}
    for _, r in stale_rows.iterrows():
        key = (r["currency"], r["indicator_key"])
        last_known = parquet_last_dates.get(key)
        newest_found = None
        newest_row = None
        for p in payloads:
            try:
                data = json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict):
                data = data.get("data") or list(data.values())[0]
            cal = parse_fn(data)
            sub = cal[(cal["currency"] == r["currency"])]
            sub = sub.assign(indicator_key=sub["name_canonical"].map(
                lambda n, c=r["currency"]: matcher.match(
                    {"USD": "United States", "EUR": "European Union", "GBP": "United Kingdom",
                     "JPY": "Japan", "AUD": "Australia", "NZD": "New Zealand",
                     "CAD": "Canada", "CHF": "Switzerland"}.get(c, ""), n)))
            sub = sub[(sub["indicator_key"] == r["indicator_key"]) & sub["actual"].notna()]
            if sub.empty:
                continue
            latest = sub.sort_values("datetime_utc").iloc[-1]
            dt = pd.Timestamp(latest["datetime_utc"])
            if last_known is None or dt > last_known:
                if newest_found is None or dt > newest_found:
                    newest_found = dt
                    newest_row = latest
        if newest_found is not None:
            result[key] = {"status": "actual_available_not_ingested",
                          "release_dt": newest_found.isoformat(),
                          "actual": float(newest_row["actual"]), "name_raw": newest_row["name_raw"]}
        else:
            result[key] = {"status": "no_newer_data"}
    return result
