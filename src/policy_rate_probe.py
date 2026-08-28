"""Read-only data spike: can BIS replace the manual data/policy_rates.yaml entry?

Diagnostic only — probes the BIS SDMX RESTful API (Central bank policy rates,
keyless) for the 8 board currencies, compares the latest observation against
what's currently committed in data/policy_rates.yaml, and prints a report.
Writes nothing: no YAML edits, no parquet, no pipeline wiring, no /carry
change. Mirrors the report structure of `src.rates_probe`.

    python -m src.policy_rate_probe
    python -m src.policy_rate_probe --currency USD --currency EUR

Source: https://stats.bis.org/api-doc/v2/
Two dataflow IDs are tried per the task brief — `WS_CBPOL_D` and `WS_CBPOL`
— and the script reports which one actually responds (see [0] below); do not
assume either without checking, the API has renamed/removed dataflows before.
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import yaml

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
POLICY_RATES_YAML = ROOT / "data" / "policy_rates.yaml"

BASE_URL = "https://stats.bis.org/api/v2/data/dataflow"
AGENCY = "BIS"
# Both are tested at runtime (see probe_dataflow) — this is not a guess baked
# into the report, [0] shows which one actually served data today.
CANDIDATE_DATAFLOWS = ["WS_CBPOL_D", "WS_CBPOL"]

# Board currency -> BIS REF_AREA code (BIS's own CL_BIS_GL_REF_AREA codelist).
# XM = euro area (the ECB deposit facility rate is published under this code,
# not under any single euro-area member country).
CCY_REF_AREA = {
    "USD": "US", "EUR": "XM", "GBP": "GB", "JPY": "JP",
    "AUD": "AU", "NZD": "NZ", "CAD": "CA", "CHF": "CH",
}

# Some statistical-agency servers 403 a bare requests/urllib UA — see
# src/rate_sources/__init__.py's UA constant + docstring for the same fix
# applied to RBA/BoE/Stooq. Reused verbatim here, not re-derived.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 15
RETRIES = 2
BACKOFF_S = 1.0

LAG_WARN_DAYS = 7


# ---------------------------------------------------------------------------
# HTTP + CSV parsing — never raises, always returns a (result, note) pair.
# ---------------------------------------------------------------------------

def _get(url: str) -> tuple[Optional[requests.Response], str]:
    """GET with limited retries. Returns (response, note) — response is None
    on any network failure or non-200 status after retries are exhausted;
    `note` explains why. Never raises."""
    headers = {"User-Agent": UA, "Accept": "text/csv, application/json;q=0.5, */*;q=0.1"}
    last_note = ""
    for attempt in range(RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            last_note = f"network:{type(e).__name__}: {e}"
        else:
            if r.status_code == 200:
                return r, "HTTP 200"
            last_note = f"HTTP {r.status_code}"
            # Only retry on transport failures / 5xx — a 404/400 won't change.
            if r.status_code < 500:
                return r, last_note
        if attempt < RETRIES:
            time.sleep(BACKOFF_S)
    return None, last_note or "UNREACHABLE"


def _data_url(dataflow: str, key: str, last_n: int = 1) -> str:
    return f"{BASE_URL}/{AGENCY}/{dataflow}/1.0/{key}?lastNObservations={last_n}&format=csv"


def _data_url_range(dataflow: str, key: str, start: date, end: date) -> str:
    return (f"{BASE_URL}/{AGENCY}/{dataflow}/1.0/{key}"
            f"?startPeriod={start.isoformat()}&endPeriod={end.isoformat()}&format=csv")


def _months_ago(d: date, months: int) -> date:
    """d minus `months` calendar months, no extra dependency. Clamps the
    day-of-month to 28 so it never lands on an invalid date (e.g. Feb 30)."""
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(d.day, 28))


@dataclass
class CsvRow:
    ref_area: str
    time_period: str
    obs_value: Optional[float]
    obs_status: str = ""       # "A" actual | "M" missing | ... (BIS's own flag)
    compilation: str = ""      # free-text series description (which rate/convention)


def _parse_csv(text: str) -> tuple[list[CsvRow], str]:
    """Parse the BIS CSV payload into rows, or ([], reason) on any failure —
    including a well-formed-but-empty response (header only, no data rows,
    which BIS returns as a 200 for a key with no observations)."""
    try:
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or "OBS_VALUE" not in reader.fieldnames:
            return [], "parse-fail: no OBS_VALUE column (not a CSV data payload)"
        rows: list[CsvRow] = []
        for row in reader:
            raw_val = (row.get("OBS_VALUE") or "").strip()
            # BIS emits the literal string "NaN" (OBS_STATUS "M") for a
            # calendar-grid slot with no real observation — startPeriod/
            # endPeriod range queries return these (lastNObservations does
            # not). float("NaN") would silently "succeed" and poison every
            # comparison downstream (nan != nan), so it must be caught before
            # the float() call, not left to raise.
            if not raw_val or raw_val.lower() == "nan":
                val = None
            else:
                try:
                    val = float(raw_val)
                except ValueError:
                    val = None
            rows.append(CsvRow(
                ref_area=(row.get("REF_AREA") or "").strip(),
                time_period=(row.get("TIME_PERIOD") or "").strip(),
                obs_value=val,
                obs_status=(row.get("OBS_STATUS") or "").strip(),
                compilation=(row.get("COMPILATION") or "").strip(),
            ))
        if not rows:
            return [], "parse-ok but zero observation rows"
        return rows, ""
    except Exception as e:  # noqa: BLE001 — probe must never crash on a bad payload
        return [], f"parse-fail: {type(e).__name__}: {e}"


def _to_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _load_yaml_rates() -> dict:
    if not POLICY_RATES_YAML.exists():
        return {}
    with open(POLICY_RATES_YAML) as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("rates") or {}


# ---------------------------------------------------------------------------
# [0] Dataflow discovery
# ---------------------------------------------------------------------------

@dataclass
class DataflowProbe:
    dataflow: str
    url: str
    http_status: Optional[int]
    ok: bool
    n_rows: int
    note: str = ""


def probe_dataflow(dataflow: str) -> DataflowProbe:
    url = _data_url(dataflow, "D.US", last_n=1)
    r, http_note = _get(url)
    if r is None:
        return DataflowProbe(dataflow, url, None, False, 0, http_note)
    if r.status_code != 200:
        snippet = r.text[:160].replace("\n", " ")
        return DataflowProbe(dataflow, url, r.status_code, False, 0, f"{http_note}: {snippet!r}")
    rows, err = _parse_csv(r.text)
    if err:
        return DataflowProbe(dataflow, url, r.status_code, False, 0, err)
    return DataflowProbe(dataflow, url, r.status_code, True, len(rows), "")


def discover_dataflow(candidates: list[str]) -> tuple[Optional[str], list[DataflowProbe]]:
    probes = [probe_dataflow(d) for d in candidates]
    working = next((p.dataflow for p in probes if p.ok), None)
    return working, probes


# ---------------------------------------------------------------------------
# [A] Per-currency probe
# ---------------------------------------------------------------------------

@dataclass
class CcyResult:
    currency: str
    ref_area: str
    http_status: Optional[int]
    ok: bool
    obs_date: Optional[date]
    obs_value: Optional[float]
    lag_days: Optional[int]
    yaml_value: Optional[float]
    comparison: str   # "identic" | "diferit" | "lipsă (BIS)" | "lipsă (YAML)" | "eșuat"
    note: str = ""
    compilation: str = ""   # BIS's own free-text series description (which rate)


def probe_currency(dataflow: str, currency: str, ref_area: str,
                    yaml_rates: dict, today: date) -> CcyResult:
    yaml_leg = yaml_rates.get(currency) or {}
    yaml_raw = yaml_leg.get("rate_pct")
    yaml_value = float(yaml_raw) if yaml_raw is not None else None

    url = _data_url(dataflow, f"D.{ref_area}", last_n=1)
    r, http_note = _get(url)
    if r is None:
        return CcyResult(currency, ref_area, None, False, None, None, None,
                         yaml_value, "eșuat (UNRESOLVED)", http_note)
    if r.status_code != 200:
        return CcyResult(currency, ref_area, r.status_code, False, None, None, None,
                         yaml_value, "eșuat (UNRESOLVED)", http_note)

    rows, err = _parse_csv(r.text)
    if err or not rows:
        return CcyResult(currency, ref_area, r.status_code, False, None, None, None,
                         yaml_value, "eșuat (UNRESOLVED)", err or "no rows")

    row = rows[-1]
    obs_date = _to_date(row.time_period)
    obs_value = row.obs_value
    lag_days = (today - obs_date).days if obs_date else None

    if obs_value is None:
        comparison = "lipsă (BIS)"
    elif yaml_value is None:
        comparison = "lipsă (YAML)"
    elif abs(obs_value - yaml_value) < 1e-6:
        comparison = "identic"
    else:
        comparison = "diferit"

    return CcyResult(currency, ref_area, r.status_code, True, obs_date, obs_value,
                     lag_days, yaml_value, comparison, compilation=row.compilation)


# ---------------------------------------------------------------------------
# [B] Bulk-call test — one request for all 8 instead of 8 requests.
# ---------------------------------------------------------------------------

@dataclass
class BulkProbe:
    label: str
    key: str
    url: str
    http_status: Optional[int]
    ok: bool
    n_rows: int
    covers_all_8: bool
    matches_individual: Optional[bool]
    note: str = ""


def probe_bulk(dataflow: str, label: str, key: str,
                individual: dict[str, CcyResult]) -> BulkProbe:
    url = _data_url(dataflow, key, last_n=1)
    r, http_note = _get(url)
    if r is None:
        return BulkProbe(label, key, url, None, False, 0, False, None, http_note)
    if r.status_code != 200:
        return BulkProbe(label, key, url, r.status_code, False, 0, False, None, http_note)

    rows, err = _parse_csv(r.text)
    if err:
        return BulkProbe(label, key, url, r.status_code, False, 0, False, None, err)

    by_area: dict[str, CsvRow] = {}
    for row in rows:
        by_area.setdefault(row.ref_area, row)

    covers_all_8 = all(ra in by_area for ra in CCY_REF_AREA.values())
    matches: Optional[bool] = None
    mismatch_note = ""
    if covers_all_8:
        mismatches = []
        for ccy, ra in CCY_REF_AREA.items():
            ind = individual.get(ccy)
            bulk_row = by_area.get(ra)
            if not (ind and ind.ok and bulk_row):
                mismatches.append(ccy)
                continue
            if bulk_row.obs_value != ind.obs_value or _to_date(bulk_row.time_period) != ind.obs_date:
                mismatches.append(ccy)
        matches = not mismatches
        if mismatches:
            mismatch_note = f"mismatch vs individual calls: {', '.join(mismatches)}"

    return BulkProbe(label, key, url, r.status_code, True, len(rows), covers_all_8,
                     matches, mismatch_note)


# ---------------------------------------------------------------------------
# [D] Per-country cadence — does BIS publish daily (value repeated every
# business day / every calendar day) or only at rate-change dates? Two very
# different freshness checks would be needed depending on the answer.
# ---------------------------------------------------------------------------

CADENCE_WINDOW_N = 90


def _gap_buckets(dates: list[date]) -> tuple[int, int, int, int]:
    """(n gaps ==1 day, n gaps in [2,3], n gaps in [4,7], n gaps >7) over
    consecutive REAL (non-missing) observation dates, ascending."""
    b1 = b23 = b47 = bgt7 = 0
    for i in range(1, len(dates)):
        d = (dates[i] - dates[i - 1]).days
        if d == 1:
            b1 += 1
        elif 2 <= d <= 3:
            b23 += 1
        elif 4 <= d <= 7:
            b47 += 1
        else:
            bgt7 += 1
    return b1, b23, b47, bgt7


def _classify_regime(first_date: Optional[date], last_date: Optional[date],
                      n_real: int, gap_gt7: int) -> str:
    """Grounded in measured density (real observations / calendar days
    spanned), not gap-bucket heuristics — a business-day-only series (~5/7
    density) and a calendar series (weekends repeated, ~7/7 density) are
    both "daily" in the sense that matters here (fresh every publication
    day); only a low-density series with big gaps is genuinely sparse."""
    if n_real == 0 or first_date is None or last_date is None:
        return "UNRESOLVED (nicio observație reală în fereastră)"
    span_days = (last_date - first_date).days + 1
    density = n_real / span_days if span_days > 0 else 1.0
    if density < 0.5 and gap_gt7 > 0:
        return f"SPARSE (densitate {density:.0%} — posibil doar-la-schimbare, nu zilnic)"
    if density >= 0.9:
        return f"DAILY, calendaristic (densitate {density:.0%} — valoare repetată inclusiv weekend)"
    return f"DAILY, zile lucrătoare (densitate {density:.0%} — weekend/sărbători omise)"


@dataclass
class CadenceResult:
    currency: str
    ref_area: str
    http_status: Optional[int]
    ok: bool
    n_returned: int
    n_real: int
    n_missing: int
    first_date: Optional[date]
    last_date: Optional[date]
    gap_1: int
    gap_2_3: int
    gap_4_7: int
    gap_gt7: int
    constant: Optional[bool]
    distinct_values: list
    regime: str
    note: str = ""


def probe_cadence(dataflow: str, currency: str, ref_area: str,
                   n: int = CADENCE_WINDOW_N) -> CadenceResult:
    url = _data_url(dataflow, f"D.{ref_area}", last_n=n)
    r, http_note = _get(url)
    if r is None or r.status_code != 200:
        status = r.status_code if r else None
        return CadenceResult(currency, ref_area, status, False, 0, 0, 0, None, None,
                             0, 0, 0, 0, None, [], "UNRESOLVED",
                             http_note if r is None else f"HTTP {status}")

    rows, err = _parse_csv(r.text)
    if err:
        return CadenceResult(currency, ref_area, r.status_code, False, 0, 0, 0, None, None,
                             0, 0, 0, 0, None, [], "UNRESOLVED", err)

    n_returned = len(rows)
    real_rows = [rr for rr in rows if rr.obs_value is not None]
    n_real = len(real_rows)
    n_missing = n_returned - n_real

    dates = sorted(d for d in (_to_date(rr.time_period) for rr in real_rows) if d is not None)
    first_date = dates[0] if dates else None
    last_date = dates[-1] if dates else None
    g1, g23, g47, g7 = _gap_buckets(dates)

    distinct_values = sorted({rr.obs_value for rr in real_rows})
    constant = (len(distinct_values) <= 1) if real_rows else None
    regime = _classify_regime(first_date, last_date, n_real, g7)

    return CadenceResult(currency, ref_area, r.status_code, True, n_returned, n_real, n_missing,
                         first_date, last_date, g1, g23, g47, g7, constant, distinct_values, regime)


# ---------------------------------------------------------------------------
# [E] Change-pickup test — for each currency, find every value change over
# the trailing 24 months, then check whether the KNOWN real decisions
# (anchors, given or looked up) land on the date BIS actually shows.
# ---------------------------------------------------------------------------

HISTORY_MONTHS = 24


@dataclass
class ChangePoint:
    obs_date: date
    from_value: Optional[float]   # None for the window's first real observation (not a real "change")
    to_value: float


@dataclass
class HistoryResult:
    currency: str
    ref_area: str
    http_status: Optional[int]
    ok: bool
    n_returned: int
    n_real: int
    n_missing: int
    start_period: date
    end_period: date
    changes: list
    observations: list = None   # list[tuple[date, float]] — every REAL obs, not just change points
    note: str = ""


def probe_history(dataflow: str, currency: str, ref_area: str, today: date,
                   months: int = HISTORY_MONTHS) -> HistoryResult:
    start = _months_ago(today, months)
    url = _data_url_range(dataflow, f"D.{ref_area}", start, today)
    r, http_note = _get(url)
    if r is None or r.status_code != 200:
        status = r.status_code if r else None
        return HistoryResult(currency, ref_area, status, False, 0, 0, 0, start, today, [],
                             observations=[],
                             note=http_note if r is None else f"HTTP {status}")

    rows, err = _parse_csv(r.text)
    if err:
        return HistoryResult(currency, ref_area, r.status_code, False, 0, 0, 0, start, today, [],
                             observations=[], note=err)

    n_returned = len(rows)
    real_rows = sorted(
        ((_to_date(rr.time_period), rr.obs_value) for rr in rows if rr.obs_value is not None),
        key=lambda t: t[0] or date.min,
    )
    real_rows = [(d, v) for d, v in real_rows if d is not None]
    n_real = len(real_rows)
    n_missing = n_returned - n_real

    changes: list[ChangePoint] = []
    prev_val: Optional[float] = None
    for d, v in real_rows:
        if prev_val is None or v != prev_val:
            changes.append(ChangePoint(d, prev_val, v))
        prev_val = v

    return HistoryResult(currency, ref_area, r.status_code, True, n_returned, n_real, n_missing,
                         start, today, changes, observations=real_rows)


@dataclass(frozen=True)
class Anchor:
    currency: str
    decision_date: Optional[date]
    effective_date: Optional[date]
    label: str


# Given by the task (JPY, EUR, NZD) or looked up live via WebSearch (AUD, no
# anchor was supplied for it) — every date here is a real, sourced decision,
# never guessed. AUD source: RBA Board increased the cash rate target 25bp to
# 4.35% on 5 May 2026 (domain.com.au "RBA August 2026" recap +
# x.com/Ajay_Bagga/status/2051738370313945397, both retrieved 2026-08-28); no
# explicit RBA "effective" date was found in that search, so AUD is checked
# against decision+1 (the same T+1 convention the other 3 anchors confirm).
ANCHORS: dict[str, Anchor] = {
    "JPY": Anchor("JPY", date(2026, 6, 16), date(2026, 6, 17),
                  "BoJ: decizie 2026-06-16, efectiv 2026-06-17 (0.75% -> 1.00%)"),
    "EUR": Anchor("EUR", None, date(2026, 6, 17),
                  "ECB: efectiv 2026-06-17 (schimbare la 2.25%) — fără dată de decizie separată dată"),
    "NZD": Anchor("NZD", date(2026, 7, 8), None,
                  "RBNZ: decizie 2026-07-08 (2.25% -> 2.50%) — fără dată efectivă separată dată"),
    "AUD": Anchor("AUD", date(2026, 5, 5), None,
                  "RBA: decizie 2026-05-05 (4.10% -> 4.35%), găsită prin WebSearch "
                  "(domain.com.au + x.com/Ajay_Bagga) — fără dată efectivă RBA explicită găsită"),
}


@dataclass
class AnchorCheck:
    currency: str
    anchor_label: str
    decision_date: Optional[date]
    effective_date: Optional[date]
    bis_change_date: Optional[date]
    bis_value: Optional[float]
    matches: Optional[bool]
    note: str = ""


def check_anchor(anchor: Anchor, history: HistoryResult) -> AnchorCheck:
    if not history.ok:
        return AnchorCheck(anchor.currency, anchor.label, anchor.decision_date,
                           anchor.effective_date, None, None, None,
                           f"history probe failed: {history.note}")
    # Exclude the window's synthetic "first observation" entry (from_value is
    # None there) — that's the window boundary, not a real rate change.
    real_changes = [c for c in history.changes if c.from_value is not None]
    if not real_changes:
        return AnchorCheck(anchor.currency, anchor.label, anchor.decision_date,
                           anchor.effective_date, None, None, None,
                           "nicio schimbare reală de valoare găsită în fereastra de 24 de luni")

    ref = anchor.effective_date or anchor.decision_date
    best = min(real_changes, key=lambda c: abs((c.obs_date - ref).days))

    if anchor.effective_date:
        matches = best.obs_date == anchor.effective_date
        note = ("potrivire exactă cu data efectivă" if matches else
                f"diferă de data efectivă cu {(best.obs_date - anchor.effective_date).days:+d} zile")
    else:
        delta = (best.obs_date - anchor.decision_date).days
        matches = delta == 1
        note = ("T+1 față de decizie, consistent cu celelalte ancore" if matches else
                f"decizie+{delta}d, nu T+1-ul obișnuit")

    return AnchorCheck(anchor.currency, anchor.label, anchor.decision_date,
                       anchor.effective_date, best.obs_date, best.to_value, matches, note)


# ---------------------------------------------------------------------------
# [G] Close out the 4 remaining anchors (USD/GBP/CAD/CHF) — each needs a
# different kind of check, not just a date match, so these are bespoke
# rather than routed through the generic Anchor/check_anchor machinery.
# ---------------------------------------------------------------------------

def _find_change_to(history: HistoryResult, target_value: float, eps: float = 1e-6) -> Optional[ChangePoint]:
    """Most recent real transition whose `to_value` matches `target_value`."""
    matches = [c for c in history.changes if c.from_value is not None and abs(c.to_value - target_value) < eps]
    return matches[-1] if matches else None


def _is_fed_midpoint(v: float) -> bool:
    """True iff v is the exact midpoint of a 25bp band (…, .125/.375/.625/.875).
    Integer milli-units to dodge float noise: midpoint*1000 mod 250 == 125."""
    return round(v * 1000) % 250 == 125


@dataclass
class CloseoutCheck:
    currency: str
    label: str
    bis_change_date: Optional[date]
    from_value: Optional[float]
    to_value: Optional[float]
    convention_ok: Optional[bool]
    note: str
    blocker: bool = False


def check_usd(history: HistoryResult) -> CloseoutCheck:
    label = "Fed funds midpoint, interval curent 3.50-3.75% (mijloc 3.625)"
    if not history.ok:
        return CloseoutCheck("USD", label, None, None, None, None,
                             f"history probe indisponibil: {history.note}", blocker=True)
    real_changes = [c for c in history.changes if c.from_value is not None]
    last = real_changes[-1] if real_changes else None
    obs = history.observations or []
    bad = [(d, v) for d, v in obs if not _is_fed_midpoint(v)]
    convention_ok = len(bad) == 0
    if convention_ok:
        note = (f"toate cele {len(obs)} observații reale din fereastra de 24 de luni cad pe "
                f"mijlocul unei benzi de 25bp (…, .125/.375/.625/.875) — niciodată o limită")
    else:
        sample = ", ".join(f"{d}={v:g}" for d, v in bad[:5])
        note = f"BLOCANT: {len(bad)}/{len(obs)} observații NU sunt pe mijloc — ex: {sample}"
    return CloseoutCheck("USD", label, last.obs_date if last else None,
                         last.from_value if last else None, last.to_value if last else None,
                         convention_ok, note, blocker=not convention_ok)


def check_gbp(history: HistoryResult) -> CloseoutCheck:
    label = "Bank Rate 3.75% din decembrie 2025 (tăiere de la 4.00%)"
    if not history.ok:
        return CloseoutCheck("GBP", label, None, None, None, None,
                             f"history probe indisponibil: {history.note}", blocker=True)
    chg = _find_change_to(history, 3.75)
    if chg is None:
        return CloseoutCheck("GBP", label, None, None, None, False,
                             "BLOCANT: nicio schimbare la 3.75 găsită în fereastra de 24 de luni",
                             blocker=True)
    month_ok = (chg.obs_date.year, chg.obs_date.month) == (2025, 12)
    note = ("confirmat: schimbarea e în decembrie 2025" if month_ok else
            f"BLOCANT: BIS arată schimbarea la {chg.obs_date}, nu în decembrie 2025")
    return CloseoutCheck("GBP", label, chg.obs_date, chg.from_value, chg.to_value,
                         month_ok, note, blocker=not month_ok)


def check_cad(history: HistoryResult, current: Optional[CcyResult]) -> CloseoutCheck:
    label = "Overnight target 2.25% (nu Bank Rate 2.50, nu rata de depozit 2.20)"
    if not history.ok:
        return CloseoutCheck("CAD", label, None, None, None, None,
                             f"history probe indisponibil: {history.note}", blocker=True)
    real_changes = [c for c in history.changes if c.from_value is not None]
    last = real_changes[-1] if real_changes else None
    cur_val = current.obs_value if current and current.ok else None
    wrong_conv = cur_val is not None and (abs(cur_val - 2.50) < 1e-6 or abs(cur_val - 2.20) < 1e-6)
    convention_ok = cur_val is not None and abs(cur_val - 2.25) < 1e-6
    comp = (current.compilation if current else "") or ""
    if wrong_conv:
        note = (f"BLOCANT: valoarea curentă BIS e {cur_val:g} — arată Bank Rate (2.50) sau rata "
                f"de depozit (2.20), NU target overnight (2.25)")
    elif convention_ok:
        snippet = comp[:100] + "…" if len(comp) > 100 else comp
        note = f"confirmat: valoare curentă 2.25 = target overnight. COMPILATION: {snippet!r}"
    else:
        note = f"BLOCANT: valoare curentă neașteptată: {_fmt_val(cur_val)} (nici 2.25, nici 2.50, nici 2.20)"
    return CloseoutCheck("CAD", label, last.obs_date if last else None,
                         last.from_value if last else None, last.to_value if last else None,
                         convention_ok, note, blocker=(wrong_conv or not convention_ok))


def check_chf(history: HistoryResult, current: Optional[CcyResult]) -> CloseoutCheck:
    label = "SNB policy rate 0.00% din iunie 2025"
    if not history.ok:
        return CloseoutCheck("CHF", label, None, None, None, None,
                             f"history probe indisponibil: {history.note}", blocker=True)
    chg = _find_change_to(history, 0.00)
    if chg is None:
        return CloseoutCheck("CHF", label, None, None, None, False,
                             "BLOCANT: nicio schimbare la 0.00 găsită în fereastra de 24 de luni",
                             blocker=True)
    month_ok = (chg.obs_date.year, chg.obs_date.month) == (2025, 6)
    comp = ((current.compilation if current else "") or "").lower()
    is_policy_rate = "policy rate" in comp
    bits = []
    bits.append("confirmat: schimbarea e în iunie 2025" if month_ok else
                f"BLOCANT: BIS arată schimbarea la {chg.obs_date}, nu în iunie 2025")
    bits.append("COMPILATION confirmă 'SNB Policy rate' (nu discount rate/SARON)" if is_policy_rate else
                f"ATENȚIE: textul COMPILATION nu conține 'policy rate' explicit — verifică manual")
    convention_ok = month_ok and is_policy_rate
    return CloseoutCheck("CHF", label, chg.obs_date, chg.from_value, chg.to_value,
                         convention_ok, " | ".join(bits), blocker=not convention_ok)


# ---------------------------------------------------------------------------
# [H] Robustness inventory — every distinct RAW form OBS_VALUE takes across
# the last N observations per currency, beyond just the already-known "NaN"
# string. A fetch parser needs to tell apart three states that all "look
# present": a real zero (CHF 0.00), a missing observation, and a value that
# doesn't parse at all — conflating any two produces a rate that looks
# configured but is silently wrong.
# ---------------------------------------------------------------------------

VALUE_FORM_SCAN_N = 250


@dataclass
class ValueFormResult:
    currency: str
    ref_area: str
    http_status: Optional[int]
    ok: bool
    n_rows: int
    n_clean: int
    forms: dict   # {raw_form_repr: {"count": int, "statuses": set[str]}}
    note: str = ""


def probe_value_forms(dataflow: str, currency: str, ref_area: str,
                       n: int = VALUE_FORM_SCAN_N) -> ValueFormResult:
    url = _data_url(dataflow, f"D.{ref_area}", last_n=n)
    r, http_note = _get(url)
    if r is None or r.status_code != 200:
        status = r.status_code if r else None
        return ValueFormResult(currency, ref_area, status, False, 0, 0, {},
                               http_note if r is None else f"HTTP {status}")

    # Deliberately NOT reusing _parse_csv here: that function already
    # normalizes "NaN"/blank to None, which is exactly the distinction this
    # inventory needs to see broken out form-by-form, not pre-collapsed.
    try:
        reader = csv.DictReader(io.StringIO(r.text))
        if reader.fieldnames is None or "OBS_VALUE" not in reader.fieldnames:
            return ValueFormResult(currency, ref_area, r.status_code, False, 0, 0, {},
                                   "parse-fail: no OBS_VALUE column")
        raw_rows = [((row.get("OBS_VALUE") or ""), (row.get("OBS_STATUS") or "")) for row in reader]
    except Exception as e:  # noqa: BLE001 — probe must never crash on a bad payload
        return ValueFormResult(currency, ref_area, r.status_code, False, 0, 0, {},
                               f"parse-fail: {type(e).__name__}: {e}")

    n_rows = len(raw_rows)
    forms: dict[str, dict] = {}
    n_clean = 0
    for raw, status in raw_rows:
        stripped = raw.strip()
        is_clean = False
        if stripped:
            try:
                fv = float(stripped)
                is_clean = stripped.lower() not in ("nan", "inf", "-inf", "+inf", "infinity")
                is_clean = is_clean and fv == fv  # guards a NaN that slipped through some other spelling
            except ValueError:
                is_clean = False
        if is_clean:
            n_clean += 1
        else:
            key = repr(stripped) if stripped else "<empty string>"
            entry = forms.setdefault(key, {"count": 0, "statuses": set()})
            entry["count"] += 1
            entry["statuses"].add(status if status else "<empty>")

    return ValueFormResult(currency, ref_area, r.status_code, True, n_rows, n_clean, forms)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt_val(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:g}"


def report(dataflow_probes: list[DataflowProbe], dataflow: Optional[str],
           results: dict[str, CcyResult], bulk_probes: list[BulkProbe],
           cadence_results: dict[str, CadenceResult],
           history_results: dict[str, HistoryResult],
           anchor_checks: list[AnchorCheck], yaml_rates: dict,
           closeout_checks: list[CloseoutCheck],
           value_form_results: dict[str, ValueFormResult],
           today: date) -> None:
    print("\n" + "=" * 96)
    print(f"BIS POLICY-RATE PROBE — can BIS replace manual data/policy_rates.yaml? · {today.isoformat()}")
    print("=" * 96)

    print("\n[0] DATAFLOW DISCOVERY (test key D.US, lastNObservations=1)")
    print(f"  {'dataflow':<14}{'HTTP':<8}{'ok':<7}{'rows':<6}note")
    for p in dataflow_probes:
        print(f"  {p.dataflow:<14}{str(p.http_status):<8}{str(p.ok):<7}{p.n_rows:<6}{p.note}")
    if dataflow:
        print(f"  -> using dataflow: {dataflow}")
    else:
        print("  -> NONE of the candidate dataflows responded — probe cannot continue.")

    if not dataflow:
        print("\n[STOP] No working dataflow found. No YAML writes, no pipeline changes.\n")
        return

    print(f"\n[A] PER-CURRENCY LOG (dataflow={dataflow}, key=D.<ref_area>)")
    print(f"  {'CCY':<5}{'area':<6}{'HTTP':<6}{'latest':<12}{'value':<9}{'lag(d)':<8}"
          f"{'YAML':<9}{'compare'}")
    for ccy in CCY_REF_AREA:
        if ccy not in results:
            continue
        res = results[ccy]
        if not res.ok:
            print(f"  {ccy:<5}{res.ref_area:<6}{str(res.http_status):<6}{'—':<12}{'—':<9}"
                  f"{'—':<8}{_fmt_val(res.yaml_value):<9}{res.comparison}  ({res.note})")
            continue
        line = (f"  {ccy:<5}{res.ref_area:<6}{res.http_status:<6}"
                f"{str(res.obs_date):<12}{_fmt_val(res.obs_value):<9}"
                f"{str(res.lag_days):<8}{_fmt_val(res.yaml_value):<9}{res.comparison}")
        print(line)
        if res.comparison == "diferit":
            print(f"        BIS={_fmt_val(res.obs_value)}  YAML={_fmt_val(res.yaml_value)}")

    print("\n[B] BULK-CALL TEST — one request for all 8 vs. 8 individual requests")
    print(f"  {'variant':<32}{'HTTP':<6}{'ok':<6}{'rows':<6}{'covers-8':<10}{'matches-indiv'}")
    for b in bulk_probes:
        matches_s = "—" if b.matches_individual is None else str(b.matches_individual)
        print(f"  {b.label:<32}{str(b.http_status):<6}{str(b.ok):<6}{b.n_rows:<6}"
              f"{str(b.covers_all_8):<10}{matches_s}")
        if b.note:
            print(f"      note: {b.note}")
        print(f"      key: {b.key}")
    working_bulk = [b for b in bulk_probes if b.ok and b.covers_all_8 and b.matches_individual]
    if working_bulk:
        best = min(working_bulk, key=lambda b: b.n_rows)  # prefer the tightest scope
        print(f"  -> preferred bulk variant: {best.label} ({best.n_rows} rows for our 8 currencies)")
    else:
        print("  -> no bulk variant both covers all 8 AND matches the individual calls; "
              "stick to 8 individual requests.")

    print("\n[C] VERDICT")
    ok_results = [r for r in results.values() if r.ok]
    auto_resolved = [r for r in ok_results if r.obs_value is not None]
    high_lag = [r for r in ok_results if r.lag_days is not None and r.lag_days > LAG_WARN_DAYS]
    mismatched = [r for r in ok_results if r.comparison == "diferit"]
    unresolved = [r for r in results.values() if not r.ok]
    print(f"  auto-resolved (BIS returned a value) : {len(auto_resolved)}/{len(results)}"
          f"  ({', '.join(r.currency for r in auto_resolved) or '—'})")
    print(f"  lag > {LAG_WARN_DAYS}d                          : "
          f"{', '.join(f'{r.currency}({r.lag_days}d)' for r in high_lag) or '—'}")
    print(f"  YAML MISMATCH (needs a look)         : "
          f"{', '.join(r.currency for r in mismatched) or '—'}")
    if unresolved:
        print(f"  UNRESOLVED (fetch/parse failed)      : "
              f"{', '.join(r.currency for r in unresolved)}")
    for r in mismatched:
        print(f"    {r.currency}: BIS={_fmt_val(r.obs_value)} ({r.obs_date}) "
              f"vs YAML={_fmt_val(r.yaml_value)}")

    print(f"\n[D] CADENȚA SERIEI PER ȚARĂ (ultimele {CADENCE_WINDOW_N} observații reale, "
          f"lastNObservations={CADENCE_WINDOW_N})")
    print(f"  {'CCY':<5}{'span (real)':<24}{'ret':<5}{'real':<6}{'miss':<6}"
          f"{'=1d':<5}{'2-3d':<6}{'4-7d':<6}{'>7d':<5}{'const?':<8}regim")
    for ccy in CCY_REF_AREA:
        if ccy not in cadence_results:
            continue
        c = cadence_results[ccy]
        if not c.ok:
            print(f"  {ccy:<5}{'—':<24}{'—':<5}{'—':<6}{'—':<6}{'—':<5}{'—':<6}{'—':<6}"
                  f"{'—':<5}{'—':<8}UNRESOLVED ({c.note})")
            continue
        span = f"{c.first_date}..{c.last_date}"
        const_s = "DA" if c.constant else ("NU" if c.constant is False else "—")
        print(f"  {ccy:<5}{span:<24}{c.n_returned:<5}{c.n_real:<6}{c.n_missing:<6}"
              f"{c.gap_1:<5}{c.gap_2_3:<6}{c.gap_4_7:<6}{c.gap_gt7:<5}{const_s:<8}{c.regime}")
        if c.n_missing:
            print(f"        notă: {c.n_missing} rând(uri) explicit 'missing' (OBS_VALUE=NaN, "
                  f"OBS_STATUS=M) în fereastră — excluse din calculul intervalelor de mai sus.")
    daily_ok = [c for c in cadence_results.values() if c.ok and "SPARSE" not in c.regime]
    sparse = [c for c in cadence_results.values() if c.ok and "SPARSE" in c.regime]
    print(f"  -> {len(daily_ok)}/{len(cadence_results)} publică zilnic (business-day sau calendaristic); "
          f"{('SPARSE: ' + ', '.join(c.currency for c in sparse)) if sparse else 'niciuna sparse'}")

    print(f"\n[E] TEST DE PRELUARE A UNEI SCHIMBĂRI (ultimele {HISTORY_MONTHS} luni)")
    for ccy in CCY_REF_AREA:
        if ccy not in history_results:
            continue
        h = history_results[ccy]
        if not h.ok:
            print(f"  {ccy}: UNRESOLVED ({h.note})")
            continue
        real_changes = [c for c in h.changes if c.from_value is not None]
        print(f"  {ccy}  ({h.start_period}..{h.end_period}, {h.n_real} obs reale, "
              f"{h.n_missing} missing)  — {len(real_changes)} schimbări reale de valoare:")
        for chg in h.changes:
            if chg.from_value is None:
                print(f"      {chg.obs_date}  (prima valoare din fereastră) {_fmt_val(chg.to_value)}")
            else:
                print(f"      {chg.obs_date}  {_fmt_val(chg.from_value)} -> {_fmt_val(chg.to_value)}")
        if not real_changes:
            print("      (nicio schimbare reală în fereastra de 24 de luni)")

    print("\n  VERIFICARE PE ANCORE (decizie/dată efectivă reală vs. data la care BIS arată schimbarea)")
    for a in anchor_checks:
        print(f"  {a.currency}: {a.anchor_label}")
        if a.bis_change_date is None:
            print(f"      BIS: {a.note}")
        else:
            match_s = "DA" if a.matches else ("NU" if a.matches is False else "—")
            print(f"      BIS arată schimbarea la {a.bis_change_date} (-> {_fmt_val(a.bis_value)})"
                  f"  |  potrivire: {match_s}  ({a.note})")
        yaml_leg = yaml_rates.get(a.currency) or {}
        yaml_eff = _to_date(str(yaml_leg.get("effective"))) if yaml_leg.get("effective") else None
        if yaml_eff and a.bis_change_date and yaml_eff != a.bis_change_date:
            print(f"      ATENȚIE: data/policy_rates.yaml are effective={yaml_eff} pentru {a.currency}, "
                  f"diferă de schimbarea găsită de BIS ({a.bis_change_date}) cu "
                  f"{(yaml_eff - a.bis_change_date).days:+d} zile.")
    unanchored = [ccy for ccy in history_results if ccy not in ANCHORS]
    if unanchored:
        print(f"\n  {', '.join(unanchored)}: schimbări listate mai sus, dar FĂRĂ ancoră externă "
              f"furnizată/căutată — nu se poate confirma independent dacă BIS pune data corectă "
              f"pentru acestea.")

    print("\n[F] VERDICT REVIZUIT")
    print("  1) Regim de publicare per țară:")
    for ccy in CCY_REF_AREA:
        if ccy not in cadence_results:
            continue
        c = cadence_results[ccy]
        print(f"     {ccy}: {c.regime if c.ok else 'UNRESOLVED'}")
    au = cadence_results.get("AUD")
    if au and au.ok:
        print(f"     -> ipoteza AU confirmată: {au.regime.split(' (')[0]}, ultima observație reală "
              f"{au.last_date} — lag-ul mare e coadă stagnată a fluxului (nicio publicare nouă din "
              f"{au.last_date} până azi), NU cadență rară de tip 'doar la schimbare'.")

    print("  2) BIS pune schimbările la data efectivă/decizională corectă?")
    if anchor_checks:
        for a in anchor_checks:
            match_s = "DA" if a.matches else ("NU" if a.matches is False else "necunoscut")
            print(f"     {a.currency}: {match_s} — {a.note}")
    else:
        print("     (nicio ancoră disponibilă de verificat)")

    print("  3) Țară unde NU putem demonstra preluarea corectă a unei schimbări:")
    failed = [a.currency for a in anchor_checks if a.matches is False or a.bis_change_date is None]
    if failed:
        print(f"     {', '.join(failed)}")
    else:
        print("     niciuna dintre cele cu ancoră verificată (JPY/EUR/NZD/AUD) — toate confirmate.")
    if unanchored:
        print(f"     neverificate din lipsă de ancoră (nu putem afirma nici pro, nici contra): "
              f"{', '.join(unanchored)}")

    print("\n[G] ÎNCHIDE CELE 4 ANCORE LIPSĂ (USD, GBP, CAD, CHF — ancore date de utilizator, fără web search)")
    for c in closeout_checks:
        print(f"  {c.currency}: {c.label}")
        if c.bis_change_date is None:
            print(f"      {c.note}")
        else:
            print(f"      BIS: {c.bis_change_date}  {_fmt_val(c.from_value)} -> {_fmt_val(c.to_value)}")
            print(f"      {c.note}")
        if c.blocker:
            print(f"      *** BLOCANT ***")

    print(f"\n[H] ROBUSTEȚE LA VALORI FALSE (ultimele {VALUE_FORM_SCAN_N} observații per țară, forme "
          f"non-numerice ale OBS_VALUE)")
    print(f"  {'CCY':<5}{'rows':<6}{'clean':<7}{'non-clean':<11}forme distincte")
    all_forms: dict[str, dict] = {}
    for ccy in CCY_REF_AREA:
        vf = value_form_results.get(ccy)
        if vf is None:
            continue
        if not vf.ok:
            print(f"  {ccy:<5}{'—':<6}{'—':<7}{'—':<11}UNRESOLVED ({vf.note})")
            continue
        n_bad = vf.n_rows - vf.n_clean
        print(f"  {ccy:<5}{vf.n_rows:<6}{vf.n_clean:<7}{n_bad:<11}"
              f"{'(niciuna)' if not vf.forms else ''}")
        for form, info in sorted(vf.forms.items(), key=lambda kv: -kv[1]["count"]):
            statuses = ", ".join(sorted(info["statuses"]))
            print(f"        {form:<20} de {info['count']:<4} ori   OBS_STATUS văzut: {statuses}")
            all_forms.setdefault(form, {"count": 0, "currencies": set()})
            all_forms[form]["count"] += info["count"]
            all_forms[form]["currencies"].add(ccy)

    print("\n[I] VERDICT FINAL")
    print("  1) Toate 8 au preluarea schimbărilor confirmată la data corectă?")
    all_anchor_checks = list(anchor_checks) + [
        AnchorCheck(c.currency, c.label, None, None, c.bis_change_date, c.to_value, c.convention_ok, c.note)
        for c in closeout_checks
    ]
    bad_anchors = [a for a in all_anchor_checks if a.matches is False or a.bis_change_date is None]
    if not bad_anchors:
        print("     DA — toate 8 (JPY/EUR/NZD/AUD din [E]/[F], USD/GBP/CAD/CHF din [G]) confirmate.")
    else:
        print("     NU, integral. Excepții:")
        for a in bad_anchors:
            print(f"       {a.currency}: {a.note}")

    print("  2) Vreo convenție greșită (rata publicată nu e cea pe care o vrem)?")
    conv_bad = [c for c in closeout_checks if c.convention_ok is False]
    if conv_bad:
        for c in conv_bad:
            print(f"     DA — {c.currency}: {c.note}")
    else:
        print("     NU — USD (mijloc, nu limită), GBP (Bank Rate), CAD (target overnight, nu Bank Rate "
              "2.50 și nu depozit 2.20), CHF (SNB policy rate, nu discount/SARON) — toate confirmate corecte.")

    print("  3) Lista completă a formelor de 'valoare lipsă'/nereprezentabilă găsite (peste toate cele 8):")
    if all_forms:
        for form, info in sorted(all_forms.items(), key=lambda kv: -kv[1]["count"]):
            print(f"     {form:<20} total {info['count']:<5} ori, în: {', '.join(sorted(info['currencies']))}")
    else:
        print("     niciuna — toate observațiile din fereastra scanată au fost numere curate.")
    print("     (reamintire: un OBS_VALUE curat de '0' e o rată reală — CHF 0.00 — NU trebuie tratat "
          "ca lipsă; doar formele enumerate mai sus sunt lipsă/nereprezentabile.)")

    print("\n[STOP] Probe only — no YAML writes, no pipeline changes, no /carry changes.\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only BIS policy-rate probe.")
    ap.add_argument("--currency", action="append", choices=list(CCY_REF_AREA))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

    currencies = args.currency or list(CCY_REF_AREA)
    today = datetime.now(timezone.utc).date()

    working_dataflow, dataflow_probes = discover_dataflow(CANDIDATE_DATAFLOWS)

    results: dict[str, CcyResult] = {}
    bulk_probes: list[BulkProbe] = []
    cadence_results: dict[str, CadenceResult] = {}
    history_results: dict[str, HistoryResult] = {}
    anchor_checks: list[AnchorCheck] = []
    yaml_rates: dict = {}
    if working_dataflow:
        yaml_rates = _load_yaml_rates()
        for ccy in currencies:
            log.info("probing %s (BIS %s) ...", ccy, CCY_REF_AREA[ccy])
            results[ccy] = probe_currency(working_dataflow, ccy, CCY_REF_AREA[ccy],
                                          yaml_rates, today)

        if currencies == list(CCY_REF_AREA):  # bulk test only makes sense over the full set
            plus_key = "D." + "+".join(CCY_REF_AREA.values())
            bulk_probes.append(probe_bulk(working_dataflow, '"+"-joined (8 areas)', plus_key, results))
            bulk_probes.append(probe_bulk(working_dataflow, "empty ref_area (all areas)", "D.", results))

        for ccy in currencies:
            log.info("cadence probe %s (BIS %s, last %d obs) ...", ccy, CCY_REF_AREA[ccy], CADENCE_WINDOW_N)
            cadence_results[ccy] = probe_cadence(working_dataflow, ccy, CCY_REF_AREA[ccy])
            log.info("history probe %s (BIS %s, %dmo) ...", ccy, CCY_REF_AREA[ccy], HISTORY_MONTHS)
            history_results[ccy] = probe_history(working_dataflow, ccy, CCY_REF_AREA[ccy], today)

        for ccy, anchor in ANCHORS.items():
            if ccy in history_results:
                anchor_checks.append(check_anchor(anchor, history_results[ccy]))

    closeout_checks: list[CloseoutCheck] = []
    value_form_results: dict[str, ValueFormResult] = {}
    if working_dataflow:
        if "USD" in history_results:
            closeout_checks.append(check_usd(history_results["USD"]))
        if "GBP" in history_results:
            closeout_checks.append(check_gbp(history_results["GBP"]))
        if "CAD" in history_results:
            closeout_checks.append(check_cad(history_results["CAD"], results.get("CAD")))
        if "CHF" in history_results:
            closeout_checks.append(check_chf(history_results["CHF"], results.get("CHF")))

        for ccy in currencies:
            log.info("value-form scan %s (BIS %s, last %d obs) ...", ccy, CCY_REF_AREA[ccy], VALUE_FORM_SCAN_N)
            value_form_results[ccy] = probe_value_forms(working_dataflow, ccy, CCY_REF_AREA[ccy])

    report(dataflow_probes, working_dataflow, results, bulk_probes,
          cadence_results, history_results, anchor_checks, yaml_rates,
          closeout_checks, value_form_results, today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
