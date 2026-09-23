"""Phase 2 pre-registered criterion C1 (audit 2026-09-23, revised).

On JBlanked events (data/archive/ff_calendar_range.json + every jb_raw payload
ever committed, newest payload wins per event) with actual != 0 and a next
print carrying `previous`: mismatch = |next.previous - actual| > tol(series),
tol = median + 3*1.4826*MAD of the series' |next.previous - actual| (the
approved previous-consistency formula, src/previous_consistency.py).
C1 passes iff mismatch_rate(Bad Data) <= mismatch_rate(Good Data) + 2pp.
Directional agreement (Good/Bad vs sign(actual - forecast) and x direction) is
reported descriptively only.

    .venv/bin/python scripts/measure/ff_provenance_c1.py [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.econ_calendar_ff import jblanked_to_utc, load_aliases  # noqa: E402
from src.ff_scoring import CCY2COUNTRY, build_matcher, load_configs  # noqa: E402
from src.previous_consistency import EPS, revision_tolerance  # noqa: E402


def _payloads() -> list[tuple[str, list]]:
    out = [("0000-archive", json.loads((ROOT / "data/archive/ff_calendar_range.json").read_text()))]
    log = subprocess.run(["git", "log", "--all", "--diff-filter=A", "--name-only",
                          "--format=COMMIT %H", "--", "data/jb_raw/"],
                         capture_output=True, text=True, cwd=ROOT).stdout
    seen = set()
    commit = None
    for line in log.splitlines():
        if line.startswith("COMMIT "):
            commit = line.split()[1]
        elif line.strip() and line not in seen:
            seen.add(line)
            raw = subprocess.run(["git", "show", f"{commit}:{line}"], capture_output=True,
                                 text=True, cwd=ROOT).stdout
            try:
                out.append((Path(line).stem, json.loads(raw)))
            except json.JSONDecodeError:
                pass
    return sorted(out, key=lambda x: x[0])      # jb_range_<ISO> sorts by time; archive first


def events() -> pd.DataFrame:
    recs = {}
    for tag, evs in _payloads():
        for e in evs:
            dt = jblanked_to_utc(e.get("Date", ""))
            if dt is None:
                continue
            k = (str(e.get("Currency", "")).strip(), str(e.get("Name", "")).strip(), pd.Timestamp(dt))
            recs[k] = {"currency": k[0], "name": k[1], "dt": k[2], "payload": tag,
                       "actual": e.get("Actual"), "forecast": e.get("Forecast"),
                       "previous": e.get("Previous"), "quality": e.get("Quality"),
                       "strength": e.get("Strength")}
    df = pd.DataFrame(recs.values())
    for c in ("actual", "forecast", "previous"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def measure(df: pd.DataFrame) -> dict:
    df = df.sort_values(["currency", "name", "dt"]).copy()
    df["_date"] = df["dt"].dt.date
    df = df.drop_duplicates(["currency", "name", "_date"], keep="last")
    df = df[df["quality"] != "Data Not Loaded"]
    parts = []
    for (_, _), g in df.groupby(["currency", "name"], sort=False):
        g = g.sort_values("dt").copy()
        g["next_previous"] = g["previous"].shift(-1)
        tol, n = revision_tolerance(g["actual"].to_numpy(float), g["next_previous"].to_numpy(float))
        g["tol"] = tol
        parts.append(g)
    d = pd.concat(parts)
    d = d[d["actual"].notna() & (d["actual"] != 0.0) & d["next_previous"].notna()]
    d["mismatch"] = (d["next_previous"] - d["actual"]).abs() > d["tol"] + EPS
    rates = {}
    for q in ("Good Data", "Bad Data"):
        s = d[d["quality"] == q]
        rates[q] = {"n": int(len(s)), "mismatch": int(s["mismatch"].sum()),
                    "rate": float(s["mismatch"].mean()) if len(s) else None}
    passed = (rates["Bad Data"]["rate"] is not None and rates["Good Data"]["rate"] is not None
              and rates["Bad Data"]["rate"] <= rates["Good Data"]["rate"] + 0.02)

    # descriptive: direction agreement
    ind, _ = load_configs()
    indicators = ind.get("indicators", {}) or {}
    al, m = load_aliases(), build_matcher()

    def direction(ccy, name):
        canon = (al.get(ccy, {}) or {}).get(name)
        key = m.match(CCY2COUNTRY.get(ccy, ""), canon) if canon else None
        if key is None:
            return None
        cfg = indicators.get(key, {}) or {}
        return int((cfg.get("direction_overrides") or {}).get(ccy, cfg.get("direction", 1)))

    dd = d[d["quality"].isin(["Good Data", "Bad Data"]) & d["forecast"].notna()].copy()
    dd["raw_sign"] = np.sign(dd["actual"] - dd["forecast"])
    dd["dir"] = [direction(c, n) for c, n in zip(dd["currency"], dd["name"])]
    desc = {}
    for q in ("Good Data", "Bad Data"):
        s = dd[dd["quality"] == q]
        want = 1 if q == "Good Data" else -1
        sm = s[s["dir"].notna()]
        desc[q] = {"n": int(len(s)),
                   "raw_sign_agrees": float((s["raw_sign"] == want).mean()) if len(s) else None,
                   "n_modeled": int(len(sm)),
                   "sign_x_direction_agrees": float((sm["raw_sign"] * sm["dir"] == want).mean()) if len(sm) else None,
                   "at_consensus": float((s["raw_sign"] == 0).mean()) if len(s) else None}
    return {"events_total": int(len(df)), "checked_rows": int(len(d)), "rates": rates,
            "C1_pass": bool(passed), "direction_descriptive": desc}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    res = measure(events())
    print(json.dumps(res, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
