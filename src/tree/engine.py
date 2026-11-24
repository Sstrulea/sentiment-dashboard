"""Evaluate a tree at one asof: global nodes (once per asof, shared), tree nodes,
regime classifier, hierarchical combination, decision.

Combination (not a sum):
  direction  = sign(primary z) if |z| >= z_min else 0
  conviction = |primary z| x product of modifier multipliers
  modifier   : its rule implies +1 / -1 / 0; agree_mult when the implied
               direction equals the decision direction, disagree_mult when
               opposite, 1 when it implies nothing; skipped (with the reason)
               when its node is missing
  veto       : a condition that fires sets the direction to 0, with its reason
"""
from __future__ import annotations

import hashlib
import json
import operator
import string

import pandas as pd

from .data import Inputs
from .nodes import REGISTRY, NodeContext, NodeResult

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
       "==": operator.eq, "!=": operator.ne}


class CycleError(ValueError):
    pass


def topo_order(nodes: list[dict], known: set[str] = frozenset()) -> list[dict]:
    """Kahn's sort; ties keep the YAML order. Inputs that are not node ids are raw
    series; ids in `known` (already computed, e.g. global nodes) are satisfied."""
    by_id = {n["id"]: n for n in nodes}
    deps = {n["id"]: [i for i in n["inputs"] if i in by_id] for n in nodes}
    order, done = [], set(known)
    pending = [n["id"] for n in nodes]
    while pending:
        ready = [i for i in pending if all(d in done for d in deps[i])]
        if not ready:
            raise CycleError(f"node cycle among {sorted(pending)}")
        for i in ready:
            order.append(by_id[i])
            done.add(i)
        pending = [i for i in pending if i not in done]
    return order


def eval_nodes(nodes: list[dict], inputs: Inputs, asof: pd.Timestamp,
               known: dict[str, NodeResult] | None = None) -> dict[str, NodeResult]:
    results = dict(known or {})
    for spec in topo_order(nodes, set(results)):
        ctx = NodeContext(spec, inputs, asof, results)
        lost = [i for i in spec["inputs"] if i in results and results[i].missing]
        if lost:
            results[spec["id"]] = ctx.missing("input node missing: " + ", ".join(lost))
            continue
        r = REGISTRY[spec["fn"]](ctx)
        if not r.missing and r.source_last_date is not None:
            age = (pd.Timestamp(asof) - pd.Timestamp(r.source_last_date)).days
            if age > spec["stale_after_days"]:
                r = ctx.missing(f"stale: last observation {r.source_last_date} "
                                f"({age}d > {spec['stale_after_days']}d)", r.source_last_date)
        results[spec["id"]] = r
    return results


def eval_cond(c: dict, results: dict[str, NodeResult]):
    """Three-valued: True / False / None (undetermined because a node is missing)."""
    if "any" in c or "all" in c:
        vals = [eval_cond(x, results) for x in c.get("any", c.get("all"))]
        if "any" in c:
            return True if True in vals else (None if None in vals else False)
        return False if False in vals else (None if None in vals else True)
    r = results[c["node"]]
    v = None if r.missing else getattr(r, c.get("field", "value"))
    return None if v is None else bool(OPS[c["op"]](v, c["value"]))


def classify(cls: dict, results: dict[str, NodeResult]) -> dict:
    """First rule in precedence that fires. An undetermined rule does not fire;
    it is listed and the regime is marked degraded."""
    undetermined = []
    for name in cls["precedence"]:
        v = eval_cond(cls["rules"][name], results)
        if v is True:
            return {"regime": name, "fired": name, "undetermined": undetermined,
                    "degraded": bool(undetermined)}
        if v is None:
            undetermined.append(name)
    return {"regime": cls["default"], "fired": None, "undetermined": undetermined,
            "degraded": bool(undetermined)}


def implied_direction(rule: dict, r: NodeResult) -> int:
    s = rule["sign"]
    if rule["type"] == "pctile":
        if r.value <= rule["low"]:
            return s
        if r.value >= rule["high"]:
            return -s
        return 0
    t = rule["threshold"]
    if r.z >= t:
        return s
    if rule.get("two_sided") and r.z <= -t:
        return -s
    return 0


def _sgn(x: float) -> int:
    return (x > 0) - (x < 0)


class _Safe(dict):
    def __missing__(self, k):
        return "n/a"


def _fmt(template: str, values: dict) -> str:
    out = []
    for lit, field, spec, conv in string.Formatter().parse(template):
        out.append(lit)
        if field is None:
            continue
        v = values.get(field)
        if v is None:
            out.append("n/a")
        else:
            try:
                out.append(format(v, spec or ""))
            except (ValueError, TypeError):
                out.append(str(v))
    return "".join(out)


