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
