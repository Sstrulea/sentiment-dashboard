"""PHASE 2 (isolated) — score the FF calendar through the EXISTING scoring code.

Reuses `economic_compute.build_payload` + the `economic_fetch` matcher by IMPORT
ONLY — no production file is modified, nothing is wired into the live pipeline
(that is Phase 3). Provides the FF-canonical → MT5-calendar-schema bridge so the
identical scoring runs on the FF source, and a thin z-score baseline that is
rebuilt exclusively from FF (build_payload computes the trailing-K sigma live from
whatever calendar frame it is given — here, only FF rows).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

from .econ_calendar_ff import JB_NOT_LOADED, ensure_provenance_columns
from .economic_compute import build_payload
from .economic_fetch import CompiledMatcher, _load_indicators_cfg

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"


def load_can_be_zero(indicators_cfg: Optional[dict] = None) -> set[str]:
    """Set of indicator_keys where 0.0 is a LEGITIMATE reading BY CONFIG
    (can_be_zero: true) — net-change/level indicators (employment_change,
    interest_rate_decision) with no period-transform suffix to derive it from.

    This is only ONE of two orthogonal reasons `to_scoring_frame` may treat a
    0.0 actual as legitimate, not the whole decision (fix/can-be-zero-transform,
    2026-08): the OTHER is the row's own REAL transform (extract_period_suffix
    on name_raw) being m/m or q/q, gated by a Quality/Strength guard — see
    `to_scoring_frame`'s docstring. `cpi_yoy` is correctly absent from this SET
    for every currency (0.0 on a y/y print is still suspect by config), even
    though CHF/CAD/... cpi_yoy zeros ARE now recoverable via the suffix path."""
    cfg = indicators_cfg or _load_indicators_cfg()
    return {k for k, v in (cfg.get("indicators", {}) or {}).items()
            if (v or {}).get("can_be_zero") is True}

# matcher is country-keyed; map our currency -> the matcher's country label.
CCY2COUNTRY = {
    "USD": "United States", "EUR": "European Union", "GBP": "United Kingdom",
    "JPY": "Japan", "AUD": "Australia", "NZD": "New Zealand",
    "CAD": "Canada", "CHF": "Switzerland",
}

SCORING_COLUMNS = ["currency", "indicator_key", "release_dt", "actual",
                   "consensus", "previous", "source", "name_raw", "actual_origin",
                   "publication"]
# publication (audit 7A): "scored" = the publication config/ff_aliases.yaml asks to
# score (flash, or the final where the config says so — GBP PMIs); "telemetry" = a
# variant listed under excluded_final_variants, kept for display only. NaN (manual,
# cb, mt5 rows) counts as scored.


def build_matcher() -> CompiledMatcher:
    return CompiledMatcher(_load_indicators_cfg().get("matcher", {}) or {})


def effective_consensus(forecast, origin) -> float:
    """Audit 2.2 — the ONE consensus rule, from the provenance recorded at ingest:
      ff        valid, 0.0 included (FF printed "0.0%": a real consensus)
      manual    valid
      ff_blank  NaN (FF printed "": no consensus)
      jb / None a JBlanked 0.0 is its "no forecast" placeholder -> NaN; any other
                value is kept (history before the FF archive)."""
    if forecast is None or pd.isna(forecast):
        return float("nan")
    if origin in ("ff", "manual"):
        return float(forecast)
    if origin == "ff_blank":
        return float("nan")
    return float("nan") if float(forecast) == 0.0 else float(forecast)


ZERO_POSSIBLE_YAML = ROOT / "config" / "ff_zero_possible.yaml"


def load_zero_possible(path: Path = ZERO_POSSIBLE_YAML) -> dict[str, bool]:
    """{canonical_id: bool} — explicit per mapped FF series (audit Z1)."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return {str(k): bool(v) for k, v in (raw.get("zero_possible") or {}).items()}


def _policy_zero_evidence(decisions: Optional[pd.DataFrame], currency: str, day) -> bool:
    """A 0.00 policy rate decided that day, or the next meeting (within ~4
    months) starting from rate_before = 0.00 (decisions.parquet)."""
    if decisions is None or decisions.empty:
        return False
    day = pd.Timestamp(day).date()
    d = decisions[decisions["currency"] == currency]
    md = pd.to_datetime(d["meeting_date"]).dt.date
    same = d[md == day]
    if not same.empty:
        return bool((same["rate_after"].astype(float) == 0.0).any())
    later = d[md > day].assign(_md=md[md > day]).sort_values("_md")
    if later.empty or (pd.Timestamp(later["_md"].iloc[0]) - pd.Timestamp(day)).days > 120:
        return False
    return float(later["rate_before"].iloc[0]) == 0.0


