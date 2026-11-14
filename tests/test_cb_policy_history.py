"""Stance S1.1 A1 - the policy-rate history behind the cycle (config `policy_history:`, src/cb_policy_history.py)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from src import cb_collect as cc
from src import cb_policy_history as PH
from src.cb_sources.official import Obs, OfficialResult, load_official, parse_bis

BANKS = ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF")


def test_one_history_series_per_bank_bis_except_eur_dfr():
    cfg = PH.load_config()
    assert str(cfg["meta"]["from"]) == "2015-01-01"
    by = {s["currency"]: (sid, s) for sid, s in cfg["series"].items()}
    assert sorted(by) == sorted(BANKS) and len(cfg["series"]) == 8
    for cur, (sid, s) in by.items():
        if cur == "EUR":                                                   # BIS gives the MRO until 2024: the ECB's own DFR
            assert (s["provider"], s["role"], s["key"]) == ("ecb", "policy_official_history", "FM/D.U2.EUR.4F.KR.DFR.LEV")
        else:
            assert (s["provider"], s["role"]) == ("bis", "policy_bis_history"), cur
    full = load_official()
    assert not set(cfg["series"]) & set(full["series"])                   # never in the official (decisions) store
    assert all(s.get("role") not in PH.ROLES for s in full["series"].values())


def test_bis_days_without_a_value_are_not_used():
    """WS_CBPOL prints NaN on days without a value; the history keeps only real values (parse_bis itself is unchanged: the
    official store of the decisions keeps what it always stored)."""
    text = "REF_AREA,TIME_PERIOD,OBS_VALUE\nCH,2025-06-19,0.25\nCH,2025-06-20,0\nCH,2025-06-21,NaN\nCH,2025-06-22,\nCH,2025-06-23,0\n"
    got = parse_bis(text, date(2025, 1, 1))["CH"]
    store = {("hist:bis:CH", d): {"date": d, "value": v} for d, v in got}
    h = PH.by_currency(store, {"hist:bis:CH": {"currency": "CHF"}})
    assert h["CHF"][1] == [(date(2025, 6, 19), 0.25), (date(2025, 6, 20), 0.0), (date(2025, 6, 23), 0.0)]


class FakeSrc:
    def __init__(self, obs):
        self.obs, self.since = obs, None
        self.last_status = self.last_note = ""

    def fetch(self, since, state):
        self.since = since
        return OfficialResult("ok", [o for o in self.obs if o.date >= since])


def _o(sid, cur, d, v):
    return Obs(sid, cur, d, v, "percent", datetime(2026, 11, 14, tzinfo=timezone.utc))


def test_run_history_backfills_then_reads_the_lookback_into_yearly_partitions(tmp_path):
    cfg = load_official()
    cfg = {**cfg, "policy_history": {"meta": {"from": "2015-01-01", "lookback_days": 30},
                                     "series": {"hist:bis:CH": cfg["policy_history"]["series"]["hist:bis:CH"]}}}
    obs = [_o("hist:bis:CH", "CHF", date(2015, 1, 15), -0.75), _o("hist:bis:CH", "CHF", date(2025, 6, 20), 0.0),
           _o("hist:bis:CH", "CHF", date(2026, 11, 10), 0.0), _o("hist:bis:CH", "CHF", date(2026, 11, 11), float("nan"))]
    paths = cc.Paths(tmp_path / "cb")
    src = FakeSrc(obs)
    rep, = PH.run_history(paths, date(2026, 11, 14), cfg=cfg, sources={"bis": src})
    assert src.since == date(2015, 1, 1) and rep.merge.new == 3 and rep.last == {"hist:bis:CH": date(2026, 11, 10)}   # NaN dropped
    assert sorted(p.name for p in (tmp_path / "cb" / "policy_history").iterdir()) == [
        "policy_history_2015.parquet", "policy_history_2025.parquet", "policy_history_2026.parquet"]
    src2 = FakeSrc(obs)
    rep, = PH.run_history(paths, date(2026, 11, 14), cfg=cfg, sources={"bis": src2})
    assert src2.since == date(2026, 10, 15) and rep.merge.new == 0                     # stored: only the lookback
    h = PH.by_currency(PH.load(paths), cfg["policy_history"]["series"], date(2026, 1, 1))
    assert h["CHF"] == ("hist:bis:CH", [(date(2015, 1, 15), -0.75), (date(2025, 6, 20), 0.0)])


def test_the_stage_stance_refreshes_the_history_first():
    text = (cc.Path(cc.__file__)).read_text()
    i, j = text.index('if "stance" in stages:'), text.index("SH.append(")
    assert "PH.run_history(" in text[i:j]


def test_committed_history_covers_2015_for_every_bank():
    h = PH.by_currency(PH.load(cc.Paths()), PH.load_config()["series"])
    for cur, (sid, pts) in h.items():
        first = pts[0][0]
        assert first <= (date(2016, 9, 21) if cur == "JPY" else date(2015, 1, 2)), (cur, first)      # BIS JP starts 2016-09-21
        assert all(v == v for _, v in pts)
