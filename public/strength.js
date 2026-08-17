/* Currency Strength page (/strength) — pure DOM over /data/economic.json.
 *
 * ONE engine, TWO displays: every number here (`pct`, `index`, `bias_label`,
 * `monetary_available`, `previous`, indicator `unit`) is read directly off the
 * SAME payload /economic reads — nothing is recomputed. Position (the arc +
 * pct text) comes from `pct`; color comes from `bias_label` (the same
 * function/thresholds /economic uses) — see src/economic_render.py
 * STRENGTH_PCT_K and _attach_strength_fields. This file must never derive a
 * color band from the percent, and must never recompute pct.
 */
(function () {
  "use strict";

  const DATA_URL = window.ECON_DATA_URL || "/data/economic.json";
  const CCY_ORDER = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"];
  const LOW_N_THRESHOLD = 2; // coverage 1-2 -> confidence warning on the N badge, not hidden

  const state = { payload: null };

  // ---- Formatting -----------------------------------------------------------
  function fmtSigned(v, dp) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    return (n > 0 ? "+" : "") + n.toFixed(dp === undefined ? 2 : dp);
  }
  function fmtScoreCell(v) {
    if (v === null || v === undefined) return "—";
    return (v > 0 ? "+" : "") + v;
  }
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toISOString().slice(0, 10);
  }
  function escAttr(s) { return String(s).replace(/"/g, "&quot;"); }
  // Actual/Forecast/Previous per meta.indicators[key].unit — suffix is a
  // display label ONLY, never a scale factor (see INDICATOR_UNITS docstring).
  function fmtUnit(v, unit, signed) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    const dec = unit && typeof unit.decimals === "number" ? unit.decimals : 1;
    const suffix = (unit && unit.suffix) || "";
    const body = signed ? (n > 0 ? "+" : "") + n.toFixed(dec) : n.toFixed(dec);
    return suffix ? body + " " + suffix : body;
  }

  function cellClass(score) {
    const n = Number(score);
    if (Number.isNaN(n)) return "ec-0";
    if (n >= 3) return "ec-p3";
    if (n === 2) return "ec-p2";
    if (n === 1) return "ec-p1";
    if (n <= -3) return "ec-n3";
    if (n === -2) return "ec-n2";
    if (n === -1) return "ec-n1";
    return "ec-0";
  }
  function biasClass(bias) {
    switch (bias) {
      case "Very Bullish": return "bias-very-bull";
      case "Bullish": return "bias-bull";
      case "Bearish": return "bias-bear";
      case "Very Bearish": return "bias-very-bear";
      default: return "bias-neut";
    }
  }

  // Matrix cell tint — SAME divergent color pair as .ec-p3/.ec-n3 (COT blue
  // #1565c0 / red #d32f2f, identical rgb triplets — see cellClass above and
  // static/economic-chart.js's gradientStyle, which this mirrors), but
  // scaled CONTINUOUSLY against `maxAbs` (the matrix's own largest |diff|)
  // instead of a fixed constant — the matrix has no natural ±N breakpoints
  // the way a per-indicator score does.
  const MATRIX_BLUE = "21,101,192";
  const MATRIX_RED = "211,47,47";
  function matrixCellStyle(diff, maxAbs) {
    if (!maxAbs) return "";
    const t = Math.max(-1, Math.min(1, diff / maxAbs));
    const mag = Math.pow(Math.abs(t), 0.7);
    if (mag < 0.001) return "";
    const rgb = t > 0 ? MATRIX_BLUE : MATRIX_RED;
    const a = mag.toFixed(3);
    const fg = mag >= 0.55 ? "#fff" : "";
    return "background:rgba(" + rgb + "," + a + ")" + (fg ? ";color:" + fg : "");
  }

  function indMeta(key) {
    return (state.payload.meta && state.payload.meta.indicators && state.payload.meta.indicators[key]) || {};
  }
  function catLabel(key) {
    const c = state.payload.meta && state.payload.meta.categories && state.payload.meta.categories[key];
    return (c && c.label) || key;
  }
  function tableLayout() {
    return (state.payload.meta && state.payload.meta.table_layout) || [];
  }

  // ---- Pure data helpers (no DOM — keep testable in isolation) --------------
  function hasCoverage(card) { return !!card && Number(card.coverage) > 0; }

  function sortedCurrencies(currencies) {
    const entries = CCY_ORDER
      .filter(ccy => currencies[ccy])
      .map(ccy => [ccy, currencies[ccy]]);
    const withData = entries.filter(([, c]) => hasCoverage(c));
    const noData = entries.filter(([, c]) => !hasCoverage(c));
    withData.sort((a, b) => b[1].pct - a[1].pct);
    return withData.map(([ccy]) => ccy).concat(noData.map(([ccy]) => ccy));
  }

  // Matrix row/column order: same descending-by-pct sort as the cadrane, but
  // coverage==0 currencies are DROPPED entirely (no row, no column) rather
  // than pushed to a tail — same exclusion rule as divergence().
  function matrixOrder(currencies) {
    return sortedCurrencies(currencies).filter(ccy => hasCoverage(currencies[ccy]));
  }

  // Single source of truth for K: read off the payload (see
  // economic_render.py STRENGTH_PCT_K / meta["strength_pct_k"]) — never
  // hardcoded here, so this file can't drift from the Python constant.
  // The literal fallback only fires if an old cached payload predates the
  // meta key; it mirrors STRENGTH_PCT_K's last-measured value.
  function strengthK() {
    const k = state.payload.meta && state.payload.meta.strength_pct_k;
    return typeof k === "number" ? k : 10.5;
  }

  // The UNCLAMPED pct-space differential between two cards: (a.index -
  // b.index) * K. NOT a.pct - b.pct — pct is clamp(50 + index*K, 0, 100),
  // so a plain pct differential silently truncates whenever either side is
  // clamped (the "clamp leak" this function exists to avoid). A differential
  // is not itself bounded to [0,100] — two saturated opposite extremes can
  // legitimately read over 100pp apart.
  function unclampedDiffPct(a, b) {
    return (a.index - b.index) * strengthK();
  }

  // {spread, strongest, weakest} over coverage>0 currencies only, or null if
  // fewer than 2 qualify (a "spread" needs at least two points to compare).
  // `strongest`/`weakest` are picked by pct (matches the cadrane's own
  // sort — the visible "top card"/"bottom card"); `spread` is the
  // UNCLAMPED index differential (see unclampedDiffPct), not pct_top -
  // pct_bottom, so it stays correct even when one or both ends are capped.
  function divergence(currencies) {
    const withData = Object.keys(currencies)
      .map(ccy => [ccy, currencies[ccy]])
      .filter(([, c]) => hasCoverage(c));
    if (withData.length < 2) return null;
    withData.sort((a, b) => b[1].pct - a[1].pct);
    const top = withData[0], bottom = withData[withData.length - 1];
    return {
      spread: unclampedDiffPct(top[1], bottom[1]),
      strongest: top[0], weakest: bottom[0],
    };
  }

  // Impact state for one breakdown entry — score==0 has THREE distinct causes
  // that must never render the same (see module docstring / PASUL 2 spec):
  // a real dead-zone ("neutral"), no_consensus ("no_forecast", excluded from
  // N), or stale (excluded from N). Non-zero scores are directional as-is
  // (direction is already baked into `score` by compute_indicator_score).
  function impactState(e) {
    if (!e) return "neutral";
    if (e.flag === "no_consensus") return "no_forecast";
    if (e.stale) return "stale";
    if (!e.score) return "neutral";
    return e.score > 0 ? "bullish" : "bearish";
  }

  // ---- Gauge arc --------------------------------------------------------
  // Semicircular arc, normalized to pathLength=100 so dasharray/dashoffset are
  // plain percentages regardless of the SVG's actual radius. Color = bias
  // (never derived from pct — see .cadran-arc-fill.bias-* in style.css,
  // 1:1 copies of the .bias-* hex values, kept in sync manually).
  function arcSvg(pct, biasCls, noData) {
    const fillPct = noData ? 0 : Math.max(0, Math.min(100, pct));
    const offset = (100 - fillPct).toFixed(2);
    const fillCls = "cadran-arc-fill" + (noData ? " cadran-arc-fill-empty" : " " + biasCls);
    return (
      '<svg viewBox="0 0 120 66" aria-hidden="true">' +
      '<path class="cadran-arc-track" pathLength="100" d="M 10 60 A 50 50 0 0 1 110 60"/>' +
      '<path class="' + fillCls + '" pathLength="100" stroke-dasharray="100" ' +
      'stroke-dashoffset="' + offset + '" d="M 10 60 A 50 50 0 0 1 110 60"/>' +
      '</svg>'
    );
  }

  // ---- Main view: 8 cadrane + divergence + sorted table ----------------------
  function renderDivergence() {
    const el = document.getElementById("strengthDivergence");
    const d = divergence(state.payload.currencies || {});
    if (!d) {
      el.innerHTML = '<span class="muted">Not enough currencies with coverage to compute a divergence.</span>';
      return;
    }
    el.innerHTML = "Divergence: <b>" + d.spread.toFixed(1) + "pp</b> &middot; " +
      "<b>" + d.strongest + "</b> vs <b>" + d.weakest + "</b>";
  }

  function cadranHtml(ccy, card) {
    const noData = !hasCoverage(card);
    const bias = card.bias_label || "Neutral";
    const bCls = biasClass(bias);
    const n = card.coverage || 0;

    let badges = "";
    if (card.monetary_available === false) {
      badges += '<span class="cadran-badge badge-no-rate" title="No rate-expectations data for this currency — index is growth/inflation/labour only">no rate</span>';
    }
    if (card.pct_clamped) {
      badges += '<span class="cadran-badge badge-clamped" title="Saturated at the display range — the real magnitude is in the index below">capped</span>';
    }

    const body = noData
      ? '<div class="cadran-nodata-label">no data</div>'
      : (
        '<div class="cadran-arc-value">' +
        '<div class="cadran-pct">' + card.pct.toFixed(1) + '<span class="unit">%</span></div>' +
        '</div>'
      );

    const indexLine = noData ? "" : '<div class="cadran-index">index ' + fmtSigned(card.index, 2) + '</div>';
    const nCls = (!noData && n > 0 && n <= LOW_N_THRESHOLD) ? " cadran-n-low" : "";
    const nTitle = (!noData && n > 0 && n <= LOW_N_THRESHOLD) ? ' title="Low coverage — read with caution"' : "";

    return (
      '<div class="cadran' + (noData ? " cadran-nodata" : "") + '" data-ccy="' + escAttr(ccy) + '">' +
      '<div class="cadran-ccy">' + ccy + '</div>' +
      '<div class="cadran-arc-wrap">' + arcSvg(noData ? 0 : card.pct, bCls, noData) + body + '</div>' +
      indexLine +
      '<div class="cadran-n' + nCls + '"' + nTitle + '>N=' + n + '</div>' +
      '<div class="cadran-badges">' + badges + '</div>' +
      '</div>'
    );
  }

  function renderGrid() {
    const grid = document.getElementById("strengthGrid");
    const currencies = state.payload.currencies || {};
    const order = sortedCurrencies(currencies);
    grid.innerHTML = order.map(ccy => cadranHtml(ccy, currencies[ccy])).join("");
    grid.querySelectorAll(".cadran[data-ccy]").forEach(el => {
      el.addEventListener("click", () => goToDrilldown(el.dataset.ccy));
    });
  }

  function tableRowHtml(ccy, card) {
    const noData = !hasCoverage(card);
    const bias = card.bias_label || "Neutral";
    const monetary = card.monetary_available ? "Yes" : "No";
    return (
      '<tr' + (noData ? ' class="strength-row-nodata"' : "") + ' data-ccy="' + escAttr(ccy) + '">' +
      '<td class="strength-ccy">' + ccy + '</td>' +
      '<td class="strength-num">' + (noData ? "—" : card.pct.toFixed(1) + "%") + '</td>' +
      '<td class="strength-num">' + (noData ? "—" : fmtSigned(card.index, 2)) + '</td>' +
      '<td>' + (noData ? "—" : bias) + '</td>' +
      '<td class="strength-num">' + (card.coverage || 0) + '</td>' +
      '<td>' + (noData ? "—" : monetary) + '</td>' +
      '</tr>'
    );
  }

  function renderTable() {
    const body = document.getElementById("strengthTableBody");
    const currencies = state.payload.currencies || {};
    const order = sortedCurrencies(currencies);
    body.innerHTML = order.map(ccy => tableRowHtml(ccy, currencies[ccy])).join("");
    body.querySelectorAll("tr[data-ccy]").forEach(tr => {
      tr.addEventListener("click", () => goToDrilldown(tr.dataset.ccy));
    });
  }

  // One card's tooltip fragment: "EUR 100% (clamped, index +6.56)" when
  // pct_clamped, else the plain "EUR 54.4%" — so the hover always makes the
  // unclamped diff traceable back to the two real index values behind it.
  function matrixTipFragment(ccy, card) {
    if (card.pct_clamped) {
      return ccy + " " + card.pct.toFixed(0) + "% (clamped, index " + fmtSigned(card.index, 2) + ")";
    }
    return ccy + " " + card.pct.toFixed(1) + "%";
  }

  // 8x8 (or fewer, once coverage==0 currencies are dropped) divergence
  // matrix — pure recombination of `index` already in the payload (× the
  // same K /strength uses everywhere else). No new math beyond that
  // multiplication, no new data, no scoring call. Cell = (row.index -
  // column.index) * K, NOT row.pct - column.pct — pct is clamped to
  // [0,100] for cadran display, so a pct-only differential silently
  // truncates once either side saturates (see unclampedDiffPct). A
  // differential is not itself bounded to [0,100].
  function renderMatrix() {
    const table = document.getElementById("strengthMatrix");
    const currencies = state.payload.currencies || {};
    const order = matrixOrder(currencies);

    if (order.length < 2) {
      table.innerHTML = "";
      table.parentElement.insertAdjacentHTML(
        "beforeend",
        '<p class="muted">Not enough currencies with coverage for a matrix.</p>'
      );
      return;
    }

    let maxAbs = 0;
    order.forEach(base => order.forEach(quote => {
      if (base === quote) return;
      const d = Math.abs(unclampedDiffPct(currencies[base], currencies[quote]));
      if (d > maxAbs) maxAbs = d;
    }));

    const headerRow = '<tr><th class="matrix-corner"></th>' +
      order.map(ccy => '<th>' + ccy + '</th>').join("") + '</tr>';

    const bodyRows = order.map(base => {
      const cells = order.map(quote => {
        if (base === quote) return '<td class="matrix-diag"></td>';
        const diff = unclampedDiffPct(currencies[base], currencies[quote]);
        const style = matrixCellStyle(diff, maxAbs);
        const tip = matrixTipFragment(base, currencies[base]) + " − " +
          matrixTipFragment(quote, currencies[quote]) + " = " + fmtSigned(diff, 1) + "pp";
        return '<td class="matrix-cell"' + (style ? ' style="' + style + '"' : "") +
          ' title="' + escAttr(tip) + '">' + fmtSigned(diff, 1) + '</td>';
      }).join("");
      return '<tr><th class="matrix-row-label">' + base + '</th>' + cells + '</tr>';
    }).join("");

    table.innerHTML = '<thead>' + headerRow + '</thead><tbody>' + bodyRows + '</tbody>';
  }

  function renderMain() {
    renderDivergence();
    renderGrid();
    renderTable();
    renderMatrix();
  }

  // ---- Drilldown --------------------------------------------------------
  function flagBadge(state_) {
    if (state_ === "no_forecast") return '<span class="econ-flag flag-nc" title="No consensus available — scored 0, excluded from N">No forecast</span>';
    if (state_ === "stale") return '<span class="econ-flag flag-stale" title="Latest release is older than max_age_days — excluded from N">Stale</span>';
    return "";
  }

  function indicatorRowHtml(key, e) {
    const meta = indMeta(key);
    const unit = meta.unit || { suffix: "", decimals: 1 };
    // rate_expectations has no calendar actual/consensus — latest_yield is its
    // closest analog (see INDICATOR_UNITS["rate_expectations"] in economic_render.py).
    const isRate = key === "rate_expectations";
    const actualRaw = isRate ? e.latest_yield : e.actual;
    const forecastRaw = isRate ? null : e.consensus;
    const surpriseRaw = isRate ? null : e.surprise;
    const dateVal = e.release_dt || e.as_of;
    const impact = impactState(e);
    const rowCls = impact === "stale" ? ' class="ei-stale"' : (impact === "no_forecast" ? ' class="ei-no-consensus"' : "");

    let impactHtml;
    if (impact === "no_forecast" || impact === "stale") {
      impactHtml = flagBadge(impact);
    } else if (impact === "neutral") {
      impactHtml = '<span class="' + cellClass(0) + '">Neutral</span>';
    } else {
      impactHtml = '<span class="' + cellClass(e.score) + '">' + fmtScoreCell(e.score) + '</span>';
    }

    return (
      '<tr' + rowCls + '>' +
      '<td class="ei-name">' + (meta.label || key) + '</td>' +
      '<td class="ei-date">' + fmtDate(dateVal) + '</td>' +
      '<td class="ei-num">' + fmtUnit(actualRaw, unit) + '</td>' +
      '<td class="ei-num">' + fmtUnit(forecastRaw, unit) + '</td>' +
      '<td class="ei-num">' + fmtUnit(surpriseRaw, unit, true) + '</td>' +
      '<td class="ei-num">' + fmtUnit(e.previous, unit) + '</td>' +
      '<td class="ei-score ei-impact">' + impactHtml + '</td>' +
      '</tr>'
    );
  }

  // The 7 columns, fixed widths — a single <colgroup> for the single
  // drilldown table (see .strength-drilldown table.econ-ind-table in
  // style.css for the widths). One table, one <thead> → alignment across
  // categories is now a structural guarantee (same table, same columns),
  // not something colgroups have to keep in sync across five separate
  // <table> elements the way the previous pass did it.
  const DRILLDOWN_COLGROUP =
    '<colgroup><col class="col-indicator"><col class="col-date">' +
    '<col class="col-actual"><col class="col-forecast"><col class="col-surprise">' +
    '<col class="col-previous"><col class="col-impact"></colgroup>';

  // One <tr class="strength-cat-header"> spanning all 7 columns per
  // category, in place of the previous pass's separate <div class="econ-
  // cat-head">+<table> per category. Keeps the same content (label,
  // score_cell, N) — see style.css for the row's look.
  function categoryHeaderRowHtml(catKey, card) {
    const sub = (card.categories || {})[catKey];
    let subHtml;
    if (sub) {
      subHtml = '<span class="econ-cat-sub ' + cellClass(sub.score_cell) + '">' +
        fmtScoreCell(sub.score_cell) + '</span>' +
        '<span class="muted"> &middot; N' + (sub.coverage || 0) + '</span>';
    } else {
      subHtml = '<span class="econ-flag flag-fb" title="Category not scored — shown for visibility, never counted toward N">nescorat</span>';
    }
    return '<tr class="strength-cat-header"><td colspan="7">' + catLabel(catKey).toUpperCase() +
      ' ' + subHtml + '</td></tr>';
  }

  // Category groups in TABLE_LAYOUT order (growth/inflation/labour/monetary —
  // meta.table_layout), then any OTHER category actually present in this
  // currency's breakdown (rates, inflation_display, growth_display…),
  // appended in first-seen order — identical algorithm to /economic's
  // legHtml. A group whose key is a REAL card.categories entry shows its
  // score_cell + N; anything else is display-only ("nescorat") and never
  // promoted into a real category's N (mirrors the economic_compute.py
  // regression test that guards this at the scoring layer).
  function drilldownGroupsHtml(card, ccy) {
    const breakdown = card.breakdown || {};
    const cats = tableLayout().map(g => g.category);
    Object.keys(breakdown).forEach(k => {
      const c = indMeta(k).category;
      if (c && cats.indexOf(c) === -1) cats.push(c);
    });

    let rows = "";
    cats.forEach(catKey => {
      const keys = Object.keys(breakdown).filter(k => indMeta(k).category === catKey);
      if (!keys.length) return;
      keys.sort((a, b) => (indMeta(a).label || a).localeCompare(indMeta(b).label || b));

      rows += categoryHeaderRowHtml(catKey, card);
      rows += keys.map(k => indicatorRowHtml(k, breakdown[k])).join("");
    });

    if (!rows) return '<p class="muted">No indicators within the lookback window.</p>';

    return (
      '<div class="econ-ind-scroll strength-dd-scroll"><table class="econ-ind-table">' +
      DRILLDOWN_COLGROUP +
      '<thead><tr><th>Indicator</th><th>Date</th><th>Actual</th><th>Forecast</th>' +
      '<th>Surprise</th><th>Previous</th><th class="impact-head">Impact ' + ccy + '</th></tr></thead>' +
      '<tbody>' + rows + '</tbody>' +
      '</table></div>'
    );
  }

  function renderDrilldown(ccy) {
    const content = document.getElementById("strengthDrilldownContent");
    const card = (state.payload.currencies || {})[ccy];
    if (!card) {
      content.innerHTML = '<p class="muted">No data for ' + escAttr(ccy) + '.</p>';
      return;
    }
    const bias = card.bias_label || "Neutral";
    const noteBits = [];
    if (card.monetary_available === false) noteBits.push("no rate-expectations data");
    if (card.pct_clamped) noteBits.push("saturated at the display range");
    const note = noteBits.length ? ' <span class="strength-dd-meta">(' + noteBits.join(" · ") + ')</span>' : "";

    content.innerHTML =
      '<div class="strength-dd-head">' +
      '<h2>' + ccy + '</h2>' +
      (hasCoverage(card)
        ? '<span class="strength-dd-meta">' + card.pct.toFixed(1) + '% &middot; index ' + fmtSigned(card.index, 2) +
          ' &middot; N=' + (card.coverage || 0) + '</span>' +
          '<span class="bias-pill ' + biasClass(bias) + '">' + bias + '</span>' + note
        : '<span class="strength-dd-meta">no data</span>') +
      '</div>' +
      drilldownGroupsHtml(card, ccy);
  }

  // ---- Routing (query-string, deep-linkable, back-button friendly) ----------
  function ccyFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const ccy = params.get("ccy");
    return ccy ? ccy.toUpperCase() : null;
  }

  function render() {
    const ccy = ccyFromUrl();
    const main = document.getElementById("strengthMain");
    const drill = document.getElementById("strengthDrilldown");
    if (ccy && state.payload.currencies && state.payload.currencies[ccy]) {
      main.hidden = true;
      drill.hidden = false;
      renderDrilldown(ccy);
    } else {
      drill.hidden = true;
      main.hidden = false;
      renderMain();
    }
  }

  function goToDrilldown(ccy) {
    const url = new URL(window.location.href);
    url.searchParams.set("ccy", ccy);
    window.history.pushState({ ccy: ccy }, "", url);
    render();
    window.scrollTo(0, 0);
  }

  // Help modal wiring lives in the shared static/help-scoring.js (one
  // source for both /economic and /strength — see
  // templates/_help_scoring_modal.html.j2), not here.
  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("strengthBack").addEventListener("click", () => {
      const url = new URL(window.location.href);
      url.searchParams.delete("ccy");
      window.history.pushState({}, "", url);
      render();
    });
    window.addEventListener("popstate", render);

    fetch(DATA_URL)
      .then(r => r.json())
      .then(data => {
        state.payload = data;
        render();
      })
      .catch(err => {
        document.getElementById("strengthGrid").innerHTML =
          '<p class="muted" style="grid-column:1/-1;">Failed to load data: ' + escAttr(String(err)) + '</p>';
      });
  });

  // Exposed ONLY for local verification (node-run, no DOM) of the pure
  // helpers — never referenced from the render path above.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      sortedCurrencies, divergence, impactState, hasCoverage,
      drilldownGroupsHtml, indicatorRowHtml, cadranHtml, tableRowHtml,
      matrixOrder, matrixCellStyle, unclampedDiffPct, strengthK,
      _setPayloadForTest: (p) => { state.payload = p; },
    };
  }
})();
