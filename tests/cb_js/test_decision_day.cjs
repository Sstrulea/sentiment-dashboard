// Decision day + "Up next" + the joint text on the real static/cb.js (plain Node, pieces cut out of the file).
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
                ";this.dayModeOf = dayModeOf; this.pickUpNext = pickUpNext; this.upNextHtml = upNextHtml; this.compareTableHtml = compareTableHtml; this.compareRows = compareRows;" +
                "this.sortRows = sortRows; this.jointText = jointText; this.compareListHtml = compareListHtml;", ctx);
let failures = 0;
const eq = (got, want, what) => { if (got !== want) { failures++; console.error("FAIL " + what + ": got " + JSON.stringify(got) + ", want " + JSON.stringify(want)); } };
const ok = (cond, what) => { if (!cond) { failures++; console.error("FAIL " + what); } };

// decision-day mode: today = the next meeting, or = the last recorded decision (stays on after the decision is in the data)
const bank = (next, last) => ({ bank: { tz: "America/New_York" }, summary: { next: next ? { decision: next, time: { tz: "America/New_York" } } : null, last_decision: last ? { date: last } : null } });
const today = (d) => () => d;
ok(ctx.dayModeOf(bank("2026-10-28", "2026-09-16"), today("2026-10-28")), "today is the next meeting");
ok(ctx.dayModeOf(bank("2026-12-09", "2026-10-28"), today("2026-10-28")), "today is the last recorded decision, next is the following meeting");
ok(!ctx.dayModeOf(bank("2026-12-09", "2026-10-28"), today("2026-10-29")), "the day after: off");
ok(!ctx.dayModeOf(bank(null, null), today("2026-10-28")), "nothing on record: off");

// Up next: the nearest decision not passed yet
const pt = (m, extra) => Object.assign({ meeting: m, rate: 2, cum_bp: 5, step_bp: 5, flag: "CURVE", est: false, prob: { dir: "hike", n: 0, p: 0.2 }, na: null, joint: null, includes: [] }, extra || {});
const pj = (ccy, next, sortUtc, points, endUtc) => ({ ccy: ccy, short: ccy + "B", href: "/central-banks/" + ccy.toLowerCase() + ".html", asof: "2026-10-08", data_asof: "2026-10-08", data_label: "X",
  current: { rate: 2, range: false }, next: { decision: next, effective: next, time: { utc: endUtc ? null : sortUtc, tz: "UTC", tbd: !!endUtc }, sort_utc: sortUtc, end_utc: endUtc || null },
  points: points, m12: { meeting: next, cum_bp: 5, moves: 0.2, est: false }, history: { "1w": { na: "x", points: [] }, "3w": { na: "x", points: [] } },
  delta: { "1w": { v: null, na: "x" }, "3w": { v: null, na: "x" } } });
const nzJoint = pt("2026-10-28", { rate: null, cum_bp: null, step_bp: null, prob: null, na: "not covered by any contract", flag: "ESTIMATE", est: true, joint: { meeting: "2026-12-09", step_bp: 43.7, cum_bp: 43.7 } });
const paths = {
  NZD: pj("NZD", "2026-10-28", "2026-10-28T01:00:00Z", [nzJoint, pt("2026-12-09", { step_bp: 43.7, cum_bp: 43.7, prob: null, flag: "ESTIMATE", est: true, includes: ["2026-10-28"] })]),
  CAD: pj("CAD", "2026-10-28", "2026-10-28T13:45:00Z", [pt("2026-10-28", { step_bp: 10 })]),
  USD: pj("USD", "2026-10-28", "2026-10-28T18:00:00Z", [pt("2026-10-28", { step_bp: 5.4 })]),
  JPY: pj("JPY", "2026-10-30", "2026-10-30T02:30:00Z", [pt("2026-10-30", { step_bp: 2 })], "2026-10-30T04:30:00Z")
};
eq(ctx.pickUpNext(paths, Date.parse("2026-10-28T02:00:00Z"), null).ccy, "CAD", "28 Oct 02:00Z: RBNZ has passed -> BoC");
eq(ctx.pickUpNext(paths, Date.parse("2026-10-09T12:00:00Z"), null).ccy, "NZD", "9 Oct: RBNZ first");
eq(ctx.pickUpNext(paths, Date.parse("2026-10-30T03:00:00Z"), null).ccy, "JPY", "BoJ inside its window is still up next");
eq(ctx.pickUpNext(paths, Date.parse("2026-10-30T05:00:00Z"), { ccy: "FALLBACK" }).ccy, "FALLBACK", "nothing left: the payload's next_decision");
const up = ctx.upNextHtml(ctx.pickUpNext(paths, Date.parse("2026-10-09T12:00:00Z"), null));
ok(up.indexOf("≈ +43.7 bp by 9 Dec, together with 28 Oct") > 0, "Up next shows the joint text: " + up);
ok(up.indexOf('title="No contract isolates this meeting; the 9 Dec estimate includes it."') > 0, "the joint tooltip");
ok(up.indexOf("cb-joint muted") > 0 && up.indexOf("not covered by any contract") < 0, "muted, not the n/a reason");

// the Compare table: the joint cell (muted, no tint), NZD last when sorted by the next move
const html = ctx.compareTableHtml(paths, "move");
const nzRow = html.slice(html.indexOf('data-ccy="NZD"'), html.indexOf("</tr>", html.indexOf('data-ccy="NZD"')));
ok(nzRow.indexOf("≈ +43.7 bp by 9 Dec, together with 28 Oct") > 0 && nzRow.indexOf("cb-joint muted") > 0, "Next move priced: the joint text");
ok(!/style="[^"]*background/.test(nzRow.split("cb-joint")[0].slice(-80)), "the joint cell carries no ScorePalette tint");
eq(ctx.sortRows(ctx.compareRows(paths), "move").map((r) => r.ccy).pop(), "NZD", "sorted by the next move, NZD stays last");
eq(ctx.sortRows(ctx.compareRows(paths), "next").map((r) => r.ccy).join(","), "NZD,CAD,USD,JPY", "sorted by next meeting = by sort_utc");
ok(html.indexOf('title="Market data 8 Oct · X"') > 0, "the data date on the bank cell");
ok(ctx.compareListHtml(paths).indexOf("together with 28 Oct") > 0, "the phone list shows the joint text");

if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("ok - decision day");
