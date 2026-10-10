// Rate paths v2 (RP style) on the real static/cb.js in plain Node: the moves in words, the Compare hover values at a date, the rows of the
// Upcoming meetings table, the Pairs over 12 months and the bank-page hover rows. The pieces are cut out of the file and run in a sandbox.
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
              document: { body: { classList: { contains: () => false } }, querySelectorAll: () => [] }, window: {},
              getComputedStyle: () => ({ getPropertyValue: () => "" }) };
vm.createContext(ctx);
vm.runInContext(cut("  const MINUS =", "  const state =") + cut("  function esc(s) {", "  // ---- rate / next meeting text") +
                cut("  function withAlpha(c, a)", "  function monthTicks(scale)") + cut("  function colors() {", "  function withAlpha(c, a)") +
                cut("  // ---- rate paths (stage 3)", "  // ---- bank page ---") +
                ";this.hikesText = hikesText; this.bankValueAt = bankValueAt; this.compareHoverRows = compareHoverRows; this.compareRows = compareRows; this.upcomingCells = upcomingCells;" +
                "this.upcomingTableHtml = upcomingTableHtml; this.upcomingListHtml = upcomingListHtml; this.pairCalc = pairCalc; this.sortPairRows = sortPairRows; this.pairsTableHtml = pairsTableHtml;" +
                "this.pairCards = pairCards; this.gapRow = gapRow; this.bankTipRows = bankTipRows; this.bankTipHtml = bankTipHtml; this.seriesTabsHtml = seriesTabsHtml; this.ms = ms;", ctx);
let failures = 0;
const eq = (got, want, what) => { if (got !== want) { failures++; console.error("FAIL " + what + ": got " + JSON.stringify(got) + ", want " + JSON.stringify(want)); } };
const ok = (cond, what) => { if (!cond) { failures++; console.error("FAIL " + what); } };
const near = (got, want, what) => ok(typeof got === "number" && Math.abs(got - want) < 1e-9, what + ": got " + got + ", want " + want);

// ---- the moves in words: one rule everywhere ----
eq(ctx.hikesText(10), "0 or 1 hike", "10 bp: f = 0.40 -> 0 or 1 hike");
eq(ctx.hikesText(37.6), "1 or 2 hikes", "37.6 bp: f = 0.504");
eq(ctx.hikesText(64.6), "2 or 3 hikes", "64.6 bp: f = 0.584");
eq(ctx.hikesText(103.3), "4 hikes", "103.3 bp: round(4.13)");
eq(ctx.hikesText(15), "0 or 1 hike", "15 bp: f = 0.60 is inside the band");
eq(ctx.hikesText(16), "1 hike", "16 bp: f = 0.64 -> round = 1");
eq(ctx.hikesText(9), "no change", "9 bp: round(0.36) = 0");
eq(ctx.hikesText(0), "no change", "0 bp");
eq(ctx.hikesText(-10), "0 or 1 cut", "-10 bp");
eq(ctx.hikesText(-64.6), "2 or 3 cuts", "-64.6 bp");
eq(ctx.hikesText(-25), "1 cut", "-25 bp");
eq(ctx.hikesText(-103.3), "4 cuts", "-103.3 bp");
eq(ctx.hikesText(null), "", "n/a");
ok(src.split("hikes\"").length < 6 && !/toFixed\(2\) \+ " " \+ word/.test(src), "no second moves-in-words rule left in cb.js");

// ---- fixtures ----
const pt = (m, rate, cum, step, extra) => Object.assign({ meeting: m, effective: m, rate: rate, cum_bp: cum, step_bp: step, flag: "CURVE", method: "CURVE", est: false,
  prob: step === null ? null : { dir: step >= 0 ? "hike" : "cut", n: Math.floor(Math.abs(step) / 25), p: (Math.abs(step) % 25) / 25 }, na: null, joint: null, includes: [] }, extra || {});