def combine(tree: dict, regime: str, results: dict[str, NodeResult]) -> dict:
    rc = tree["regime_switch"]["regimes"][regime]
    dec = tree["decision"]
    z_min = float(dec["z_min"])
    path, skipped, vetoed = [], [], []

    p = rc["primary"]
    if "const_z" in p:
        pz, pid, pval = float(p["const_z"]), "const", None
        path.append({"role": "primary", "node": "const", "z": pz})
    else:
        r = results[p["node"]]
        pid = p["node"]
        if r.missing or r.z is None:
            skipped.append({"node": pid, "role": "primary",
                            "reason": r.meta.get("reason") or r.meta.get("z_note", "no z")})
            return _decision(tree, regime, 0, 0.0, path, skipped, vetoed, pid, None, None,
                             results, rc, reason="primary missing")
        pz, pval = p["sign"] * r.z, r.value
        path.append({"role": "primary", "node": pid, "sign": p["sign"], "value": r.value,
                     "z": r.z, "signed_z": round(pz, 6)})

    direction = _sgn(pz) if abs(pz) >= z_min else 0
    conviction = abs(pz)
    for m in rc.get("modifiers") or []:
        r = results[m["node"]]
        if r.missing or (m["rule"]["type"] == "z" and r.z is None):
            skipped.append({"node": m["node"], "role": "modifier",
                            "reason": r.meta.get("reason") or r.meta.get("z_note", "no z")})
            continue
        imp = implied_direction(m["rule"], r)
        if imp == 0 or direction == 0:
            verdict, mult = "neutral", 1.0
        elif imp == direction:
            verdict, mult = "agree", float(m["agree_mult"])
        else:
            verdict, mult = "disagree", float(m["disagree_mult"])
        conviction *= mult
        path.append({"role": "modifier", "node": m["node"], "value": r.value, "z": r.z,
                     "implied": imp, "verdict": verdict, "mult": mult})
    for v in rc.get("vetoes") or []:
        fired = eval_cond({k: v[k] for k in ("node", "field", "op", "value") if k in v}, results)
        if fired is None:
            skipped.append({"node": v["node"], "role": "veto",
                            "reason": results[v["node"]].meta.get("reason", "missing")})
        elif fired:
            vetoed.append({"node": v["node"], "reason": v["reason"],
                           "value": results[v["node"]].value})
            path.append({"role": "veto", "node": v["node"], "value": results[v["node"]].value,
                         "reason": v["reason"]})
    if vetoed:
        direction = 0
    return _decision(tree, regime, direction, conviction, path, skipped, vetoed, pid, pz, pval,
                     results, rc)


def _decision(tree, regime, direction, conviction, path, skipped, vetoed, pid, pz, pval,
              results, rc, reason=None) -> dict:
    tiers = tree["decision"]["conviction_tiers"]
    conviction = conviction if direction else 0.0
    tier = sum(conviction >= t for t in tiers) if direction else 0
    values = {k: r.value for k, r in results.items()}
    values.update(primary=pid, primary_z=pz, primary_value=pval,
                  z_min=tree["decision"]["z_min"], direction=direction)
    return {"direction": direction, "conviction": round(conviction, 6),
            "signed_conviction": round(direction * conviction, 6), "conviction_tier": int(tier),
            "regime": regime, "active_path": path, "skipped": skipped, "vetoes": vetoed,
            "invalidator": _fmt(rc["invalidator"], values), "reason": reason
            or ("veto" if vetoed else ("|z| < z_min" if direction == 0 else None))}


def config_hash(glob: dict, tree: dict) -> str:
    blob = json.dumps({"global": glob, "tree": tree}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class Engine:
    def __init__(self, glob: dict, inputs: Inputs):
        self.glob = glob
        self.inputs = inputs
        self._global_cache: dict[pd.Timestamp, dict[str, NodeResult]] = {}

    def global_results(self, asof: pd.Timestamp) -> dict[str, NodeResult]:
        asof = pd.Timestamp(asof)
        if asof not in self._global_cache:
            self._global_cache = {asof: eval_nodes(self.glob["nodes"], self.inputs, asof)}
        return self._global_cache[asof]

    def run(self, tree: dict, asof) -> dict:
        asof = pd.Timestamp(asof).normalize()
        glob_res = self.global_results(asof)
        results = eval_nodes(tree["nodes"], self.inputs, asof, glob_res)
        cls = self.glob["classifiers"][tree["regime_switch"]["classifier"]]
        reg = classify(cls, results)
        dec = combine(tree, reg["regime"], results)
        return {
            "tree_id": tree["tree_id"], "asset": tree["asset"], "asof": asof.strftime("%Y-%m-%d"),
            "horizon_weeks": tree["horizon_weeks"], "status": "uncalibrated",
            "classifier": tree["regime_switch"]["classifier"], "regime_detail": reg,
            **dec,
            "nodes": {k: results[k].to_dict() for k in sorted(results)},
            "config_hash": config_hash(self.glob, tree),
            "inputs_fingerprint": self.inputs.fingerprint,
        }
