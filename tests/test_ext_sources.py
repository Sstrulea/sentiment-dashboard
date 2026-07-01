"""STRAT 3 external fallback — refuses gracefully when sources are unavailable."""
from __future__ import annotations
import pandas as pd
import src.calendar_ext_sources as ext
from src.calendar_fred_fallback import CALENDAR_COLUMNS

def _pq(tmp_path):
    rows=[{"release_dt":pd.Timestamp("2026-05-05"),"country":"Australia","currency":"AUD",
           "indicator_key":"retail_sales","actual":0.5,"consensus":0.4,"previous":0.1,
           "unit":"1","event_raw":"Retail Sales m/m","period":"2026.04.01","source":"mt5"}]
    p=tmp_path/"c.parquet"; pd.DataFrame(rows,columns=CALENDAR_COLUMNS).to_parquet(p,index=False); return p

def test_ext_refuses_when_all_sources_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(ext, "_dispatch", lambda c: None)  # every source unreachable
    rep=ext.apply_ext_fallback(parquet_path=_pq(tmp_path), targets=ext.EXT_TARGETS)
    assert rep["filled"]==0
    assert all(t["decision"]=="REFUSED" for t in rep["targets"])

def test_estat_gated_without_key(monkeypatch):
    monkeypatch.delenv("ESTAT_APP_ID", raising=False)
    assert ext._fetch_estat("0003109741") is None  # no key -> skip, never guess
