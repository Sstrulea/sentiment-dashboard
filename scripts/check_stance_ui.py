"""Automated checks of the stance UI (S2, S2.1) on a rendered site, in BOTH themes, + the screenshots of reports/stance-ui/.

    python scripts/check_stance_ui.py <public_dir> [--port 8766] [--shots reports/stance-ui] [--baseline <public_dir_before>]

Serves <public_dir> (Playwright + Chromium, the repo's .venv); for dark and light:
  * /central-banks#stance: 8 table rows (1440) / 8 cards (390); one map dot per bank WITH a neutral; the need-only banks in the
    note under the map; the map labels overlap neither each other nor a dot and stay in the plot; the canvas has an aria-label and
    a text alternative with one line per dot; no horizontal scroll at 390 px; the table is not clipped at 1440 px and no flag row
    overflows its cell; the 3 sortings give the payload order; the hover card of every dot stays in the viewport and in the chart;
  * every rendered stance number (span[data-f]) = its payload value (data-k in the bank's stance block) formatted by B.4;
  * each bank page: the zone after the trajectory grid and before "The bank"; the summary in the order Market -> Bank -> Rule;
    "Show calculation" = the same number check + its formulas; Next test only with a date; History text under 5 snapshots;
  * text contrast in the stance areas (WCAG ratio of every text node against its effective background);
  * zero console errors on every page opened; the FRED footer present;
  * with --baseline: the other CB pages and /economic keep their DOM outside the stance.
Exit 1 on any failure. The JSON printed lists every check with its result and the screenshot it relates to.
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
THEMES = ("dark", "light")
MIN_CONTRAST = 4.5

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


def sort_order(st: dict, key: str) -> list:
    def val(c):
        s = st[c]
        return s["pressure"]["v"] if key == "pressure" else s["lens"]["v"] if key == "lens" else s.get("m12_bp")
    have = sorted((c for c in BANKS if val(c) is not None), key=lambda c: (-val(c), BANKS.index(c)))
    return have + [c for c in BANKS if val(c) is None]


NUMS_JS = """(sel) => Array.from(document.querySelectorAll(sel)).map(e => {
  const h = e.closest('[data-ccy]');
  return {k: e.dataset.k || null, f: e.dataset.f, v: Number(e.dataset.v), text: e.textContent, ccy: h ? h.dataset.ccy : null};
})"""

CONTRAST_JS = """(sel) => {
  const lum = (c) => { const m = c.match(/[\\d.]+/g).map(Number); const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
                       return [0.2126 * f(m[0]) + 0.7152 * f(m[1]) + 0.0722 * f(m[2]), m.length > 3 ? m[3] : 1]; };
  const bg = (el) => { for (let e = el; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor; const l = lum(c); if (l[1] > 0.5) return c; }
                       return getComputedStyle(document.body).backgroundColor; };
  const out = [];
  document.querySelectorAll(sel).forEach((root) => {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (!n.textContent.trim()) continue;
      const el = n.parentElement, cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || el.closest('[hidden], .cb-sr-only') || !el.getClientRects().length) continue;
      if (el.closest('details:not([open])') && !el.closest('summary')) continue;
      const a = lum(cs.color)[0], b = lum(bg(el))[0];
      const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      out.push({text: n.textContent.trim().slice(0, 40), ratio: Math.round(ratio * 100) / 100, cls: String(el.className || el.tagName)});
    }
  });
  return out;
}"""

LABELS_JS = """() => { const ch = Chart.getChart(document.getElementById('cbStanceMap')); const ca = ch.chartArea;
  return {labels: ch.$cbLabels || [], dots: ch.$cbDots || [], area: {l: ca.left, r: ca.right, t: ca.top, b: ca.bottom},
          today: ch.data.datasets.filter(d => d.$cbToday).map(d => d.$cbToday)}; }"""


def check_numbers(items: list, stance: dict, page_ccy, where: str, fails: list) -> int:
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
    fails, counts, shots, checklist, errors, contrast = [], {}, [], [], [], {}

    def item(point: str, ok: bool, shot: str, detail: str = ""):
        checklist.append({"point": point, "ok": bool(ok), "screenshot": shot, "detail": detail})
        if not ok:
            fails.append(f"{point}: {detail}")

    def hit(r, o):
        return r["x"] < o["x"] + o["w"] and o["x"] < r["x"] + r["w"] and r["y"] < o["y"] + o["h"] and o["y"] < r["y"] + r["h"]

    with sync_playwright() as p:
        br = p.chromium.launch()

        def page(w, h, phone, theme):
            ctx = br.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, is_mobile=phone, has_touch=phone, color_scheme=theme)
            pg = ctx.new_page()
            pg.on("console", lambda m: m.type == "error" and errors.append(f"{pg.url}: {m.text}"))
            pg.on("pageerror", lambda e: errors.append(f"{pg.url}: {e}"))
            return pg

        def low_contrast(pg, sel, where):
            res = pg.evaluate(CONTRAST_JS, sel)
            bad = [r for r in res if r["ratio"] < MIN_CONTRAST]
            contrast[where] = {"texts": len(res), "min": min((r["ratio"] for r in res), default=None), "below": bad[:6]}
            return bad

        for theme in THEMES:
            # ---- overview, tab Stance ----
            for vw, (w, h, phone) in {"desktop": (1440, 900, False), "phone": (390, 844, True)}.items():
                pg = page(w, h, phone, theme)
                pg.goto(f"{base}/central-banks.html#stance")
                pg.wait_for_selector("#cbStance:not([hidden]) canvas#cbStanceMap", timeout=15000)
                pg.wait_for_timeout(500)
                where = f"overview#stance {theme} {vw}"
                shot = f"central-banks-stance-{theme}-{vw}-{w}.png"
                if not phone:
                    rows = pg.eval_on_selector_all("#cbStanceTable tbody tr", "rs => rs.map(r => r.dataset.ccy)")
                    item(f"2 table 8 rows [{where}]", sorted(rows) == sorted(BANKS), shot, str(rows))
                    clip = pg.evaluate("""() => { const t = document.getElementById('cbStanceTable'), w = t.parentElement;
                        const flags = Array.from(t.querySelectorAll('.cb-st-flags')).filter(f => f.scrollWidth > f.clientWidth + 1 || f.getBoundingClientRect().right > f.closest('td').getBoundingClientRect().right + 1).length;
                        return {table: t.scrollWidth, box: w.clientWidth, right: t.getBoundingClientRect().right, vw: document.documentElement.clientWidth, flags: flags}; }""")
                    item(f"2 table not clipped at 1440 [{where}]", clip["table"] <= clip["box"] + 1 and clip["right"] <= clip["vw"], shot, json.dumps(clip))
                    item(f"2 chips wrap inside their cell [{where}]", clip["flags"] == 0, shot, f"{clip['flags']} overflowing flag rows")
                    for key in ("lens", "pressure", "m12"):
                        pg.click(f'#cbStanceTableBox .cb-pill[data-sort="{key}"]')
                        got = pg.eval_on_selector_all("#cbStanceTable tbody tr", "rs => rs.map(r => r.dataset.ccy)")
                        item(f"2 sort {key} [{where}]", got == sort_order(st, key), shot, f"{got} vs {sort_order(st, key)}")
                    vis = "#cbStanceTableBox"
                else:
                    cards = pg.eval_on_selector_all("#cbStanceListBox .cb-st-item", "rs => rs.map(r => r.dataset.ccy)")
                    item(f"5 phone 8 cards [{where}]", sorted(cards) == sorted(BANKS), shot, str(cards))
                    vis = "#cbStanceListBox"
                sw = pg.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
                item(f"5 no horizontal scroll [{where}]", sw[0] <= sw[1] + 1, shot, str(sw))
                note = pg.inner_text(".cb-st-note") if pg.query_selector(".cb-st-note") else ""
                item(f"1 need-only note visible [{where}]", all(f"{c} —" in note for c in need_only) and pg.is_visible(".cb-st-note"), shot, note[:80])
                L = pg.evaluate(LABELS_JS)
                item(f"1 map dots = banks with a neutral [{where}]", sorted(L["today"]) == sorted(with_n), shot, str(L["today"]))
                lab = L["labels"]
                over = [(x["ccy"], y["ccy"]) for i, x in enumerate(lab) for y in lab[i + 1:] if hit(x, y)]
                on_dot = [x["ccy"] for x in lab for d in L["dots"] if hit(x, {"x": d["x"] - 6, "y": d["y"] - 6, "w": 12, "h": 12})]
                outside = [x["ccy"] for x in lab if x["x"] < L["area"]["l"] or x["x"] + x["w"] > L["area"]["r"] or x["y"] < L["area"]["t"] or x["y"] + x["h"] > L["area"]["b"]]
                item(f"1 map labels: no overlap, not on a dot, inside [{where}]", not over and not on_dot and not outside and len(lab) == len(with_n), shot,
                     f"overlap {over}, on a dot {on_dot}, outside {outside}")
                alt = pg.eval_on_selector_all("#cbStanceMapAlt li", "ls => ls.map(l => l.textContent)")
                lbl = pg.get_attribute("#cbStanceMap", "aria-label") or ""
                item(f"A4 map aria-label + text alternative [{where}]", bool(lbl) and len(alt) == len(with_n) and all("need" in t and "stance" in t and "situation" in t for t in alt),
                     shot, (alt[:1] or [""])[0])
                counts[where] = check_numbers(pg.evaluate(NUMS_JS, f"{vis} [data-f], .cb-st-note [data-f]"), st, None, where, fails)
                bad = low_contrast(pg, f"{vis}, .cb-st-note, .cb-st-intro", where)
                item(f"6 contrast >= {MIN_CONTRAST} [{where}]", not bad, shot, json.dumps(bad[:3], ensure_ascii=False))
                item(f"footer [{where}]", bool(pg.query_selector("footer.fred-notice")), shot)
                pg.screenshot(path=str(a.shots / shot), full_page=True)
                shots.append(shot)
                # hover card of every dot: inside the viewport and the chart box
                pg.evaluate("document.getElementById('cbStanceMap').scrollIntoView({block: 'center'})")
                pg.wait_for_timeout(200)
                bad_tt = []
                hs = f"central-banks-stance-hover-{theme}-{vw}-{w}.png"
                for c in with_n:
                    pos = pg.eval_on_selector("#cbStanceMap", "(cv, c) => { const ch = Chart.getChart(cv); const d = ch.data.datasets.find(x => x.$cbToday === c); "
                                              "const r = cv.getBoundingClientRect(); return {x: r.left + ch.scales.x.getPixelForValue(d.data[0].x), y: r.top + ch.scales.y.getPixelForValue(d.data[0].y)}; }", c)
                    if phone:
                        pg.touchscreen.tap(pos["x"], pos["y"])
                    else:
                        pg.mouse.move(pos["x"], pos["y"])
                    pg.wait_for_timeout(120)
                    r = pg.evaluate("""() => { const t = document.querySelector('#cbStance .cb-tt:not([hidden])'); if (!t) return null;
                        const a = t.getBoundingClientRect(), w = t.parentElement.getBoundingClientRect();
                        return {l: a.left, r: a.right, t: a.top, b: a.bottom, vw: innerWidth, vh: innerHeight, wl: w.left, wr: w.right, wt: w.top, wb: w.bottom}; }""")
                    if r is None or r["l"] < 0 or r["r"] > r["vw"] or r["t"] < 0 or r["b"] > r["vh"] or r["l"] < r["wl"] - 1 or r["r"] > r["wr"] + 1:
                        bad_tt.append((c, r))
                    if c == "USD":
                        counts[where + " hover"] = check_numbers(pg.evaluate(NUMS_JS, "#cbStance .cb-tt [data-f]"), {"USD": st["USD"]}, "USD", where + " hover", fails)
                        pg.screenshot(path=str(a.shots / hs))
                        shots.append(hs)
                item(f"3 hover card in the viewport, every dot [{where}]", not bad_tt, hs, str(bad_tt[:2]))
                pg.context.close()

            # ---- bank pages ----
            for c in BANKS:
                for vw, (w, h, phone) in {"desktop": (1440, 900, False), "phone": (390, 844, True)}.items():
                    if phone and c not in ("USD", "CHF"):
                        continue
                    pg = page(w, h, phone, theme)
                    pg.goto(f"{base}/central-banks/{c.lower()}.html")
                    pg.wait_for_selector("#cbStanceZone", timeout=15000)
                    pg.wait_for_timeout(400)
                    where = f"{c} {theme} {vw}"
                    shot = f"{c.lower()}-{theme}-{vw}-{w}.png"
                    order = pg.evaluate("""() => { const all = Array.from(document.querySelectorAll('#cbRoot *'));
                        const z = document.getElementById('cbStanceZone'), g = document.querySelector('.cb-pathgrid');
                        const b = Array.from(document.querySelectorAll('h2.cb-zone')).find(h => h.textContent === 'The bank');
                        return [all.indexOf(g), all.indexOf(z), b ? all.indexOf(b) : 1e9]; }""")
                    item(f"4 zone below the trajectory, above 'The bank' [{where}]", order[0] < order[1] < order[2], shot, str(order))
                    summ = pg.inner_text(".cb-st-summary")
                    want = list(full[c]["summary"])
                    pos = [summ.find(x) for x in want]
                    kinds = ["market" if x.startswith("The market prices") else "rule" if x.startswith(("On today's data", "If the rate is held")) else "bank" for x in want]
                    item(f"4 summary Market -> Bank -> Rule [{where}]", all(x >= 0 for x in pos) and pos == sorted(pos) and kinds == sorted(kinds, key=["market", "bank", "rule"].index),
                         shot, " | ".join(want))
                    nt = full[c]["next_test"]
                    has = bool(pg.query_selector("[data-st-next]"))
                    item(f"A2 Next test only with a date [{where}]", has == bool(nt and nt.get("date")), shot, f"date {nt and nt.get('date')}, shown {has}")
                    if full[c]["pivot"]["v"] is not None:
                        item(f"A2 pivot shown [{where}]", pg.is_visible(".cb-st-pivot"), shot)
                    pg.evaluate("document.getElementById('cbStanceCalc').open = true")
                    counts[where] = check_numbers(pg.evaluate(NUMS_JS, ".cb-st-zone [data-f]"), full, c, where, fails)
                    calc = pg.inner_text("#cbStanceCalc")
                    musts = ("need x = gap + y/3",) + (("stance S = i − N", "pressure G = 1.5 ×") if c in with_n else ("neutral: n/a",))
                    item(f"4 Show calculation complete [{where}]", all(m in calc for m in musts), shot, ", ".join(m for m in musts if m not in calc))
                    if c == "USD":
                        item(f"7 USD as a range, midpoint only in the calculation [{where}]", "midpoint of the target range 3.75–4.00%" in calc
                             and "3.875" not in pg.inner_text(".cb-st-cards"), shot)
                    if len(full[c]["history"]) < 5:
                        item(f"History text [{where}]", "History starts on" in pg.inner_text(".cb-st-zone"), shot)
                    sw = pg.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
                    item(f"5 no horizontal scroll [{where}]", sw[0] <= sw[1] + 1, shot, str(sw))
                    bad = low_contrast(pg, ".cb-st-zone", where)
                    item(f"6 contrast >= {MIN_CONTRAST} [{where}]", not bad, shot, json.dumps(bad[:3], ensure_ascii=False))
                    item(f"footer [{where}]", bool(pg.query_selector("footer.fred-notice")), shot)
                    pg.evaluate("window.scrollTo(0, 0)")
                    pg.screenshot(path=str(a.shots / shot), full_page=True)
                    shots.append(shot)
                    pg.context.close()

        # ---- the other pages: no console error, and (with --baseline) the same DOM outside the stance ----
        for rel in ("central-banks.html", "central-banks.html#pairs", "central-banks/pair/eurusd.html", "economic.html"):
            pg = page(1440, 900, False, "dark")
            pg.goto(f"{base}/{rel}")
            pg.wait_for_timeout(1500)
            item(f"footer [{rel}]", bool(pg.query_selector("footer.fred-notice")), "")
            pg.context.close()
        if a.baseline:
            hb = serve(a.baseline, a.port + 1)
            for rel, sel in (("central-banks.html", "#cbCompare"), ("central-banks.html#pairs", "#cbPairs"),
                             ("central-banks/pair/eurusd.html", "#cbRoot"), ("economic.html", "main")):
                doms = []
                for b in (base, f"http://127.0.0.1:{a.port + 1}"):
                    pg = page(1440, 900, False, "dark")
                    pg.goto(f"{b}/{rel}")
                    pg.wait_for_timeout(1500)
                    doms.append(pg.evaluate(f"""() => {{ const e = document.querySelector('{sel}'); if (!e) return '';
                        const c = e.cloneNode(true); c.querySelectorAll('canvas, .cb-cd, .cb-bankbar').forEach(x => x.remove()); return c.innerText; }}"""))
                    pg.context.close()
                item(f"other pages unchanged [{rel} {sel}]", doms[0] == doms[1], "")
            hb.shutdown()
        br.close()
    httpd.shutdown()
    item("zero console errors", not errors, "", "; ".join(errors[:5]))
    print(json.dumps({"checks": len(checklist), "failed": [c for c in checklist if not c["ok"]], "other_failures": [f for f in fails if not any(f.startswith(c["point"]) for c in checklist)],
                      "numbers_checked": sum(counts.values()), "contrast": contrast, "screenshots": shots, "checklist": checklist}, indent=1, ensure_ascii=False))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
