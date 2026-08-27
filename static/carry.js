/* Carry page logic. Pure DOM over /data/carry.json — no charts.
 * Shows the overnight interest-rate differential (carry_pct = base_rate -
 * quote_rate, the LONG-the-pair perspective) for every FX pair /economic
 * shows, sorted carry desc. Direction chips flip the sign of every row (and
 * the sort order) for the SHORT perspective; blue always ends up on top,
 * because "on top" is defined by the currently-selected direction's own
 * sign, not by the raw stored value.
 */
(function () {
  "use strict";

  if (!window.ScorePalette) {
    throw new Error("score-palette.js must be loaded before carry.js");
  }
  const gradientStyle = window.ScorePalette.gradientStyle;
  const styleAttr = window.ScorePalette.styleAttr;
  const COT_BLUE = window.ScorePalette.COT_BLUE;
  const COT_RED = window.ScorePalette.COT_RED;

  const DATA_URL = window.CARRY_DATA_URL || "/data/carry.json";

  const state = {
    payload: null,
    direction: "long",     // "long" | "short"
    ccySel: new Set(),     // empty = no currency filter (show all)
  };

  // ---- Formatting -----------------------------------------------------
  function fmtSigned(v, dp) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    const s = n.toFixed(dp === undefined ? 2 : dp);
    return (n > 0 ? "+" : "") + s;
  }
  function fmtRate(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return null;
    return Number(v).toFixed(2);
  }
  function fmtAsOf(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toISOString().slice(0, 16).replace("T", " ") + "Z";
  }
  function escAttr(s) { return String(s).replace(/"/g, "&quot;"); }

  const CHIP_SVG =
    '<svg class="chip-box" viewBox="0 0 16 16" aria-hidden="true">' +
    '<rect class="chip-box-rect" x="2" y="2" width="12" height="12" rx="2.5" ry="2.5" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
    '<path class="chip-box-check" d="M4.5 8.4l2.4 2.4L11.8 5.6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
    '</svg>';

  // ---- Direction: carry_pct as seen from the currently-selected side.
  // LONG = base_rate - quote_rate (as stored); SHORT = the mirror image.
  function directedCarry(row) {
    if (row.carry_pct === null || row.carry_pct === undefined) return null;
    return state.direction === "short" ? -row.carry_pct : row.carry_pct;
  }

  // ---- Filters ----------------------------------------------------------
  function passesFilters(row) {
    if (state.ccySel.size && !state.ccySel.has(row.base) && !state.ccySel.has(row.quote)) return false;
    return true;
  }

  function sortedRows() {
    const rows = (state.payload.rows || []).filter(passesFilters);
    const available = rows.filter(function (r) { return r.available; });
    const unavailable = rows.filter(function (r) { return !r.available; });
    available.sort(function (a, b) { return directedCarry(b) - directedCarry(a); });
    return available.concat(unavailable);
  }

  // ---- Meta-bar -----------------------------------------------------------
  function currencyStatus() {
    const rates = state.payload.rates || {};
    const asOf = new Date(state.payload.as_of);
    const staleDays = state.payload.stale_after_days;
    let missing = 0, stale = 0;
    Object.keys(rates).forEach(function (ccy) {
      const leg = rates[ccy] || {};
      if (leg.rate_pct === null || leg.rate_pct === undefined || !leg.verified) {
        missing += 1;
        return;
      }
      const verified = new Date(leg.verified);
      if (isNaN(verified.getTime())) return;
      const ageDays = Math.floor((asOf - verified) / 86400000);
      if (ageDays > staleDays) stale += 1;
    });
    return { missing: missing, stale: stale };
  }

  function renderMeta() {
    const el = document.getElementById("carryMeta");
    const p = state.payload;
    const parts = [];
    parts.push("As of " + (p.as_of || "—"));
    parts.push(p.configured + " valute configurate");
    if (p.generated_at) parts.push("Generated " + fmtAsOf(p.generated_at));
    let html = parts.join(" · ");

    const status = currencyStatus();
    if (status.missing || status.stale) {
      const bits = [];
      if (status.missing) bits.push(status.missing + " lipsă");
      if (status.stale) bits.push(status.stale + " stale");
      html += ' <span class="fresh-badge stale" title="' + escAttr(bits.join(", ")) +
        '">⚠ ' + bits.join(", ") + "</span>";
    }
    el.innerHTML = html;
  }

  // ---- Filter chips ---------------------------------------------------
  function renderDirectionChips() {
    const wrap = document.getElementById("carryDirChips");
    wrap.innerHTML = "";
    [["long", "Long"], ["short", "Short"]].forEach(function (pair) {
      const key = pair[0], label = pair[1];
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cat-chip" + (state.direction === key ? " selected" : "");
      btn.dataset.key = key;
      btn.setAttribute("aria-pressed", state.direction === key ? "true" : "false");
      btn.innerHTML = CHIP_SVG + "<span></span>";
      btn.querySelector("span").textContent = label;
      btn.addEventListener("click", function () {
        if (state.direction === key) return;
        state.direction = key;
        renderDirectionChips();
        renderTable();
      });
      wrap.appendChild(btn);
    });
  }

  function presentCurrencies() {
    const set = new Set();
    (state.payload.rows || []).forEach(function (r) {
      set.add(r.base);
      set.add(r.quote);
    });
    const CCY_ORDER = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"];
    return CCY_ORDER.filter(function (c) { return set.has(c); });
  }

  function renderCcyChips() {
    const wrap = document.getElementById("carryCcyChips");
    wrap.innerHTML = "";
    presentCurrencies().forEach(function (ccy) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cat-chip" + (state.ccySel.has(ccy) ? " selected" : "");
      btn.dataset.key = ccy;
      btn.setAttribute("aria-pressed", state.ccySel.has(ccy) ? "true" : "false");
      btn.innerHTML = CHIP_SVG + "<span></span>";
      btn.querySelector("span").textContent = ccy;
      btn.addEventListener("click", function () {
        if (state.ccySel.has(ccy)) state.ccySel.delete(ccy);
        else state.ccySel.add(ccy);
        btn.classList.toggle("selected");
        btn.setAttribute("aria-pressed", state.ccySel.has(ccy) ? "true" : "false");
        updateAllButton();
        renderTable();
      });
      wrap.appendChild(btn);
    });
  }

  function updateAllButton() {
    document.querySelectorAll('#carryCcyFilter .cat-action[data-action="all"]').forEach(function (b) {
      b.classList.toggle("active", state.ccySel.size === 0);
    });
  }

  function wireAllButton() {
    document.querySelectorAll('#carryCcyFilter .cat-action[data-action="all"]').forEach(function (b) {
      b.addEventListener("click", function () {
        state.ccySel.clear();
        document.querySelectorAll("#carryCcyChips .cat-chip").forEach(function (ch) {
          ch.classList.remove("selected");
          ch.setAttribute("aria-pressed", "false");
        });
        updateAllButton();
        renderTable();
      });
    });
  }

  function renderFilters() {
    renderDirectionChips();
    renderCcyChips();
    wireAllButton();
    updateAllButton();
  }

  // ---- Table --------------------------------------------------------------
  function legTip(ccy) {
    const leg = (state.payload.rates || {})[ccy] || {};
    const parts = [];
    if (leg.source_name) parts.push(leg.source_name);
    if (leg.effective) parts.push("effective " + leg.effective);
    if (leg.verified) parts.push("verified " + leg.verified);
    return parts.length ? parts.join(" · ") : "no data";
  }

  function legCellHtml(ccy, ratePct) {
    const r = fmtRate(ratePct);
    const text = r === null ? "—" : ccy + " " + r + "%";
    return '<td class="carry-leg-col" title="' + escAttr(legTip(ccy)) + '">' + text + "</td>";
  }

  function verifiedTip(row) {
    const rates = state.payload.rates || {};
    const b = rates[row.base] || {}, q = rates[row.quote] || {};
    const bits = [];
    if (b.verified) bits.push(row.base + " verified " + b.verified);
    if (q.verified) bits.push(row.quote + " verified " + q.verified);
    return bits.length ? bits.join(" · ") : "stale";
  }

  function carryBarHtml(v, scale) {
    if (v === null || v === undefined) {
      return '<td class="carry-bar-cell carry-cell-na">—</td>';
    }
    const clamped = Math.abs(v) > scale;
    const widthPct = Math.min(Math.abs(v) / scale, 1) * 50;
    const positive = v > 0;
    const rgb = positive ? COT_BLUE : COT_RED;
    const fillStyle = "width:" + widthPct.toFixed(2) + "%;background:rgb(" + rgb + ")";
    const fillCls = "carry-bar-fill " + (positive ? "pos" : "neg");
    const clampMark = clamped
      ? ' <span class="carry-clamp" title="carry excedează scala ±' + scale.toFixed(2) + 'pp — bară saturată">›</span>'
      : "";
    return '<td class="carry-bar-cell">' +
      '<div class="' + fillCls + '" style="' + fillStyle + '"></div>' +
      '<span class="carry-bar-value">' + fmtSigned(v, 2) + "%" + clampMark + "</span>" +
      "</td>";
  }

  function rowHtml(row) {
    const scale = state.payload.scale_pp || 5.0;
    const v = directedCarry(row);
    const symStyle = (row.available && v !== null) ? styleAttr(gradientStyle(v, scale)) : "";
    const staleBadge = row.stale
      ? ' <span class="flag-stale" title="' + escAttr(verifiedTip(row)) + '">STALE</span>'
      : "";
    const trCls = row.available ? "" : ' class="carry-row-na"';
    return "<tr" + trCls + ">" +
      '<td class="sym"' + symStyle + ">" + escAttr(row.display || row.symbol) + staleBadge + "</td>" +
      carryBarHtml(v, scale) +
      legCellHtml(row.base, row.base_rate) +
      legCellHtml(row.quote, row.quote_rate) +
      "</tr>";
  }

  function tableHtml() {
    const rows = sortedRows();
    let html = '<div class="retail-table-scroll"><table class="carry-table"><thead><tr>' +
      '<th class="col-sym">Symbol</th><th>Carry (% anual)</th>' +
      '<th class="carry-leg-th">Base</th><th class="carry-leg-th">Quote</th>' +
      "</tr></thead><tbody>";
    if (!rows.length) {
      html += '<tr><td colspan="4" class="muted" style="text-align:center;padding:24px;">No pairs match this filter.</td></tr>';
    } else {
      rows.forEach(function (r) { html += rowHtml(r); });
    }
    html += "</tbody></table></div>";
    return html;
  }

  function renderTable() {
    document.getElementById("carryContent").innerHTML = tableHtml();
  }

  // ---- Bootstrap ------------------------------------------------------
  function init(payload) {
    state.payload = payload;
    renderMeta();
    renderFilters();
    renderTable();
  }

  function boot() {
    fetch(DATA_URL, { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("Failed to load " + DATA_URL + ": HTTP " + r.status);
        return r.json();
      })
      .then(init)
      .catch(function (err) {
        console.error("carry.js bootstrap error:", err);
        const wrap = document.getElementById("carryContent");
        if (wrap) {
          wrap.innerHTML = '<p style="color:var(--muted);text-align:center;padding:40px;">Failed to load carry data.</p>';
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
