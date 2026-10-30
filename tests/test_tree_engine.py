"""feat/tree-engine — src/tree: schema validation, topological order, missing
nodes, as-of leakage, hierarchical combination, vetoes, determinism; plus the
measurement helpers of scripts/measure/tree_jpy_v0.py on synthetic data."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.tree import schema
from src.tree.data import Inputs
from src.tree.engine import CycleError, Engine, classify, combine, eval_nodes, topo_order
from src.tree.nodes import NodeResult
from src.tree.run import dumps

ROOT = Path(__file__).resolve().parents[1]
DAYS = pd.bdate_range("2014-01-01", "2020-12-31")


def _series(name, values, dates=DAYS, lag=1):
    d = pd.DatetimeIndex(dates)
    return pd.DataFrame({"series": name, "date": d, "avail": d + pd.Timedelta(days=lag),
                         "value": np.asarray(values, float)})


def _synthetic(seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(DAYS)
    walk = lambda s0, sd: s0 + np.cumsum(rng.normal(0, sd, n))  # noqa: E731
    frames = [_series("DGS2", walk(2, 0.03)), _series("DGS10", walk(3, 0.03)),
              _series("VIXCLS", np.abs(walk(18, 0.5)) + 9), _series("VXVCLS", np.abs(walk(20, 0.4)) + 10),
              _series("SP500", np.exp(walk(7.5, 0.01))), _series("DCOILWTICO", np.exp(walk(4, 0.02))),
              _series("DEXJPUS", np.exp(walk(4.7, 0.006))), _series("JGB2Y", walk(0.1, 0.01))]
    tue = pd.date_range("2014-01-07", "2020-12-29", freq="7D")
    frames.append(_series("COT_JPY", rng.normal(0, 50000, len(tue)), tue, lag=6))
    return pd.concat(frames, ignore_index=True)        # no BAMLH0A0HYM2: hy node missing


@pytest.fixture(scope="module")
def cfg():
    return schema.load("jpy")


@pytest.fixture(scope="module")
def inputs():
    return Inputs(_synthetic())


def _res(nid, value=None, z=None, missing=False):
    return NodeResult(nid, value, z, "2020-01-03", "2020-01-02", "missing" if missing else "ok",
                      "context", {"reason": "test"} if missing else {})


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_real_configs_validate(cfg):
    glob, tree = cfg
    assert tree["tree_id"] == "jpy"
    assert set(tree["regime_switch"]["regimes"]) == {"calm", "growth_scare", "inflation_shock",
                                                    "liquidity_stress"}


@pytest.mark.parametrize("mutate,msg", [
    (lambda t: t["nodes"][0].pop("status"), "missing keys"),
    (lambda t: t["nodes"][0].update(fn="nope"), "not registered"),
    (lambda t: t["nodes"][0].update(status="maybe"), "status"),
    (lambda t: t["nodes"][0].update(stale_after_days=0), "stale_after_days"),
    (lambda t: t["nodes"].append(copy.deepcopy(t["nodes"][0])), "duplicate node id"),
    (lambda t: t.update(extra=1), "unknown keys"),
    (lambda t: t["regime_switch"]["regimes"].pop("calm"), "must configure exactly"),
    (lambda t: t["regime_switch"]["regimes"]["calm"]["primary"].update(node="ghost"), "unknown node"),
    (lambda t: t["regime_switch"]["regimes"]["calm"]["modifiers"][0]["rule"].update(type="vibes"), "rule.type"),
    (lambda t: t["regime_switch"]["regimes"]["calm"]["modifiers"][0].update(agree_mult=0), "> 0"),
    (lambda t: t["decision"].update(conviction_tiers=[2, 1, 3]), "conviction_tiers"),
    (lambda t: t["regime_switch"].update(classifier="other"), "unknown classifier"),
])
def test_schema_rejects_bad_trees(cfg, mutate, msg):
    glob, tree = cfg
    bad = copy.deepcopy(tree)
    mutate(bad)
    with pytest.raises(schema.TreeSchemaError, match=msg):
        schema.validate_tree(bad, glob)


def test_schema_rejects_global_node_redefined_in_tree(cfg):
    glob, tree = cfg
    bad = copy.deepcopy(tree)
    bad["nodes"].append({**copy.deepcopy(glob["nodes"][0])})
    with pytest.raises(schema.TreeSchemaError, match="already defined in _global"):
        schema.validate_tree(bad, glob)


def test_schema_rejects_bad_classifier(cfg):
    glob, _ = cfg
    bad = copy.deepcopy(glob)
    bad["classifiers"]["risk_regime_v0"]["rules"]["calm2"] = {"node": "vix_level", "op": ">", "value": 1}
    with pytest.raises(schema.TreeSchemaError, match="precedence"):
        schema.validate_global(bad)
    bad = copy.deepcopy(glob)
    bad["classifiers"]["risk_regime_v0"]["rules"]["growth_scare"]["all"][0]["op"] = "=~"
    with pytest.raises(schema.TreeSchemaError, match="op"):
        schema.validate_global(bad)


def test_load_reports_invalid_yaml(tmp_path):
    (tmp_path / "_global.yml").write_text("nodes: [\n")
    with pytest.raises(schema.TreeSchemaError, match="invalid YAML"):
        schema.load("jpy", tmp_path)


# ---------------------------------------------------------------------------
# Topological order
# ---------------------------------------------------------------------------

def _n(i, inputs):
    return {"id": i, "fn": "level", "inputs": inputs, "status": "context", "stale_after_days": 7}


def test_topo_order_puts_dependencies_first_and_keeps_yaml_order():
    nodes = [_n("c", ["b"]), _n("a", ["RAW"]), _n("b", ["a", "RAW"]), _n("d", ["RAW"])]
    assert [n["id"] for n in topo_order(nodes)] == ["a", "d", "b", "c"]


def test_topo_order_accepts_already_computed_globals():
    nodes = [_n("t1", ["g1"]), _n("t2", ["t1"])]
    assert [n["id"] for n in topo_order(nodes, {"g1"})] == ["t1", "t2"]


def test_topo_order_detects_cycles():
    with pytest.raises(CycleError):
        topo_order([_n("a", ["b"]), _n("b", ["a"])])


# ---------------------------------------------------------------------------
# Missing nodes
# ---------------------------------------------------------------------------

def test_missing_input_series_and_dependants(cfg, inputs):
    glob, tree = cfg
    d = Engine(glob, inputs).run(tree, "2019-06-07")
    assert d["nodes"]["hy_oas_chg_20d"]["quality"] == "missing"
    assert d["nodes"]["hy_oas_chg_20d"]["meta"]["reason"] == "no data at asof"
    # a node fed by a missing node is missing with the reason
    nodes = [{"id": "x", "fn": "chg", "inputs": ["NOPE"], "params": {"n": 1}, "status": "context",
              "stale_after_days": 7},
             {"id": "flag", "fn": "abs_gt_flag", "inputs": ["x"], "params": {"threshold": 1},
              "status": "context", "stale_after_days": 7}]
    r = eval_nodes(nodes, inputs, pd.Timestamp("2019-06-07"))
    assert r["flag"].missing and "input node missing: x" in r["flag"].meta["reason"]


def test_stale_node_is_missing(cfg):
    glob, tree = cfg
    df = _synthetic()
    df = df[~((df["series"] == "DGS2") & (df["date"] > "2019-05-01"))]
    d = Engine(glob, Inputs(df)).run(tree, "2019-06-07")
    assert d["nodes"]["us2y_chg_20d"]["quality"] == "missing"
    assert d["nodes"]["us2y_chg_20d"]["meta"]["reason"].startswith("stale")


def test_missing_modifier_is_skipped_with_reason(cfg):
    _, tree = cfg
    res = {"us2y_chg_20d": _res("us2y_chg_20d", -30, -1.5),
           "jpy_cot_pctile": _res("jpy_cot_pctile", missing=True),
           "oil_chg_20d": _res("oil_chg_20d", 5, 1.4)}
    d = combine(tree, "calm", res)
    assert d["direction"] == 1
    assert d["skipped"] == [{"node": "jpy_cot_pctile", "role": "modifier", "reason": "test"}]
    assert d["conviction"] == pytest.approx(1.5 * 0.8)        # oil disagrees (JPY down)


def test_missing_primary_gives_no_direction(cfg):
    _, tree = cfg
    res = {"spx_ret_20d": _res("spx_ret_20d", missing=True),
           "us2y_chg_20d": _res("us2y_chg_20d", 1, 1.0), "jpy_cot_pctile": _res("jpy_cot_pctile", 50, 0)}
    d = combine(tree, "growth_scare", res)
    assert (d["direction"], d["conviction"], d["reason"]) == (0, 0.0, "primary missing")


def test_undetermined_classifier_rule_does_not_fire(cfg):
    glob, _ = cfg
    cls = glob["classifiers"]["risk_regime_v0"]
    res = {"vix_term": _res("vix_term", missing=True), "vix_level": _res("vix_level", 35, 2),
           "hy_oas_chg_20d": _res("hy_oas_chg_20d", missing=True),
           "spx_ret_20d": _res("spx_ret_20d", -5, -2), "us10y_chg_20d": _res("us10y_chg_20d", -20, -1)}
    r = classify(cls, res)
    assert r == {"regime": "growth_scare", "fired": "growth_scare",
                 "undetermined": ["liquidity_stress"], "degraded": True}
    res["vix_level"] = _res("vix_level", 20, 0)              # AND with a False is decided
    res["vix_term"] = _res("vix_term", 0.9, 0)
    assert classify(cls, res)["degraded"] is False


# ---------------------------------------------------------------------------
# As-of leakage
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("asof", ["2017-03-03", "2019-06-07", "2020-03-20"])
def test_no_leakage_after_asof(cfg, asof):
    glob, tree = cfg
    base = _synthetic(1)
    t = pd.Timestamp(asof)
    shocked = base.copy()
    after = shocked["avail"] > t
    shocked.loc[after, "value"] = shocked.loc[after, "value"] * 3 + 100   # rewrite the future
    shocked = shocked[~(after & (shocked.index % 3 == 0))]               # and drop some of it
    a = Engine(glob, Inputs(base)).run(tree, t)
    b = Engine(glob, Inputs(shocked)).run(tree, t)
    for d in (a, b):
        d.pop("inputs_fingerprint")
    assert a == b


def test_asof_day_observation_is_not_seen(inputs):
    s = inputs.asof("DGS2", pd.Timestamp("2019-06-07"))
    assert s.index[-1] == pd.Timestamp("2019-06-06")
    cot = inputs.asof("COT_JPY", pd.Timestamp("2019-06-07"))
    assert cot.index[-1] <= pd.Timestamp("2019-06-01")          # report Tuesday + 6 days


# ---------------------------------------------------------------------------
# Combination rules + veto
# ---------------------------------------------------------------------------

def test_combination_is_multiplicative_and_follows_rules(cfg):
    _, tree = cfg
    res = {"us2y_chg_20d": _res("us2y_chg_20d", -30, -1.5),          # signed z +1.5 -> JPY up
           "jpy_cot_pctile": _res("jpy_cot_pctile", 10, -1.5),       # <= 20 -> up: agree
           "oil_chg_20d": _res("oil_chg_20d", 1, 0.2)}               # below threshold: neutral
    d = combine(tree, "calm", res)
    assert d["direction"] == 1
    assert d["conviction"] == pytest.approx(1.5 * 1.3)
    assert d["conviction_tier"] == 2
    verdicts = {p["node"]: p["verdict"] for p in d["active_path"] if p["role"] == "modifier"}
    assert verdicts == {"jpy_cot_pctile": "agree", "oil_chg_20d": "neutral"}
    res["jpy_cot_pctile"] = _res("jpy_cot_pctile", 90, 1.5)          # >= 80 -> down: disagree
    res["oil_chg_20d"] = _res("oil_chg_20d", 9, 1.2)                 # z >= 1 -> down: disagree
    d = combine(tree, "calm", res)
    assert d["direction"] == 1 and d["conviction"] == pytest.approx(1.5 * 0.7 * 0.8)
    assert d["signed_conviction"] == pytest.approx(d["conviction"])
    assert "now +1.50" in d["invalidator"]


def test_below_z_min_is_flat(cfg):
    _, tree = cfg
    res = {"us2y_chg_20d": _res("us2y_chg_20d", 3, 0.3), "jpy_cot_pctile": _res("jpy_cot_pctile", 10, -1),
           "oil_chg_20d": _res("oil_chg_20d", 0, 0)}
    d = combine(tree, "calm", res)
    assert (d["direction"], d["conviction"], d["conviction_tier"], d["reason"]) == (0, 0.0, 0, "|z| < z_min")


def test_constant_primary_and_two_sided_rule(cfg):
    _, tree = cfg
    d = combine(tree, "liquidity_stress", {"jpy_cot_pctile": _res("jpy_cot_pctile", 15, -1),
                                           "vix_term": _res("vix_term", 1.08, 2)})
    assert d["direction"] == 1 and d["conviction"] == pytest.approx(3.0) and d["conviction_tier"] == 3
    assert d["invalidator"] == "Stress eases: vix_term < 0.9 (now 1.080)"
    d = combine(tree, "growth_scare", {"spx_ret_20d": _res("spx_ret_20d", -6, -2.0),
                                       "us2y_chg_20d": _res("us2y_chg_20d", 10, 0.4),   # two-sided: z>=0 -> down
                                       "jpy_cot_pctile": _res("jpy_cot_pctile", 50, 0)})
    assert d["direction"] == 1 and d["conviction"] == pytest.approx(2.0 * 0.8)


def test_veto_sets_direction_zero_with_reason(cfg):
    _, tree = cfg
    t = copy.deepcopy(tree)
    t["regime_switch"]["regimes"]["calm"]["vetoes"] = [
        {"node": "usdjpy_speed_flag", "op": ">=", "value": 1, "reason": "intervention risk"}]
    res = {"us2y_chg_20d": _res("us2y_chg_20d", -30, -1.5), "jpy_cot_pctile": _res("jpy_cot_pctile", 50, 0),
           "oil_chg_20d": _res("oil_chg_20d", 0, 0), "usdjpy_speed_flag": _res("usdjpy_speed_flag", 1.0)}
    d = combine(t, "calm", res)
    assert d["direction"] == 0 and d["conviction_tier"] == 0 and d["reason"] == "veto"
    assert d["vetoes"] == [{"node": "usdjpy_speed_flag", "reason": "intervention risk", "value": 1.0}]
    res["usdjpy_speed_flag"] = _res("usdjpy_speed_flag", 0.0)
    assert combine(t, "calm", res)["direction"] == 1
    res["usdjpy_speed_flag"] = _res("usdjpy_speed_flag", missing=True)
    d = combine(t, "calm", res)
    assert d["direction"] == 1 and d["skipped"][0]["role"] == "veto"


# ---------------------------------------------------------------------------
# Determinism + CLI
# ---------------------------------------------------------------------------

def test_same_asof_gives_identical_json(cfg, inputs):
    glob, tree = cfg
    a = dumps(Engine(glob, inputs).run(tree, "2019-06-07"))
    b = dumps(Engine(glob, Inputs(_synthetic())).run(tree, "2019-06-07"))
    assert a == b
    d = json.loads(a)
    assert d["status"] == "uncalibrated" and d["regime"] in tree["regime_switch"]["regimes"]


def test_cli_runs_offline(tmp_path):
    from src.tree import run
    p = tmp_path / "in.parquet"
    _synthetic().to_parquet(p, index=False)
    assert run.main(["--tree", "jpy", "--asof", "2019-06-07", "--inputs", str(p), "--out-dir", str(tmp_path)]) == 0
    one = (tmp_path / "jpy_2019-06-07.json").read_text()
    assert one == (tmp_path / "jpy_latest.json").read_text()
    assert run.main(["--tree", "jpy", "--walk-forward", "--start", "2019-01-04", "--end", "2019-03-01",
                     "--inputs", str(p), "--out-dir", str(tmp_path)]) == 0
    wf = pd.read_parquet(tmp_path / "jpy_wf_2019-01-04_2019-03-01.parquet")
    assert len(wf) == 9 and (pd.to_datetime(wf["asof"]).dt.dayofweek == 4).all()


# ---------------------------------------------------------------------------
# Measurement helpers (synthetic returns only)
# ---------------------------------------------------------------------------

def _measure_mod():
    spec = importlib.util.spec_from_file_location("tree_jpy_v0", ROOT / "scripts/measure/tree_jpy_v0.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_forward_returns_sign_and_staleness():
    m = _measure_mod()
    idx = pd.bdate_range("2020-01-01", "2020-03-31")
    price = pd.Series(100 * np.exp(-0.001 * np.arange(len(idx))), index=idx)   # USDJPY falls: JPY up
    asofs = pd.Series(pd.to_datetime(["2020-01-03", "2020-03-27"]))
    r = m.forward_returns(price, asofs, 1)
    assert r.iloc[0] == pytest.approx(5 * 0.001 * 1e4) and np.isnan(r.iloc[1])


def test_metrics_and_verdicts_on_a_perfect_signal():
    m = _measure_mod()
    rng = np.random.default_rng(3)
    ret = pd.Series(rng.normal(0, 50, 300))
    s = ret * 0.01 + pd.Series(rng.normal(0, 0.001, 300))
    d = np.sign(s).astype(int)
    grid = pd.Series(np.arange(300) % 2 == 0)
    x = m.metrics(s, d, ret, grid)
    assert x["N"] == 300 and x["N_ind"] == 150 and x["hit"] == 1.0 and x["IC"] > 0.99
    v = m.verdict_signal(x, 0.0, {1: 0.2, 2: x["IC"], 4: -0.1}, [1, 2, 4], 2)
    assert v["criteria"]["neighbours_same_sign"] is False and not v["pass"]
    v = m.verdict_signal(x, 0.0, {1: 0.2, 2: x["IC"], 4: 0.1}, [1, 2, 4], 2)
    assert v["pass"]
    assert m.neighbours([1, 2, 4, 8, 13], 13) == [8]
