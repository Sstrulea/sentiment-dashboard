"""`python -m src.cb_compute --stance-report` - the full arithmetic of the stance per bank, as Markdown (pure rendering).

Every input with its as-of and source -> gap, y, x, N, S, G, situation; the same for 12 months; pivot, lens, cycle, market,
flags. Values are shown with 4 decimals (the computation is not rounded)."""
from __future__ import annotations

from typing import Optional


def f(x: Optional[float], nd: int = 4) -> str:
    return "n/a" if x is None else f"{x:+.{nd}f}" if x < 0 else f"{x:.{nd}f}"


def _val(v: Optional[dict]) -> str:
    if not v or v.get("v") is None:
        return "n/a"
    rng = f" (range {f(v['lo'])}..{f(v['hi'])})" if v.get("lo") is not None and v.get("hi") is not None else ""
    note = f"; {v['note']}" if v.get("note") else ""
    return f"{f(v['v'])}{rng} - as-of {v.get('asof')} - {v.get('source')}{note}"


def bank_block(r: dict, params: dict) -> str:
    inp = r["inputs"]
    star = r["target_star"]
    tgt = f"{star}" if star is not None else f"band {r['band'][0]}-{r['band'][1]} (no declared midpoint: distance to the band)"
    third = "0 (no slack)" if r["y"] is None else f"{f(r['y'])} / 3 = {f(r['y'] / 3)}"
    out = [f"### {r['currency']}", "",
           f"mandate version from {r['mandate_id']}; cb_stance v{r['stance_version']}; code {r['code_version']}; as-of {r['asof']}", "",
           "| input | value - as-of - source |", "|---|---|",
           f"| i (policy rate) | {_val(inp['rate'])} |",
           f"| pi* (target) | {tgt} |",
           f"| pi underlying | {_val(inp['pi_under'])} |",
           f"| pi headline | {_val(inp['pi_head'])} |",
           f"| slack type | {inp['slack_type']} |"]
    if inp["slack_type"] == "output_gap":
        out.append(f"| output gap (today) | {_val(inp['y_today'])} |")
    if inp["slack_type"] == "unemployment_gap":
        out += [f"| u | {_val(inp['u'])} |", f"| u* | {_val(inp['u_star'])} |"]
    N = r["N"]
    if N:
        used = "; ".join(f"{lab}: {f(v) if isinstance(v, float) else v}" for lab, v in N["used"])
        out.append(f"| N (neutral, nominal) | mid {f(N['mid'])}, lo {f(N['lo'])}, hi {f(N['hi'])} - as-of {N['asof']} - {N['flag']}"
                   f"{' STALE' if N['stale'] else ''} - from {N['nominal_from']} - {N['source']} [{used}] |")
        if N.get("check"):
            c = N["check"]
            out.append(f"| SEP dots check | median of {c['dots']} longer-run dots {f(c['from_dots'])} -> rounded half-even {c['rounded']} vs published "
                       f"{c['published']}: {'OK' if c['ok'] else 'ALERT - mismatch'} |")
    else:
        out.append(f"| N (neutral) | n/a - {inp['neutral_status']} |")
    out += [f"| pi (12 months) | {_val(inp['pi_12m'])} - basis {inp['pi_12m_basis'] or 'n/a'} |"]
    if inp["slack_type"] == "output_gap":
        out.append(f"| output gap (12 months) | {_val(inp['y_12m'])} |")
    if inp["slack_type"] == "unemployment_gap":
        out.append(f"| u (12 months) | {_val(inp['u_12m'])} |")
    out += ["", "**Today**", "",
            f"- gap(pi underlying) = {f(r['pi_under'])} - pi* -> {f(r['gap_under'])};  gap(pi headline) -> {f(r['gap_head'])}",
            f"- y = {f(r['y'])} ({r['y_how'] or 'none'});  y/3 = {third}",
            f"- x = {f(r['gap_under'])} + y/3 = **{f(r['x'])}**;  x_headline = {f(r['x_headline'])}"]
    if N:
        out += [f"- S = i - N_mid = {f(r['rate'])} - {f(N['mid'])} = **{f(r['S'])}**  (S_lo = i - N_hi = {f(r['S_lo'])}, S_hi = i - N_lo = {f(r['S_hi'])})",
                f"- G = 1.5 * x - S = 1.5 * {f(r['x'])} - {f(r['S'])} = **{f(r['G'])}**  (at N_lo: {f(r['G_lo'])}, at N_hi: {f(r['G_hi'])})",
                f"- situation (delta {params['situations']['delta_pp']}, epsilon {params['situations']['epsilon_pp']}): **{r['situation']}** "
                f"direction {r['direction']}, distance to the threshold {f(r['margin'])}; at N_lo {r['situation_at_N_lo']}, at N_hi {r['situation_at_N_hi']}",
                f"- pivot = pi* + S/1.5 - y/3 = {f(r['pivot'])}"]
    else:
        out.append("- no neutral: situation null (need_only)")
    out += ["", "**In 12 months, the rate held**", "",
            f"- pi_12m = {f(r['pi_12m'])} (period {r.get('pi_12m_period')}, basis {r.get('pi_12m_basis') or 'n/a'})" + (f" - {r['na_12m']}" if r.get("na_12m") else ""),
            f"- gap_12m = {f(r['gap_12m'])};  y_12m = {f(r['y_12m'])} ({r.get('y_12m_how') or 'none'});  x_12m = **{f(r['x_12m'])}**"]
    if N:
        out.append(f"- G_12m = 1.5 * {f(r['x_12m'])} - {f(r['S'])} = **{f(r['G_12m'])}** -> **{r['situation_12m']}** direction {r['direction_12m']}"
                   f" (at N_lo {f(r.get('G_12m_lo'))} {r.get('situation_12m_at_N_lo')}, at N_hi {f(r.get('G_12m_hi'))} {r.get('situation_12m_at_N_hi')}; "
                   f"distance to the threshold {f(r.get('margin_12m'))}; flags 12 months: {', '.join(r.get('flags_12m') or []) or 'none'})")
    lens = r["lens"]
    lines = [f"kind {lens.get('kind')}"]
    for k in ("year", "bank", "market", "gap_bp", "target_date", "rate_today_curve", "flag_today", "cutoff", "cutoff_flag", "rate_cutoff_curve",
              "flag_cutoff", "assumed_rate", "delta_bp", "flag", "horizon_gap", "horizon_period", "horizon_basis", "horizon_flag", "consistent", "source", "na"):
        if lens.get(k) is not None:
            v = lens[k]
            lines.append(f"{k} {f(v) if isinstance(v, float) else v}")
    cyc = r["cycle"]
    moves = ", ".join(f"{m['date']} {m['bp']:+.0f}" + ("" if m["date_source"] == "decision" else f" [{m['date_source']}]") for m in cyc.get("moves") or [])
    prev = cyc.get("previous")
    prev_t = f"{prev['direction']} x{prev['count']} ({prev['first_move']}..{prev['last_move']})" if prev else (cyc.get("previous_na") or "n/a")
    hist = cyc.get("history") or {}
    mk = r["market"]
    out += ["", f"**Lens (bank vs market):** {'; '.join(lines)}", "",
            f"**Cycle** (history {hist.get('series')} {hist.get('from')}..{hist.get('to')} + decisions.parquet): current stage {cyc.get('direction')} "
            f"x{cyc.get('count')} ({cyc.get('first_move')}..{cyc.get('last_move')}): {moves or 'none'}; holds since the last move: {cyc.get('holds_since')}"
            + (f" (decisions from {cyc['holds_from']})" if cyc.get("holds_from") else "") + f"; previous stage: {prev_t}", "",
            f"**Market:** next meeting {mk['next']['meeting']} step {f(mk['next']['step_bp'], 2)} bp [{mk['next']['flag']}]"
            + (f" ({mk['next']['na']})" if mk['next'].get('na') else "")
            + f"; 12 months: {mk['m12'].get('meeting')} rate {f(mk['m12'].get('rate'))} cum {f(mk['m12'].get('cum_bp'), 2)} bp [{mk['m12'].get('flag')}]"
            + (f" ({mk['m12']['na']})" if mk['m12'].get('na') else ""), "",
            f"**Flags:** {', '.join(r['flags']) or 'none'}"]
    if r["notes"]:
        out.append("**Notes:** " + "; ".join(r["notes"]))
    return "\n".join(out) + "\n"