const pj = (ccy, short, name, rate, next, points, m12, d1, d3, hist) => ({ ccy: ccy, short: short, name: name, href: "/central-banks/" + ccy.toLowerCase() + ".html", asof: "2026-10-09",
  data_asof: "2026-10-09", data_label: "X", current: { rate: rate, range: false }, next: next ? { decision: next, effective: next, time: { utc: next + "T12:00:00Z", tz: "UTC" }, sort_utc: next + "T12:00:00Z" } : null,
  points: points, m12: m12, history: hist || { "1w": { asof: "2026-10-02", na: "history starts 2026-10-08", points: [] }, "3w": { asof: "2026-09-18", na: "history starts 2026-10-08", points: [] } },
  delta: { "1w": d1, "3w": d3 }, spread_note: null });
const NA1 = { v: null, flag: null, na: "history starts 2026-10-08" };
const usd = pj("USD", "Fed", "Federal Reserve", 3.875, "2026-10-28",
  [pt("2026-10-28", 3.964, 8.9, 8.9, { prob: { dir: "hike", n: 0, p: 0.356 } }), pt("2026-12-09", 4.128, 25.3, 16.4), pt("2027-09-15", 4.73, 85.5, 9.4)],
  { meeting: "2027-09-15", cum_bp: 85.5, moves: 3.42, est: false, flag: "CURVE" }, { v: 3, flag: "CURVE", na: null }, NA1,
  { "1w": { asof: "2026-10-02", na: null, points: [{ meeting: "2026-10-28", rate: 3.95, cum_bp: 7.5, est: false, na: null }, { meeting: "2026-12-09", rate: null, cum_bp: null, est: false, na: "not priced on 2026-10-02" }, { meeting: "2027-09-15", rate: 4.7, cum_bp: 82.5, est: false, na: null }] },
    "3w": { asof: "2026-09-18", na: "history starts 2026-10-08 (needs 15 business days back to 2026-09-18)", points: [] } });
const eur = pj("EUR", "ECB", "European Central Bank", 2.5, "2026-10-29",
  [pt("2026-10-29", 2.528, 2.8, 2.8), pt("2027-09-09", 3.229, 72.9, 10)], { meeting: "2027-09-09", cum_bp: 72.9, moves: 2.92, est: false, flag: "CURVE" }, { v: 1, flag: "CURVE", na: null }, NA1);
const gbp = pj("GBP", "BoE", "Bank of England", 3.75, "2026-11-05",
  [pt("2026-11-05", 4.0, 25, 25, { prob: { dir: "hike", n: 1, p: 0.2 } }), pt("2027-09-16", 4.783, 103.3, 30)], { meeting: "2027-09-16", cum_bp: 103.3, moves: 4.13, est: false, flag: "CURVE" }, NA1, NA1);
const nzd = pj("NZD", "RBNZ", "Reserve Bank of New Zealand", 2.75, "2026-10-28",
  [pt("2026-10-28", null, null, null, { est: true, flag: "ESTIMATE", method: "FIT", na: "not covered by any contract", joint: { meeting: "2026-12-09", step_bp: 44.82, cum_bp: 44.82 } }),
   pt("2026-12-09", 3.198, 44.82, 44.82, { est: true, flag: "ESTIMATE", method: "FIT", prob: null, includes: ["2026-10-28"] })],
  { meeting: "2026-12-09", cum_bp: 44.82, moves: 1.79, est: true, flag: "ESTIMATE" }, { v: -8, flag: "ESTIMATE", na: null }, NA1);
const chf = pj("CHF", "SNB", "Swiss National Bank", 0, "2026-12-10", [pt("2026-12-10", 0.0, 0, 0, { prob: { dir: "hold", n: 0, p: 0 } })], { meeting: "2026-12-10", cum_bp: 0, moves: 0, est: false, flag: "CURVE" }, NA1, NA1);
const paths = { USD: usd, EUR: eur, GBP: gbp, NZD: nzd, CHF: chf };
const D = (iso) => ctx.ms(iso);

