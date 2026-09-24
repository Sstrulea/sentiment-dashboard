"""Audit 6C — the Manual Actuals panel as a signal."""
import pandas as pd

from src.economic_render import panel_relevance


def _rows(*rs):
    return pd.DataFrame([{"canonical_id": c, "currency": "CHF", "indicator_key": k,
                          "datetime_utc": pd.Timestamp(d), "state": "MISSING"} for c, k, d in rs])


def _scoring(k, dates, cons=1.0):
    return pd.DataFrame([{"currency": "CHF", "indicator_key": k, "release_dt": pd.Timestamp(d),
                          "actual": 0.5, "consensus": cons} for d in dates])


MONTHLY = [f"2025-{m:02d}-05 07:30" for m in range(1, 13)] + [f"2026-{m:02d}-05 07:30" for m in range(1, 9)]


def test_release_scored_on_the_adjacent_day_is_a_duplicate():
    rows = _rows(("chf_ppi", "ppi", "2025-04-04 21:00"))           # re-listed the evening before
    aff, hist, dup = panel_relevance(rows, _scoring("ppi", ["2025-04-05 06:35"] + MONTHLY[4:]))
    assert (len(aff), len(hist), dup) == (0, 0, 1)


def test_sigma_window_and_last_print_decide_affects_vs_history():
    sc = _scoring("cpi", MONTHLY)                                 # 20 monthly prints, K = 12
    rows = _rows(("chf_cpi", "cpi", "2026-09-05 07:30"),         # after the last print
                 ("chf_cpi", "cpi", "2025-11-20 07:30"),         # inside the last 12
                 ("chf_cpi", "cpi", "2025-02-20 07:30"))         # older than the window
    aff, hist, dup = panel_relevance(rows, sc)
    assert dup == 0
    assert sorted(aff["datetime_utc"].dt.strftime("%Y-%m-%d")) == ["2025-11-20", "2026-09-05"]
    assert hist["datetime_utc"].dt.strftime("%Y-%m-%d").tolist() == ["2025-02-20"]


def test_series_without_scoring_window_affects():
    aff, hist, dup = panel_relevance(_rows(("chf_x", "retail_sales", "2024-01-05 07:30")), _scoring("cpi", MONTHLY))
    assert (len(aff), len(hist), dup) == (1, 0, 0)


# --- 7C: ⚠ only for real impact ----------------------------------------------------

def test_impact_reasons():
    full = _scoring("cpi", MONTHLY)                                    # 20 prints: window full
    rows = _rows(("chf_cpi", "cpi", "2026-09-05 07:30"),              # after the last print
                 ("chf_cpi", "cpi", "2025-11-20 07:30"))              # inside a full window
    aff, _h, _d = panel_relevance(rows, full)
    got = dict(zip(aff["datetime_utc"].dt.strftime("%Y-%m-%d"), aff["impact"]))
    assert got == {"2026-09-05": "last_print", "2025-11-20": None}
    few = _scoring("pmi", MONTHLY[:4])                                 # 4 prints: fallback
    aff, _h, _d = panel_relevance(_rows(("chf_pmi", "pmi", "2025-02-20 07:30")), few)
    assert aff["impact"].tolist() == ["fallback"]
    part = _scoring("gdp", MONTHLY[:8])                                # 8 prints: window 8/12
    aff, _h, _d = panel_relevance(_rows(("chf_gdp", "gdp", "2025-03-20 07:30")), part)
    assert aff["impact"].tolist() == ["sigma_window"]


def test_not_full_window_takes_old_rows_and_telemetry_never_affects():
    part = _scoring("gdp", MONTHLY[10:18])                             # 8 prints, window 8/12
    rows = _rows(("chf_gdp", "gdp", "2024-06-20 07:30"))               # older than every print
    aff, hist, _d = panel_relevance(rows, part)
    assert (len(aff), aff["impact"].tolist()) == (1, ["sigma_window"])
    tel = pd.DataFrame([{"canonical_id": "eur_x", "currency": "EUR", "indicator_key": "gdp",
                         "name_raw": "Final Manufacturing PMI", "datetime_utc": pd.Timestamp("2026-09-01"),
                         "state": "MISSING"}])
    sc = part.assign(currency="EUR")
    aff, hist, _d = panel_relevance(tel, sc)
    assert (len(aff), len(hist)) == (0, 1)
