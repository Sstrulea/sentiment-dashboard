"""Automated checks of the stance UI (S2) on a rendered site + the screenshots of reports/stance-ui/.

    python scripts/check_stance_ui.py <public_dir> [--port 8766] [--shots reports/stance-ui] [--baseline <public_dir_before>]

Serves <public_dir> (Playwright + Chromium, the repo's .venv) and checks:
  * /central-banks#stance: 8 table rows (desktop) / 8 cards (phone); one map dot per bank WITH a neutral, the need-only banks in the
    note under the map; no horizontal scroll at 390 px;
  * every rendered stance number (span[data-f]) = its payload value (data-k in the bank's stance block) formatted by the B.4 rule;
  * "Show calculation" on each bank page: the same check on every number of the arithmetic;
  * zero console errors on every page opened; the FRED footer present;
  * with --baseline: the other CB pages and /economic keep their DOM outside the stance (Compare, Pairs, a pair page, /economic).
Exit 1 on any failure.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.cb_compute.stance_ui import MINUS  # noqa: E402

BANKS = ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF")


socketserver.TCPServer.allow_reuse_address = True


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(public: Path, port: int):
    httpd = socketserver.TCPServer(("127.0.0.1", port), functools.partial(_Quiet, directory=str(public)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# ---- B.4, the same rule as cb.js (independent implementation) ----
def _signed(v: float, nd: int) -> str:
    s = f"{abs(v):.{nd}f}"
    return s if float(s) == 0 else ("+" if v > 0 else MINUS) + s


def fmt(f: str, v: float) -> str:
    if f == "pp":
        return _signed(v, 2)
    if f in ("bp", "bp100"):
        x = v * 100 if f == "bp100" else v
        n = int(abs(x) + 0.5)
        return "0" if n == 0 else ("+" if x > 0 else MINUS) + str(n)
    if f == "lvl":
        return f"{v:.2f}"
    if f == "pub":
        r = repr(float(v))
        return r[:-2] if r.endswith(".0") else r
    raise ValueError(f)


def at(obj, key: str):
    for k in key.split("."):
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj


NUMS_JS = """(sel) => Array.from(document.querySelectorAll(sel)).map(e => {
  const h = e.closest('[data-ccy]');
  return {k: e.dataset.k || null, f: e.dataset.f, v: Number(e.dataset.v), text: e.textContent, ccy: h ? h.dataset.ccy : null};
})"""


def check_numbers(items: list, stance: dict, page_ccy: str | None, where: str, fails: list) -> int:
    n = 0
    for it in items:
        n += 1
        want = fmt(it["f"], it["v"])
        if it["text"] != want:
            fails.append(f"{where}: text {it['text']!r} != {want!r} ({it['f']} of {it['v']})")
        if it["k"]:
            ccy = it["ccy"] or page_ccy
            pv = at(stance.get(ccy) or {}, it["k"])
            if pv is None or abs(float(pv) - it["v"]) > 1e-9:
                fails.append(f"{where} {ccy} {it['k']}: rendered value {it['v']} != payload {pv}")
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("public", type=Path)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--shots", type=Path, default=ROOT / "reports" / "stance-ui")
    ap.add_argument("--baseline", type=Path, default=None)
    a = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright
    ov = json.loads((a.public / "data" / "cb" / "overview.json").read_text())
    st = ov["stance"]["banks"]
    full = {c: json.loads((a.public / "data" / "cb" / f"{c.lower()}.json").read_text())["stance"] for c in BANKS}
    with_n = [c for c in BANKS if st[c]["stance"]["v"] is not None]
    need_only = [c for c in BANKS if c not in with_n]
    a.shots.mkdir(parents=True, exist_ok=True)
    httpd = serve(a.public, a.port)
    base = f"http://127.0.0.1:{a.port}"
    fails, counts, shots = [], {}, []
    errors: list = []
    with sync_playwright() as p:
        br = p.chromium.launch()

        def page(w, h, phone=False):
            ctx = br.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, is_mobile=phone, has_touch=phone,
                                 color_scheme="dark")
            pg = ctx.new_page()
            pg.on("console", lambda m: m.type == "error" and errors.append(f"{pg.url}: {m.text}"))
            pg.on("pageerror", lambda e: errors.append(f"{pg.url}: {e}"))
            return pg

        def footer(pg, where):
            if not pg.query_selector("footer.fred-notice"):
                fails.append(f"{where}: FRED footer missing")

        # ---- overview, tab Stance ----
        for vw, (w, h, phone) in {"desktop": (1440, 900, False), "phone": (390, 844, True)}.items():
            pg = page(w, h, phone)
            pg.goto(f"{base}/central-banks.html#stance")
            pg.wait_for_selector("#cbStance:not([hidden]) canvas#cbStanceMap", timeout=15000)
            pg.wait_for_timeout(400)
            where = f"overview#stance {vw}"
            if not phone:
                rows = pg.eval_on_selector_all("#cbStanceTable tbody tr", "rs => rs.map(r => r.dataset.ccy)")
                if sorted(rows) != sorted(BANKS):
                    fails.append(f"{where}: table rows {rows}")
                vis = "#cbStanceTableBox"
            else:
                cards = pg.eval_on_selector_all("#cbStanceListBox .cb-st-item", "rs => rs.map(r => r.dataset.ccy)")
                if sorted(cards) != sorted(BANKS):
                    fails.append(f"{where}: cards {cards}")
                if pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"):
                    fails.append(f"{where}: horizontal scroll ({pg.evaluate('document.documentElement.scrollWidth')} px)")
                vis = "#cbStanceListBox"
            note = pg.inner_text(".cb-st-note") if pg.query_selector(".cb-st-note") else ""
            for c in need_only:
                if f"{c} —" not in note:
                    fails.append(f"{where}: need-only {c} not in the note under the map")
            dots = pg.evaluate("""() => { const ch = Chart.getChart(document.getElementById('cbStanceMap'));
                                           return ch.data.datasets.filter(d => d.$cbToday).map(d => d.$cbToday); }""")
            if sorted(dots) != sorted(with_n):
                fails.append(f"{where}: map dots {dots} != banks with a neutral {with_n}")
            counts[where] = check_numbers(pg.evaluate(NUMS_JS, f"{vis} [data-f], .cb-st-note [data-f]"), st, None, where, fails)
            footer(pg, where)
            out = a.shots / f"central-banks-stance-{vw}-{w}.png"
            pg.screenshot(path=str(out), full_page=True)
            shots.append(out)
            if not phone:                                                   # a hover card on the map (USD)
                pg.evaluate("document.getElementById('cbStanceMap').scrollIntoView({block: 'center'})")
                pg.wait_for_timeout(200)
                box = pg.eval_on_selector("#cbStanceMap", "c => { const ch = Chart.getChart(c); const d = ch.data.datasets.find(x => x.$cbToday === 'USD'); "
                                                         "const p = d.data[0]; const r = c.getBoundingClientRect(); "
                                                         "return {x: r.left + ch.scales.x.getPixelForValue(p.x), y: r.top + ch.scales.y.getPixelForValue(p.y)}; }")
                pg.mouse.move(box["x"], box["y"])
                pg.wait_for_timeout(200)
                tt = pg.query_selector("#cbStance .cb-tt:not([hidden])")
                if not tt:
                    fails.append(f"{where}: no hover card on the USD dot")
                else:
                    counts[where + " hover"] = check_numbers(pg.evaluate(NUMS_JS, "#cbStance .cb-tt [data-f]"), {"USD": st["USD"]}, "USD", where + " hover", fails)
                    out = a.shots / "central-banks-stance-hover-desktop-1440.png"
                    pg.screenshot(path=str(out), full_page=False)
                    shots.append(out)
            pg.context.close()

        # ---- bank pages: zone "Stance and need" ----
        for c in BANKS:
            for vw, (w, h, phone) in {"desktop": (1440, 900, False), "phone": (390, 844, True)}.items():
                if phone and c not in ("USD", "CHF"):
                    continue
                pg = page(w, h, phone)
                pg.goto(f"{base}/central-banks/{c.lower()}.html")
                pg.wait_for_selector("#cbStanceZone", timeout=15000)
                pg.wait_for_timeout(400)
                where = f"{c} {vw}"
                pg.evaluate("document.getElementById('cbStanceCalc').open = true")
                counts[where] = check_numbers(pg.evaluate(NUMS_JS, ".cb-st-zone [data-f]"), full, c, where, fails)
                calc = pg.inner_text("#cbStanceCalc")
                for must in ("need x = gap + y/3",) + (("stance S = i − N", "pressure G = 1.5 ×") if c in with_n else ("neutral: n/a",)):
                    if must not in calc:
                        fails.append(f"{where}: Show calculation lacks {must!r}")
                if c == "USD" and "midpoint of the target range 3.75–4.00%" not in calc:
                    fails.append(f"{where}: USD midpoint not explained in the calculation")
                hist = full[c]["history"]
                if len(hist) < 5 and f"History starts on" not in pg.inner_text(".cb-st-zone"):
                    fails.append(f"{where}: the 'History starts on' text is missing")
                if phone and pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"):
                    fails.append(f"{where}: horizontal scroll")
                footer(pg, where)
                pg.evaluate("window.scrollTo(0, 0)")
                out = a.shots / f"{c.lower()}-{vw}-{w}.png"
                pg.screenshot(path=str(out), full_page=True)
                shots.append(out)
                pg.context.close()

        # ---- the other pages: no console error, and (with --baseline) the same DOM outside the stance ----
        others = ["central-banks.html", "central-banks.html#pairs", "central-banks/pair/eurusd.html", "economic.html"]
        for rel in others:
            pg = page(1440, 900)
            pg.goto(f"{base}/{rel}")
            pg.wait_for_timeout(1500)
            footer(pg, rel)
            pg.context.close()
        if a.baseline:
            hb = serve(a.baseline, a.port + 1)
            for rel, sel in (("central-banks.html", "#cbCompare"), ("central-banks.html#pairs", "#cbPairs"),
                             ("central-banks/pair/eurusd.html", "#cbRoot"), ("economic.html", "main")):
                doms = []
                for b in (base, f"http://127.0.0.1:{a.port + 1}"):
                    pg = page(1440, 900)
                    pg.goto(f"{b}/{rel}")
                    pg.wait_for_timeout(1500)
                    doms.append(pg.evaluate(f"""() => {{ const e = document.querySelector('{sel}'); if (!e) return '';
                        const c = e.cloneNode(true); c.querySelectorAll('canvas, .cb-cd, .cb-bankbar').forEach(x => x.remove()); return c.innerText; }}"""))
                    pg.context.close()
                if doms[0] != doms[1]:
                    fails.append(f"{rel} {sel}: DOM text differs from the baseline")
            hb.shutdown()
        br.close()
    httpd.shutdown()
    fails += [f"console: {e}" for e in errors]
    print(json.dumps({"numbers_checked": counts, "screenshots": [s.name for s in shots], "failures": fails}, indent=1, ensure_ascii=False))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
