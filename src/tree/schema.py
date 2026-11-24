"""Load and validate the tree YAML files (data/trees/_global.yml, data/trees/<tree>.yml).
Every problem raises TreeSchemaError naming the file and the key."""
from __future__ import annotations

from pathlib import Path

import yaml

from . import reaction  # noqa: F401  (registers the reaction-layer node functions)
from .nodes import REGISTRY, STATUSES

ROOT = Path(__file__).resolve().parents[2]
TREES_DIR = ROOT / "data" / "trees"

NODE_KEYS = {"id", "fn", "inputs", "params", "status", "stale_after_days", "note"}
NODE_REQUIRED = {"id", "fn", "inputs", "status", "stale_after_days"}
TREE_KEYS = {"tree_id", "asset", "horizon_weeks", "nodes", "regime_switch", "decision", "note"}
GLOBAL_KEYS = {"nodes", "classifiers", "note"}
REGIME_KEYS = {"primary", "modifiers", "vetoes", "invalidator", "note"}
MOD_KEYS = {"node", "rule", "agree_mult", "disagree_mult", "note"}
VETO_KEYS = {"node", "field", "op", "value", "reason", "note"}
DECISION_KEYS = {"z_min", "conviction_tiers", "note"}
OPS = {">", ">=", "<", "<=", "==", "!="}
RULE_TYPES = {"z", "pctile"}


class TreeSchemaError(ValueError):
    pass


def _err(where: str, msg: str):
    raise TreeSchemaError(f"{where}: {msg}")


def _keys(where: str, d, allowed: set, required: set = frozenset()):
    if not isinstance(d, dict):
        _err(where, f"expected a mapping, got {type(d).__name__}")
    extra = set(d) - allowed
    if extra:
        _err(where, f"unknown keys {sorted(extra)}")
    lack = set(required) - set(d)
    if lack:
        _err(where, f"missing keys {sorted(lack)}")


def _num(where: str, v, positive: bool = False):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        _err(where, f"expected a number, got {v!r}")
    if positive and v <= 0:
        _err(where, f"must be > 0, got {v!r}")


def validate_nodes(where: str, nodes) -> list[dict]:
    if not isinstance(nodes, list) or not nodes:
        _err(where, "nodes must be a non-empty list")
    seen = set()
    for i, n in enumerate(nodes):
        w = f"{where}.nodes[{i}]"
        _keys(w, n, NODE_KEYS, NODE_REQUIRED)
        nid = n["id"]
        if not isinstance(nid, str) or not nid:
            _err(w, "id must be a non-empty string")
        if nid in seen:
            _err(w, f"duplicate node id {nid!r}")
        seen.add(nid)
        if n["fn"] not in REGISTRY:
            _err(w, f"fn {n['fn']!r} is not registered (known: {sorted(REGISTRY)})")
        if not isinstance(n["inputs"], list) or not all(isinstance(x, str) for x in n["inputs"]):
            _err(w, "inputs must be a list of strings")
        if n["status"] not in STATUSES:
            _err(w, f"status {n['status']!r} not in {STATUSES}")
        if not isinstance(n["stale_after_days"], int) or n["stale_after_days"] <= 0:
            _err(w, "stale_after_days must be a positive integer")
        if "params" in n and not isinstance(n["params"], dict):
            _err(w, "params must be a mapping")
    return nodes


def _cond(where: str, c, node_ids: set):
    if isinstance(c, dict) and set(c) in ({"any"}, {"all"}):
        items = c.get("any", c.get("all"))
        if not isinstance(items, list) or not items:
            _err(where, "any/all must hold a non-empty list")
        for j, x in enumerate(items):
            _cond(f"{where}.{next(iter(c))}[{j}]", x, node_ids)
        return
    _keys(where, c, {"node", "field", "op", "value"}, {"node", "op", "value"})
    if c["node"] not in node_ids:
        _err(where, f"unknown node {c['node']!r}")
    if c["op"] not in OPS:
        _err(where, f"op {c['op']!r} not in {sorted(OPS)}")
    if c.get("field", "value") not in ("value", "z"):
        _err(where, "field must be value or z")
    _num(where, c["value"])


def validate_global(cfg: dict, where: str = "_global.yml") -> dict:
    _keys(where, cfg, GLOBAL_KEYS, {"nodes", "classifiers"})
    validate_nodes(where, cfg["nodes"])
    ids = {n["id"] for n in cfg["nodes"]}
    if not isinstance(cfg["classifiers"], dict) or not cfg["classifiers"]:
        _err(where, "classifiers must be a non-empty mapping")
    for cid, c in cfg["classifiers"].items():
        w = f"{where}.classifiers.{cid}"
        _keys(w, c, {"precedence", "default", "rules", "note"}, {"precedence", "default", "rules"})
        if not isinstance(c["precedence"], list) or set(c["precedence"]) != set(c["rules"]):
            _err(w, "precedence must list exactly the rule names")
        if c["default"] in c["rules"]:
            _err(w, "default regime must not also be a rule")
        for r, cond in c["rules"].items():
            _cond(f"{w}.rules.{r}", cond, ids)
    return cfg


def _rule(where: str, r):
    if not isinstance(r, dict) or r.get("type") not in RULE_TYPES:
        _err(where, f"rule.type must be one of {sorted(RULE_TYPES)}")
    if r["type"] == "z":
        _keys(where, r, {"type", "threshold", "sign", "two_sided"}, {"type", "threshold", "sign"})
        _num(where, r["threshold"])
    else:
        _keys(where, r, {"type", "low", "high", "sign"}, {"type", "low", "high", "sign"})
        _num(where, r["low"]), _num(where, r["high"])
        if not r["low"] < r["high"]:
            _err(where, "pctile low must be < high")
    if r["sign"] not in (1, -1):
        _err(where, "rule.sign must be 1 or -1")


