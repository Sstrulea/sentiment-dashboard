"""Forex Factory economic-calendar ingest → canonical DataFrame (PHASE 1, standalone).

Transforms either raw FF input form into ONE canonical schema so Phase 2 can wire it
to scoring without downstream changes:
  - JBlanked range payload  (schema: Name/Currency/Date/Actual/Forecast/Previous,
    Date = naive 'YYYY.MM.DD HH:MM:SS' in a US-Eastern+7h wall clock)
  - FF weekly JSON          (schema: title/country/date/impact/forecast/previous[/actual],
    date = ISO-8601 WITH explicit US-Eastern offset)

Canonical output columns:
    canonical_id, currency, name_raw, name_canonical, datetime_utc, actual,
    forecast, previous, released (bool), source ('ff')

Not wired into the pipeline; no existing src/ file is modified. Handles the 5 spike
traps: (1) MT5→FF name matcher via config/ff_aliases.yaml, (2) EUR aggregate whitelist,
(3) released gate by DATE not value, (4) timezone normalization (per-source), (5)
revisions reported as telemetry (never treated as error).
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
ALIASES_YAML = ROOT / "config" / "ff_aliases.yaml"
EUR_WHITELIST_YAML = ROOT / "config" / "ff_eur_whitelist.yaml"

CANON_COLUMNS = [
    "canonical_id", "currency", "name_raw", "name_canonical", "datetime_utc",
    "actual", "forecast", "previous", "released", "source",
    "forecast_origin", "jb_status",
]
# Provenance recorded at ingest (audit 2026-09-23, phase 2 — 2.1/2.3):
#   forecast_origin  ff        FF weekly delivered a consensus (a "0.0%" included)
#                    ff_blank  FF weekly delivered "" (no consensus) -> forecast NaN
#                    jb        the forecast came from JBlanked (range/archive)
#                    manual    entered by hand (no row carries it today)
#                    unknown   migrated row with no evidence (ff_provenance) -> as jb
#   jb_status        the newest JBlanked payload's verdict for the event:
#                    "Data Not Loaded" if Quality or Strength says so, else the
#                    Quality value; None when no JB payload carried the event.
FORECAST_ORIGINS = ("ff", "ff_blank", "jb", "manual", "unknown")
JB_NOT_LOADED = "Data Not Loaded"
PROVENANCE_COLUMNS = ("forecast_origin", "jb_status")


def ensure_provenance_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add missing provenance columns as None (frames built before 2.1)."""
    for c in PROVENANCE_COLUMNS:
        if c not in df.columns:
            df = df.assign(**{c: None})
    return df


def jb_status_of(event: dict) -> Optional[str]:
    q, s = event.get("Quality"), event.get("Strength")
    if JB_NOT_LOADED in (q, s):
        return JB_NOT_LOADED
    return q if q is not None else None
OUR_CCYS = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"}
SOURCE = "ff"

# JBlanked range Date wall clock = US-Eastern + this many hours (validated in spike:
# NFP 08:30 ET shows 15:30 all year → constant ET+7, DST-following).
JBLANKED_ET_OFFSET_HOURS = 7
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

_PERIOD_SUFFIX = re.compile(r"\s+(y/y|q/q|m/m|3m/y|q/y|w/w)\s*$", re.IGNORECASE)
_KNOWN_PERIOD_SUFFIXES = {"y/y", "q/q", "m/m", "3m/y", "q/y", "w/w"}
# Broader than _PERIOD_SUFFIX: matches ANY trailing "<word>/<word>"-shaped token,
# not just the known ones — used by extract_period_suffix to fail loud on an
# UNRECOGNIZED slash-shaped suffix (e.g. a future "6m/y") instead of silently
# treating it as "no suffix". Deliberately does NOT try to catch no-slash
# conventions (e.g. "MoM") — validated empirically (2026-08) against every
# name_raw feeding a scored indicator today: a broader "any short trailing
# token" heuristic false-positives on legitimate suffix-less names ("ISM
# Manufacturing PMI" ends in "PMI", same shape as "MoM").
_TRAILING_SLASH_TOKEN = re.compile(r"\s+(\d*[A-Za-z]+/[A-Za-z]+)\s*$")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_aliases(path: Path = ALIASES_YAML) -> dict[str, dict[str, str]]:
    with open(path) as f:
        return (yaml.safe_load(f) or {}).get("aliases", {}) or {}


