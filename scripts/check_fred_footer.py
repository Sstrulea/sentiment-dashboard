"""Automated check of the FRED API terms notice (templates/_footer.html.j2) on every rendered page.

    python scripts/check_fred_footer.py <public_dir> [--port 8765]

Serves <public_dir> locally (clean URLs, like Vercel), opens every *.html at 1440x900 and at 390x844 (phone) with
Playwright and checks, per page and viewport: the exact notice text, the link text and URL, that the footer is visible
(not display:none / hidden / zero-size), inside the viewport width, and not clipped (no horizontal or vertical overflow
of its text). Exit 1 when any page fails. Needs Playwright + Chromium (the repo's .venv).
"""
from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.fred_api import NOTICE, TERMS_URL  # noqa: E402

VIEWPORTS = (("desktop", 1440, 900, False), ("phone", 390, 844, True))
LINK_TEXT = "FRED® API Terms of Use"


class _Clean(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(public: Path, port: int):
    handler = functools.partial(_Clean, directory=str(public))
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


CHECK_JS = """() => {
  const f = document.querySelector('footer.fred-notice');
  if (!f) return {ok: false, why: 'no footer'};
  const p = f.querySelector('p'), a = f.querySelector('a');
  const cs = getComputedStyle(f), r = f.getBoundingClientRect();
  const text = p ? p.childNodes[0].textContent.replace(/\\s+/g, ' ').trim() : '';
  let hidden = cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0 || r.width === 0 || r.height === 0;
  for (let e = f.parentElement; e; e = e.parentElement) { const s = getComputedStyle(e); if (s.display === 'none' || s.visibility === 'hidden') hidden = true; }
  return {ok: true, text, link_text: a ? a.textContent.trim() : '', href: a ? a.getAttribute('href') : '', hidden,
          left: r.left, right: r.right, vw: document.documentElement.clientWidth,
          clipped: p ? (p.scrollWidth > p.clientWidth + 1 || p.scrollHeight > p.clientHeight + 1) : true};
}"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("public", type=Path)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright
    pages = sorted(p.relative_to(a.public).as_posix() for p in a.public.rglob("*.html"))
    httpd = serve(a.public, a.port)
    fails, n = [], 0
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            for vname, w, h, mobile in VIEWPORTS:
                ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=mobile, has_touch=mobile)
                pg = ctx.new_page()
                for rel in pages:
                    pg.goto(f"http://127.0.0.1:{a.port}/{rel}", wait_until="load")
                    pg.wait_for_timeout(300)
                    loc = pg.locator("footer.fred-notice")
                    if loc.count():
                        loc.first.scroll_into_view_if_needed()
                    r = pg.evaluate(CHECK_JS)
                    n += 1
                    why = []
                    if not r.get("ok"):
                        why.append(r.get("why"))
                    else:
                        if r["text"] != NOTICE:
                            why.append(f"text {r['text']!r}")
                        if r["link_text"] != LINK_TEXT or r["href"] != TERMS_URL:
                            why.append(f"link {r['link_text']!r} -> {r['href']!r}")
                        if r["hidden"]:
                            why.append("hidden")
                        if r["left"] < -1 or r["right"] > r["vw"] + 1:
                            why.append(f"outside the viewport ({r['left']:.0f}..{r['right']:.0f} of {r['vw']})")
                        if r["clipped"]:
                            why.append("text clipped")
                        if not loc.first.is_visible():
                            why.append("not visible")
                    if why:
                        fails.append((vname, rel, why))
                ctx.close()
            b.close()
    finally:
        httpd.shutdown()
    print(f"checked {n} page views ({len(pages)} pages x {len(VIEWPORTS)} viewports): {n - len(fails)} ok, {len(fails)} failed")
    for v, rel, why in fails:
        print(f"  FAIL {v} {rel}: {'; '.join(why)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
