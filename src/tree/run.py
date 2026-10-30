"""Run a tree offline from data/tree_inputs.parquet.

    python -m src.tree.run --tree jpy [--asof YYYY-MM-DD]
        -> data/tree_runs/jpy_<asof>.json and jpy_latest.json
    python -m src.tree.run --tree jpy --walk-forward --start 2000-01-07 [--end YYYY-MM-DD] [--step 7d]
        -> data/tree_runs/jpy_wf_<start>_<end>.parquet (one row per asof)

The default asof is the last day any input became available. The same asof and
the same inputs give byte-identical JSON."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from . import schema
from .data import INPUTS_FILE, Inputs
from .engine import Engine

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "data" / "tree_runs"


def dumps(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def default_asof(inputs: Inputs) -> pd.Timestamp:
    return max(pd.Timestamp(inputs._by[n][0][-1]) for n in inputs.names)


def flat_row(d: dict) -> dict:
    """One walk-forward row: the decision plus value / z / quality of every node."""
    row = {k: d[k] for k in ("asof", "direction", "conviction", "signed_conviction",
                             "conviction_tier", "regime", "reason", "invalidator")}
    row["degraded"] = d["regime_detail"]["degraded"]
    row["undetermined"] = ",".join(d["regime_detail"]["undetermined"])
    row["skipped"] = ",".join(s["node"] for s in d["skipped"])
    row["path"] = json.dumps(d["active_path"], sort_keys=True)
    for nid, r in d["nodes"].items():
        row[f"{nid}__value"] = r["value"]
        row[f"{nid}__z"] = r["z"]
        row[f"{nid}__q"] = r["quality"]
    return row


def walk_forward(engine: Engine, tree: dict, start, end, step_days: int = 7) -> pd.DataFrame:
    rows = [flat_row(engine.run(tree, t))
            for t in pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq=f"{step_days}D")]
    return pd.DataFrame(rows)


def run_layer(a) -> int:
    """A per-currency node layer (reaction): snapshot JSON per asof."""
    from .reaction import ReactionInputs, snapshot
    layer = schema.load_layer(a.tree)
    ri = ReactionInputs.load()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    if a.walk_forward:
        from .reaction import panel
        end = pd.Timestamp(a.end) if a.end else default_asof(ri)
        t0 = time.time()
        df = panel(layer, ri, pd.date_range(pd.Timestamp(a.start), end, freq=f"{int(a.step[:-1])}D"))
        last = df["asof"].max()
        out = a.out_dir / f"{a.tree}_wf_{pd.Timestamp(a.start):%Y-%m-%d}_{last:%Y-%m-%d}.parquet"
        df.to_parquet(out, index=False)
        print(f"{df['asof'].nunique()} asofs in {time.time() - t0:.1f}s -> {out}")
        return 0
    asof = pd.Timestamp(a.asof) if a.asof else default_asof(ri)
    d = snapshot(layer, ri, asof)
    text = dumps(d)
    (a.out_dir / f"{a.tree}_{d['asof']}.json").write_text(text)
    (a.out_dir / f"{a.tree}_latest.json").write_text(text)
    print(f"{a.tree} {d['asof']}: " + ", ".join(
        f"{c} {sum(not r['quality'] == 'missing' for r in v.values())}/{len(v)}"
        for c, v in d["currencies"].items()) + " nodes ok")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", required=True)
    ap.add_argument("--asof")
    ap.add_argument("--inputs", type=Path, default=INPUTS_FILE)
    ap.add_argument("--out-dir", type=Path, default=RUNS_DIR)
    ap.add_argument("--walk-forward", action="store_true")
    ap.add_argument("--start", default="2000-01-07")
    ap.add_argument("--end")
    ap.add_argument("--step", default="7d")
    a = ap.parse_args(argv)

    if schema.is_layer(a.tree):
        return run_layer(a)
    glob, tree = schema.load(a.tree)
    inputs = Inputs.load(a.inputs)
    engine = Engine(glob, inputs)
    a.out_dir.mkdir(parents=True, exist_ok=True)

    if a.walk_forward:
        if not a.step.endswith("d"):
            ap.error("--step must look like 7d")
        end = pd.Timestamp(a.end) if a.end else default_asof(inputs)
        t0 = time.time()
        df = walk_forward(engine, tree, a.start, end, int(a.step[:-1]))
        last = df["asof"].iloc[-1]
        out = a.out_dir / f"{a.tree}_wf_{pd.Timestamp(a.start):%Y-%m-%d}_{last}.parquet"
        df.to_parquet(out, index=False)
        print(f"{len(df)} asofs {df['asof'].iloc[0]} -> {last} in {time.time() - t0:.1f}s -> {out}")
        print(df["regime"].value_counts().to_string())
        return 0

    asof = pd.Timestamp(a.asof) if a.asof else default_asof(inputs)
    d = engine.run(tree, asof)
    text = dumps(d)
    (a.out_dir / f"{a.tree}_{d['asof']}.json").write_text(text)
    (a.out_dir / f"{a.tree}_latest.json").write_text(text)
    print(f"{a.tree} {d['asof']}: regime {d['regime']}, direction {d['direction']:+d}, "
          f"conviction {d['conviction']:.3f} (tier {d['conviction_tier']}), status {d['status']}")
    print(f"  invalidator: {d['invalidator']}")
    for s in d["skipped"]:
        print(f"  skipped {s['role']} {s['node']}: {s['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