// ---- Compare hover: the value at a date ----
const v0 = ctx.bankValueAt(usd, D("2026-10-20"));
ok(v0.bp === 0 && v0.level === 3.875 && v0.meeting === null, "before the first meeting: 0 bp at the current rate");
near(ctx.bankValueAt(usd, D("2026-10-28")).bp, 8.9, "on the decision date: that meeting");
near(ctx.bankValueAt(usd, D("2027-03-01")).bp, 25.3, "between meetings: the last meeting on or before the date");
near(ctx.bankValueAt(usd, D("2027-10-01")).bp, 85.5, "after the last meeting: the last value");
const vNz = ctx.bankValueAt(nzd, D("2026-11-15"));
ok(vNz.bp === null && vNz.na === "not covered by any contract", "an n/a meeting is n/a (—) with its reason");
ok(ctx.bankValueAt(nzd, D("2026-12-09")).est === true, "an estimate is flagged");
const rows = ctx.compareHoverRows(paths, D("2026-11-15"), "bp", {});
eq(rows.map((r) => r.ccy).join(","), "GBP,USD,EUR,CHF,NZD", "sorted high to low, n/a last");
eq(rows[0].text, "BoE +25.0 bp · 1 hike", "row text: name, bp, hikes");
eq(rows[4].text, "RBNZ —", "n/a row");
const rows2 = ctx.compareHoverRows(paths, D("2027-09-20"), "bp", { GBP: true });
ok(rows2.every((r) => r.ccy !== "GBP"), "a bank hidden in the legend is left out");
eq(rows2.find((r) => r.ccy === "NZD").text, "RBNZ ≈ +44.8 bp · 2 hikes", "estimates carry ≈");
eq(ctx.compareHoverRows(paths, D("2027-09-20"), "level", {})[0].text, "BoE 4.78% · +103.3 bp · 4 hikes", "level mode: level · bp · hikes");
eq(ctx.compareHoverRows(paths, D("2027-09-20"), "level", {}).map((r) => r.ccy).join(","), "GBP,USD,EUR,NZD,CHF", "level mode sorts by the level");