def load_eur_whitelist(path: Path = EUR_WHITELIST_YAML) -> set[str]:
    with open(path) as f:
        return set((yaml.safe_load(f) or {}).get("eur_aggregate", []) or [])


def load_excluded_finals(path: Path = ALIASES_YAML) -> dict[str, dict[str, dict]]:
    """{currency: {name_raw: {canonical, reason}}} — later-estimate variants excluded
    from scoring (revision telemetry only). Deterministic flash/final at alias level."""
    with open(path) as f:
        return (yaml.safe_load(f) or {}).get("excluded_final_variants", {}) or {}


# ---------------------------------------------------------------------------
# Pure helpers — timezone, value parsing, id
# ---------------------------------------------------------------------------

def jblanked_to_utc(date_str: str) -> Optional[pd.Timestamp]:
    """Naive 'YYYY.MM.DD HH:MM:SS' (US-Eastern+7h wall) -> tz-naive UTC Timestamp.

    Recipe (validated in spike): subtract 7h to recover the America/New_York wall
    clock, localize with NY's DST rules, convert to UTC. Never assumes a fixed offset.
    """
    try:
        naive = datetime.strptime(str(date_str).strip(), "%Y.%m.%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    et_wall = naive - timedelta(hours=JBLANKED_ET_OFFSET_HOURS)
    aware = et_wall.replace(tzinfo=ET)          # localize as America/New_York
    utc = aware.astimezone(UTC).replace(tzinfo=None)
    return pd.Timestamp(utc)


def iso_to_utc(date_str: str) -> Optional[pd.Timestamp]:
    """ISO-8601 WITH offset (FF weekly, e.g. '2026-07-05T21:00:00-04:00') -> tz-naive UTC.

    The weekly feed carries an explicit US-Eastern offset, so we convert directly
    (documented divergence from the JBlanked naive+7h form)."""
    try:
        aware = datetime.fromisoformat(str(date_str).strip())
    except (ValueError, TypeError):
        return None
    if aware.tzinfo is None:      # defensive: unexpected naive ISO → treat as UTC
        return pd.Timestamp(aware)
    return pd.Timestamp(aware.astimezone(UTC).replace(tzinfo=None))


# Empirically-derived scale factors to the RANGE (JBlanked) convention, which stores
# the BARE FF mantissa (the displayed number with the unit LETTER stripped, NOT scaled).
# Derived by pairing the same series in both forms from the spike cache:
#   K: USD Non-Farm Employment Change  range 311.0  ↔  FF "311K"    → factor 1.0
#   M: USD JOLTS Job Openings          range 11.01  ↔  FF "11.01M"  → factor 1.0
#   B: USD Trade Balance               range -63.2  ↔  FF "-63.2B"  → factor 1.0
#   %: USD CPI y/y                     range 6.0    ↔  FF "6.0%"    → strip %, factor 1.0
# i.e. every suffix strips to the bare mantissa; the old K→×1000 scaling was the bug
# that would turn the first post-switch print into a ~1000× fake surprise.
_SUFFIX_FACTORS = {"K": 1.0, "M": 1.0, "B": 1.0, "T": 1.0}
_NA_TOKENS = {"", "none", "nan", "n/a", "-", "—"}


def normalize_ff_value(raw: Any, *, name: str = "") -> float:
    """Parse an FF actual/forecast/previous cell to the canonical RANGE convention.

    Strips a unit suffix (K/M/B/T) to the bare mantissa (factor 1.0 — see
    _SUFFIX_FACTORS derivation), strips '%' and ','-grouping, empty/'n/a' -> NaN.
    An UNKNOWN suffix raises ValueError (with `name`) — fail loud rather than
    silently mis-scale. Numeric input passes through unchanged (range floats).
    """
    if raw is None:
        return float("nan")
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if s.lower() in _NA_TOKENS:
        return float("nan")
    s = s.replace("%", "").replace(",", "").strip()
    if s.lower() in _NA_TOKENS:
        return float("nan")
    factor = 1.0
    if s and s[-1].isalpha():
        suf = s[-1].upper()
        if suf not in _SUFFIX_FACTORS:
            raise ValueError(f"unknown unit suffix {s[-1]!r} in FF value {raw!r} (name={name!r})")
        factor = _SUFFIX_FACTORS[suf]
        s = s[:-1].strip()
    try:
        return float(s) * factor
    except ValueError:
        raise ValueError(f"unparseable FF value {raw!r} (name={name!r})")


def _strip_period(name: str) -> str:
    return _PERIOD_SUFFIX.sub("", str(name)).strip()


def canonical_id(currency: str, name_canonical: str) -> str:
    """slug(currency, name_canonical) with period suffix stripped → transform-agnostic
    stable id, e.g. ('USD','Core CPI y/y') -> 'usd_core_cpi'."""
    base = f"{currency}_{_strip_period(name_canonical)}".lower()
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", base)).strip("_")


def extract_period_suffix(name_raw: str) -> str:
    """The REAL trailing period-transform token of a raw JBlanked/FF event name,
    e.g. 'CPI m/m' -> 'm/m', 'Employment Change' -> 'none'.

    FAIL LOUD (same discipline as normalize_ff_value): a trailing "<word>/<word>"
    -shaped token that ISN'T one of the known suffixes raises ValueError instead
    of silently returning 'none' — a future feed name using an unmodeled slash
    convention (e.g. '6m/y') must crash a can_be_zero decision that depends on
    it, not silently fall back to always-quarantine. A name with NO trailing
    slash token (levels/counts/decisions — 'Employment Change', 'Federal Funds
    Rate') genuinely has no period suffix: returns 'none', no raise. Validated
    (2026-08) against every name_raw feeding a scored indicator today: 0 raises.
    """
    if not isinstance(name_raw, str):
        raise ValueError(f"name_raw must be str, got {type(name_raw)!r}")
    m = _TRAILING_SLASH_TOKEN.search(name_raw)
    if m is None:
        return "none"
    token = m.group(1).lower()
    if token not in _KNOWN_PERIOD_SUFFIXES:
        raise ValueError(f"unrecognized period suffix {token!r} in name_raw {name_raw!r}")
    return token


# ---------------------------------------------------------------------------
# Canonicalization core (shared by both parsers)
# ---------------------------------------------------------------------------

def _now_utc(now_utc: Optional[pd.Timestamp]) -> pd.Timestamp:
    if now_utc is not None:
        return pd.Timestamp(now_utc)
    return pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))


