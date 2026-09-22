// The decision -> site latency block of the methodology panel (phase 4), on the real static/cb.js in plain Node: every label and number comes from the payload.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "cb.js"), "utf8");
function cut(from, to) {
  const a = src.indexOf(from), b = src.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error("not found in cb.js: " + from + " .. " + to);
  return src.slice(a, b);
}
const ctx = { document: { addEventListener: () => {} }, DOT: " - ", EN: "-", fmtSigned: (v) => String(v) };
vm.createContext(ctx);
vm.runInContext(`const NA = "n/a", MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]; function isNum(v) { return typeof v === "number" && isFinite(v); }` +
  cut("  function esc(s) {", "  function fmtDate") + cut("  function parts(", "  function fmtDay") + cut("  function fmtDate", "  function fmtRange") + cut("  function utcStamp(", "  function footerCard"), ctx);
const block = vm.runInContext("latencyBlock", ctx);
const l = { title: "Time from decision to site", text: "First seen = when the collector stored it.",
            summary: { target_minutes: 15, n: 2, within: 1, median_minutes: 9.5, max_minutes: 21, measure_from: "2026-09-22" },
            rows: [{ meeting: "2026-09-24", official_at: "2026-09-24T07:30:00Z", first_seen_at: "2026-09-24T07:34:10Z", minutes: 4.2, ok: true, measured: true },
                   { meeting: "2026-10-28", official_at: "2026-10-28T18:00:00Z", first_seen_at: "2026-10-28T18:21:00Z", minutes: 21, ok: false, measured: true },
                   { meeting: "2026-09-16", official_at: "2026-09-16T18:00:00Z", first_seen_at: "2026-09-20T19:18:01Z", minutes: 5838, ok: false, measured: false },
                   { meeting: "2026-09-18", official_at: null, first_seen_at: "2026-09-18T04:00:00Z", minutes: null, ok: null, measured: true }] };
const html = block(l);
const fail = (m) => { console.error("FAIL: " + m + "\n" + html); process.exit(1); };
if (block(null) !== "" || block({ rows: [] }) !== "") fail("no rows, no block");
if (!html.includes("<h4>Time from decision to site</h4><p>First seen = when the collector stored it.</p><p>1 of 2 measured decisions within 15 min (median 9.5 min)</p>")) fail("the title, the text and the summary come from the payload");
if (!html.includes("<td>24 Sep 2026</td><td>24 Sep 07:30</td><td>24 Sep 07:34</td><td>4.2 min</td>")) fail("a measured row: dates, UTC stamps, delay");
if (!html.includes("21.0 min (over the target)")) fail("a delay over the target says so");
if (!html.includes('<span class="muted">before the trigger</span>') || html.includes("5838")) fail("a statement found before the trigger shows no delay");
if (!html.includes("<td>n/a</td><td>18 Sep 04:00</td><td>n/a</td>")) fail("no official time: n/a");
const none = block({ ...l, summary: { target_minutes: 15, n: 0, within: 0, median_minutes: null } });
if (!none.includes("No decision measured yet (target: within 15 min).")) fail("nothing measured yet");
if (block({ ...l, title: "<i>x</i>" }).includes("<i>x</i>")) fail("the title is escaped");
// the panel itself carries the block, after the notes and the checks (and stays the same without one)
const panel = vm.runInContext("methodPanel", ctx);
const d = { meta: { methodology: [{ title: "T", text: "x" }] }, crosschecks: [], consistency: [], notes: [], spread: null, latency: l };
const withBlock = panel(d), without = panel({ ...d, latency: null });
if (!withBlock.includes("<h4>Time from decision to site</h4>") || !withBlock.endsWith("</table></div></details>")) fail("the methodology panel ends with the latency block");
if (without.includes("Time from decision to site") || !without.endsWith("</dl></details>")) fail("no latency, no block");
console.log("ok: latency block");