// ---- Upcoming meetings: the rows ----
const byCcy = {};
ctx.compareRows(paths).forEach((r) => { byCcy[r.ccy] = ctx.upcomingCells(r); });
eq(byCcy.USD.prob, "35.6%", "Probability with one decimal (n = 0)");
eq(byCcy.USD.dir, "hike", "Hike/Cut");
eq(byCcy.USD.move, "+8.9", "Priced move = step_bp");
eq(byCcy.USD.likely, "HOLD", "Most likely: 35.6% of a hike -> HOLD");
eq(byCcy.GBP.prob, "+25 bp + 20.0% of +50", "Probability at n >= 1 keeps the existing text");
eq(byCcy.GBP.likely, "HIKE", "Most likely: 80% of +25 -> HIKE");
eq(byCcy.CHF.prob, null, "a hold has no probability");
eq(byCcy.CHF.dir, null, "a hold has no direction");
eq(byCcy.CHF.likely, "HOLD", "a hold is HOLD");
eq(byCcy.NZD.move, "≈ +44.8 (by 9 Dec)", "NZD: the joint estimate");
ok(byCcy.NZD.joint && byCcy.NZD.prob === null && byCcy.NZD.likely === null, "NZD: muted joint, no probability, no most likely");
eq(byCcy.USD.m12, "+85.5", "12M with one decimal");
eq(byCcy.USD.m12Tip, "after the 15 Sep 2027 meeting · 3 or 4 hikes", "12M tooltip");
eq(byCcy.NZD.m12, "≈ +44.8", "12M of an estimate");
const big = JSON.parse(JSON.stringify(gbp)); big.points[0].prob = { dir: "hike", n: 1, p: 0.7 };
eq(ctx.upcomingCells(ctx.compareRows({ GBP: big })[0]).likely, "HIKE 50", "Most likely: 70% of +50 -> HIKE 50");
const ecut = JSON.parse(JSON.stringify(eur)); ecut.points[0] = Object.assign(ecut.points[0], { step_bp: -15, prob: { dir: "cut", n: 0, p: 0.6 } });
eq(ctx.upcomingCells(ctx.compareRows({ EUR: ecut })[0]).likely, "CUT", "Most likely: a cut");
const html = ctx.upcomingTableHtml(paths, "next");
ok(html.indexOf("<h3>Upcoming meetings</h3>") >= 0 && html.indexOf('class="cb-pill active" data-sort="next"') > 0, "title and the active sort pill");
ok(html.indexOf(">Date</button>") > 0 && html.indexOf(">Priced move</button>") > 0 && html.indexOf(">12M</button>") > 0, "the three sort pills");
ok(html.indexOf("Federal Reserve") > 0 && html.indexOf(">28 Oct 2026</td>") > 0, "full bank name and the date only");
ok(!/style="[^"]*background/.test(html), "no coloured cell backgrounds");
ok(/<td class="cb-n cb-hawk" title="Change in the implied rate[^"]*">\+8\.9<\/td>/.test(html), "Priced move: blue text and its tooltip");
ok(/<td class="cb-n cb-dove">≈ −8\.0<\/td>/.test(html), "Δ 1w of an estimate: red text with ≈");
ok(html.indexOf('<span class="cb-joint muted" title="No contract isolates this meeting; the 9 Dec estimate includes it.">≈ +44.8 (by 9 Dec)</span>') > 0, "NZD priced move: muted, joint tooltip");
const list = ctx.upcomingListHtml(paths, "m12");
ok(list.indexOf('class="cb-pill active" data-sort="m12"') > 0 && list.indexOf("35.6% hike · HOLD") > 0 && list.indexOf("≈ +44.8 (by 9 Dec)") > 0, "the phone list has the same values");
ok(list.indexOf("Bank of England") < list.indexOf("Federal Reserve"), "the phone list follows the sort");

// ---- Pairs over 12 months ----
const p = ctx.pairCalc({ pair: "EURUSD", display: "EUR/USD", href: "/central-banks/pair/eurusd.html", base: "EUR", quote: "USD" }, paths);
near(p.gap, -137.5, "Rate gap now = (base - quote) x 100");
eq(p.rates, "2.50% vs 3.88%", "the two rates");
eq(p.priced, "ECB +73 · Fed +86", "Priced 12M of each bank");
near(p.change, 72.9 - 85.5, "12M change = base 12M - quote 12M");
eq(p.favors, "USD", "negative favors the quote");
near(p.gap12, -137.5 + 72.9 - 85.5, "Rate gap in 12M = gap now + 12M change");
near(p.d1w.v, 1 - 3, "Δ 1w = base Δ - quote Δ");
eq(p.d1w.toward, "USD", "Δ toward the quote");
eq(p.d3w.v, null, "Δ 3w n/a");
eq(p.d3w.na, "history starts 2026-10-08 (both legs)", "a reason shared by both legs, once");
const pn = ctx.pairCalc({ pair: "NZDUSD", display: "NZD/USD", href: "/x", base: "NZD", quote: "USD" }, paths);
ok(pn.est && pn.favors === "USD" && Math.abs(pn.change - (44.82 - 85.5)) < 1e-9, "an estimate leg marks the pair");
eq(pn.priced, "RBNZ ≈ +45 · Fed +86", "the estimated leg carries ≈");
const pg = ctx.pairCalc({ pair: "GBPEUR", display: "GBP/EUR", href: "/y", base: "GBP", quote: "EUR" }, paths);
eq(pg.favors, "GBP", "positive favors the base");
eq(pg.d1w.na, "GBP: history starts 2026-10-08", "Δ n/a: the reason of the leg that is n/a");
const missing = ctx.pairCalc({ pair: "EURCHF", display: "EUR/CHF", href: "/z", base: "EUR", quote: "CHF" }, { EUR: eur, CHF: Object.assign({}, chf, { m12: { cum_bp: null, na: "no meeting in the next 12 months" } }) });
ok(missing.change === null && missing.changeNa === "CHF: no meeting in the next 12 months", "12M change n/a with the reason");
const prs = [pn, p, pg, missing];
eq(ctx.sortPairRows(prs, "change").map((r) => r.pair).join(","), "NZDUSD,GBPEUR,EURUSD,EURCHF", "default sort: |12M change| descending, n/a last");
eq(ctx.sortPairRows(prs, "gap").map((r) => r.pair).join(","), "EURCHF,GBPEUR,NZDUSD,EURUSD", "sort by Rate gap now");
const ph = ctx.pairsTableHtml(prs, "change");
ok(ph.indexOf("ECB vs Fed") > 0 && ph.indexOf("favors USD") > 0 && ph.indexOf("≈ −40.7") > 0, "Pairs table: names, favors, ≈");
ok(ph.indexOf('title="toward USD (Δ of the base bank') > 0, "Δ tooltip: toward");
ok(ph.indexOf('class="cb-pill active" data-sort="change"') > 0 && !/style="[^"]*background/.test(ph), "pills; no backgrounds");
ok(ctx.pairCards(p).indexOf("favors USD") > 0 && ctx.pairCards(p).indexOf("−137.5 bp") > 0, "the pair page cards");
eq(ctx.gapRow(eur, usd, D("2026-10-30")).text, "Gap −143.6 bp", "pair hover: the Gap row from the two levels in force");
eq(ctx.gapRow(nzd, usd, D("2026-11-01")).text, "Gap —", "pair hover: Gap n/a when a leg is n/a");
ok(src.indexOf("cbGapChart") < 0 && src.indexOf("function drawGapChart") < 0, "the pair page has no Rate gap chart");

