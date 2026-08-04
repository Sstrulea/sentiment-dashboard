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

fix/freshness-guard-scored-view (2026-08, docs/diag-aud-inflation-round1.md
Q5): `per_currency_indicator_freshness` was being fed the RAW parquet by its
only production caller (`scripts/check_calendar_freshness.py`) — BEFORE
`ff_scoring.to_scoring_frame`'s zero-placeholder quarantine runs. A row whose
`actual` the quarantine nulls (e.g. a JBlanked "Data Not Loaded" 0.0, AUD
`import_prices` 2026-07-30) still reads as a present, non-null actual to a
guard looking at the raw frame — so it reported "fresh" for a series the
scoring engine itself treats as having no valid print in months. Measured:
raw-frame run reports 3 stale currencies; scored-frame run reports 5 stale
(currency, indicator_key) pairs across 4 currencies. This function now
REQUIRES its caller to pass the SCORED frame (`calendar_df`, post-quarantine
`actual`) — that is the only change to what gets EVALUATED. Nothing here
calls into `ff_scoring`/`economic_compute` differently or touches a
threshold; the caller decides which frame to build, this function just
stopped silently accepting either one as equivalent. `raw_calendar_df` is a
new, optional, second frame (pre-quarantine) used ONLY to label *why* a
non-fresh row is non-fresh (`reason`, below) and to compute `age_raw`
(below) — never `status` or `threshold_days`.

