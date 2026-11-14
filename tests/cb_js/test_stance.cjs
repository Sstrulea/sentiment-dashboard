// Stance S2 on the real static/cb.js in plain Node: B.4 formatting, chips and flag chips, the map geometry (hold band, eps lines,
// whiskers, arrows, need-only banks off the map), the table rows and their sorting, "Show calculation".
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "cb.js"), "utf8");

function cut(from, to) {
  const a = src.indexOf(from), b = src.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error("not found in cb.js: " + from + " .. " + to);
  return src.slice(a, b);
}
const ctx = { CFG: { urls: { overview_page: "/central-banks.html" } }, SP: undefined, root: null, metaEl: null, titleEl: null, window: {},
              document: { body: { classList: { contains: () => false } }, querySelectorAll: () => [], addEventListener() {} },
              getComputedStyle: () => ({ getPropertyValue: () => "" }), setInterval() {} };
vm.createContext(ctx);
vm.runInContext(cut("  const MINUS =", "  // ---- boot ---") +
                ";this.fmtPP = fmtPP; this.fmtBP = fmtBP; this.stNum = stNum; this.stChip = stChip; this.stFlagChips = stFlagChips; this.stanceMapModel = stanceMapModel;" +
                "this.stanceZone = stanceZone; this.stanceRows = stanceRows; this.stanceTableHtml = stanceTableHtml; this.stanceListHtml = stanceListHtml; this.calcLines = calcLines;" +
                "this.stRate = stRate; this.stanceHistoryHtml = stanceHistoryHtml; this.cycleCell = cycleCell;", ctx);
let failures = 0;
const eq = (got, want, what) => { if (got !== want) { failures++; console.error("FAIL " + what + ": got " + JSON.stringify(got) + ", want " + JSON.stringify(want)); } };
const ok = (cond, what) => { if (!cond) { failures++; console.error("FAIL " + what); } };
const M = "−";

// ---- B.4 ----
eq(ctx.fmtPP(0.675), "+0.68", "pp 2 decimals with a sign");
eq(ctx.fmtPP(-0.55), M + "0.55", "pp negative");
eq(ctx.fmtPP(0), "0.00", "pp zero");
eq(ctx.fmtBP(69.6), "+70", "bp signed integer");
eq(ctx.fmtBP(-0.5), M + "1", "bp half away from zero");
eq(ctx.fmtBP(0.4), "0", "bp zero");
eq(ctx.fmtBP(null), "—", "bp n/a");
ok(ctx.stNum(1.0749, "bp100", "pressure.v").indexOf('data-k="pressure.v" data-f="bp100" data-v="1.0749">+107<') > 0, "a number carries key, format, value");
ok(ctx.stNum(null, "pp", "x", "no neutral").indexOf('title="no neutral"') > 0, "n/a: the reason in the tooltip");
eq(ctx.stRate({ rate: 3.875, lower: 3.75, upper: 4.0, range: true }), "3.75–4.00%", "USD: the rate as a range");
eq(ctx.stRate({ rate: 2.5, range: false }), "2.50%", "a single rate");

// ---- chips ----
ok(ctx.stChip("S1 hike", "S1", 1).indexOf("cb-st-S1 up") > 0 && ctx.stChip("S1 hike", "S1", 1).indexOf(">S1 hike<") > 0, "chip S1 hike");
ok(ctx.stChip("S2 down", "S2", -1).indexOf(" down") > 0, "chip S2 down");
ok(ctx.stChip(null, null, null).indexOf(">need only<") > 0, "no situation: need only");
const fc = ctx.stFlagChips(["stale", "boundary", "need_only", "unknown"], "", { asof: "2026-11-13" });
ok(fc.indexOf("near a threshold") < fc.indexOf("stale") && fc.indexOf("need only") < 0 && fc.indexOf("unknown") < 0, "flags: ordered, need only is the chip, unknown dropped");
ok(fc.indexOf("(as of 13 Nov 2026)") > 0, "stale: the date in the tooltip");
ok(ctx.stFlagChips(["neutral_sensitive"], "12m", {}).indexOf(">12m neutral-sensitive<") > 0, "12-month flags are labelled");

