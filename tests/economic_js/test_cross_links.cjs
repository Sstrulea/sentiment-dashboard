// The central-bank cell of every pair row of /economic links to the Central Banks pair page, the DXY row to the USD bank page.
// Scoring v3 (2026-10-07): that cell is CARRY (the policy-rate differential); the RATE EXP (2Y) column is gone.
// On a v2 payload (no meta.scoring) it is still the RATE EXP (2Y) cell.
// Plain Node, no jsdom: static/economic-chart.js exposes indicatorCellHtml through its `module.exports` guard. The data is the real public/data/economic.json.
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..", "..");

// The repo's package.json says "type": "module" (the Vercel middleware), so a .js file would load as an ES module and `module` would not exist:
// the browser script is run as a plain script in a vm context instead, with just enough of a DOM for the file to load (readyState "loading":
// boot() is registered, never run).
const vm = require("vm");
const sandbox = { module: { exports: {} }, window: {}, document: { readyState: "loading", addEventListener: () => {} }, console };
vm.createContext(sandbox);
for (const f of ["score-palette.js", "economic-chart.js"]) {                       // the page loads the palette first (economic-chart.js fails closed without it)
  vm.runInContext(fs.readFileSync(path.join(ROOT, "static", f), "utf8"), sandbox, { filename: f });
}
const mod = sandbox.module.exports;
assert.strictEqual(typeof mod.indicatorCellHtml, "function", "economic-chart.js exposes indicatorCellHtml for tests");
const payload = JSON.parse(fs.readFileSync(path.join(ROOT, "public", "data", "economic.json"), "utf8"));

const fx = payload.instruments.filter((i) => i.type === "fx");
const singles = payload.instruments.filter((i) => i.type !== "fx");
assert.strictEqual(fx.length, 28, "the page has 28 pairs");

const v3 = (payload.meta || {}).scoring === "v3";
const cbCell = (inst) => (v3 ? mod.carryCellHtml(inst) : mod.indicatorCellHtml(inst, "rate_expectations"));
const linksOf = (inst) => (cbCell(inst).match(/<a class="cb-xlink" href="([^"]+)"/g) || []).map((a) => a.split('href="')[1].slice(0, -1));

// every pair: exactly one link, to its own page, which exists
const all = [];
for (const inst of fx) {
  const l = linksOf(inst);
  assert.deepStrictEqual(l, ["/central-banks/pair/" + inst.symbol.toLowerCase()], inst.symbol + " has its pair link");
  assert.ok(fs.existsSync(path.join(ROOT, "public", "central-banks", "pair", inst.symbol.toLowerCase() + ".html")), inst.symbol + " page exists");
  all.push(l[0]);
}
// DXY: the USD bank page
const dxy = singles.filter((i) => i.symbol === "US-DOLLAR");
assert.strictEqual(dxy.length, 1);
assert.deepStrictEqual(linksOf(dxy[0]), ["/central-banks/usd"]);
all.push("/central-banks/usd");

assert.strictEqual(all.length, 29, "28 pairs + DXY");
assert.strictEqual(new Set(all).size, 29, "no link twice");

// the pair without a 2Y value (n/a) keeps the link on its dash, in a cell that is still marked n/a
const na = fx.filter((i) => (v3 ? (i.v3 || {}).carry : ((i.indicator_cells || {}).rate_expectations || {}).v) === null);
if (!v3) assert.ok(na.length >= 1, "at least one pair has no RATE EXP value in the real payload");
// v3: a pair without a carry value (a leg with no policy rate) keeps the link on its dash, like the 2Y cell did
const noCarry = JSON.parse(JSON.stringify(fx[0]));
if (v3) { noCarry.v3.carry = null; na.push(noCarry); }
for (const inst of na) {
  const html = cbCell(inst);
  assert.ok(html.startsWith('<td class="econ-cell cell-na"') && html.includes(">—</a>"), inst.symbol + ": the dash is the link");
}

// other columns are untouched: no link
for (const key of ["gdp", "cpi", "unemployment"]) {
  for (const inst of fx.slice(0, 5)) assert.ok(!mod.indicatorCellHtml(inst, key).includes("cb-xlink"), key + " has no link");
}
console.log("ok: " + fx.length + " pair links + DXY; n/a cells: " + na.map((i) => i.symbol).join(","));
