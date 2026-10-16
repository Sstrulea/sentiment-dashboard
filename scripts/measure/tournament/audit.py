"""Step 1 — data audit of the scoring tournament (first date, last date, gaps of
every input), plus the price checks (mapping, FRED correlation) and the last
Friday with a 4-week return on every price series. No score and no return metric
is computed here.

    python scripts/measure/tournament/audit.py [--md docs/eval/scoring-tournament-data-audit.md]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CACHE, CCYS, CROSS, ROOT  # noqa: E402
import prices as P  # noqa: E402

from src import economic_render as er  # noqa: E402
from src.ff_scoring import scoring_view  # noqa: E402
from src.policy_rate import load_decisions, policy_rate_history  # noqa: E402

DAILY_GAP_DAYS = 7      # a daily series "gap" = more than 7 calendar days without an observation
WEEKLY_GAP_DAYS = 8     # a weekly series gap = more than 8 days between reports
CADENCE_DAYS = {"weekly": 7, "monthly": 31, "quarterly": 92}


def _gaps(dates, max_days: int) -> list[tuple[str, str, int]]:
    d = pd.Series(sorted(pd.to_datetime(pd.Series(dates)).dt.normalize().unique()))
    if len(d) < 2:
        return []
    diff = d.diff().dt.days
    return [(str(d.iloc[i - 1].date()), str(d.iloc[i].date()), int(diff.iloc[i]))
            for i in range(1, len(d)) if diff.iloc[i] > max_days]


def _span(dates) -> dict:
    d = pd.to_datetime(pd.Series(dates))
    return {"first": str(d.min()), "last": str(d.max()), "n": int(len(d))}


def ff_audit(now: pd.Timestamp) -> dict:
    """Per (currency, indicator_key) of the scoring frame as production builds it
    (_load_calendar_frame + _with_policy_rate + scoring_view): first/last release,
    rows with an actual, gaps > 2× the configured cadence."""
    ind = yaml.safe_load(er.INDICATORS_YAML.read_text())
    defaults, cfgs = ind["defaults"], ind["indicators"]
    cal = er._load_calendar_frame(now)
    cal = er._with_policy_rate(cal, er._policy_rate_decisions(), now)
    raw_rows = len(cal)
    cal = scoring_view(cal)
    out = {"rows_scoring_frame": int(len(cal)), "rows_before_scoring_view": int(raw_rows),
           "unmapped_rows": int(cal["indicator_key"].isna().sum()), "per": []}
    cal = cal[cal["indicator_key"].notna() & cal["actual"].notna()]
    for (c, k), g in cal.groupby(["currency", "indicator_key"]):
        cfg = cfgs.get(k, {})
        freq = (cfg.get("frequency_overrides") or {}).get(c) or cfg.get("frequency") or defaults["default_frequency"]
        cad = CADENCE_DAYS.get(freq, 31)
        gaps = _gaps(g["release_dt"], 2 * cad)
        out["per"].append({"currency": c, "indicator": k, "category": cfg.get("category", "?"), "frequency": freq,
                           "first": str(g["release_dt"].min().date()), "last": str(g["release_dt"].max().date()),
                           "n": int(len(g)), "n_consensus": int(g["consensus"].notna().sum()),
                           "gaps": gaps, "in_config": k in cfgs})
    return out


def rates_audit() -> dict:
    r = pd.read_parquet(er.RATES_PARQUET)
    out = {}
    for (c, tenor), g in r.groupby(["currency", "tenor"]):
        g = g[g["yield_pct"].notna()]
        out[f"{c} {tenor}"] = {**_span(g["date"]), "sources": sorted(g["source"].unique()),
                               "gaps_since_2014": _gaps(g.loc[g["date"] >= "2014-01-01", "date"], DAILY_GAP_DAYS)}
    for c in CCYS:
        if not any(k.startswith(c + " ") for k in out):
            out[f"{c} 2y"] = {"first": None, "last": None, "n": 0, "note": "no 2Y series in data/rates.parquet"}
    ry = pd.read_parquet(er.REAL_YIELDS_PARQUET)
    real = {}
    for s, g in ry.groupby("series"):
        g = g[g["yield_pct"].notna()]
        real[s] = {**_span(g["date"]), "sources": sorted(g["source"].unique()),
                   "gaps": _gaps(g["date"], DAILY_GAP_DAYS)}
    return {"rates": out, "real_yields": real}


def policy_audit() -> dict:
    ff = pd.read_parquet(ROOT / "data" / "economic_calendar_ff.parquet")
    h = policy_rate_history(ff, load_decisions())
    out = {}
    for c in CCYS:
        g = h[h["currency"] == c]
        if g.empty:
            out[c] = {"first": None, "last": None, "n": 0}
            continue
        out[c] = {"first": str(g["release_dt"].min()), "last": str(g["release_dt"].max()), "n": int(len(g)),
                  "sources": {s: int(n) for s, n in g["source"].value_counts().items()},
                  "gaps_over_100d": _gaps(g["release_dt"], 100), "last_rate": float(g["rate"].iloc[-1])}
    return out


def cot_audit() -> dict:
    h = pd.read_parquet(ROOT / "data" / "history.parquet")
    meta = yaml.safe_load((ROOT / "data" / "contracts.yaml").read_text())
    sym = {i["cftc_code"]: (cat, i["symbol"]) for cat, c in meta["categories"].items() for i in c["instruments"]}
    want = {"fx", "metals", "indices"}
    out = {}
    for code, g in h.groupby("cftc_contract_market_code"):
        cat, s = sym.get(code, ("?", "?"))
        if cat not in want:
            continue
        out[s] = {"code": code, "category": cat, **_span(g["report_date_as_yyyy_mm_dd"]),
                  "gaps": _gaps(g["report_date_as_yyyy_mm_dd"], WEEKLY_GAP_DAYS)}
    import cot_extra
    nk = cot_extra.fetch()
    out["NIKKEI*"] = {"code": cot_extra.NIKKEI_CODE, "category": "indices (evaluation only)",
                      **_span(nk["report_date_as_yyyy_mm_dd"]), "gaps": _gaps(nk["report_date_as_yyyy_mm_dd"], WEEKLY_GAP_DAYS)}
    return out


def pc_audit() -> dict:
    pc = pd.read_parquet(er.PC_HISTORY_PARQUET)
    out = {}
    for col in ("equity", "total"):
        g = pc[pc[col].notna()]
        out[col] = {**_span(g["date"]), "gaps": _gaps(g["date"], DAILY_GAP_DAYS)}
    return out


def retail_audit() -> dict:
    r = pd.read_parquet(ROOT / "data" / "retail_history.parquet")
    r["day"] = pd.to_datetime(r["date"]).dt.tz_localize(None).dt.normalize()
    out = {"providers": sorted(r["provider"].unique()), "per": {}}
    for s, g in r.groupby("symbol"):
        out["per"][s] = {"first": str(g["day"].min().date()), "last": str(g["day"].max().date()),
                         "days": int(g["day"].nunique()), "gaps": _gaps(g["day"], DAILY_GAP_DAYS)}
    out["first"], out["last"] = str(r["day"].min().date()), str(r["day"].max().date())
    return out


def price_audit() -> dict:
    px = P.all_prices()
    out = {}
    src = {**{c: f"FRED {P.fs.FRED_FX[c][0]}" for c in P.fs.FRED_FX},
           **{s: f"Yahoo {P.YAHOO[s]}" for s in P.YAHOO}, **{s: f"Stooq {P.STOOQ[s].name}" for s in P.STOOQ}}
    for u, s in px.items():
        out[u] = {"source": src[u], "first": str(s.index.min().date()), "last": str(s.index.max().date()),
                  "n": int(len(s)), "gaps_since_2014": _gaps(s.index[s.index >= "2014-01-01"], DAILY_GAP_DAYS),
                  "last_friday_4w": str(P.last_friday_with_return(s, 4))}
    end = min(pd.Timestamp(v["last_friday_4w"]) for v in out.values())
    return {"series": out, "common_end_4w": str(end),
            "binding": sorted(u for u, v in out.items() if pd.Timestamp(v["last_friday_4w"]) == end)}


def run(now: pd.Timestamp) -> dict:
    return {"as_of": str(now), "ff": ff_audit(now), **rates_audit(), "policy": policy_audit(),
            "cot": cot_audit(), "pc": pc_audit(), "retail": retail_audit(), "prices": price_audit(),
            "price_mapping": P.mapping_check(), "fred_correlation": P.fred_correlation()}


# ---------------------------------------------------------------------------
# Markdown (Romanian)
# ---------------------------------------------------------------------------

def _g(gaps, k=4) -> str:
    if not gaps:
        return "—"
    s = "; ".join(f"{a}→{b} ({d}z)" for a, b, d in gaps[:k])
    return s + (f"; +{len(gaps) - k}" if len(gaps) > k else "")


def to_md(a: dict) -> str:
    L = ["# Turneu de scoring — auditul datelor (Pasul 1)", "",
         f"Instantaneu: {a['as_of']} UTC, branch `eval/scoring-tournament`. Generat de "
         "`scripts/measure/tournament/audit.py`. Fără scoruri și fără metrici pe randamente.", "",
         "Gol = interval fără observații mai lung decât: 7 zile calendaristice pentru seriile zilnice, "
         "8 zile pentru COT, 2× cadența din config pentru calendarul FF și 100 de zile pentru deciziile de politică.", ""]
    ff = a["ff"]
    L += ["## 1. Calendarul ForexFactory (cadrul de scoring)", "",
          f"Cadrul de scoring are {ff['rows_scoring_frame']} rânduri. Este construit ca în producție: "
          "`_load_calendar_frame`, `_with_policy_rate`, `scoring_view`. Are "
          f"{ff['unmapped_rows']} rânduri fără indicator_key. Tabelul numără doar publicările cu actual.", "",
          "| Valută | Indicator | Categ. | Frecv. | Prima | Ultima | N | N cu consens | Goluri |",
          "|---|---|---|---|---|---|---:|---:|---|"]
    for r in sorted(ff["per"], key=lambda r: (r["currency"], r["category"], r["indicator"])):
        L.append(f"| {r['currency']} | {r['indicator']}{'' if r['in_config'] else ' ⚠'} | {r['category']} | "
                 f"{r['frequency']} | {r['first']} | {r['last']} | {r['n']} | {r['n_consensus']} | {_g(r['gaps'], 3)} |")
    L += ["", "## 2. Randamente 2Y și reale", "", "| Serie | Prima | Ultima | N | Sursă | Goluri (din 2014) |",
          "|---|---|---|---:|---|---|"]
    for k, v in sorted(a["rates"].items()):
        if not v.get("n"):
            L.append(f"| {k} | — | — | 0 | — | {v.get('note', '')} |")
            continue
        L.append(f"| {k} | {v['first'][:10]} | {v['last'][:10]} | {v['n']} | {', '.join(v['sources'])} | "
                 f"{_g(v['gaps_since_2014'])} |")
    for k, v in a["real_yields"].items():
        L.append(f"| {k} | {v['first'][:10]} | {v['last'][:10]} | {v['n']} | {', '.join(v['sources'])} | {_g(v['gaps'])} |")
    L += ["", "## 3. Ratele de politică (`src.policy_rate.policy_rate_history`)", "",
          "| Valută | Prima decizie | Ultima | N | Surse | Goluri > 100 z | Rata curentă |",
          "|---|---|---|---:|---|---|---:|"]
    for c, v in a["policy"].items():
        if not v["n"]:
            L.append(f"| {c} | — | — | 0 | — | — | — |")
            continue
        L.append(f"| {c} | {v['first'][:10]} | {v['last'][:10]} | {v['n']} | "
                 f"{', '.join(f'{s} {n}' for s, n in v['sources'].items())} | {_g(v['gaps_over_100d'])} | {v['last_rate']:.3f} |")
    L += ["", "## 4. COT (`data/history.parquet`)", "", "| Simbol | Cod CFTC | Prima | Ultima | N | Goluri |",
          "|---|---|---|---|---:|---|"]
    for s, v in sorted(a["cot"].items()):
        if v.get("code") is None:
            L.append(f"| {s} | — | — | — | — | {v['note']} |")
            continue
        L.append(f"| {s} | {v['code']} | {v['first'][:10]} | {v['last'][:10]} | {v['n']} | {_g(v['gaps'])} |")
    L += ["", "\\* NIKKEI nu are contract în `data/contracts.yaml`. Contractul 240743 (NIKKEI STOCK AVERAGE YEN DENOM, CME) "
              "e descărcat doar pentru evaluare, din același dataset CFTC (jun7-fc8e), de la aceeași primă dată, "
              "în `~/.cache/macro-tournament/cftc`. Contractul în USD, 240741, nu mai raportează după 2026-03-03. "
              "DAX și FTSE100 nu au contract CFTC, deci sunt n/a la COT."]
    L += ["", "## 5. Put/call CBOE (`data/pc_history.parquet`)", "", "| Coloană | Prima | Ultima | N | Goluri |",
          "|---|---|---|---:|---|"]
    for k, v in a["pc"].items():
        L.append(f"| {k} | {v['first'][:10]} | {v['last'][:10]} | {v['n']} | {_g(v['gaps'])} |")
    r = a["retail"]
    L += ["", "## 6. Retail (`data/retail_history.parquet`)", "",
          f"Furnizori: {', '.join(r['providers'])}. Interval total: {r['first']} → {r['last']}.", "",
          "| Simbol | Prima | Ultima | Zile | Goluri |", "|---|---|---|---:|---|"]
    for s, v in sorted(r["per"].items()):
        L.append(f"| {s} | {v['first']} | {v['last']} | {v['days']} | {_g(v['gaps'], 2)} |")
    p = a["prices"]
    L += ["", "## 7. Prețuri", "", "| Unitate | Sursă | Prima | Ultima | N | Goluri (din 2014) | Ultima vineri cu randament la 4 săpt. |",
          "|---|---|---|---|---:|---|---|"]
    for u, v in p["series"].items():
        L.append(f"| {u} | {v['source']} | {v['first']} | {v['last']} | {v['n']} | {_g(v['gaps_since_2014'], 3)} | {v['last_friday_4w'][:10]} |")
    L += ["", f"**Sfârșitul ferestrei comune (regula din pre-înregistrare): {p['common_end_4w'][:10]}**. "
              f"Seriile care îl determină: {', '.join(p['binding'])}.", ""]
    m = a["price_mapping"]
    L += ["### Maparea față de `data/crossasset_instruments.yaml`", "",
          f"Instrumente în config: {', '.join(m['config_symbols'])}. Lipsă din mapare: {m['missing_in_map'] or 'niciunul'}. "
          f"În plus în mapare: {m['extra_in_map'] or 'niciunul'}.", "",
          "| Instrument | Tip (config) | home_ccy (config) | Sursă | Ticker/fișier |", "|---|---|---|---|---|"]
    for s, v in m["rows"].items():
        L.append(f"| {s} | {v['type']} | {v['home_ccy']} | {v['vendor']} | `{v['ticker']}` |")
    L += ["", "### Corelația randamentelor zilnice Yahoo vs FRED (prag > 0,99)", "",
          "| Instrument | Yahoo | FRED | Perioada comună | N randamente | Corelație | Abatere max. de nivel | OK |",
          "|---|---|---|---|---:|---:|---:|---|"]
    for s, v in a["fred_correlation"].items():
        L.append(f"| {s} | {v['yahoo']} | {v['fred']} | {v['common_from']} → {v['common_to']} | {v['n_returns']} | "
                 f"{v['corr']:.6f} | {100 * v['max_abs_level_rel_diff']:.3f}% | {'da' if v['ok'] else 'NU'} |")
    return "\n".join(L) + "\n"


def main() -> int:
    logging.disable(logging.CRITICAL)
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=str(ROOT / "docs" / "eval" / "scoring-tournament-data-audit.md"))
    ap.add_argument("--now", default=None, help="instant of the audit (UTC); default: now")
    a = ap.parse_args()
    now = pd.Timestamp(a.now) if a.now else pd.Timestamp.now(tz="UTC").tz_localize(None).floor("s")
    res = run(now)
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "audit.json").write_text(json.dumps(res, indent=1, default=str))
    Path(a.md).write_text(to_md(res))
    ok = res["price_mapping"]["ok"] and all(v["ok"] for v in res["fred_correlation"].values())
    print(json.dumps({"common_end_4w": res["prices"]["common_end_4w"], "binding": res["prices"]["binding"],
                      "prices_ok": ok}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
