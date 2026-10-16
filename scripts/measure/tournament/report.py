"""Step 7 — docs/eval/scoring-tournament.md (Romanian) from the cached metric JSONs.

    python scripts/measure/tournament/report.py [--print-12]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CACHE, CCYS, INDICES, METALS, ROOT  # noqa: E402
import variants as VV  # noqa: E402

OUT = ROOT / "docs" / "eval" / "scoring-tournament.md"
CLS_NAME = {"FX": "FX (28 de perechi + US Dollar)", "IDX": "Indici", "MET": "Metale"}


def f(x, d=3, pct=False):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    return f"{100 * x:.1f}%" if pct else f"{x:.{d}f}"


def fci(c, d=3, pct=False):
    if not c or any(v is None or not math.isfinite(v) for v in c):
        return ""
    return f" [{f(c[0], d, pct)}; {f(c[1], d, pct)}]"


def fp(p):
    if p is None or not math.isfinite(p):
        return "—"
    return "<0,001" if p < 0.001 else f"{p:.3f}"


def ranking(R: dict, cls: str) -> list[str]:
    rows = [(v, o) for v, o in R.items() if "na" not in o]
    rows.sort(key=lambda x: -(x[1]["ba"]["2"]["ba"] if math.isfinite(x[1]["ba"]["2"]["ba"] or float("nan")) else -9))
    L = ["| # | Variantă | Fereastră | BA 1s | BA 2s [CI 95%] | BA 4s | Hit p55 / p90 (2s) | IC 2s [CI 95%] | "
         "Acoperire | BA 2s jum. 1 / 2 | Δ BA 2s vs v3 [CI] | p Holm | Bate v3 |",
         "|---:|---|---|---|---|---|---|---|---:|---|---|---:|---|"]
    for i, (v, o) in enumerate(rows, 1):
        b = o["ba"]
        st = o["strong"]["2"]
        win = f"{o['window'][0][:10]} → {o['window'][1][:10]} ({o['window'][2]})"
        d = o.get("vs_v3")
        if d:
            beat = "**DA**" if d["beats_v3"] else "nu (" + ", ".join(
                n for n, ok in (("1", d["c1"]), ("2", d["c2"]), ("3", d["c3"])) if not ok) + ")"
            dd = f"{f(d['d_ba'], 3)}{fci(d['d_ba_ci'])}"
            ph = fp(d["p_holm_ba"])
        else:
            beat, dd, ph = "referință", "—", "—"
        L.append(f"| {i} | **{v}** {VV.NAMES[v]} | {win} | {f(b['1']['ba'])}{fci(b['1']['ci'])} | "
                 f"{f(b['2']['ba'])}{fci(b['2']['ci'])} | {f(b['4']['ba'])}{fci(b['4']['ci'])} | "
                 f"{f(st['p55']['hit'], pct=True)} / {f(st['p90']['hit'], pct=True)} | "
                 f"{f(o['ic']['2']['ic'])}{fci(o['ic']['2']['ci'])} | {f(o['coverage'], pct=True)} | "
                 f"{f(b['2']['half1'])} / {f(b['2']['half2'])} | {dd} | {ph} | {beat} |")
    na = [(v, o["na"]) for v, o in R.items() if "na" in o]
    if na:
        L += ["", "n/a în clasă: " + "; ".join(f"{v} ({why})" for v, why in na) + "."]
    return L


def _why(d: dict) -> str:
    """Compact reasons for the failed conditions."""
    parts = []
    if not d["c1"]:
        parts.append(f"1 (Δ BA {f(d['d_ba'])}{fci(d['d_ba_ci'])}, p Holm {fp(d['p_holm_ba'])})")
    if not d["c2"]:
        parts.append(f"2 (Δ pe jumătăți {f(d['d_ba_h1'])} / {f(d['d_ba_h2'])})")
    if not d["c3"]:
        parts.append(f"3 (IC {f(d['ic_var'])} < {f(d['ic_v3'])})")
    word = "condiția" if len(parts) == 1 else "condițiile"
    return f"{word} " + (", ".join(parts[:-1]) + " și " + parts[-1] if len(parts) > 1 else parts[0])


def _cls_lower(c: str) -> str:
    return {"FX": "FX", "IDX": "indici", "MET": "metale"}[c]


def conclusion(m: dict) -> list[str]:
    out = []
    winners = {cls: [v for v, o in R.items() if "na" not in o and (o.get("vs_v3") or {}).get("beats_v3")]
               for cls, R in m["classes"].items()}
    if not any(winners.values()):
        out.append("**Nicio variantă nu îndeplinește toate cele trei condiții pre-înregistrate în nicio clasă "
                   "(FX, indici, metale), deci niciuna nu e mai precisă decât v3 și clasamentul rămâne descriptiv.**")
    else:
        out.append("**" + " ".join(
            f"{CLS_NAME[c]}: {', '.join(w)} îndeplinesc toate cele trei condiții și sunt mai precise decât v3."
            if w else f"{CLS_NAME[c]}: nicio variantă nu îndeplinește toate cele trei condiții."
            for c, w in winners.items()) + "**")
    for cls, R in m["classes"].items():
        a1 = R["A1"]
        v, o = max(((v, o) for v, o in R.items() if "na" not in o and v != "A1"),
                   key=lambda x: x[1]["ba"]["2"]["ba"] if math.isfinite(x[1]["ba"]["2"]["ba"]) else -9)
        d = o["vs_v3"]
        out.append(f"La {_cls_lower(cls)}, v3 are BA la 2 săptămâni {f(a1['ba']['2']['ba'])}{fci(a1['ba']['2']['ci'])} "
                   f"și IC {f(a1['ic']['2']['ic'])}, iar cea mai bună alternativă, {v} ({VV.NAMES[v]}, BA "
                   f"{f(o['ba']['2']['ba'])}), "
                   + ("trece toate condițiile." if d["beats_v3"] else f"pică {_why(d)}."))
    a = {c: m["classes"][c]["A1"]["ba"]["2"]["ci"] for c in m["classes"]}
    inc = [_cls_lower(c) for c, x in a.items() if x[0] <= 0.5]
    out.append("Diferențele dintre variante sunt mici față de incertitudine: CI-ul BA al lui v3 "
               + (f"include 0,5 la {' și '.join(inc)}" if inc else "exclude 0,5 în toate clasele")
               + ", iar constantele v3, măsurate pe aceeași fereastră, îi dau lui A1/A2 un avantaj in-sample.")
    return out


def section_coinc(m: dict) -> list[str]:
    L = ["### 3a. Coincident vs predictiv (IC la 2 săptămâni)", "",
         "„Trecut” = IC cu randamentul ultimelor 2 săptămâni până vineri. „Următor” = IC cu randamentul de după intrare.",
         "", "| Variantă | FX trecut | FX următor | Indici trecut | Indici următor | Metale trecut | Metale următor |",
         "|---|---|---|---|---|---|---|"]
    for v in VV.VARIANTS:
        cells = []
        for cls in ("FX", "IDX", "MET"):
            o = m["classes"][cls].get(v, {})
            if "na" in o or not o:
                cells += ["n/a", "n/a"]
            else:
                c = o["coinc"]["2"]
                cells += [f(c["ic_past"]), f(c["ic_fwd"])]
        L.append(f"| {v} | " + " | ".join(cells) + " |")
    L += ["", "### 3b. Valoare peste trend (β-ul scorului lângă F1, 2 săptămâni, CI 95%)", "",
          "| Variantă | FX | Indici | Metale |", "|---|---|---|---|"]
    for v in VV.VARIANTS:
        if v == "F1":
            continue
        cells = []
        for cls in ("FX", "IDX", "MET"):
            o = m["classes"][cls].get(v, {})
            vt = (o.get("vot") or {}).get("2") if "na" not in o else None
            cells.append("n/a" if not vt else f"{f(vt['beta'])}{fci(vt['ci'])}")
        L.append(f"| {v} | " + " | ".join(cells) + " |")
    L += ["", "### 3c. Diagnostic de semn (IC la 2 săptămâni pe categorie, cu semnul folosit)", "",
          "| Variantă | Clasă | growth | inflation | labour | 2Y (C2) |", "|---|---|---|---|---|---|"]
    sd = m["sign_diag"]
    for v in ("B2", "B3", "C1", "C2"):
        for cls in ("FX", "IDX", "MET"):
            cells = [f(sd.get(f"{v}|{cls}|{c}", {}).get("ic")) for c in ("growth", "inflation", "labour", "rates")]
            L.append(f"| {v} | {cls} | " + " | ".join(cells) + " |")
    return L


def section_extra(m: dict) -> list[str]:
    L = ["### 3d. PnL naiv (Sharpe anualizat; probabilitatea Sharpe-ului deflat în paranteză)", "",
         "| Variantă | FX 1s | FX 2s | FX 4s | Indici 2s | Metale 2s |", "|---|---|---|---|---|---|"]
    for v in VV.VARIANTS:
        cells = []
        for cls, h in (("FX", "1"), ("FX", "2"), ("FX", "4"), ("IDX", "2"), ("MET", "2")):
            o = m["classes"][cls].get(v, {})
            if "na" in o or not o:
                cells.append("n/a")
                continue
            p = o["pnl"][h]
            cells.append(f"{f(p['sharpe_ann'], 2)} ({f(p.get('dsr'), 2)})")
        L.append(f"| {v} | " + " | ".join(cells) + " |")
    L += ["", "### 3e. Randamentul mediu pe chintile de scor (2 săptămâni, log, %; Q1 = scorurile cele mai mici)", "",
          "| Variantă | FX Q1 → Q5 | Indici Q1 → Q5 | Metale Q1 → Q5 |", "|---|---|---|---|"]
    for v in VV.VARIANTS:
        cells = []
        for cls in ("FX", "IDX", "MET"):
            o = m["classes"][cls].get(v, {})
            q = o.get("quint") if "na" not in o else None
            cells.append("n/a" if not q else " / ".join(f"{100 * x['mean_ret']:.2f}" for x in q))
        L.append(f"| {v} | " + " | ".join(cells) + " |")
    L += ["", "### 3f. Robustețe pe valută și instrument (BA la 2 săptămâni)", "",
          "FX pe valută (vederea pe valute, randament demeaned):", "",
          "| Variantă | " + " | ".join(CCYS) + " |", "|---|" + "---|" * len(CCYS)]
    for v in VV.VARIANTS:
        o = m["classes"]["FX"].get(v, {})
        pc = o.get("per_ccy") if "na" not in o else None
        L.append(f"| {v} | " + " | ".join(f(pc[c]["ba"]) if pc else "n/a" for c in CCYS) + " |")
    L += ["", "Indici și metale:", "", "| Variantă | " + " | ".join(INDICES + METALS) + " |",
          "|---|" + "---|" * (len(INDICES) + len(METALS))]
    for v in VV.VARIANTS:
        cells = []
        for cls, units in (("IDX", INDICES), ("MET", METALS)):
            o = m["classes"][cls].get(v, {})
            pu = o.get("per_unit") if "na" not in o else None
            cells += [f(pu[u]["ba"]) if pu else "n/a" for u in units]
        L.append(f"| {v} | " + " | ".join(cells) + " |")
    L += ["", "Pe pereche (29 de unități FX), IC pe serie temporală pe valută și instrument: în "
              "`docs/eval/scoring-tournament-metrics.csv` (`unit_ba:*`, `unit_ic:*`, `ccy_ba:*`, `ccy_ic:*`)."]
    return L


def section_long(lg: dict) -> list[str]:
    L = [f"Săptămâni {lg['window'][0][:10]} → {lg['window'][1][:10]} ({lg['window'][2]}). v3 nu există aici. "
         "Comparația este Δ BA la 2 săptămâni față de F1 (bootstrap pereche, Holm) și e descriptivă.", ""]
    for cls in ("FX", "IDX", "MET"):
        L += [f"**{CLS_NAME[cls]}**", "", "| Variantă | Prima săpt. cu scor | BA 1s | BA 2s [CI] | BA 4s | Hit 2s | "
              "IC 1s | IC 2s [CI] | IC 4s | Δ BA 2s vs F1 (p Holm) |", "|---|---|---|---|---|---|---|---|---|---|"]
        for v in sorted(lg["classes"][cls]):
            o = lg["classes"][cls][v]
            if "na" in o:
                L.append(f"| {v} | n/a ({o['na']}) | | | | | | | | |")
                continue
            b, ic = o["ba"], o["ic"]
            d = o.get("vs_f1")
            dd = "etalon" if v == "F1" else (f"{f(d['d_ba'])} ({fp(d.get('p_holm'))})" if d else "—")
            L.append(f"| {v} | {o['first_week'][:10]} | {f(b['1']['ba'])} | {f(b['2']['ba'])}{fci(b['2']['ci'])} | "
                     f"{f(b['4']['ba'])} | {f(b['2']['hit'], pct=True)} | {f(ic['1']['ic'])} | "
                     f"{f(ic['2']['ic'])}{fci(ic['2']['ci'])} | {f(ic['4']['ic'])} | {dd} |")
        L.append("")
    L.append("D1 (carry) este n/a în fereastra lungă: `policy_rate_history` începe în 2023-01, "
             "pentru că deciziile din FF și `data/cb/decisions.parquet` nu merg mai înapoi.")
    return L


def section_data(m: dict, audit: dict) -> list[str]:
    st = m["starts"]
    late = sorted(k for k, v in st.items() if v > m["common_start"] and
                  (__import__("pandas").Timestamp(v) - __import__("pandas").Timestamp(m["common_start"])).days > 91)
    L = [f"- Fereastra comună: {m['common_start'][:10]} → {m['end'][:10]}. Variante evaluate separat, pe fereastra lor "
         f"(încep la peste 13 săptămâni după v3): " + ", ".join(
             f"{k.split('|')[1]} la {({'FX': 'FX', 'IDX': 'indici', 'MET': 'metale'})[k.split('|')[0]]} (din {st[k][:10]})"
             for k in late) + ".",
         "- Acoperirea fiecărei variante (% unitate × săptămână cu scor definit și ≠ 0) e în coloana „Acoperire” din "
         "clasament și în CSV (`coverage`).",
         "- **n/a și de ce:**",
         "  - D1 la indici și metale: e doar FX, după definiție.",
         "  - E1, E2, E3 și componenta COT din B1 la DAX și FTSE100: nu există contract CFTC.",
         "  - D2 FX, componenta de rate FX din B1 și 2Y-ul din C2 la NZD și CHF: nu există 2Y în `data/rates.parquet` "
         "(decizie din 2023-09-23). De aceea D2 FX are doar 15 din 28 de perechi.",
         "  - C1/C2, factorul de inflație la CAD, CHF și NZD: `cpi_yoy` e „CPI m/m”, respectiv „CPI q/q”, nu y/y. "
         "Nu s-a folosit niciun substitut.",
         "  - C1/C2, factorul de creștere la EUR și AUD înainte de 2026-01 și la JPY înainte de 2025-12: "
         "PMI-ul din cadrul de scoring începe atunci (releasele „Final” sunt telemetry).",
         "  - B1: consumer confidence și PCE y/y nu există în config, deci nu intră. "
         "Trendul tehnic EdgeFinder nu are regulă publicată, deci nu intră.",
         "- **Substitute (declarate în pre-înregistrare):**",
         "  - Ratele FX din B1 = 2Y − rata de politică (`policy_rate_history`), în loc de prognozele băncilor centrale, "
         "al căror istoric lipsește.",
         f"  - Retail în B1: `data/retail_history.parquet` începe pe {audit['retail']['first']} "
         "(myfxbook), sub pragul de 80% din fereastra comună, deci componenta **nu intră**.",
         "  - COT Nikkei: contractul 240743 (yen), descărcat doar pentru evaluare. Contractul în USD, 240741, "
         "nu mai raportează după 2026-03-03.",
         "- Prețuri:",
         "  - FX vine din FRED H.10, cu ultima zi 2026-10-09.",
         "  - Indicii vin din Yahoo (`^GSPC`, `^IXIC`, `^DJI`, `^GDAXI`, `^N225`, `^FTSE`, coloana Close). "
         "Corelația zilnică cu FRED e 0,999995 / 0,995495 / 0,999999 / 1,000000 (SP500 / NASDAQ / DJIA / NIKKEI).",
         "  - Metalele vin din Stooq (CSV-urile din `~/Downloads`, până la 2026-10-06).",
         "  - Nu s-au folosit `data/price_history.parquet` și MT5.",
         "- Constantele v3 (σ, RMS, praguri) au fost măsurate pe 2023-09-22 → 2026-10-02. A1 și A2 sunt deci parțial "
         "in-sample pe această fereastră, ceea ce le avantajează față de celelalte variante.",
         "- Rapoartele COT sunt folosite după data de raport (marți) ≤ t, ca în producție. În săptămânile cu publicare "
         "amânată de o sărbătoare, raportul e folosit cu cel mult 3 zile mai devreme. Efectul e același pentru toate variantele.",
         "- Detaliile auditului (prima și ultima dată, goluri pe valută × indicator, rate, COT, P/C, retail, prețuri): "
         "`docs/eval/scoring-tournament-data-audit.md`."]
    return L


def build() -> tuple[str, str]:
    m = json.loads((CACHE / "metrics_main.json").read_text())
    lg = json.loads((CACHE / "metrics_long.json").read_text())
    audit = json.loads((CACHE / "audit.json").read_text())
    s1 = ["## 1. Clasamentul pe clasă", "",
          f"Fereastra comună: {m['common_start'][:10]} → {m['end'][:10]}. Variantele sunt ordonate după BA la 2 săptămâni. "
          "Cifrele sunt estimări punctuale, cu CI 95% din bootstrap pe blocuri de săptămâni. „Hit p55 / p90” = hit rate-ul "
          "pe apelurile cu |z| peste p55, respectiv p90. Δ și p sunt calculate față de v3 (A1), pe aceleași săptămâni. "
          "„Bate v3” cere condițiile 1, 2 și 3. Când nu le îndeplinește, în paranteză apar cele ratate.", ""]
    for cls in ("FX", "IDX", "MET"):
        s1 += [f"### {CLS_NAME[cls]}", ""] + ranking(m["classes"][cls], cls) + [""]
    s2 = ["## 2. Concluzia după regula de decizie", ""] + [" ".join(conclusion(m))]
    head = ["# Turneu de scoring — raport", "",
            "Evaluare read-only, branch `eval/scoring-tournament`. Producția nu se schimbă. Pre-înregistrarea "
            "(`docs/eval/scoring-tournament-prereg.md`, commit c33440af) a fost făcută înainte de orice metrică pe randamente. "
            "Toate metricile sunt în `docs/eval/scoring-tournament-metrics.csv`. Scorurile săptămânale stau în "
            "`~/.cache/macro-tournament`, nu în repo.", ""]
    s3 = ["## 3. Coincident vs predictiv, valoare peste trend, diagnostic de semn", ""] + section_coinc(m) + [""] \
        + section_extra(m)
    s4 = ["## 4. Fereastra lungă (variante fără calendarul FF)", ""] + section_long(lg)
    s5 = ["## 5. Datele", ""] + section_data(m, audit)
    md = "\n".join(head + s1 + s2 + [""] + s3 + [""] + s4 + [""] + s5) + "\n"
    return md, "\n".join(s1 + s2)


if __name__ == "__main__":
    md, s12 = build()
    OUT.write_text(md)
    if "--print-12" in sys.argv:
        print(s12)
