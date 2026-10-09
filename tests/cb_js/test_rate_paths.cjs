// Stage 3 (rate paths) on the real static/cb.js in plain Node: the probability text, the Compare rows (≈ for estimates, — with the
// reason, sorting), the bank table (Δ vs 1w) and BANK_COLORS. The pieces are cut out of the file and run in a sandbox.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "cb.js"), "utf8");

function cut(from, to) {
  const a = src.indexOf(from), b = src.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error("not found in cb.js: " + from + " .. " + to);
  return src.slice(a, b);
}
const ctx = { CFG: { urls: { overview_page: "/central-banks.html" } }, SP: undefined, state: { meta: { flags: {} }, charts: [], redraw: [] }, CHEV: "",
              document: { body: { classList: { contains: () => false } }, querySelectorAll: () => [] }, window: {} };
vm.createContext(ctx);
vm.runInContext(cut("  const MINUS =", "  const state =") + cut("  function esc(s) {", "  // ---- rate / next meeting text") +
                cut("  // ---- rate paths (stage 3)", "  // ---- bank page ---") +
                ";this.probText = probText; this.moveText = moveText; this.compareRows = compareRows; this.sortRows = sortRows; this.compareTableHtml = compareTableHtml;" +
                "this.pathTableRows = pathTableRows; this.pathTableHtml = pathTableHtml; this.BANK_COLORS = BANK_COLORS; this.BANK_ORDER = BANK_ORDER; this.m12Text = m12Text;", ctx);
let failures = 0;
const eq = (got, want, what) => { if (got !== want) { failures++; console.error("FAIL " + what + ": got " + JSON.stringify(got) + ", want " + JSON.stringify(want)); } };
const ok = (cond, what) => { if (!cond) { failures++; console.error("FAIL " + what); } };

// probability text
eq(ctx.probText({ dir: "hike", n: 0, p: 0.3 }), "30% hike", "n = 0 hike");
eq(ctx.probText({ dir: "hike", n: 1, p: 0.28 }), "+25 bp + 28% of +50", "n ≥ 1 hike");
eq(ctx.probText({ dir: "cut", n: 0, p: 0.3 }), "30% cut", "n = 0 cut");
eq(ctx.probText({ dir: "cut", n: 1, p: 0.28 }), "−25 bp + 28% of −50", "n ≥ 1 cut");
eq(ctx.probText({ dir: "hike", n: 2, p: 0 }), "+50 bp", "whole moves only");
eq(ctx.probText({ dir: "hold", n: 0, p: 0 }), "hold", "hold");
eq(ctx.moveText({ step_bp: 12, prob: { dir: "hike", n: 0, p: 0.48 }, est: false }), "48% hike · +12.0 bp", "priced move");
eq(ctx.moveText({ step_bp: 12, prob: null, est: true }), "≈ +12.0 bp", "estimate: ≈, no probability");
eq(ctx.moveText({ step_bp: null, na: "x" }), null, "n/a move");

// Compare rows
const pt = (m, rate, cum, step, extra) => Object.assign({ meeting: m, effective: m, rate: rate, cum_bp: cum, step_bp: step, flag: "CURVE", method: "CURVE", est: false, prob: { dir: step >= 0 ? "hike" : "cut", n: 0, p: Math.abs(step) / 25 }, na: null }, extra || {});
const pj = (ccy, next, points, m12, d1, d3) => ({ ccy: ccy, short: ccy + "B", href: "/central-banks/" + ccy.toLowerCase() + ".html", asof: "2026-10-08",
  current: { rate: 2, range: false }, next: next ? { decision: next, effective: next, time: { utc: next + "T12:00:00Z", tz: "UTC" } } : null, points: points, m12: m12,
  history: { "1w": { na: null, points: points.map((p) => ({ meeting: p.meeting, rate: p.rate === null ? null : p.rate - 0.05, cum_bp: null })) }, "3w": { na: "history starts 2026-10-08", points: [] } },
  delta: { "1w": d1, "3w": d3 } });
