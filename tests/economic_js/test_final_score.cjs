// /economic tables (2026-10-08): the Score column is the final −10..+10 integer, sorted
// by final_score then z; its tooltip gives raw score, RMS and z; the details carry
// "raw score → z → Score".
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const ROOT = path.join(__dirname, "..", "..");
const sb = { module: { exports: {} }, window: {}, document: { readyState: "loading", addEventListener: () => {} }, console };
vm.createContext(sb);
for (const f of ["score-palette.js", "economic-chart.js"]) vm.runInContext(fs.readFileSync(path.join(ROOT, "static", f), "utf8"), sb, { filename: f });
const ec = sb.module.exports;
const insts = [
  { symbol: "A", score: 2.0, z: 0.80, final_score: 3, rms: 2.5 },
  { symbol: "B", score: 3.4, z: 1.60, final_score: 6, rms: 2.1 },
  { symbol: "C", score: 2.1, z: 0.95, final_score: 3, rms: 2.2 },    // same Score as A, higher z
  { symbol: "D", score: -1.0, z: -0.40, final_score: -1, rms: 2.5 },
];
ec._setPayloadForTest({ meta: { scoring: "v3" }, instruments: insts });
assert.deepStrictEqual(insts.slice().sort(ec.compareInstruments).map((i) => i.symbol), ["B", "C", "A", "D"]);
assert.strictEqual(ec.SCORE_HEADER_TIP, "Score −10..+10: 0–2 Neutral, 3–5 Bullish/Bearish, 6–10 Very");
assert.strictEqual(ec.scoreTip(insts[0]), "raw score +2.00 · usual size (RMS) 2.50 · z +0.80");
assert.strictEqual(ec.chainText(insts[1]), "raw score +3.40 → z +1.60 → Score +6");
const ca = { table_layout: [{ key: "growth", label: "Growth", columns: [] }], bias_thresholds: { mild: 0.65, very: 1.59 },
  instruments: [{ symbol: "GOLD", display: "Gold", type: "metal", home_ccy: "USD", bias_label: "Bearish",
                  score_precise: -1.68, z: -0.84, final_score: -3, rms: 2.004, factors: [], cells: {} }] };
const html = ec.caTableHtml(ca);
assert.ok(html.includes(">Score</th>") && html.includes(">-3</td>") && !html.includes(">z</th>"), html);
console.log("final score ok");
