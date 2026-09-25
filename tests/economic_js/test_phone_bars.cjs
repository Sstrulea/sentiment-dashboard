// Faza 11B: the phone detail sheet's "What moves the score" bars.
// FX / DXY: the contributions summed per group add up to inst.score exactly (all 29 rows).
// Cross-asset: bars only when the factors add up to score_precise exactly; otherwise none.
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..", "..");
const sandbox = { module: { exports: {} }, window: {}, document: { readyState: "loading", addEventListener: () => {} }, console };
vm.createContext(sandbox);
for (const f of ["score-palette.js", "economic-chart.js"]) {
  vm.runInContext(fs.readFileSync(path.join(ROOT, "static", f), "utf8"), sandbox, { filename: f });
}
const mod = sandbox.module.exports;
const payload = JSON.parse(fs.readFileSync(path.join(ROOT, "public", "data", "economic.json"), "utf8"));

assert.strictEqual(payload.instruments.length, 29, "29 rows (28 pairs + DXY)");
for (const inst of payload.instruments) {
  const g = mod.scoreGroups(inst);
  const sum = g.reduce((s, x) => s + x.value, 0);
  assert.ok(Math.abs(sum - inst.score) < 1e-9, inst.symbol + ": bars " + sum + " != score " + inst.score);
  assert.ok(g.every((x) => Math.abs(x.value) > 1e-12), inst.symbol + ": no zero group");
  for (let i = 1; i < g.length; i++) assert.ok(Math.abs(g[i - 1].value) >= Math.abs(g[i].value), inst.symbol + ": sorted by |value|");
}
// The grouping behaviour, pinned on a FIXED row shaped like the board's AUD/JPY (key /
// category / contribution). Not on live data: public/data/economic.json moves with every
// refresh, and a snapshot of the live AUDJPY row broke on routine data twice (PR #26, and
// 2026-09-25 after a manual JPY core CPI entry). The live rows are covered above by the
// invariants that must hold on any data (bars add up to the score, no zero group, sorted).
// Pinned here: contributions summed per category, the sentiment row = COT, a group netting
// to 0 gets no bar (Growth), bars sorted by |value|, the group labels.
const fixed = {
  symbol: "FIXED", score: 1.66,
  contributions: [
    { key: "rate_expectations", category: "monetary", contribution: 1.25 },
    { key: "cpi_yoy", category: "inflation", contribution: 0.25 },
    { key: "core_cpi", category: "inflation", contribution: 0.06 },
    { key: "unemployment_rate", category: "labour", contribution: -0.315 },
    { key: "wage_growth", category: "labour", contribution: -0.315 },
    { key: "retail_sales", category: "growth", contribution: 0.156 },
    { key: "capital_expenditure", category: "growth", contribution: -0.156 },
    { key: "sentiment", category: null, contribution: 0.73 },
  ],
};
const fg = mod.scoreGroups(fixed);
assert.strictEqual(JSON.stringify(fg.map((x) => x.label)), JSON.stringify(["Monetary · 2Y", "COT", "Labour", "Inflation"]));
assert.strictEqual(fg.map((x) => x.value.toFixed(2)).join(","), "1.25,0.73,-0.63,0.31");
assert.ok(Math.abs(fg.reduce((s, x) => s + x.value, 0) - fixed.score) < 1e-9, "fixed row: bars add up to the score");

// cross-asset: bars iff exact
const ca = (payload.crossasset || {}).instruments || [];
let shown = 0;
for (const inst of ca) {
  const bars = mod.caBars(inst);
  if (bars) {
    shown++;
    const sum = bars.reduce((s, x) => s + x.value, 0);
    assert.ok(Math.abs(sum - inst.score_precise) < 1e-9, inst.symbol + ": shown bars must add up to score_precise");
  }
}
// synthetic: a factor set that does add up shows its bars, one that does not shows none
const exact = { score_precise: 1.5, factors: [{ name: "growth", present: true, contribution: 1 }, { name: "rates", present: true, contribution: 0.5 }] };
assert.ok(mod.caBars(exact) && mod.caBars(exact).length === 2);
assert.strictEqual(mod.caBars({ score_precise: 2, factors: exact.factors }), null);
console.log("phone bars: ok (" + payload.instruments.length + " rows exact; cross-asset bars shown for " + shown + "/" + ca.length + ")");
