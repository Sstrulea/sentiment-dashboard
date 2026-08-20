// FAZA 1D 4.3 — minimal automated test for static/history.js.
//
// Plain Node, no test framework / no jsdom (neither is a project dependency).
// Exercises resolveEntry(), the function behind P1.4's points_ref dedup — the
// exact seam where a payload-resolution regression would show up as either
// the wrong bar count or a silently empty chart. Only the pure, DOM-free
// exports are used (see the `module.exports` guard at the bottom of
// history.js); document/window are stubbed just enough that requiring the
// file doesn't throw (readyState "loading" means boot() is registered as a
// listener but never actually invoked, so no real DOM access happens).
"use strict";

const assert = require("assert");
const path = require("path");

global.document = { readyState: "loading", addEventListener: () => {} };
global.window = {};

const mod = require(path.join(__dirname, "..", "..", "static", "history.js"));

function freshPayload() {
  const points = [1, 2, 3, 4, 5].map((i) => ({
    release_dt: "2024-0" + i + "-01T00:00:00", actual: i * 1.0, forecast: i * 1.0 - 0.1,
    previous: i * 1.0 - 0.2, z: 0.1, bucket: 1, score_status: "scored", revised_from: null,
  }));
  const windowOptions = { "1y": { n: points.length, points: points } };
  return {
    meta: { generated_at: "now", catalog_version: "test", as_of: "now" },
    categories: {
      inflation: {
        USD: [
          {
            role: "market", rank: 1, indicator_key: "cpi_yoy", display_label: "CPI y/y",
            unit: "%", label_source: "canonical", target: null, chart_type: "bar",
            cadence_empirical: "monthly", window_options: windowOptions, quarantine_count: 0,
          },
          {
            role: "policy", rank: 2, indicator_key: "cpi_yoy", display_label: "CPI y/y",
            unit: "%", label_source: "canonical", target: { kind: "point", value: 2 },
            chart_type: "bar", cadence_empirical: "monthly",
            points_ref: { role: "market" },
          },
          {
            role: "secondary", rank: 3, indicator_key: "core_cpi", display_label: "Core CPI",
            unit: "%", label_source: "canonical", target: null, chart_type: "bar",
            cadence_empirical: "monthly",
            points_ref: { role: "does_not_exist" },   // deliberately broken (4.1)
          },
        ],
      },
    },
  };
}

function run() {
  let failures = 0;
  function check(name, fn) {
    try { fn(); console.log("ok — " + name); }
    catch (e) { failures++; console.error("FAIL — " + name + ": " + e.message); }
  }

  mod.state.payload = freshPayload();
  mod.state.category = "inflation";

  check("market entry resolves its own 5 points (bar count)", () => {
    const r = mod.resolveEntry("USD", "market");
    assert.strictEqual(r.windowOptions["1y"].points.length, 5);
  });

  check("policy entry (points_ref -> market) resolves the SAME 5 points, not a copy/empty set", () => {
    const r = mod.resolveEntry("USD", "policy");
    assert.ok(r, "resolveEntry returned null");
    assert.strictEqual(r.windowOptions["1y"].points.length, 5);
    assert.strictEqual(r.windowOptions["1y"].points[0], mod.resolveEntry("USD", "market").windowOptions["1y"].points[0]);
  });

  check("broken points_ref fails loudly: console.error called, refBroken flagged, no silent empty chart", () => {
    const calls = [];
    const origErr = console.error;
    console.error = (msg) => calls.push(msg);
    try {
      const r = mod.resolveEntry("USD", "secondary");
      assert.strictEqual(r.refBroken, true);
      assert.strictEqual(calls.length, 1, "expected exactly one console.error call");
      assert.ok(/points_ref target role/.test(calls[0]));
    } finally {
      console.error = origErr;
    }
  });

  check("unknown role returns null (not a resolved-but-empty entry)", () => {
    assert.strictEqual(mod.resolveEntry("USD", "nope"), null);
  });

  check("availableCategories reflects only non-empty categories", () => {
    assert.deepStrictEqual(mod.availableCategories(), ["inflation"]);
  });

  check("allCurrencies is the union across categories", () => {
    assert.deepStrictEqual(mod.allCurrencies(), ["USD"]);
  });

  if (failures) { console.error("\n" + failures + " test(s) failed."); process.exit(1); }
  console.log("\nAll history.js resolveEntry tests passed.");
}

run();
