// /strength and /economic drilldowns (fix/strength-display): category headers show
// score_precise · value in σ · N, a "Macro …σ" line sits above them, and the
// /strength drilldown has no "Rates" group (the policy rate feeds Carry only).
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const ROOT = path.join(__dirname, "..", "..");

function load(file) {
  const sb = { module: { exports: {} }, window: {}, document: { readyState: "loading", addEventListener: () => {} }, console };
  vm.createContext(sb);
  for (const f of ["score-palette.js", file]) vm.runInContext(fs.readFileSync(path.join(ROOT, "static", f), "utf8"), sb, { filename: f });
  return sb.module.exports;
}

const card = {
  coverage: 20,
  categories: { growth: { score_precise: -0.1, score_cell: 0, coverage: 8 },
                inflation: { score_precise: 0.2, score_cell: 0, coverage: 5 },
                labour: { score_precise: -0.4286, score_cell: 0, coverage: 7 } },
  breakdown: {
    gdp_qoq: { score: 0, category: "growth" },
    cpi_yoy: { score: 1, category: "inflation" },
    unemployment_rate: { score: -1, category: "labour" },
    interest_rate_decision: { score: 0, category: "rates" },
  },
  v3: { in_sigma: { growth: -0.1 / 0.279, inflation: 0.2 / 0.528, labour: -0.4286 / 0.298 },
        blocks: { macro: -1.0412 } },
};
const meta = {
  indicators: { gdp_qoq: { category: "growth", label: "GDP" }, cpi_yoy: { category: "inflation", label: "CPI" },
                unemployment_rate: { category: "labour", label: "Unemployment" },
                interest_rate_decision: { category: "rates", label: "Interest Rate Decision" } },
  categories: { growth: { label: "Growth" }, inflation: { label: "Inflation" }, labour: { label: "Labour Market" } },
  table_layout: [{ category: "growth", columns: [] }, { category: "inflation", columns: [] }, { category: "labour", columns: [] }],
};

const st = load("strength.js");
st._setPayloadForTest({ meta: meta, currencies: { USD: card } });
assert.strictEqual(st.catSigmaText(card, "labour"), "−0.43 · −1.44σ · N7");
assert.strictEqual(st.macroSigmaText(card), "Macro −1.04σ");
assert.deepStrictEqual(Array.from(st.drilldownCats(card)), ["growth", "inflation", "labour"]);
const html = st.drilldownGroupsHtml(card, "USD");
assert.ok(html.includes("Macro −1.04σ"), "Macro line");
assert.ok(html.indexOf("Macro −1.04σ") < html.indexOf("GROWTH"), "Macro line above the categories");
assert.ok(html.includes("−0.43 · −1.44σ · N7"), "labour header");
assert.ok(!html.includes("Interest Rate Decision") && !html.includes("not scored"), "no Rates group on /strength");
// a v2 payload (no card.v3) keeps the old header
const v2card = Object.assign({}, card, { v3: undefined });
assert.strictEqual(st.catSigmaText(v2card, "labour"), null);

const ec = load("economic-chart.js");
assert.strictEqual(ec.catSigmaText(card, "labour"), "−0.43 · −1.44σ · N7");
assert.strictEqual(ec.macroSigmaText(card), "Macro −1.04σ");
console.log("sigma headers ok");
