"""stance-ui-v5: the golden of the brief (8/8, the inputs of the stance_history snapshot of 2026-11-15), the mirrored cases, the
thresholds = config/cb_stance.yaml v5, the payload (only what is displayed) and the JS (tests/cb_js/test_stance_v5.cjs)."""
from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from src.cb_compute import stance_v5 as V

ROOT = Path(__file__).resolve().parents[1]
FIX = json.loads((ROOT / "tests" / "fixtures" / "cb_stance" / "v5_inputs_2026-11-15.json").read_text())
M = "−"


def inp(c: str) -> V.V5In:
    b = dict(FIX["banks"][c])
    b["band"] = tuple(b["band"]) if b.get("band") else None
    b["path"] = [tuple(p) for p in b.get("path") or []]
    return V.V5In(**b)


GOLDEN = [
    ("USD", "+0.68 Slightly restrictive", "+1.10 Above target", "One more hike likely",
     "Only slightly restrictive for inflation this far above target. The Fed plans one more hike, then a hold, and sees core inflation at 2.5% by end-2027."),
    ("AUD", "+1.36 Restrictive", "+1.10 Above target", "Likely on hold",
     "Already restrictive, and the RBA sees core inflation easing from 3.6% to 2.9% by end-2027 without further hikes."),
    ("NZD", "—", "+0.90 Above target", "More hikes likely", "The RBNZ's own rate track rises from 2.75% to 3.2% by end-2027."),
    ("GBP", "+1.06 Restrictive", "+0.80 Above target", "More hikes likely",
     "Already restrictive, but the BoE sees total inflation rising from 3.5% to 4.0% by end-2027, even with the hikes markets expected."),
    ("EUR", "+0.50 Slightly restrictive", "+0.50 Slightly above target", "More hikes likely",
     "Slightly restrictive, but the ECB sees core inflation still at 2.6% in 2027, not coming down."),
    ("JPY", f"{M}0.55 Slightly stimulative", "+0.40 Slightly above target", "More hikes likely",
     "Still below neutral with inflation above target, and the BoJ sees core inflation rising from 2.4% to 2.7% in FY2027."),
    ("CHF", "—", "0.00 Inside target range", "No reason to move",
     "Core inflation is inside the target range, and the SNB sees total inflation at 0.8% in 2027."),
    ("CAD", f"{M}0.25 Neutral", f"{M}0.15 Near target", "No reason to move",
     "Rate near neutral and core inflation near target. The risk is total inflation: 3.1% now, 2.6% by end-2027."),
]


def pp(x):
    from src.cb_compute.payload import _r2
    if x is None:
        return "—"
    v = _r2(x)
    return "0.00" if v == 0 else ("+" if v > 0 else M) + f"{abs(v):.2f}"


