"""Text report of phase 1B-2 (pure rendering): one block per bank (trajectory, next meeting, year ends, GAP, repricing,
surprises and reaction, cross-checks) and the table of currency pairs."""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from .analysis import BankReport, PairRow, bank_report, pair_row
from .engine import Context, Point, YEAR_ENDS

NA = "n/a"
KIND_TEXT = {"bkbm": "BKBM level, not the OCR", "sovereign_proxy": "sovereign proxy, not policy-equivalent"}
ORDER = ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF")


def bp(x: Optional[float], digits: int = 1, sign: bool = True) -> str:
    return NA if x is None else f"{x:+.{digits}f}" if sign else f"{x:.{digits}f}"


def pct(x: Optional[float], digits: int = 3) -> str:
    return NA if x is None else f"{x:.{digits}f}"


def dt(d: Optional[date]) -> str:
    return NA if d is None else d.isoformat()


def rng(w: Optional[tuple]) -> str:
    return "" if not w else f"{w[0]:%m-%d}..{w[1]:%m-%d}"


def table(rows: list, header: list, indent: str = "  ") -> list:
    rows = [[str(c) for c in r] for r in rows]
    width = [max(len(str(h)), *(len(r[i]) for r in rows)) if rows else len(str(h)) for i, h in enumerate(header)]
    line = lambda r: indent + "  ".join(c.ljust(w) for c, w in zip(r, width)).rstrip()            # noqa: E731
    return [line(header), indent + "  ".join("-" * w for w in width)] + [line(r) for r in rows]


def short(reason: str) -> str:
    """The headline of a long reason (the full text stays in the note line and in the JSON)."""
    if reason.startswith("proxy without short end"):
        return reason.split(":", 1)[0]
    if reason.startswith("history starts"):
        return reason.split(" (", 1)[0]
    return reason


def probs(nm) -> str:
    if nm.probabilities is None:
        return f"{NA} ({short(nm.prob_reason)})"
    d, mv = nm.probabilities["direction"], nm.probabilities["moves"]
    return d + ": " + ", ".join(f"{k} move{'s' if k != 1 else ''} {v * 100:.1f}%" for k, v in mv.items())


def point_row(p: Point) -> list:
    where = f"{p.eff:%Y-%m-%d}" if p.window is None or p.method in ("CURVE", "PROXY_CURVE") else f"{p.eff:%m-%d} -> {rng(p.window)}"
    if p.method in ("CURVE", "PROXY_CURVE") and p.window:
        where = f"{p.eff:%Y-%m-%d} [avg {rng(p.window)})"
    extra = []
    if p.method == "WINDOW" and p.rate is not None:
        e = p.extra
        extra.append(f"interior={e.get('interior')} pre={e.get('pre_days')}d gap={e.get('gap_days'):+d}d")
    if p.method == "EXACT" and p.extra.get("how") == "next_month":
        extra.append("next-month avg")
    if p.rate is None and p.level is not None:
        extra.append(KIND_TEXT.get(p.level_kind, p.level_kind))
    if p.rate is None:
        extra.append(short(p.reason))
    elif p.step_bp is None and p.method != "WINDOW":
        extra.append("step " + NA + ": " + short(p.extra.get("step_reason") or ""))
    extra += [n for n in p.notes if p.method in ("CURVE", "PROXY_CURVE")]
    rate_s = pct(p.rate) if p.rate is not None else (f"[{pct(p.level)}]" if p.level is not None else NA)      # [x] = a level, not a policy rate
    return [dt(p.meeting), where, rate_s, bp(p.cum_bp), bp(p.step_bp) if p.step_bp is not None else NA, p.flag,
            f"{p.source} {dt(p.source_asof)}", "STALE" if p.stale else "", "; ".join(x for x in extra if x)]