def summary_table(results: dict) -> str:
    rows = ["| bank | i | N mid (lo..hi) | S | x | x_headline | y | G (lo..hi) | today | 12 months (basis) | pivot | flags | flags 12 months |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c, r in results.items():
        N = r["N"]
        rows.append(f"| {c} | {f(r['rate'], 3)} | {('%s (%s..%s)' % (f(N['mid'], 3), f(N['lo'], 3), f(N['hi'], 3))) if N else 'n/a'} | {f(r['S'], 3)} | "
                    f"{f(r['x'], 3)} | {f(r['x_headline'], 3)} | {f(r['y'], 3)} | "
                    f"{('%s (%s..%s)' % (f(r['G'], 3), f(r['G_lo'], 3), f(r['G_hi'], 3))) if r['G'] is not None else 'n/a'} | {r['situation'] or 'n/a'} | "
                    f"{r['situation_12m'] or 'n/a'} ({r.get('pi_12m_basis') or 'n/a'}) | {f(r['pivot'], 3)} | {', '.join(r['flags'])} | {', '.join(r.get('flags_12m') or [])} |")
    return "\n".join(rows) + "\n"


def render(results: dict, params: dict, asof) -> str:
    head = f"## Stance report - as-of {asof} (cb_stance v{params['version']})\n\n"
    return head + summary_table(results) + "\n" + "\n".join(bank_block(r, params) for r in results.values())
