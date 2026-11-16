// stance-ui-v5 on the real static/cb.js in plain Node: formatting, the shared bar scale (capped), the sorting, the table and the
// phone cards, the bank block.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "cb.js"), "utf8");
function cut(from, to) { const a = src.indexOf(from), b = src.indexOf(to, a); if (a < 0 || b < 0) throw new Error("cut " + from); return src.slice(a, b); }
const ctx = { CFG: { urls: { overview_page: "/central-banks.html" } }, SP: undefined, root: null, metaEl: null, titleEl: null, window: {},
              document: { body: { classList: { contains: () => false } }, querySelectorAll: () => [], addEventListener() {} },
              getComputedStyle: () => ({ getPropertyValue: () => "" }), setInterval() {} };
vm.createContext(ctx);
vm.runInContext(cut("  const MINUS =", "  // ---- boot ---") + ";this.fmtPP = fmtPP; this.stBar = stBar; this.stanceRows = stanceRows; this.stanceTableHtml = stanceTableHtml;" +
                "this.stanceListHtml = stanceListHtml; this.stanceZoneHtml = stanceZoneHtml; this.stanceHistoryHtml = stanceHistoryHtml;", ctx);
let failures = 0;
const eq = (g, w, what) => { if (g !== w) { failures++; console.error("FAIL " + what + ": got " + JSON.stringify(g) + ", want " + JSON.stringify(w)); } };
const ok = (c, what) => { if (!c) { failures++; console.error("FAIL " + what); } };
eq(ctx.fmtPP(0.68), "+0.68", "pp with a sign"); eq(ctx.fmtPP(-0.55), "−0.55", "minus sign"); eq(ctx.fmtPP(0), "0.00", "zero");
ok(ctx.stBar(1, 2, "pos").indexOf("left:50%;width:25.00%") > 0, "+1 pp on a 2 pp scale: half of one side");
ok(ctx.stBar(-3, 2, "need").indexOf("left:0.00%;width:50.00%") > 0, "capped at -2 pp");
const b = (c, p, n) => ({ bank: c, asof: "2026-11-15", rate_display: { rate: 2.5, range: false }, position: { v: p, label: "x", neutral: 2, na: p === null ? "SNB publishes no neutral" : null },
  need: { v: n, label: "y", measure: "Core CPI 2.4% (Sep)", target: "vs 2%", official_name: "o" }, why: { conclusion: "More hikes likely", reason: "r", move: true, sources: [{ what: "core", text: "t" }] } });
const st = { scale_pp: 2, banks: { USD: b("USD", 0.68, 1.1), AUD: b("AUD", 1.36, 1.1), EUR: b("EUR", 0.5, 0.5), CHF: b("CHF", null, 0), CAD: b("CAD", -0.25, -0.15) } };
eq(ctx.stanceRows(st, "need").map((s) => s.bank).join(","), "USD,AUD,EUR,CHF,CAD", "Need, descending (default)");
eq(ctx.stanceRows(st, "position").map((s) => s.bank).join(","), "AUD,USD,EUR,CAD,CHF", "Position, n/a last");
eq(ctx.stanceRows(st, "bank").map((s) => s.bank).join(","), "USD,EUR,CAD,AUD,CHF", "Bank order");
const html = ctx.stanceTableHtml(st, "need");
ok(html.indexOf("rate − neutral: how tight policy is") > 0 && html.indexOf("core inflation − target: how far inflation is from goal") > 0 && html.indexOf("likely next move and the reason") > 0, "column subheads");
eq((html.match(/<tr class="cb-row"/g) || []).length, 5, "one row per bank");
ok(html.indexOf("SNB publishes no neutral") > 0, "Position n/a with the reason");
ok(html.indexOf('title="o"') > 0 && html.indexOf('title="core: t"') > 0, "official name and sources on hover");
eq((ctx.stanceListHtml(st, "need").match(/cb-v5-card-why/g) || []).length, 5, "phone: Why under each card");
const z = ctx.stanceZoneHtml({ stance: Object.assign(b("USD", 0.68, 1.1), { scale_pp: 2, mandate: { target: "2%" }, next_test: { date: null }, history: [{ date: "2026-11-15" }] }) });
ok(z.indexOf("Stance and need") > 0 && z.indexOf("Mandate") > 0 && z.indexOf("History starts on 15 Nov 2026") > 0 && z.indexOf("data-st-next") < 0, "bank zone");
if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("stance v5: all checks passed");
