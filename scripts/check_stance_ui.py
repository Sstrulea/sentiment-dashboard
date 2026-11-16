"""Automated visual / structural check of the stance UI (v5: Position / Need / Why), both themes, desktop 1280 and phone 390.

    python scripts/check_stance_ui.py <public_dir> [--port 8766] [--shots reports/stance-ui]

Per theme and viewport: /central-banks#stance - 8 rows (desktop) / 8 cards with Why under them (phone); the 3 sortings (Need
default, Position, Bank) give the payload order; every rendered pp number = the payload value formatted (+0.68, -0.55, 0.00); no
horizontal overflow (page and table); text contrast >= 4.5:1 in the stance areas; each bank page: the zone "Stance and need" with
its numbers, no overflow, contrast; zero console errors; the FRED footer. Screenshots in --shots. Exit 1 on any failure.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import socketserver
import threading
from pathlib import Path

BANKS = ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF")
MINUS = "−"
MIN_CONTRAST = 4.5
socketserver.TCPServer.allow_reuse_address = True


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(public: Path, port: int):
    httpd = socketserver.TCPServer(("127.0.0.1", port), functools.partial(_Quiet, directory=str(public)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def fmt_pp(v: float) -> str:
    s = f"{abs(v):.2f}"
    return s if float(s) == 0 else ("+" if v > 0 else MINUS) + s


def at(obj, key):
    for k in key.split("."):
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj


def order(st: dict, key: str) -> list:
    if key == "bank":
        return [c for c in BANKS if c in st]
    val = (lambda c: st[c]["position"]["v"]) if key == "position" else (lambda c: st[c]["need"]["v"])
    have = sorted((c for c in BANKS if val(c) is not None), key=lambda c: (-val(c), BANKS.index(c)))
    return have + [c for c in BANKS if val(c) is None]


NUMS_JS = """(sel) => Array.from(document.querySelectorAll(sel)).map(e => { const h = e.closest('[data-ccy]');
  return {k: e.dataset.k, v: Number(e.dataset.v), text: e.textContent, ccy: h ? h.dataset.ccy : null}; })"""
CONTRAST_JS = """(sel) => { const lum = (c) => { const m = c.match(/[\\d.]+/g).map(Number); const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
    return [0.2126 * f(m[0]) + 0.7152 * f(m[1]) + 0.0722 * f(m[2]), m.length > 3 ? m[3] : 1]; };
  const bg = (el) => { for (let e = el; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor; if (lum(c)[1] > 0.5) return c; } return getComputedStyle(document.body).backgroundColor; };
  const bad = []; let n = 0, min = 99;
  document.querySelectorAll(sel).forEach((root) => { const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let t = w.nextNode(); t; t = w.nextNode()) { if (!t.textContent.trim()) continue; const el = t.parentElement;
      if (el.closest('[hidden], details:not([open]) > :not(summary)') || !el.getClientRects().length) continue;
      const a = lum(getComputedStyle(el).color)[0], b = lum(bg(el))[0], r = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); n++; min = Math.min(min, r);
      if (r < 4.5) bad.push({text: t.textContent.trim().slice(0, 30), ratio: Math.round(r * 100) / 100}); } });
  return {n, min: Math.round(min * 100) / 100, bad: bad.slice(0, 5)}; }"""
OVERFLOW_JS = """(sel) => { const d = document.documentElement; const out = {page: d.scrollWidth > d.clientWidth + 1, cells: 0};
  document.querySelectorAll(sel).forEach(e => { if (e.scrollWidth > e.clientWidth + 1) out.cells++; }); return out; }"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("public", type=Path)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--shots", type=Path, default=Path("reports/stance-ui"))
    a = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright
    st = json.loads((a.public / "data" / "cb" / "overview.json").read_text())["stance"]["banks"]
    full = {c: json.loads((a.public / "data" / "cb" / f"{c.lower()}.json").read_text())["stance"] for c in BANKS}
    a.shots.mkdir(parents=True, exist_ok=True)
    httpd = serve(a.public, a.port)
    base = f"http://127.0.0.1:{a.port}"
    checks, errors, shots, contrast = [], [], [], {}

    def item(point, ok, shot="", detail=""):
        checks.append({"point": point, "ok": bool(ok), "shot": shot, "detail": detail})

    def numbers(pg, sel, stance, ccy, where, shot):
        bad = []
        for it in pg.evaluate(NUMS_JS, sel):
            want = fmt_pp(it["v"])
            pv = at(stance.get(it["ccy"] or ccy) or {}, it["k"])
            if it["text"] != want or pv is None or abs(pv - it["v"]) > 1e-9:
                bad.append(it)
        item(f"numbers = payload [{where}]", not bad, shot, str(bad[:2]))

    with sync_playwright() as p:
        br = p.chromium.launch()
        for theme in ("light", "dark"):
            for vw, (w, h, phone) in {"desktop": (1280, 900, False), "phone": (390, 844, True)}.items():
                ctx = br.new_context(viewport={"width": w, "height": h}, is_mobile=phone, has_touch=phone, color_scheme=theme)
                pg = ctx.new_page()
                pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
                pg.on("pageerror", lambda e: errors.append(str(e)))
                where, shot = f"overview {theme} {vw}", f"stance-{theme}-{vw}-{w}.png"
                pg.goto(f"{base}/central-banks.html#stance")
                pg.wait_for_selector("#cbStance:not([hidden]) " + (".cb-st-list" if phone else "#cbStanceTable"), timeout=15000)
                pg.wait_for_timeout(300)
                if not phone:
                    rows = pg.eval_on_selector_all("#cbStanceTable tbody tr", "rs => rs.map(r => r.dataset.ccy)")
                    item(f"8 rows, Need default [{where}]", rows == order(st, "need"), shot, str(rows))
                    for key in ("position", "bank", "need"):
                        pg.click(f'#cbStanceTableBox .cb-pill[data-sort="{key}"]')
                        got = pg.eval_on_selector_all("#cbStanceTable tbody tr", "rs => rs.map(r => r.dataset.ccy)")
                        item(f"sort {key} [{where}]", got == order(st, key), shot, str(got))
                    box = "#cbStanceTableBox"
                else:
                    cards = pg.eval_on_selector_all("#cbStanceListBox .cb-v5-card", "rs => rs.map(r => r.dataset.ccy)")
                    whys = pg.eval_on_selector_all("#cbStanceListBox .cb-v5-card .cb-v5-card-why", "x => x.length")
                    item(f"8 cards, Why under each [{where}]", sorted(cards) == sorted(BANKS) and whys == 8, shot, str(cards))
                    box = "#cbStanceListBox"
                numbers(pg, f"{box} [data-f]", st, None, where, shot)
                ov = pg.evaluate(OVERFLOW_JS, f"{box} td, {box} .cb-v5-card")
                item(f"no overflow [{where}]", not ov["page"] and ov["cells"] == 0, shot, json.dumps(ov))
                c = pg.evaluate(CONTRAST_JS, f"{box}, #cbStanceHow")
                contrast[where] = c["min"]
                item(f"contrast >= 4.5 [{where}]", not c["bad"], shot, json.dumps(c["bad"]))
                item(f"footer [{where}]", bool(pg.query_selector("footer.fred-notice")), shot)
                pg.screenshot(path=str(a.shots / shot), full_page=True)
                shots.append(shot)
                for cur in (BANKS if not phone else ("USD", "CHF")):
                    pg.goto(f"{base}/central-banks/{cur.lower()}.html")
                    pg.wait_for_selector("#cbStanceZone", timeout=15000)
                    pg.wait_for_timeout(300)
                    wb, sb = f"{cur} {theme} {vw}", f"{cur.lower()}-{theme}-{vw}-{w}.png"
                    numbers(pg, ".cb-st-zone [data-f]", full, cur, wb, sb)
                    ov = pg.evaluate(OVERFLOW_JS, ".cb-st-zone td")
                    item(f"no overflow [{wb}]", not ov["page"] and ov["cells"] == 0, sb, json.dumps(ov))
                    c = pg.evaluate(CONTRAST_JS, ".cb-st-zone")
                    contrast[wb] = c["min"]
                    item(f"contrast >= 4.5 [{wb}]", not c["bad"], sb, json.dumps(c["bad"]))
                    if cur in ("USD", "CHF", "NZD"):
                        pg.screenshot(path=str(a.shots / sb), full_page=True)
                        shots.append(sb)
                ctx.close()
        br.close()
    httpd.shutdown()
    item("zero console errors", not errors, "", "; ".join(errors[:3]))
    failed = [c for c in checks if not c["ok"]]
    print(json.dumps({"checks": len(checks), "failed": failed, "min_contrast": min(contrast.values()) if contrast else None, "screenshots": shots}, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
