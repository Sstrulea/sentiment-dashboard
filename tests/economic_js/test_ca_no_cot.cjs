// v3 cross-asset table (2026-10-07): no COT / SENTIMENT column — positioning is not
// scored on the cross-asset board any more (COT left the metal score).
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
const ca = {
  table_layout: [{ key: "growth", label: "Growth", columns: [{ key: "gdp_qoq", label: "GDP" }] },
                 { key: "rates", label: "Rates", columns: [{ key: "rate_exp_2y", label: "2Y (3m)" }] }],
  bias_thresholds: { mild: 0.65, very: 1.59 }, bias_thresholds_metal: { mild: 0.71, very: 1.44 },
  instruments: [{ symbol: "GOLD", display: "Gold", type: "metal", home_ccy: "USD", bias_label: "Neutral",
                  score_precise: 0.5, z: 0.25, factors: [], cells: { gdp_qoq: { score: 1 }, rate_exp_2y: { score: -0.4, continuous: true } },
                  cot: { cell: 2, level: 1, flow: 1, z: 0.5 } }],
};
ec._setPayloadForTest({ meta: { scoring: "v3" }, crossasset: ca, instruments: [] });
const html = ec.caTableHtml(ca);
assert.ok(!/grp-sentiment|>COT<|COT \/ P\/C|SENTIMENT/.test(html), "no COT column in v3: " + html);
assert.ok(html.includes(ec.SCORE_HEADER_TIP) && !html.includes(">z</th>"), "Score header (−10..+10 bands) replaces the z column");
// a v2 payload keeps its SENTIMENT column
ec._setPayloadForTest({ meta: {}, crossasset: ca, instruments: [] });
assert.ok(/SENTIMENT/.test(ec.caTableHtml(ca)), "v2 keeps the column");
console.log("cross-asset no cot ok");
