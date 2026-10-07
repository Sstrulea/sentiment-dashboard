// FX / cross-asset tables: the z column (score / RMS, payload field `z`), sorted by z by default.
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

// default order = z desc, not score desc
const insts = [{ symbol: "A", score: 3.0, z: 0.9 }, { symbol: "B", score: 1.0, z: 1.6 }, { symbol: "C", score: -2.0, z: -0.8 }];
ec._setPayloadForTest({ meta: {}, instruments: insts });
const order = insts.slice().sort(ec.compareInstruments).map((i) => i.symbol);
assert.deepStrictEqual(order, ["B", "A", "C"]);
assert.strictEqual(ec.zOf({ score: 2.0, z: null }), 2.0);          // no z → the score
const tip = ec.zHeaderTip({ mild: 0.7, very: 1.51 });
assert.ok(tip.includes("Neutral below 0.70") && tip.includes("Bullish/Bearish from 0.70") && tip.includes("Very from 1.51"), tip);
console.log("z column ok");
