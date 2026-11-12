"""OOS check — evaluation and report (docs/eval/oos-cot-rates.md).

    python scripts/measure/oos/run.py            # scores → metrics → report, prints sections 1–2
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import signals as S  # noqa: E402

import metrics as M  # noqa: E402  (scripts/measure/tournament)
import prices as P  # noqa: E402

CACHE = Path.home() / ".cache" / "macro-tournament" / "oos"
OUT = S.ROOT / "docs" / "eval" / "oos-cot-rates.md"
HS = (1, 2, 4)


# ---------------------------------------------------------------------------
# H1
# ---------------------------------------------------------------------------

def eval_h1(sc: pd.DataFrame, tg: pd.DataFrame, weeks) -> dict:
    n = len(weeks)
    half = math.ceil(n / 2)
    res = {"window": [str(weeks[0]), str(weeks[-1]), n], "half_split": str(weeks[half]), "variants": {}}
    pan = {}
    for v in ("L", "F", "C"):
        p = M.panel(sc[sc["variant"] == v], tg, "CCY", weeks)
        s = p["S"][v]
        pan[v] = (s, p["T"])
        defined = np.isfinite(s).any(axis=1)
        o = {"coverage": float((np.isfinite(s) & (s != 0)).sum() / s.size),
             "first_week": str(weeks[int(np.argmax(defined))]),
             "last_week": str(weeks[len(weeks) - 1 - int(np.argmax(defined[::-1]))]), "ba": {}, "ic": {}}
        for h in HS:
            r = p["T"][f"fwd_{h}"]
            cnt = M.call_counts(s, r)
            idx = M.boot_idx(n, M.BLOCK[h])
            ba, hit, calls = M.ba_from(cnt.sum(0))
            o["ba"][str(h)] = {"ba": ba, "hit": hit, "calls": int(calls), "ci": M.ci(M.ba_from(cnt[idx].sum(1))[0]),
                               "half1": M.ba_from(cnt[:half].sum(0))[0], "half2": M.ba_from(cnt[half:].sum(0))[0]}
            w = M.weekly_xs_ic(s, r, 6)
            o["ic"][str(h)] = {"ic": float(np.nanmean(w)), "ci": M.ci([np.nanmean(w[i]) for i in idx]),
                               "half1": float(np.nanmean(w[:half])), "half2": float(np.nanmean(w[half:])),
                               "weeks": int(np.isfinite(w).sum())}
        res["variants"][v] = o
    # decision: L − C at 2 weeks
    (sl, T), (scc, _) = pan["L"], pan["C"]
    r = T["fwd_2"]
    cl, cc = M.call_counts(sl, r), M.call_counts(scc, r)
    idx = M.boot_idx(n, M.BLOCK[2])
    d = M.ba_from(cl.sum(0))[0] - M.ba_from(cc.sum(0))[0]
    db = M.ba_from(cl[idx].sum(1))[0] - M.ba_from(cc[idx].sum(1))[0]
    dec = {"d_ba": d, "d_ci": M.ci(db), "p": float((1 + np.sum(~(db > 0))) / (M.B + 1)),
           "d_half1": M.ba_from(cl[:half].sum(0))[0] - M.ba_from(cc[:half].sum(0))[0],
           "d_half2": M.ba_from(cl[half:].sum(0))[0] - M.ba_from(cc[half:].sum(0))[0],
           "ic_F_2w": res["variants"]["F"]["ic"]["2"]["ic"]}
    dec["c1"] = bool(dec["d_ba"] > 0 and dec["p"] < 0.05)
    dec["c2"] = bool(dec["d_half1"] > 0 and dec["d_half2"] > 0)
    dec["c3"] = bool(dec["ic_F_2w"] <= 0)
    dec["remove_flow"] = dec["c1"] and dec["c2"] and dec["c3"]
    res["decision"] = dec
    return res


# ---------------------------------------------------------------------------
# H2
# ---------------------------------------------------------------------------

def eval_h2(rs: pd.DataFrame, weeks) -> dict:
    px = {s: P.cross_price(s) for s in S.INDICES}
    R = rs.pivot(index="week", columns="unit", values="R").reindex(index=weeks, columns=list(S.INDICES)).to_numpy(float)
    ret = np.array([[P.fwd_log_return(px[s], t, 2) if True else None for s in S.INDICES] for t in weeks], dtype=float)
    thr = {s: float(np.quantile(np.abs(R[np.isfinite(R[:, j]), j]), 0.55)) for j, s in enumerate(S.INDICES)}
    th = np.array([thr[s] for s in S.INDICES])[None, :]
    ok = np.isfinite(R) & np.isfinite(ret)
    bear, bull = ok & (R <= -th), ok & (R >= th)
    rest = ok & ~bear & ~bull
    r0 = np.where(ok, ret, 0.0)

    def sums(mask):
        return np.stack([(r0 * mask).sum(1), mask.sum(1)], axis=1)       # [weeks × 2]

    G = {"bear": sums(bear), "bull": sums(bull), "rest": sums(rest), "not_bear": sums(ok & ~bear),
         "not_bull": sums(ok & ~bull), "all": sums(ok)}

    def mean(g, sel=None):
        x = G[g] if sel is None else G[g][sel]
        s = x.sum(axis=-2)
        with np.errstate(invalid="ignore", divide="ignore"):
            return s[..., 0] / s[..., 1]

    n = len(weeks)
    idx = M.boot_idx(n, M.BLOCK[2])
    h1 = np.array([w < S.H2_HALF2 for w in weeks])
    out = {"window": [str(weeks[0]), str(weeks[-1]), n], "thresholds_p55": thr,
           "n_index_weeks": int(ok.sum()), "groups": {}, "halves": {}}
    for g in ("bear", "bull", "rest", "all"):
        boot = np.array([mean(g, i) for i in idx])
        out["groups"][g] = {"mean": float(mean(g)), "ci": M.ci(boot), "n": int(G[g][:, 1].sum())}
    for name, a, b in (("d_bear", "bear", "not_bear"), ("d_bull", "bull", "not_bull")):
        d = float(mean(a) - mean(b))
        boot = np.array([mean(a, i) - mean(b, i) for i in idx])
        p = (1 + np.sum(boot >= 0)) / (M.B + 1) if name == "d_bear" else (1 + np.sum(boot <= 0)) / (M.B + 1)
        out["groups"][name] = {"diff": d, "ci": M.ci(boot), "p_one_sided": float(p)}
    for hn, sel in (("half1", h1), ("half2", ~h1)):
        hw = [w for w, k in zip(weeks, sel) if k]
        out["halves"][hn] = {"from": str(hw[0]), "to": str(hw[-1]), "weeks": len(hw)}
        for g in ("bear", "bull", "rest"):
            out["halves"][hn][g] = {"mean": float(mean(g, sel)), "n": int(G[g][sel, 1].sum())}
        out["halves"][hn]["d_bear"] = float(mean("bear", sel) - mean("not_bear", sel))
        out["halves"][hn]["d_bull"] = float(mean("bull", sel) - mean("not_bull", sel))
    per = {}
    for j, s in enumerate(S.INDICES):
        b, nb = bear[:, j], ok[:, j] & ~bear[:, j]
        per[s] = {"first_R": str(weeks[int(np.argmax(np.isfinite(R[:, j])))]),
                  "bear_n": int(b.sum()), "bull_n": int(bull[:, j].sum()),
                  "d_bear": float(ret[b, j].mean() - ret[nb, j].mean()) if b.any() and nb.any() else None}
    out["per_index"] = per
    d = out["groups"]["d_bear"]
    dec = {"c1": bool(d["diff"] < 0 and d["p_one_sided"] < 0.05),
           "c2": bool(out["halves"]["half1"]["d_bear"] < 0 and out["halves"]["half2"]["d_bear"] < 0)}
    dec["symmetric"] = dec["c1"] and dec["c2"]
    out["decision"] = dec
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def f(x, d=3, pct=False):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    return f"{100 * x:.2f}%" if pct else f"{x:.{d}f}"


def fci(c, d=3, pct=False):
    return "" if not c or any(not math.isfinite(v) for v in c) else f" [{f(c[0], d, pct)}; {f(c[1], d, pct)}]"


def report(h1: dict, h2: dict, cov: dict) -> tuple[str, str]:
    names = {"L": "L — level", "F": "F — flow", "C": "C — celula v3 (cot_cell)"}
    s1 = ["## 1. H1 — COT FX: level fără flow", "",
          f"Fereastra: {h1['window'][0][:10]} → {h1['window'][1][:10]} ({h1['window'][2]} de vineri). Jumătatea a doua "
          f"începe la {h1['half_split'][:10]}. Unitățile sunt cele 8 valute (USD = DXY), cu randament demeaned față de USD. "
          "BA se calculează pe apeluri (scor ≠ 0), iar IC e media Spearman-ului cross-secțional. Între paranteze drepte e CI 95% din bootstrap pe blocuri.", "",
          "| Semnal | Prima / ultima săpt. | Acoperire | BA 1s | BA 2s | BA 4s | IC 1s | IC 2s | IC 4s |",
          "|---|---|---:|---|---|---|---|---|---|"]
    for v in ("L", "F", "C"):
        o = h1["variants"][v]
        s1.append(f"| **{names[v]}** | {o['first_week'][:10]} / {o['last_week'][:10]} | {f(o['coverage'], pct=True)} | "
                  + " | ".join(f"{f(o['ba'][h]['ba'])}{fci(o['ba'][h]['ci'])}" for h in ("1", "2", "4")) + " | "
                  + " | ".join(f"{f(o['ic'][h]['ic'])}{fci(o['ic'][h]['ci'])}" for h in ("1", "2", "4")) + " |")
    s1 += ["", "Pe jumătăți (jum. 1 / jum. 2):", "",
           "| Semnal | BA 1s | BA 2s | BA 4s | IC 1s | IC 2s | IC 4s |", "|---|---|---|---|---|---|---|"]
    for v in ("L", "F", "C"):
        o = h1["variants"][v]
        s1.append(f"| {v} | " + " | ".join(f"{f(o['ba'][h]['half1'])} / {f(o['ba'][h]['half2'])}" for h in ("1", "2", "4"))
                  + " | " + " | ".join(f"{f(o['ic'][h]['half1'])} / {f(o['ic'][h]['half2'])}" for h in ("1", "2", "4")) + " |")
    d = h1["decision"]
    yn = lambda b: "**da**" if b else "nu"  # noqa: E731
    s1 += ["", "**Decizia (pre-înregistrată):**", "",
           f"1. BA la 2 săptămâni, L − C = {f(d['d_ba'], 4)}{fci(d['d_ci'], 4)}, p unilateral {f(d['p'])} → {yn(d['c1'])}",
           f"2. L − C în ambele jumătăți: {f(d['d_half1'], 4)} / {f(d['d_half2'], 4)} → {yn(d['c2'])}",
           f"3. IC-ul lui F la 2 săptămâni = {f(d['ic_F_2w'])} ≤ 0 → {yn(d['c3'])}", "",
           ("**Rezultat: toate trei sunt adevărate → „scoate flow din celula COT”.**" if d["remove_flow"] else
            "**Rezultat: nu toate trei sunt adevărate → celula COT rămâne cum e.**"), ""]
    g, hv, dec = h2["groups"], h2["halves"], h2["decision"]
    s2 = ["## 2. H2 — Indici: ratele simetrice", "",
          f"Fereastra: {h2['window'][0][:10]} → {h2['window'][1][:10]} ({h2['window'][2]} de vineri). Datele sunt pooled pe cei 6 indici "
          f"({h2['n_index_weeks']} indice × săptămâni cu R și randament). R este blocul de rate v3, adică media semnalelor −Δ63/σ_y "
          "pentru 2Y din valuta de bază și pentru DFII10. Pragul e p55 din |R| per indice, pe toată fereastra. "
          "Ținta e randamentul log la 2 săptămâni (Yahoo). CI 95% vine din bootstrap pe blocuri de 4 săptămâni.", "",
          "| Grup | Toată fereastra: medie [CI] | N | Jum. 1 (2014-01 → 2018-10) | N | Jum. 2 (2018-11 → 2023-09) | N |",
          "|---|---|---:|---|---:|---|---:|"]
    lab = {"bear": "După apeluri bearish (R ≤ −p55)", "bull": "După apeluri bullish (R ≥ +p55)",
           "rest": "Restul săptămânilor (nici bearish, nici bullish)"}
    for k in ("bear", "bull", "rest"):
        s2.append(f"| {lab[k]} | {f(g[k]['mean'], pct=True)}{fci(g[k]['ci'], pct=True)} | {g[k]['n']} | "
                  f"{f(hv['half1'][k]['mean'], pct=True)} | {hv['half1'][k]['n']} | {f(hv['half2'][k]['mean'], pct=True)} | {hv['half2'][k]['n']} |")
    s2 += [f"| Δ bearish − toate celelalte | {f(g['d_bear']['diff'], pct=True)}{fci(g['d_bear']['ci'], pct=True)}, "
           f"p unilateral {f(g['d_bear']['p_one_sided'])} | | {f(hv['half1']['d_bear'], pct=True)} | | {f(hv['half2']['d_bear'], pct=True)} | |",
           f"| Δ bullish − toate celelalte | {f(g['d_bull']['diff'], pct=True)}{fci(g['d_bull']['ci'], pct=True)}, "
           f"p unilateral (> 0) {f(g['d_bull']['p_one_sided'])} | | {f(hv['half1']['d_bull'], pct=True)} | | {f(hv['half2']['d_bull'], pct=True)} | |",
           "", "Per indice (informativ): " + "; ".join(
               f"{s} Δ_bear {f(x['d_bear'], pct=True)} ({x['bear_n']} apeluri bearish, {x['bull_n']} bullish)"
               for s, x in h2["per_index"].items()) + ".", "",
           "**Decizia (pre-înregistrată):**", "",
           f"1. Randamentul după apelurile bearish e mai mic decât în celelalte săptămâni, cu p < 0,05: Δ {f(g['d_bear']['diff'], pct=True)}, "
           f"p {f(g['d_bear']['p_one_sided'])} → {yn(dec['c1'])}",
           f"2. Același semn (negativ) în ambele jumătăți: {f(hv['half1']['d_bear'], pct=True)} / {f(hv['half2']['d_bear'], pct=True)} → {yn(dec['c2'])}",
           "", ("**Rezultat: ambele sunt adevărate → „ratele devin simetrice la indici”.**" if dec["symmetric"] else
                "**Rezultat: nu ambele sunt adevărate → regula asimetrică rămâne.**"), ""]
    s3 = ["## 3. Datele", "", "### Istoricul CFTC (jun7-fc8e, descărcat pe 2026-10-16)", "",
          "| Valută | Cod | Primul raport | Ultimul raport | Rapoarte | Goluri > 8 zile (toate) | Goluri în H1 sau în cei 3 ani dinainte |",
          "|---|---|---|---|---:|---|---|"]
    for c, x in cov["cftc"].items():
        gl = "; ".join(f"{a}→{b} ({n}z)" for a, b, n in x["gaps"]) or "—"
        s3.append(f"| {c} | {x['code']} | {x['first']} | {x['last']} | {x['n']} | {gl} | {'; '.join(f'{a}→{b} ({n}z)' for a, b, n in x['gaps_in_window']) or '—'} |")
    s3 += ["", "- Începutul H1 cere 156 de rapoarte consecutive, fără goluri, la toate cele 8 contracte. "
               "NZD îl fixează: al 156-lea raport după golul 2006-06-13 → 2006-07-11 vine pe 2009-06-30.",
           f"- Concordanța cu `data/history.parquet`: rândurile brute sunt identice pe cele {cov['hist']['common_reports']} de "
           f"rapoarte × contracte comune, cu {cov['hist']['raw_mismatches']} diferențe. L, F și C au fost comparate pe "
           f"{cov['hist']['comparable_cells']} de celule (valută × vineri × semnal) din 2023-03 încoace. Celulele sunt cele pentru care "
           "`history.parquet` are fereastra trailing completă: 156 de rapoarte pentru L și C, adică din 2026-03, și 57 pentru F. "
           f"Diferențe: {cov['hist']['mismatches']}. Înainte de 2026-03, `history.parquet` nu are 3 ani de istoric, deci L și C diferă "
           "legitim (bază „6m_only”). Testul este în `tests/eval/test_oos_cot_rates.py`.",
           "- 2Y EUR/GBP înainte de `data/rates.parquet`:",
           "  - EUR din ECB YC, de la 2013-01-02, cu diferența max. 4e-16 pp pe 1.294 de zile comune;",
           "  - GBP din BoE GLC, de la 2013-01-02, cu diferența max. 9e-16 pp pe 2.715 zile comune;",
           "  - cache în `~/.cache/macro-tournament/yields`, verificat de `scripts/measure/oos/yields_ext.py`.",
           "- Prima vineri cu R, per indice: " + ", ".join(f"{s} {x['first_R'][:10]}" for s, x in h2["per_index"].items()) + ".",
           "- Prețurile vin din cache-ul turneului: FRED H.10 pentru FX și Yahoo pentru indici. Nu s-au folosit `price_history.parquet` și MT5.",
           "- Pre-înregistrarea: `docs/eval/oos-cot-rates-prereg.md` (commit 2094be9e), făcută înainte de orice calcul pe randamente."]
    head = ["# Verificare out-of-sample: COT FX și rate la indici", "",
            "Evaluare read-only pe date anterioare ferestrei turneului (până la 2023-09-15). Producția nu se schimbă.", ""]
    md = "\n".join(head + s1 + s2 + s3) + "\n"
    return md, "\n".join(s1 + s2)


def coverage(en_raw: pd.DataFrame) -> dict:
    out = {"cftc": {}}
    for c, code in S.CODES.items():
        d = en_raw[en_raw["cftc_contract_market_code"] == code][S.DATE].drop_duplicates().sort_values().reset_index(drop=True)
        g = d.diff().dt.days
        gaps = [(str(d[i - 1].date()), str(d[i].date()), int(x)) for i, x in enumerate(g) if x > 8]
        inw = [x for x in gaps if pd.Timestamp(x[1]) >= S.H1_START - pd.Timedelta(days=3 * 365) and
               pd.Timestamp(x[0]) <= S.H1_END]
        out["cftc"][c if c != "USD" else "USD (DXY)"] = {"code": code, "first": str(d.min().date()), "last": str(d.max().date()),
                                                       "n": int(len(d)), "gaps": gaps, "gaps_in_window": inw}
    return out


def main() -> int:
    import logging
    logging.disable(logging.CRITICAL)
    CACHE.mkdir(parents=True, exist_ok=True)
    raw = S.cftc_raw()
    en = S.enrich(raw)
    w1 = S.fridays(S.H1_START, S.H1_END)
    sc = S.h1_scores(en, w1)
    sc.to_parquet(CACHE / "h1_scores.parquet", index=False)
    tg = M.targets(w1)
    h1 = eval_h1(sc, tg, w1)
    w2 = S.fridays(S.H2_START, S.H2_END)
    rs = S.h2_scores(S.rates_ext(), pd.read_parquet(S.ROOT / "data" / "real_yields.parquet"), w2)
    rs.to_parquet(CACHE / "h2_scores.parquet", index=False)
    h2 = eval_h2(rs, w2)
    cov = coverage(raw)
    hist = json.loads((CACHE / "hist_check.json").read_text()) if (CACHE / "hist_check.json").exists() else \
        {"common_reports": "?", "comparable_weeks": "?", "mismatches": "?"}
    cov["hist"] = hist
    (CACHE / "results.json").write_text(json.dumps({"h1": h1, "h2": h2, "cov": cov}, default=str, indent=1))
    md, s12 = report(h1, h2, cov)
    OUT.write_text(md)
    print(s12)
    return 0


if __name__ == "__main__":
    sys.exit(main())