def zero_verdicts(ff_df: pd.DataFrame, zero_possible: dict[str, bool],
                  matcher: Optional[CompiledMatcher] = None,
                  decisions: Optional[pd.DataFrame] = None) -> dict:
    """THE zero rule (audit Z1-Z4), one verdict per FF row whose actual is 0.0:
    {(canonical_id, datetime_utc): (placeholder: bool, recovered: float)}.

      Z1  zero_possible[canonical_id] is False (level / index) -> placeholder.
          No default: a mapped series missing from config/ff_zero_possible.yaml
          raises.
      Z2  zero_possible True -> placeholder if the newest JB payload said
          "Data Not Loaded" (jb_status).
      R2  ... and real ONLY with INDEPENDENT evidence (audit 5A): the `previous`
          the next valid release publishes is within 2 x resolution of 0 and
          does not contradict it (|next.previous| <= tol, the previous-
          consistency tolerance, no floor; release_integrity.SeriesChain,
          nothing across a gap). For interest_rate_decision the evidence is
          data/cb/decisions.parquet: a 0.00 decision that day, or the next
          meeting's rate_before = 0.00. A JB "Good/Bad Data" label is NOT
          evidence: JB derives it from the same 0.0. No evidence ->
          placeholder. A human override is the remaining evidence: a manual
          row, unioned by the caller.
      Z3  same publication (release_integrity.publication_keys, audit 7D), a
          sibling carries a real non-zero actual -> the 0.0 is a placeholder
          of that release (not a divergence), never recovered.
      Z4  a placeholder that is its day's release (latest listed time, no real
          sibling) and has a next.previous is recovered from it
          (actual_origin 'ff_previous'); otherwise recovered = NaN.
          R1: a recovered value is a REVISED value, not the first release: it is
          for display/history only and never enters sigma or a score
          (see scoring_view).
    Rows whose 0.0 is real map to (False, NaN)."""
    from .previous_consistency import EPS, revision_tolerance, series_resolution
    from .release_integrity import SeriesChain, next_valid_previous, resolve_conflicts
    matcher = matcher or build_matcher()
    df = ensure_provenance_columns(ff_df)
    df = df.assign(datetime_utc=pd.to_datetime(df["datetime_utc"]),
                   actual=pd.to_numeric(df["actual"], errors="coerce"),
                   previous=pd.to_numeric(df["previous"], errors="coerce"))
    out: dict = {}
    if df.empty:
        return out
    from .release_integrity import publication_keys
    df["day"] = publication_keys(df)     # 7D: the publication, not the UTC day
    excluded, _ = resolve_conflicts(df, zero_possible)
    df = df[[k not in excluded for k in zip(df["canonical_id"], df["datetime_utc"])]]
    for cid, g in df.groupby("canonical_id", sort=False):
        zeros = g[g["actual"] == 0.0]
        if zeros.empty:
            continue
        r0 = g.iloc[0]
        if matcher.match(CCY2COUNTRY.get(r0.currency, ""), r0.name_canonical) is None:
            continue
        if cid not in zero_possible:
            raise ValueError(f"zero_possible not declared for mapped FF series {cid!r} "
                             f"(config/ff_zero_possible.yaml; no default)")
        zp = zero_possible[cid]
        chain = SeriesChain(g, zp)
        rel = g.sort_values("datetime_utc").drop_duplicates("day", keep="last")
        rep_dt = dict(zip(rel["day"], rel["datetime_utc"]))
        tol, _n = revision_tolerance(rel["actual"].to_numpy(float),
                                     np.append(rel["previous"].to_numpy(float)[1:], np.nan))
        zero_band = min(tol, 2 * series_resolution(np.concatenate([g["actual"].to_numpy(float),
                                                                   g["previous"].to_numpy(float)])))
        policy = cid.endswith("_interest_rate_decision")
        if policy and decisions is None:
            from .policy_rate import load_decisions
            try:
                decisions = load_decisions()
            except Exception:  # noqa: BLE001 — no decisions file: no policy evidence
                decisions = pd.DataFrame()
        real_days = set(g.loc[g["actual"].notna() & (g["actual"] != 0.0), "day"])
        for z in zeros.itertuples(index=False):
            # 4B: "next" = the next period's release with a usable previous
            # (same-publication re-listings and 0/0/0 rows skipped; nothing is
            # compared across a gap) — release_integrity.SeriesChain.
            nprev = next_valid_previous(chain, z.day)
            if z.day in real_days:
                out[(cid, z.datetime_utc)] = (True, float("nan"))                 # Z3
                continue
            if policy:
                confirmed = _policy_zero_evidence(decisions, z.currency, z.day)
            else:
                confirmed = pd.notna(nprev) and abs(float(nprev)) <= zero_band + EPS
            placeholder = (not zp) or z.jb_status == JB_NOT_LOADED or not confirmed
            recovered = float("nan")
            if placeholder and pd.notna(nprev) and z.datetime_utc == rep_dt[z.day]:
                recovered = float(nprev)                                          # Z4
            out[(cid, z.datetime_utc)] = (placeholder, recovered)
    return out


