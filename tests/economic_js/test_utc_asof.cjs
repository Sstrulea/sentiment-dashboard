// payload.as_of (and generated_at) are UTC timestamps without an offset: every place
// that turns one into a time must read it as UTC, never as the viewer's local time.
// Run with TZ=Europe/Bucharest (UTC+3 in October) by tests/test_economic_js.py.
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const ROOT = path.join(__dirname, "..", "..");

assert.strictEqual(new Date(2026, 9, 7, 12).getTimezoneOffset(), -180, "runs at UTC+3 (TZ=Europe/Bucharest)");

function load(file) {
  const sb = { module: { exports: {} }, window: {}, document: { readyState: "loading", addEventListener: () => {} }, console, Date };
  vm.createContext(sb);
  for (const f of ["score-palette.js", file]) vm.runInContext(fs.readFileSync(path.join(ROOT, "static", f), "utf8"), sb, { filename: f });
  return sb.module.exports;
}
const ec = load("economic-chart.js");
assert.strictEqual(ec.fmtAsOf("2026-10-07T18:12:51.429876"), "2026-10-07 18:12 UTC");   // was 15:12 at UTC+3
assert.strictEqual(ec.fmtAsOf("2026-10-07T18:12:51+00:00"), "2026-10-07 18:12 UTC");    // with an offset: unchanged
assert.strictEqual(ec.phoneAsOfText("2026-10-07T18:12:51.429876"), "7 Oct, 18:12 UTC");
assert.strictEqual(ec.phoneAsOfText("2026-10-07T22:30:00"), "7 Oct, 22:30 UTC");        // not 8 Oct, 01:30

const st = load("strength.js");
assert.strictEqual(st.modelDateText("2026-10-07T22:30:00"), "7 Oct");                   // local would be 8 Oct
assert.strictEqual(st.modelDateText("2026-10-07T18:12:51.429876"), "7 Oct");
console.log("utc as_of ok");
