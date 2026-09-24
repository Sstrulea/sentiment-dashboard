import { next } from "@vercel/functions";

const COOKIE = "dash_auth";
const MAX_AGE = 60 * 60 * 24 * 30;

function loadTokens() {
  const raw = process.env.DASH_TOKENS || "";
  const byToken = new Map();
  for (const pair of raw.split(",")) {
    const i = pair.indexOf(":");
    if (i <= 0) continue;
    const name = pair.slice(0, i).trim();
    const token = pair.slice(i + 1).trim();
    if (name && token) byToken.set(token, name);
  }
  return byToken;
}

function parseCookies(header) {
  const out = {};
  for (const part of (header || "").split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
  }
  return out;
}

const DEFAULT_NEXT = "/economic";

// Only an internal path may be a post-login target: starts with "/" but not
// "//" (protocol-relative) or "/\\" (browsers normalize it to "//").
function safeNext(value) {
  const v = String(value || "");
  if (!v.startsWith("/") || v.startsWith("//") || v.startsWith("/\\")) return DEFAULT_NEXT;
  return v;
}

function escapeAttr(v) {
  return String(v).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function loginPage(error, nextPath) {
  const msg = error || "Sentiment Dashboard";
  const cls = error ? ' class="err"' : "";
  return `<!doctype html><html lang="ro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Acces</title>
<style>
  :root{color-scheme:dark}
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d1117;
       color:#e6edf3;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  form{display:flex;flex-direction:column;gap:16px;width:min(260px,80vw);text-align:center}
  p{margin:0;font-size:14px;color:#8b949e}
  p.err{color:#f85149}
  input{width:100%;padding:8px 0;border:0;border-bottom:1px solid #30363d;
        background:transparent;color:inherit;font:inherit;text-align:center;outline:none}
  input:focus{border-bottom-color:#8b949e}
</style></head><body>
<form method="POST" action="/login">
  <input type="hidden" name="next" value="${escapeAttr(safeNext(nextPath))}">
  <p${cls}>${msg}</p>
  <input type="password" name="token" autofocus autocomplete="current-password">
</form></body></html>`;
}

function html(body, status) {
  return new Response(body, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
}

export default async function middleware(request) {
  const url = new URL(request.url);
  // "/" -> /economic (307, temporary), before any auth check and whatever the
  // cookie: the single place for this redirect (not vercel.json).
  if (url.pathname === "/") {
    return new Response(null, {
      status: 307,
      headers: { Location: "/economic" + url.search, "cache-control": "no-store" },
    });
  }

  const tokens = loadTokens();

  if (tokens.size === 0) {
    return new Response("DASH_TOKENS not configured", { status: 503 });
  }

  if (url.pathname === "/logout") {
    return new Response(null, {
      status: 303,
      headers: {
        Location: "/login",
        "Set-Cookie": `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`,
      },
    });
  }

  if (url.pathname === "/login") {
    if (request.method === "POST") {
      const form = await request.formData();
      const submitted = String(form.get("token") || "").trim();
      const nextPath = safeNext(form.get("next") || url.searchParams.get("next"));
      if (!tokens.has(submitted)) return html(loginPage("Parolă greșită.", nextPath), 401);
      return new Response(null, {
        status: 303,
        headers: {
          Location: nextPath,
          "Set-Cookie": `${COOKIE}=${encodeURIComponent(submitted)}; Path=/; HttpOnly; ` +
                        `Secure; SameSite=Lax; Max-Age=${MAX_AGE}`,
        },
      });
    }
    return html(loginPage("", url.searchParams.get("next")), 200);
  }

  const raw = parseCookies(request.headers.get("cookie"))[COOKIE];
  const who = raw ? tokens.get(decodeURIComponent(raw)) : undefined;
  if (who) {
    if (!url.pathname.includes(".")) console.log(`[auth] ${who} ${url.pathname}`);
    return next();
  }

  const back = encodeURIComponent(url.pathname + url.search);
  return new Response(null, {
    status: 303,
    headers: { Location: `/login?next=${back}`, "cache-control": "no-store" },
  });
}