def scoring_view(cal: pd.DataFrame) -> pd.DataFrame:
    """R1 — the frame scoring sees: a value recovered from the next print's
    `previous` (actual_origin 'ff_previous') is a revision, not a first release,
    so it enters neither sigma nor a score (actual -> NaN here). 7A: a
    "telemetry" publication (a final/revision variant the config does not score)
    is dropped. The full frame, both included and marked, stays for display and
    history."""
    if cal is None or cal.empty:
        return cal
    if "publication" in cal.columns:
        tel = cal["publication"] == "telemetry"
        if tel.any():
            cal = cal[~tel].reset_index(drop=True)
    if "actual_origin" not in cal.columns:
        return cal
    rec = cal["actual_origin"] == "ff_previous"
    if not rec.any():
        return cal
    return cal.assign(actual=cal["actual"].where(~rec))


def zero_beside_real_value(cal: pd.DataFrame) -> pd.DataFrame:
    """Z3 across sources: once manual rows are unioned into the scoring frame, an
    FF 0.0 on the same (currency, indicator_key, UTC day) as a real value from
    another row (e.g. a human override) is that release's placeholder -> NaN."""
    if cal is None or cal.empty:
        return cal
    cal = cal.copy()
    day = pd.to_datetime(cal["release_dt"]).dt.date
    real = cal["actual"].notna() & (cal["actual"] != 0.0)
    keys = set(zip(cal.loc[real, "currency"], cal.loc[real, "indicator_key"], day[real]))
    zero_ff = (cal["actual"] == 0.0) & (cal["source"] == "ff")
    hit = zero_ff & pd.Series([k in keys for k in zip(cal["currency"], cal["indicator_key"], day)],
                              index=cal.index)
    cal.loc[hit, "actual"] = float("nan")
    return cal