// ---- the map geometry ----
const m = (v, extra) => Object.assign({ v: v, flag: null, stale: false, na: v === null ? "no neutral" : null }, extra || {});
const bank = (c, x, S, lo, hi, x12) => ({ bank: c, need: m(x), stance: m(S, { lo: lo, hi: hi }), need_12m: m(x12) });
const params = { inflation_response: 1.5, delta_pp: 0.25, epsilon_pp: 0.5 };
const banks = { USD: bank("USD", 1.1667, 0.675, -0.025, 0.975, 0.5667), NZD: bank("NZD", 0.4333, null, null, null, -0.2), CHF: bank("CHF", 0, null, null, null, 0) };
const g = ctx.stanceMapModel(banks, params);
eq(g.points.map((p) => p.ccy).join(","), "USD", "dots only for banks with a neutral");
eq(g.excluded.map((p) => p.ccy).join(","), "NZD,CHF", "need-only banks off the map");
const lineAt = (l, x) => l[0].y + (l[1].y - l[0].y) * (x - l[0].x) / (l[1].x - l[0].x);
ok(Math.abs(lineAt(g.band.lower, 1) - 1.25) < 1e-12 && Math.abs(lineAt(g.band.upper, 1) - 1.75) < 1e-12, "hold band: y = 1.5x ± 0.25");
ok(Math.abs(lineAt(g.band.upper, 0) - lineAt(g.band.lower, 0) - 0.5) < 1e-12, "band width 2δ");
eq(g.eps_lines.join(","), "-0.5,0.5", "dotted lines at x = ±ε");
eq(JSON.stringify(g.whiskers[0]), JSON.stringify({ ccy: "USD", from: { x: 1.1667, y: -0.025 }, to: { x: 1.1667, y: 0.975 } }), "whisker = the neutral interval");
eq(JSON.stringify(g.arrows[0]), JSON.stringify({ ccy: "USD", from: { x: 1.1667, y: 0.675 }, to: { x: 0.5667, y: 0.675 } }), "arrow: held rate, need in 12m");
ok(g.xr[0] <= -1 && g.xr[1] >= 1.1667 && g.yr[0] <= -1 && g.yr[1] >= 1, "the ranges cover every point and ±ε");
eq(ctx.stanceZone(g, 1.1667, 0.675), "hike", "G > δ: hike zone");
eq(ctx.stanceZone(g, 0, 1), "cut", "G < −δ: cut zone");
eq(ctx.stanceZone(g, 0.5, 0.6), "hold", "|G| ≤ δ: the band");

// ---- table rows and sorting ----
const full = (c, m12, G, lens) => ({ bank: c, asof: "2026-11-13", m12_bp: m12, pressure: m(G, { lo: G, hi: G }), lens: m(lens, { kind: "market" }), need: m(0.5), need_12m: m(0.5),
  stance: m(0.1, { lo: 0, hi: 0.2 }), situation: m("S0", { direction: 0 }), situation_12m: m("S0", { direction: 0 }), chip: "S0 balanced", chip_12m: "S0 balanced",
  flags: [], flags_12m: [], calc: { gap_head: 0.3, rate: 2.5, neutral: { mid: 2.4, lo: 2.3, hi: 2.5, flag: "BANK_ESTIMATE", asof: "2026-10-01" } }, cycle: { direction: "hike", count: 2, previous: { direction: "cut", count: 8 }, holds_since: 1 },
  rate_display: { rate: 2.5, range: false } });
const st = { banks: { USD: full("USD", 85, 1.07, 58), EUR: full("EUR", 70, 0.25, null), GBP: full("GBP", 90, -0.36, 0), AUD: full("AUD", -12, 0.34, -13),
                      NZD: full("NZD", 100, null, 56), CHF: full("CHF", 52, null, 52), JPY: full("JPY", 70, 1.42, -3), CAD: full("CAD", 83, -0.35, null) } };