# HOTFIX 2026-07-29 (Patch A2) — izolare pe rând: un rând care crapă la parsare
# (fail-loud normalize_ff_value) nu mai oprește tot payload-ul; e sărit și numărat.
# Semantică per-apel — _canonicalize e chemat și de parse_jblanked_range.
_FF_ROW_FAILURES: dict[str, int] = {}


def ff_row_failures() -> dict[str, int]:
    """Rânduri sărite din cauza unei erori de parsare la ultimul _canonicalize."""
    return dict(_FF_ROW_FAILURES)


def _canonicalize(rows: list[dict], now_utc: Optional[pd.Timestamp],
                  aliases: dict, eur_wl: set[str],
                  excluded_finals: Optional[dict] = None) -> pd.DataFrame:
    """rows: list of {currency, name_raw, dt_utc, actual_raw, forecast_raw, previous_raw,
    origin, jb_status}; origin "ff" (weekly feed -> forecast_origin ff/ff_blank per
    value) or "jb".
    Applies EUR whitelist (trap 2), alias matcher (trap 1), released gate (trap 3).
    Final/revision variants (excluded_finals) are dropped from scoring at DEBUG (they
    surface as revision telemetry, not unmapped WARNINGs). Value normalization runs
    ONLY AFTER an event passes the matcher — so unmapped events (auctions/speeches,
    e.g. composite '3.86|2.9') are dropped and never trigger the fail-loud normalizer.
    A row whose value fails to parse (genuinely invalid, not an operator-prefix
    recoverable by normalize_ff_value) is skipped and counted in _FF_ROW_FAILURES
    rather than aborting the whole payload — see Patch A2, 2026-07-29."""
    now = _now_utc(now_utc)
    excluded_finals = excluded_finals or {}
    out: list[dict] = []
    n_eur_dropped = n_final = 0
    unmapped_counts: dict[tuple, int] = {}   # (ccy, name_raw) -> count, aggregated
    _FF_ROW_FAILURES.clear()
    for r in rows:
        ccy = r["currency"]
        name_raw = r["name_raw"]
        if ccy not in OUR_CCYS:
            continue
        if ccy == "EUR" and name_raw not in eur_wl:
            n_eur_dropped += 1
            log.debug("EUR non-aggregate dropped: %r", name_raw)
            continue
        name_canonical = (aliases.get(ccy, {}) or {}).get(name_raw)
        if name_canonical is None:
            if name_raw in (excluded_finals.get(ccy, {}) or {}):
                n_final += 1
                log.debug("Final/revision variant excluded from scoring: %s | %r", ccy, name_raw)
                continue
            unmapped_counts[(ccy, name_raw)] = unmapped_counts.get((ccy, name_raw), 0) + 1
            log.debug("Unmapped FF event (excluded): %s | %r", ccy, name_raw)
            continue
        dt = r["dt_utc"]
        if dt is None or pd.isna(dt):
            log.warning("Unparseable datetime, skipped: %s | %r", ccy, name_raw)
            continue
        # normalize values only now (mapped event) — fail loud on a genuine bad value,
        # but isolated to THIS row (Patch A2): a bad cell no longer sinks the payload.
        try:
            actual_v = normalize_ff_value(r["actual_raw"], name=name_raw)
            forecast_v = normalize_ff_value(r["forecast_raw"], name=name_raw)
            previous_v = normalize_ff_value(r["previous_raw"], name=name_raw)
        except ValueError:
            key = f"{ccy}/{name_raw}"
            _FF_ROW_FAILURES[key] = _FF_ROW_FAILURES.get(key, 0) + 1
            continue
        released = pd.Timestamp(dt) < now
        actual = actual_v if released else float("nan")   # trap 3: gate by DATE
        out.append({
            "canonical_id": canonical_id(ccy, name_canonical),
            "currency": ccy,
            "name_raw": name_raw,
            "name_canonical": name_canonical,
            "datetime_utc": pd.Timestamp(dt),
            "actual": actual,
            "forecast": forecast_v,
            "previous": previous_v,
            "released": bool(released),
            "source": SOURCE,
            "forecast_origin": (("ff" if pd.notna(forecast_v) else "ff_blank")
                                if r.get("origin") == "ff" else "jb"),
            "jb_status": r.get("jb_status"),
        })
    if _FF_ROW_FAILURES:
        log.warning("FF ingest: %d rând(uri) sărite (eroare de parsare): %s",
                    sum(_FF_ROW_FAILURES.values()), list(_FF_ROW_FAILURES)[:5])
    if unmapped_counts:
        # AGGREGATED unmapped summary (name_raw × count) — reviewable at each ingest so a
        # future alias gap (a modeled indicator falling through) is visible, not buried in
        # per-event logs. `unmapped_summary()` returns the same for the ingest report.
        total = sum(unmapped_counts.values())
        top = sorted(unmapped_counts.items(), key=lambda kv: -kv[1])[:15]
        log.info("FF ingest: %d unmapped event(s) excluded across %d name(s). Top: %s",
                 total, len(unmapped_counts),
                 "; ".join(f"{c}/{n}×{k}" for (c, n), k in top))
    if n_eur_dropped:
        log.info("FF ingest: %d EUR non-aggregate event(s) dropped.", n_eur_dropped)
    if n_final:
        log.info("FF ingest: %d final/revision variant(s) excluded from scoring (telemetry).", n_final)

    if not out:
        return pd.DataFrame(columns=CANON_COLUMNS)
    df = pd.DataFrame(out, columns=CANON_COLUMNS)
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    # deterministic order → idempotent output
    return df.sort_values(["currency", "canonical_id", "datetime_utc"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _load_json(src: str | Path | list) -> list:
    if isinstance(src, list):
        return src
    p = str(src)
    if p.startswith("http://") or p.startswith("https://"):
        import requests
        r = requests.get(p, headers={"User-Agent": "macro-data-analysis/1.0"}, timeout=30)
        r.raise_for_status()
        return r.json()
    return json.loads(Path(p).read_text())


def parse_jblanked_range(src: str | Path | list, *, now_utc: Optional[pd.Timestamp] = None,
                         aliases: Optional[dict] = None, eur_wl: Optional[set[str]] = None,
                         excluded_finals: Optional[dict] = None) -> pd.DataFrame:
    """Parse a JBlanked range/week payload (list of events) → canonical DataFrame."""
    data = _load_json(src)
    aliases = load_aliases() if aliases is None else aliases
    eur_wl = load_eur_whitelist() if eur_wl is None else eur_wl
    excluded_finals = load_excluded_finals() if excluded_finals is None else excluded_finals
    rows = [{
        "currency": str(e.get("Currency", "")).strip(),
        "name_raw": str(e.get("Name", "")).strip(),
        "dt_utc": jblanked_to_utc(e.get("Date", "")),
        "actual_raw": e.get("Actual"),          # normalized in _canonicalize (post-matcher)
        "forecast_raw": e.get("Forecast"),      # FF 'Forecast' -> 'forecast'
        "previous_raw": e.get("Previous"),
        "origin": "jb",
        "jb_status": jb_status_of(e),
    } for e in data]
    return _canonicalize(rows, now_utc, aliases, eur_wl, excluded_finals)


def parse_ff_weekly(src: str | Path | list, *, now_utc: Optional[pd.Timestamp] = None,
                    aliases: Optional[dict] = None, eur_wl: Optional[set[str]] = None,
                    excluded_finals: Optional[dict] = None) -> pd.DataFrame:
    """Parse the official FF weekly JSON (faireconomy) → canonical DataFrame.
    Fields: title, country(=currency), date(ISO+offset), forecast, previous, [actual]."""
    data = _load_json(src)
    aliases = load_aliases() if aliases is None else aliases
    eur_wl = load_eur_whitelist() if eur_wl is None else eur_wl
    excluded_finals = load_excluded_finals() if excluded_finals is None else excluded_finals
    rows = [{
        "currency": str(e.get("country", "")).strip(),
        "name_raw": str(e.get("title", "")).strip(),
        "dt_utc": iso_to_utc(e.get("date", "")),
        "actual_raw": e.get("actual"),          # often absent (upcoming) -> NaN; normalized post-matcher
        "forecast_raw": e.get("forecast"),
        "previous_raw": e.get("previous"),
        "origin": "ff",
        "jb_status": None,
    } for e in data]
    return _canonicalize(rows, now_utc, aliases, eur_wl, excluded_finals)


# ---------------------------------------------------------------------------
# Revisions telemetry (trap 5) — INFO, never an error
# ---------------------------------------------------------------------------

def flash_final_revisions(src: str | Path | list, *, aliases: Optional[dict] = None,
                          excluded_finals: Optional[dict] = None) -> pd.DataFrame:
    """Pair each EXCLUDED final/revision print with its SCORED flash counterpart
    (same currency+canonical, nearest scored print at/ before the final) and report
    actual(flash) → actual(final) as revision telemetry. INFO only — never scoring.
    The pairing is best-effort (FF has no reference-period field); it is informational
    and does not affect any score (scoring is deterministic: flash only)."""
    data = _load_json(src)
    aliases = load_aliases() if aliases is None else aliases
    excluded_finals = load_excluded_finals() if excluded_finals is None else excluded_finals

    scored: dict[tuple, list] = defaultdict(list)   # (ccy, canonical) -> [(dt, actual)]
    finals: list[tuple] = []
    for e in data:
        ccy = str(e.get("Currency", "")).strip()
        nm = str(e.get("Name", "")).strip()
        if ccy not in OUR_CCYS:
            continue
        dt = jblanked_to_utc(e.get("Date", ""))
        if dt is None:
            continue
        try:
            a = normalize_ff_value(e.get("Actual"), name=nm)
        except ValueError:
            continue
        canon = (aliases.get(ccy, {}) or {}).get(nm)
        if canon is not None:
            scored[(ccy, canon)].append((pd.Timestamp(dt), a))
        elif nm in (excluded_finals.get(ccy, {}) or {}):
            finals.append((ccy, excluded_finals[ccy][nm]["canonical"], pd.Timestamp(dt), a, nm))

    recs: list[dict] = []
    for ccy, canon, fdt, fa, nm in finals:
        cand = [(dt, a) for dt, a in scored.get((ccy, canon), []) if dt <= fdt]
        if not cand or pd.isna(fa):
            continue
        pdt, pa = max(cand, key=lambda x: x[0])
        if pd.isna(pa):
            continue
        recs.append({"currency": ccy, "canonical": canon, "flash_dt": pdt,
                     "flash_actual": pa, "final_name": nm, "final_dt": fdt,
                     "final_actual": fa, "revision": round(fa - pa, 4)})
    if recs:
        log.info("FF flash→final revisions (telemetry): %d point(s).", len(recs))
    return pd.DataFrame(recs, columns=["currency", "canonical", "flash_dt", "flash_actual",
                                       "final_name", "final_dt", "final_actual", "revision"])


def unmapped_summary(src: str | Path | list, *, aliases: Optional[dict] = None,
                     eur_wl: Optional[set[str]] = None,
                     excluded_finals: Optional[dict] = None) -> pd.DataFrame:
    """Aggregated (currency, name_raw) × count of in-scope events that are neither
    mapped nor intentionally excluded — the ingest-report view that makes a future
    alias gap visible (a modeled indicator falling through). EUR non-aggregate
    (member-state) and final/revision variants are NOT counted (intentional drops)."""
    data = _load_json(src)
    aliases = load_aliases() if aliases is None else aliases
    eur_wl = load_eur_whitelist() if eur_wl is None else eur_wl
    excluded_finals = load_excluded_finals() if excluded_finals is None else excluded_finals
    counts: dict[tuple, int] = {}
    for e in data:
        ccy = str(e.get("Currency", e.get("country", ""))).strip()
        nm = str(e.get("Name", e.get("title", ""))).strip()
        if ccy not in OUR_CCYS:
            continue
        if ccy == "EUR" and nm not in eur_wl:
            continue
        if nm in (aliases.get(ccy, {}) or {}) or nm in (excluded_finals.get(ccy, {}) or {}):
            continue
        counts[(ccy, nm)] = counts.get((ccy, nm), 0) + 1
    rows = [{"currency": c, "name_raw": n, "count": k} for (c, n), k in counts.items()]
    return pd.DataFrame(rows, columns=["currency", "name_raw", "count"]).sort_values(
        "count", ascending=False).reset_index(drop=True)


def detect_revisions(df: pd.DataFrame, *, tol: float = 0.06) -> pd.DataFrame:
    """Report where previous(t) != actual(t-1) per canonical series — FF carries the
    REVISED previous, so these are legitimate revisions (telemetry, NOT validation).
    Returns rows: canonical_id, datetime_utc, prior_actual, reported_previous, revision."""
    recs: list[dict] = []
    for cid, g in df.sort_values("datetime_utc").groupby("canonical_id"):
        g = g[g["released"]]
        prev_actual = None
        for _, row in g.iterrows():
            pv = row["previous"]
            if prev_actual is not None and pd.notna(pv) and pd.notna(prev_actual) \
                    and abs(pv - prev_actual) > tol:
                recs.append({
                    "canonical_id": cid, "datetime_utc": row["datetime_utc"],
                    "prior_actual": prev_actual, "reported_previous": pv,
                    "revision": round(pv - prev_actual, 4),
                })
            if pd.notna(row["actual"]):
                prev_actual = row["actual"]
    if recs:
        log.info("FF revisions detected (telemetry): %d point(s).", len(recs))
    return pd.DataFrame(recs, columns=["canonical_id", "datetime_utc", "prior_actual",
                                       "reported_previous", "revision"])


# ---------------------------------------------------------------------------
# HOTFIX 2026-07-29 — gramatica valorilor FF: prefixe de operator.
# FF publică rata BOJ ca '<1.00%' (plafon de bandă). Istoricul din parquet arată
# aceeași rată scrisă plain ('1.00' în iunie 2026), deci stripping-ul operatorului
# CONTINUĂ seria fără discontinuitate — nu e mixare de convenții.
# Doar prefixele de operator sunt acceptate. Orice altă valoare invalidă ridică
# mai departe: contractul fail-loud rămâne intact.
# ---------------------------------------------------------------------------
_ff_value_original = normalize_ff_value
_FF_OPERATORS = "<>≤≥≈~± "


def normalize_ff_value(raw, *, name: str = "") -> float:  # noqa: F811
    try:
        return _ff_value_original(raw, name=name)
    except ValueError:
        if not isinstance(raw, str):
            raise
        stripped = raw.strip().lstrip(_FF_OPERATORS).strip()
        if not stripped or stripped == raw.strip():
            raise                                    # nu era prefix de operator
        return _ff_value_original(stripped, name=name)   # ridică dacă tot e invalid


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    ap = argparse.ArgumentParser(description="Parse an FF calendar payload → canonical table")
    ap.add_argument("path", help="JBlanked range JSON path (or FF weekly with --weekly)")
    ap.add_argument("--weekly", action="store_true", help="parse as FF weekly JSON")
    args = ap.parse_args()
    df = parse_ff_weekly(args.path) if args.weekly else parse_jblanked_range(args.path)
    print(df.to_string())
    print(f"\n{len(df)} canonical rows; {df['released'].sum()} released; "
          f"{df['canonical_id'].nunique()} distinct series.")
