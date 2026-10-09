"""NZD BKBM - OCR spread (src/cb_compute/nzd_spread.py, scripts/cb_nzd_bkbm_spread.py): the month filter (no OCR change in the
month nor in the 3 after it), the median, NaN days, the incomplete last month - on synthetic series and on REAL cuts of FRED
IR3TIB01NZM156N and BIS WS_CBPOL NZ (2022-01 .. 2026-10, captured 2026-10-09)."""
from __future__ import annotations

import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from src.cb_compute.nzd_spread import bkbm_ocr_spread, ocr_months
from src.cb_loader import load_estimated_spreads
from src.cb_sources.official import parse_bis, parse_fred

FIX = Path(__file__).parent / "fixtures" / "cb"
ROOT = Path(__file__).resolve().parents[1]
D = date


def daily(path: list, start: date, end: date) -> list:
    """[(from_date, rate)] step function -> one observation per calendar day."""
    out, d = [], start
    while d <= end:
        out.append((d, [r for f, r in path if f <= d][-1]))
        d += timedelta(days=1)
    return out


def monthly(values: dict) -> list:
    return [(D(y, m, 1), v) for (y, m), v in sorted(values.items())]


def test_months_with_an_ocr_change_in_them_or_in_the_next_three_are_dropped():
    ocr = daily([(D(2024, 1, 1), 5.50), (D(2024, 6, 12), 5.25)], D(2024, 1, 1), D(2024, 12, 31))
    m3 = monthly({(2024, m): 5.50 + 0.01 * m for m in range(1, 13)})
    res = bkbm_ocr_spread(m3, ocr, (2024, 1))
    kept = [m for m, _ in res.months]
    assert kept == ["2024-01", "2024-02", "2024-07", "2024-08", "2024-09"]                  # Mar..Jun see the June cut; Oct..Dec lack 3 months ahead
    assert [round(x, 9) for _, x in res.months] == [1.0, 2.0, 32.0, 33.0, 34.0]             # 3M (5.50 + 0.01 m) - mean OCR, bp
    assert res.value_bp == pytest.approx(statistics.median([1.0, 2.0, 32.0, 33.0, 34.0]))
    assert res.n_months == 5 and res.window == ("2024-01", "2024-12") and res.iqr_bp == pytest.approx((2.0, 33.0))


def test_the_spread_is_against_the_monthly_mean_ocr_and_nan_days_are_skipped():
    ocr = daily([(D(2025, 1, 1), 3.00)], D(2025, 1, 1), D(2025, 8, 31))
    ocr = [(d, float("nan") if d.weekday() >= 5 else v) for d, v in ocr]                    # BIS: NaN on weekends
    mean, changed = ocr_months(ocr)
    assert mean[(2025, 3)] == pytest.approx(3.00) and not changed
    res = bkbm_ocr_spread(monthly({(2025, m): 3.18 for m in range(1, 9)} | {(2025, 4): float("nan")}), ocr, (2025, 1))
    assert [m for m, _ in res.months] == ["2025-01", "2025-02", "2025-03", "2025-05"] and res.value_bp == pytest.approx(18.0)


def test_an_incomplete_last_ocr_month_is_not_a_month_mean():
    ocr = daily([(D(2025, 1, 1), 3.00)], D(2025, 1, 1), D(2025, 6, 10))                     # June has 10 days only
    res = bkbm_ocr_spread(monthly({(2025, m): 3.10 for m in range(1, 7)}), ocr, (2025, 1))
    assert res.window == ("2025-01", "2025-05") and [m for m, _ in res.months] == ["2025-01", "2025-02"]


def test_no_quiet_month_gives_no_value():
    ocr = daily([(D(2025, 1, 1), 3.0)] + [(D(2025, m, 15), 3.0 - 0.25 * m) for m in range(2, 9)], D(2025, 1, 1), D(2025, 9, 30))
    res = bkbm_ocr_spread(monthly({(2025, m): 3.0 for m in range(1, 10)}), ocr, (2025, 1))
    assert res.value_bp is None and res.reason


def test_real_cuts_fred_ir3tib_and_bis_cbpol():
    m3 = parse_fred((FIX / "nzd_fred_ir3tib_cut.csv").read_text(), D(2022, 1, 1))
    ocr = parse_bis((FIX / "nzd_bis_cbpol_cut.csv").read_text(), D(2022, 1, 1))["NZ"]
    res = bkbm_ocr_spread(m3, ocr, (2022, 1))
    # independent recomputation: month means of the non-NaN days, changes between consecutive observed days
    obs = sorted((d, v) for d, v in ocr if v == v)
    by: dict = {}
    for d, v in obs:
        by.setdefault((d.year, d.month), []).append(v)
    ch = {(d.year, d.month) for (p, a), (d, b) in zip(obs, obs[1:]) if a != b}
    last = max(by) if obs[-1][0].day >= 28 else sorted(by)[-2]
    exp = []
    for d, v in m3:
        k = (d.year, d.month)
        ahead = [((k[0] * 12 + k[1] - 1 + j) // 12, (k[0] * 12 + k[1] - 1 + j) % 12 + 1) for j in range(4)]
        if k in by and ahead[-1] <= last and not any(a in ch for a in ahead):
            exp.append((v - sum(by[k]) / len(by[k])) * 100)
    assert res.n_months == len(exp) >= 1 and res.value_bp == pytest.approx(statistics.median(exp))
    assert res.window[0] == "2022-01"


def test_the_committed_spread_file_and_the_loader():
    doc = yaml.safe_load((ROOT / "config" / "cb_nzd_spread.yaml").read_text())
    assert 10 <= doc["value_bp"] <= 45 and doc["n_months"] >= 24                              # the plausibility bounds of the spec
    assert isinstance(doc["computed_on"], date) and "IR3TIB01NZM156N" in doc["sources"]["rate_3m"]["series"] and "WS_CBPOL" in doc["sources"]["ocr"]["series"]
    est = load_estimated_spreads()["NZD"]
    assert est["value_bp"] == doc["value_bp"] and est["policy"] == "OCR"
    assert load_estimated_spreads(ROOT / "config" / "no_such_file.yaml") == {}


def test_the_script_document_shape(monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import cb_nzd_bkbm_spread as S
    m3 = parse_fred((FIX / "nzd_fred_ir3tib_cut.csv").read_text(), D(2022, 1, 1))
    ocr = parse_bis((FIX / "nzd_bis_cbpol_cut.csv").read_text(), D(2022, 1, 1))["NZ"]
    res = bkbm_ocr_spread(m3, ocr, (2022, 1))
    doc = S.document(res, "fred-url", "bis-url", D(2026, 10, 9))
    assert set(doc) >= {"value_bp", "n_months", "window", "iqr_bp", "computed_on", "sources"} and doc["computed_on"] == D(2026, 10, 9)
    assert doc["value_bp"] == round(res.value_bp, 1) and len(doc["months"]) == res.n_months
