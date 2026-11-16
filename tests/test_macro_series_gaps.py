"""Guard (stance-ui-v5): no series of data/cb/macro_series may miss a period between its first and last observation, except a
gap covered by a window of config/known_gaps.yaml for the series' currency (e.g. the 2025 US shutdown: UNRATE 2025-10)."""
from __future__ import annotations

from datetime import date

import yaml

from src import cb_collect as cc
from src import cb_macro


def _next(p: str) -> str:
    if "Q" in p:
        y, q = int(p[:4]), int(p[-1])
        return f"{y + 1}Q1" if q == 4 else f"{y}Q{q + 1}"
    y, m = int(p[:4]), int(p[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def gaps(per: dict) -> dict:
    out = {}
    for sid, rows in per.items():
        ps = sorted(rows)
        p, miss = ps[0], []
        while p < ps[-1]:
            p = _next(p)
            if p not in rows:
                miss.append(p)
        if miss:
            out[sid] = miss
    return out


def test_gap_finder_on_a_hole():
    assert gaps({"x": {"2025Q3": 1, "2026Q1": 1}, "y": {"2025-11": 1, "2026-01": 1}}) == {"x": ["2025Q4"], "y": ["2025-12"]}


def test_no_unexplained_gap_in_macro_series():
    per = cb_macro.by_series(cb_macro.load_macro(cc.Paths()))
    known = yaml.safe_load(open("config/known_gaps.yaml"))["known_gaps"]
    cur = {sid: s["currency"] for sid, s in cb_macro.load_macro_config()["series"].items()}
    bad = {}
    for sid, miss in gaps(per).items():
        for p in miss:
            start = cb_macro.period_start(p)
            if not any(date.fromisoformat(str(k["start"])) <= start <= date.fromisoformat(str(k["end"]))
                       and cur.get(sid) in (k.get("currencies") or [cur.get(sid)]) for k in known):
                bad.setdefault(sid, []).append(p)
    assert bad == {}, bad
