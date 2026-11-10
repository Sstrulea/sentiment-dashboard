// Strip + tooltip of the official (display-only) NZD CPI y/y series, and the
// Previous column added to every series' strip. Points are the real payload
// shapes (P2, NZD inflation, 2026-11-10).
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

// Same sandbox as test_resolve_entry.cjs; a body stub for themeColors().
const sandbox = {
  module: { exports: {} }, console,
  document: { readyState: "loading", addEventListener: () => {}, body: { classList: { contains: () => false } } },
  window: {},
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "..", "static", "history.js"), "utf8"),
                sandbox, { filename: "history.js" });
const api = sandbox.module.exports;
const plain = (x) => JSON.parse(JSON.stringify(x));
const strip = (meta, pts) => plain(api.stripItems(meta, pts));

const officialMeta = {
  role: "official", rank: 1, indicator_key: "cpi_yoy_official", source: "official", scored: false,
  unit: "pct", comparable_with_target: true, chart_type: "bar",
  target: { kind: "band", low: 1.0, high: 3.0, effective_from: "2012-09-26", label: "RBNZ 1-3% (focus 2%)" },
};
const off = (dt, period, a, prev, la, lf) => ({
  release_dt: dt, period, actual: a, previous: prev, forecast: null, z: null, bucket: null,
  score_status: "official", revised_from: null, recovered: false, manual: false, source_ref: "stats.govt.nz",
  linked: { indicator_key: "cpi_yoy", label: "CPI (QoQ)", actual: la, forecast: lf },
});
const offPts = [
  off("2026-07-20T22:45:00", "2026Q2", 4.1, 3.1, 1.5, 1.4),
  off("2026-10-21T21:45:00", "2026Q3", 3.7, 4.1, 0.7, 0.8),
];

// ---- official strip: Release | Actual | Previous | Surprise (QoQ) | Not scored
assert.deepStrictEqual(strip(officialMeta, offPts), [
  { label: "Release", value: "2026-10-21" },
  { label: "Actual", value: "3.7 pct" },
  { label: "Previous", value: "4.1 pct" },
  { label: "Surprise (QoQ)", value: "0.7 vs 0.8 = −0.1", cls: "hs-delta-neg" },
  { label: "Not scored", value: "Stats NZ", cls: "hs-muted" },
]);
// no Forecast / Delta of its own
const labels = strip(officialMeta, offPts).map((i) => i.label);
assert.ok(!labels.includes("Forecast") && !labels.includes("Delta (Act-Fcst)"));
// a beat colors positive (2026Q2: 1.5 vs 1.4)
assert.deepStrictEqual(strip(officialMeta, offPts.slice(0, 1))[3],
                       { label: "Surprise (QoQ)", value: "1.5 vs 1.4 = 0.1", cls: "hs-delta-pos" });
// in-line: no sign, no color (2023Q4: 0.5 vs 0.5)
assert.deepStrictEqual(strip(officialMeta, [off("2024-01-23T21:45:00", "2023Q4", 4.7, 5.6, 0.5, 0.5)])[3],
                       { label: "Surprise (QoQ)", value: "0.5 vs 0.5 = 0.0", cls: "" });

// ---- official tooltip
assert.deepStrictEqual(plain(api.officialTooltipLines(offPts[1])), [
  "Q3 2026", "Actual (YoY): 3.7", "Previous: 4.1",
  "QoQ: 0.7 vs 0.8 forecast (Δ −0.1)", "Source: Stats NZ · not scored",
]);

// ---- every other series: Previous right after Actual, rest unchanged
const qoqMeta = { role: "market", indicator_key: "cpi_yoy", unit: "pct", chart_type: "bar" };
const qoqLast = { release_dt: "2026-10-21T21:45:00", actual: 0.7, previous: 1.5, forecast: 0.8, z: -0.97,
                  bucket: -1, score_status: "scored", revised_from: null, recovered: false, manual: false };
assert.deepStrictEqual(strip(qoqMeta, [qoqLast]), [
  { label: "Release", value: "2026-10-21" },
  { label: "Actual", value: "0.70 pct" },
  { label: "Previous", value: "1.50 pct" },
  { label: "Forecast", value: "0.80 pct" },
  { label: "Delta (Act-Fcst)", value: "-0.10 pct", cls: "hs-delta-neg" },
]);
// pending release still gets its own "Next" item, after Delta
const next = { release_dt: "2027-01-21T21:45:00", actual: null, previous: 0.7, forecast: 0.5,
               score_status: "no_actual", revised_from: null };
assert.deepStrictEqual(strip(qoqMeta, [qoqLast, next]).map((i) => i.label),
                       ["Release", "Actual", "Previous", "Forecast", "Delta (Act-Fcst)", "Next"]);
// a missing previous renders as a dash, never "NaN"/"null"
assert.strictEqual(strip(qoqMeta, [{ ...qoqLast, previous: null }])[2].value, "— pct");
assert.strictEqual(api.stripItems(qoqMeta, []), null);

// ---- chart: official has no forecast line, draws the band; others keep the line
const cfgOff = api.buildChartConfig(officialMeta, offPts);
assert.deepStrictEqual(plain(cfgOff.data.datasets.map((d) => d.label)), ["Actual"]);
const band = cfgOff.options.plugins.annotation.annotations.targetBand;
assert.ok(band && band.yMin === 1.0 && band.yMax === 3.0 && band.label.content === "RBNZ 1-3% (focus 2%)");
const cfgQoq = api.buildChartConfig({ ...qoqMeta, comparable_with_target: false }, [qoqLast]);
assert.deepStrictEqual(plain(cfgQoq.data.datasets.map((d) => d.label)), ["Actual", "Forecast"]);
assert.deepStrictEqual(plain(cfgQoq.options.plugins.annotation.annotations), {});

console.log("test_official_strip: ok");
