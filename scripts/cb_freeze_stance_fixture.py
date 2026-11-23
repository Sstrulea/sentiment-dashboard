"""Freeze the arguments of payload.stance_page_v6 (step 1 of the stance render) into tests/fixtures/cb_stance/.

    python scripts/cb_freeze_stance_fixture.py [--asof 2026-11-20] [--out tests/fixtures/cb_stance/v6_page_inputs_<asof>.json]

Runs cb_render.load_stance_args at --asof on the current data/ and writes, per bank, every argument of stance_page_v6: r (the
stance.compute result WITHOUT its "v6" key), d (stance_v6.display_inputs of the BankInputs), ui, rate, mandate, next_test, history,
neutral_na, notes, path, bank_row, vote, stale_after_bd; plus scale_pp. The tests rebuild r["v6"] with stance_v6.evaluate(r, d) and the
block with cb_render.build_stance, so the exact-value tests do not move when data dated before --asof arrives later (e.g. the SNB
SARON fixings of 16-20 Nov, added on 23 Nov) and any change of stance_v6.py shows in the texts. Deterministic: same data, same bytes.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import cb_render as R                       # noqa: E402
from src.cb_compute import payload as P              # noqa: E402
from src.cb_compute.stance_v6 import display_inputs  # noqa: E402
from src.cb_loader import load_context, load_pair_defs  # noqa: E402


def freeze(asof: date, data_dir=None) -> dict:
    ctx = load_context(data_dir)
    out = P.build(ctx, asof, load_pair_defs())
    args = R.load_stance_args(data_dir, ctx, asof, out, with_inputs=True)
    inputs = args.pop("inputs")
    banks = {}
    for c, a in args["banks"].items():
        a = dict(a)
        r = {k: v for k, v in a.pop("r").items() if k != "v6"}
        banks[c] = {"r": r, "d": display_inputs(inputs[c]), **a}
    return {"asof": asof.isoformat(),
            "source": f"scripts/cb_freeze_stance_fixture.py --asof {asof.isoformat()} (cb_render.load_stance_args on data/)",
            "scale_pp": args["scale_pp"], "stale_after_bd": args["stale_after_bd"], "banks": banks}


def load(path: Path) -> dict:
    """The frozen file -> stance_page_args (r["v6"] recomputed by stance_v6.evaluate), ready for cb_render.build_stance."""
    from src.cb_compute.stance_v6 import evaluate
    fx = json.loads(Path(path).read_text(encoding="utf-8"))
    banks = {}
    for c, b in fx["banks"].items():
        b = dict(b)
        r, d = b.pop("r"), b.pop("d")
        banks[c] = {"r": {**r, "v6": evaluate(r, d)}, **b}
    return {"banks": banks, "scale_pp": fx["scale_pp"], "stale_after_bd": fx["stale_after_bd"]}


def dumps(doc: dict) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False, default=str) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--asof", type=date.fromisoformat, default=date(2026, 11, 20))
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    out = a.out or ROOT / "tests" / "fixtures" / "cb_stance" / f"v6_page_inputs_{a.asof.isoformat()}.json"
    text = dumps(freeze(a.asof))
    out.write_text(text, encoding="utf-8")
    print(f"{out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}: {len(text.encode())} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
