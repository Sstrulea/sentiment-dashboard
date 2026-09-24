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
  // A policy rate set as a target RANGE (Fed; decisions.parquet lower/upper):
  // the value is the midpoint, shown as the range around it (same half-width).
  function fmtPolicy(v, e) {
    if (!e || !e.range || v === null || v === undefined || Number.isNaN(Number(v))) return fmtNum(v);
    const hw = (Number(e.range.upper) - Number(e.range.lower)) / 2;
    return (Number(v) - hw).toFixed(2) + "–" + (Number(v) + hw).toFixed(2);
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
  // Dates (audit V2): the payload's release_dt is tz-naive UTC ("2026-09-18T02:54:00",
  // no "Z"); `new Date()` would read it as LOCAL time. Parse it as UTC, show the
  // viewer's local date. A date-only value ("2026-09-23", or an entry flagged
  // `date_only`, e.g. a BoJ decision with no time) is shown as-is: there is no
  // instant to convert, and inventing 00:00 would shift it a day west of UTC.
  function parseUtc(iso) {
    const s = String(iso);
    return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + "Z");
  }
  function fmtDate(iso, dateOnly) {
    if (!iso) return "—";
    const s = String(iso);
    if (dateOnly || /^\d{4}-\d{2}-\d{2}$/.test(s)) return s.slice(0, 10);
    const d = parseUtc(s);
    if (isNaN(d.getTime())) return s;
    const p = n => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }
  function escAttr(s) { return String(s).replace(/"/g, "&quot;"); }

  // Backend-computed contribution lookup (never recomputed here — see
  // src.economic_compute.compute_instrument, which derives `contributions`/
  // `contrib_sum`/`contrib_residual` via exact telescoping differences, so a
  // render-side bug can't mask itself by re-deriving its own "total").
  // Surfaced via `title` tooltip ONLY — cell box/row height must stay
  // pixel-identical to the raw-score-only layout; a title attribute never
  // affects layout.
  function findContribution(inst, key) {
    const list = inst.contributions || [];
    for (let i = 0; i < list.length; i++) {
      if (list[i].key === key) return list[i].contribution;
    }
    return null;
  }
  function contribTip(contribution) {
    if (contribution === null || contribution === undefined || Number.isNaN(contribution)) return "";
    return "Contributes " + fmtSigned(contribution, 2) + " to Score.";
  }

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

  // Divergent gradient + style-attr helpers now live in score-palette.js
  // (shared with /carry) — fail closed if it didn't load, rather than
  // silently falling back to some other color scheme.
  //
  // The throw below halts this IIFE before boot() is ever defined/wired to
  // DOMContentLoaded, so without the explicit message here the page would
  // sit on its static "Loading economic bias…" placeholder forever —
  // indistinguishable from "no data yet". Same user-facing style as boot()'s
  // own fetch-failure catch further down.
  if (!window.ScorePalette) {
    const wrap = document.getElementById("econContent");
    if (wrap) {
      wrap.innerHTML = '<p style="color:var(--muted);text-align:center;padding:40px;">Failed to load economic data (score-palette.js missing).</p>';
    }
    throw new Error("score-palette.js must be loaded before economic-chart.js");
  }
  const gradientStyle = window.ScorePalette.gradientStyle;
  const styleAttr = window.ScorePalette.styleAttr;

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

  // Per-source freshness badges (calendar / actuals pull / price). STALE → red
  // badge so a silent data freeze is visible instantly without comparing to
  // external sources. "Actuals pull" is the daily JBlanked pull — distinct from
  // calendar-stale (the weekly calendar feed carries no actuals, so the pull can
  // freeze while the calendar itself keeps updating); age_days === null means no
  // successful pull was EVER recorded ("never").
  function freshnessBadges(f) {
    if (!f) return "";
    let out = "";
    [["calendar", "Calendar"], ["actuals_pull", "Actuals pull"]].forEach(function (pair) {
      const v = f[pair[0]];
      if (!v) return;
      const age = (v.age_days === null || v.age_days === undefined) ? "never"
        : (v.age_days <= 0 ? "today" : v.age_days + "d ago");
      const cls = v.stale ? "fresh-badge stale" : "fresh-badge ok";
      const txt = (v.stale ? "⚠ STALE " : "") + pair[1] + " " + age;
      out += ' <span class="' + cls + '" title="last update ' + escAttr(v.last_update || "never") +
        '">' + txt + "</span>";
    });
    out += priceBadge(f.price);
    return out;
  }

  // Price is per-instrument (economic_render._freshness()'s "price" entry —
  // src.price_freshness_guard), not one aggregate age: a single frozen
  // instrument must be nameable, not hidden behind 35 healthy ones. "no_data"
  // instruments (e.g. DXY — no broker mapping) never appear here, at any
  // collapse level — freshness_report() already excludes them from `stale`.
  //   0 stale         -> green, no names
  //   1..3 stale       -> names inline: "FTSE100 88d"
  //   >3 stale         -> "N/M instruments", full names in the title attribute
  function priceBadge(v) {
    if (!v) return "";
    const stale = v.stale_instruments || [];
    const cls = v.stale ? "fresh-badge stale" : "fresh-badge ok";
    if (!v.stale) {
      const age = (v.age_days === null || v.age_days === undefined) ? "never"
        : (v.age_days <= 0 ? "today" : v.age_days + "d ago");
      return ' <span class="' + cls + '" title="last update ' + escAttr(v.last_update || "never") +
        '">Price ' + age + "</span>";
    }
    const named = stale.map(function (r) {
      return r.symbol + " " + (r.age_days === null || r.age_days === undefined ? "?" : r.age_days) + "d";
    });
    const total = v.total_count - (v.no_data ? v.no_data.length : 0);
    const label = stale.length <= 3 ? named.join(", ") : stale.length + "/" + total + " instruments";
    const title = named.join(", ") || "last update " + escAttr(v.last_update || "never");
    return ' <span class="' + cls + '" title="' + escAttr(title) +
      '">⚠ STALE Price: ' + label + "</span>";
  }
  function fmtAsOf(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
  }

  // ---- Manual Actuals Panel (feat/manual-actuals-panel) -----------------------
  // Rows the pipeline cannot use without a human: MISSING (scheduled, time
  // passed, nothing landed) or ZERO_CONFIRM (a 0.0 print that scoring would
  // quarantine — real flat print or unreleased placeholder, ambiguous). Both
  // states render identically (red/actionable) per spec; only the label differs.
  // One row, one decision — no bulk actions; each row submits independently to
  // POST /api/manual-actual (api/manual-actual.py), which commits an updated
  // data/manual_actuals_overrides.json via the GitHub API and then best-effort
  // triggers econ-refresh.yml. That means a successful submit does NOT
  // retroactively change THIS already-loaded payload — the row is marked
  // "applying" (never "saved"/hidden optimistically) and stays that way until
  // the operator reloads the page and the row is genuinely gone from the next
  // payload. The button count is still decremented optimistically since it's
  // just a UI counter, not a claim about what's live.
  function renderManualActualsButton() {
    const btn = document.getElementById("manualActualsBtn");
    if (!btn) return;
    const ma = state.payload.manual_actuals;
    if (!ma) { btn.hidden = true; return; }
    btn.hidden = false;
    const n = ma.count || 0;
    const older = ma.older_count || 0;
    // `older` is a count only (manual_actuals.RELEVANCE_WINDOW, 45d default) —
    // those rows are still fully actionable/overridable, just not listed here.
    const olderSuffix = older > 0 ? " (+" + older + " older)" : "";
    btn.textContent = (n === 0 ? "Needs review: 0" : "⚠ Needs review: " + n) + olderSuffix;
    btn.classList.toggle("has-items", n > 0);
    btn.onclick = openManualActualsModal;
  }

  function indicatorLabel(row) {
    const meta = (state.payload.meta && state.payload.meta.indicators) || {};
    return (meta[row.indicator_key] && meta[row.indicator_key].label) || row.name_canonical || row.indicator_key;
  }

  // `datetime_utc` is a tz-naive UTC string ("2026-07-31T09:00:00", no "Z")
  // straight from the parquet — new Date(iso) would parse it as LOCAL time
  // and silently shift it by the viewer's offset. Plain string slicing keeps
  // it exact regardless of where this runs.
  function fmtUtcNaive(iso) {
    if (!iso) return "—";
    return String(iso).replace("T", " ").slice(0, 16) + " UTC";
  }

  function manualActualsFormHtml(row) {
    if (row.state === "MISSING") {
      return '<div class="ma-form">' +
        '<input type="number" step="any" class="ma-input" placeholder="actual" aria-label="actual value">' +
        '<button type="button" class="ma-btn ma-submit">Submit</button>' +
        '</div>';
    }
    return '<div class="ma-form">' +
      '<button type="button" class="ma-btn ma-confirm">Confirm 0.0</button>' +
      '<span class="muted">or</span>' +
      '<input type="number" step="any" class="ma-input ma-correct" placeholder="correct to" aria-label="corrected value">' +
      '<button type="button" class="ma-btn ma-submit">Submit</button>' +
      '</div>';
  }

  function manualActualsRowHtml(row) {
    const stateLabel = row.state === "MISSING" ? "Missing" : "Zero — confirm";
    return '<tr data-canonical-id="' + escAttr(row.canonical_id) + '" data-currency="' + escAttr(row.currency) +
      '" data-indicator-key="' + escAttr(row.indicator_key) + '" data-datetime-utc="' + escAttr(row.datetime_utc) +
      '" data-state="' + escAttr(row.state) + '">' +
      '<td class="ei-name">' + row.currency + '</td>' +
      '<td class="ei-name">' + indicatorLabel(row) + '</td>' +
      '<td class="ei-date">' + fmtUtcNaive(row.datetime_utc) + '</td>' +
      '<td class="ei-num">' + fmtNum(row.forecast) + '</td>' +
      '<td class="ei-score ma-cell">' +
      '<span class="pill pill-bearish ma-state">' + stateLabel + '</span>' +
      manualActualsFormHtml(row) +
      '<input type="text" class="ma-input ma-note" placeholder="note (optional — source link)" aria-label="note">' +
      '</td></tr>';
  }

  function openManualActualsModal() {
    const ma = state.payload.manual_actuals || { count: 0, rows: [] };
    const modal = document.getElementById("econDetailModal");
    const body = document.getElementById("econDetailContent");

    const rowsHtml = ma.rows.length
      ? ma.rows.map(manualActualsRowHtml).join("")
      : '<tr><td colspan="5" class="muted" style="text-align:center;padding:16px;">Nothing needs review.</td></tr>';

    const olderNote = ma.older_count
      ? ' <span title="Older than the 45-day panel window — still actionable and overridable, just not listed here.">+' +
        ma.older_count + ' older, not shown</span>'
      : "";
    body.innerHTML =
      '<header class="modal-header">' +
      '<h2>Manual Actuals <small class="muted">(' + ma.count + ' needing review' + olderNote + ')</small></h2>' +
      '<div class="muted modal-subhead">MISSING: scheduled, past due, no usable print (nothing yet, or a 0.0 placeholder where 0.0 cannot be the value) — enter the actual. ' +
      'ZERO_CONFIRM: a 0.0 print with no independent evidence, on a series where 0.0 can be real — confirm it as a real flat print, or correct it. ' +
      'One row, one decision; a submit starts a refresh (~3 min) but only reload of this page will confirm it landed.</div>' +
      '</header>' +
      '<div class="econ-ind-scroll"><table class="econ-ind-table">' +
      '<thead><tr><th>Currency</th><th style="text-align:left">Indicator</th><th>UTC</th><th>Forecast</th><th style="text-align:left">Action</th></tr></thead>' +
      '<tbody>' + rowsHtml + '</tbody></table></div>';

    modal.hidden = false;
    document.body.classList.add("modal-open");
  }

  // ---- Manual Actuals Panel: submit -------------------------------------------
  function manualToken() {
    let t = null;
    try { t = localStorage.getItem("manualActualsToken"); } catch (_) {}
    if (!t) {
      t = window.prompt("Manual Actuals token (set once, stored in this browser):");
      if (t) { try { localStorage.setItem("manualActualsToken", t); } catch (_) {} }
    }
    return t;
  }

  function manualUser() {
    let u = null;
    try { u = localStorage.getItem("manualActualsUser"); } catch (_) {}
    if (!u) {
      u = window.prompt("Your name (kept with each entry for the audit trail):");
      if (u) { try { localStorage.setItem("manualActualsUser", u); } catch (_) {} }
    }
    return u || "unknown";
  }

  function manualActualsDecrementCount() {
    const ma = state.payload.manual_actuals;
    if (ma && ma.count > 0) ma.count -= 1;
    renderManualActualsButton();
    const small = document.querySelector("#econDetailContent .modal-header h2 small");
    if (small && ma) {
      const olderNote = ma.older_count ? " +" + ma.older_count + " older, not shown" : "";
      small.textContent = "(" + ma.count + " needing review" + olderNote + ")";
    }
  }

  function submitManualActual(tr, actual) {
    const token = manualToken();
    if (!token) return;
    const form = tr.querySelector(".ma-form");
    const noteEl = tr.querySelector(".ma-note");
    const btns = tr.querySelectorAll(".ma-btn");
    btns.forEach(function (b) { b.disabled = true; });

    const body = {
      canonical_id: tr.dataset.canonicalId, currency: tr.dataset.currency,
      indicator_key: tr.dataset.indicatorKey, datetime_utc: tr.dataset.datetimeUtc,
      actual: actual, state_resolved: tr.dataset.state,
      entered_by: manualUser(), note: noteEl ? noteEl.value : "",
    };

    fetch("/api/manual-actual", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Manual-Token": token },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, status: r.status, data: data }; }); })
      .then(function (res) {
        if (!res.ok) {
          if (res.status === 401) { try { localStorage.removeItem("manualActualsToken"); } catch (_) {} }
          form.insertAdjacentHTML("beforeend", '<div class="ma-error">' + escAttr(res.data.error || ("HTTP " + res.status)) + "</div>");
          btns.forEach(function (b) { b.disabled = false; });
          return;
        }
        tr.classList.add("ma-done");
        // Never show "Saved" here: the commit landed, but THIS payload is
        // unchanged until a refresh render actually happens. Reflect the
        // best-effort dispatch outcome (res.data.refresh_triggered) instead
        // of assuming success — the row stays marked until a page reload
        // pulls a payload that genuinely no longer contains it.
        form.outerHTML = res.data.refresh_triggered
          ? '<span class="ma-applying">⏳ Applying — refresh running, ~3 min. Reload the page after that to confirm.</span>'
          : '<span class="ma-applying ma-applying-manual">✓ Committed — auto-refresh didn\'t start; applies on the next hourly run. Reload later to confirm.</span>';
        if (noteEl) noteEl.remove();
        manualActualsDecrementCount();
      })
      .catch(function () {
        form.insertAdjacentHTML("beforeend", '<div class="ma-error">Network error — try again.</div>');
        btns.forEach(function (b) { b.disabled = false; });
      });
  }

  function wireManualActualsForms() {
    const body = document.getElementById("econDetailContent");
    body.addEventListener("click", function (e) {
      const tr = e.target.closest("tr[data-canonical-id]");
      if (!tr) return;
      if (e.target.classList.contains("ma-confirm")) {
        submitManualActual(tr, 0.0);
      } else if (e.target.classList.contains("ma-submit")) {
        const input = tr.querySelector(tr.dataset.state === "MISSING" ? ".ma-input:not(.ma-note)" : ".ma-correct");
        const val = input ? parseFloat(input.value) : NaN;
        if (!input || Number.isNaN(val)) {
          if (input) input.focus();
          return;
        }
        submitManualActual(tr, val);
      }
    });
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
  // TREND kill switch (config/pipeline.yaml, mirrored into payload.meta by
  // src.economic_render). FAIL-CLOSED by design: only an explicit `true`
  // renders TREND — a missing key, undefined, null, or any other value all
  // mean "no TREND" (matches a stale/older payload that predates this key
  // existing at all, not just a live `false`). False → zero TREND traces in
  // the DOM: no group header, no REGIME+MOM sub-header, no data-sort="trend",
  // no cell (FX or cross-asset), no modal section (including its "no data"
  // branch), no "+ TREND" in the Σ tooltip. See docs/accepted-degradations.md.
  function trendEnabled() {
    return !!(state.payload.meta) && state.payload.meta.trend_enabled === true;
  }
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
  // Per-currency label — some indicator_keys are fed by a print whose real
  // unit/series differs from the generic key name (e.g. CAD's cpi_yoy is
  // actually "CPI m/m"; CAD's core_cpi is actually BoC's "Median CPI y/y",
  // a different series entirely — see src/economic_render.py
  // INDICATOR_LABEL_OVERRIDES for how each override was verified against
  // the raw calendar data). Falls back to the global label when a
  // currency has no override, which is the common case.
  function indLabelForCcy(key, ccy) {
    const overrides = state.payload.meta.indicator_label_overrides || {};
    const forCcy = overrides[ccy];
    if (forCcy && forCcy[key]) return forCcy[key];
    return indMeta(key).label || key;
  }

  // ---- Sorting ------------------------------------------------------------
  function rowSortValue(inst, key) {
    if (key === "symbol") return inst.display || inst.symbol;
    if (key === "bias" || key === "score") return inst.score;          // precise float
    if (key === "contrib_sum") {
      return (inst.contrib_sum !== null && inst.contrib_sum !== undefined) ? inst.contrib_sum : -Infinity;
    }
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
  // Every cell below shows ONLY the RAW per-indicator score, exactly as on
  // main — box size, padding, line count all unchanged. The backend-computed
  // contribution to Score is surfaced EXCLUSIVELY via `title` (hover) so row
  // height stays pixel-identical; see findContribution()/contribTip().
  // Discreet marker for a pair cell subtracting two legs whose underlying
  // print uses a different transform (e.g. CAD's CPI m/m minus USD's CPI
  // y/y) — c.transform_mismatch/c.transform_tip come straight from the
  // backend (src.economic_render._build_indicator_cells), derived from the
  // same verified per-currency data behind INDICATOR_LABEL_OVERRIDES, never
  // guessed here. Both scores are still real z-scores (comparable as
  // dimensionless surprise), so this is a caveat, not an error state — no
  // color/background change, just the asterisk + tooltip.
  function transformMismatchMark(c) {
    if (!c.transform_mismatch) return "";
    return ' <span class="econ-xf-mismatch" title="' + escAttr(c.transform_tip || "") + '">*</span>';
  }

  // Cross-link to the Central Banks module: the RATE EXP (2Y) cell of a pair row opens the pair page, the one of the
  // single-currency (DXY) row opens the USD bank page. A separate link (stopPropagation in renderTable), so the row click
  // that opens the breakdown modal is untouched.
  function cbHref(inst) {
    if (inst.type === "fx") return "/central-banks/pair/" + String(inst.symbol).toLowerCase();
    const ccy = inst.breakdown && inst.breakdown.base && inst.breakdown.base.currency;
    return ccy ? "/central-banks/" + String(ccy).toLowerCase() : null;
  }
  function cbLink(inst, key, inner) {
    if (key !== "rate_expectations") return inner;
    const href = cbHref(inst);
    if (!href) return inner;
    return '<a class="cb-xlink" href="' + escAttr(href) + '" title="Open the central-bank rate outlook">' + inner + "</a>";
  }

  function indicatorCellHtml(inst, key) {
    const c = (inst.indicator_cells || {})[key] || { v: null, stale: false };
    const v = c.v;
    if (v === null || v === undefined) {
      // A pair whose RATE EXP (2Y) value is n/a (NZDCHF: no 2Y yield on one leg) still has its central-bank page: the link stays on the dash.
      return '<td class="econ-cell cell-na" title="not available for this instrument">' + cbLink(inst, key, "—") + "</td>";
    }
    const tip = contribTip(findContribution(inst, key));
    const mark = transformMismatchMark(c);
    if (c.stale) {
      const staleTip = "stale — latest release is outside the lookback window; excluded from scoring" +
        (tip ? " " + tip : "");
      return '<td class="econ-cell ec-stale" title="' + escAttr(staleTip) + '">' + cbLink(inst, key, fmtScoreCell(v)) + mark + '</td>';
    }
    // Continuous gradient on the per-indicator differential (saturates at ±4).
    return '<td class="econ-cell"' + styleAttr(gradientStyle(v, 4)) +
      (tip ? ' title="' + escAttr(tip) + '"' : "") + ">" + cbLink(inst, key, fmtScoreCell(v)) + mark + "</td>";
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
    const contrib = findContribution(inst, "trend");
    const tip = "TREND · MA structure (SMA20/50/200) × ADX strength → cell " +
      fmtScoreCell(v) + " (weight 0.5 in the Score). " + contribTip(contrib);
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
    const contrib = findContribution(inst, "sentiment");
    function legTxt(ccy, cell, det) {
      let t = ccy + " " + fmtScoreCell(cell === null || cell === undefined ? 0 : cell);
      if (det) t += " (lvl " + fmtScoreCell(det.level) + ", flow " + fmtScoreCell(det.flow) + ")";
      return t;
    }
    const baseTxt = legTxt(cot.base, cot.base_cell, cot.base_detail);
    const tip = "COT positioning · " + (cot.quote
      ? "base " + baseTxt + " − quote " + legTxt(cot.quote, cot.quote_cell, cot.quote_detail) +
        " = cell " + fmtScoreCell(v)
      : baseTxt + " = cell " + fmtScoreCell(v)) + ". " + contribTip(contrib);
    return '<td class="econ-cell"' + styleAttr(gradientStyle(v, 4)) +
      ' title="' + escAttr(tip) + '">' + fmtScoreCell(v) + "</td>";
  }

  // Σ column: the backend-computed sum of every displayed contribution
  // (FUND + SENTIMENT + TREND), which by construction reconstructs Score
  // exactly (see compute_instrument's `contrib_sum`/`contrib_residual`). JS
  // does NOT sum the cells itself — it only compares two numbers the backend
  // already computed, so a summing bug here can't hide by "agreeing with itself".
  const CONTRIB_SUM_TOLERANCE = 0.15;
  function contribSumCellHtml(inst) {
    const sum = inst.contrib_sum;
    if (sum === null || sum === undefined || Number.isNaN(sum)) {
      return '<td class="contrib-sum-cell cell-na"></td>';
    }
    const mismatch = Math.abs(sum - inst.score) > CONTRIB_SUM_TOLERANCE;
    const cls = "contrib-sum-cell" + (mismatch ? " contrib-mismatch" : "");
    const parts = trendEnabled() ? "FUND + SENTIMENT + TREND" : "FUND + SENTIMENT";
    const tip = mismatch
      ? "Σ (" + fmtSigned(sum, 2) + ") differs from Score (" + fmtSigned(inst.score, 2) + ") by more than " +
        CONTRIB_SUM_TOLERANCE + " — a displayed contribution is missing or wrong"
      : "Σ of every displayed contribution (" + parts + ") — reconstructs Score";
    return '<td class="' + cls + '" title="' + escAttr(tip) + '">' + fmtSigned(sum, 2) +
      (mismatch ? ' <span class="econ-flag flag-stale" title="' + escAttr(tip) + '">⚠</span>' : "") + "</td>";
  }

  // §M D1=D: FX pairs score on the INTERSECTION of both legs' present
  // categories (categories_used/categories_total on the instrument). When
  // the intersection is reduced, flag it with the exact count — never an
  // adjective. Lives on the SYMBOL cell (text-align:left), not Score:
  // Score/Bias are right/center-aligned + shrink-to-fit (width:1%), so a
  // trailing badge there shifts the digit's own position relative to
  // rows without a badge (the whole right/center-aligned unit moves as
  // the badge's width changes column-to-column) — a real misalignment
  // bug, not cosmetic. Symbol is left-aligned: trailing content never
  // moves the pair name's start position, so appending it here cannot
  // misalign anything, on any row, by construction — no browser needed
  // to confirm this one (M-IMPL-4).
  function categoriesFlagHtml(inst) {
    const used = inst.categories_used, total = inst.categories_total;
    if (used === null || used === undefined || total === null || total === undefined) return "";
    if (used >= total) return "";
    const excluded = inst.categories_excluded || [];
    const tip = "Scored on " + used + "/" + total + " categories (intersection of both legs)" +
      (excluded.length ? " — " + excluded.map(catLabel).join(", ") + " excluded from this pair" : "");
    return ' <span class="econ-flag flag-reduced" title="' + escAttr(tip) + '">' + used + "/" + total + "</span>";
  }

  // N (coverage) per scored category (growth/inflation/labour — NOT monetary,
  // which comes from the rate-expectations engine, not the calendar) for BOTH
  // legs of an FX row. Surfaced ONLY as a tooltip on the existing symbol cell
  // (zero new visible pixels, discreet per spec) — the dense table's 4 fixed
  // columns per category never explain how many indicators actually fed
  // score_precise (could be more than 4 since the bucket-C additions); this
  // makes that count checkable inline, consistent with the drill-down's own
  // "· n{coverage}" format (see legHtml). A single-type instrument (no quote
  // leg) shows just the one currency.
  function categoriesNTooltip(inst) {
    const base = inst.breakdown && inst.breakdown.base;
    if (!base) return "";
    const quote = inst.breakdown && inst.breakdown.quote;
    const baseCard = (state.payload.currencies || {})[base.currency];
    const quoteCard = quote ? (state.payload.currencies || {})[quote.currency] : null;
    const nOf = (card, cat) => (card && card.categories && card.categories[cat]
      ? card.categories[cat].coverage : 0);
    const lines = ["growth", "inflation", "labour"].map(cat => {
      const label = catLabel(cat);
      const baseN = base.currency + " N" + nOf(baseCard, cat);
      if (quote) {
        return label + ": " + baseN + " · " + quote.currency + " N" + nOf(quoteCard, cat);
      }
      return label + ": " + baseN;
    });
    return lines.join("\n");
  }

  function renderRow(inst) {
    const cells = columnKeys().map(k => indicatorCellHtml(inst, k)).join("");
    // Symbol / Bias / Score share one continuous gradient driven by the precise
    // score (saturates near ±6) → a smooth top-to-bottom column gradient.
    const sg = styleAttr(gradientStyle(inst.score, 6));
    return (
      '<tr data-symbol="' + escAttr(inst.symbol) + '">' +
      '<td class="sym"' + sg + ' title="' + escAttr(categoriesNTooltip(inst)) + '">' +
      (inst.display || inst.symbol) + categoriesFlagHtml(inst) + "</td>" +
      '<td class="bias-cell"' + sg + ">" + inst.bias + "</td>" +
      '<td class="score-cell"' + sg + ">" + fmtScoreInt(inst.score) + "</td>" +
      contribSumCellHtml(inst) +
      (trendEnabled() ? trendCellHtml(inst) : "") +
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
    // Absent entirely (headers + cells) when trend_enabled is false.
    const trendGroupHeader = trendEnabled()
      ? '<th colspan="1" class="grp-head grp-trend" title="Price TREND: SMA20/50/200 structure × ADX(14) strength, ±3, direct on the pair. Blue = bullish, red = bearish. Weighted 0.5 in the Score.">TREND</th>'
      : "";
    const trendSubHeader = trendEnabled()
      ? '<th data-sort="trend" class="ind-head grp-trend" title="Price trend, −3…+3 (weight 0.5 in score) = Regime (SMA50/200 structure, −2…+2) + Momentum (ATR-normalized SMA50 slope, −1…+1). ADX is display-only trend quality, not in the score.">REGIME+MOM</th>'
      : "";

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

    const sumTip = trendEnabled()
      ? "Sum of every displayed contribution (FUND + SENTIMENT + TREND) — reconstructs Score exactly; a mismatch beyond 0.15 is flagged, never silent"
      : "Sum of every displayed contribution (FUND + SENTIMENT) — reconstructs Score exactly; a mismatch beyond 0.15 is flagged, never silent";

    wrap.innerHTML =
      '<div class="retail-table-scroll econ-scroll">' +
      '<table class="retail-table econ-table">' +
      '<thead>' +
      '<tr>' +
      '<th rowspan="2" data-sort="symbol" class="col-sym">Symbol</th>' +
      '<th rowspan="2" data-sort="bias">Bias</th>' +
      '<th rowspan="2" data-sort="score">Score</th>' +
      '<th rowspan="2" data-sort="contrib_sum" title="' + escAttr(sumTip) + '">Σ</th>' +
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

    wrap.querySelectorAll("a.cb-xlink").forEach(a => {
      a.addEventListener("click", ev => ev.stopPropagation());
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
    if (flag === "direction_mismatch") return '<span class="econ-flag flag-nc" title="Polarity guard failed — observed raw event name doesn\'t match what direction_override_expect requires; excluded from scoring pending review">direction guard</span>';
    return '<span class="econ-flag">' + flag + '</span>';
  }

  function indicatorRow(key, e, ccy) {
    const meta = indMeta(key);
    const label = ccy ? indLabelForCcy(key, ccy) : (meta.label || key);
    // Effective direction: per-currency override (state.payload.meta.
    // indicator_direction_overrides, sparse) wins, else the global indMeta one.
    const dirOverrides = ccy && (state.payload.meta.indicator_direction_overrides || {})[ccy];
    const effDirection = (dirOverrides && key in dirOverrides) ? dirOverrides[key] : meta.direction;
    const inverted = effDirection === -1
      ? ' <span class="econ-inv" title="Inverted: a higher actual is bearish for this currency">⤵</span>'
      : '';
    const zTxt = (e.z === null || e.z === undefined) ? "—" : fmtSigned(e.z, 2);
    const dateTxt = fmtDate(e.release_dt, e.date_only) +
      (e.age_days !== null && e.age_days !== undefined ? ' <span class="muted">(' + e.age_days + 'd)</span>' : '');
    const staleBadge = e.stale ? ' <span class="econ-flag flag-stale" title="Latest release is older than max_age_days — shown for visibility but excluded from the category average / index">stale</span>' : '';
    return (
      '<tr' + (e.stale ? ' class="ei-stale"' : '') + '>' +
      '<td class="ei-name">' + label + inverted + '</td>' +
      '<td class="ei-num">' + fmtPolicy(e.actual, e) + '</td>' +
      '<td class="ei-num">' + fmtPolicy(e.consensus, e) + '</td>' +
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

  function legHtml(role, currency, excludedCategories, pairSpread) {
    if (!currency) return "";
    const excluded = excludedCategories || [];
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
      // §M D1=D: this category is present for this currency but was excluded
      // from THIS pair's intersection (the other leg lacks it) — the raw
      // score above is real and included normally on other pairs; only its
      // contribution to THIS pair is zero. Distinct from stale/no-consensus:
      // this is a property of the comparison, not of the data.
      if (excluded.indexOf(catKey) !== -1) {
        const tip = "Excluded from this pair's score — the other leg has no data for this category. " +
          "Included normally on pairs where both legs have it.";
        subHtml += ' <span class="econ-flag flag-excluded" title="' + escAttr(tip) + '">excluded here</span>';
      }
      // Audit 5B: in a pair the monetary factor is the pair's 2Y spread (one
      // pair row above); the leg's own 2Y is shown for information only.
      if (catKey === "monetary" && pairSpread) {
        subHtml += ' <span class="econ-flag flag-excluded" title="' +
          escAttr("This pair scores monetary on the 2Y spread (the Monetary section above). " +
                  "The currency's own 2Y repricing is shown for information.") +
          '">info — pair uses 2Y spread</span>';
      }
      let table;
      if (catKey === "monetary") {
        table =
          '<thead><tr><th>Indicator</th><th>Latest 2y</th><th>Δ2y(1m)</th><th>z</th><th>Score</th><th>Method</th><th>As-of</th></tr></thead>' +
          '<tbody>' + keys.map(k => rateExpRow(breakdown[k])).join("") + '</tbody>';
      } else {
        table =
          '<thead><tr><th>Indicator</th><th>Act</th><th>Cons</th><th>Surp</th><th>z</th><th>Score</th><th>Method</th><th>Release</th></tr></thead>' +
          '<tbody>' + keys.map(k => indicatorRow(k, breakdown[k], currency)).join("") + '</tbody>';
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
  // inst.trend_detail {bull_points,regime,slope_atr,momentum,adx,trend_cell} (v2).
  // None / all-null → "no data". Placed FIRST (matches the TREND-first column).
  // ADX is DISPLAY-ONLY "trend quality" — it does NOT affect the score (regime+momentum).
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
      ' <span class="muted">· weight 0.5 · Regime + Momentum</span></div>';
    const row = (name, v, c) => '<tr><td class="ei-name">' + name + '</td><td class="ei-num ' +
      (c || "") + '">' + v + '</td></tr>';
    const slopeAtr = (d.slope_atr === null || d.slope_atr === undefined)
      ? "—" : Number(d.slope_atr).toFixed(3);
    const adxTxt = (d.adx === null || d.adx === undefined) ? "—" : Number(d.adx).toFixed(1);
    const rows =
      row("Regime (SMA50/200 structure, " + d.bull_points + "/3 bull)",
          fmtScoreCell(d.regime), cellClass(d.regime)) +
      row("Momentum (SMA50 slope ÷ ATR = " + slopeAtr + ")",
          fmtScoreCell(d.momentum), cellClass(d.momentum)) +
      row("<strong>Trend cell = clamp(regime + momentum)</strong>",
          "<strong>" + fmtScoreCell(d.trend_cell) + "</strong>", cls) +
      row('ADX(14) <span class="muted">· trend quality, display-only</span>', adxTxt, "");
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

  // Audit 5B: the pair's monetary factor — ONE pair row on the 2Y spread.
  function fxMonetarySectionHtml(inst, base, quote) {
    const mp = inst.monetary_pair;
    if (!mp) return "";
    const cls = cellClass(mp.m);
    const num = (v, d, unit) => (v === null || v === undefined) ? "—" : fmtSigned(v, d) + (unit || "");
    const head = '<div class="econ-cat-group"><div class="econ-cat-head">Monetary (2Y spread) ' +
      '<span class="econ-cat-sub ' + cls + '">' + fmtScoreCell(mp.m) + '</span>' +
      ' <span class="muted">· m replaces the legs\u2019 own 2Y difference · 5-obs averaged ends, z vs 252 changes</span></div>';
    const row =
      '<tr><td class="ei-name">2Y ' + base + ' − 2Y ' + quote + '</td>' +
      '<td class="ei-num">' + num(mp.spread, 3, "pp") + '</td>' +
      '<td class="ei-num">' + num(mp.delta, 3, "pp") + '</td>' +
      '<td class="ei-num">' + num(mp.z, 2) + '</td>' +
      '<td class="ei-score ' + cls + '">' + fmtScoreCell(mp.m) + '</td>' +
      '<td class="ei-date">' + (mp.as_of ? fmtDate(mp.as_of) : "—") + '</td></tr>';
    return head + '<div class="econ-ind-scroll"><table class="econ-ind-table">' +
      '<thead><tr><th>Spread</th><th>Level</th><th>Δ(1m)</th><th>z</th><th>m</th><th>As-of</th></tr></thead>' +
      '<tbody>' + row + '</tbody></table></div></div>';
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
      ? legHtml("Base", base, inst.categories_excluded, !!inst.monetary_pair) +
        legHtml("Quote", quote, inst.categories_excluded, !!inst.monetary_pair)
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
      // TREND first (matches column order), then Sentiment (COT), then the macro legs.
      (trendEnabled() ? trendSectionHtml(inst.trend_detail) : "") +
      fxCotSectionHtml(inst.cot) +
      (isFx ? fxMonetarySectionHtml(inst, base, quote) : "") +
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

  // N (coverage) per scored category for a cross-asset instrument's SINGLE
  // home currency — simpler than the FX version (categoriesNTooltip), no
  // base/quote pair to reconcile. Same scope (growth/inflation/labour only).
  function caCategoriesNTooltip(inst) {
    const home = inst.home_ccy;
    if (!home) return "";
    const card = (state.payload.currencies || {})[home];
    const nOf = cat => (card && card.categories && card.categories[cat]
      ? card.categories[cat].coverage : 0);
    return ["growth", "inflation", "labour"]
      .map(cat => catLabel(cat) + ": " + home + " N" + nOf(cat))
      .join("\n");
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
    if (c.excluded) {
      const tip = "Weight set to 0 — excluded from the composite (accepted degradation, see docs/accepted-degradations.md). Value shown is still live.";
      return '<td class="econ-cell ec-excluded" title="' + escAttr(tip) + '">' +
        fmtScoreCell(v) + ' <span class="econ-flag flag-excluded">excluded from composite</span></td>';
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
      // foreign indices (DAX/Nikkei/FTSE) take the US P/C as a global-risk PROXY —
      // marked with a trailing * and an explicit tooltip so it's not read as native.
      const proxy = pc.proxy === true;
      const tip = (proxy ? "US equity P/C — global risk proxy · " : "P/C equity contrarian · ") +
        "percentile " + Number(pc.pct).toFixed(0) + " (1Y) → cell " + fmtScoreCell(v) + " · " + pc.basis;
      const mark = proxy ? '<sup class="pc-proxy">*</sup>' : "";
      return '<td class="econ-cell' + (proxy ? ' pc-proxy-cell' : '') + '"' + styleAttr(gradientStyle(v, 3)) +
        ' title="' + escAttr(tip) + '">' + fmtScoreCell(v) + mark + "</td>";
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
    // Absent entirely (headers + cells) when trend_enabled is false.
    const trendGroupHeader = trendEnabled()
      ? '<th colspan="1" class="grp-head grp-trend" title="Price TREND: SMA20/50/200 structure × ADX(14) strength, ±3, direct on the asset. Blue = bullish, red = bearish. Weighted 0.5 in the Score.">TREND</th>'
      : "";
    const trendSubHeader = trendEnabled()
      ? '<th class="ind-head grp-trend" title="Price trend, −3…+3 = Regime (SMA50/200) + Momentum (ATR-normalized SMA50 slope). ADX display-only.">REGIME+MOM</th>'
      : "";

    // SENTIMENT: top-level group placed after TREND, before the macro factor
    // groups. Contributes to Score with weight 0.5.
    const sentimentGroupHeader =
      '<th colspan="1" class="grp-head grp-sentiment" title="Positioning sentiment (COT for metals, P/C equity for US indices), contrarian. Foreign indices (DAX/Nikkei/FTSE, marked *) take the US equity P/C as a global-risk PROXY. Blue = bullish for the asset, red = bearish. Weighted 0.5 in the Score.">SENTIMENT</th>';
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
        '<td class="sym biasfill ' + bcls + '" title="' + escAttr(caCategoriesNTooltip(inst)) + '">' +
        (inst.display || inst.symbol) + '</td>' +
        '<td class="bias-cell biasfill ' + bcls + '">' + inst.bias_label + '</td>' +
        '<td class="score-cell biasfill ' + bcls + '">' + fmtScoreInt(inst.score_precise) + '</td>' +
        (trendEnabled() ? caTrendCellHtml(inst) : "") + caCotCellHtml(inst) + cells + '</tr>'
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
  // mean); a genuinely-missing one shows "absent"; a weight-0 accepted
  // degradation shows "excluded from composite" — the raw value is real and
  // live, only its weight was zeroed (see docs/accepted-degradations.md).
  function caRateRow(c) {
    const hasVal = c.raw !== null && c.raw !== undefined;
    const contrib = hasVal ? fmtSigned((Math.abs(c.contribution) < 0.05 ? 0 : c.contribution), 1) : "—";
    const cls = hasVal ? cellClass(Math.round(c.contribution)) : "";
    const label = ({rate_exp_2y: "Rate Expectations (2Y)", real_yield_10y: "10Y Real Yield",
                    balance_sheet: "Bank Reserves"})[c.name] || c.name;
    const flag = c.excluded
      ? '<span class="econ-flag flag-excluded" title="weight set to 0 — accepted degradation, see docs/accepted-degradations.md">excluded from composite</span>'
      : c.stale
      ? '<span class="econ-flag flag-stale" title="stale — shown for visibility, excluded from the rates mean">stale</span>'
      : (hasVal ? "" : '<span class="econ-flag flag-stale" title="not available">absent</span>');
    return (
      "<tr" + ((c.present && !c.excluded) ? "" : ' class="ei-stale"') + ">" +
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
    const dateTxt = fmtDate(e.release_dt, e.date_only) +
      (e.age_days !== null && e.age_days !== undefined ? ' <span class="muted">(' + e.age_days + 'd)</span>' : '');
    const staleBadge = e.stale ? ' <span class="econ-flag flag-stale" title="stale — excluded from the category average">stale</span>' : '';
    const signed = (e.score === null || e.score === undefined) ? null : sign * e.score;
    const scoreTxt = (signed === null) ? "—" : fmtScoreCell(signed);
    const scoreCls = (signed === null) ? "" : cellClass(signed);
    // Row is excluded from the category average for three reasons
    // (compute_currency_scorecard): stale, no_consensus, or direction_mismatch
    // (the direction_override_expect polarity guard tripped). All three get
    // the same dimmed-row treatment so exclusion is visible at a glance, not
    // just via the small inline flag badge — distinct classes so the
    // reason is still inspectable (title text differs per badge above).
    const rowCls = e.stale ? ' class="ei-stale"'
      : ((e.flag === "no_consensus" || e.flag === "direction_mismatch") ? ' class="ei-no-consensus"' : '');
    return (
      '<tr' + rowCls + '>' +
      '<td class="ei-name">' + (meta.label || key) + '</td>' +
      '<td class="ei-num">' + fmtPolicy(e.actual, e) + '</td>' +
      '<td class="ei-num">' + fmtPolicy(e.consensus, e) + '</td>' +
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
    // N = how many of this category's indicators actually entered the mean
    // (compute_currency_scorecard's per-cat coverage — same number already
    // used for the row-hover tooltip via caCategoriesNTooltip). Shown next
    // to contrib so the average is checkable by eye against the rows below;
    // "rates" has no equivalent per-indicator coverage concept (it's a
    // sub-component composite, not a category average) so it's omitted there.
    const catMeta = ((state.payload.currencies || {})[home] || {}).categories || {};
    const n = (catMeta[group.key] && catMeta[group.key].coverage != null)
      ? catMeta[group.key].coverage : null;
    const nTxt = (group.key === "rates" || n === null) ? "" : ' <span class="muted">· N ' + n + '</span>';
    const head =
      '<div class="econ-cat-head">' + group.label +
      ' <span class="econ-cat-sub ' + cls + '">contrib ' + contrib + "</span>" + nTxt + signTxt + "</div>";

    let table, note = "";
    if (group.key === "rates") {
      const comps = (f && f.components) || [];
      table =
        '<thead><tr><th>Sub-component</th><th>Raw</th><th>Sign</th><th>Weight</th><th>Contribution</th><th></th><th>Source</th></tr></thead>' +
        '<tbody>' + comps.map(caRateRow).join("") + "</tbody>";
      const nl = (state.payload.crossasset || {}).net_liquidity || {};
      // series reflects what's ACTUALLY scored: reserves (WRBWFRBL) by default,
      // or the net_liquidity (WALCL − TGA − RRP) fallback when reserves is
      // unresolved — never label one while showing the other's numbers. The
      // visible label is plain "Bank Reserves"; the FRED series id is a
      // tooltip-only detail, shown alongside the roc/level line below.
      const bits = [nl.series === "NET_LIQUIDITY"
        ? "Net Liquidity (fallback) = WALCL − TGA − RRP"
        : "Bank Reserves"];
      if (nl.present) {
        if (nl.series && nl.series !== "NET_LIQUIDITY") bits.push("series " + nl.series);
        bits.push("21d roc " + (nl.roc == null ? "—" : fmtSigned(nl.roc * 100, 2) + "%/mo"));
        if (nl.latest != null) bits.push("level " + Number(nl.latest).toFixed(0));
        if (nl.as_of) bits.push("@ " + fmtDate(nl.as_of) + (nl.stale ? " (stale)" : ""));
      }
      note = '<div class="muted" style="font-size:11px;margin-top:4px;">' + bits.join(" · ") + "</div>";
    } else {
      // Dynamic: EVERY indicator this currency has in `breakdown` for this
      // category, not just CROSSASSET_TABLE_LAYOUT's curated sub-columns
      // (those stay fixed — this only widens the modal, never the table).
      // Curated columns keep their existing order first (no visual churn for
      // the common case); anything else the category has is appended,
      // alphabetically by label, so it's still deterministic.
      const brk = ((state.payload.currencies || {})[home] || {}).breakdown || {};
      const layoutKeys = group.columns.map(c => c.key);
      const extraKeys = Object.keys(brk)
        .filter(k => brk[k] && brk[k].category === group.key && layoutKeys.indexOf(k) === -1)
        .sort((a, b) => (indMeta(a).label || a).localeCompare(indMeta(b).label || b));
      const keys = layoutKeys.filter(k => brk[k]).concat(extraKeys);
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
      detail = (c.proxy === true ? "US equity P/C — global risk proxy · percentile "
                                 : "P/C equity contrarian · percentile ") +
        Number(c.pct).toFixed(0) + " (1Y) → cell " + fmtScoreCell(c.cell);
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
    const sections = (trendEnabled() ? trendSectionHtml(inst.trend_detail) : "") +
      caSentimentSection(inst) +
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
    renderManualActualsButton();
    renderFilters();
    wireModalClose();
    wireManualActualsForms();
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

  // DOM-free seam for tests/economic_js (plain Node, no jsdom); nothing in the page reads it.
  if (typeof module !== "undefined" && module.exports) module.exports = { indicatorCellHtml: indicatorCellHtml, cbHref: cbHref };
})();