def to_scoring_frame(ff_df: pd.DataFrame, matcher: Optional[CompiledMatcher] = None,
                     can_be_zero: Optional[set[str]] = None,
                     zero_possible: Optional[dict[str, bool]] = None) -> pd.DataFrame:
    """FF canonical DataFrame → MT5-schema calendar frame that build_payload accepts.

    indicator_key = matcher.match(country(currency), name_canonical). Rows whose
    name_canonical is not modeled for that country are DROPPED (parity with MT5).

    Provenance recorded at ingest, never guessed here (audit 2026-09-23, phase 2):
      consensus  effective_consensus(forecast, forecast_origin)            (2.2)
      actual     0.0 -> zero_verdicts (Z1-Z4): a placeholder is recovered from
                 the next print's `previous` (actual_origin 'ff_previous') when
                 there is one, else NaN; any other actual is kept ('ff').
    Same UTC day: a 0.0 beside a real value is its placeholder (Z3); identical
    real 0.0 copies collapse to one; two different REAL values are left as they
    are (the pre-existing duplicate behavior, untouched). `can_be_zero` is ignored.
    """
    matcher = matcher or build_matcher()
    zp = load_zero_possible() if zero_possible is None else zero_possible
    ff_df = ensure_provenance_columns(ff_df)
    # 7A: ONE flash/final rule, whatever the ingest path that wrote the row.
    from .econ_calendar_ff import load_excluded_finals
    telemetry = {(c, n) for c, names in (load_excluded_finals() or {}).items() for n in (names or {})}
    # 4B: one publication = one row — a same-day row the series chain
    # contradicts leaves scoring (reported by the integrity check).
    from .release_integrity import resolve_conflicts
    conflicts, _ = resolve_conflicts(ff_df, zp)
    if conflicts:
        keys = zip(ff_df["canonical_id"], pd.to_datetime(ff_df["datetime_utc"]))
        ff_df = ff_df[[k not in conflicts for k in keys]]
    verdicts = zero_verdicts(ff_df, zp, matcher)
    from .release_integrity import publication_keys
    pub_key = publication_keys(ff_df.assign(actual=pd.to_numeric(ff_df["actual"], errors="coerce")))
    recs: list[dict] = []
    n_ph = n_rec = n_cons = 0
    for pk, r in zip(pub_key.to_numpy(), ff_df.itertuples(index=False)):
        key = matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
        if key is None:
            continue
        actual, origin = r.actual, "ff"
        v = verdicts.get((r.canonical_id, pd.Timestamp(r.datetime_utc)))
        if v is not None and v[0]:
            n_ph += 1
            if pd.notna(v[1]):
                actual, origin = v[1], "ff_previous"
                n_rec += 1
            else:
                actual = float("nan")
        consensus = effective_consensus(r.forecast, r.forecast_origin)
        if pd.notna(r.forecast) and pd.isna(consensus):
            n_cons += 1
        recs.append({
            "currency": r.currency, "indicator_key": key, "release_dt": r.datetime_utc,
            "actual": actual, "consensus": consensus, "previous": r.previous, "source": "ff",
            "name_raw": r.name_raw, "actual_origin": origin,
            "publication": "telemetry" if (r.currency, r.name_raw) in telemetry else "scored",
            "_pub": pk,
        })
    # identical REAL zeros of one publication (a re-listed 0.0 print that now
    # counts) collapse to one row — the earliest, as the previous guard did; real
    # non-zero duplicates are left exactly as before (pre-existing behavior).
    by_day: dict = {}
    for i, rec in enumerate(recs):
        by_day.setdefault((rec["currency"], rec["indicator_key"], rec["name_raw"],
                           rec["_pub"]), []).append(i)
    for idxs in by_day.values():
        zeros = [i for i in idxs if recs[i]["actual"] == 0.0]
        if len(zeros) > 1 and all(recs[i]["actual"] == 0.0 for i in idxs
                                  if pd.notna(recs[i]["actual"])):
            zeros.sort(key=lambda i: recs[i]["release_dt"])
            for i in zeros[1:]:
                recs[i]["actual"] = float("nan")

    if n_ph or n_cons:
        log.info("Zero rule: %d placeholder 0.0 (%d recovered from next.previous); "
                 "%d consensus value(s) (ff_blank / jb 0.0) -> NaN.", n_ph, n_rec, n_cons)
    return pd.DataFrame(recs, columns=SCORING_COLUMNS)


# z-history sufficiency thresholds by DETECTED cadence (not a uniform monthly rule).
CADENCE_THRESHOLD = {"weekly": 24, "monthly": 24, "quarterly": 8, "annual": 3, "unknown": 24}
# interest_rate_decision is display-only (weight 0, scored via the FRED rate engine,
# not calendar z-surprise) → excluded from the z-history threshold discussion entirely.
NON_ZSCORED_INDICATORS = {"interest_rate_decision"}


def detect_cadence(dates) -> str:
    """Classify a series' cadence from the MEDIAN inter-print interval (days):
    <=10 weekly · <=45 monthly · <=135 quarterly · else annual."""
    d = pd.to_datetime(pd.Series(sorted(set(pd.to_datetime(dates)))))
    if len(d) < 2:
        return "unknown"
    med = float(d.diff().dt.days.dropna().median())
    if med <= 10:
        return "weekly"
    if med <= 45:
        return "monthly"
    if med <= 135:
        return "quarterly"
    return "annual"


def load_configs() -> tuple[dict, dict]:
    with open(INDICATORS_YAML) as f:
        ind = yaml.safe_load(f) or {}
    with open(INSTRUMENTS_YAML) as f:
        inst = yaml.safe_load(f) or {}
    return ind, inst


def score_calendar(calendar_df: pd.DataFrame, as_of: pd.Timestamp,
                   rate_scores: Optional[dict] = None) -> dict:
    """Run the EXISTING build_payload on a calendar frame (MT5 or FF-bridged).
    sentiment_cells=None → category scores are unaffected by COT (they are computed
    pre-sentiment); the before/after table compares only calendar-driven categories."""
    ind, inst = load_configs()
    return build_payload(calendar_df, ind, inst, as_of=as_of,
                         rate_scores=rate_scores or None, sentiment_cells=None)
