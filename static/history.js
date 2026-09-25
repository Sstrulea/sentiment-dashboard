/* Economic History page chart logic (FAZA 1C shipped inflation only; FAZA 1D
   extends to all 4 catalog categories: inflation/growth/labor/rates).
   Depends on Chart.js v4 UMD + chartjs-plugin-annotation. Payload is embedded
   in the page (window.HISTORY_PAYLOAD) — no runtime fetch, unlike the other
   pages on this site. */
(function () {
  "use strict";

  const CATEGORY_ORDER = ["inflation", "growth", "labor", "rates"];
  const CATEGORY_LABEL = { inflation: "Inflation", growth: "Growth", labor: "Labor", rates: "Rates" };

  // FAZA 2E-2 — `key` (indicator_key) is the selected-series identifier,
  // unique per (category, currency) — see the note above resolveEntry for
  // why `role` (market/secondary/policy) could never be this: labor/USD and
  // labor/GBP both carry two DIFFERENT series under role "secondary", so a
  // role-keyed selection could never tell them apart (confirmed live bug,
  // FAZA 2E: both chips showed active, only the first ever rendered).
  const state = {
    payload: (typeof window !== "undefined" && window.HISTORY_PAYLOAD) || { categories: {} },
    category: null,
    ccy: null,
    key: null,
    windowKey: null,
    chart: null,
  };

  // ---- Theme (same convention as vix-chart.js / pc-chart.js) --------------

  function isDarkTheme() {
    return document.body.classList.contains("dark");
  }

  // FAZA 2C 1 — bucket-based bar coloring (beat/in-line/miss) is gone: the
  // z-score dead-zone bucket and the bar-vs-forecast-line relationship both
  // encoded "the surprise," and disagreeing on close calls read as a
  // contradiction ("bar is below the line but colored green"). The bar/line
  // relationship alone is unambiguous, so it's the only one left. `accent`
  // is the site's one existing accent blue (static/style.css --accent),
  // reused verbatim for every "Actual" series in every category, not a new
  // color invented for this page.
  function themeColors() {
    const dark = isDarkTheme();
    return {
      accent: dark ? "#64b5f6" : "#1565c0",
      grid: dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
      tick: dark ? "#9aa0a6" : "#666",
      forecastLine: dark ? "#ff6b6b" : "#c62828",
      targetLine: dark ? "#ffc107" : "#a67600",
      targetBand: dark ? "rgba(255, 193, 7, 0.10)" : "rgba(255, 193, 7, 0.14)",
      quarantineMarker: dark ? "#9aa0a6" : "#757575",
      tooltipBg: dark ? "rgba(10,10,10,0.95)" : "rgba(255,255,255,0.95)",
      tooltipFg: dark ? "#eee" : "#222",
      tooltipBorder: dark ? "#333" : "#ccc",
    };
  }

  // ---- Formatting -----------------------------------------------------------

  function fmtNum(x, digits) {
    if (x === null || x === undefined || Number.isNaN(Number(x))) return "—";
    return Number(x).toFixed(digits === undefined ? 2 : digits);
  }
  function fmtDate(iso) {
    if (!iso) return "—";
    return iso.slice(0, 10);
  }
  // Same convention as /economic's meta-bar (economic-chart.js's fmtAsOf) —
  // "YYYY-MM-DD HH:MM UTC" — so a reader who knows one page's stamp format
  // recognizes the other instantly.
  function fmtAsOf(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
  }
  // FAZA 1H 1 — `actual` on a point is the EFFECTIVE (possibly revised)
  // value; the as-first-published figure (what the bucket color reflects)
  // is `revised_from` when a revision exists, else `actual` itself is
  // already the original. Anywhere "the surprise" needs computing (Delta),
  // this is the actual to use — never the revised one, or the color and
  // the number would tell two different stories.
  function originalActualOf(p) {
    return (p.revised_from !== null && p.revised_from !== undefined) ? p.revised_from : p.actual;
  }
  const STATUS_LABEL = {
    scored: "Scored", no_actual: "No print yet",
    insufficient_history: "Not enough history for a full score", quarantined: "Quarantined (data-quality)",
    recovered: "Revised value, not scored", telemetry: "Final revision, not scored",
  };
  // Tooltip lines for a printed point (pure, exported for tests). "Actual" is
  // the print AS PUBLISHED: what Delta and the score are computed from, so
  // Actual - Forecast = Delta always reads true. A later revision is its own
  // line — and it is what the bar shows. A manual value names its source.
  function pointTooltipLines(p) {
    if (p.recovered) return ["Revised: " + fmtNum(p.actual), "First print unavailable — not scored"];
    if (p.score_status === "telemetry") return ["Final: " + fmtNum(p.actual), "The flash is the scored print — not scored"];
    const published = originalActualOf(p);
    const lines = ["Actual: " + fmtNum(published), "Forecast: " + fmtNum(p.forecast)];
    if (published !== null && published !== undefined && p.forecast !== null && p.forecast !== undefined) {
      lines.push("Delta: " + fmtNum(published - p.forecast));
    }
    if (p.revised_from !== null && p.revised_from !== undefined) lines.push("Revised: " + fmtNum(p.actual));
    if (p.manual) lines.push("Source: manual entry" + (p.source_ref ? " · " + p.source_ref : ""));
    return lines;
  }
  // audit B2 / 7A: drawn hollow — a recovered (revised) value or a final/revision
  // publication the config does not score (the flash is the scored print).
  function isHollow(p) { return !!(p.recovered || p.score_status === "telemetry"); }
  const CADENCE_ABBR = { monthly: "M", quarterly: "Q", weekly: "W", unknown: "?" };

  // ---- Category / currency universe --------------------------------------

  // Categories that actually carry data in this payload, in the site's fixed
  // display order (not just Object.keys order, which follows the YAML).
  function availableCategories() {
    const cats = state.payload.categories || {};
    return CATEGORY_ORDER.filter((c) => cats[c] && Object.keys(cats[c]).length);
  }

  // Union of every currency appearing in ANY category — used for the
  // currency dropdown so it stays stable across tab switches instead of
  // jumping around (a category that omits a currency, e.g. rates/CHF or
  // growth/JPY, still lists it; selecting it shows an explicit "no data"
  // message rather than making the currency disappear from the list).
  function allCurrencies() {
    const cats = state.payload.categories || {};
    const set = new Set();
    Object.keys(cats).forEach((cat) => Object.keys(cats[cat] || {}).forEach((c) => set.add(c)));
    return Array.from(set).sort();
  }

  // ---- Series resolution (P1.4: a role can reference a sibling's points) ---
  // No catalog entry sets `points_ref` today (its one user, inflation's
  // `policy` duplicate-reference role, was removed at FAZA 2C 4) — the
  // resolution mechanism stays general-purpose for whatever future entry
  // might need it.

  function seriesEntries(ccy) {
    const cat = state.payload.categories[state.category] || {};
    return cat[ccy] || [];
  }

  function sortedEntries(ccy) {
    return seriesEntries(ccy).slice().sort((a, b) => a.rank - b.rank);
  }

  // FAZA 2B 2 — a catalog entry with zero real prints (no indicator_key at
  // all — a target-note-only placeholder — or a real indicator_key that
  // never matched a single actual, payload's has_data:false) must not offer
  // a chip that leads nowhere. `resolveEntry`/`seriesEntries` stay raw (a
  // points_ref lookup must still be able to find its sibling role
  // internally); this filter only governs what a user can click into.
  function isVisibleEntry(e) {
    return e.indicator_key !== null && e.indicator_key !== undefined && e.has_data !== false;
  }
  function visibleEntries(ccy) {
    return sortedEntries(ccy).filter(isVisibleEntry);
  }

  // Returns {meta, windowOptions, quarantineCount} — `meta` is the entry's
  // OWN metadata (label/target/rank), windowOptions is resolved through
  // points_ref if this entry doesn't carry its own window_options (P1.4
  // dedup). Returns null only if `key` doesn't exist for this ccy.
  //
  // FAZA 2E-2 — keyed on indicator_key, NOT role. `role` (market/secondary/
  // policy) is a display-grouping label, never a unique identifier: labor/
  // USD and labor/GBP each carry two entries with role "secondary"
  // (Unemployment Rate + a second series) — selecting by role meant both
  // chips showed active and only the first ever rendered, silently, for
  // months (FAZA 2E). indicator_key IS unique per (category, currency) —
  // enforced by test_history_js's own catalog-wide check now, and by
  // tests/test_catalog_health.py-adjacent reasoning: two entries sharing a
  // selection key in the same list is a catalog bug, not a UI one.
  // `points_ref` itself still names its target by ROLE (that part of the
  // payload schema is unchanged — it is a payload-size dedup for "this
  // role's points are identical to that role's", independent of how the UI
  // picks which entry to show) — so the inner lookup below stays role-based.
  //
  // FAZA 1D 4.1: if points_ref names a role that isn't actually present
  // (a catalog/payload-building bug), fail LOUDLY — console.error plus a
  // visible fallback — instead of silently returning an empty
  // windowOptions (which previously rendered as a blank chart, indistinguishable
  // from "no data this window").
  function resolveEntry(ccy, key) {
    const entries = seriesEntries(ccy);
    const entry = entries.find((e) => e.indicator_key === key);
    if (!entry) return null;
    if (entry.points_ref) {
      const target = entries.find((e) => e.role === entry.points_ref.role);
      if (!target) {
        console.error(
          "history.js: points_ref target role '" + entry.points_ref.role + "' not found for " +
          ccy + "/" + entry.indicator_key + " (key=" + key + "). Payload/catalog bug — showing fallback.");
        return { meta: entry, windowOptions: {}, quarantineCount: 0, refBroken: true };
      }
      return { meta: entry, windowOptions: target.window_options || {}, quarantineCount: target.quarantine_count || 0 };
    }
    return { meta: entry, windowOptions: entry.window_options || {}, quarantineCount: entry.quarantine_count || 0 };
  }

  // ---- Controls -----------------------------------------------------------

  function renderCategoryTabs() {
    const wrap = document.getElementById("historyCatTabs");
    const cats = availableCategories();
    wrap.innerHTML = cats.map((c) => {
      const cls = "history-cat-tab" + (c === state.category ? " active" : "");
      return '<button class="' + cls + '" data-cat="' + c + '" role="tab" aria-selected="' +
        (c === state.category) + '">' + CATEGORY_LABEL[c] + "</button>";
    }).join("");
    wrap.querySelectorAll(".history-cat-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        const cat = btn.getAttribute("data-cat");
        if (cat === state.category) return;
        state.category = cat;
        // Keep the currently selected currency (the dropdown is global);
        // only re-pick a series — the old key may not exist in the new category.
        const entries = visibleEntries(state.ccy);
        const stillValid = entries.find((e) => e.indicator_key === state.key);
        state.key = stillValid ? state.key : (entries[0] ? entries[0].indicator_key : null);
        state.windowKey = null;
        syncUrl();
        renderCategoryTabs();
        renderAll();
      });
    });
  }

  function populateCcySelect() {
    const sel = document.getElementById("historyCcySelect");
    const ccys = allCurrencies();
    sel.innerHTML = ccys.map((c) => '<option value="' + c + '">' + c + "</option>").join("");
    sel.value = state.ccy;
    sel.addEventListener("change", () => {
      state.ccy = sel.value;
      const entries = visibleEntries(state.ccy);
      state.key = entries[0] ? entries[0].indicator_key : null;
      state.windowKey = null;
      syncUrl();
      renderAll();
    });
  }

  // FAZA 2C 4 — a currency/category with exactly one visible series (AUD/NZD
  // /CHF inflation after this phase's cleanup; every `rates` entry now that
  // `policy` isn't a separate duplicate chip) has nothing to switch TO — a
  // clickable, "active"-highlighted button implies a choice that doesn't
  // exist. Render it as a plain, non-interactive label instead: still shows
  // which series is on screen (visual consistency with every other
  // category), but no hover/click affordance and no click handler. Two or
  // more entries still render as the normal clickable chips.
  function renderSeriesChips() {
    const wrap = document.getElementById("historySeriesChips");
    const entries = visibleEntries(state.ccy);
    wrap.innerHTML = "";
    const single = entries.length === 1;
    entries.forEach((e) => {
      const el = document.createElement(single ? "span" : "button");
      if (!single) el.type = "button";
      // FAZA 2E-2 — active state keyed on indicator_key (unique), not role
      // (a display-only prefix below — never the identifier).
      el.className = "history-series-chip" + (single ? " history-series-chip-static" : (e.indicator_key === state.key ? " active" : ""));
      let label = e.role.charAt(0).toUpperCase() + e.role.slice(1);
      if (e.indicator_key) label += ": " + (e.display_label || e.indicator_key);
      if (e.label_source === "derived") label += ' <span class="label-derived-mark" title="Corrected label — the production canonical name did not match the real feed (see catalog mismatch_note)">*</span>';
      if (e.cadence_empirical) {
        const abbr = CADENCE_ABBR[e.cadence_empirical] || "?";
        label += ' <span class="history-cadence-badge" title="Empirical print cadence: ' + e.cadence_empirical +
          ' — adjacent points on this chart are NOT evenly spaced in time">' + abbr + "</span>";
      }
      el.innerHTML = label;
      if (!single) {
        el.addEventListener("click", () => {
          state.key = e.indicator_key;
          state.windowKey = null;
          syncUrl();
          renderAll();
        });
      }
      wrap.appendChild(el);
    });
  }

  function renderWindowSelector(windowOptions) {
    const wrap = document.getElementById("historyWindowSelector");
    const order = ["1y", "2y", "max"];
    const available = order.filter((w) => windowOptions[w]);
    if (!state.windowKey || !available.includes(state.windowKey)) {
      state.windowKey = available[available.length - 1] || null;
    }
    wrap.innerHTML = available.map((w) => {
      const n = windowOptions[w].n;
      const cls = "win-btn" + (w === state.windowKey ? " active" : "");
      const wl = w === "max" ? "Max" : w.toUpperCase();
      return '<button class="' + cls + '" data-window="' + w + '">' + wl + " (" + n + ")</button>";
    }).join("");
    wrap.querySelectorAll(".win-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.windowKey = btn.getAttribute("data-window");
        syncUrl();
        renderChartAndStrip();
      });
    });
  }

  // ---- Strip (last print) + target note ------------------------------------

  function renderStrip(meta, points) {
    const strip = document.getElementById("historyStrip");
    function item(label, value, cls) {
      return '<div class="hs-item"><span class="hs-label">' + label + '</span>' +
        '<span class="hs-value' + (cls ? " " + cls : "") + '">' + value + "</span></div>";
    }
    const unitSuffix = meta.unit ? " " + meta.unit : "";

    // FAZA 2C 3 — the header strip describes the LAST REAL print (an actual
    // value exists), never a scheduled/pending release. `points` can end
    // with a not-yet-printed row (release_dt in the future, most commonly —
    // that's the bug this fixes: AUD inflation showed "No print yet" as if
    // it were the latest data) — that row belongs on the forecast line and
    // in the "Next" note below, never masquerading as "the latest actual."
    let lastRealIdx = -1;
    for (let i = points.length - 1; i >= 0; i--) {
      if (points[i].actual !== null && points[i].actual !== undefined) { lastRealIdx = i; break; }
    }
    const last = lastRealIdx !== -1 ? points[lastRealIdx] : null;

    if (!last) {
      strip.innerHTML = '<div class="hs-item"><span class="hs-value">No prints in this window</span></div>';
    } else {
      // FAZA 1H 4 — Delta is the surprise (actual-as-published vs forecast),
      // same reasoning/helper as the tooltip (point 3) — never actual-vs-
      // previous (that was the old, removed "Delta (Act-Prev)").
      const originalActual = originalActualOf(last);
      const delta = (originalActual !== null && last.forecast !== null) ? originalActual - last.forecast : null;
      const deltaCls = delta === null ? "" : delta > 0 ? "hs-delta-pos" : delta < 0 ? "hs-delta-neg" : "";
      const statusNote = last.score_status !== "scored" ? " (" + STATUS_LABEL[last.score_status] + ")" : "";
      let html =
        item("Release", fmtDate(last.release_dt)) +
        item("Actual", fmtNum(last.actual) + unitSuffix + statusNote) +
        item("Forecast", fmtNum(last.forecast) + unitSuffix) +
        item("Delta (Act-Fcst)", fmtNum(delta) + unitSuffix, deltaCls);

      // A pending release after the last real print (scheduled but not yet
      // printed) — its own line, so what's expected next is visible without
      // implying it already happened.
      const next = points.slice(lastRealIdx + 1).find((p) => p.forecast !== null && p.forecast !== undefined);
      if (next) {
        html += item("Next", fmtDate(next.release_dt) + ", forecast " + fmtNum(next.forecast) + unitSuffix);
      }
      strip.innerHTML = html;
    }

    const noteEl = document.getElementById("historyTargetNote");
    let noteText = "";
    if (meta.target_note) {
      noteText = "Policy target: " + meta.target_note;
    } else if (meta.target) {
      const t = meta.target;
      noteText = "Policy target: " + (t.kind === "point"
        ? t.value + "%" : t.low + "–" + t.high + "%") +
        (t.effective_from ? " (since " + t.effective_from + ")" : "");
    }
    if (noteText) { noteEl.textContent = noteText; noteEl.hidden = false; } else { noteEl.hidden = true; }
  }

  // ---- Chart: shared annotations --------------------------------------------

  // FAZA 2B 3.1/3.3 — a target band/line is only drawn on a series that is
  // actually comparable to it, i.e. y/y (comparable_with_target, derived
  // server-side from transform_real — see history_compute.build_payload).
  // A m/m or q/q series never reaches here with a `target` object in the
  // first place (the catalog only carries `target_note`, plain text, for
  // those — see renderStrip), but the gate is kept as a second, explicit
  // guard so a future catalog edit can never silently draw an annual-rate
  // line across a monthly-change chart.
  //
  // `target.label`, when present, overrides the default "X% target" content
  // — used where the currency's real policy target sits on a DIFFERENT
  // underlying series than the one on screen (e.g. BoC targets headline
  // CPI, not the Median/Trimmed core measure charted here) — the reader
  // must see that distinction on the chart itself, not just infer it.
  function buildAnnotations(meta, colors) {
    const annotations = {};
    const target = meta.target;
    if (!target || !meta.comparable_with_target) return annotations;
    if (target.kind === "band") {
      annotations.targetBand = {
        type: "box", yMin: target.low, yMax: target.high,
        backgroundColor: colors.targetBand, borderWidth: 0,
      };
      if (target.label) {
        // Anchored at the band's own top edge (y: "start") rather than
        // vertically centered — the band's numeric range overlaps the bars'
        // own value range by design, so centering the label would run it
        // straight through the tallest bars instead of sitting above them.
        annotations.targetBand.label = {
          display: true, content: target.label, position: { x: "end", y: "start" },
          backgroundColor: colors.targetLine, color: "#fff",
          font: { size: 10, weight: "600" }, padding: { x: 6, y: 2 },
        };
      }
    } else if (target.kind === "point") {
      annotations.targetLine = {
        type: "line", yMin: target.value, yMax: target.value,
        borderColor: colors.targetLine, borderWidth: 1.5, borderDash: [6, 4],
        label: {
          display: true, content: target.label || (target.value + "% target"), position: "end",
          backgroundColor: colors.targetLine, color: "#fff",
          font: { size: 10, weight: "600" }, padding: { x: 6, y: 2 },
        },
      };
    }
    return annotations;
  }

  // ---- Chart: bar path (inflation / growth / labor) -------------------------

  function buildBarDatasets(points, colors) {
    // FAZA 2C 1 — a single accent color for every bar. bucket/z-score
    // coloring is gone entirely (see themeColors); the bar-vs-forecast-line
    // relationship is the only surprise signal left, and it's unambiguous.
    // FAZA 2C 3 — a future/not-yet-printed row (actual null) gets `null`
    // here, so Chart.js draws no bar for it at all — only a point on the
    // forecast line (see the forecast dataset below).
    const actualData = points.map((p) => (p.actual === null || p.actual === undefined ? null : p.actual));
    // audit B2: a recovered (revised) value — from the next print's previous,
    // never scored — is drawn HOLLOW (outline only) so it never reads as a
    // first release.
    return [{
      type: "bar", label: "Actual", data: actualData,
      backgroundColor: points.map((p) => (isHollow(p) ? "transparent" : colors.accent)),
      borderColor: colors.accent,
      borderWidth: points.map((p) => (isHollow(p) ? 2 : 0)),
      order: 2,
    }];
  }

  // ---- Chart: step path (rates — a policy rate is a step function, not a
  // bar-per-print; FAZA 1D 3.2) ------------------------------------------

  function buildStepDatasets(points, colors) {
    // Quarantined prints (FAZA 1A data_integrity, display-only — e.g. JPY's
    // corrupted zero rows) must NOT bend the step line down to 0: they are
    // excluded from the line's data (spanGaps:false below turns that into a
    // visible gap) and rendered instead as a separate, distinct marker
    // pinned at the last known-good level, so the discontinuity is visible
    // rather than either a false drop-to-zero or a silently smoothed-over gap.
    //
    // FAZA 2C 1 — one accent color throughout (no more bucket coloring on
    // the "changed" markers); a level change is still visible via radius
    // (6 vs 2.5), just not via color.
    let lastGood = null;
    const radii = [];
    const fills = [];   // audit B2: a recovered (revised) value is drawn as a hollow point
    const actualData = [];
    const quarantineY = [];
    points.forEach((p) => {
      const isQuarantined = p.score_status === "quarantined";
      const hasActual = p.actual !== null && p.actual !== undefined;
      if (isQuarantined || !hasActual) {
        actualData.push(null);
        radii.push(0);
        fills.push(colors.accent);
        quarantineY.push(isQuarantined ? lastGood : null);
        return;
      }
      const changed = lastGood !== null && p.actual !== lastGood;
      radii.push(isHollow(p) ? 5 : (changed ? 6 : 2.5));
      fills.push(isHollow(p) ? "transparent" : colors.accent);
      actualData.push(p.actual);
      quarantineY.push(null);
      lastGood = p.actual;
    });

    const datasets = [{
      type: "line", label: "Actual", data: actualData, stepped: "before",
      spanGaps: false, borderColor: colors.accent, borderWidth: 2,
      pointRadius: radii, pointBackgroundColor: fills, pointBorderColor: colors.accent,
      pointBorderWidth: 2,
      order: 2,
    }];
    if (quarantineY.some((v) => v !== null)) {
      datasets.push({
        type: "line", label: "Quarantined", data: quarantineY, showLine: false,
        pointStyle: "crossRot", pointRadius: 6, pointBorderWidth: 2,
        pointBorderColor: colors.quarantineMarker, pointBackgroundColor: "transparent",
        order: 1, _isQuarantineDataset: true,
      });
    }
    return datasets;
  }

  // ---- Chart: assembly --------------------------------------------------

  function buildChartConfig(meta, points) {
    const colors = themeColors();
    const labels = points.map((p) => fmtDate(p.release_dt));
    const isStep = meta.chart_type === "step";

    const mainDatasets = isStep ? buildStepDatasets(points, colors) : buildBarDatasets(points, colors);
    const quarantineIdx = mainDatasets.findIndex((d) => d._isQuarantineDataset);

    // FAZA 2C 2 — a continuous line joining every forecast point (not a
    // per-print tick): draws OVER the bars, in a contrasting coral/red so it
    // stays legible against the accent-blue bars, with a circular marker on
    // every print (including a future/not-yet-printed one — FAZA 2C 3, that
    // point has no bar but still lands on this line, showing what's expected
    // without pretending it already happened). spanGaps:false — a genuinely
    // unknown forecast (rare, e.g. a manual override row) leaves a visible
    // gap rather than a fabricated interpolation.
    const forecastData = points.map((p) => (p.forecast === null || p.forecast === undefined ? null : p.forecast));

    const datasets = mainDatasets.concat([
      {
        type: "line", label: "Forecast", data: forecastData,
        borderColor: colors.forecastLine, borderWidth: 2, spanGaps: false, fill: false,
        pointStyle: "circle", pointRadius: 3, pointHoverRadius: 4,
        pointBackgroundColor: colors.forecastLine, pointBorderColor: colors.forecastLine,
        order: 1,
      },
    ]);
    const forecastIdx = datasets.length - 1;

    const annotations = buildAnnotations(meta, colors);

    const yTitle = meta.unit ? meta.unit : undefined;

    return {
      data: { labels: labels, datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          annotation: { annotations: annotations },
          tooltip: {
            backgroundColor: colors.tooltipBg, titleColor: colors.tooltipFg, bodyColor: colors.tooltipFg,
            borderColor: colors.tooltipBorder, borderWidth: 1, padding: 10,
            // FAZA 2C 3 — a future/not-yet-printed row has a value on the
            // forecast dataset only (the bar/step dataset is null there); a
            // real print has a value on BOTH datasets simultaneously
            // ("index" mode activates both). Filter down to exactly one
            // active item per point so the label callback below never
            // emits the same info twice.
            filter: (item) => {
              if (quarantineIdx !== -1 && item.datasetIndex === quarantineIdx) return true;
              const p = points[item.dataIndex];
              const hasActual = p.actual !== null && p.actual !== undefined;
              return hasActual ? item.datasetIndex === 0 : item.datasetIndex === forecastIdx;
            },
            callbacks: {
              title: (items) => points[items[0].dataIndex].release_dt.slice(0, 10),
              label: (ctx) => {
                if (quarantineIdx !== -1 && ctx.datasetIndex === quarantineIdx) {
                  return "Quarantined print (data-quality flag — excluded from the step line)";
                }
                const p = points[ctx.dataIndex];
                const hasActual = p.actual !== null && p.actual !== undefined;
                if (!hasActual) {
                  return (p.forecast !== null && p.forecast !== undefined)
                    ? "Forecast: " + fmtNum(p.forecast) + " (not printed yet)" : null;
                }
                return pointTooltipLines(p);
              },
            },
          },
        },
        scales: {
          x: {
            ticks: { color: colors.tick, maxRotation: 0, autoSkip: true, autoSkipPadding: 20, font: { size: 10 } },
            grid: { color: colors.grid, drawTicks: false },
            border: { color: colors.grid },
          },
          y: {
            title: yTitle ? { display: true, text: yTitle, color: colors.tick, font: { size: 10 } } : undefined,
            ticks: { color: colors.tick, font: { size: 10 } },
            grid: { color: colors.grid, drawTicks: false },
            border: { color: colors.grid },
          },
        },
      },
    };
  }

  // FAZA 1G 3.2 — a STALE series (real data exists, but the latest print is
  // older than its recency window) or a NO-DATA one (catalog entry, zero
  // real prints ever — data_integrity/matcher/typo issue) must say so
  // explicitly, not just let the chart quietly stop or show a generic
  // empty-window message. Uses the `stale`/`has_data`/`age_days`/
  // `last_print_release_dt` fields history_compute.build_payload stamps on
  // every entry (FAZA 1G 3.1) — computed once at generation time, not
  // re-derived here.
  function renderStaleBanner(meta) {
    const el = document.getElementById("historyStaleBanner");
    if (!meta.has_data) {
      el.textContent = "No data: this catalog entry has zero real prints — likely a config/matcher issue, not a legitimately quiet series.";
      el.hidden = false;
    } else if (meta.stale) {
      el.textContent = "Last print: " + fmtDate(meta.last_print_release_dt) + " (" + meta.age_days +
        "d ago) — this series appears to have stopped printing.";
      el.hidden = false;
    } else {
      el.hidden = true;
    }
  }

  // FAZA 2B 1 — the hatch swatch is gone along with the hatch fill itself:
  // "in-line" and "no score yet" now share the same neutral color and are
  // deliberately indistinguishable (both mean "nothing notable"), so there
  // is nothing left to explain a hatch pattern for.
  // FAZA 2C 1/2 — the single accent color for every "Actual" bar/step (no
  // more Beat/In-line/Miss buckets) and the coral/red forecast line, plus the
  // hollow "not scored" bar when the window shows one. A distinct swatch shape
  // per kind (filled box, line, outline) so the legend hints at how each is drawn.
  function renderLegend(points) {
    const el = document.getElementById("historyLegend");
    if (!el) return;
    const colors = themeColors();
    // A hollow bar is explained only when the window on screen has one.
    const hollow = (points || []).some((p) => p.actual !== null && p.actual !== undefined && isHollow(p));
    el.innerHTML =
      '<span class="hl-item"><span class="hl-swatch" style="background:' + colors.accent + ';"></span>Actual</span>' +
      '<span class="hl-item"><span class="hl-swatch hl-line" style="background:' + colors.forecastLine + ';"></span>Forecast</span>' +
      (hollow ? '<span class="hl-item"><span class="hl-swatch hl-hollow" style="border-color:' + colors.accent +
        ';"></span>Not scored (revised or final value only)</span>' : "");
  }

  function renderChartAndStrip() {
    renderLegend([]);
    const wrapper = document.querySelector(".chart-wrapper");
    const resolved = resolveEntry(state.ccy, state.key);

    if (!resolved) {
      // No series at all for this (category, currency) — e.g. rates/CHF
      // (stale, omitted from the catalog) or growth/JPY (n=3, below the
      // 4-point minimum). FAZA 1D 3.2: this must be an explicit message,
      // never a silently blank tab.
      document.getElementById("historyWindowSelector").innerHTML = "";
      document.getElementById("historyStrip").innerHTML = "";
      document.getElementById("historyTargetNote").hidden = true;
      document.getElementById("historyStaleBanner").hidden = true;
      wrapper.innerHTML = '<p class="history-empty-category">No ' + CATEGORY_LABEL[state.category] +
        " data for " + state.ccy + " — this series is omitted from the catalog " +
        "(insufficient/stale history; see data/econ_catalog.yml).</p>";
      return;
    }

    const { meta, windowOptions, refBroken } = resolved;
    if (refBroken) {
      document.getElementById("historyWindowSelector").innerHTML = "";
      document.getElementById("historyStrip").innerHTML = "";
      document.getElementById("historyTargetNote").hidden = true;
      document.getElementById("historyStaleBanner").hidden = true;
      wrapper.innerHTML = '<p class="history-empty-category">Internal error: could not resolve the shared ' +
        "series data for this entry (points_ref target missing). Reported to the console.</p>";
      return;
    }

    renderStaleBanner(meta);
    renderWindowSelector(windowOptions);
    const w = windowOptions[state.windowKey];
    const points = w ? w.points : [];

    renderStrip(meta, points);
    renderLegend(points);

    if (!points.length) {
      wrapper.innerHTML = '<p class="history-empty-window">No printed rows in this window.</p>';
      return;
    }
    if (!wrapper.querySelector("canvas")) wrapper.innerHTML = '<canvas id="historyChart"></canvas>';
    const cfg = buildChartConfig(meta, points);
    if (state.chart) state.chart.destroy();
    state.chart = new Chart(document.getElementById("historyChart"), cfg);
  }

  function renderAll() {
    renderSeriesChips();
    renderChartAndStrip();
  }

  // ---- Deep-link (?cat=growth&ccy=CAD&range=2y&key=unemployment_rate) —
  // same pattern as /strength's ?ccy=.

  function stateFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const cats = availableCategories();
    const catParam = params.get("cat");
    state.category = cats.includes(catParam) ? catParam : cats[0];

    const ccys = allCurrencies();
    const ccyParam = (params.get("ccy") || "").toUpperCase();
    state.ccy = ccys.includes(ccyParam) ? ccyParam : ccys[0];

    const entries = visibleEntries(state.ccy);
    const keyParam = params.get("key");
    const roleParam = params.get("role");
    let legacyRoleLink = false;
    if (keyParam && entries.find((e) => e.indicator_key === keyParam)) {
      state.key = keyParam;
    } else if (roleParam) {
      // FAZA 2E-2 — back-compat for a bookmarked/shared ?role= link (the
      // OLD, ambiguous identifier — resolve exactly like the pre-fix code
      // did, first entry with that role, so an old bookmark never lands on
      // a blank page) — then rewrite the URL below so a reload/re-share
      // uses the real, unambiguous ?key= form from now on.
      const match = entries.find((e) => e.role === roleParam);
      state.key = match ? match.indicator_key : (entries[0] ? entries[0].indicator_key : null);
      legacyRoleLink = true;
    } else {
      state.key = entries[0] ? entries[0].indicator_key : null;
    }
    const rangeParam = params.get("range");
    state.windowKey = ["1y", "2y", "max"].includes(rangeParam) ? rangeParam : null;

    if (legacyRoleLink) syncUrl();
  }

  function syncUrl() {
    const url = new URL(window.location.href);
    url.searchParams.set("cat", state.category);
    url.searchParams.set("ccy", state.ccy);
    if (state.key) url.searchParams.set("key", state.key); else url.searchParams.delete("key");
    url.searchParams.delete("role");   // FAZA 2E-2 — legacy identifier, always rewritten to `key`
    if (state.windowKey) url.searchParams.set("range", state.windowKey);
    window.history.pushState({}, "", url);
  }

  // ---- Meta bar (FAZA 1G 2.3) ------------------------------------------
  // Same source/shape as /economic's "As of ..." stamp (payload.as_of /
  // payload.generated_at) — if this page ever stops getting regenerated by
  // the cron alongside /economic, the two stamps drift apart and that's
  // immediately visible side by side, instead of a page that looks live but
  // silently isn't.

  function renderMeta() {
    const el = document.getElementById("historyMeta");
    if (!el) return;
    const meta = state.payload.meta || {};
    const parts = ["As of " + fmtAsOf(meta.as_of)];
    if (meta.generated_at) parts.push("Generated " + fmtAsOf(meta.generated_at));
    el.innerHTML = '<span class="muted">' + parts.join(" · ") + "</span>";
  }

  // ---- Bootstrap ------------------------------------------------------------

  function boot() {
    renderMeta();
    if (!availableCategories().length) {
      document.querySelector(".chart-section").innerHTML =
        '<p class="history-empty-window">No history data in payload.</p>';
      return;
    }
    stateFromUrl();
    renderCategoryTabs();
    populateCcySelect();
    document.getElementById("historyCcySelect").value = state.ccy;
    renderAll();

    const observer = new MutationObserver(() => renderChartAndStrip());
    observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    window.addEventListener("popstate", () => {
      stateFromUrl();
      renderCategoryTabs();
      populateCcySelect();
      document.getElementById("historyCcySelect").value = state.ccy;
      renderAll();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // ---- Test seam (FAZA 1D 4.3) -----------------------------------------
  // Pure-data functions only (no DOM access) — exported under a CommonJS
  // guard so a plain `node` script can exercise payload-resolution logic
  // (resolveEntry / points_ref dedup, the P1.4 bug class) without a browser
  // or a DOM shim. No-op in the browser: `module` is undefined there.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { state, resolveEntry, seriesEntries, availableCategories, allCurrencies,
                       buildBarDatasets, buildStepDatasets, pointTooltipLines };
  }
})();
