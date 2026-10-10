// The bank legend above the Compare / pair chart on the real static/cb.js in plain Node: a toggle hides the bank from the chart and from
// the hover card, "Show all" brings every bank back, hovering a toggle fades the other lines but never changes what is hidden.
// drawCompareChart runs against a stand-in Chart class, so the hover card checked here is the one the page builds.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "cb.js"), "utf8");

function cut(from, to) {
  const a = src.indexOf(from), b = src.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error("not found in cb.js: " + from + " .. " + to);
  return src.slice(a, b);
}
class FakeChart {
  constructor(canvas, cfg) { this.canvas = canvas; this.data = cfg.data; this.options = cfg.options; this.updates = 0; }
  isDatasetVisible(i) { return !this.data.datasets[i].hidden; }
  update() { this.updates++; }
  destroy() {}
}
const tip = { hidden: true, innerHTML: "", style: {}, setAttribute() {}, offsetWidth: 100, offsetHeight: 80, parentNode: { clientWidth: 800, clientHeight: 400 } };
const canvas = { offsetLeft: 0, offsetTop: 0, parentNode: { querySelector: () => tip, appendChild() {} } };
const ctx = { CFG: { urls: { overview_page: "/central-banks.html" } }, SP: undefined, state: { meta: { flags: {} }, charts: [], redraw: [] }, CHEV: "", Chart: FakeChart,
              document: { body: { classList: { contains: () => false } }, querySelectorAll: () => [] }, window: {},
              getComputedStyle: () => ({ getPropertyValue: () => "" }) };
vm.createContext(ctx);
vm.runInContext(cut("  const MINUS =", "  const state =") + cut("  function esc(s) {", "  // ---- rate / next meeting text") +
                cut("  function colors() {", "  function destroyCharts()") + cut("  // ---- rate paths (stage 3)", "  // ---- bank page ---") +
                ";this.legendState = legendState; this.legendHtml = legendHtml; this.compareHoverRows = compareHoverRows; this.drawCompareChart = drawCompareChart; this.ms = ms;", ctx);
let failures = 0;
const eq = (got, want, what) => { if (got !== want) { failures++; console.error("FAIL " + what + ": got " + JSON.stringify(got) + ", want " + JSON.stringify(want)); } };
const ok = (cond, what) => { if (!cond) { failures++; console.error("FAIL " + what); } };

const pt = (m, rate, cum) => ({ meeting: m, effective: m, rate: rate, cum_bp: cum, step_bp: cum, flag: "CURVE", est: false, prob: null, na: null, joint: null, includes: [] });
const pj = (ccy, short, rate, cum) => ({ ccy: ccy, short: short, name: short + " bank", asof: "2026-10-09", current: { rate: rate }, points: [pt("2026-11-05", rate + cum / 100, cum)] });
const paths = { USD: pj("USD", "Fed", 3.875, 10), GBP: pj("GBP", "BoE", 3.75, 25), NZD: pj("NZD", "RBNZ", 2.75, 40), CHF: pj("CHF", "SNB", 0, 5) };
const T = ctx.ms("2026-12-01");
const names = (rows) => rows.map((r) => r.ccy).join(",");

// the state: toggle / Show all / hover
const st = ctx.legendState(["CHF", "NZD", "USD", "GBP"]);
eq(st.ccys.join(","), "USD,GBP,NZD,CHF", "toggles follow BANK_ORDER");
st.toggle("GBP"); st.toggle("NZD");
ok(st.hidden.GBP && st.hidden.NZD && st.anyHidden(), "two banks hidden");
eq(names(ctx.compareHoverRows(paths, T, "bp", st.hidden)), "USD,CHF", "a hidden bank leaves the hover rows");
st.toggle("NZD");
ok(!st.hidden.NZD && st.hidden.GBP, "a second click shows the bank again");
st.toggle("XXX");
ok(!st.hidden.XXX, "an unknown code is ignored");
let html = ctx.legendHtml(st, { GBP: "Bank of England" });
ok(html.indexOf('data-ccy="GBP" aria-pressed="false" title="Hide') < 0 && /class="cb-lg-item off" data-ccy="GBP" aria-pressed="false" title="Show Bank of England"/.test(html), "a hidden bank: off, aria-pressed=false");
ok(/<button type="button" class="cb-lg-item" data-ccy="USD" aria-pressed="true"/.test(html), "toggles are real buttons with aria-pressed");
ok(html.indexOf(">Show all</button>") > 0, "Show all appears while a bank is hidden");
ok(html.indexOf("Bank of England") < 0 || html.indexOf(">Bank of England<") < 0, "the toggle shows the code, not the name");
const before = JSON.stringify(st.hidden);
st.hover("USD");
eq(JSON.stringify(st.hidden), before, "hovering a toggle does not change what is hidden");
eq(st.alpha("USD"), 1, "the hovered bank stays at full colour");
eq(st.alpha("CHF"), 0.2, "the other banks fade to 20%");
eq(names(ctx.compareHoverRows(paths, T, "bp", st.hidden)), "NZD,USD,CHF", "hovering a toggle does not change the hover rows");
st.hover("GBP");
eq(st.focus, null, "hovering a hidden bank fades nothing");
st.hover(null);
eq(st.alpha("CHF"), 1, "leaving the toggle restores every line");
st.showAll();
ok(!st.anyHidden() && ctx.legendHtml(st).indexOf("Show all") < 0, "Show all brings every bank back and disappears");
eq(names(ctx.compareHoverRows(paths, T, "bp", st.hidden)), "NZD,GBP,USD,CHF", "every bank back in the hover rows");

// the chart: hidden banks are not drawn and are not in the hover card the page builds
const lg = ctx.legendState(Object.keys(paths));
lg.toggle("GBP");
let chart = ctx.drawCompareChart(canvas, paths, "bp", { legend: lg });
ok(chart.data.datasets.find((d) => d.cbCcy === "GBP").hidden === true && chart.data.datasets.filter((d) => d.hidden).length === 1, "the hidden bank's line is not drawn");
eq(chart.options.plugins.legend.display, false, "no Chart.js legend");
chart.$cb.show(chart, { x: 100, y: 100, xv: T, yv: 10 });
ok(tip.innerHTML.indexOf("BoE") < 0 && tip.innerHTML.indexOf("RBNZ") > 0 && tip.innerHTML.indexOf("Fed") > 0, "the hover card leaves the hidden bank out");
lg.hover("USD");
chart = ctx.drawCompareChart(canvas, paths, "bp", { legend: lg });
ok(/rgba\([^)]*,0\.2\)/.test(chart.data.datasets.find((d) => d.cbCcy === "NZD").borderColor) && !/rgba/.test(chart.data.datasets.find((d) => d.cbCcy === "USD").borderColor), "focus: the others at 20%, the focused bank normal");
eq(chart.data.datasets.find((d) => d.cbCcy === "GBP").hidden, true, "focus keeps the hidden bank hidden");
lg.hover(null); lg.showAll();
chart = ctx.drawCompareChart(canvas, paths, "bp", { legend: lg });
chart.$cb.show(chart, { x: 100, y: 100, xv: T, yv: 10 });
ok(chart.data.datasets.every((d) => !d.hidden) && tip.innerHTML.indexOf("BoE") > 0, "after Show all the bank is drawn and in the card again");

if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("ok - chart legend");
