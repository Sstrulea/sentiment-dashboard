// Plain-Node test of middleware.js login flow (audit 9A): no browser, no Vercel.
// @vercel/functions' next() is stubbed by loading a copy of the module source.
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import assert from "node:assert/strict";

const src = readFileSync(new URL("../../middleware.js", import.meta.url), "utf8")
  .replace('import { next } from "@vercel/functions";',
           'const next = () => new Response("next", { status: 200 });');
const dir = mkdtempSync(join(tmpdir(), "mw-"));
writeFileSync(join(dir, "mw.mjs"), src);
process.env.DASH_TOKENS = "tester:secret";
const { default: middleware } = await import(join(dir, "mw.mjs"));

const H = "https://dash.example";
const get = (path, cookie) => middleware(new Request(H + path, { headers: cookie ? { cookie } : {} }));
const post = (path, form) => middleware(new Request(H + path, {
  method: "POST", body: new URLSearchParams(form),
  headers: { "content-type": "application/x-www-form-urlencoded" } }));

// "/" -> 307 /economic, with and without a cookie (before the auth check)
for (const cookie of [undefined, "dash_auth=secret", "dash_auth=wrong"]) {
  const rr = await get("/", cookie);
  assert.equal(rr.status, 307, String(cookie));
  assert.equal(rr.headers.get("location"), "/economic", String(cookie));
}

// /economic not logged in -> /login?next=%2Feconomic; after login -> /economic
let e = await get("/economic");
assert.equal(e.status, 303);
assert.equal(e.headers.get("location"), "/login?next=%2Feconomic");
e = await post("/login?next=%2Feconomic", { token: "secret", next: "/economic" });
assert.equal(e.status, 303); assert.equal(e.headers.get("location"), "/economic");

// not logged in -> /login?next=<path+query>
let r = await get("/carry?x=1&y=2");
assert.equal(r.status, 303);
assert.equal(r.headers.get("location"), "/login?next=" + encodeURIComponent("/carry?x=1&y=2"));

// the login page carries next in a hidden field (escaped)
r = await get("/login?next=" + encodeURIComponent("/carry?x=1"));
assert.match(await r.text(), /name="next" value="\/carry\?x=1"/);

// login -> next (internal only), default /economic
r = await post("/login", { token: "secret", next: "/carry?x=1" });
assert.equal(r.status, 303); assert.equal(r.headers.get("location"), "/carry?x=1");
for (const bad of ["https://evil.example/", "//evil.example/", "/\\evil.example", "carry", ""]) {
  r = await post("/login", { token: "secret", next: bad });
  assert.equal(r.headers.get("location"), "/economic", bad);
}
r = await post("/login?next=%2Fhistory", { token: "secret" });
assert.equal(r.headers.get("location"), "/history");

// wrong token keeps next
r = await post("/login", { token: "nope", next: "/carry" });
assert.equal(r.status, 401); assert.match(await r.text(), /value="\/carry"/);

// logged in -> next()
r = await get("/carry", "dash_auth=secret");
assert.equal(r.status, 200);
console.log("middleware next-path: ok");