def validate_tree(cfg: dict, glob: dict, where: str = "tree") -> dict:
    _keys(where, cfg, TREE_KEYS, TREE_KEYS - {"note"})
    if not isinstance(cfg["horizon_weeks"], int) or cfg["horizon_weeks"] <= 0:
        _err(where, "horizon_weeks must be a positive integer")
    validate_nodes(where, cfg["nodes"])
    gids = {n["id"] for n in glob["nodes"]}
    tids = {n["id"] for n in cfg["nodes"]}
    clash = gids & tids
    if clash:
        _err(where, f"node ids already defined in _global.yml: {sorted(clash)}")
    ids = gids | tids

    rs = cfg["regime_switch"]
    _keys(f"{where}.regime_switch", rs, {"classifier", "regimes", "note"}, {"classifier", "regimes"})
    cls = glob["classifiers"].get(rs["classifier"])
    if cls is None:
        _err(f"{where}.regime_switch", f"unknown classifier {rs['classifier']!r}")
    want = set(cls["precedence"]) | {cls["default"]}
    if set(rs["regimes"]) != want:
        _err(f"{where}.regime_switch.regimes", f"must configure exactly {sorted(want)}, got {sorted(rs['regimes'])}")
    for reg, rc in rs["regimes"].items():
        w = f"{where}.regime_switch.regimes.{reg}"
        _keys(w, rc, REGIME_KEYS, {"primary", "invalidator"})
        p = rc["primary"]
        if not isinstance(p, dict) or not (set(p) <= {"node", "sign", "const_z", "note"}):
            _err(f"{w}.primary", "expected {node, sign} or {const_z}")
        if "const_z" in p:
            if "node" in p:
                _err(f"{w}.primary", "const_z and node are exclusive")
            _num(f"{w}.primary", p["const_z"])
        else:
            if p.get("node") not in ids:
                _err(f"{w}.primary", f"unknown node {p.get('node')!r}")
            if p.get("sign") not in (1, -1):
                _err(f"{w}.primary", "sign must be 1 or -1")
        for j, m in enumerate(rc.get("modifiers") or []):
            mw = f"{w}.modifiers[{j}]"
            _keys(mw, m, MOD_KEYS, MOD_KEYS - {"note"})
            if m["node"] not in ids:
                _err(mw, f"unknown node {m['node']!r}")
            _rule(f"{mw}.rule", m["rule"])
            _num(mw, m["agree_mult"], positive=True), _num(mw, m["disagree_mult"], positive=True)
        for j, v in enumerate(rc.get("vetoes") or []):
            vw = f"{w}.vetoes[{j}]"
            _keys(vw, v, VETO_KEYS, {"node", "op", "value", "reason"})
            _cond(vw, {k: v[k] for k in ("node", "field", "op", "value") if k in v}, ids)
        if not isinstance(rc["invalidator"], str) or not rc["invalidator"]:
            _err(w, "invalidator must be a non-empty template string")

    d = cfg["decision"]
    _keys(f"{where}.decision", d, DECISION_KEYS, {"z_min", "conviction_tiers"})
    _num(f"{where}.decision.z_min", d["z_min"])
    t = d["conviction_tiers"]
    if not (isinstance(t, list) and len(t) == 3 and all(isinstance(x, (int, float)) for x in t)
            and t == sorted(t) and t[0] > 0):
        _err(f"{where}.decision.conviction_tiers", "must be 3 increasing positive numbers (tiers 1-3)")
    return cfg


def load_yaml(path: Path) -> dict:
    try:
        cfg = yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as e:
        raise TreeSchemaError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(cfg, dict):
        raise TreeSchemaError(f"{path}: top level must be a mapping")
    return cfg


LAYER_KEYS = {"layer_id", "currencies", "nodes", "missing_by_ccy", "note"}


def validate_layer(cfg: dict, where: str = "layer") -> dict:
    """A per-currency node layer (no regime switch, no decision): data/trees/reaction.yml."""
    _keys(where, cfg, LAYER_KEYS, {"layer_id", "currencies", "nodes"})
    cur = cfg["currencies"]
    if not isinstance(cur, list) or not cur or len(set(cur)) != len(cur):
        _err(f"{where}.currencies", "must be a non-empty list without duplicates")
    validate_nodes(where, cfg["nodes"])
    ids = {n["id"] for n in cfg["nodes"]}
    for nid, per in (cfg.get("missing_by_ccy") or {}).items():
        if nid not in ids:
            _err(f"{where}.missing_by_ccy", f"unknown node {nid!r}")
        if not isinstance(per, dict) or not set(per) <= set(cur):
            _err(f"{where}.missing_by_ccy.{nid}", "must map listed currencies to a reason")
    return cfg


def is_layer(name: str, trees_dir: Path = TREES_DIR) -> bool:
    path = trees_dir / f"{name}.yml"
    return path.exists() and "layer_id" in load_yaml(path)


def load_layer(name: str, trees_dir: Path = TREES_DIR) -> dict:
    path = trees_dir / f"{name}.yml"
    if not path.exists():
        raise TreeSchemaError(f"{path}: no such layer")
    return validate_layer(load_yaml(path), path.name)


def load(tree: str, trees_dir: Path = TREES_DIR) -> tuple[dict, dict]:
    """-> (global config, tree config), both validated."""
    glob = validate_global(load_yaml(trees_dir / "_global.yml"), "_global.yml")
    path = trees_dir / f"{tree}.yml"
    if not path.exists():
        raise TreeSchemaError(f"{path}: no such tree")
    return glob, validate_tree(load_yaml(path), glob, path.name)
