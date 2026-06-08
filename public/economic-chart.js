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
  };

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

  // ---- Color classes ------------------------------------------------------
  // Clamp magnitude so differentials beyond ±2 still color strongly.
  function cellClass(score) {
    const n = Number(score);
    if (Number.isNaN(n)) return "cell-z0";
    if (n >= 2) return "cell-p2";
    if (n === 1) return "cell-p1";
    if (n <= -2) return "cell-n2";
    if (n === -1) return "cell-n1";
    return "cell-z0";
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
  function dirClass(inst) {
    const b = inst.bias || "";
    if (b.indexOf("Bull") >= 0) return "dir-bull";
    if (b.indexOf("Bear") >= 0) return "dir-bear";
    return "dir-neut";
  }

  // ---- Meta-bar -----------------------------------------------------------
  function renderMeta() {
    const el = document.getElementById("econMeta");
    const p = state.payload;
    const parts = [];
    parts.push("As of " + fmtAsOf(p.as_of));
    parts.push(p.instruments.length + " instruments");
    if (p.generated_at) parts.push("Generated " + fmtAsOf(p.generated_at));
    el.textContent = parts.join(" · ");
  }
  function fmtAsOf(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
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
    if (key.indexOf("ind:") === 0) {
      const k = key.slice(4);
      const v = (inst.indicator_cells || {})[k];
      return (v === null || v === undefined) ? -Infinity : v;
    }
    return 0;
  }
  function compareInstruments(a, b) {
    const key = state.sortKey;
    if (!key) return Math.abs(b.score) - Math.abs(a.score);            // strongest first
    const av = rowSortValue(a, key);
    const bv = rowSortValue(b, key);
    let cmp;
    if (typeof av === "number" && typeof bv === "number") cmp = av - bv;
    else cmp = String(av).localeCompare(String(bv));
    return state.sortAsc ? cmp : -cmp;
  }

  // ---- Table --------------------------------------------------------------
  function indicatorCellHtml(inst, key) {
    const v = (inst.indicator_cells || {})[key];
    if (v === null || v === undefined) {
      return '<td class="econ-cell cell-na" title="not available for this instrument">—</td>';
    }
    return '<td class="econ-cell ' + cellClass(v) + '">' + fmtScoreCell(v) + '</td>';
  }

  function renderRow(inst) {
    const dir = dirClass(inst);
    const cells = columnKeys().map(k => indicatorCellHtml(inst, k)).join("");
    return (
      '<tr data-symbol="' + escAttr(inst.symbol) + '">' +
      '<td class="sym ' + dir + '">' + (inst.display || inst.symbol) + '</td>' +
      '<td class="bias-cell"><span class="pill ' + biasClass(inst.bias) + '">' + inst.bias + '</span></td>' +
      '<td class="score-cell ' + dir + '">' + fmtScoreInt(inst.score) + '</td>' +
      cells +
      '</tr>'
    );
  }

  function renderTable() {
    const wrap = document.getElementById("econContent");
    const layout = tableLayout();
    const instruments = state.payload.instruments.slice().sort(compareInstruments);

    const groupHeaderCells = layout.map(g =>
      '<th colspan="' + g.columns.length + '" class="grp-head grp-' + g.category + '">' +
      g.label + '</th>'
    ).join("");

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
      groupHeaderCells +
      '</tr>' +
      '<tr>' + subHeaderCells + '</tr>' +
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
    const staleBadge = e.stale ? ' <span class="econ-flag flag-stale" title="Older than max_age">stale</span>' : '';
    return (
      '<tr>' +
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
      groups +=
        '<div class="econ-cat-group">' +
        '<div class="econ-cat-head">' + catLabel(catKey) + ' ' + subHtml + '</div>' +
        '<div class="econ-ind-scroll"><table class="econ-ind-table">' +
        '<thead><tr><th>Indicator</th><th>Act</th><th>Cons</th><th>Surp</th><th>z</th><th>Score</th><th>Method</th><th>Release</th></tr></thead>' +
        '<tbody>' + keys.map(k => indicatorRow(k, breakdown[k])).join("") + '</tbody>' +
        '</table></div></div>';
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
      '<span class="modal-score ' + dirClass(inst) + '">Score ' + fmtSigned(inst.score, 2) + '</span>' +
      '<span class="modal-formula">' + sub + '</span></div>' +
      '</header>' +
      '<p class="muted econ-modal-note">Rounded cells can hide divergence — e.g. a Labour score near 0 may be ' +
      'NFP +2 against Jobless Claims −2. The per-indicator rows below show the real spread.</p>' +
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

  // ---- Theme (match other pages) -----------------------------------------
  function applyStoredTheme() {
    try { if (localStorage.getItem("cot-theme") === "dark") document.body.classList.add("dark"); }
    catch (_) {}
  }

  // ---- Bootstrap ----------------------------------------------------------
  function init(payload) {
    state.payload = payload;
    renderMeta();
    wireModalClose();
    renderTable();
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
