"""2y source adapters (audit 2026-09-23, points 1.3-1.5) — offline, fixtures only."""
from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src import rate_fetch, rate_migrations
from src.rate_sources import BoeSource, MofJgbSource

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


# --- 1.4 BoE GLC nominal spot 2.0y --------------------------------------------


def _zip(member: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(member, payload)
    return buf.getvalue()


def test_boe_reads_2y_spot_from_current_month(monkeypatch):
    src = BoeSource()
    xlsx = (FIX / "boe_glc_nominal_current_month.xlsx").read_bytes()
    _serve(monkeypatch, src, {src.URL_LATEST: _zip(src.MEMBER_LATEST, xlsx)})
    s = src.fetch("GBP")
    assert s is not None, src.last_note
    d = dict(s.points)
    assert round(d[date(2026, 9, 18)], 3) == 4.658     # control values
    assert round(d[date(2026, 9, 21)], 3) == 4.561
    assert s.latest_date == date(2026, 9, 21) and s.source == "boe_glc"


def test_boe_without_2y_maturity_is_refused():
    with pytest.raises(ValueError, match="2.0-year"):
        BoeSource.parse_spot_xlsx((FIX / "boe_glc_nominal_no_2y.xlsx").read_bytes())


def test_incremental_source_is_assessed_with_its_stored_history():
    src = BoeSource()
    new = src.parse_spot_xlsx((FIX / "boe_glc_nominal_current_month.xlsx").read_bytes())
    from src.rate_sources import YieldSeries, assess
    s = YieldSeries("GBP", "boe_glc", "2y", "daily", new)
    days = pd.bdate_range("2023-01-02", "2026-08-28")
    stored = pd.DataFrame({"currency": "GBP", "date": days, "tenor": "2y",
                           "yield_pct": 4.0, "source": "boe_glc"})
    other = stored.assign(source="boe")        # legacy 5y rows never join
    merged = rate_fetch._with_stored_history(s, pd.concat([stored, other]))
    assert merged.n_points == len(days) + len(new)
    assert assess(s, date(2026, 9, 22)).status == "SHORT-HIST"
    assert assess(merged, date(2026, 9, 22)).qualifies


def _rates(gbp_source: str, start: str) -> pd.DataFrame:
    days = pd.bdate_range(start, "2026-09-18")
    gbp = pd.DataFrame({"currency": "GBP", "date": days, "tenor": "2y",
                        "yield_pct": 3.9, "source": gbp_source})
    usd = pd.DataFrame({"currency": "USD", "date": days, "tenor": "2y",
                        "yield_pct": 3.5, "source": "fred"})
    return pd.concat([gbp, usd], ignore_index=True)


def test_gbp_migration_drops_legacy_and_backfills():
    hist = [(d.date(), 4.1) for d in pd.bdate_range("2016-01-04", "2026-08-28")]
    out, rep = rate_migrations.migrate_gbp_2y_glc(_rates("boe", "2018-01-02"),
                                                  lambda since: hist)
    gbp = out[out.currency == "GBP"]
    assert set(gbp.source) == {"boe_glc"} and rep["status"] == "migrated"
    assert gbp.date.min() == pd.Timestamp("2016-01-04")
    assert len(gbp) == len(hist)
    assert len(out[out.currency == "USD"]) == len(_rates("boe", "2018-01-02").query("currency == 'USD'"))
    # idempotent: the second pass is a no-op and never calls the download
    out2, rep2 = rate_migrations.migrate_gbp_2y_glc(out, lambda since: 1 / 0)
    assert rep2["status"] == "noop" and out2.equals(out)


def test_gbp_migration_failure_blocks_gbp_write(tmp_path):
    p = tmp_path / "rates.parquet"
    _rates("boe", "2018-01-02").to_parquet(p, index=False)

    def boom(since):
        raise RuntimeError("download failed")
    assert rate_migrations.ensure_all(p, history=boom) == {"GBP": False}
    assert set(pd.read_parquet(p).query("currency == 'GBP'").source) == {"boe"}  # untouched
