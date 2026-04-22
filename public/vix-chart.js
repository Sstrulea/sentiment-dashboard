/* VIX/VIX3M page chart logic. Depends on Chart.js v4 UMD + chartjs-plugin-annotation. */
(function () {
  "use strict";

  const DATA_URL = window.VIX_DATA_URL || "/data/vix-ratio.json";

  const REGIME_COPY = {
    ACUTE_PANIC: {
      cls: "regime-acute-panic",
      sub: "Extreme short-term stress",
      swatchCls: "acute-panic",
    },
    BACKWARDATION: {
      cls: "regime-backwardation",
      sub: "Short-term volatility premium (stress building)",
      swatchCls: "backwardation",
    },
    NORMAL: {
      cls: "regime-normal",
      sub: "Healthy contango",
      swatchCls: "normal",
    },
    COMPLACENCY: {
      cls: "regime-complacency",
      sub: "Complacent pricing; potential risk building",
      swatchCls: "complacency",
    },
  };

  const state = {
    payload: null,
    window: "6M",
    showCrossovers: true,
    chart: null,
    hoveredAnn: null,
  };

  // ---- Theme ---------------------------------------------------------------

  function isDarkTheme() {
    return document.body.classList.contains("dark");
  }

  function themeColors() {
    const dark = isDarkTheme();
    return {
      primary: dark ? "#ffffff" : "#1a1a1a",
      grid: dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
      tick: dark ? "#9aa0a6" : "#666",
      label: dark ? "#eee" : "#222",
      panicLine: "#d32f2f",
      backwardLine: "#f44336",
      complacencyLine: "#ffc107",
      panicBand: "rgba(211, 47, 47, 0.08)",
      backwardBand: "rgba(244, 67, 54, 0.06)",
      normalBand: "rgba(76, 175, 80, 0.04)",
      complacencyBand: "rgba(255, 193, 7, 0.06)",
      crossUp: "#ff5252",
      crossDown: "#4caf50",
      tooltipBg: dark ? "rgba(10,10,10,0.95)" : "rgba(255,255,255,0.95)",
      tooltipFg: dark ? "#eee" : "#222",
      tooltipBorder: dark ? "#333" : "#ccc",
    };
  }

  // ---- Formatting ----------------------------------------------------------

  function fmt4(x) {
    return x === null || x === undefined || Number.isNaN(x) ? "—" : Number(x).toFixed(4);
  }
  function fmt2(x) {
    return x === null || x === undefined || Number.isNaN(x) ? "—" : Number(x).toFixed(2);
  }

  // ---- Signal card ---------------------------------------------------------

  function updateSignalCard() {
    const cur = state.payload.current;
    document.getElementById("vix-ratio-value").textContent = fmt4(cur.ratio);
    document.getElementById("vix-date").textContent = cur.date || "—";
    document.getElementById("vix-value").textContent = fmt2(cur.vix);
    document.getElementById("vix3m-value").textContent = fmt2(cur.vix3m);
    document.getElementById("vix-days-in-regime").textContent =
      cur.days_in_regime === null || cur.days_in_regime === undefined
        ? "—"
        : String(cur.days_in_regime);

    const regimeEl = document.getElementById("vix-regime");
    const subEl = document.getElementById("vix-regime-sub");
    const flipEl = document.getElementById("vix-regime-flip");

    const copy = REGIME_COPY[cur.regime] || { cls: "", sub: cur.regime };
    regimeEl.textContent = cur.regime;
    regimeEl.className = "regime-badge " + copy.cls;
    subEl.textContent = copy.sub;

    if (cur.regime_changed_today && cur.previous_regime) {
      flipEl.textContent = "Flipped today from " + cur.previous_regime;
      flipEl.hidden = false;
    } else {
      flipEl.hidden = true;
      flipEl.textContent = "";
    }
  }

  // ---- Chart ---------------------------------------------------------------

  function crossoverDatesInWindow(winKey) {
    const dates = new Set(state.payload.series[winKey].dates);
    const ups = state.payload.crossovers_above_1.filter((c) => dates.has(c.date));
    const downs = state.payload.crossovers_below_1.filter((c) => dates.has(c.date));
    return { ups, downs };
  }

  function buildChartConfig(winKey, showCrossovers) {
    const series = state.payload.series[winKey];
    const colors = themeColors();
    const th = state.payload.thresholds;
    const cross = crossoverDatesInWindow(winKey);

    const pointRadius = [];
    const pointColors = [];
    const pointStyles = [];
    const upByDate = new Set(cross.ups.map((c) => c.date));
    const downByDate = new Set(cross.downs.map((c) => c.date));
    for (const d of series.dates) {
      if (showCrossovers && upByDate.has(d)) {
        pointRadius.push(7);
        pointColors.push(colors.crossUp);
        pointStyles.push("triangle");
      } else if (showCrossovers && downByDate.has(d)) {
        pointRadius.push(7);
        pointColors.push(colors.crossDown);
        pointStyles.push("triangle");
      } else {
        pointRadius.push(0);
        pointColors.push(colors.primary);
        pointStyles.push("circle");
      }
    }

    const datasets = [
      {
        label: "VIX / VIX3M",
        data: series.ratio,
        borderColor: colors.primary,
        borderWidth: 2.5,
        backgroundColor: "transparent",
        pointRadius: pointRadius,
        pointBackgroundColor: pointColors,
        pointBorderColor: pointColors,
        pointStyle: pointStyles,
        pointHoverRadius: function (ctx) {
          const r = pointRadius[ctx.dataIndex] || 0;
          return r > 0 ? r + 2 : 4;
        },
        stepped: true,
        tension: 0,
      },
    ];

    const annotations = {};

    // Regime bands (full chart width).
    annotations.bandAcute = {
      type: "box",
      yMin: th.acute_panic,
      yMax: Number.MAX_SAFE_INTEGER,
      backgroundColor: colors.panicBand,
      borderWidth: 0,
    };
    annotations.bandBackward = {
      type: "box",
      yMin: th.backwardation,
      yMax: th.acute_panic,
      backgroundColor: colors.backwardBand,
      borderWidth: 0,
    };
    annotations.bandNormal = {
      type: "box",
      yMin: th.complacency,
      yMax: th.backwardation,
      backgroundColor: colors.normalBand,
      borderWidth: 0,
    };
    annotations.bandComplacency = {
      type: "box",
      yMin: Number.MIN_SAFE_INTEGER,
      yMax: th.complacency,
      backgroundColor: colors.complacencyBand,
      borderWidth: 0,
    };

    // Threshold lines.
    function makeLine(y, color, label, hoverKey) {
      return {
        type: "line",
        yMin: y,
        yMax: y,
        borderColor: color,
        borderWidth: 1.4,
        borderDash: [6, 4],
        label: {
          display: true,
          content: label,
          position: "start",
          backgroundColor: color,
          color: "#fff",
          font: { size: 10, weight: "600" },
          padding: { x: 6, y: 2 },
        },
        enter() { state.hoveredAnn = hoverKey; state.chart && state.chart.update("none"); },
        leave() { state.hoveredAnn = null; state.chart && state.chart.update("none"); },
      };
    }
    annotations.panicLine = makeLine(
      th.acute_panic, colors.panicLine, `${th.acute_panic.toFixed(2)} Acute Panic`, "acute_panic",
    );
    annotations.backwardLine = makeLine(
      th.backwardation, colors.backwardLine,
      `${th.backwardation.toFixed(2)} Contango/Backwardation`, "backwardation",
    );
    annotations.complacencyLine = makeLine(
      th.complacency, colors.complacencyLine,
      `${th.complacency.toFixed(2)} Complacency`, "complacency",
    );

    const visibleVals = series.ratio.filter((x) => x !== null && !Number.isNaN(x));
    let yMin = visibleVals.length ? Math.min(...visibleVals) : 0.85;
    let yMax = visibleVals.length ? Math.max(...visibleVals) : 1.15;
    yMin = Math.min(yMin, th.complacency);
    yMax = Math.max(yMax, th.acute_panic);
    const pad = Math.max((yMax - yMin) * 0.05, 0.01);

    return {
      type: "line",
      data: { labels: series.dates, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: colors.tooltipBg,
            titleColor: colors.tooltipFg,
            bodyColor: colors.tooltipFg,
            borderColor: colors.tooltipBorder,
            borderWidth: 1,
            padding: 10,
            callbacks: {
              label: function (ctx) {
                const i = ctx.dataIndex;
                const regime = series.regimes[i];
                const vix = series.vix[i];
                const vix3m = series.vix3m[i];
                const lines = [
                  `Ratio:  ${fmt4(ctx.parsed.y)}`,
                  `Regime: ${regime}`,
                ];
                if (vix !== null && vix !== undefined) lines.push(`VIX:    ${fmt2(vix)}`);
                if (vix3m !== null && vix3m !== undefined) lines.push(`VIX3M:  ${fmt2(vix3m)}`);
                if (upByDate.has(series.dates[i])) lines.push("▲ Crossed above 1.00");
                if (downByDate.has(series.dates[i])) lines.push("▼ Crossed below 1.00");
                return lines;
              },
            },
          },
          annotation: { annotations: annotations },
        },
        scales: {
          x: {
            ticks: {
              color: colors.tick,
              maxRotation: 0,
              autoSkip: true,
              autoSkipPadding: 30,
              font: { size: 10 },
            },
            grid: { color: colors.grid, drawTicks: false },
            border: { color: colors.grid },
          },
          y: {
            suggestedMin: yMin - pad,
            suggestedMax: yMax + pad,
            ticks: { color: colors.tick, font: { size: 10 } },
            grid: { color: colors.grid, drawTicks: false },
            border: { color: colors.grid },
          },
        },
      },
    };
  }

  function renderChart() {
    const cfg = buildChartConfig(state.window, state.showCrossovers);
    if (state.chart) state.chart.destroy();
    state.chart = new Chart(document.getElementById("vix-chart"), cfg);
  }

  // ---- Events --------------------------------------------------------------

  function wireEvents() {
    document.querySelectorAll(".win-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".win-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.window = btn.getAttribute("data-window");
        renderChart();
      });
    });

    document.getElementById("crossovers-toggle").addEventListener("change", (e) => {
      state.showCrossovers = !!e.target.checked;
      renderChart();
    });

    const observer = new MutationObserver(() => renderChart());
    observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });

    window.addEventListener("storage", (e) => {
      if (e.key === "cot-theme") {
        if (e.newValue === "dark") document.body.classList.add("dark");
        else document.body.classList.remove("dark");
      }
    });
  }

  // ---- Bootstrap -----------------------------------------------------------

  function applyStoredTheme() {
    try {
      const saved = localStorage.getItem("cot-theme");
      if (saved === "dark") document.body.classList.add("dark");
    } catch (_) { /* localStorage blocked */ }
  }

  function init(payload) {
    state.payload = payload;
    updateSignalCard();
    renderChart();
    wireEvents();
  }

  function boot() {
    applyStoredTheme();
    fetch(DATA_URL, { cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error("Failed to load " + DATA_URL + ": HTTP " + r.status);
        return r.json();
      })
      .then(init)
      .catch((err) => {
        console.error("vix-chart bootstrap error:", err);
        const wrap = document.querySelector(".chart-wrapper");
        if (wrap) {
          wrap.innerHTML =
            '<p style="color:var(--muted);text-align:center;padding:40px;">' +
            "Failed to load chart data.</p>";
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
