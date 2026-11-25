// Scoring v4 (2026-11-25): the FX board scores COT only and shows its inputs — the base leg, the quote leg and their
// difference Δ (score = 2.5 × Δ / σ_cot); the US Dollar row has one leg, DXY. Checked on the real payload switched to v4
// in memory (meta.fx_model), so it runs the same before and after the live payload is v4.
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
payload.meta = Object.assign({}, payload.meta, { fx_model: "v4", v4: Object.assign({ scale: 2.5, sigma_cot: 1.773 }, (payload.meta || {}).v4 || {}) });
mod._setPayloadForTest(payload);
assert.strictEqual(mod.isFxV4(), true);

const tds = (html) => html.match(/<td[^>]*>.*?<\/td>/g) || [];
const text = (td) => td.replace(/<[^>]+>/g, "").replace("−", "-");
let n = 0;
for (const inst of payload.instruments) {
  const cot = inst.cot;
  const cells = tds(mod.fxCotV4CellsHtml(inst));
  assert.strictEqual(cells.length, 3, inst.symbol + ": three COT cells");
  if (!cot || cot.cell === null || cot.cell === undefined) {
    cells.forEach((c) => assert.ok(c.includes("cell-empty"), inst.symbol + ": empty without COT"));
    continue;
  }
  const b = Number(cot.base_cell || 0), q = cot.quote ? Number(cot.quote_cell || 0) : 0;
  const d = inst.type === "single" ? b : b - q;
  assert.strictEqual(Number(text(cells[0])), b, inst.symbol + ": base leg");
  if (inst.type === "single") {
    assert.ok(cells[1].includes("cell-empty"), "US Dollar: no quote leg");
    assert.ok(cells[0].includes("DXY COT cell"), "US Dollar: the DXY leg");
  } else {
    assert.strictEqual(Number(text(cells[1])), q, inst.symbol + ": quote leg");
  }
  assert.strictEqual(Number(text(cells[2])), d, inst.symbol + ": Δ = base − quote, uncapped");
  assert.ok(cells[2].includes("2.5 × (COT base − COT quote) / "), inst.symbol + ": the formula in the Δ tooltip");
  n++;
}
assert.ok(n >= 28, "every pair with COT checked");
console.log("v4 cot columns ok (" + n + " rows)");