def bank_block(r: BankReport, points: int = 8) -> list:
    tr = r.trajectory
    out = [f"=== {r.currency} - as of {r.asof}"]
    if tr.na_reason:
        out.append(f"  n/a: {tr.na_reason}")
    if tr.base:
        b = tr.base
        note = f", effective {b.eff}{' (decided, not in force yet)' if b.pending else ''}" if b.eff else ""
        out.append(f"  base {b.rate:g}% (decision {dt(b.meeting)}{note}){' - ' + b.note if b.note else ''}")
    if tr.spread and tr.spread.value is not None:
        s = tr.spread
        out.append(f"  spread {s.benchmark} - {s.policy}: {s.bp:+.2f} bp  median of n={s.n} of the 20 business days {s.start}..{s.end} ({len(s.excluded)} excluded)")
    elif tr.spread:
        out.append(f"  spread {NA}: {tr.spread.reason}")
    if tr.points:
        out.append("  trajectory (implied policy rate; cum = vs base; step = at the meeting, per-meeting methods only)")
        out += table([point_row(p) for p in tr.points[:points]],
                     ["meeting", "effective / window", "rate %", "cum bp", "step bp", "flag", "source (as-of)", "", "notes"], indent="    ")
        if any(p.rate is None and p.level is not None for p in tr.points):
            out.append("    [x] = a level that is not a policy-equivalent rate (see notes); n/a = not available")
        if len(tr.points) > points:
            out.append(f"    ... {len(tr.points) - points} more meetings")
    for n in tr.notes:
        out.append(f"  note: {n}")
    for c in tr.consistency:
        out.append(f"  check {c[0]} {c[1]}: {c[2]:+.1f} bp ({c[3]})")

    nm = r.next
    out.append(f"  next meeting: {dt(nm.decision)} (effective {dt(nm.eff)})  step: " +
               (f"{bp(nm.step_bp)} bp [{nm.flag}]{' STALE' if nm.stale else ''}" if nm.step_bp is not None else f"{NA} ({short(nm.step_reason)})"))
    out.append(f"    probability: {probs(nm)}")
    for y in YEAR_ENDS:
        ye = r.year_ends[y]
        head = f"  last meeting {y}: {dt(ye.meeting)}  "
        if ye.cum_bp is not None:
            e = ye.extra
            det = ""
            if ye.flag == "UPPER_BOUND":
                det = f"  window {rng(ye.window)} interior={e.get('interior')} pre={e.get('pre_days')}d"
            elif ye.window:
                det = f"  avg {rng(ye.window)}"
            out.append(f"{head}cum {bp(ye.cum_bp)} bp -> {pct(ye.rate)}% [{ye.flag}]{' STALE' if ye.stale else ''}{det}")
        elif ye.flag == "DECIDED":
            out.append(f"{head}decided: part of the base ({ye.reason})")
        else:
            extra = f"; level {pct(ye.level)}% ({KIND_TEXT.get(ye.level_kind, ye.level_kind)}) [{ye.flag}]" if ye.level is not None else ""
            out.append(f"{head}cum {NA} ({short(ye.reason)}){extra}")

    g = r.gap
    out.append("  GAP = market - bank: " + (f"{NA} ({g.reason})" if g.kind == "n/a" or (not g.years and g.reason) else f"{g.kind}, SEP/MPS {dt(g.sep)}"))
    for gy in g.years:
        if g.kind == "dots":
            dots = " ".join(f"{lvl:g}x{n}" for lvl, n in gy.dots)
            out.append(f"    {gy.year}: dots median {pct(gy.bank_median, 3)} (n={gy.n_dots}) | market {pct(gy.market_rate)} [{gy.market_flag or NA}] {rng(gy.market_window)}"
                       f" | GAP {bp(gy.gap_bp)} bp{' - ' + gy.note if gy.note else ''}")
            if dots:
                out.append(f"          distribution {dots}")
        else:
            out.append(f"    {gy.note}: MPS {pct(gy.bank_median, 2)} | ASX BB {pct(gy.market_rate)} [{gy.market_flag or NA}] {rng(gy.market_window)} | GAP {bp(gy.gap_bp)} bp")

    bpth = r.bank_path
    if bpth and bpth.kind == "ocr_track":
        out.append(f"  bank path ({bpth.note}; MPS {dt(bpth.source)}, projections finalised {dt(bpth.finalised)})")
        out.append("    " + "  ".join(f"{lbl} {v:g}" for lbl, v in bpth.points))

    for name in ("1s", "1l"):
        rp = r.repricing.get(name)
        if rp is None:
            continue
        head = f"  repricing {name} ({rp.bd} bd" + (f", vs {rp.prev}" if rp.prev else "") + "): "
        if "all" in rp.reasons:
            out.append(head + f"{NA} ({rp.reasons['all']})")
            continue
        parts = [f"level {y} {bp(rp.level[y])} bp [{rp.level_flag.get(y)}]" if y in rp.level else f"level {y} {NA} ({short(rp.reasons.get(str(y), ''))})" for y in YEAR_ENDS]
        parts.append(f"next step ({dt(rp.step_meeting)}) {bp(rp.step_bp)} bp [{rp.step_flag}]" if rp.step_bp is not None else f"next step {NA} ({short(rp.reasons.get('step', ''))})")
        out.append(head + "; ".join(parts))
        sec = ", ".join(f"{y} {bp(rp.cum[y])}" for y in YEAR_ENDS if y in rp.cum)
        if sec:
            out.append(f"      secondary - change of the cumulative bp (vs the base at each date): {sec}"
                       + (f"; the base moved {bp(rp.base_change_bp)} bp in between (a decision)" if rp.base_change_bp else ""))

    if r.surprises:
        out.append("  surprises and reaction (last 4 decisions + upcoming)")
        rows = []
        for s in r.surprises:
            if s.decided:
                vm = f"{bp(s.vs_market_bp)} [{s.vs_market_flag}] (T-1 implied {bp(s.implied_step_bp)})" if s.vs_market_bp is not None else f"{NA}: {short(s.vs_market_reason)}"
                rn = f"{bp(s.reaction_next_bp)} -> {s.reaction_next_target:%m-%d} [{s.reaction_next_flag}]" if s.reaction_next_bp is not None else f"{NA}: {s.reaction_reason.get('next', '')}"
                ry = f"{bp(s.reaction_year_bp)} -> {s.reaction_year_target:%m-%d} [{s.reaction_year_flag}]" if s.reaction_year_bp is not None else f"{NA}: {s.reaction_reason.get('year', '')}"
                rows.append([dt(s.meeting), bp(s.delta_bp), bp(s.vs_consensus_bp), vm, rn, ry])
            else:
                st = f"{bp(s.implied_step_bp)} [{s.vs_market_flag}]" if s.implied_step_bp is not None else f"{NA}: {short(s.vs_market_reason)}"
                rows.append([dt(s.meeting) + " (upcoming)", "-", "-", f"priced now {st}", "-", "-"])
        out += table(rows, ["meeting", "decided bp", "vs consensus", "vs market T-1", "reaction next meeting", "reaction last meeting of year"], indent="    ")

    if r.crosschecks:
        out.append("  cross-checks")
        out += table([[c.name, c.period, f"{c.a_label} {c.a:.3f}" if c.a is not None else c.a_label,
                       f"{c.b_label} {c.b:.3f}" if c.b is not None else c.b_label, f"{c.diff_bp:+.1f} bp" if c.diff_bp is not None else NA, c.note or c.na_reason]
                      for c in r.crosschecks], ["check", "period", "primary", "second source", "diff", "note"], indent="    ")
    return out


