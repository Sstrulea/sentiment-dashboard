/* Economic Dashboard page logic. Pure DOM over /data/economic.json — no charts.
 * Dense EdgeFinder-style table: Symbol | Bias | Score | one column per indicator
 * (grouped under Growth / Inflation / Labour headers). Each indicator cell is the
 * differential score (base − quote) for a pair, or the currency score for a
 * single; USD-only indicators show "—" on non-USD instruments. Row click opens a
 * modal with the full per-indicator detail (actual / consensus / surprise / z /
 * score / method) and category subtotals — that's where the rounded-cell
 * divergence is explained.
 */
(function () {
  "use strict";

  const DATA_URL = window.ECON_DATA_URL || "/data/economic.json";

  const state = {
    payload: null,
    sortKey: null,
    sortAsc: false,
    activeSymbol: null,
    ccySel: new Set(),   // empty = no currency filter (show all)
    biasSel: new Set(),  // empty = no bias filter (show all)
  };

  const CCY_ORDER = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"];
  const BIAS_GROUPS = ["Bullish", "Neutral", "Bearish"];

  // ---- Formatting ---------------------------------------------------------
  function fmtNum(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const n = Number(v);
    if (Math.abs(n - Math.round(n)) < 1e-9) return String(Math.round(n));
    return n.toFixed(2);
  }
  function fmtSigned(v, dp) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const n = Number(v);
    const s = n.toFixed(dp === undefined ? 2 : dp);
    return (n > 0 ? "+" : "") + s;
  }
  function fmtScoreCell(v) {
    if (v === null || v === undefined) return "—";
    return (v > 0 ? "+" : "") + v;
  }
  function fmtScoreInt(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const n = Math.round(Number(v));
    return (n > 0 ? "+" : "") + n;
  }
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toISOString().slice(0, 10);
  }
  function escAttr(s) { return String(s).replace(/"/g, "&quot;"); }

  // ---- Color classes (COT divergent palette: blue = bullish/buy, red = bearish) ----
  // Divergent scale by magnitude; differentials run to ±4 so we have ±3 tiers.
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

  // Continuous divergent gradient using the COT endpoints (intense blue ↔ intense
  // red), as a tint over the cell so it adapts to light/dark themes. `score` is
  // signed; `scale` is the magnitude that saturates to full intensity. Returns
  // an inline-style string ("" for neutral → no tint).
  const COT_BLUE = "21,101,192";   // #1565c0  (bullish / positive)
  const COT_RED = "211,47,47";     // #d32f2f  (bearish / negative)
  function gradientStyle(score, scale) {
    if (score === null || score === undefined || Number.isNaN(Number(score))) return "";
    const t = Math.max(-1, Math.min(1, Number(score) / scale));
    const mag = Math.pow(Math.abs(t), 0.7);          // ease so low values still read
    if (mag < 0.001) return "";                       // neutral → transparent
    const rgb = t > 0 ? COT_BLUE : COT_RED;
    const a = mag.toFixed(3);
    const fg = mag >= 0.55 ? "#fff" : "";
    return "background:rgba(" + rgb + "," + a + ")" + (fg ? ";color:" + fg : "");
  }
  function styleAttr(s) { return s ? ' style="' + s + '"' : ""; }

  // ---- Meta-bar -----------------------------------------------------------
  function renderMeta() {
    const el = document.getElementById("econMeta");
    const p = state.payload;
    const parts = [];
    parts.push("As of " + fmtAsOf(p.as_of));
    parts.push(p.instruments.length + " instruments");
    if (p.generated_at) parts.push("Generated " + fmtAsOf(p.generated_at));
    el.innerHTML = parts.join(" · ") + freshnessBadges(p.freshness);
  }

  // Per-source freshness badges (calendar / price). STALE → red badge so a silent
  // data freeze is visible instantly without comparing to external sources.
  function freshnessBadges(f) {
    if (!f) return "";
    let out = "";
    [["calendar", "Calendar"], ["price", "Price"]].forEach(function (pair) {
      const v = f[pair[0]];
      if (!v) return;
      const age = v.age_days <= 0 ? "today" : v.age_days + "d ago";
      const cls = v.stale ? "fresh-badge stale" : "fresh-badge ok";
      const txt = (v.stale ? "⚠ STALE " : "") + pair[1] + " " + age;
      out += ' <span class="' + cls + '" title="last update ' + escAttr(v.last_update || "") +
        '">' + txt + "</span>";
    });
    return out;
  }
  function fmtAsOf(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
  }

  // ---- Filters ------------------------------------------------------------
  function biasGroup(bias) {
    const b = bias || "";
    if (b.indexOf("Bull") >= 0) return "Bullish";
    if (b.indexOf("Bear") >= 0) return "Bearish";
    return "Neutral";
  }
  function instrumentCurrencies(inst) {
    const out = [];
    const b = inst.breakdown && inst.breakdown.base;
    const q = inst.breakdown && inst.breakdown.quote;
    if (b && b.currency) out.push(b.currency);
    if (q && q.currency) out.push(q.currency);
    return out;
  }
  function passesFilters(inst) {
    if (state.ccySel.size && !instrumentCurrencies(inst).some(c => state.ccySel.has(c))) return false;
    if (state.biasSel.size && !state.biasSel.has(biasGroup(inst.bias))) return false;
    return true;
  }

  const CHIP_SVG =
    '<svg class="chip-box" viewBox="0 0 16 16" aria-hidden="true">' +
    '<rect class="chip-box-rect" x="2" y="2" width="12" height="12" rx="2.5" ry="2.5" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
    '<path class="chip-box-check" d="M4.5 8.4l2.4 2.4L11.8 5.6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
    '</svg>';

  function buildChipGroup(containerId, items, selSet) {
    const wrap = document.getElementById(containerId);
    if (!wrap) return;
    wrap.innerHTML = "";
    items.forEach(key => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cat-chip" + (selSet.has(key) ? " selected" : "");
      btn.dataset.key = key;
      btn.setAttribute("aria-pressed", selSet.has(key) ? "true" : "false");
      btn.innerHTML = CHIP_SVG + "<span></span>";
      btn.querySelector("span").textContent = key;
      btn.addEventListener("click", () => {
        if (selSet.has(key)) selSet.delete(key);
        else selSet.add(key);
        btn.classList.toggle("selected");
        btn.setAttribute("aria-pressed", selSet.has(key) ? "true" : "false");
        updateAllButtons();
        renderTable();
      });
      wrap.appendChild(btn);
    });
  }

  function wireAllButton(filterId, containerId, selSet) {
    document.querySelectorAll("#" + filterId + ' .cat-action[data-action="all"]').forEach(b => {
      b.addEventListener("click", () => {
        selSet.clear();
        document.querySelectorAll("#" + containerId + " .cat-chip").forEach(ch => {
          ch.classList.remove("selected");
          ch.setAttribute("aria-pressed", "false");
        });
        updateAllButtons();
        renderTable();
      });
    });
  }

  function updateAllButtons() {
    const map = [["econCcyFilter", state.ccySel], ["econBiasFilter", state.biasSel]];
    map.forEach(([fid, set]) => {
      document.querySelectorAll("#" + fid + ' .cat-action[data-action="all"]').forEach(b => {
        b.classList.toggle("active", set.size === 0);
      });
    });
  }

  function renderFilters() {
    const present = CCY_ORDER.filter(c => (state.payload.currencies || {})[c]);
    buildChipGroup("econCcyChips", present, state.ccySel);
    buildChipGroup("econBiasChips", BIAS_GROUPS, state.biasSel);
    wireAllButton("econCcyFilter", "econCcyChips", state.ccySel);
    wireAllButton("econBiasFilter", "econBiasChips", state.biasSel);
    updateAllButtons();
  }

  // ---- Layout helpers -----------------------------------------------------
  function tableLayout() {
    return (state.payload.meta && state.payload.meta.table_layout) || [];
  }
  function columnKeys() {
    const out = [];
    tableLayout().forEach(g => g.columns.forEach(c => out.push(c.key)));
    return out;
  }
  function catLabel(key) {
    const m = state.payload.meta && state.payload.meta.categories;
    return (m && m[key] && m[key].label) || key;
  }
  function indMeta(key) {
    return (state.payload.meta.indicators || {})[key] || {};
  }

  // ---- Sorting ------------------------------------------------------------
  function rowSortValue(inst, key) {
    if (key === "symbol") return inst.display || inst.symbol;
    if (key === "bias" || key === "score") return inst.score;          // precise float
    if (key === "trend") {
      return (inst.trend !== null && inst.trend !== undefined) ? inst.trend : -Infinity;
    }
    if (key === "cot") {
      const c = inst.cot;
      return (c && c.cell !== null && c.cell !== undefined) ? c.cell : -Infinity;
    }
    if (key.indexOf("ind:") === 0) {
      const k = key.slice(4);
      const c = (inst.indicator_cells || {})[k];
      const v = c && c.v;
      return (v === null || v === undefined) ? -Infinity : v;
    }
    return 0;
  }
  function compareInstruments(a, b) {
    const key = state.sortKey;
    if (!key) return b.score - a.score;                                // most bullish on top, most bearish bottom
    const av = rowSortValue(a, key);
    const bv = rowSortValue(b, key);
    let cmp;
    if (typeof av === "number" && typeof bv === "number") cmp = av - bv;
    else cmp = String(av).localeCompare(String(bv));
    return state.sortAsc ? cmp : -cmp;
  }

  // ---- Table --------------------------------------------------------------
  function indicatorCellHtml(inst, key) {
    const c = (inst.indicator_cells || {})[key] || { v: null, stale: false };
    const v = c.v;
    if (v === null || v === undefined) {
      return '<td class="econ-cell cell-na" title="not available for this instrument">—</td>';
    }
    if (c.stale) {
      return '<td class="econ-cell ec-stale" title="stale — latest release is outside the lookback window; excluded from scoring">' +
        fmtScoreCell(v) + '</td>';
    }
    // Continuous gradient on the per-indicator differential (saturates at ±4).
    return '<td class="econ-cell"' + styleAttr(gradientStyle(v, 4)) + ">" + fmtScoreCell(v) + "</td>";
  }

  // TREND sub-cell for an FX row (display-only). Same divergent color engine as
  // the factor cells (gradientStyle), on the ±3 trend scale. The pair's own price
  // series gives one number (NOT decomposed base−quote). Rows without a series
  // (US-DOLLAR single — DXY not exported) render blank.
  function trendCellHtml(inst) {
    const v = inst.trend;
    if (v === null || v === undefined) {
      return '<td class="econ-cell cell-empty"></td>';
    }
    const tip = "TREND · MA structure (SMA20/50/200) × ADX strength → cell " +
      fmtScoreCell(v) + " (weight 0.5 in the Score)";
    return '<td class="econ-cell"' + styleAttr(gradientStyle(v, 3)) +
      ' title="' + escAttr(tip) + '">' + fmtScoreCell(v) + "</td>";
  }

  // SENTIMENT > COT sub-cell for an FX row (display-only). Same divergent color
  // engine as the factor cells (gradientStyle), on the COT ±4 scale. Pairs whose
  // non-USD leg lacks COT data render blank (no "—", which means N/A).
  function fxCotCellHtml(inst) {
    const cot = inst.cot;
    if (!cot || cot.cell === null || cot.cell === undefined) {
      return '<td class="econ-cell cell-empty"></td>';
    }
    const v = cot.cell;
    function legTxt(ccy, cell, det) {
      let t = ccy + " " + fmtScoreCell(cell === null || cell === undefined ? 0 : cell);
      if (det) t += " (lvl " + fmtScoreCell(det.level) + ", flow " + fmtScoreCell(det.flow) + ")";
      return t;
    }
    const baseTxt = legTxt(cot.base, cot.base_cell, cot.base_detail);
    const tip = "COT positioning · " + (cot.quote
      ? "base " + baseTxt + " − quote " + legTxt(cot.quote, cot.quote_cell, cot.quote_detail) +
        " = cell " + fmtScoreCell(v)
      : baseTxt + " = cell " + fmtScoreCell(v));
    return '<td class="econ-cell"' + styleAttr(gradientStyle(v, 4)) +
      ' title="' + escAttr(tip) + '">' + fmtScoreCell(v) + "</td>";
  }

  function renderRow(inst) {
    const cells = columnKeys().map(k => indicatorCellHtml(inst, k)).join("");
    // Symbol / Bias / Score share one continuous gradient driven by the precise
    // score (saturates near ±6) → a smooth top-to-bottom column gradient.
    const sg = styleAttr(gradientStyle(inst.score, 6));
    return (
      '<tr data-symbol="' + escAttr(inst.symbol) + '">' +
      '<td class="sym"' + sg + ">" + (inst.display || inst.symbol) + "</td>" +
      '<td class="bias-cell"' + sg + ">" + inst.bias + "</td>" +
      '<td class="score-cell"' + sg + ">" + fmtScoreInt(inst.score) + "</td>" +
      trendCellHtml(inst) +
      fxCotCellHtml(inst) +
      cells +
      "</tr>"
    );
  }

  function renderTable() {
    const wrap = document.getElementById("econContent");
    const layout = tableLayout();
    const instruments = state.payload.instruments.filter(passesFilters).sort(compareInstruments);

    if (!instruments.length) {
      wrap.innerHTML = '<p class="muted" style="padding:40px;text-align:center;">No instruments match the selected filters.</p>';
      return;
    }

    const groupHeaderCells = layout.map(g =>
      '<th colspan="' + g.columns.length + '" class="grp-head grp-' + g.category + '">' +
      g.label + '</th>'
    ).join("");

    // TREND: top-level group placed FIRST (before SENTIMENT). Price MA structure
    // × ADX strength, direct on the pair. Contributes to Score with weight 0.5.
    const trendGroupHeader =
      '<th colspan="1" class="grp-head grp-trend" title="Price TREND: SMA20/50/200 structure × ADX(14) strength, ±3, direct on the pair. Blue = bullish, red = bearish. Weighted 0.5 in the Score.">TREND</th>';
    const trendSubHeader =
      '<th data-sort="trend" class="ind-head grp-trend" title="Price trend (weight 0.5 in score)">MA×ADX</th>';

    // SENTIMENT: top-level group placed after TREND, before the macro factor
    // groups. Contributes to Score with weight 0.5.
    const sentimentGroupHeader =
      '<th colspan="1" class="grp-head grp-sentiment" title="COT positioning sentiment: currency vs-USD extreme + 4-week flow, combined base − quote. Blue = bullish for the pair, red = bearish. Weighted 0.5 in the Score.">SENTIMENT</th>';
    const sentimentSubHeader =
      '<th data-sort="cot" class="ind-head grp-sentiment" title="COT positioning (weight 0.5 in score)">COT</th>';

    let subHeaderCells = "";
    layout.forEach(g => g.columns.forEach(c => {
      subHeaderCells += '<th data-sort="ind:' + c.key + '" class="ind-head grp-' + g.category +
        '" title="' + escAttr(indMeta(c.key).label || c.key) + '">' + c.label + '</th>';
    }));

    wrap.innerHTML =
      '<div class="retail-table-scroll econ-scroll">' +
      '<table class="retail-table econ-table">' +
      '<thead>' +
      '<tr>' +
      '<th rowspan="2" data-sort="symbol" class="col-sym">Symbol</th>' +
      '<th rowspan="2" data-sort="bias">Bias</th>' +
      '<th rowspan="2" data-sort="score">Score</th>' +
      trendGroupHeader + sentimentGroupHeader + groupHeaderCells +
      '</tr>' +
      '<tr>' + trendSubHeader + sentimentSubHeader + subHeaderCells + '</tr>' +
      '</thead>' +
      '<tbody>' + instruments.map(renderRow).join("") + '</tbody>' +
      '</table></div>';

    wrap.querySelectorAll("th[data-sort]").forEach(th => {
      th.style.cursor = "pointer";
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.sortKey === key) state.sortAsc = !state.sortAsc;
        else { state.sortKey = key; state.sortAsc = false; }
        renderTable();
      });
      if (state.sortKey === th.dataset.sort) {
        th.classList.add("sorted-" + (state.sortAsc ? "asc" : "desc"));
      }
    });

    wrap.querySelectorAll("tbody tr").forEach(tr => {
      tr.addEventListener("click", () => openModal(tr.dataset.symbol));
    });
  }

  // ---- Modal --------------------------------------------------------------
  function findInstrument(sym) {
    return state.payload.instruments.find(i => i.symbol === sym) || null;
  }

  function CATS() {
    return (state.payload.meta && state.payload.meta.categories_display) ||
      ["growth", "inflation", "labour"];
  }

  function flagBadge(flag) {
    if (!flag || flag === "ok") return '<span class="econ-flag flag-z">z-score</span>';
    if (flag === "fallback") return '<span class="econ-flag flag-fb" title="Too few prints for a stable sigma — scored on % surprise vs consensus">fallback</span>';
    if (flag === "no_consensus") return '<span class="econ-flag flag-nc" title="No consensus available — scored 0">no consensus</span>';
    return '<span class="econ-flag">' + flag + '</span>';
  }

  function indicatorRow(key, e) {
    const meta = indMeta(key);
    const inverted = meta.direction === -1
      ? ' <span class="econ-inv" title="Inverted: a higher actual is bearish for this currency">⤵</span>'
      : '';
    const zTxt = (e.z === null || e.z === undefined) ? "—" : fmtSigned(e.z, 2);
    const dateTxt = fmtDate(e.release_dt) +
      (e.age_days !== null && e.age_days !== undefined ? ' <span class="muted">(' + e.age_days + 'd)</span>' : '');
    const staleBadge = e.stale ? ' <span class="econ-flag flag-stale" title="Latest release is older than max_age_days — shown for visibility but excluded from the category average / index">stale</span>' : '';
    return (
      '<tr' + (e.stale ? ' class="ei-stale"' : '') + '>' +
      '<td class="ei-name">' + (meta.label || key) + inverted + '</td>' +
      '<td class="ei-num">' + fmtNum(e.actual) + '</td>' +
      '<td class="ei-num">' + fmtNum(e.consensus) + '</td>' +
      '<td class="ei-num">' + fmtSigned(e.surprise, 2) + '</td>' +
      '<td class="ei-num">' + zTxt + '</td>' +
      '<td class="ei-score ' + cellClass(e.score) + '">' + fmtScoreCell(e.score) + '</td>' +
      '<td class="ei-flag">' + flagBadge(e.flag) + staleBadge + '</td>' +
      '<td class="ei-date">' + dateTxt + '</td>' +
      '</tr>'
    );
  }

  function rateExpRow(e) {
    if (!e) return "";
    const yld = (e.latest_yield === null || e.latest_yield === undefined) ? "—" : Number(e.latest_yield).toFixed(3) + "%";
    const dw = (e.delta_w === null || e.delta_w === undefined) ? "—" : fmtSigned(e.delta_w, 3) + "pp";
    const z = (e.z === null || e.z === undefined) ? "—" : fmtSigned(e.z, 2);
    const stale = e.stale ? ' <span class="econ-flag flag-stale" title="2y yield older than ~7 business days">stale</span>' : "";
    const asof = e.as_of ? fmtDate(e.as_of) : "—";
    const src = e.source ? ' <span class="muted">(' + e.source + ")</span>" : "";
    return (
      '<tr' + (e.stale ? ' class="ei-stale"' : '') + '>' +
      '<td class="ei-name">Rate Expectations (2y)' + src + '</td>' +
      '<td class="ei-num">' + yld + '</td>' +
      '<td class="ei-num">' + dw + '</td>' +
      '<td class="ei-num">' + z + '</td>' +
      '<td class="ei-score ' + cellClass(e.score) + '">' + fmtScoreCell(e.score) + '</td>' +
      '<td class="ei-flag">' + (e.method || "—") + stale + '</td>' +
      '<td class="ei-date">' + asof + '</td>' +
      '</tr>'
    );
  }

  function legHtml(role, currency) {
    if (!currency) return "";
    const card = (state.payload.currencies || {})[currency];
    if (!card) {
      return '<div class="modal-card econ-leg"><h3>' + role + ' — ' + currency +
        '</h3><p class="muted">No data.</p></div>';
    }
    const breakdown = card.breakdown || {};
    const cats = CATS().slice();
    Object.keys(breakdown).forEach(k => {
      const c = indMeta(k).category;
      if (c && cats.indexOf(c) === -1) cats.push(c);
    });

    let groups = "";
    cats.forEach(catKey => {
      const keys = Object.keys(breakdown).filter(k => indMeta(k).category === catKey);
      if (!keys.length) return;
      keys.sort((a, b) => (indMeta(a).label || a).localeCompare(indMeta(b).label || b));
      const sub = (card.categories || {})[catKey];
      let subHtml = "";
      if (sub) {
        subHtml = '<span class="econ-cat-sub ' + cellClass(sub.score_cell) + '">' +
          fmtScoreCell(sub.score_cell) + '</span>' +
          '<span class="muted"> · precise ' + fmtSigned(sub.score_precise, 2) +
          ' · n' + (sub.coverage || 0) + '</span>';
      } else {
        subHtml = '<span class="muted">display-only</span>';
      }
      let table;
      if (catKey === "monetary") {
        table =
          '<thead><tr><th>Indicator</th><th>Latest 2y</th><th>Δ2y(1m)</th><th>z</th><th>Score</th><th>Method</th><th>As-of</th></tr></thead>' +
          '<tbody>' + keys.map(k => rateExpRow(breakdown[k])).join("") + '</tbody>';
      } else {
        table =
          '<thead><tr><th>Indicator</th><th>Act</th><th>Cons</th><th>Surp</th><th>z</th><th>Score</th><th>Method</th><th>Release</th></tr></thead>' +
          '<tbody>' + keys.map(k => indicatorRow(k, breakdown[k])).join("") + '</tbody>';
      }
      groups +=
        '<div class="econ-cat-group">' +
        '<div class="econ-cat-head">' + catLabel(catKey) + ' ' + subHtml + '</div>' +
        '<div class="econ-ind-scroll"><table class="econ-ind-table">' + table + '</table></div></div>';
    });

    if (!groups) groups = '<p class="muted">No indicators within the lookback window.</p>';

    const idx = card.index;
    return (
      '<div class="modal-card econ-leg">' +
      '<h3>' + role + ' — ' + currency +
      ' <span class="econ-leg-index" title="Currency macro index (~−10…+10)">index ' + fmtSigned(idx, 2) +
      ' · n' + (card.coverage || 0) + '</span></h3>' +
      groups +
      '</div>'
    );
  }

  // TREND decomposition section for a modal (shared FX + cross-asset). Reads
  // inst.trend_detail {short,long,slope,raw,adx,factor,trend_cell}. None / all-null
  // → "no data" (no price series). Placed FIRST (matches the TREND-first column).
  function trendSectionHtml(detail) {
    const head0 = '<div class="econ-cat-group"><div class="econ-cat-head">Trend ';
    if (!detail || detail.trend_cell === null || detail.trend_cell === undefined) {
      return head0 + '<span class="econ-cat-sub">no data</span></div>' +
        '<div class="econ-ind-scroll"><p class="muted" style="padding:6px 10px;">' +
        'No price series for this instrument — trend excluded from the Score.</p></div></div>';
    }
    const d = detail, cls = cellClass(d.trend_cell);
    const head = head0 +
      '<span class="econ-cat-sub ' + cls + '">cell ' + fmtScoreCell(d.trend_cell) + '</span>' +
      ' <span class="muted">· weight 0.5 · MA structure × ADX strength</span></div>';
    const row = (name, v, c) => '<tr><td class="ei-name">' + name + '</td><td class="ei-num ' +
      (c || "") + '">' + v + '</td></tr>';
    const rows =
      row("Short (SMA10 vs SMA20)", fmtScoreCell(d.short), cellClass(d.short)) +
      row("Long (SMA20 vs SMA50)", fmtScoreCell(d.long), cellClass(d.long)) +
      row("Slope (SMA20, 10 bars)", fmtScoreCell(d.slope), cellClass(d.slope)) +
      row("Raw (sum, ±3)", fmtScoreCell(d.raw), "") +
      row("ADX(14)", Number(d.adx).toFixed(1), "") +
      row("Strength factor", "×" + Number(d.factor).toFixed(2), "") +
      row("<strong>Trend cell = clamp(round(raw × factor))</strong>",
          "<strong>" + fmtScoreCell(d.trend_cell) + "</strong>", cls);
    return head + '<div class="econ-ind-scroll"><table class="econ-ind-table">' +
      '<thead><tr><th>Component</th><th>Value</th></tr></thead><tbody>' + rows +
      '</tbody></table></div></div>';
  }

  // SENTIMENT (COT) section for an FX modal — a TABLE (parity with cross-asset's
  // caSentimentSection), replacing the old one-line note. base − quote legs.
  function fxCotSectionHtml(cot) {
    const head0 = '<div class="econ-cat-group"><div class="econ-cat-head">Sentiment (COT) ';
    if (!cot || cot.cell === null || cot.cell === undefined) {
      return head0 + '<span class="econ-cat-sub">no data</span></div>' +
        '<div class="econ-ind-scroll"><p class="muted" style="padding:6px 10px;">' +
        'No COT positioning for this pair.</p></div></div>';
    }
    const cls = cellClass(cot.cell);
    const head = head0 +
      '<span class="econ-cat-sub ' + cls + '">cell ' + fmtScoreCell(cot.cell) + '</span>' +
      ' <span class="muted">· weight 0.5 · positioning, base − quote (vs-USD)</span></div>';
    const legRow = (role, ccy, cell, det) => {
      if (ccy === null || ccy === undefined) return "";
      const extra = det ? (" · level " + fmtScoreCell(det.level) + ", flow " + fmtScoreCell(det.flow))
        : (ccy === "USD" ? " · vs-USD leg = 0" : "");
      const c = (cell === null || cell === undefined) ? 0 : cell;
      return '<tr><td class="ei-name">' + role + " " + ccy + extra + '</td><td class="ei-num ' +
        cellClass(c) + '">' + fmtScoreCell(c) + '</td></tr>';
    };
    const rows = legRow("Base", cot.base, cot.base_cell, cot.base_detail) +
      (cot.quote ? legRow("Quote", cot.quote, cot.quote_cell, cot.quote_detail) : "") +
      '<tr><td class="ei-name"><strong>Pair cell (base − quote)</strong></td><td class="ei-num ' +
      cls + '"><strong>' + fmtScoreCell(cot.cell) + '</strong></td></tr>';
    return head + '<div class="econ-ind-scroll"><table class="econ-ind-table">' +
      '<thead><tr><th>Leg</th><th>Cell</th></tr></thead><tbody>' + rows +
      '</tbody></table></div></div>';
  }

  function openModal(symKey) {
    const inst = findInstrument(symKey);
    if (!inst) return;
    state.activeSymbol = inst;

    const modal = document.getElementById("econDetailModal");
    const body = document.getElementById("econDetailContent");

    const base = (inst.breakdown && inst.breakdown.base) ? inst.breakdown.base.currency : null;
    const quote = (inst.breakdown && inst.breakdown.quote) ? inst.breakdown.quote.currency : null;
    const isFx = inst.type === "fx";

    const gridClass = isFx ? "modal-grid econ-leg-grid" : "modal-grid econ-leg-grid one-col";
    const legs = isFx
      ? legHtml("Base", base) + legHtml("Quote", quote)
      : legHtml("Currency", base);

    const sub =
      isFx
        ? "FX pair · score = (index(" + base + ") − index(" + quote + ")) / " +
          (state.payload.meta.pair_divisor || 2)
        : "Single currency · score = index × sign";

    body.innerHTML =
      '<header class="modal-header">' +
      '<h2>' + (inst.display || inst.symbol) + ' <small class="muted">(' + inst.symbol + ')</small></h2>' +
      '<div class="muted modal-subhead">' +
      '<span class="pill ' + biasClass(inst.bias) + '">' + inst.bias + '</span> ' +
      '<span class="modal-score">Score ' + fmtSigned(inst.score, 2) + '</span>' +
      '<span class="modal-formula">' + sub + '</span></div>' +
      '</header>' +
      '<p class="muted econ-modal-note">Rounded cells can hide divergence — e.g. a Labour score near 0 may be ' +
      'NFP +2 against Jobless Claims −2. The per-indicator rows below show the real spread.</p>' +
      // TREND first (matches column order), then Sentiment (COT), then the macro legs.
      trendSectionHtml(inst.trend_detail) +
      fxCotSectionHtml(inst.cot) +
      '<div class="' + gridClass + '">' + legs + '</div>';

    modal.hidden = false;
    document.body.classList.add("modal-open");
  }

  function closeModal() {
    const modal = document.getElementById("econDetailModal");
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    state.activeSymbol = null;
  }

  function wireModalClose() {
    const modal = document.getElementById("econDetailModal");
    modal.querySelector(".modal-close").addEventListener("click", closeModal);
    modal.querySelector(".modal-backdrop").addEventListener("click", closeModal);
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && !modal.hidden) closeModal();
    });
  }

  // ---- Cross-Asset section (indices + metals) -----------------------------
  function caLayout() {
    const ca = state.payload.crossasset || {};
    return ca.table_layout || [];
  }
  function caFactor(inst, name) {
    return (inst.factors || []).find(f => f.name === name) || null;
  }

  // Sub-column cell = the PER-ASSET signed score (home-ccy raw × category sign),
  // so blue = bullish-for-this-asset, red = bearish. Sign is applied once in the
  // render layer; do not re-apply here.
  function caCellHtml(inst, key) {
    const c = (inst.cells || {})[key] || { score: null };
    const v = c.score;
    if (v === null || v === undefined) {
      return '<td class="econ-cell cell-na" title="not available">—</td>';
    }
    if (c.stale) {
      return '<td class="econ-cell ec-stale" title="stale — outside the lookback window; excluded from scoring">' +
        fmtScoreCell(v) + "</td>";
    }
    return '<td class="econ-cell"' + styleAttr(gradientStyle(v, 2)) + ">" + fmtScoreCell(v) + "</td>";
  }

  // TREND sub-cell (display-only) for a cross-asset row. Direct on the asset's
  // own price series (±3, gradientStyle scale 3). Rows without a series
  // (NASDAQ/DAX/NIKKEI/FTSE100) render blank.
  function caTrendCellHtml(inst) {
    const v = inst.trend;
    if (v === null || v === undefined) {
      return '<td class="econ-cell cell-empty"></td>';
    }
    const tip = "TREND · MA structure (SMA20/50/200) × ADX strength → cell " +
      fmtScoreCell(v) + " (weight 0.5 in the Score)";
    return '<td class="econ-cell"' + styleAttr(gradientStyle(v, 3)) +
      ' title="' + escAttr(tip) + '">' + fmtScoreCell(v) + "</td>";
  }

  // SENTIMENT sub-cell (display-only). One column, two sources by row:
  //   - metals  → COT positioning (±4, gradientStyle scale 4)
  //   - US idx  → P/C equity contrarian (±3, gradientStyle scale 3)
  // Rows with neither (foreign indices, or data missing) render blank — no "—",
  // which means N/A. Same divergent color engine as the factor cells.
  function caCotCellHtml(inst) {
    // P/C (US equity indices) — takes precedence; metals never carry .sentiment.
    const pc = inst.sentiment;
    if (pc && pc.source === "pc" && pc.cell !== null && pc.cell !== undefined) {
      const v = pc.cell;
      const tip = "P/C equity contrarian · percentile " + Number(pc.pct).toFixed(0) +
        " (1Y) → cell " + fmtScoreCell(v) + " · " + pc.basis;
      return '<td class="econ-cell"' + styleAttr(gradientStyle(v, 3)) +
        ' title="' + escAttr(tip) + '">' + fmtScoreCell(v) + "</td>";
    }
    // COT (metals).
    const cot = inst.cot;
    if (!cot || cot.cell === null || cot.cell === undefined) {
      return '<td class="econ-cell cell-empty"></td>';
    }
    const v = cot.cell;
    const tip = "COT positioning · level " + fmtScoreCell(cot.level) +
      " + flow " + fmtScoreCell(cot.flow) + " = cell " + fmtScoreCell(v) +
      " · blend " + Number(cot.blend).toFixed(0) +
      (cot.z === null || cot.z === undefined ? "" : " · z " + fmtSigned(cot.z, 2)) +
      " · " + cot.basis;
    return '<td class="econ-cell"' + styleAttr(gradientStyle(v, 4)) +
      ' title="' + escAttr(tip) + '">' + fmtScoreCell(v) + "</td>";
  }

  function renderCrossAsset() {
    const ca = state.payload.crossasset;
    const section = document.getElementById("crossassetSection");
    const wrap = document.getElementById("crossassetContent");
    if (!section || !wrap || !ca || !(ca.instruments || []).length) return;
    section.hidden = false;

    const layout = caLayout();
    const insts = ca.instruments.slice().sort((a, b) => b.score_precise - a.score_precise);

    const groupHeaders = layout.map(g =>
      '<th colspan="' + g.columns.length + '" class="grp-head grp-' + g.key +
      '" title="Per-asset directional score (home-ccy reading × category sign): blue = bullish for this asset, red = bearish. Click a row for the full breakdown.">' +
      g.label + '</th>').join("");

    // TREND: top-level group placed FIRST (before SENTIMENT). Price MA structure
    // × ADX strength, direct on the asset. Contributes to Score with weight 0.5.
    const trendGroupHeader =
      '<th colspan="1" class="grp-head grp-trend" title="Price TREND: SMA20/50/200 structure × ADX(14) strength, ±3, direct on the asset. Blue = bullish, red = bearish. Weighted 0.5 in the Score.">TREND</th>';
    const trendSubHeader = '<th class="ind-head grp-trend">MA×ADX</th>';

    // SENTIMENT: top-level group placed after TREND, before the macro factor
    // groups. Contributes to Score with weight 0.5.
    const sentimentGroupHeader =
      '<th colspan="1" class="grp-head grp-sentiment" title="Positioning sentiment (COT for metals, P/C equity for US indices), contrarian. Blue = bullish for the asset, red = bearish. Weighted 0.5 in the Score.">SENTIMENT</th>';
    const sentimentSubHeader = '<th class="ind-head grp-sentiment">COT / P/C</th>';

    let subHeaders = "";
    layout.forEach(g => g.columns.forEach(c => {
      subHeaders += '<th class="ind-head grp-' + g.key + '">' + c.label + '</th>';
    }));

    const rows = insts.map(inst => {
      const bcls = biasClass(inst.bias_label);
      let cells = "";
      layout.forEach(g => g.columns.forEach(c => { cells += caCellHtml(inst, c.key); }));
      return (
        '<tr data-ca-symbol="' + escAttr(inst.symbol) + '">' +
        '<td class="sym biasfill ' + bcls + '">' + (inst.display || inst.symbol) + '</td>' +
        '<td class="bias-cell biasfill ' + bcls + '">' + inst.bias_label + '</td>' +
        '<td class="score-cell biasfill ' + bcls + '">' + fmtScoreInt(inst.score_precise) + '</td>' +
        caTrendCellHtml(inst) + caCotCellHtml(inst) + cells + '</tr>'
      );
    }).join("");

    wrap.innerHTML =
      '<div class="retail-table-scroll econ-scroll">' +
      '<table class="retail-table econ-table">' +
      '<thead>' +
      '<tr><th rowspan="2" class="col-sym">Symbol</th><th rowspan="2">Bias</th><th rowspan="2">Score</th>' +
      trendGroupHeader + sentimentGroupHeader + groupHeaders + '</tr>' +
      '<tr>' + trendSubHeader + sentimentSubHeader + subHeaders + '</tr>' +
      '</thead>' +
      '<tbody>' + rows + '</tbody></table></div>';

    wrap.querySelectorAll("tbody tr").forEach(tr => {
      tr.addEventListener("click", () => openCrossAssetModal(tr.dataset.caSymbol));
    });
  }

  // Modal: a rate sub-component row (raw / sign / weight / contribution). A stale
  // component shows its value greyed with a "stale" badge (excluded from the
  // mean); a genuinely-missing one shows "absent".
  function caRateRow(c) {
    const hasVal = c.raw !== null && c.raw !== undefined;
    const contrib = hasVal ? fmtSigned((Math.abs(c.contribution) < 0.05 ? 0 : c.contribution), 1) : "—";
    const cls = hasVal ? cellClass(Math.round(c.contribution)) : "";
    const label = ({rate_exp_2y: "Rate Expectations (2Y)", real_yield_10y: "10Y Real Yield",
                    balance_sheet: "Net Liquidity"})[c.name] || c.name;
    const flag = c.stale
      ? '<span class="econ-flag flag-stale" title="stale — shown for visibility, excluded from the rates mean">stale</span>'
      : (hasVal ? "" : '<span class="econ-flag flag-stale" title="not available">absent</span>');
    return (
      "<tr" + (c.present ? "" : ' class="ei-stale"') + ">" +
      '<td class="ei-name">' + label + '</td>' +
      '<td class="ei-num">' + (hasVal ? fmtSigned(c.raw, 0) : "—") + '</td>' +
      '<td class="ei-num">' + fmtSigned(c.sign, 0) + '</td>' +
      '<td class="ei-num">' + Number(c.weight).toFixed(1) + '</td>' +
      '<td class="ei-score ' + cls + '">' + contrib + '</td>' +
      '<td class="ei-flag">' + flag + '</td>' +
      '<td class="ei-date">' + (c.source || "") + '</td>' +
      "</tr>"
    );
  }

  // Modal per-indicator row for cross-asset: like the FX indicatorRow, but the
  // Score cell shows the PER-ASSET signed score (home-ccy score × category sign)
  // and is colored accordingly. Act / Cons / Surp / z stay raw.
  function caIndicatorRow(key, e, sign) {
    const meta = indMeta(key);
    const zTxt = (e.z === null || e.z === undefined) ? "—" : fmtSigned(e.z, 2);
    const dateTxt = fmtDate(e.release_dt) +
      (e.age_days !== null && e.age_days !== undefined ? ' <span class="muted">(' + e.age_days + 'd)</span>' : '');
    const staleBadge = e.stale ? ' <span class="econ-flag flag-stale" title="stale — excluded from the category average">stale</span>' : '';
    const signed = (e.score === null || e.score === undefined) ? null : sign * e.score;
    const scoreTxt = (signed === null) ? "—" : fmtScoreCell(signed);
    const scoreCls = (signed === null) ? "" : cellClass(signed);
    return (
      '<tr' + (e.stale ? ' class="ei-stale"' : '') + '>' +
      '<td class="ei-name">' + (meta.label || key) + '</td>' +
      '<td class="ei-num">' + fmtNum(e.actual) + '</td>' +
      '<td class="ei-num">' + fmtNum(e.consensus) + '</td>' +
      '<td class="ei-num">' + fmtSigned(e.surprise, 2) + '</td>' +
      '<td class="ei-num">' + zTxt + '</td>' +
      '<td class="ei-score ' + scoreCls + '">' + scoreTxt + '</td>' +
      '<td class="ei-flag">' + flagBadge(e.flag) + staleBadge + '</td>' +
      '<td class="ei-date">' + dateTxt + '</td>' +
      '</tr>'
    );
  }

  // Modal: one category section. growth/inflation/labour show the home-ccy
  // per-indicator breakdown with the per-asset signed Score; rates shows its
  // sub-components. The section header carries the signed contribution.
  function caCatSection(inst, group) {
    const f = caFactor(inst, group.key);
    const home = inst.home_ccy;
    const present = f && f.present;
    const contrib = present ? fmtSigned((Math.abs(f.contribution) < 0.05 ? 0 : f.contribution), 2) : "—";
    const cls = present ? cellClass(Math.round(f.contribution)) : "";
    const sign = (f && f.sign !== undefined && f.sign !== null) ? f.sign : 1;
    const signTxt = (f && f.sign !== undefined && f.sign !== null)
      ? ' <span class="muted">· sign ' + fmtSigned(f.sign, 0) + "</span>" : "";
    const head =
      '<div class="econ-cat-head">' + group.label +
      ' <span class="econ-cat-sub ' + cls + '">contrib ' + contrib + "</span>" + signTxt + "</div>";

    let table, note = "";
    if (group.key === "rates") {
      const comps = (f && f.components) || [];
      table =
        '<thead><tr><th>Sub-component</th><th>Raw</th><th>Sign</th><th>Weight</th><th>Contribution</th><th></th><th>Source</th></tr></thead>' +
        '<tbody>' + comps.map(caRateRow).join("") + "</tbody>";
      const nl = (state.payload.crossasset || {}).net_liquidity || {};
      const bits = ["Net Liquidity = WALCL − TGA − RRP"];
      if (nl.present) {
        bits.push("21d roc " + (nl.roc == null ? "—" : fmtSigned(nl.roc * 100, 2) + "%/mo"));
        if (nl.latest != null) bits.push("level " + Number(nl.latest).toFixed(0));
        if (nl.as_of) bits.push("@ " + fmtDate(nl.as_of) + (nl.stale ? " (stale)" : ""));
      }
      note = '<div class="muted" style="font-size:11px;margin-top:4px;">' + bits.join(" · ") + "</div>";
    } else {
      const brk = ((state.payload.currencies || {})[home] || {}).breakdown || {};
      const keys = group.columns.map(c => c.key).filter(k => brk[k]);
      const rowsHtml = keys.length
        ? keys.map(k => caIndicatorRow(k, brk[k], sign)).join("")
        : '<tr><td class="ei-name muted" colspan="8">no home-currency data</td></tr>';
      table =
        '<thead><tr><th>Indicator (' + home + ')</th><th>Act</th><th>Cons</th><th>Surp</th><th>z</th><th>Score</th><th>Method</th><th>Release</th></tr></thead>' +
        '<tbody>' + rowsHtml + "</tbody>";
    }
    return '<div class="econ-cat-group">' + head +
      '<div class="econ-ind-scroll"><table class="econ-ind-table">' + table + "</table></div>" +
      note + "</div>";
  }

  // Modal: the SENTIMENT factor row (cell / sign / weight / contribution). Only
  // rendered when the instrument carries a sentiment factor (metals + US idx);
  // foreign indices return "" → no section (score identical to baseline).
  function caSentimentSection(inst) {
    const f = caFactor(inst, "sentiment");
    if (!f) return "";
    const present = f.present;
    const contrib = present ? fmtSigned((Math.abs(f.contribution) < 0.05 ? 0 : f.contribution), 2) : "—";
    const cls = present ? cellClass(Math.round(f.value)) : "";
    let detail = "—";
    if (inst.cot) {
      const c = inst.cot;
      detail = "COT · level " + fmtScoreCell(c.level) + " + flow " + fmtScoreCell(c.flow) +
        " = cell " + fmtScoreCell(c.cell) + " · blend " + Number(c.blend).toFixed(0);
    } else if (inst.sentiment && inst.sentiment.source === "pc") {
      const c = inst.sentiment;
      detail = "P/C equity contrarian · percentile " + Number(c.pct).toFixed(0) +
        " (1Y) → cell " + fmtScoreCell(c.cell);
    }
    const head = '<div class="econ-cat-head">Sentiment ' +
      '<span class="econ-cat-sub ' + cls + '">contrib ' + contrib + '</span>' +
      ' <span class="muted">· weight ' + Number(f.weight).toFixed(1) + '</span></div>';
    const table =
      '<thead><tr><th>Source</th><th>Cell</th><th>Sign</th><th>Weight</th><th>Contribution</th></tr></thead>' +
      '<tbody><tr>' +
      '<td class="ei-name">' + detail + '</td>' +
      '<td class="ei-num">' + (f.raw === null || f.raw === undefined ? "—" : fmtScoreCell(f.raw)) + '</td>' +
      '<td class="ei-num">' + fmtSigned(f.sign, 0) + '</td>' +
      '<td class="ei-num">' + Number(f.weight).toFixed(1) + '</td>' +
      '<td class="ei-score ' + cls + '">' + contrib + '</td>' +
      '</tr></tbody>';
    return '<div class="econ-cat-group">' + head +
      '<div class="econ-ind-scroll"><table class="econ-ind-table">' + table + '</table></div></div>';
  }

  function openCrossAssetModal(sym) {
    const ca = state.payload.crossasset;
    const inst = (ca.instruments || []).find(i => i.symbol === sym);
    if (!inst) return;
    const modal = document.getElementById("econDetailModal");
    const body = document.getElementById("econDetailContent");

    // SENTIMENT first (matches the table column order), then the macro factors.
    // TREND first (matches the column order), then SENTIMENT, then macro factors.
    const sections = trendSectionHtml(inst.trend_detail) + caSentimentSection(inst) +
      caLayout().map(g => caCatSection(inst, g)).join("");
    body.innerHTML =
      '<header class="modal-header">' +
      '<h2>' + (inst.display || inst.symbol) + ' <small class="muted">(' + inst.symbol + ' · ' + inst.home_ccy + ')</small></h2>' +
      '<div class="muted modal-subhead">' +
      '<span class="pill ' + biasClass(inst.bias_label) + '">' + inst.bias_label + '</span> ' +
      '<span class="modal-score">Score ' + fmtSigned(inst.score_precise, 2) + '</span>' +
      '<span class="modal-formula">' + inst.type + ' · weighted mean over ' + inst.coverage +
      ' present factor(s) × scale</span></div>' +
      '</header>' +
      '<p class="muted econ-modal-note">Each Score is the per-asset directional score for ' +
      (inst.display || inst.symbol) + ' (' + inst.home_ccy +
      ' reading × category sign); blue = bullish, red = bearish. Act / Cons / Surp / z stay raw. ' +
      'The category header shows the signed contribution; Rates groups the 2Y and 10Y-real components (bounded).</p>' +
      '<div class="modal-grid econ-leg-grid one-col"><div class="modal-card econ-leg">' +
      sections + '</div></div>';

    modal.hidden = false;
    document.body.classList.add("modal-open");
  }

  // ---- Theme (match other pages) -----------------------------------------
  function applyStoredTheme() {
    try { if (localStorage.getItem("cot-theme") === "dark") document.body.classList.add("dark"); }
    catch (_) {}
  }

  // ---- Bootstrap ----------------------------------------------------------
  function init(payload) {
    state.payload = payload;
    renderMeta();
    renderFilters();
    wireModalClose();
    renderTable();
    renderCrossAsset();
  }

  function boot() {
    applyStoredTheme();
    fetch(DATA_URL, { cache: "no-store" })
      .then(r => {
        if (!r.ok) throw new Error("Failed to load " + DATA_URL + ": HTTP " + r.status);
        return r.json();
      })
      .then(init)
      .catch(err => {
        console.error("economic-chart bootstrap error:", err);
        const wrap = document.getElementById("econContent");
        if (wrap) {
          wrap.innerHTML = '<p style="color:var(--muted);text-align:center;padding:40px;">Failed to load economic data.</p>';
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