// ---- the bank page hover ----
const tr = ctx.bankTipRows(usd, "2026-10-28", {});
eq(tr.map((r) => r.text).join(" | "), "Current 3.96% · +8.9 bp · no change | 1w ago 3.95% · +7.5 bp · no change | 3w ago —", "Current / 1w / 3w rows");
eq(ctx.bankTipRows(usd, "2027-09-15", {})[1].text, "1w ago 4.70% · +82.5 bp · 3 hikes", "1w ago: Δ vs today's rate and the moves");
eq(ctx.bankTipRows(usd, "2026-12-09", {})[1].text, "1w ago —", "a meeting not priced then: —");
eq(ctx.bankTipRows(usd, "2026-10-28", { "1w": true }).map((r) => r.key).join(","), "now,3w", "a series hidden by its tab is left out");
eq(ctx.bankTipRows(nzd, "2026-12-09", {})[0].text, "Current ≈ 3.20% · +44.8 bp · 2 hikes", "an estimate carries ≈");
const th = ctx.bankTipHtml(usd, "2026-10-28", {});
ok(th.indexOf("<th>Level</th><th>Δ vs today</th><th>Hikes</th>") > 0 && th.indexOf(">28 Oct 2026<") > 0, "the mini-table: title + columns");
ok(th.indexOf('<tr title="history starts 2026-10-08"><td>') > 0, "the missing series row keeps its reason");
const tabs = ctx.seriesTabsHtml(usd, {});
ok(tabs.indexOf(">Current</button>") > 0 && tabs.indexOf(">1w ago · 2 Oct</button>") > 0 && tabs.indexOf(">3w ago · 18 Sep</button>") > 0, "tab labels with the dates");
ok(/class="cb-stab cb-stab-na" data-series="3w" aria-disabled="true" title="history starts 2026-10-08"/.test(tabs), "an n/a series is struck through with the reason");
ok(/class="cb-stab" data-series="1w" aria-pressed="false"/.test(ctx.seriesTabsHtml(usd, { "1w": true })), "a hidden series is not active");

if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("ok - rp style");