const paths = {
  USD: pj("USD", "2026-10-28", [pt("2026-10-28", 3.93, 5.4, 5.4)], { meeting: "2026-10-28", cum_bp: 82, moves: 3.28, est: false }, { v: 2.5, flag: "CURVE", na: null }, { v: null, na: "history starts 2026-10-08" }),
  NZD: pj("NZD", "2026-10-28", [pt("2026-10-28", null, null, null, { prob: null, na: "not covered by any contract", flag: "ESTIMATE", est: true }), pt("2026-12-09", 3.19, 43.7, 43.7, { prob: null, flag: "ESTIMATE", est: true })],
          { meeting: "2026-12-09", cum_bp: 43.7, moves: 1.75, est: true }, { v: -8, flag: "ESTIMATE", na: null }, { v: null, na: "history starts 2026-09-18" }),
  CHF: pj("CHF", "2026-12-10", [pt("2026-12-10", 0.06, 6, 7.5)], { meeting: "2026-12-10", cum_bp: 6, moves: 0.24, est: false }, { v: null, na: "history starts 2026-10-08" }, { v: null, na: "history starts 2026-10-08" }),
  EUR: pj("EUR", "2026-10-29", [pt("2026-10-29", 2.47, 3.4, 3.4)], { meeting: "2026-10-29", cum_bp: 73, moves: 2.92, est: false }, { v: 1, flag: "CURVE", na: null }, { v: null, na: "x" })
};
const rows = ctx.compareRows(paths);
eq(rows.map((r) => r.ccy).join(","), "USD,EUR,NZD,CHF", "rows follow the bank order");
eq(ctx.sortRows(rows, "next").map((r) => r.ccy).join(","), "USD,NZD,EUR,CHF", "sort by next meeting: same instant -> bank order");
const early = JSON.parse(JSON.stringify(paths)); early.NZD.next.time.utc = "2026-10-28T01:00:00Z";
eq(ctx.sortRows(ctx.compareRows(early), "next").map((r) => r.ccy).join(","), "NZD,USD,EUR,CHF", "sort by next meeting: the earlier instant first");
eq(ctx.sortRows(rows, "move").map((r) => r.ccy).join(","), "CHF,USD,EUR,NZD", "sort by next move: most hawkish first, n/a last");
eq(ctx.sortRows(rows, "m12").map((r) => r.ccy).join(","), "USD,EUR,NZD,CHF", "sort by 12M");
const html = ctx.compareTableHtml(paths, "move");
ok(html.indexOf("≈ +44 bp · 1.75 hikes") > 0, "12M of an estimate carries ≈");
ok(html.indexOf("≈ −8.0") > 0, "Δ 1w of an estimate carries ≈");
ok(/<span class="cb-na" title="not covered by any contract">—<\/span>/.test(html), "n/a = — with the reason in the tooltip");
ok(/<span class="cb-na" title="history starts 2026-10-08">—<\/span>/.test(html), "Δ n/a carries its reason");
ok(html.indexOf('th class="cb-sort active" data-sort="move"') > 0, "the sorted column is marked");
ok(html.indexOf('data-href="/central-banks/chf.html"') < html.indexOf('data-href="/central-banks/nzd.html"'), "rows rendered in the sorted order");
ok(html.indexOf("21.6% hike") < 0 && html.indexOf("22% hike · +5.4 bp") > 0, "the move text in the table");

// bank table
const t = ctx.pathTableRows(paths.NZD);
eq(t[0].rate, null, "n/a row has no rate");
eq(t[0].na, "not covered by any contract", "n/a row keeps the reason");
eq(t[1].rate, "≈ 3.190%", "estimate row: ≈ on the rate");
eq(t[1].prob, null, "estimate row: no probability");
eq(t[1].moves, "≈ +1.75", "moves (cumulative 25 bp)");
ok(Math.abs(t[1].dW - 5) < 1e-9, "Δ vs 1w = (now - 1w) in bp");
const tu = ctx.pathTableRows(paths.USD);
eq(tu[0].prob, "22% hike", "probability text in the bank table");
const th = ctx.pathTableHtml(paths.NZD);
ok(th.indexOf('<tr class="cb-est">') > 0 && th.indexOf("not covered by any contract") > 0, "the bank table marks estimates and keeps the reason");
const noHist = JSON.parse(JSON.stringify(paths.USD)); noHist.history["1w"] = { na: "history starts 2026-10-08", points: [] };
ok(ctx.pathTableHtml(noHist).indexOf('title="history starts 2026-10-08"') > 0, "Δ vs 1w n/a carries the history reason");

// colours: one per bank, both themes, distinct
["light", "dark"].forEach((k) => {
  const c = ctx.BANK_COLORS[k];
  eq(Object.keys(c).sort().join(","), ctx.BANK_ORDER.slice().sort().join(","), "BANK_COLORS." + k + " has the 8 banks");
  eq(new Set(Object.values(c)).size, 8, "BANK_COLORS." + k + " are distinct");
});
eq(ctx.BANK_ORDER.length, 8, "eight banks");
eq(ctx.m12Text({ cum_bp: -50, moves: -2, est: false }), "−50 bp · 2.00 cuts", "12M text for cuts");

if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("ok - rate paths");