def pairs_table(pairs: list) -> list:
    rows = []
    for p in pairs:
        cell = lambda y: (f"{p.cum_bp[y]:+.1f} ({p.implied[y] * 100:+.1f}) [{p.implied_flag[y]}]" if y in p.implied else NA)     # noqa: E731
        rep = lambda n, y: (f"{p.reprice[(n, y)]:+.1f} [{p.reprice_flag.get((n, y))}]" if (n, y) in p.reprice else NA)              # noqa: E731
        rows.append([p.pair, bp(p.current_bp) if p.current_bp is not None else NA, cell(2026), cell(2027),
                     rep("1s", 2026), rep("1s", 2027), rep("1l", 2026), rep("1l", 2027)])
    out = ["=== Pairs: base - quote (bp), each metric n/a on its own. cum = differential of the cumulative bp; (in parentheses) implied rate differential",
           "    at the last meeting of the year (both legs policy-equivalent); flag = the weaker of the two legs;",
           "    repricing = change of the implied differential over 5 / 21 business days (needs a change of level on both legs)"]
    out += table(rows, ["pair", "now bp", "2026 cum (diff)", "2027 cum (diff)", "1s 2026", "1s 2027", "1l 2026", "1l 2027"], indent="  ")
    why: dict = {}                                                    # (currency, headline) -> pairs
    for p in pairs:
        for reasons in p.reasons.values():
            for cur, reason in reasons:
                why.setdefault((cur, short(reason)), set()).add(p.pair)
    for (cur, reason), names in sorted(why.items()):
        out.append(f"  n/a - {cur}: {reason}  ->  {', '.join(sorted(names))}")
    return out


def build(ctx: Context, asof: date, pair_defs: Iterable[tuple], currencies: Iterable[str] = ORDER, points: int = 8) -> tuple:
    """(bank reports by currency, pair rows) at `asof`; `pair_defs` = [(pair, base, quote)]."""
    reports = {c: bank_report(ctx, c, asof) for c in currencies if c in ctx.banks}
    rows = [pair_row(name, reports[b], reports[q]) for name, b, q in pair_defs if b in reports and q in reports]
    return reports, rows


def render(reports: dict, pairs: list, asof: date, points: int = 8) -> str:
    lines = [f"Central-banks implied policy paths - as of {asof}", "flags: EXACT > CURVE > UPPER_BOUND (3M windows) > PROXY (government curves / bills); n/a always carries its reason", ""]
    for c in ORDER:
        if c in reports:
            lines += bank_block(reports[c], points) + [""]
    lines += pairs_table(pairs)
    return "\n".join(lines) + "\n"
