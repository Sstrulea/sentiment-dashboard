// Bank page: "By meeting" next to the chart, on the real static/cb.js in plain Node. The linked hover in both directions (a row shows the
// crosshair + card at its meeting and enlarges the point; the chart highlights the row of the meeting under the cursor) and the method badge
// of the table (hidden for EXACT / CURVE - the method goes into the date tooltip - visible for ESTIMATE).
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
  constructor(canvas, cfg) { this.canvas = canvas; this.data = cfg.data; this.options = cfg.options; this.active = []; this.updates = 0;
    this.chartArea = { left: 50, right: 650, top: 10, bottom: 410 };
    const x0 = Date.UTC(2026, 9, 9), x1 = Date.UTC(2027, 9, 1);
    this.scales = { x: { getPixelForValue: (v) => 50 + (v - x0) / (x1 - x0) * 600, getValueForPixel: (px) => x0 + (px - 50) / 600 * (x1 - x0) },
                    y: { getPixelForValue: (v) => 410 - (v - 3.8) * 400, getValueForPixel: (py) => 3.8 + (410 - py) / 400 } }; }
  isDatasetVisible(i) { return !this.data.datasets[i].hidden; }
  setActiveElements(a) { this.active = a; }
  getActiveElements() { return this.active; }
  update() { this.updates++; }
  destroy() {}
}
const tip = { hidden: true, innerHTML: "", style: {}, setAttribute() {}, offsetWidth: 100, offsetHeight: 80, parentNode: { clientWidth: 700, clientHeight: 420 } };
const canvas = { offsetLeft: 0, offsetTop: 0, parentNode: { querySelector: () => tip, appendChild() {} } };
const ctx = { CFG: { urls: { overview_page: "/central-banks.html" } }, SP: undefined, root: null, metaEl: null, titleEl: null, window: {}, Chart: FakeChart,
              document: { body: { classList: { contains: () => false } }, querySelectorAll: () => [], addEventListener() {} },
              getComputedStyle: () => ({ getPropertyValue: () => "" }), setInterval() {} };
vm.createContext(ctx);
vm.runInContext(cut("  const MINUS =", "  // ---- boot ---") + ";this.state = state; this.drawPathChart = drawPathChart; this.showAtMeeting = showAtMeeting; this.hideAtMeeting = hideAtMeeting;" +
                "this.highlightRow = highlightRow; this.wireLinkedHover = wireLinkedHover; this.pathTableHtml = pathTableHtml; this.ms = ms;", ctx);
let failures = 0;
const eq = (got, want, what) => { if (got !== want) { failures++; console.error("FAIL " + what + ": got " + JSON.stringify(got) + ", want " + JSON.stringify(want)); } };
const ok = (cond, what) => { if (!cond) { failures++; console.error("FAIL " + what); } };

ctx.state.meta = { flags: { CURVE: { label: "CURVE", help: "OIS curve" }, EXACT: { label: "EXACT", help: "one contract per meeting" }, ESTIMATE: { label: "ESTIMATE", help: "fitted" } } };
const pt = (m, rate, cum, flag) => ({ meeting: m, effective: m, rate: rate, cum_bp: cum, step_bp: cum, flag: flag, est: flag === "ESTIMATE", prob: flag === "ESTIMATE" ? null : { dir: "hike", n: 0, p: 0.2 }, na: null });
const pj = { ccy: "USD", asof: "2026-10-09", na: null, current: { rate: 3.875 },
  points: [pt("2026-10-28", 3.93, 5.5, "CURVE"), pt("2026-12-09", 4.13, 25.3, "EXACT"), pt("2027-04-28", 4.52, 64.1, "ESTIMATE")],
  history: { "1w": { asof: "2026-10-02", na: "history starts 2026-10-08", points: [] }, "3w": { asof: "2026-09-18", na: "history starts 2026-10-08", points: [] } } };

// ---- the method badge ----
const t = ctx.pathTableHtml(pj, []);
ok(t.indexOf("cb-flag-CURVE") < 0 && t.indexOf("cb-flag-EXACT") < 0, "no badge for CURVE / EXACT");
ok(t.indexOf("cb-flag-ESTIMATE") > 0, "the ESTIMATE badge stays");
ok(t.indexOf('title="Method: CURVE (OIS curve)"') > 0 && t.indexOf('title="Method: EXACT (one contract per meeting)"') > 0, "CURVE / EXACT go into the date tooltip");
ok(t.indexOf('<tr data-meeting="2026-12-09">') >= 0, "rows carry their meeting");

// ---- chart -> row ----
const highlighted = [];
const chart = ctx.drawPathChart(canvas, pj, false, {}, (m) => highlighted.push(m));
chart.$cb.show(chart, { x: 300, y: 200, xv: ctx.ms("2026-12-09"), yv: 4.1 });
eq(highlighted.pop(), "2026-12-09", "hovering the chart reports the meeting under the cursor");
chart.$cb.hide();
eq(highlighted.pop(), null, "leaving the chart clears it");
const rows = ["2026-10-28", "2026-12-09", "2027-04-28"].map((m) => ({ dataset: { meeting: m }, cls: new Set(), classList: null, listeners: {} }));
rows.forEach((r) => { r.classList = { toggle: (c, on) => (on ? r.cls.add(c) : r.cls.delete(c)) }; r.addEventListener = (ev, fn) => { r.listeners[ev] = fn; }; });
const scope = { querySelectorAll: (q) => (q === "tr[data-meeting]" ? rows : []) };
ctx.highlightRow(scope, "2026-12-09");
ok(rows[1].cls.has("cb-row-hl") && !rows[0].cls.has("cb-row-hl") && !rows[2].cls.has("cb-row-hl"), "the row of that meeting is highlighted, only it");
ctx.highlightRow(scope, null);
ok(rows.every((r) => !r.cls.has("cb-row-hl")), "cleared");

// ---- row -> chart ----
const c2 = ctx.drawPathChart(canvas, pj, false, {}, (m) => ctx.highlightRow(scope, m));
ctx.wireLinkedHover(scope, () => c2);
rows[2].listeners.mouseenter();
const idx = c2.data.datasets[0].data.findIndex((p) => p.x === ctx.ms("2027-04-28"));
eq(JSON.stringify(c2.getActiveElements()), JSON.stringify([{ datasetIndex: 0, index: idx }]), "the row enlarges its point (active element)");
ok(c2.$cross && Math.abs(c2.$cross.xv - ctx.ms("2027-04-28")) < 1 && Math.abs(c2.$cross.yv - 4.52) < 1e-9, "the crosshair sits at the meeting and its level");
ok(!tip.hidden && tip.innerHTML.indexOf("28 Apr 2027") > 0 && tip.innerHTML.indexOf("<th>Level</th>") > 0, "the card of that meeting is shown");
ok(rows[2].cls.has("cb-row-hl"), "and the row stays highlighted");
rows[2].listeners.mouseleave();
ok(c2.$cross === null && c2.getActiveElements().length === 0 && tip.hidden && rows.every((r) => !r.cls.has("cb-row-hl")), "leaving the row clears the chart and the row");
const c3 = ctx.drawPathChart(canvas, pj, false, { now: true }, null);
ok(ctx.showAtMeeting(c3, "2026-10-28") && c3.getActiveElements().length === 0, "Current hidden: crosshair and card, no point to enlarge");

if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("ok - bank side by side");
