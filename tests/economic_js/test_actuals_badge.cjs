// 2026-10-07: the "Actuals" badge reads the data (Needs review rows), not the JB source.
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
const jb401 = { last_update: "2026-10-05T01:06:15", age_days: 2,
  last_attempt: { at: "2026-10-07T08:30:29", status: "fetch_failed", http_status: 401 } };

const ok = mod.actualsBadge(Object.assign({ state: "ok", n: 0, stale: false }, jb401));
assert.strictEqual(ok.txt, "Actuals up to date");
assert.strictEqual(ok.cls, "ok");
assert.ok(ok.tip.includes("JB pull: last success 2d ago") && ok.tip.includes("HTTP 401"), ok.tip);
const html = mod.freshnessBadges({ actuals_pull: Object.assign({ state: "ok", n: 0, stale: false }, jb401) });
assert.ok(!/STALE|401/.test(html.replace(/title="[^"]*"/, "")), "no STALE / 401 in the badge text: " + html);

const pend = mod.actualsBadge(Object.assign({ state: "pending", n: 2, stale: false }, jb401));
assert.strictEqual(pend.txt, "2 actuals pending");
assert.strictEqual(pend.cls, "pending");

const miss = mod.actualsBadge(Object.assign({ state: "missing", n: 1, stale: true }, jb401));
assert.strictEqual(miss.txt, "⚠ 1 actual missing");
assert.strictEqual(miss.cls, "stale");
console.log("actuals badge: 3 states ok");
