"""2y source adapters (audit 2026-09-23, points 1.3-1.5) — offline, fixtures only."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.rate_sources import MofJgbSource

FIX = Path(__file__).parent / "fixtures" / "rates"


class _Resp:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status
        self.text = content.decode("latin-1")


def _serve(monkeypatch, src, routes: dict[str, bytes | None]):
    """Route src._get(url) to fixture bytes; None -> unreachable."""
    def fake_get(url, extra_headers=None, retries=0):
        body = routes.get(url)
        if body is None:
            src.last_status, src.last_note = "UNREACHABLE", "HTTP 404"
            return None
        return _Resp(body)
    monkeypatch.setattr(src, "_get", fake_get)


# --- 1.3 MoF JGB ------------------------------------------------------------

def test_mof_merges_history_and_current_month(monkeypatch):
    src = MofJgbSource()
    _serve(monkeypatch, src, {
        src.URL_HISTORY: (FIX / "mof_jgbcm_all.csv").read_bytes(),
        src.URL_CURRENT: (FIX / "mof_jgbcm_current.csv").read_bytes(),
    })
    s = src.fetch("JPY")
    assert s is not None, src.last_note
    assert s.latest_date == date(2026, 9, 17) and s.latest_value == 1.868
    d = dict(s.points)
    assert d[date(2026, 8, 31)] == 1.743      # last day of the history file
    assert d[date(2026, 9, 1)] == 1.802       # first day of the current month
    assert len(d) == len(s.points)            # deduped on date


def test_mof_current_month_wins_a_shared_date():
    merged = MofJgbSource.merge([(date(2026, 9, 1), 9.9), (date(2026, 8, 31), 1.7)],
                                [(date(2026, 9, 1), 1.802)])
    assert merged == [(date(2026, 8, 31), 1.7), (date(2026, 9, 1), 1.802)]


def test_mof_without_current_month_ends_at_month_end(monkeypatch):
    src = MofJgbSource()
    _serve(monkeypatch, src, {src.URL_HISTORY: (FIX / "mof_jgbcm_all.csv").read_bytes()})
    s = src.fetch("JPY")
    assert s is not None and s.latest_date == date(2026, 8, 31)


def test_mof_reads_the_2y_column_by_name():
    pts = MofJgbSource().parse_csv((FIX / "mof_jgbcm_current.csv").read_bytes())
    # 1年 is 1.581 and 3年 2.001 on R8.9.17: only 2年 is 1.868
    assert pts[-1] == (date(2026, 9, 17), 1.868)
