/* Retail Sentiment page logic. Depends on Chart.js v4 UMD. */
(function () {
  "use strict";

  const DATA_URL = window.RETAIL_DATA_URL || "/data/retail-sentiment.json";

  // Ripeness gates use distinct calendar days (not raw snapshot count) so that
  // multiple intraday snapshots don't artificially mature a symbol.
  const MIN_DAYS_FOR_1M = 30;
  const MIN_DAYS_FOR_3M = 90;
  const MIN_DAYS_FOR_6M = 180;
  const MIN_DAYS_FOR_CHART = 30;

  // Tooltip copy — shown on every badge/pill via the title attribute.
  const TOOLTIPS = {
    longs_losing:
      "Average long entry price is above current spot — the long crowd is " +
      "collectively in a loss. If price drops further, expect cascading " +
      "stop-outs from leveraged longs (long capitulation pressure).",
    shorts_losing:
      "Average short entry price is below current spot — the short crowd is " +
      "collectively in a loss. If price rises further, expect cascading " +
      "stop-outs from leveraged shorts (potential short squeeze fuel).",
    cot:
      "Retail and CFTC large speculators are both crowded on the same side " +
      "at a multi-month extreme. Both groups statistically reverse from " +
      "these levels — high-conviction contrarian setup.",
    cot_badge:
      "Retail and CFTC large specs are both at extremes on the same side — " +
      "high-conviction contrarian confluence.",
    extreme:
      "Today's positioning is at a multi-month extreme — historically a " +
      "turning-point zone.",
    pill_bullish:
      "≥70% of retail traders are short. Retail is statistically wrong at " +
      "extremes — this is a contrarian BUY signal. Look for opportunities " +
      "to go long.",
    pill_bearish:
      "≥70% of retail traders are long. Retail is statistically wrong at " +
      "extremes — this is a contrarian SELL signal. Look for opportunities " +
      "to go short.",
    pill_neutral:
      "Crowd positioning is balanced — no contrarian edge from this signal " +
      "alone. Check other signals (underwater crowd, COT divergence, extremes).",
  };

  const state = {
    payload: null,
    catVisible: {},          // cat_key -> bool
    cotOverlay: false,
    sortKey: null,
    sortAsc: false,
    chart: null,
    chartWindow: "1M",
    showSpot: false,
    activeSymbol: null,
  };

  // ---- Theme helpers ------------------------------------------------------
  function isDarkTheme() { return document.body.classList.contains("dark"); }
  function themeColors() {
    const dark = isDarkTheme();
    return {
      primary: dark ? "#ffffff" : "#1a1a1a",
      accent: dark ? "#64b5f6" : "#1565c0",
      grid: dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
      tick: dark ? "#9aa0a6" : "#666",
      label: dark ? "#eee" : "#222",
      tooltipBg: dark ? "rgba(10,10,10,0.95)" : "rgba(255,255,255,0.95)",
      tooltipFg: dark ? "#eee" : "#222",
      tooltipBorder: dark ? "#333" : "#ccc",
    };
  }

  // ---- Number / formatting -----------------------------------------------
  function fmtPct(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    return Number(v).toFixed(0) + "%";
  }
  function fmtPct1(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    return Number(v).toFixed(1) + "%";
  }
  function fmtPrice(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const n = Math.abs(Number(v));
    if (n >= 1000) return Number(v).toFixed(0);
    if (n >= 50) return Number(v).toFixed(2);
    return Number(v).toFixed(4);
  }
  function fmtPips(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const sign = v > 0 ? "+" : "";
    return sign + Number(v).toFixed(0) + "p";
  }
  function fmtInt(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    return Number(v).toLocaleString();
  }
  function fmtFundsUSD(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const n = Number(v);
    if (n >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return "$" + (n / 1e3).toFixed(1) + "K";
    return "$" + n.toFixed(0);
  }
  function fmtRelativeUtc(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toISOString().replace("T", " ").substring(0, 16) + " UTC";
  }

  // ---- Provider meta-bar --------------------------------------------------
  function renderProviderMeta() {
    const p = state.payload.provider;
    const el = document.getElementById("providerMeta");
    const parts = [];
    parts.push("Source: " + (p.display || p.name || "—"));
    if (p.snapshot_count !== null && p.snapshot_count !== undefined) {
      parts.push(p.snapshot_count + " symbols");
    }
    if (p.real_account_pct !== null && p.real_account_pct !== undefined) {
      parts.push(fmtPct1(p.real_account_pct) + " real accounts");
    }
    if (p.profitable_pct !== null && p.profitable_pct !== undefined) {
      parts.push(fmtPct1(p.profitable_pct) + " profitable");
    }
    if (p.total_funds_usd !== null && p.total_funds_usd !== undefined) {
      parts.push(fmtFundsUSD(p.total_funds_usd) + " total funds");
    }
    if (p.last_fetched_at) {
      parts.push("Last updated " + fmtRelativeUtc(p.last_fetched_at));
    }
    el.textContent = parts.join(" · ");
  }

  // ---- Category filter chips ---------------------------------------------
  function renderCatChips() {
    const wrap = document.getElementById("retailCatChips");
    wrap.innerHTML = "";
    state.payload.categories.forEach(cat => {
      if (!(cat.key in state.catVisible)) state.catVisible[cat.key] = true;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cat-chip" + (state.catVisible[cat.key] ? " selected" : "");
      btn.dataset.cat = cat.key;
      btn.setAttribute("aria-pressed", state.catVisible[cat.key] ? "true" : "false");
      btn.innerHTML =
        '<svg class="chip-box" viewBox="0 0 16 16" aria-hidden="true">' +
        '<rect class="chip-box-rect" x="2" y="2" width="12" height="12" rx="2.5" ry="2.5" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
        '<path class="chip-box-check" d="M4.5 8.4l2.4 2.4L11.8 5.6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
        "</svg><span></span>";
      btn.querySelector("span").textContent = cat.label;
      btn.addEventListener("click", () => {
        state.catVisible[cat.key] = !state.catVisible[cat.key];
        renderTables();
        // sync chip selected state
        btn.classList.toggle("selected");
        btn.setAttribute("aria-pressed", state.catVisible[cat.key] ? "true" : "false");
      });
      wrap.appendChild(btn);
    });

    document.querySelectorAll("#catFilter .cat-action").forEach(b => {
      b.addEventListener("click", () => {
        const action = b.dataset.action;
        Object.keys(state.catVisible).forEach(k => {
          state.catVisible[k] = (action === "all");
        });
        document.querySelectorAll("#retailCatChips .cat-chip").forEach(chip => {
          const k = chip.dataset.cat;
          if (state.catVisible[k]) chip.classList.add("selected");
          else chip.classList.remove("selected");
          chip.setAttribute("aria-pressed", state.catVisible[k] ? "true" : "false");
        });
        renderTables();
      });
    });
  }

  // ---- Sparkline (inline SVG) --------------------------------------------
  function sparkline(values) {
    const W = 60, H = 18;
    const v = values.filter(x => x !== null && !Number.isNaN(x));
    if (v.length < 2) return "";
    const min = Math.min.apply(null, v), max = Math.max.apply(null, v);
    const range = (max - min) || 1;
    const xs = v.map((_, i) => i * (W - 1) / (v.length - 1));
    const ys = v.map(val => H - 2 - ((val - min) / range) * (H - 4));
    const pts = xs.map((x, i) => x.toFixed(2) + "," + ys[i].toFixed(2)).join(" ");
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" class="retail-spark">' +
      '<polyline fill="none" stroke="currentColor" stroke-width="1.2" points="' + pts + '"/>' +
      "</svg>";
  }

  // ---- Row + table rendering ---------------------------------------------
  function rowSortValue(sym, key) {
    const cur = sym.current || {};
    const sig = sym.signals || {};
    const ext3 = (sig.extremes || {}).ext_3m || {};
    const ext6 = (sig.extremes || {}).ext_6m || {};
    switch (key) {
      case "symbol": return sym.display || sym.symbol;
      case "long_pct": return cur.long_pct === null || cur.long_pct === undefined ? -1 : cur.long_pct;
      case "spot": return cur.spot_estimate === null || cur.spot_estimate === undefined ? -Infinity : cur.spot_estimate;
      case "ext_3m": return ext3.value === null || ext3.value === undefined ? -1 : ext3.value;
      case "ext_6m": return ext6.value === null || ext6.value === undefined ? -1 : ext6.value;
      case "alerts": return sym.alert_count || 0;
      default: return 0;
    }
  }

  function compareSymbols(a, b) {
    const key = state.sortKey;
    if (!key) {
      // default: alert_count desc, ext_6m desc
      const ac = (b.alert_count || 0) - (a.alert_count || 0);
      if (ac !== 0) return ac;
      const e6a = ((a.signals.extremes || {}).ext_6m || {}).value;
      const e6b = ((b.signals.extremes || {}).ext_6m || {}).value;
      return (e6b == null ? -1 : e6b) - (e6a == null ? -1 : e6a);
    }
    const av = rowSortValue(a, key);
    const bv = rowSortValue(b, key);
    let cmp;
    if (typeof av === "number" && typeof bv === "number") cmp = av - bv;
    else cmp = String(av).localeCompare(String(bv));
    return state.sortAsc ? cmp : -cmp;
  }

  function escAttr(s) { return String(s).replace(/"/g, "&quot;"); }

  function detailsButton(sym) {
    const n = sym.alert_count || 0;
    const tip = (n === 0)
      ? "No signals firing"
      : (n + " signal" + (n === 1 ? "" : "s") + " firing");
    return (
      '<button type="button" class="details-btn" title="' + escAttr(tip) + '">' +
      'Details &rsaquo;</button>'
    );
  }

  function symbolPointCount(sym) {
    // Use the longest available history window for the count.
    const all = (sym.history || {}).All || {};
    const dates = all.dates || [];
    return dates.length;
  }

  function distinctDaysInHistory(historyArray) {
    // Returns the number of distinct UTC calendar dates in a list of ISO
    // timestamps (or {ts}/{date} objects). Multiple intraday snapshots count
    // as one day.
    const days = new Set();
    if (!historyArray) return 0;
    for (const point of historyArray) {
      let iso;
      if (typeof point === "string") iso = point;
      else if (point && typeof point === "object") iso = point.ts || point.date;
      if (!iso) continue;
      const d = new Date(iso);
      if (isNaN(d.getTime())) continue;
      days.add(d.toISOString().slice(0, 10));
    }
    return days.size;
  }

  function symbolDistinctDays(sym) {
    const all = (sym.history || {}).All || {};
    return distinctDaysInHistory(all.dates || []);
  }

  function pageHasAnyDaysAtLeast(minDays) {
    if (!state.payload) return false;
    for (const cat of state.payload.categories) {
      for (const s of cat.symbols) {
        if (symbolDistinctDays(s) >= minDays) return true;
      }
    }
    return false;
  }

  function pageHasAny3M() { return pageHasAnyDaysAtLeast(MIN_DAYS_FOR_3M); }
  function pageHasAny6M() { return pageHasAnyDaysAtLeast(MIN_DAYS_FOR_6M); }
  function pageHasAnySparklines() { return pageHasAnyDaysAtLeast(MIN_DAYS_FOR_1M); }

  function renderRow(sym, opts) {
    const show3MCol = !!(opts && opts.show3MCol);
    const show6MCol = !!(opts && opts.show6MCol);
    const showSparkCol = !!(opts && opts.showSparkCol);
    const cur = sym.current || {};
    const sig = sym.signals || {};
    const ext3 = (sig.extremes || {}).ext_3m || {};
    const ext6 = (sig.extremes || {}).ext_6m || {};
    const u = sig.capitulation || sig.underwater || {};
    const nDays = symbolDistinctDays(sym);

    const longPct = cur.long_pct;
    const shortPct = cur.short_pct;

    const splitBar =
      '<div class="ls-bar" title="Long ' + fmtPct(longPct) + ' · Short ' + fmtPct(shortPct) + '">' +
      '<div class="ls-bar-long" style="width:' + (longPct == null ? 0 : longPct) + '%"></div>' +
      '<div class="ls-bar-short" style="width:' + (shortPct == null ? 0 : shortPct) + '%"></div>' +
      "</div>" +
      '<div class="ls-bar-labels"><span class="ls-long">' + fmtPct(longPct) + ' L</span>' +
      '<span class="ls-short">' + fmtPct(shortPct) + ' S</span></div>';

    const longPnlClass = (u.long_pnl_pips_estimate || 0) >= 0 ? "pnl-pos" : "pnl-neg";
    const shortPnlClass = (u.short_pnl_pips_estimate || 0) >= 0 ? "pnl-pos" : "pnl-neg";

    const priceCell =
      '<div class="price-stack">' +
      '<div><span class="lbl">Spot</span> <span>' + fmtPrice(cur.spot_estimate) + '</span></div>' +
      '<div><span class="lbl">Avg L</span> <span>' + fmtPrice(cur.avg_long_price) + '</span> <span class="pnl ' + longPnlClass + '">' + fmtPips(u.long_pnl_pips_estimate) + '</span></div>' +
      '<div><span class="lbl">Avg S</span> <span>' + fmtPrice(cur.avg_short_price) + '</span> <span class="pnl ' + shortPnlClass + '">' + fmtPips(u.short_pnl_pips_estimate) + '</span></div>' +
      "</div>";

    let pillCls = "pill-neutral", pillTxt = "NEUTRAL", pillTip = TOOLTIPS.pill_neutral;
    if (sig.contrarian === "bearish_contrarian") {
      pillCls = "pill-bearish"; pillTxt = "BEARISH (contrarian)"; pillTip = TOOLTIPS.pill_bearish;
    } else if (sig.contrarian === "bullish_contrarian") {
      pillCls = "pill-bullish"; pillTxt = "BULLISH (contrarian)"; pillTip = TOOLTIPS.pill_bullish;
    }

    // Per-cell ripeness: only show a numeric extreme if the symbol itself has
    // enough calendar days. Below that, "—" with a tooltip.
    const ext3Cell = (nDays < MIN_DAYS_FOR_3M || ext3.value === null || ext3.value === undefined)
      ? '<span class="ext-na" title="Need ' + MIN_DAYS_FOR_3M + ' days; have ' + nDays + '">—</span>'
      : '<span class="' + (ext3.class || "ext-na") + '">' + Math.round((ext3.value || 0) * 100) + '%</span>';
    const ext6Cell = (nDays < MIN_DAYS_FOR_6M || ext6.value === null || ext6.value === undefined)
      ? '<span class="ext-na" title="Need ' + MIN_DAYS_FOR_6M + ' days; have ' + nDays + '">—</span>'
      : '<span class="' + (ext6.class || "ext-na") + '">' + Math.round((ext6.value || 0) * 100) + '%</span>';

    const longPctSeries = ((sym.history || {})["1M"] || {}).long_pct || [];
    const sparkCellHtml = (nDays >= MIN_DAYS_FOR_1M) ? sparkline(longPctSeries) : "";

    const ext3Html = show3MCol ? '<td class="ext-cell">' + ext3Cell + '</td>' : '';
    const ext6Html = show6MCol ? '<td class="ext-cell">' + ext6Cell + '</td>' : '';
    const sparkCell = showSparkCol ? '<td class="spark-cell">' + sparkCellHtml + '</td>' : '';

    // COT confluence badge — only when the overlay toggle is ON and the
    // symbol's confluence signal is firing.
    const cc = sig.cot_confluence;
    const cotBadge = (state.cotOverlay && cc && cc.has_confluence)
      ? ' <span class="cot-badge" title="' + escAttr(TOOLTIPS.cot_badge) + '">⚡ COT</span>'
      : '';

    return (
      '<tr data-symbol="' + sym.symbol + '">' +
      '<td class="sym">' + sym.display + '</td>' +
      '<td class="ls-cell">' + splitBar + "</td>" +
      '<td class="price-cell">' + priceCell + "</td>" +
      '<td class="signal-cell"><span class="pill ' + pillCls + '" title="' + escAttr(pillTip) + '">' + pillTxt + "</span>" + cotBadge + "</td>" +
      ext3Html +
      ext6Html +
      '<td class="details-cell">' + detailsButton(sym) + "</td>" +
      sparkCell +
      "</tr>"
    );
  }

  function renderTables() {
    const wrap = document.getElementById("retailContent");
    wrap.innerHTML = "";

    const show3MCol = pageHasAny3M();
    const show6MCol = pageHasAny6M();
    const showSparkCol = pageHasAnySparklines();
    const ext3Header = show3MCol ? '<th data-sort="ext_3m">3M ext</th>' : '';
    const ext6Header = show6MCol ? '<th data-sort="ext_6m">6M ext</th>' : '';
    const sparkHeader = showSparkCol ? '<th>1M</th>' : '';

    state.payload.categories.forEach(cat => {
      if (!state.catVisible[cat.key]) return;
      const symbols = cat.symbols.slice().sort(compareSymbols);

      const section = document.createElement("section");
      section.className = "retail-cat";
      section.dataset.cat = cat.key;
      section.innerHTML =
        '<h2>' + cat.label + '</h2>' +
        '<div class="retail-table-scroll">' +
        '<table class="retail-table">' +
        '<thead><tr>' +
        '<th data-sort="symbol">Symbol</th>' +
        '<th data-sort="long_pct">Long / Short</th>' +
        '<th data-sort="spot">Price</th>' +
        '<th>Signal</th>' +
        ext3Header +
        ext6Header +
        '<th class="details-col" data-sort="alerts">Details</th>' +
        sparkHeader +
        '</tr></thead>' +
        '<tbody>' + symbols.map(s => renderRow(s, { show3MCol, show6MCol, showSparkCol })).join("") + '</tbody>' +
        '</table></div>';

      // Wire sort headers
      section.querySelectorAll("th[data-sort]").forEach(th => {
        th.style.cursor = "pointer";
        th.addEventListener("click", () => {
          const key = th.dataset.sort;
          if (state.sortKey === key) state.sortAsc = !state.sortAsc;
          else { state.sortKey = key; state.sortAsc = false; }
          renderTables();
        });
        if (state.sortKey === th.dataset.sort) {
          th.classList.add("sorted-" + (state.sortAsc ? "asc" : "desc"));
        }
      });

      // Wire row click -> modal
      section.querySelectorAll("tbody tr").forEach(tr => {
        tr.addEventListener("click", () => openModal(tr.dataset.symbol));
      });

      wrap.appendChild(section);
    });

    if (!wrap.children.length) {
      wrap.innerHTML = '<p class="muted" style="padding:40px;text-align:center;">No categories selected.</p>';
    }
  }

  // ---- Modal --------------------------------------------------------------
  function findSymbol(sym) {
    for (const cat of state.payload.categories) {
      for (const s of cat.symbols) if (s.symbol === sym) return s;
    }
    return null;
  }

  function buildSignalEntries(sym) {
    // Returns an array of {cls?, title, body} for each FIRING signal — entries
    // that don't apply are omitted entirely (no "shorts are not underwater"
    // filler). Plain-language phrasing for end-users.
    const entries = [];
    const sig = sym.signals || {};
    const cur = sym.current || {};
    const display = sym.display || sym.symbol;

    // Trade bias
    if (sig.contrarian === "bullish_contrarian") {
      const shortPct = cur.short_pct == null ? null : Math.round(cur.short_pct);
      entries.push({
        cls: "bias-bullish",
        title: "Trade bias: BULLISH (contrarian)",
        body:
          (shortPct == null ? "≥70%" : shortPct + "%") +
          " of retail traders are short " + display +
          ". The retail crowd is statistically wrong at extremes. " +
          "Bias: look for buying opportunities.",
      });
    } else if (sig.contrarian === "bearish_contrarian") {
      const longPct = cur.long_pct == null ? null : Math.round(cur.long_pct);
      entries.push({
        cls: "bias-bearish",
        title: "Trade bias: BEARISH (contrarian)",
        body:
          (longPct == null ? "≥70%" : longPct + "%") +
          " of retail traders are long " + display +
          ". The retail crowd is statistically wrong at extremes. " +
          "Bias: look for selling opportunities.",
      });
    } else {
      entries.push({
        cls: "bias-neutral",
        title: "Trade bias: NEUTRAL",
        body:
          "Crowd positioning is balanced — no contrarian edge from this " +
          "signal alone. Check other signals (underwater crowd, COT " +
          "divergence, extremes).",
      });
    }

    // Capitulation pressure (only fires when crowded AND deeply underwater)
    const u = sig.capitulation || sig.underwater || {};
    if (u.long_pressure) {
      const pips = Math.abs(Math.round(u.long_pnl_pips_estimate || 0));
      entries.push({
        title: "Longs are losing money",
        body:
          "Average long entry: " + fmtPrice(cur.avg_long_price) +
          ". Current spot: " + fmtPrice(cur.spot_estimate) +
          ". Longs are " + pips + " pips underwater on average. " +
          "If " + display + " drops further, expect stop-outs and forced " +
          "selling — but a turn higher could trigger relief buying.",
      });
    }
    if (u.short_pressure) {
      const pips = Math.abs(Math.round(u.short_pnl_pips_estimate || 0));
      const both = u.long_pressure;
      entries.push({
        title: both ? "Shorts are also losing money" : "Shorts are losing money",
        body:
          "Average short entry: " + fmtPrice(cur.avg_short_price) +
          ". Current spot: " + fmtPrice(cur.spot_estimate) +
          ". Shorts are " + pips + " pips underwater" +
          (both
            ? ". Both sides are losing — typical of choppy/ranging conditions."
            : ". A continued rise could trigger short-covering / squeeze."),
      });
    }

    // COT confluence: retail AND large specs both crowded on the SAME side
    // at extremes. Requires the overlay toggle to be on.
    const cc = sig.cot_confluence;
    if (state.cotOverlay && cc && cc.has_confluence) {
      const retailLong = Math.round(cc.retail_long_pct || 0);
      const rank = Number(cc.cot_spec_ext_6m || 0);
      const isBearish = cc.direction === "bearish_contrarian";
      const retailLine = isBearish
        ? ("Retail: " + retailLong + "% long.")
        : ("Retail: " + (100 - retailLong) + "% short.");
      const cotLine = isBearish
        ? ("CFTC large speculators: at 6M high of long positioning (rank " + rank.toFixed(2) + ").")
        : ("CFTC large speculators: at 6M low of long positioning (rank " + rank.toFixed(2) + ").");
      const sideText = isBearish ? "bullish side" : "bearish side";
      const reversal = isBearish ? "bearish reversal" : "bullish reversal";
      entries.push({
        title: "Retail AND large specs both at extremes — strong contrarian signal",
        body:
          retailLine + " " + cotLine + " " +
          "Both groups are crowded on the " + sideText + ". " +
          "When retail and institutions both reach extremes together, " +
          "reversals statistically follow. Strong conviction for a " + reversal +
          ". (COT report: " + (cc.cot_report_date || "—") + ")",
      });
    }

    // Extremes
    const e3 = (sig.extremes || {}).ext_3m || {};
    const e6 = (sig.extremes || {}).ext_6m || {};
    const e3Ripe = e3.value !== null && e3.value !== undefined;
    const e6Ripe = e6.value !== null && e6.value !== undefined;
    if (e3Ripe || e6Ripe) {
      const parts = [];
      if (e3Ripe) parts.push("3M: " + Math.round((e3.value || 0) * 100) + "%ile (n=" + e3.n + ")");
      if (e6Ripe) parts.push("6M: " + Math.round((e6.value || 0) * 100) + "%ile (n=" + e6.n + ")");
      entries.push({
        title: "Positioning extremes",
        body: parts.join(" · ") +
          ". Values near 0% or 100% mark multi-month turning-point zones.",
      });
    } else {
      const n = Math.max(e3.n || 0, e6.n || 0);
      const need = Math.max(0, 20 - n);
      entries.push({
        title: "3M / 6M extremes: not enough history yet",
        body:
          "(" + n + " snapshot" + (n === 1 ? "" : "s") + " collected). " +
          "These signals will activate after ~" + (need < 10 ? 10 : need) +
          "-20 more snapshots.",
      });
    }

    return entries;
  }

  function renderSignalEntriesHtml(entries) {
    return entries.map(e => {
      const cls = "signal-entry" + (e.cls ? " " + e.cls : "");
      return (
        '<li class="' + cls + '">' +
        '<div class="signal-arrow">&rarr;</div>' +
        '<div class="signal-body">' +
        '<div class="signal-title">' + e.title + '</div>' +
        '<div class="signal-text">' + e.body + '</div>' +
        '</div>' +
        '</li>'
      );
    }).join("");
  }

  function openModal(symKey) {
    const sym = findSymbol(symKey);
    if (!sym) return;
    state.activeSymbol = sym;
    state.chartWindow = "1M";
    state.showSpot = false;

    const modal = document.getElementById("symbolDetailModal");
    const body = document.getElementById("symbolDetailContent");

    const cur = sym.current || {};
    const u = sym.signals.capitulation || sym.signals.underwater || {};
    const nDays = symbolDistinctDays(sym);
    const chartReady = nDays >= MIN_DAYS_FOR_CHART;

    const windowButtons = ["1W", "1M", "3M", "6M", "1Y", "All"].map(w => {
      const data = (sym.history || {})[w] || {};
      const enabled = (data.dates || []).length > 1;
      return '<button type="button" class="win-btn' + (w === "1M" ? " active" : "") + '" data-window="' + w + '"' +
        (enabled ? "" : ' disabled title="Not enough history yet"') + ">" + w + "</button>";
    }).join("");

    const chartSection = chartReady
      ? ('<div class="modal-chart-section">' +
         '<div class="chart-controls">' +
         '<div class="window-selector">' + windowButtons + '</div>' +
         '<label class="toggle"><input type="checkbox" id="modalSpotToggle"><span>Show spot estimate</span></label>' +
         '</div>' +
         '<div class="chart-wrapper"><canvas id="retailModalChart"></canvas></div>' +
         '</div>')
      : ('<div class="modal-chart-pending">' +
         'Historical chart will appear once we have at least ' + MIN_DAYS_FOR_CHART + ' days of data.<br>' +
         'Currently collecting: ' + nDays + ' day' + (nDays === 1 ? '' : 's') + ' so far.' +
         '</div>');

    body.innerHTML =
      '<header class="modal-header">' +
      '<h2>' + sym.display + ' <small class="muted">(' + sym.symbol + ')</small></h2>' +
      '<div class="muted">Snapshot: ' + (cur.fetched_at || "—") + '</div>' +
      '</header>' +
      '<div class="modal-grid">' +
      '<div class="modal-card">' +
      '<h3>Current Position</h3>' +
      '<dl class="kv">' +
      '<div><dt>Long</dt><dd>' + fmtPct1(cur.long_pct) + ' (' + fmtInt(cur.long_positions) + ')</dd></div>' +
      '<div><dt>Short</dt><dd>' + fmtPct1(cur.short_pct) + ' (' + fmtInt(cur.short_positions) + ')</dd></div>' +
      '<div><dt>Long volume</dt><dd>' + fmtPrice(cur.long_volume) + '</dd></div>' +
      '<div><dt>Short volume</dt><dd>' + fmtPrice(cur.short_volume) + '</dd></div>' +
      '<div><dt>Avg long entry</dt><dd>' + fmtPrice(cur.avg_long_price) + ' <span class="pnl">' + fmtPips(u.long_pnl_pips_estimate) + '</span></dd></div>' +
      '<div><dt>Avg short entry</dt><dd>' + fmtPrice(cur.avg_short_price) + ' <span class="pnl">' + fmtPips(u.short_pnl_pips_estimate) + '</span></dd></div>' +
      '<div><dt>Spot estimate</dt><dd>' + fmtPrice(cur.spot_estimate) + '</dd></div>' +
      '</dl>' +
      '</div>' +
      '<div class="modal-card">' +
      '<h3>Signals</h3>' +
      '<ul class="signal-list">' +
      renderSignalEntriesHtml(buildSignalEntries(sym)) +
      '</ul>' +
      '</div>' +
      '</div>' +
      chartSection;

    modal.hidden = false;
    document.body.classList.add("modal-open");

    if (chartReady) {
      body.querySelectorAll(".win-btn").forEach(btn => {
        btn.addEventListener("click", () => {
          if (btn.disabled) return;
          body.querySelectorAll(".win-btn").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
          state.chartWindow = btn.dataset.window;
          renderModalChart();
        });
      });
      body.querySelector("#modalSpotToggle").addEventListener("change", e => {
        state.showSpot = !!e.target.checked;
        renderModalChart();
      });
      renderModalChart();
    }
  }

  function closeModal() {
    const modal = document.getElementById("symbolDetailModal");
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    if (state.chart) { state.chart.destroy(); state.chart = null; }
    state.activeSymbol = null;
  }

  function renderModalChart() {
    if (!state.activeSymbol) return;
    const colors = themeColors();
    const data = (state.activeSymbol.history || {})[state.chartWindow] || { dates: [], long_pct: [], spot_estimate: [] };
    const datasets = [
      {
        label: "Long %",
        data: data.long_pct,
        borderColor: colors.accent,
        backgroundColor: "transparent",
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.15,
        yAxisID: "y",
      },
    ];
    if (state.showSpot) {
      datasets.push({
        label: "Spot estimate",
        data: data.spot_estimate,
        borderColor: colors.primary,
        backgroundColor: "transparent",
        borderWidth: 1.5,
        borderDash: [5, 4],
        pointRadius: 0,
        tension: 0.15,
        yAxisID: "y1",
      });
    }

    const cfg = {
      type: "line",
      data: { labels: data.dates, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: state.showSpot, labels: { color: colors.label } },
          tooltip: {
            backgroundColor: colors.tooltipBg,
            titleColor: colors.tooltipFg,
            bodyColor: colors.tooltipFg,
            borderColor: colors.tooltipBorder,
            borderWidth: 1,
          },
        },
        scales: {
          x: {
            ticks: { color: colors.tick, autoSkip: true, autoSkipPadding: 30, font: { size: 10 } },
            grid: { color: colors.grid },
          },
          y: {
            min: 0, max: 100,
            ticks: { color: colors.tick, callback: v => v + "%", font: { size: 10 } },
            grid: { color: colors.grid },
          },
          y1: {
            display: state.showSpot,
            position: "right",
            ticks: { color: colors.tick, font: { size: 10 } },
            grid: { drawOnChartArea: false },
          },
        },
      },
    };

    if (state.chart) state.chart.destroy();
    const canvas = document.getElementById("retailModalChart");
    state.chart = new Chart(canvas, cfg);
  }

  // ---- COT overlay toggle (persisted) ------------------------------------
  function applyStoredCotOverlay() {
    try {
      const v = localStorage.getItem("retail_cot_overlay");
      state.cotOverlay = v === "1";
    } catch (_) { state.cotOverlay = false; }
    document.getElementById("cotOverlayToggle").checked = state.cotOverlay;
  }

  function wireCotOverlay() {
    document.getElementById("cotOverlayToggle").addEventListener("change", e => {
      state.cotOverlay = !!e.target.checked;
      try { localStorage.setItem("retail_cot_overlay", state.cotOverlay ? "1" : "0"); } catch (_) {}
      renderTables();
    });
  }

  function wireModalClose() {
    const modal = document.getElementById("symbolDetailModal");
    modal.querySelector(".modal-close").addEventListener("click", closeModal);
    modal.querySelector(".modal-backdrop").addEventListener("click", closeModal);
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && !modal.hidden) closeModal();
    });
  }

  // ---- Theme reactivity ---------------------------------------------------
  function applyStoredTheme() {
    try { if (localStorage.getItem("cot-theme") === "dark") document.body.classList.add("dark"); }
    catch (_) {}
  }
  function wireThemeReactivity() {
    const observer = new MutationObserver(() => {
      if (state.chart) renderModalChart();
    });
    observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
  }

  // ---- Bootstrap ----------------------------------------------------------
  function init(payload) {
    state.payload = payload;
    renderProviderMeta();
    renderCatChips();
    applyStoredCotOverlay();
    wireCotOverlay();
    wireModalClose();
    wireThemeReactivity();
    renderTables();
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
        console.error("retail-chart bootstrap error:", err);
        const wrap = document.getElementById("retailContent");
        if (wrap) {
          wrap.innerHTML = '<p style="color:var(--muted);text-align:center;padding:40px;">Failed to load retail sentiment data.</p>';
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
