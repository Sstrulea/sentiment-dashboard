/* P/C Ratio page chart logic. Depends on Chart.js v4 UMD + chartjs-plugin-annotation. */
(function () {
  "use strict";

  const DATA_URL = window.PC_DATA_URL || "/data/pc-ratio.json";

  const SIGNAL_LABELS = {
    neutral: { text: "Neutral", cls: "neutral" },
    bearish_extreme: { text: "Bearish extreme (contrarian bullish)", cls: "warning-red" },
    bullish_extreme: { text: "Bullish extreme (contrarian bearish)", cls: "info-green" },
    bullish: { text: "Bullish", cls: "info-green" },
    bearish: { text: "Bearish", cls: "warning-red" },
  };

  const CONFIDENCE_CLASS = {
    HIGH: "high",
    MEDIUM: "medium",
    "MEDIUM-LOW": "medium-low",
    LOW: "low",
  };

  const state = {
    payload: null,
    variant: "total",
    window: "3M",
    showMa10: false,
    chart: null,
  };

  // ---- Theme helpers -----------------------------------------------------

  function isDarkTheme() {
    return document.body.classList.contains("dark");
  }

  function themeColors() {
    const dark = isDarkTheme();
    return {
      primary: dark ? "#ffffff" : "#1a1a1a",
      ma10: dark ? "#64b5f6" : "#1565c0",
      grid: dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
      tick: dark ? "#9aa0a6" : "#666",
      p90: dark ? "#ff6b6b" : "#c62828",
      p10: dark ? "#6bff6b" : "#2e7d32",
      label: dark ? "#eee" : "#222",
      tooltipBg: dark ? "rgba(10,10,10,0.95)" : "rgba(255,255,255,0.95)",
      tooltipFg: dark ? "#eee" : "#222",
      tooltipBorder: dark ? "#333" : "#ccc",
    };
  }

  // ---- Number formatting --------------------------------------------------

  function fmt2(x) {
    return x === null || x === undefined || Number.isNaN(x) ? "—" : Number(x).toFixed(2);
  }
  function fmt1(x) {
    return x === null || x === undefined || Number.isNaN(x) ? "—" : Number(x).toFixed(1);
  }

  // ---- Signal card --------------------------------------------------------

  function updateSignalCard(variant) {
    const v = state.payload.variants[variant];
    const cur = v.current;

    document.getElementById("signal-value").textContent = fmt2(cur.value);
    document.getElementById("signal-date").textContent = cur.date || "—";

    const statusEl = document.getElementById("signal-status");
    const label = SIGNAL_LABELS[cur.signal] || SIGNAL_LABELS.neutral;
    statusEl.textContent = label.text;
    statusEl.className = "status " + label.cls;

    const pct = cur.percentile_rank;
    document.getElementById("signal-percentile").textContent =
      pct === null || pct === undefined ? "—" : fmt1(pct) + "%";
    document.getElementById("signal-ma10").textContent = fmt2(cur.ma_10);
    document.getElementById("signal-source").textContent =
      cur.thresholds_source === "rolling_1y" ? "Rolling 1Y" : "Static default";

    const banner = document.getElementById("vix-info-banner");
    if (variant === "vix" && v.note) {
      document.getElementById("vix-info-text").textContent = v.note;
      banner.hidden = false;
    } else {
      banner.hidden = true;
    }

    const confEl = document.getElementById("confidence-indicator");
    const confCls = CONFIDENCE_CLASS[v.confidence] || "medium";
    confEl.className = "confidence-indicator " + confCls;
    confEl.textContent = "Confidence: " + v.confidence;

    document.getElementById("source-citation").textContent = "Source: " + v.source;
  }

  // ---- Chart rendering ----------------------------------------------------

  function buildChartConfig(variant, windowKey, showMa10) {
    const v = state.payload.variants[variant];
    const series = v.series[windowKey];
    const colors = themeColors();

    const p90 = v.current.p90_rolling;
    const p10 = v.current.p10_rolling;
    const staticPut = v.static_thresholds.high_put;
    const staticCall = v.static_thresholds.high_call;

    const datasets = [
      {
        label: v.display_name,
        data: series.values,
        borderColor: colors.primary,
        borderWidth: 2,
        backgroundColor: "transparent",
        pointRadius: 0,
        pointHoverRadius: 4,
        stepped: true,
        tension: 0,
      },
      {
        label: "MA 10",
        data: series.ma_10,
        borderColor: colors.ma10,
        borderWidth: 1,
        backgroundColor: "transparent",
        pointRadius: 0,
        pointHoverRadius: 3,
        stepped: false,
        tension: 0.25,
        hidden: !showMa10,
        borderDash: [4, 3],
      },
    ];

    const annotations = {};
    if (p90 !== null && p90 !== undefined) {
      annotations.p90 = {
        type: "line",
        yMin: p90,
        yMax: p90,
        borderColor: colors.p90,
        borderWidth: 1.5,
        borderDash: [6, 4],
        label: {
          display: true,
          content: "High Put Volume",
          position: "start",
          backgroundColor: colors.p90,
          color: "#fff",
          font: { size: 10, weight: "600" },
          padding: { x: 6, y: 2 },
          yAdjust: -10,
        },
        enter() { state.hoveredAnn = "p90"; state.chart && state.chart.update("none"); },
        leave() { state.hoveredAnn = null; state.chart && state.chart.update("none"); },
      };
    }
    if (p10 !== null && p10 !== undefined) {
      annotations.p10 = {
        type: "line",
        yMin: p10,
        yMax: p10,
        borderColor: colors.p10,
        borderWidth: 1.5,
        borderDash: [6, 4],
        label: {
          display: true,
          content: "High Call Volume",
          position: "start",
          backgroundColor: colors.p10,
          color: "#fff",
          font: { size: 10, weight: "600" },
          padding: { x: 6, y: 2 },
          yAdjust: 10,
        },
        enter() { state.hoveredAnn = "p10"; state.chart && state.chart.update("none"); },
        leave() { state.hoveredAnn = null; state.chart && state.chart.update("none"); },
      };
    }

    const allVals = series.values.filter((x) => x !== null && !Number.isNaN(x));
    let yMin = allVals.length ? Math.min(...allVals) : 0;
    let yMax = allVals.length ? Math.max(...allVals) : 1;
    if (p90 !== null && p90 !== undefined) yMax = Math.max(yMax, p90);
    if (p10 !== null && p10 !== undefined) yMin = Math.min(yMin, p10);
    const pad = (yMax - yMin) * 0.05 || 0.05;

    return {
      type: "line",
      data: { labels: series.dates, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            display: showMa10,
            position: "top",
            align: "end",
            labels: { color: colors.label, boxWidth: 18, boxHeight: 2, font: { size: 11 } },
          },
          tooltip: {
            backgroundColor: colors.tooltipBg,
            titleColor: colors.tooltipFg,
            bodyColor: colors.tooltipFg,
            borderColor: colors.tooltipBorder,
            borderWidth: 1,
            padding: 10,
            callbacks: {
              afterBody: function () {
                if (state.hoveredAnn === "p90") {
                  return [
                    `Dynamic P90 threshold: ${fmt2(p90)}`,
                    `Static reference:     ${fmt2(staticPut)}`,
                  ];
                }
                if (state.hoveredAnn === "p10") {
                  return [
                    `Dynamic P10 threshold: ${fmt2(p10)}`,
                    `Static reference:     ${fmt2(staticCall)}`,
                  ];
                }
                return [];
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
    const cfg = buildChartConfig(state.variant, state.window, state.showMa10);
    if (state.chart) {
      state.chart.destroy();
    }
    const canvas = document.getElementById("pc-chart");
    state.chart = new Chart(canvas, cfg);
  }

  // ---- Event wiring -------------------------------------------------------

  function wireEvents() {
    document.getElementById("variant-select").addEventListener("change", (e) => {
      state.variant = e.target.value;
      updateSignalCard(state.variant);
      renderChart();
    });

    document.querySelectorAll(".win-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".win-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.window = btn.getAttribute("data-window");
        renderChart();
      });
    });

    document.getElementById("ma10-toggle").addEventListener("change", (e) => {
      state.showMa10 = !!e.target.checked;
      if (state.chart) {
        state.chart.setDatasetVisibility(1, state.showMa10);
        state.chart.options.plugins.legend.display = state.showMa10;
        state.chart.update();
      }
    });

    // Theme re-render: observe body class changes (existing COT toggle writes body.dark).
    const observer = new MutationObserver(() => renderChart());
    observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });

    // Also react to the COT page's localStorage key in case theme was changed in another tab.
    window.addEventListener("storage", (e) => {
      if (e.key === "cot-theme") {
        if (e.newValue === "dark") document.body.classList.add("dark");
        else document.body.classList.remove("dark");
      }
    });
  }

  // ---- Bootstrap ----------------------------------------------------------

  function applyStoredTheme() {
    try {
      const saved = localStorage.getItem("cot-theme");
      if (saved === "dark") document.body.classList.add("dark");
    } catch (_) { /* localStorage blocked */ }
  }

  function init(payload) {
    state.payload = payload;
    state.variant = "total";
    state.window = "3M";
    state.showMa10 = false;

    document.getElementById("variant-select").value = state.variant;
    document.getElementById("ma10-toggle").checked = false;

    updateSignalCard(state.variant);
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
        console.error("pc-chart bootstrap error:", err);
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
