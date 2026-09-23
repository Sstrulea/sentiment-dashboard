// FAZA 1D 4.3 — minimal automated test for static/history.js.
// FAZA 2E-2 — resolveEntry is now keyed on indicator_key, not role (role
// alone is ambiguous: labor/USD and labor/GBP each carry two DIFFERENT
// series under role "secondary" — a role-keyed selection made both chips
// show active and only ever rendered the first, silently, for months).
// indicator_key is unique per (category, currency) by catalog contract, so
// every entry below has its own distinct key, INCLUDING the points_ref one —
// points_ref itself still names its target by role (a payload-size dedup,
// unrelated to which key the UI selects), so that inner lookup is unchanged.
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
const fs = require("fs");
const path = require("path");
const vm = require("vm");

// .cjs + vm (audit 4D): the repo's package.json is "type": "module", so a
// plain .js here is ESM (no require) and static/history.js — a classic
// browser script with a `module.exports` guard — cannot be require()d as CJS.
// Run it in a sandbox exposing `module`, like tests/economic_js does.
const sandbox = {
  module: { exports: {} }, console,
  document: { readyState: "loading", addEventListener: () => {} }, window: {},
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "..", "static", "history.js"), "utf8"),
                sandbox, { filename: "history.js" });
const mod = sandbox.module.exports;

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
            role: "policy", rank: 2, indicator_key: "cpi_yoy_policy_ref", display_label: "CPI y/y",
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

  check("market entry (keyed by indicator_key) resolves its own 5 points (bar count)", () => {
    const r = mod.resolveEntry("USD", "cpi_yoy");
    assert.strictEqual(r.windowOptions["1y"].points.length, 5);
  });

  check("policy entry (distinct key, points_ref -> market role) resolves the SAME 5 points, not a copy/empty set", () => {
    const r = mod.resolveEntry("USD", "cpi_yoy_policy_ref");
    assert.ok(r, "resolveEntry returned null");
    assert.strictEqual(r.windowOptions["1y"].points.length, 5);
    assert.strictEqual(r.windowOptions["1y"].points[0], mod.resolveEntry("USD", "cpi_yoy").windowOptions["1y"].points[0]);
  });

  check("broken points_ref fails loudly: console.error called, refBroken flagged, no silent empty chart", () => {
    const calls = [];
    const origErr = console.error;
    console.error = (msg) => calls.push(msg);
    try {
      const r = mod.resolveEntry("USD", "core_cpi");
      assert.strictEqual(r.refBroken, true);
      assert.strictEqual(calls.length, 1, "expected exactly one console.error call");
      assert.ok(/points_ref target role/.test(calls[0]));
    } finally {
      console.error = origErr;
    }
  });

  check("unknown key returns null (not a resolved-but-empty entry)", () => {
    assert.strictEqual(mod.resolveEntry("USD", "nope"), null);
  });

  check("two entries sharing role 'secondary' are independently selectable by key (the FAZA 2E bug)", () => {
    // The real bug: labor/USD carries unemployment_rate AND wage_growth,
    // both role "secondary". Keying selection on role alone made both
    // chips show active and resolveEntry always return the FIRST match,
    // regardless of which was clicked. Two fresh entries here (not reusing
    // the earlier "core_cpi"/broken-points_ref fixture) so this check is
    // never contaminated by that unrelated scenario.
    const onePoint = [{ release_dt: "2024-01-01T00:00:00", actual: 1, forecast: 0.9,
                       previous: 0.8, z: 0.1, bucket: 1, score_status: "scored", revised_from: null }];
    mod.state.payload.categories.inflation.USD.push(
      { role: "secondary", rank: 4, indicator_key: "unemployment_rate", display_label: "Unemployment",
        unit: "%", label_source: "canonical", target: null, chart_type: "bar",
        cadence_empirical: "monthly", window_options: { "1y": { n: 1, points: onePoint } }, quarantine_count: 0 },
      { role: "secondary", rank: 5, indicator_key: "wage_growth", display_label: "Wages",
        unit: "%", label_source: "canonical", target: null, chart_type: "bar",
        cadence_empirical: "monthly", window_options: { "1y": { n: 1, points: onePoint } }, quarantine_count: 0 });
    const unemployment = mod.resolveEntry("USD", "unemployment_rate");
    const wageGrowth = mod.resolveEntry("USD", "wage_growth");
    assert.ok(unemployment && wageGrowth, "both same-role entries must resolve");
    assert.notStrictEqual(unemployment.meta.indicator_key, wageGrowth.meta.indicator_key);
  });

  check("availableCategories reflects only non-empty categories", () => {
    assert.deepStrictEqual([...mod.availableCategories()], ["inflation"]);   // [...] = host realm (vm)
  });

  check("allCurrencies is the union across categories", () => {
    assert.deepStrictEqual([...mod.allCurrencies()], ["USD"]);
  });

  // audit B2: a value recovered from the next print's previous
  // (actual_origin ff_previous) passes through resolveEntry unchanged and is
  // drawn HOLLOW in both chart paths; first releases stay filled.
  check("B2 recovered point survives resolveEntry and is drawn hollow", () => {
    const p = freshPayload();
    const pts = p.categories.inflation.USD[0].window_options["1y"].points;
    pts[2].recovered = true; pts[2].score_status = "recovered"; pts[2].z = null; pts[2].bucket = null;
    mod.state.payload = p;
    const r = mod.resolveEntry("USD", "cpi_yoy");
    assert.strictEqual(r.windowOptions["1y"].points[2].recovered, true);
    const colors = { accent: "#123", quarantineMarker: "#f00", forecastLine: "#999" };
    const bar = mod.buildBarDatasets(r.windowOptions["1y"].points, colors)[0];
    assert.deepStrictEqual([...bar.backgroundColor], ["#123", "#123", "transparent", "#123", "#123"]);
    assert.deepStrictEqual([...bar.borderWidth], [0, 0, 2, 0, 0]);
    const step = mod.buildStepDatasets(r.windowOptions["1y"].points, colors)[0];
    assert.strictEqual(step.pointBackgroundColor[2], "transparent");
    assert.strictEqual(step.pointBackgroundColor[0], "#123");
  });

  if (failures) { console.error("\n" + failures + " test(s) failed."); process.exit(1); }
  console.log("\nAll history.js resolveEntry tests passed.");
}

run();
