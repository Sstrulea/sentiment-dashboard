/* Economic History page chart logic (FAZA 1C shipped inflation only; FAZA 1D
   extends to all 4 catalog categories: inflation/growth/labor/rates).
   Depends on Chart.js v4 UMD + chartjs-plugin-annotation. Payload is embedded
   in the page (window.HISTORY_PAYLOAD) — no runtime fetch, unlike the other
   pages on this site. */
(function () {
  "use strict";

  const CATEGORY_ORDER = ["inflation", "growth", "labor", "rates"];
  const CATEGORY_LABEL = { inflation: "Inflation", growth: "Growth", labor: "Labor", rates: "Rates" };

  const state = {
    payload: (typeof window !== "undefined" && window.HISTORY_PAYLOAD) || { categories: {} },
    category: null,
    ccy: null,
    role: null,
    windowKey: null,
    chart: null,
  };

  // ---- Theme (same convention as vix-chart.js / pc-chart.js) --------------

  function isDarkTheme() {
    return document.body.classList.contains("dark");
  }

  // Same discrete bias palette as /economic (static/style.css .bias-*, and
  // economic-chart.js's gradientStyle(v, 2) for an atomic -2..+2 indicator
  // score) — reused verbatim, not reinvented.
  const BUCKET_COLOR = {
    2: "#1565c0", 1: "#90caf9", 0: null /* theme-dependent neutral, see below */,
    "-1": "#ef9a9a", "-2": "#d32f2f",
  };

  function themeColors() {
    const dark = isDarkTheme();
    return {
      primary: dark ? "#ffffff" : "#1a1a1a",
      grid: dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
      tick: dark ? "#9aa0a6" : "#666",
      neutral: dark ? "#3a3f45" : "#e0e0e0",
      forecastTick: dark ? "#eee" : "#333",
      targetLine: dark ? "#ffc107" : "#a67600",
      targetBand: dark ? "rgba(255, 193, 7, 0.10)" : "rgba(255, 193, 7, 0.14)",
      revisionMarker: dark ? "#eee" : "#333",
      revisionSpot: "#ab47bc",       // fixed (non-theme) color — deliberately
                                      // high-contrast in both themes so a
                                      // revision is spottable on a dense
                                      // (Max, 40+ point) chart without hover.
      quarantineMarker: dark ? "#9aa0a6" : "#757575",
      tooltipBg: dark ? "rgba(10,10,10,0.95)" : "rgba(255,255,255,0.95)",
      tooltipFg: dark ? "#eee" : "#222",
      tooltipBorder: dark ? "#333" : "#ccc",
    };
  }

  function bucketColor(bucket, colors) {
    if (bucket === null || bucket === undefined || Number.isNaN(bucket)) return colors.neutral;
    const b = Math.max(-2, Math.min(2, Math.round(bucket)));
    return BUCKET_COLOR[b] || colors.neutral;
  }

  // Diagonal-hatch CanvasPattern for a not-fully-scored bar (score_status !=
  // "scored") — same visual idea as .ec-stale's repeating-linear-gradient on
  // /economic, translated to a Chart.js-compatible fill.
  const hatchCache = {};
  function hatchPattern(baseColor) {
    if (hatchCache[baseColor]) return hatchCache[baseColor];
    const size = 8;
    const c = document.createElement("canvas");
    c.width = size; c.height = size;
    const ctx = c.getContext("2d");
    ctx.fillStyle = baseColor;
    ctx.globalAlpha = 0.35;
    ctx.fillRect(0, 0, size, size);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = baseColor;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(0, size); ctx.lineTo(size, 0);
    ctx.moveTo(-size / 2, size / 2); ctx.lineTo(size / 2, -size / 2);
    ctx.moveTo(size / 2, size + size / 2); ctx.lineTo(size + size / 2, size / 2);
    ctx.stroke();
    const pattern = document.createElement("canvas").getContext("2d").createPattern(c, "repeat");
    hatchCache[baseColor] = pattern;
    return pattern;
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
  const STATUS_LABEL = {
    scored: "Scored", no_actual: "No print yet",
    insufficient_history: "Not enough history for a full score", quarantined: "Quarantined (data-quality)",
  };
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

  // ---- Series resolution (P1.4: policy entries reference a sibling) --------

  function seriesEntries(ccy) {
    const cat = state.payload.categories[state.category] || {};
    return cat[ccy] || [];
  }

  function sortedEntries(ccy) {
    return seriesEntries(ccy).slice().sort((a, b) => a.rank - b.rank);
  }

  // Returns {meta, windowOptions, quarantineCount} — `meta` is the entry's
  // OWN metadata (label/target/rank), windowOptions is resolved through
  // points_ref if this entry doesn't carry its own window_options (P1.4
  // dedup). Returns null only if the role itself doesn't exist for this ccy.
  // FAZA 1D 4.1: if points_ref names a role that isn't actually present
  // (a catalog/payload-building bug), fail LOUDLY — console.error plus a
  // visible fallback — instead of silently returning an empty
  // windowOptions (which previously rendered as a blank chart, indistinguishable
  // from "no data this window").
  function resolveEntry(ccy, role) {
    const entries = seriesEntries(ccy);
    const entry = entries.find((e) => e.role === role);
    if (!entry) return null;
    if (entry.points_ref) {
      const target = entries.find((e) => e.role === entry.points_ref.role);
      if (!target) {
        console.error(
          "history.js: points_ref target role '" + entry.points_ref.role + "' not found for " +
          ccy + "/" + entry.indicator_key + " (role=" + role + "). Payload/catalog bug — showing fallback.");
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
        // only re-pick a role — the old one may not exist in the new category.
        const entries = sortedEntries(state.ccy);
        const stillValid = entries.find((e) => e.role === state.role);
        state.role = stillValid ? state.role : (entries[0] ? entries[0].role : null);
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
      const entries = sortedEntries(state.ccy);
      state.role = entries[0] ? entries[0].role : null;
      state.windowKey = null;
      syncUrl();
      renderAll();
    });
  }

  function renderSeriesChips() {
    const wrap = document.getElementById("historySeriesChips");
    const entries = sortedEntries(state.ccy);
    wrap.innerHTML = "";
    entries.forEach((e) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "history-series-chip" + (e.role === state.role ? " active" : "");
      let label = e.role.charAt(0).toUpperCase() + e.role.slice(1);
      if (e.indicator_key) label += ": " + (e.display_label || e.indicator_key);
      if (e.label_source === "derived") label += ' <span class="label-derived-mark" title="Corrected label — the production canonical name did not match the real feed (see catalog mismatch_note)">*</span>';
      if (e.cadence_empirical) {
        const abbr = CADENCE_ABBR[e.cadence_empirical] || "?";
        label += ' <span class="history-cadence-badge" title="Empirical print cadence: ' + e.cadence_empirical +
          ' — adjacent points on this chart are NOT evenly spaced in time">' + abbr + "</span>";
      }
      btn.innerHTML = label;
      btn.addEventListener("click", () => {
        state.role = e.role;
        state.windowKey = null;
        syncUrl();
        renderAll();
      });
      wrap.appendChild(btn);
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
    const last = points.length ? points[points.length - 1] : null;
    function item(label, value, cls) {
      return '<div class="hs-item"><span class="hs-label">' + label + '</span>' +
        '<span class="hs-value' + (cls ? " " + cls : "") + '">' + value + "</span></div>";
    }
    const unitSuffix = meta.unit ? " " + meta.unit : "";
    if (!last) {
      strip.innerHTML = '<div class="hs-item"><span class="hs-value">No prints in this window</span></div>';
    } else {
      const delta = (last.actual !== null && last.previous !== null) ? last.actual - last.previous : null;
      const deltaCls = delta === null ? "" : delta > 0 ? "hs-delta-pos" : delta < 0 ? "hs-delta-neg" : "";
      const statusNote = last.score_status !== "scored" ? " (" + STATUS_LABEL[last.score_status] + ")" : "";
      strip.innerHTML =
        item("Release", fmtDate(last.release_dt)) +
        item("Actual", fmtNum(last.actual) + unitSuffix + statusNote) +
        item("Forecast", fmtNum(last.forecast) + unitSuffix) +
        item("Previous", fmtNum(last.previous) + unitSuffix) +
        item("Delta (Act-Prev)", fmtNum(delta) + unitSuffix, deltaCls) +
        (last.revised_from !== null && last.revised_from !== undefined
          ? item("Revised from", fmtNum(last.revised_from) + unitSuffix) : "");
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

  function buildAnnotations(meta, colors) {
    const annotations = {};
    const target = meta.target;
    if (target && target.kind === "band") {
      annotations.targetBand = {
        type: "box", yMin: target.low, yMax: target.high,
        backgroundColor: colors.targetBand, borderWidth: 0,
      };
    } else if (target && target.kind === "point") {
      annotations.targetLine = {
        type: "line", yMin: target.value, yMax: target.value,
        borderColor: colors.targetLine, borderWidth: 1.5, borderDash: [6, 4],
        label: {
          display: true, content: target.value + "% target", position: "end",
          backgroundColor: colors.targetLine, color: "#fff",
          font: { size: 10, weight: "600" }, padding: { x: 6, y: 2 },
        },
      };
    }
    return annotations;
  }

  // FAZA 1D 4.2: a revision needs to be spottable WITHOUT hover, even on a
  // dense (Max, 40+ point) chart. The original dashed connector line reused
  // the bucket/neutral palette, which is easy to lose among the bars
  // themselves at that density. Keep the connector (useful once you DO look
  // closely / hover), but add a small filled dot in a fixed, theme-independent,
  // high-contrast color at the top of every revised bar — that's the part
  // meant to catch the eye at a glance.
  function buildRevisionAnnotations(points, colors) {
    const anns = {};
    points.forEach((p, i) => {
      if (p.revised_from === null || p.revised_from === undefined) return;
      anns["revLine" + i] = {
        type: "line", xMin: i, xMax: i, yMin: p.revised_from, yMax: p.actual,
        borderColor: colors.revisionMarker, borderWidth: 1.5, borderDash: [2, 2],
      };
      anns["revSpot" + i] = {
        type: "point", xValue: i, yValue: p.actual, radius: 3.5,
        backgroundColor: colors.revisionSpot, borderColor: "#fff", borderWidth: 1,
        drawTime: "afterDatasetsDraw",
      };
    });
    return anns;
  }

  // ---- Chart: bar path (inflation / growth / labor) -------------------------

  function buildBarDatasets(points, colors) {
    const barColors = points.map((p) => {
      const base = bucketColor(p.bucket, colors);
      return p.score_status === "scored" ? base : hatchPattern(base || colors.neutral);
    });
    const actualData = points.map((p) => (p.actual === null || p.actual === undefined ? null : p.actual));
    return [{
      type: "bar", label: "Actual", data: actualData,
      backgroundColor: barColors, borderWidth: 0, order: 2,
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
    let lastGood = null;
    const radii = [];
    const pointColors = [];
    const actualData = [];
    const quarantineY = [];
    points.forEach((p) => {
      const isQuarantined = p.score_status === "quarantined";
      const hasActual = p.actual !== null && p.actual !== undefined;
      if (isQuarantined || !hasActual) {
        actualData.push(null);
        radii.push(0);
        pointColors.push(colors.neutral);
        quarantineY.push(isQuarantined ? lastGood : null);
        return;
      }
      const changed = lastGood !== null && p.actual !== lastGood;
      radii.push(changed ? 6 : 2.5);
      pointColors.push(bucketColor(p.bucket, colors) || colors.neutral);
      actualData.push(p.actual);
      quarantineY.push(null);
      lastGood = p.actual;
    });

    const datasets = [{
      type: "line", label: "Actual", data: actualData, stepped: "before",
      spanGaps: false, borderColor: colors.primary, borderWidth: 2,
      pointRadius: radii, pointBackgroundColor: pointColors, pointBorderColor: pointColors,
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

    const forecastData = points.map((p) => (p.forecast === null || p.forecast === undefined ? null : p.forecast));
    const revisedPoints = points.map((p) => (p.revised_from === null || p.revised_from === undefined ? null : p.revised_from));

    const datasets = mainDatasets.concat([
      {
        type: "line", label: "Forecast", data: forecastData, showLine: false,
        pointStyle: "line", rotation: 90, pointRadius: 9, pointBorderWidth: 2,
        pointBorderColor: colors.forecastTick, pointBackgroundColor: colors.forecastTick,
        order: 1,
      },
      {
        type: "line", label: "Revised from", data: revisedPoints, showLine: false,
        pointStyle: "circle", pointRadius: 6, pointBorderWidth: 1.5,
        pointBackgroundColor: "transparent", pointBorderColor: colors.revisionMarker,
        order: 0,
      },
    ]);

    const annotations = Object.assign({}, buildAnnotations(meta, colors),
      buildRevisionAnnotations(points, colors));

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
            callbacks: {
              title: (items) => points[items[0].dataIndex].release_dt.slice(0, 10),
              label: (ctx) => {
                if (quarantineIdx !== -1 && ctx.datasetIndex === quarantineIdx) {
                  return "Quarantined print (data-quality flag — excluded from the step line)";
                }
                if (ctx.datasetIndex !== 0) return null;
                const p = points[ctx.dataIndex];
                const lines = [
                  "Actual:   " + fmtNum(p.actual),
                  "Forecast: " + fmtNum(p.forecast),
                  "Previous: " + fmtNum(p.previous),
                ];
                if (p.actual !== null && p.previous !== null) lines.push("Delta:    " + fmtNum(p.actual - p.previous));
                lines.push("z:        " + fmtNum(p.z, 3));
                lines.push("Bucket:   " + (p.bucket === null ? "—" : p.bucket));
                lines.push("Status:   " + STATUS_LABEL[p.score_status]);
                if (p.revised_from !== null && p.revised_from !== undefined) {
                  lines.push("Revised:  " + fmtNum(p.revised_from) + " → " + fmtNum(p.actual));
                }
                return lines;
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

  function renderChartAndStrip() {
    const wrapper = document.querySelector(".chart-wrapper");
    const resolved = resolveEntry(state.ccy, state.role);

    if (!resolved) {
      // No series at all for this (category, currency) — e.g. rates/CHF
      // (stale, omitted from the catalog) or growth/JPY (n=3, below the
      // 4-point minimum). FAZA 1D 3.2: this must be an explicit message,
      // never a silently blank tab.
      document.getElementById("historyWindowSelector").innerHTML = "";
      document.getElementById("historyStrip").innerHTML = "";
      document.getElementById("historyTargetNote").hidden = true;
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
      wrapper.innerHTML = '<p class="history-empty-category">Internal error: could not resolve the shared ' +
        "series data for this entry (points_ref target missing). Reported to the console.</p>";
      return;
    }

    renderWindowSelector(windowOptions);
    const w = windowOptions[state.windowKey];
    const points = w ? w.points : [];

    renderStrip(meta, points);

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

  // ---- Deep-link (?cat=growth&ccy=CAD&range=2y) — same pattern as
  // /strength's ?ccy=.

  function stateFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const cats = availableCategories();
    const catParam = params.get("cat");
    state.category = cats.includes(catParam) ? catParam : cats[0];

    const ccys = allCurrencies();
    const ccyParam = (params.get("ccy") || "").toUpperCase();
    state.ccy = ccys.includes(ccyParam) ? ccyParam : ccys[0];

    const roleParam = params.get("role");
    const entries = sortedEntries(state.ccy);
    state.role = (roleParam && entries.find((e) => e.role === roleParam)) ? roleParam
      : (entries[0] ? entries[0].role : null);
    const rangeParam = params.get("range");
    state.windowKey = ["1y", "2y", "max"].includes(rangeParam) ? rangeParam : null;
  }

  function syncUrl() {
    const url = new URL(window.location.href);
    url.searchParams.set("cat", state.category);
    url.searchParams.set("ccy", state.ccy);
    if (state.role) url.searchParams.set("role", state.role); else url.searchParams.delete("role");
    if (state.windowKey) url.searchParams.set("range", state.windowKey);
    window.history.pushState({}, "", url);
  }

  // ---- Bootstrap ------------------------------------------------------------

  function boot() {
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
    module.exports = { state, resolveEntry, seriesEntries, availableCategories, allCurrencies };
  }
})();
