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

function loginPage(error) {
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
      if (!tokens.has(submitted)) return html(loginPage("Parolă greșită."), 401);
      return new Response(null, {
        status: 303,
        headers: {
          Location: "/",
          "Set-Cookie": `${COOKIE}=${encodeURIComponent(submitted)}; Path=/; HttpOnly; ` +
                        `Secure; SameSite=Lax; Max-Age=${MAX_AGE}`,
        },
      });
    }
    return html(loginPage(""), 200);
  }

  const raw = parseCookies(request.headers.get("cookie"))[COOKIE];
  const who = raw ? tokens.get(decodeURIComponent(raw)) : undefined;
  if (who) {
    if (!url.pathname.includes(".")) console.log(`[auth] ${who} ${url.pathname}`);
    return next();
  }

  return new Response(null, {
    status: 303,
    headers: { Location: "/login", "cache-control": "no-store" },
  });
}
