"""2026-10-07: the "Actuals" badge reads the DATA — the rows of "Needs review"
(manual_actuals rows) — not the JB source: 0 rows → ok (even with JB 401),
rows all < 24h old → pending, one ≥ 24h → missing (the only stale state)."""
from __future__ import annotations

import pandas as pd

from src.economic_render import _attach_actuals_status

AS_OF = pd.Timestamp("2026-10-07 12:00")
JB401 = {"last_update": "2026-10-05T01:06:15", "age_days": 2, "stale": True,
         "last_attempt": {"at": "2026-10-07T08:30:29", "status": "fetch_failed", "http_status": 401}}


def _fresh():
    return {"calendar": {"stale": False}, "actuals_pull": dict(JB401), "any_stale": True,
            "integrity": {"warn": 0}}


def _rows(*hours_ago):
    return {"count": len(hours_ago),
            "rows": [{"datetime_utc": AS_OF - pd.Timedelta(hours=h)} for h in hours_ago]}


def test_jb_401_and_no_rows_is_green():
    f = _fresh()
    _attach_actuals_status(f, _rows(), AS_OF)
    a = f["actuals_pull"]
    assert (a["state"], a["n"], a["stale"]) == ("ok", 0, False)
    assert a["last_attempt"]["http_status"] == 401          # kept for the tooltip
    assert f["any_stale"] is False


def test_rows_all_younger_than_24h_are_pending():
    f = _fresh()
    _attach_actuals_status(f, _rows(1, 23.9), AS_OF)
    assert (f["actuals_pull"]["state"], f["actuals_pull"]["n"], f["actuals_pull"]["stale"]) == ("pending", 2, False)
    assert f["any_stale"] is False


def test_a_row_24h_or_older_is_missing():
    f = _fresh()
    _attach_actuals_status(f, _rows(2, 24), AS_OF)
    assert (f["actuals_pull"]["state"], f["actuals_pull"]["n"], f["actuals_pull"]["stale"]) == ("missing", 2, True)
    assert f["any_stale"] is True
