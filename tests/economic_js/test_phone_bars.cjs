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
// AUD/JPY, as on the board: Monetary · 2Y, COT, Labour, Inflation, Growth = +1.65
const aj = mod.scoreGroups(payload.instruments.find((i) => i.symbol === "AUDJPY"));
assert.strictEqual(JSON.stringify(aj.map((x) => x.label)), JSON.stringify(["Monetary · 2Y", "COT", "Labour", "Inflation", "Growth"]));
assert.strictEqual(aj.map((x) => x.value.toFixed(2)).join(","), "1.25,0.73,-0.63,0.19,0.10");
assert.strictEqual(aj.reduce((s, x) => s + x.value, 0).toFixed(2), "1.65");

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
