/* Retail Sentiment page logic. Depends on Chart.js v4 UMD. */
(function () {
  "use strict";

  const DATA_URL = window.RETAIL_DATA_URL || "/data/retail-sentiment.json";

  // Hide visualizations until we have at least this many snapshots for a symbol.
  const MIN_POINTS_FOR_SPARKLINE = 5;
  const MIN_POINTS_FOR_CHART = 5;

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
    div:
      "Position count and volume disagree on direction. Many small traders " +
      "on one side, few large traders on the other — the bigger money may " +
      "be on the side with fewer positions.",
    cot:
      "Smart-money divergence: retail crowd is positioned opposite to CFTC " +
      "large speculators. When retail and institutions disagree, " +
      "institutions usually win.",
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

  function alertIcons(sym) {
    const out = [];
    const sig = sym.signals || {};
    if (sig.contrarian === "bearish_contrarian") {
      out.push('<span class="alert-icon alert-bearish" title="' + escAttr(TOOLTIPS.pill_bearish) + '">⚠</span>');
    } else if (sig.contrarian === "bullish_contrarian") {
      out.push('<span class="alert-icon alert-bullish" title="' + escAttr(TOOLTIPS.pill_bullish) + '">⚠</span>');
    }
    const u = sig.underwater || {};
    if (u.longs_underwater) {
      out.push('<span class="alert-icon alert-under" title="' + escAttr(TOOLTIPS.longs_losing) + '">LONGS LOSING</span>');
    }
    if (u.shorts_underwater) {
      out.push('<span class="alert-icon alert-under" title="' + escAttr(TOOLTIPS.shorts_losing) + '">SHORTS LOSING</span>');
    }
    const vd = sig.vol_position_divergence || {};
    if (vd.has_divergence) {
      out.push('<span class="alert-icon alert-div" title="' + escAttr(TOOLTIPS.div) + '">DIV</span>');
    }
    const e3 = (sig.extremes || {}).ext_3m || {};
    const e6 = (sig.extremes || {}).ext_6m || {};
    if (e3.class === "ext-95" || e3.class === "ext-05") {
      out.push('<span class="alert-icon alert-ext" title="' + escAttr(TOOLTIPS.extreme) + '">⚡3M</span>');
    }
    if (e6.class === "ext-95" || e6.class === "ext-05") {
      out.push('<span class="alert-icon alert-ext" title="' + escAttr(TOOLTIPS.extreme) + '">⚡6M</span>');
    }
    if (state.cotOverlay && sig.cot_divergence && sig.cot_divergence.has_divergence) {
      out.push('<span class="alert-icon alert-cot" title="' + escAttr(TOOLTIPS.cot) + '">⚡COT</span>');
    }
    return out.join("");
  }

  function symbolPointCount(sym) {
    // Use the longest available history window for the count.
    const all = (sym.history || {}).All || {};
    const dates = all.dates || [];
    return dates.length;
  }

  function pageHasAnyExtremes() {
    if (!state.payload) return false;
    for (const cat of state.payload.categories) {
      for (const s of cat.symbols) {
        const sig = s.signals || {};
        const e3 = (sig.extremes || {}).ext_3m || {};
        const e6 = (sig.extremes || {}).ext_6m || {};
        if (e3.value !== null && e3.value !== undefined) return true;
        if (e6.value !== null && e6.value !== undefined) return true;
      }
    }
    return false;
  }

  function renderRow(sym, opts) {
    const showExtCols = opts && opts.showExtCols;
    const cur = sym.current || {};
    const sig = sym.signals || {};
    const ext3 = (sig.extremes || {}).ext_3m || {};
    const ext6 = (sig.extremes || {}).ext_6m || {};
    const u = sig.underwater || {};
    const vd = sig.vol_position_divergence || {};
    const nPoints = symbolPointCount(sym);

    const longPct = cur.long_pct;
    const shortPct = cur.short_pct;
    const volLong = (vd.volume_long_pct !== null && vd.volume_long_pct !== undefined) ? vd.volume_long_pct : null;
    const volShort = volLong !== null ? (100 - volLong) : null;

    const splitBar =
      '<div class="ls-bar" title="Long ' + fmtPct(longPct) + ' · Short ' + fmtPct(shortPct) + '">' +
      '<div class="ls-bar-long" style="width:' + (longPct == null ? 0 : longPct) + '%"></div>' +
      '<div class="ls-bar-short" style="width:' + (shortPct == null ? 0 : shortPct) + '%"></div>' +
      "</div>" +
      '<div class="ls-bar-labels"><span class="ls-long">' + fmtPct(longPct) + ' L</span>' +
      '<span class="ls-short">' + fmtPct(shortPct) + ' S</span></div>';

    const volBar = (volLong !== null) ?
      ('<div class="ls-bar ls-bar-sm ' + (vd.has_divergence ? "ls-bar-divergent" : "ls-bar-muted") + '" title="Volume Long ' + fmtPct1(volLong) + '">' +
        '<div class="ls-bar-long" style="width:' + volLong + '%"></div>' +
        '<div class="ls-bar-short" style="width:' + volShort + '%"></div>' +
        "</div>" +
        '<div class="ls-bar-labels small">' +
        '<span>' + fmtPct1(volLong) + ' vL</span>' +
        '<span>' + fmtPct1(volShort) + ' vS</span></div>') :
      '<span class="muted small">—</span>';

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

    const ext3Cell = (ext3.value === null || ext3.value === undefined) ?
      '<span class="ext-na" title="' + (ext3.n || 0) + ' datapoints (need 20)">—</span>' :
      '<span class="' + (ext3.class || "ext-na") + '">' + Math.round((ext3.value || 0) * 100) + '%</span>';
    const ext6Cell = (ext6.value === null || ext6.value === undefined) ?
      '<span class="ext-na" title="' + (ext6.n || 0) + ' datapoints (need 20)">—</span>' :
      '<span class="' + (ext6.class || "ext-na") + '">' + Math.round((ext6.value || 0) * 100) + '%</span>';

    const longPctSeries = ((sym.history || {})["1M"] || {}).long_pct || [];
    const sparkCellHtml = (nPoints >= MIN_POINTS_FOR_SPARKLINE) ? sparkline(longPctSeries) : "";

    const extCells = showExtCols
      ? '<td class="ext-cell">' + ext3Cell + '</td><td class="ext-cell">' + ext6Cell + '</td>'
      : '';

    return (
      '<tr data-symbol="' + sym.symbol + '">' +
      '<td class="sym">' + sym.display + '</td>' +
      '<td class="ls-cell">' + splitBar + "</td>" +
      '<td class="vol-cell">' + volBar + "</td>" +
      '<td class="price-cell">' + priceCell + "</td>" +
      '<td><span class="pill ' + pillCls + '" title="' + escAttr(pillTip) + '">' + pillTxt + "</span></td>" +
      extCells +
      '<td class="alerts-cell"><div class="alerts-row">' + alertIcons(sym) + "</div></td>" +
      '<td class="spark-cell">' + sparkCellHtml + "</td>" +
      "</tr>"
    );
  }

  function renderTables() {
    const wrap = document.getElementById("retailContent");
    wrap.innerHTML = "";

    const showExtCols = pageHasAnyExtremes();
    const extHeaders = showExtCols
      ? '<th data-sort="ext_3m">3M ext</th><th data-sort="ext_6m">6M ext</th>'
      : '';

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
        '<th>Volume</th>' +
        '<th data-sort="spot">Price</th>' +
        '<th>Signal</th>' +
        extHeaders +
        '<th data-sort="alerts">Alerts</th>' +
        '<th>1M</th>' +
        '</tr></thead>' +
        '<tbody>' + symbols.map(s => renderRow(s, { showExtCols })).join("") + '</tbody>' +
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

    // Underwater longs
    const u = sig.underwater || {};
    if (u.longs_underwater) {
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
    if (u.shorts_underwater) {
      const pips = Math.abs(Math.round(u.short_pnl_pips_estimate || 0));
      const both = u.longs_underwater;
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

    // Volume vs position divergence
    const vd = sig.vol_position_divergence || {};
    if (vd.has_divergence) {
      entries.push({
        title: "Volume disagrees with position count",
        body:
          fmtPct1(vd.position_long_pct) + " of accounts are long, but " +
          fmtPct1(vd.volume_long_pct) + " of volume is long (gap: " +
          fmtPct1(vd.divergence_pp) + "). Many small traders on one side, " +
          "fewer large traders on the other — bigger money may be on the " +
          "side with fewer positions.",
      });
    }

    // COT smart-money divergence — only if overlay is enabled
    const cd = sig.cot_divergence;
    if (state.cotOverlay && cd && cd.has_divergence) {
      const retail = Math.round((cd.retail_long_normalized || 0) * 100);
      const cot = Math.round((cd.cot_spec_long_normalized || 0) * 100);
      const biasNote = (sig.contrarian !== "neutral")
        ? " — adds conviction to the " +
          (sig.contrarian === "bullish_contrarian" ? "bullish" : "bearish") +
          " bias above"
        : "";
      entries.push({
        title: "Smart money disagrees with retail",
        body:
          "Retail crowd: " + retail + "% long. CFTC large speculators " +
          "(institutions): " + cot + "% long. They're on opposite sides. " +
          "Institutions usually win these disagreements" + biasNote +
          ". (COT report: " + (cd.cot_report_date || "—") + ")",
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
    const u = sym.signals.underwater || {};
    const nPoints = symbolPointCount(sym);
    const chartReady = nPoints >= MIN_POINTS_FOR_CHART;

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
         'Historical chart will appear once enough data is collected ' +
         '(currently ' + nPoints + '/' + MIN_POINTS_FOR_CHART + ' snapshots).' +
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
