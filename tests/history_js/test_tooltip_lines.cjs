// Tooltip lines of a printed /history point (pointTooltipLines): "Actual" is the
// print as published (Actual - Forecast = Delta), a revision is its own line,
// a manual value names its source.
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

// Same sandbox as test_resolve_entry.cjs (package.json is "type": "module").
const sandbox = {
  module: { exports: {} }, console,
  document: { readyState: "loading", addEventListener: () => {} }, window: {},
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "..", "static", "history.js"), "utf8"),
                sandbox, { filename: "history.js" });
// Arrays built inside the sandbox have its own Array prototype: compare plain copies.
const pointTooltipLines = (p) => JSON.parse(JSON.stringify(sandbox.module.exports.pointTooltipLines(p)));

// revised + manual (AUD Employment 2025-10-16 shape)
assert.deepStrictEqual(
  pointTooltipLines({ actual: 12.8, revised_from: 14.9, forecast: 20.5, score_status: "scored",
                      recovered: false, manual: true, source_ref: "abs.gov.au" }),
  ["Actual: 14.90", "Forecast: 20.50", "Delta: -5.60", "Revised: 12.80", "Source: manual entry · abs.gov.au"]);

// feed print, no revision
assert.deepStrictEqual(
  pointTooltipLines({ actual: 0.1, revised_from: null, forecast: 0.2, score_status: "scored",
                      recovered: false, manual: false, source_ref: null }),
  ["Actual: 0.10", "Forecast: 0.20", "Delta: -0.10"]);

// manual entry whose note has no URL
const noUrl = pointTooltipLines({ actual: 1.8, revised_from: null, forecast: 1.7, score_status: "scored",
                                  recovered: false, manual: true, source_ref: null });
assert.strictEqual(noUrl[noUrl.length - 1], "Source: manual entry");

// recovered (revised value only)
assert.deepStrictEqual(
  pointTooltipLines({ actual: 0.1, revised_from: null, forecast: 0.2, score_status: "recovered",
                      recovered: true, manual: false, source_ref: null }),
  ["Revised: 0.10", "First print unavailable — not scored"]);

console.log("tooltip lines: ok");
