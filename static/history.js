/* Economic History page chart logic (FAZA 1C pilot — inflation only).
   Depends on Chart.js v4 UMD + chartjs-plugin-annotation. Payload is embedded
   in the page (window.HISTORY_PAYLOAD) — no runtime fetch, unlike the other
   pages on this site. */
(function () {
  "use strict";

  const PILOT_CATEGORY = "inflation";

  const state = {
    payload: window.HISTORY_PAYLOAD || { categories: {} },
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

  // ---- Series resolution (P1.4: policy entries reference a sibling) --------

  function seriesEntries(ccy) {
    const cat = state.payload.categories[PILOT_CATEGORY] || {};
    return cat[ccy] || [];
  }

  // Returns {meta, points_by_window} — `meta` is the entry's OWN metadata
  // (label/target/rank), points_by_window is resolved through points_ref if
  // this entry doesn't carry its own window_options (P1.4 dedup).
  function resolveEntry(ccy, role) {
    const entries = seriesEntries(ccy);
    const entry = entries.find((e) => e.role === role);
    if (!entry) return null;
    if (entry.points_ref) {
      const target = entries.find((e) => e.role === entry.points_ref.role);
      return { meta: entry, windowOptions: target ? target.window_options : {}, quarantineCount: target ? target.quarantine_count : 0 };
    }
    return { meta: entry, windowOptions: entry.window_options || {}, quarantineCount: entry.quarantine_count || 0 };
  }

  // ---- Controls ---------------------------------------------------------

  function populateCcySelect() {
    const sel = document.getElementById("historyCcySelect");
    const cat = state.payload.categories[PILOT_CATEGORY] || {};
    const ccys = Object.keys(cat).sort();   // derived from presence in the payload, not a fixed list
    sel.innerHTML = ccys.map((c) => '<option value="' + c + '">' + c + "</option>").join("");
    sel.value = state.ccy;
    sel.addEventListener("change", () => {
      state.ccy = sel.value;
      const first = seriesEntries(state.ccy)[0];
      state.role = first ? first.role : null;
      state.windowKey = null;
      syncUrl();
      renderAll();
    });
  }

  function renderSeriesChips() {
    const wrap = document.getElementById("historySeriesChips");
    const entries = seriesEntries(state.ccy).slice().sort((a, b) => a.rank - b.rank);
    wrap.innerHTML = "";
    entries.forEach((e) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "history-series-chip" + (e.role === state.role ? " active" : "");
      let label = e.role.charAt(0).toUpperCase() + e.role.slice(1);
      if (e.indicator_key) label += ": " + (e.display_label || e.indicator_key);
      if (e.label_source === "derived") label += ' <span class="label-derived-mark" title="Corrected label — the production canonical name did not match the real feed (see catalog mismatch_note)">*</span>';
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
    if (!last) {
      strip.innerHTML = '<div class="hs-item"><span class="hs-value">No prints in this window</span></div>';
    } else {
      const delta = (last.actual !== null && last.previous !== null) ? last.actual - last.previous : null;
      const deltaCls = delta === null ? "" : delta > 0 ? "hs-delta-pos" : delta < 0 ? "hs-delta-neg" : "";
      const statusNote = last.score_status !== "scored" ? " (" + STATUS_LABEL[last.score_status] + ")" : "";
      strip.innerHTML =
        item("Release", fmtDate(last.release_dt)) +
        item("Actual", fmtNum(last.actual) + statusNote) +
        item("Forecast", fmtNum(last.forecast)) +
        item("Previous", fmtNum(last.previous)) +
        item("Delta (Act-Prev)", fmtNum(delta), deltaCls) +
        (last.revised_from !== null && last.revised_from !== undefined
          ? item("Revised from", fmtNum(last.revised_from)) : "");
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

  // ---- Chart ----------------------------------------------------------------

  function buildAnnotations(meta, colors, xLabels) {
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

  function buildRevisionAnnotations(points, colors) {
    const anns = {};
    points.forEach((p, i) => {
      if (p.revised_from === null || p.revised_from === undefined) return;
      const key = "rev" + i;
      anns[key] = {
        type: "line", xMin: i, xMax: i, yMin: p.revised_from, yMax: p.actual,
        borderColor: colors.revisionMarker, borderWidth: 1.5, borderDash: [2, 2],
      };
    });
    return anns;
  }

  function buildChartConfig(meta, points) {
    const colors = themeColors();
    const labels = points.map((p) => fmtDate(p.release_dt));
    const barColors = points.map((p) => {
      const base = bucketColor(p.bucket, colors);
      return p.score_status === "scored" ? base : hatchPattern(base || colors.neutral);
    });
    const actualData = points.map((p) => (p.actual === null || p.actual === undefined ? null : p.actual));
    const forecastData = points.map((p) => (p.forecast === null || p.forecast === undefined ? null : p.forecast));
    const revisedPoints = points.map((p) => (p.revised_from === null || p.revised_from === undefined ? null : p.revised_from));

    const datasets = [
      {
        type: "bar", label: "Actual", data: actualData,
        backgroundColor: barColors, borderWidth: 0, order: 2,
      },
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
    ];

    const annotations = Object.assign({}, buildAnnotations(meta, colors, labels),
      buildRevisionAnnotations(points, colors));

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
            ticks: { color: colors.tick, font: { size: 10 } },
            grid: { color: colors.grid, drawTicks: false },
            border: { color: colors.grid },
          },
        },
      },
    };
  }

  function renderChartAndStrip() {
    const resolved = resolveEntry(state.ccy, state.role);
    if (!resolved) return;
    const { meta, windowOptions } = resolved;
    renderWindowSelector(windowOptions);
    const w = windowOptions[state.windowKey];
    const points = w ? w.points : [];

    renderStrip(meta, points);

    const wrapper = document.querySelector(".chart-wrapper");
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

  // ---- Deep-link (?cat=inflation&ccy=USD&range=2y) — same pattern as
  // /strength's ?ccy=. `cat` is accepted but pinned to "inflation" in this
  // pilot (the other 3 categories are not rendered yet).

  function stateFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const cat = state.payload.categories[PILOT_CATEGORY] || {};
    const ccyParam = (params.get("ccy") || "").toUpperCase();
    const ccys = Object.keys(cat).sort();
    state.ccy = cat[ccyParam] ? ccyParam : ccys[0];
    const roleParam = params.get("role");
    const entries = seriesEntries(state.ccy);
    state.role = (roleParam && entries.find((e) => e.role === roleParam)) ? roleParam
      : (entries[0] ? entries[0].role : null);
    const rangeParam = params.get("range");
    state.windowKey = ["1y", "2y", "max"].includes(rangeParam) ? rangeParam : null;
  }

  function syncUrl() {
    const url = new URL(window.location.href);
    url.searchParams.set("cat", PILOT_CATEGORY);
    url.searchParams.set("ccy", state.ccy);
    url.searchParams.set("role", state.role);
    if (state.windowKey) url.searchParams.set("range", state.windowKey);
    window.history.pushState({}, "", url);
  }

  // ---- Bootstrap ------------------------------------------------------------

  function boot() {
    if (!state.payload.categories || !state.payload.categories[PILOT_CATEGORY]) {
      document.querySelector(".chart-section").innerHTML =
        '<p class="history-empty-window">No inflation data in payload.</p>';
      return;
    }
    stateFromUrl();
    populateCcySelect();
    document.getElementById("historyCcySelect").value = state.ccy;
    renderAll();

    const observer = new MutationObserver(() => renderChartAndStrip());
    observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    window.addEventListener("popstate", () => { stateFromUrl(); populateCcySelect(); renderAll(); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
