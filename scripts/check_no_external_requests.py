"""fix/sources-compliance: the published pages never request data from another host - every page is opened (Playwright) from a local
copy of public/ and every request it makes is recorded; anything that is not the local server fails the check.

    python scripts/check_no_external_requests.py <public_dir> [--port 8790]
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import socketserver
import threading
from pathlib import Path
from urllib.parse import urlsplit

socketserver.TCPServer.allow_reuse_address = True


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("public", type=Path)
    ap.add_argument("--port", type=int, default=8790)
    a = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright
    httpd = socketserver.TCPServer(("127.0.0.1", a.port), functools.partial(_Quiet, directory=str(a.public)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    pages = sorted(p.relative_to(a.public).as_posix() for p in a.public.rglob("*.html"))
    external, total = {}, 0
    with sync_playwright() as p:
        br = p.chromium.launch()
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        for rel in pages:
            pg = ctx.new_page()
            seen = []
            pg.on("request", lambda r, seen=seen: seen.append(r.url))
            pg.goto(f"http://127.0.0.1:{a.port}/{rel}", wait_until="networkidle")
            total += len(seen)
            for u in seen:
                host = urlsplit(u).hostname or ""
                if host not in ("127.0.0.1", "localhost") and not u.startswith(("data:", "blob:", "about:")):
                    external.setdefault(rel, []).append(u)
            pg.close()
        br.close()
    httpd.shutdown()
    print(json.dumps({"pages": len(pages), "requests": total, "external": external}, indent=1))
    return 1 if external else 0


if __name__ == "__main__":
    raise SystemExit(main())