@pytest.mark.parametrize("cur,pos,need,concl,why", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_golden_2026_11_15(cur, pos, need, concl, why):
    r = V.evaluate(inp(cur))
    assert (f"{pp(r['P'])} {r['P_label']}" if r["P"] is not None else "—") == pos
    assert f"{pp(r['N'])} {r['N_label']}" == need
    assert r["conclusion"] == concl and r["reason"] == why


def test_thresholds_are_the_preregistered_ones():
    cfg = yaml.safe_load(open(ROOT / "config" / "cb_stance.yaml"))["v5"]
    assert list(V.SCALE) == cfg["scale_pp"] and V.STEP == cfg["step_pp"] and V.TOL == cfg["borderline_tol_pp"]
    assert cfg["no_move_pp"] == cfg["restrictive_side_pp"] == cfg["forecast_improvement_pp"] == cfg["path_add_pp"] == 0.25


# ---- mirrored cases ----

def test_inflation_below_target_mirrors_the_texts():
    i = replace(inp("AUD"), core=1.4, headline=1.2, fc_value=2.1, rate=2.5, neutral_mid=3.49, neutral_lo=3.0, neutral_hi=4.4, market_add=-0.05)
    r = V.evaluate(i)                     # N = -1.10, P = -0.99 (P.d = 0.99 > 0.25) -> f); the forecast closes |N| by >= 0.25, the path adds < 0.25 in d
    assert r["N_label"] == "Below target" and r["P_label"] == "Stimulative"
    assert r["conclusion"] == "Likely on hold" and r["reason"] == ("Only stimulative for inflation this far below target, and the RBA sees core inflation rising from 1.4% "
                                                                 "to 2.1% by end-2027 without further cuts.")       # d.P = 0.99 < |N| - 0.05
    j = replace(i, rate=3.8)              # P = +0.31: on the wrong side -> e) more cuts
    assert V.evaluate(j)["conclusion"] == "More cuts likely" and "Still above neutral with inflation below target" in V.evaluate(j)["reason"]
    k = replace(i, fc_value=1.5)          # forecast not closing the gap -> "still at", "not going up"
    assert V.evaluate(k)["conclusion"] == "More cuts likely" and "still at 1.5% by end-2027, not going up" in V.evaluate(k)["reason"]


def test_restrictive_bank_with_need_near_zero_does_not_move():
    r = V.evaluate(replace(inp("GBP"), core=2.1))
    assert r["conclusion"] == "No reason to move" and r["reason"].startswith("Rate restrictive and core inflation near target.")


def test_own_path_going_down():
    i = replace(inp("NZD"), path=[("2026Q4", 2.5), ("2027Q4", 2.25)])
    r = V.evaluate(i)
    assert r["conclusion"] == "More cuts likely" and r["reason"] == "The RBNZ's own rate track falls from 2.75% to 2.25% by end-2027."
    d = replace(inp("USD"), path=[("2026", 3.625), ("2027", 3.625)])
    rd = V.evaluate(d)
    assert rd["conclusion"] == "One more cut likely" and "The Fed plans one more cut, then a hold" in rd["reason"]
    assert V.evaluate(replace(inp("USD"), path=[("2026", 3.875), ("2027", 3.875)]))["conclusion"] == "Likely on hold"


def test_unpublished_path_is_noted_and_borderline_is_marked():
    i = replace(inp("AUD"), conditioning="unpublished", market_add=None)
    assert V.evaluate(i)["reason"].endswith("(rate path not published).")
    b = replace(inp("AUD"), fc_value=3.37)                 # the forecast closes |N| by 0.23: within 0.05 of 0.25 -> borderline
    assert V.evaluate(b)["reason"].endswith(", borderline.")
    assert not any("borderline" in V.evaluate(inp(c))["reason"] for c, *_ in GOLDEN)


def test_no_neutral_and_no_path_is_na_with_the_reason():
    i = replace(inp("GBP"), neutral_mid=None, neutral_lo=None, neutral_hi=None, neutral_na="pending")
    r = V.evaluate(i)
    assert r["conclusion"] == "—" and "(pending)" in r["reason"]


# ---- payload ----

def test_written_payload_has_only_what_is_displayed(tmp_path):
    from src import cb_render as R
    R.render(None, tmp_path)
    ov = json.loads((tmp_path / "data" / "cb" / "overview.json").read_text())["stance"]
    assert set(ov) == {"how_to_read", "scale_pp", "na", "banks"} and ov["scale_pp"] == 2.0
    for c, b in ov["banks"].items():
        assert set(b) == {"bank", "asof", "rate_display", "position", "need", "why"}, c
        assert set(b["why"]) == {"conclusion", "reason", "move", "sources"} and b["why"]["sources"]
    usd = json.loads((tmp_path / "data" / "cb" / "usd.json").read_text())["stance"]
    assert set(usd) >= {"mandate", "next_test", "history"} and usd["position"]["v"] == 0.68 and usd["need"]["measure"].startswith("Core PCE 3.1% (")
    text = (tmp_path / "data" / "cb" / "usd.json").read_text()
    for gone in ("pressure", "pivot", "S0", "chip", "cycle_line", "lens", "If held"):
        assert f'"{gone}' not in text, gone
    chf = json.loads((tmp_path / "data" / "cb" / "chf.json").read_text())["stance"]
    assert chf["position"]["na"] == "SNB publishes no neutral" and chf["need"]["target"] == "target 0–2%"
    nzd = json.loads((tmp_path / "data" / "cb" / "nzd.json").read_text())["stance"]
    assert nzd["position"]["na"].startswith("RBNZ neutral due ")


def test_stance_js():
    r = subprocess.run(["node", str(ROOT / "tests" / "cb_js" / "test_stance_v5.cjs")], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_how_to_read_text_with_thresholds_from_config(tmp_path, monkeypatch):
    from src.cb_compute.stance_ui import how_to_read
    cfg = yaml.safe_load(open(ROOT / "config" / "cb_stance.yaml"))["v5"]["scale_pp"]
    t = how_to_read(cfg)
    assert t[0] == ("Position = policy rate − the bank's neutral rate. Need = core inflation − target. Same scale for both: within "
                    "±0.25 pp neutral / near target, up to 0.75 slightly, up to 1.5 plain, beyond that very / well.")
    assert t[1].startswith("Why: inflation near target → no reason to move.") and t[1].endswith("total inflation is used where the bank doesn't forecast core.")
    assert "0.3" in how_to_read([0.3, 0.8, 1.6])[0]                    # the numbers follow the config, not the code