eq(ctx.stanceRows(st, "m12").map((s) => s.bank).join(","), "NZD,GBP,USD,CAD,EUR,JPY,CHF,AUD", "default: Market 12M, the most hawkish first");
eq(ctx.stanceRows(st, "pressure").map((s) => s.bank).join(","), "JPY,USD,AUD,EUR,CAD,GBP,NZD,CHF", "Pressure, n/a last");
eq(ctx.stanceRows(st, "lens").map((s) => s.bank).join(","), "USD,NZD,CHF,GBP,JPY,AUD,EUR,CAD", "Bank vs market, n/a last");
const html = ctx.stanceTableHtml(st, "m12");
eq((html.match(/<tr class="cb-row"/g) || []).length, 8, "8 rows");
ok(html.indexOf("Cycle and market") > 0 && html.indexOf("Mandate check") > 0, "two column groups");
eq((ctx.stanceListHtml(st, "m12").match(/class="cb-st-item"/g) || []).length, 8, "phone: 8 cards");
ok(ctx.cycleCell(st.banks.USD).indexOf("↑2") === 0 || ctx.cycleCell(st.banks.USD).indexOf("↑2") > 0, "cycle cell ↑2 after ↓8");

// ---- Show calculation ----
const calc = { rate: 3.875, target: 2.0, band: null, pi_under: 3.1, pi_head: 3.6, gap_under: 1.1, gap_head: 1.6, y: 0.2, y_how: "-2.0 * (u 4.1 - u* 4.2)", x: 1.1667, x_headline: 1.6667,
  neutral: { mid: 3.2, lo: 2.9, hi: 3.9, asof: "2026-09-16", flag: "SEP_LONGER_RUN", stale: false }, neutral_check: { dots: 18, from_dots: 3.25, rounded: 3.2, published: 3.2, ok: true },
  S: 0.675, S_lo: -0.025, S_hi: 0.975, G: 1.075, G_lo: 0.775, G_hi: 1.775, pivot: 2.383, pi_12m: 2.5, pi_12m_period: "2027", pi_12m_basis: "underlying", gap_12m: 0.5,
  y_12m: 0.2, y_12m_how: "", x_12m: 0.5667, G_12m: 0.175, inputs: { rate: { source: "fed:DFEDTARL/U" }, pi_under: { asof: "2026-09" } } };
const s = { calc: calc, rate_display: { rate: 3.875, lower: 3.75, upper: 4.0, range: true }, chip: "S1 hike", situation: m("S1", { direction: 1 }), chip_12m: "S3 hold",
            situation_12m: m("S3", { direction: 1 }), stance: m(0.675), pivot: m(2.383), need_12m: m(0.5667), params: params };
const L = ctx.calcLines(s).join("\n").replace(/<[^>]+>/g, "");
ok(L.indexOf("i = 3.875% (midpoint of the target range 3.75–4.00%)") >= 0, "the midpoint only in the calculation");
ok(L.indexOf("gap = 3.1% − 2% = +1.10 pp") >= 0, "gap");
ok(L.indexOf("need x = gap + y/3 = +1.17 pp") >= 0, "need");
ok(L.indexOf("stance S = i − N = 3.875 − 3.2 = +0.68 pp (−0.03 to +0.97)") >= 0, "stance");
ok(L.indexOf("pressure G = 1.5 × +1.17 − +0.68 = +1.07 pp = +108 bp (+78 to +178)") >= 0, "pressure");
ok(L.indexOf("check: median of 18 longer-run dots 3.25 → rounded 3.2 vs published 3.2 ✓") >= 0, "SEP check");
ok(L.indexOf("pivot = π* + S/1.5 − y/3 = 2.38%") >= 0, "pivot");
ok(L.indexOf("G in 12 months = 1.5 × +0.57 − +0.68 = +0.17 pp") >= 0, "12 months");

// ---- history ----
ok(ctx.stanceHistoryHtml({ history: [{ date: "2026-11-14" }] }).indexOf("History starts on 14 Nov 2026.") > 0, "under 5 snapshots: the start date");
ok(ctx.stanceHistoryHtml({ history: [1, 2, 3, 4, 5].map((d) => ({ date: "2026-11-1" + d })) }).indexOf("cbStanceHist") > 0, "5 snapshots: the chart");

if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("stance: all checks passed");
