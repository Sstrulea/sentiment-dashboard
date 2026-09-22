// End-to-end check of the Worker in the real runtime (workerd, through `npx wrangler dev`), against a mock GitHub on localhost: no token, no network, nothing deployed.
//   node test/e2e_local.mjs        (needs npx; wrangler is fetched on demand - nothing is added to the project)
// The mock serves a schedule built around "now": a decision due in a minute, a regular run due this minute, a BoJ window that is open and whose RSS has a decision item.
import http from "node:http";
import { spawn } from "node:child_process";

const PORT_GH = 8799, PORT_DEV = 8788;
const now = new Date();
const iso = (d) => new Date(d).toISOString().replace(/\.\d+Z$/, "Z");
const plus = (min) => iso(now.getTime() + min * 60000);
const schedule = {
  version: 1, tick_minutes: 5, boj: { rss: `http://127.0.0.1:${PORT_GH}/boj.xml`, item_match: "/mopo/mpmdeci/" },
  events: [
    { id: "decision:USD:2099-01-01", kind: "decision", bank: "USD", date: "2099-01-01", at: plus(0), fire_at: plus(1), lead_minutes: 5, grace_minutes: 90, dispatch: { workflow: "cb-refresh.yml", inputs: { event: "decision", bank: "USD", date: "2099-01-01" } } },
    { id: "conference:USD:2099-01-01", kind: "conference", bank: "USD", date: "2099-01-01", at: plus(-200), fire_at: plus(60), lead_minutes: 0, grace_minutes: 240, dispatch: { workflow: "cb-refresh.yml", inputs: { event: "conference", bank: "USD", date: "2099-01-01" } } },
    { id: "decision:JPY:2099-01-01", kind: "boj_window", bank: "JPY", date: "2099-01-01", window_start: plus(-30), window_end: plus(60), fallback_at_window_end: true, grace_minutes: 30, dispatch: { workflow: "cb-refresh.yml", inputs: { event: "decision", bank: "JPY", date: "2099-01-01" } } },
  ],
  regular: [{ workflow: "econ-refresh.yml", minute: now.getUTCMinutes() - (now.getUTCMinutes() % 5), hours: [now.getUTCHours()], dow: [now.getUTCDay()] }],
};
const rss = `<rss version="2.0"><channel><item><title>Statement on Monetary Policy</title><pubDate>${now.toUTCString()}</pubDate><link>http://www.boj.or.jp/en/mopo/mpmdeci/mpr_2099/k990101a.pdf</link></item></channel></rss>`;
const seen = [];
const gh = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", () => {
    seen.push({ method: req.method, url: req.url, auth: req.headers.authorization, body: body ? JSON.parse(body) : null });
    if (req.url.startsWith("/repos/owner/repo/contents/")) { res.writeHead(200, { etag: '"e2e"', "content-type": "application/json" }); return res.end(JSON.stringify(schedule)); }
    if (req.url === "/boj.xml") { res.writeHead(200, { "content-type": "application/xml" }); return res.end(rss); }
    if (req.url.endsWith("/dispatches")) { res.writeHead(204); return res.end(); }
    res.writeHead(404); res.end();
  });
}).listen(PORT_GH);

const dev = spawn("npx", ["--yes", "wrangler", "dev", "--test-scheduled", "--port", String(PORT_DEV), "--ip", "127.0.0.1",
  "--var", `GH_API:http://127.0.0.1:${PORT_GH}`, "--var", "GH_REPO:owner/repo", "--var", "GH_TOKEN:local-test-token", "--var", "GH_REF:main", "--var", "SCHEDULE_PATH:data/cb/trigger_schedule.json"],
  { cwd: new URL("..", import.meta.url).pathname, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, WRANGLER_SEND_METRICS: "false", CI: "1" } });
let log = "";
dev.stdout.on("data", (d) => (log += d));
dev.stderr.on("data", (d) => (log += d));
const done = (code, msg) => { console.log(msg); dev.kill("SIGTERM"); gh.close(); setTimeout(() => process.exit(code), 500); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const tick = () => fetch(`http://127.0.0.1:${PORT_DEV}/__scheduled?cron=*%2F5+*+*+*+*`).then((r) => r.status);
const dispatches = () => seen.filter((s) => s.url.endsWith("/dispatches")).map((s) => `${s.url.split("/workflows/")[1].replace("/dispatches", "")} ${JSON.stringify(s.body.inputs || {})}`);

(async () => {
  for (let i = 0; i < 120 && !/Ready on/.test(log); i++) await sleep(1000);
  if (!/Ready on/.test(log)) return done(1, "FAIL: wrangler dev did not start\n" + log.slice(-1500));
  const s1 = await tick();
  await sleep(3000);
  const first = dispatches();
  const want = ['cb-refresh.yml {"event":"decision","bank":"USD","date":"2099-01-01"}', 'cb-refresh.yml {"event":"decision","bank":"JPY","date":"2099-01-01"}'];
  const regularWanted = "econ-refresh.yml {}";
  const s2 = await tick();
  await sleep(3000);
  const second = dispatches();
  const ok = s1 === 200 && want.every((w) => first.includes(w)) && first.includes(regularWanted) && !first.some((d) => d.includes("conference")) &&
    second.length === first.length && seen.every((s) => s.auth === undefined || s.auth === "Bearer local-test-token") &&
    seen.some((s) => s.url.startsWith("/repos/owner/repo/contents/") && s.auth === "Bearer local-test-token");
  console.log("first tick :", s1, first);
  console.log("second tick:", s2, second.length === first.length ? "no new dispatch (dedupe)" : second);
  done(ok ? 0 : 1, ok ? "ok: wrangler dev end-to-end" : "FAIL\n" + log.slice(-2500));
})();