Also adds, per (currency, indicator_key) row:
  - `age_scored` — the ORIGINAL `age_days`, renamed for clarity now that a
    second age exists: days since the last actual that SURVIVED quarantine.
    `status` (`stale`/`fresh`/`no_data`) is still decided from this one,
    unchanged — the series genuinely has no current valid number feeding
    scoring, and that fact belongs in `status` regardless of what's sitting
    quarantined upstream.
  - `age_raw` — days since the most recent RAW row for this pair, ANY
    actual (not filtered by non-null) — i.e. has this event even fired
    recently, independent of data quality. `None` unless `raw_calendar_df`
    is supplied.
  - `reason` (only set when `status != "fresh"`, and only when
    `raw_calendar_df` is supplied): `NO_ROW` if the raw feed has no row with
    a non-null actual more recent than what's showing as fresh — a genuine
    gap; `QUARANTINED` if the raw feed DOES have a more recent row with a
    non-null actual that the scored frame nulled — the AUD `import_prices`
    failure mode. These are different failures needing different follow-up
    (chase the data provider vs. re-examine the quarantine/can_be_zero
    config) and must not be reported identically.
  - `severity` (only set when `status == "stale"`): `STALE` if age is within
    1x-2x its own threshold_days; `DEAD` if age exceeds 2x threshold_days OR
    the indicator's own expected cadence implies >=2 whole periods have been
    missed. **The age fed into this check is `age_raw`, not `age_scored`,
    whenever `reason == QUARANTINED`** — a live series sitting behind a
    quarantined print is blocked, not dead, and grading it on `age_scored`
    alone silently overstates the problem: AUD `import_prices` at
    age_scored=187d/threshold=110d cleared the 2x-periods-missed bar and
    read DEAD, even though its most recent RAW row (age_raw) was 5 days
    old. Measured 2026-08 (docs/diag-aud-inflation-round1.md Q5 + this
    fix's PR): of the 5 real stale pairs, only this one's severity changes
    (DEAD -> STALE) under the corrected input; the other QUARANTINED pair
    (JPY `capital_expenditure`, age_raw=65d) was already STALE either way.

Pure — no I/O beyond what's passed in.
"""
from __future__ import annotations

import pandas as pd

from src.economic_compute import effective_frequency, _max_age_for, _indicator_applies

# Expected inter-print interval per configured frequency tier, days. Used
# ONLY for the `severity` "periods missed" check below — a local, monitoring-
# only convention (matches the expected_gap_days convention already used in
# docs/bucket-c-quality.csv), NOT the scoring engine's own dedup_gap_days or
# max_age_by_frequency (those are untouched, still economic_indicators.yaml's).
_CADENCE_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 91}


def _severity(age_days: float, threshold_days: float, freq: str | None) -> str | None:
    """Only meaningful for an already-`stale` row. `age_days` here is
    whichever age the caller decided is the right one to grade severity on
    (age_scored normally, age_raw when reason == QUARANTINED — see module
    docstring) — this function itself doesn't know or care which."""
    if age_days is None or threshold_days is None:
        return None
    period_days = _CADENCE_DAYS.get(freq)
    periods_missed = (age_days / period_days) if period_days else None
    if age_days > 2 * threshold_days or (periods_missed is not None and periods_missed >= 2):
        return "DEAD"
    return "STALE"


def _raw_signal(raw_calendar_df: "pd.DataFrame | None", ccy: str, key: str,
                scored_last_date, as_of: pd.Timestamp) -> tuple["str | None", "float | None"]:
    """(reason, age_raw) for a (currency, indicator_key) pair. `None, None`
    if no `raw_calendar_df` was supplied — can't tell without it.

    `reason` (NO_ROW vs QUARANTINED) is decided from raw rows WITH a
    non-null actual only — a row nobody ever populated isn't evidence either
    way. `age_raw` is computed from ALL raw rows for the pair regardless of
    whether their actual is null — the "has this event even fired" signal,
    independent of quarantine/data-quality (module docstring)."""
    if raw_calendar_df is None:
        return None, None
    pair_sub = raw_calendar_df[(raw_calendar_df["currency"] == ccy)
                               & (raw_calendar_df["indicator_key"] == key)]
    age_raw = None
    if not pair_sub.empty:
        raw_last_any = pd.to_datetime(pair_sub["release_dt"]).max()
        age_raw = float((as_of.normalize() - raw_last_any.normalize()).days)

    raw_sub = pair_sub[pair_sub["actual"].notna()]
    if raw_sub.empty:
        reason = "NO_ROW"
    else:
        raw_last_date = pd.to_datetime(raw_sub["release_dt"]).max()
        reason = ("QUARANTINED"
                 if (scored_last_date is None or raw_last_date > pd.Timestamp(scored_last_date))
                 else "NO_ROW")
    return reason, age_raw


def per_currency_indicator_freshness(
    calendar_df: pd.DataFrame,
    indicators_cfg: dict,
    as_of: pd.Timestamp,
    currencies: list[str] | None = None,
    raw_calendar_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per (currency, indicator_key) that applies to that currency
    and is actually SCORED (weight > 0 — display-only slots are excluded,
    same convention as compute_currency_scorecard's own aggregation and the
    prior audit). `status` ∈ {stale, fresh, no_data}. Mirrors
    `_max_age_for`'s existing, already-validated per-frequency window —
    not a new statistic.

    `calendar_df` MUST be the SCORED frame — `src.ff_scoring.to_scoring_frame`'s
    output (or equivalent), post zero-placeholder quarantine. `status` and
    `age_scored` are decided against ITS `actual` column only, unchanged
    logic from before this frame requirement was made explicit. Passing the
    raw pre-quarantine frame here reintroduces the exact blind spot this
    function exists to close (module docstring).

    `raw_calendar_df`, optional, same (currency, indicator_key, release_dt,
    actual) shape but PRE-quarantine: used only to populate `reason` and
    `age_raw` on non-fresh rows (module docstring) — never affects
    `status`/`age_scored`/`threshold_days`, but DOES feed `severity` when
    `reason == QUARANTINED` (module docstring).
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
                reason, age_raw = _raw_signal(raw_calendar_df, ccy, key, None, as_of)
                rows.append({"currency": ccy, "indicator_key": key, "status": "no_data",
                            "last_date": None, "age_scored": None, "age_raw": age_raw,
                            "threshold_days": None, "severity": None, "reason": reason})
                continue
            last_date = pd.to_datetime(sub["release_dt"]).max()
            age_scored = (as_of.normalize() - last_date.normalize()).days
            freq = effective_frequency(ind_cfg, defaults, ccy)
            threshold = _max_age_for(ind_cfg, defaults, freq)
            status = "stale" if age_scored > threshold else "fresh"
            reason, age_raw = (_raw_signal(raw_calendar_df, ccy, key, last_date, as_of)
                               if status != "fresh" else (None, None))
            severity = None
            if status == "stale":
                severity_age = age_raw if (reason == "QUARANTINED" and age_raw is not None) else age_scored
                severity = _severity(severity_age, threshold, freq)
            rows.append({"currency": ccy, "indicator_key": key, "status": status,
                        "last_date": last_date, "age_scored": age_scored, "age_raw": age_raw,
                        "threshold_days": threshold, "severity": severity, "reason": reason})
    return pd.DataFrame(rows, columns=["currency", "indicator_key", "status",
                                       "last_date", "age_scored", "age_raw", "threshold_days",
                                       "severity", "reason"])


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
                 "age_scored": r["age_scored"], "age_raw": r["age_raw"],
                 "threshold_days": r["threshold_days"],
                 "severity": r["severity"], "reason": r["reason"]}
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
