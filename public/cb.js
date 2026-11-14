/* Central Banks (phase 3a): overview, bank page, pair page. Pure DOM over /data/cb/*.json - every number, reason, tooltip and
 * link comes from the JSON; the HTML shell only carries the page kind. Blue = hawkish (higher rates), red = dovish, the sign is
 * always written out. Depends on score-palette.js and, on bank / pair pages, Chart.js 4 + chartjs-plugin-annotation.
 */
(function () {
  "use strict";

  const CFG = window.CB || {};
  const SP = window.ScorePalette;
  const root = document.getElementById("cbRoot");
  const metaEl = document.getElementById("cbMeta");
  const titleEl = document.getElementById("cbTitle");
  const MINUS = "−", NA = "—", EN = "–", DOT = " · ";
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const DAY_MS = 86400000;
  const state = { meta: null, charts: [], redraw: [] };

  // ---- formatting -----------------------------------------------------------------------------------------------------
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function isNum(v) { return v !== null && v !== undefined && v !== "" && !Number.isNaN(Number(v)); }
  function fmtRate(v, dp) { return isNum(v) ? Number(v).toFixed(dp === undefined ? 2 : dp) : NA; }
  // 2 decimals, or 3 when the value is a midpoint that 2 would misstate (Fed 3.875)
  function fmtRateAuto(v) { return isNum(v) ? (Math.abs(Number(v) * 100 - Math.round(Number(v) * 100)) < 1e-6 ? Number(v).toFixed(2) : Number(v).toFixed(3)) : NA; }
  function fmtSigned(v, dp) {
    if (!isNum(v)) return NA;
    const n = Number(v), s = Math.abs(n).toFixed(dp === undefined ? 1 : dp);
    if (Number(s) === 0) return s;
    return (n > 0 ? "+" : MINUS) + s;
  }
  function parts(iso) { const p = String(iso).slice(0, 10).split("-"); return { y: +p[0], m: +p[1], d: +p[2] }; }
  function fmtDay(iso) { if (!iso) return NA; const p = parts(iso); return p.d + " " + MONTHS[p.m - 1]; }
  function fmtDate(iso) { if (!iso) return NA; const p = parts(iso); return p.d + " " + MONTHS[p.m - 1] + " " + p.y; }
  function fmtRange(w) { return w ? fmtDay(w[0]) + EN + fmtDay(w[1]) : ""; }
  function ms(iso) { return Date.parse(String(iso).slice(0, 10) + "T00:00:00Z"); }
  function fmtMonthYear(x) { const d = new Date(x); return MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear(); }
  function weekday(iso) { return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date(ms(iso)).getUTCDay()]; }
  function todayIn(tz) {
    try { return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date()); }
    catch (e) { return new Date().toISOString().slice(0, 10); }
  }
  function daysBetween(a, b) { return Math.round((ms(b) - ms(a)) / DAY_MS); }
  function pct(p) { return (Number(p) * 100).toFixed(1) + "%"; }                   // one decimal everywhere ("22.6%")

  // headline of a long reason (the full text stays in the title attribute)
  function shortNa(t) { return String(t === null || t === undefined ? "" : t).replace(/proxy without short end:[^;]*/g, "proxy without short end").replace(/history starts (\d{4}-\d{2}-\d{2}) \(needs[^)]*\)/g, "history starts $1"); }
  function tint(v, scale) { return SP && isNum(v) ? SP.styleAttr(SP.gradientStyle(Number(v), scale)) : ""; }
  function flagBadge(flag) {
    if (!flag) return "";
    const f = ((state.meta || {}).flags || {})[flag] || {};
    return ' <span class="econ-flag cb-flag cb-flag-' + esc(flag) + '" title="' + esc(f.help || "") + '">' + esc(f.label || flag) + "</span>";
  }
  function staleBadge(stale, tip) {
    return stale ? ' <span class="econ-flag flag-stale" title="' + esc(tip || "stale: the source is older than the freshness threshold") + '">stale</span>' : "";
  }

  // A metric {v, flag, stale, na}: signed value with a blue/red tint, its method badge; "—" with the reason when n/a.
  function numCell(m, o) {
    o = o || {};
    if (!m || !isNum(m.v)) {
      return '<td class="econ-cell cell-na cb-na" title="' + esc((m && m.na) || o.na || "n/a") + '">' + NA + "</td>";
    }
    const tip = (o.tip ? o.tip + "\n" : "") + (m.stale ? "stale: the source is older than the freshness threshold\n" : "") + (o.tipAfter || "");
    return '<td class="econ-cell cb-num' + (m.stale ? " cb-stale" : "") + (o.cls ? " " + o.cls : "") + '"' + (m.stale ? "" : tint(m.v, o.scale || 25)) +
      (tip.trim() ? ' title="' + esc(tip.trim()) + '"' : "") + ">" + fmtSigned(m.v, o.dp) + (o.unit || "") + (o.noBadge ? "" : flagBadge(m.flag)) + (o.extra || "") + "</td>";
  }
  function valueSpan(m, dp, unit, plain) {
    if (!m || !isNum(m.v)) return '<span class="cb-na" title="' + esc((m && m.na) || "n/a") + '">' + NA + "</span>";
    return '<span class="cb-val ' + (plain ? "" : m.v > 0 ? "cb-hawk" : m.v < 0 ? "cb-dove" : "") + '">' + fmtSigned(m.v, dp) + (unit || "") + "</span>";
  }

  // ---- countdown / decision time -----------------------------------------------------------------------------------------
  function timeText(t) {
    if (!t) return "";
    if (t.tbd) return "time TBD" + (t.window_local ? ", ~" + t.window_local[0] + EN + t.window_local[1] + " " + t.abbr : "");
    return t.local ? t.local + " " + t.abbr : "";
  }
  function cdSpan(n) {
    const t = n.time || {};
    return '<span class="cb-cd" data-utc="' + esc(t.utc || "") + '" data-date="' + esc(n.decision) + '" data-tz="' + esc(t.tz || "UTC") + '"></span>';
  }
  function countdownText(el) {
    const utc = el.dataset.utc, date = el.dataset.date, tz = el.dataset.tz;
    if (utc) {
      const diff = Date.parse(utc) - Date.now();
      if (diff <= 0) return todayIn(tz) === date ? "announced today" : "announced";
      const d = Math.floor(diff / DAY_MS), h = Math.floor((diff % DAY_MS) / 3600000), m = Math.floor((diff % 3600000) / 60000);
      return d >= 1 ? "in " + d + " d " + h + " h" : "in " + h + " h " + m + " m";
    }
    const days = daysBetween(todayIn(tz), date);
    return days > 0 ? "in " + days + " d" : days === 0 ? "today" : "announced";
  }
  function tick() {
    document.querySelectorAll(".cb-cd").forEach(function (el) { el.textContent = countdownText(el); });
    localTimes();
    document.querySelectorAll("[data-blackout-start]").forEach(function (el) {
      const now = Date.now(), a = Date.parse(el.dataset.blackoutStart), b = Date.parse(el.dataset.blackoutEnd);
      el.hidden = !(now >= a && now <= b);
    });
  }

  // ---- rate / next meeting text ------------------------------------------------------------------------------------------
  function rateHtml(r) {
    if (!r || !isNum(r.value)) return '<span class="cb-na">' + NA + "</span>";
    const main = r.range && isNum(r.lower) ? fmtRate(r.lower) + EN + fmtRate(r.upper) : fmtRate(r.value);
    let out = '<span class="cb-rate">' + main + "</span>";
    if (r.pending) {
      const tip = "In force today: " + fmtRate(r.in_force) + "%. Decided " + fmtDate(r.decided) + ", effective " + fmtDate(r.from) + ".";
      out += ' <span class="cb-pending" title="' + esc(tip) + '">(from ' + fmtDay(r.from) + ")</span>";
    }
    if (r.decision_status && r.decision_status !== "official" && r.decision_status !== "bis") {
      out += ' <span class="econ-flag flag-nc" title="' + esc("Decision status: " + r.decision_status + ". Not confirmed by an official series yet.") + '">unconfirmed</span>';
    }
    return out;
  }

  // ---- overview ------------------------------------------------------------------------------------------------------------
  function wireRows(scope) {
    scope.querySelectorAll("tr.cb-row").forEach(function (tr) {
      const go = function () { window.location.href = tr.dataset.href; };
      tr.addEventListener("click", function (ev) { if (ev.target.closest("a")) return; go(); });
      tr.addEventListener("keydown", function (ev) { if (ev.key === "Enter") go(); });
    });
  }


  const COMPARE_HELP = 'One line per bank from today, a point on each decision date. Hover (or tap / drag) for every bank at a date: the rate in force after the last meeting on or before it, ' +
    "in bp vs now and in 25 bp moves. Hollow points and dashed segments = estimates (no contract isolates the meeting). Click a bank above the chart to hide or show it; hover it to bring its line forward. " +
    '<span class="cb-hawk"><b>Blue = hawkish</b></span>, <span class="cb-dove"><b>red = dovish</b></span> in the tables; the sign is always written out.';
  // the explanation under a chart: "ⓘ How to read", closed, on the desktop; the phone keeps its own fold (PhoneUI, [data-htr])
  function howToRead(html) {
    return '<details class="cb-htr desk-only"><summary>ⓘ How to read</summary><div class="cb-sub">' + html + "</div></details>" +
      '<div class="cb-sub phone-only" data-htr>' + html + "</div>";
  }
  function sortable(box, key, render) {
    box.innerHTML = render(key);
    wireRows(box);
    paintSwatches();
    box.querySelectorAll(".cb-pill[data-sort]").forEach(function (b) { b.addEventListener("click", function () { sortable(box, b.dataset.sort, render); }); });
  }

  let themeWatched = false, hashWired = false;
  const PENDING = '<p class="cb-loading">The data files predate this page: the next Central Banks refresh writes the rate paths (at most ~2 hours).</p>';
  function renderOverview(ov) {
    if (!ov.paths) { root.innerHTML = PENDING; return; }
    destroyCharts();
    state.redraw = [];
    state.meta = ov.meta;
    titleEl.textContent = "Central Banks";
    metaEl.innerHTML = "As of " + esc(fmtDate(ov.meta.asof)) + DOT + ov.banks.length + " banks" + DOT + "stale after " + ov.meta.stale_after_bd + " business days";
    root.innerHTML = bankBar("compare") +
      '<section id="cbCompare">' + upNextHtml(pickUpNext(ov.paths, Date.now(), ov.next_decision)) +
      '<section class="cb-card cb-chartcard"><h3>Market-implied paths <small class="muted">next 12 months, every bank</small>' +
      '<span class="cb-toggle" role="group" aria-label="Scale"><button type="button" class="cb-tbtn active" data-mode="bp" aria-pressed="true">bp vs now</button><button type="button" class="cb-tbtn" data-mode="level" aria-pressed="false">level %</button></span></h3>' +
      '<div class="cb-legend-box" id="cbCompareLegend"></div><div class="cb-chart-wrap cb-chart-tall cb-compare-wrap"><canvas id="cbCompareChart"></canvas></div>' +
      howToRead(COMPARE_HELP) + "</section>" +
      '<div class="desk-only" id="cbCompareTable"></div><div class="phone-only" id="cbCompareList"></div></section>' +
      '<section id="cbStance" hidden></section>' +
      '<section id="cbPairs" hidden><p class="muted cb-loading">Loading pairs…</p></section>';
    let mode = "bp", chart = null;
    const canvas = document.getElementById("cbCompareChart");
    const legend = legendState(Object.keys(ov.paths).filter(function (c) { return ov.paths[c] && ov.paths[c].points; }));
    const names = {};
    Object.keys(ov.paths).forEach(function (c) { names[c] = ov.paths[c].name || ov.paths[c].short || c; });
    const redraw = function () { destroyCharts(); state.redraw.forEach(function (fn) { fn(); }); };
    registerRedraw(function () { chart = drawCompareChart(canvas, ov.paths, mode, { legend: legend }); });
    wireLegend(document.getElementById("cbCompareLegend"), legend, names, redraw, function () { return chart; });
    root.querySelectorAll(".cb-tbtn[data-mode]").forEach(function (b) {
      b.addEventListener("click", function () {
        mode = b.dataset.mode;
        root.querySelectorAll(".cb-tbtn[data-mode]").forEach(function (x) { x.classList.toggle("active", x === b); x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
        destroyCharts(); state.redraw.forEach(function (fn) { fn(); });
      });
    });
    sortable(document.getElementById("cbCompareTable"), "next", function (k) { return upcomingTableHtml(ov.paths, k); });
    sortable(document.getElementById("cbCompareList"), "next", function (k) { return upcomingListHtml(ov.paths, k); });
    let pairsLoaded = false, stanceDrawn = false;
    const tabOf = function () { const h = window.location.hash; return h === "#pairs" ? "pairs" : h === "#stance" || h === "#stance-how" ? "stance" : "compare"; };
    const show = function (tab) {                                     // Compare / Stance / Pairs: the first items of the bank bar (#stance, #pairs)
      root.querySelectorAll(".cb-bankbar-item").forEach(function (x) {
        const on = (x.dataset.k === tab);
        x.classList.toggle("active", on);
        if (on) x.setAttribute("aria-current", "page"); else x.removeAttribute("aria-current");
      });
      document.getElementById("cbCompare").hidden = tab !== "compare";
      document.getElementById("cbPairs").hidden = tab !== "pairs";
      document.getElementById("cbStance").hidden = tab !== "stance";
      if (tab === "stance" && !stanceDrawn) {                          // drawn when first shown (the map needs a visible canvas)
        stanceDrawn = true;
        renderStanceTab(document.getElementById("cbStance"), ov.stance);
        paintSwatches();
      }
      if (tab === "stance" && window.location.hash === "#stance-how") { const h = document.getElementById("cbStanceHow"); if (h) { h.open = true; h.scrollIntoView({ block: "start" }); } }
      if (tab === "pairs" && !pairsLoaded) {
        pairsLoaded = true;
        getJSON(CFG.urls.pairs).then(function (pj) {
          const rows = pj.pairs.map(function (p) { return pairCalc(p, ov.paths); });
          const box = document.getElementById("cbPairs");
          box.innerHTML = '<p class="cb-sub cb-pairs-help">' + esc(PAIRS_HELP) + '</p><div class="desk-only" id="cbPairsTable"></div><div class="phone-only" id="cbPairsList"></div>';
          sortable(document.getElementById("cbPairsTable"), "change", function (k) { return pairsTableHtml(rows, k); });
          sortable(document.getElementById("cbPairsList"), "change", function (k) { return pairsListHtml(rows, k); });
        }).catch(function (e) { document.getElementById("cbPairs").innerHTML = failHtml(e); });
      }
    };
    root.querySelectorAll('.cb-bankbar-item[data-k="compare"], .cb-bankbar-item[data-k="stance"], .cb-bankbar-item[data-k="pairs"]').forEach(function (a) {
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        if (history.replaceState) history.replaceState(null, "", a.dataset.k === "compare" ? window.location.pathname : "#" + a.dataset.k);
        show(a.dataset.k);
      });
    });
    show(tabOf());
    if (window.PhoneUI) window.PhoneUI.foldHowToRead();
    if (!hashWired) {
      hashWired = true;
      window.addEventListener("hashchange", function () { show(tabOf()); });
    }
    if (!themeWatched) { themeWatched = true; watchTheme(); }
    paintSwatches();
    tick();
  }

  // ---- charts ---------------------------------------------------------------------------------------------------------------
  function colors() {
    const cs = getComputedStyle(document.body);
    const g = function (n, d) { return (cs.getPropertyValue(n) || "").trim() || d; };
    return { fg: g("--fg", "#1a1a1a"), muted: g("--muted", "#666"), border: g("--border", "#e0e0e0"), accent: g("--accent", "#1565c0"),
             bank: g("--excluded-fg", "#7b1fa2"), warn: g("--warn-mid-fg", "#bf5a00"), surface: g("--surface", "#fff"),
             red: SP ? "rgb(" + SP.COT_RED + ")" : "#d32f2f", blue: SP ? "rgb(" + SP.COT_BLUE + ")" : "#1565c0" };
  }
  function withAlpha(c, a) {
    if (/^#/.test(c)) { const h = c.slice(1); const n = h.length === 3 ? h.split("").map(function (x) { return x + x; }).join("") : h; return "rgba(" + parseInt(n.slice(0, 2), 16) + "," + parseInt(n.slice(2, 4), 16) + "," + parseInt(n.slice(4, 6), 16) + "," + a + ")"; }
    return c.replace(/^rgb\(/, "rgba(").replace(/\)$/, "," + a + ")");
  }
  function monthTicks(scale) {
    const min = scale.min, max = scale.max, span = (max - min) / (30.4375 * DAY_MS);
    let step = span > 40 ? 6 : span > 20 ? 3 : 2;
    if (scale.width && scale.width < 420 && step < 3) step = 3;                       // phone: fewer month labels, no overlap
    const d0 = new Date(min), ticks = [], now = !!scale.options.cbNow;
    if (now) ticks.push({ value: min });                                               // the first point of a forward path is "Now"
    let y = d0.getUTCFullYear(), m = d0.getUTCMonth();
    m = Math.ceil(m / step) * step;
    for (;;) {
      const v = Date.UTC(y + Math.floor(m / 12), m % 12, 1);
      if (v > max) break;
      if (v >= min && !(now && v - min < 25 * DAY_MS)) ticks.push({ value: v });
      m += step;
    }
    scale.ticks = ticks;
  }
  function baseScales(c, yTitle, nowTick) {
    return {
      x: { type: "linear", cbNow: !!nowTick, grid: { color: withAlpha(c.border, 0.7) }, ticks: { color: c.muted, maxRotation: 0, autoSkip: false,
           callback: function (v) { return this.options.cbNow && v === this.min ? "Now" : fmtMonthYear(v); } }, afterBuildTicks: monthTicks },
      y: { grid: { color: withAlpha(c.border, 0.7) }, ticks: { color: c.muted }, title: { display: true, text: yTitle, color: c.muted } }
    };
  }
  function destroyCharts() { state.charts.forEach(function (ch) { ch.destroy(); }); state.charts = []; }
  function registerRedraw(fn) {
    state.redraw.push(fn);
    fn();
  }
  function watchTheme() {
    new MutationObserver(function () { destroyCharts(); state.redraw.forEach(function (fn) { fn(); }); paintSwatches(); }).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  }

  // ---- rate paths (stage 3): Compare (every bank on one chart) and the bank page (Now / 1w / 3w) --------------------------------
  // One fixed colour per bank, used on every CB page. Categorical palette validated with the dataviz validator (adjacent CVD ΔE ≥ 8.4,
  // normal-vision ΔE ≥ 19.3; dark steps ≥ 3:1 on the dark surface). Three light steps sit under 3:1 on white: the lines carry a direct
  // label and the table below repeats every value.
  const BANK_ORDER = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"];
  const BANK_COLORS = {
    light: { USD: "#2a78d6", EUR: "#eb6834", GBP: "#1baf7a", JPY: "#eda100", CAD: "#e87ba4", AUD: "#008300", NZD: "#4a3aa7", CHF: "#e34948" },
    dark: { USD: "#3987e5", EUR: "#d95926", GBP: "#199e70", JPY: "#c98500", CAD: "#d55181", AUD: "#008300", NZD: "#9085e9", CHF: "#e66767" }
  };
  function isDark() { return !!(document.body && document.body.classList.contains("dark")); }
  function bankColor(ccy) { return BANK_COLORS[isDark() ? "dark" : "light"][ccy] || "#888888"; }
  function paintSwatches() { document.querySelectorAll(".cb-swatch[data-ccy], .cb-lg-item:not(.off) .cb-lg-line[data-ccy]").forEach(function (el) { el.style.background = bankColor(el.dataset.ccy); }); }

  // "30% hike" (n = 0) · "+25 bp + 28% of +50" (n ≥ 1) · the same with "cut" / minus signs; "hold" when nothing is priced
  function probText(pr) {
    if (!pr) return "";
    const cut = pr.dir === "cut", sgn = cut ? MINUS : "+", word = cut ? "cut" : "hike";
    const p = Number(pr.p) || 0;
    if (pr.dir === "hold" || (pr.n === 0 && p < 0.005)) return "hold";
    const pc = pct(p);
    if (pr.n === 0) return pc + " " + word;
    return sgn + 25 * pr.n + " bp" + (p >= 0.005 ? " + " + pc + " of " + sgn + 25 * (pr.n + 1) : "");
  }
  // the move priced at one meeting: "48% hike · +12 bp" (EXACT / CURVE), "≈ +12 bp" (ESTIMATE), null when n/a
  function moveText(pt) {
    if (!pt || !isNum(pt.step_bp)) return null;
    const bp = fmtSigned(pt.step_bp, 1) + " bp";
    if (pt.est) return "≈ " + bp;
    return pt.prob ? probText(pt.prob) + DOT + bp : bp;
  }
  // a meeting no contract isolates (NZD 28 Oct): its move sits in a later estimate - "≈ +43.7 bp by 9 Dec, together with 28 Oct"
  function jointText(pt) {
    if (!pt || !pt.joint || !isNum(pt.joint.cum_bp)) return null;
    return "≈ " + fmtSigned(pt.joint.cum_bp, 1) + " bp by " + fmtDay(pt.joint.meeting) + ", together with " + fmtDay(pt.meeting);
  }
  function jointTip(pt) { return "No contract isolates this meeting; the " + fmtDay(pt.joint.meeting) + " estimate includes it."; }
  function jointSpan(pt) { return '<span class="cb-joint muted" title="' + esc(jointTip(pt)) + '">' + esc(jointText(pt)) + "</span>"; }
  function dataDateText(pj) { return pj && pj.data_asof ? "Market data " + fmtDay(pj.data_asof) + (pj.data_label ? DOT + pj.data_label : "") : ""; }
  // decision-day mode: today (bank time zone) is the next meeting or the last recorded decision - so it stays on all day, also after the decision is in the data
  function dayModeOf(d, today) {
    const n = d.summary.next, last = d.summary.last_decision, tz = (n && n.time && n.time.tz) || d.bank.tz || "UTC", t = today(tz);
    return !!((n && n.decision && t === n.decision) || (last && last.date && t === last.date));
  }
  // "Up next" in the browser: the nearest decision not passed yet (fixed time; BoJ: the end of its window); the payload's when none
  function pickUpNext(paths, nowMs, fallback) {
    let best = null;
    BANK_ORDER.forEach(function (c) {
      const pj = paths[c], n = pj && pj.next;
      if (!n) return;
      const until = Date.parse(n.end_utc || n.sort_utc || (n.decision + "T23:59:59Z"));
      if (!(until > nowMs)) return;
      const key = n.sort_utc || n.decision;
      if (!best || key < best.sort_utc) {
        best = { ccy: c, short: pj.short, href: pj.href, decision: n.decision, time: n.time, sort_utc: key, end_utc: n.end_utc || null,
                 point: (pj.points || []).find(function (p) { return p.meeting === n.decision; }) || null };
      }
    });
    return best || fallback || null;
  }
  function naSpan(reason) { return '<span class="cb-na" title="' + esc(shortNa(reason || "n/a")) + '">' + NA + "</span>"; }
  function m12Text(m) {
    if (!m || !isNum(m.cum_bp)) return null;
    return (m.est ? "≈ " : "") + fmtSigned(m.cum_bp, 1) + " bp" + DOT + hikesText(m.cum_bp);
  }
  function firstPoint(pj) { return pj && pj.points && pj.points.length ? pj.points[0] : null; }

  // rows of the Compare table: one per bank, values + sort keys (n/a always last)
  function compareRows(paths) {
    return BANK_ORDER.filter(function (c) { return paths[c]; }).map(function (c) {
      const pj = paths[c], pt = firstPoint(pj), n = pj.next;
      return { ccy: c, short: pj.short, name: pj.name || pj.short || c, href: pj.href, current: pj.current, next: n, point: pt, m12: pj.m12, d1w: pj.delta["1w"], d3w: pj.delta["3w"],
               data_text: dataDateText(pj), spread_note: pj.spread_note || null,
               keys: { next: n ? n.sort_utc || (n.time && n.time.utc) || n.decision : null, move: pt && isNum(pt.step_bp) ? Number(pt.step_bp) : null,
                       m12: pj.m12 && isNum(pj.m12.cum_bp) ? Number(pj.m12.cum_bp) : null } };
    });
  }
  function sortRows(rows, key) {
    const out = rows.slice();
    out.sort(function (a, b) {
      const x = a.keys[key], y = b.keys[key];
      if (x === null && y === null) return BANK_ORDER.indexOf(a.ccy) - BANK_ORDER.indexOf(b.ccy);
      if (x === null) return 1;
      if (y === null) return -1;
      if (key === "next") return x < y ? -1 : x > y ? 1 : BANK_ORDER.indexOf(a.ccy) - BANK_ORDER.indexOf(b.ccy);
      return y - x || BANK_ORDER.indexOf(a.ccy) - BANK_ORDER.indexOf(b.ccy);              // bp: most hawkish first
    });
    return out;
  }
  function currentText(cur) {
    if (!cur || !isNum(cur.rate)) return NA;
    return cur.range ? fmtRate(cur.lower) + EN + fmtRate(cur.upper) + "%" : fmtRateAuto(cur.rate) + "%";
  }
  // ---- v2 (RP style): moves in words, plain tables with coloured numbers, Pairs over the 12 months of Compare -------------------
  // The number of 25 bp moves in words, the one text used wherever "hikes" appears: x = |bp| / 25, k = floor(x), f = x - k;
  // 0.4 <= f <= 0.6 reads "k or k+1 hikes", otherwise n = round(x): "no change", "1 hike", "n hikes" (cut / cuts below zero).
  function hikesText(bp) {
    if (!isNum(bp)) return "";
    const v = Number(bp), x = Math.abs(v) / 25, E = 1e-9, k = Math.floor(x + E), f = x - k;
    const word = function (n) { return (v < 0 ? "cut" : "hike") + (n === 1 ? "" : "s"); };
    if (f >= 0.4 - E && f <= 0.6 + E) return k + " or " + (k + 1) + " " + word(k + 1);
    const n = Math.round(x);
    return n === 0 ? "no change" : n + " " + word(n);
  }
  // numbers are coloured as text only (blue = hawkish / higher, red = dovish / lower), never as a cell background
  function tone(v) { return isNum(v) && Number(v) > 0 ? " cb-hawk" : isNum(v) && Number(v) < 0 ? " cb-dove" : ""; }
  function txtTd(v, html, tip, cls) {
    return '<td class="cb-n' + tone(v) + (cls ? " " + cls : "") + '"' + (tip ? ' title="' + esc(tip) + '"' : "") + ">" + html + "</td>";
  }
  function naTd(reason) { return '<td class="cb-n">' + naSpan(reason) + "</td>"; }
  function deltaTd(m, tip) { return m && isNum(m.v) ? txtTd(m.v, esc((m.flag === "ESTIMATE" || m.est ? "≈ " : "") + fmtSigned(m.v, 1)), tip) : naTd(m && m.na); }
  function pct1(p) { return (Number(p) * 100).toFixed(1) + "%"; }

  // the next meeting of one bank in the Upcoming table: probability, direction, priced move, most likely outcome, 12M
  function moveDir(pt) {
    if (!pt) return null;
    if (pt.prob) return pt.prob.dir === "hold" || (pt.prob.n === 0 && !(Number(pt.prob.p) >= 0.0005)) ? null : pt.prob.dir;
    const v = isNum(pt.step_bp) ? Number(pt.step_bp) : pt.joint && isNum(pt.joint.step_bp) ? Number(pt.joint.step_bp) : null;
    return v === null || Math.abs(v) < 0.05 ? null : v > 0 ? "hike" : "cut";
  }
  function probCellText(pt) {                                         // "35.6%" (n = 0), "+25 bp + 20% of +50" (n >= 1); null for a hold / an estimate
    if (!pt || pt.est || !pt.prob || moveDir(pt) === null) return null;
    return pt.prob.n === 0 ? pct1(pt.prob.p) : probText(pt.prob);
  }
  function mostLikelyText(pt) {                                       // n or n+1 moves, whichever is more likely: HOLD, HIKE, CUT, HIKE 50, ...
    if (!pt || pt.est || !pt.prob) return null;
    const pr = pt.prob;
    if (pr.dir === "hold") return "HOLD";
    const k = (Number(pr.p) || 0) > 0.5 ? pr.n + 1 : pr.n, w = pr.dir === "cut" ? "CUT" : "HIKE";
    return k === 0 ? "HOLD" : k === 1 ? w : w + " " + 25 * k;
  }
  function pricedMove(pt) {                                           // step_bp at the meeting; NZD: the later estimate that includes it
    if (pt && isNum(pt.step_bp)) return { text: (pt.est ? "≈ " : "") + fmtSigned(pt.step_bp, 1), joint: false };
    if (pt && pt.joint && isNum(pt.joint.cum_bp)) return { text: "≈ " + fmtSigned(pt.joint.cum_bp, 1) + " (by " + fmtDay(pt.joint.meeting) + ")", joint: true };
    return null;
  }
  function upcomingCells(r) {
    const pt = r.point, m = r.m12, mv = pricedMove(pt);
    const probNa = !pt ? "no upcoming meeting" : pt.est ? "estimate: no contract isolates the meeting, so no probability" : "no move priced";
    return { prob: probCellText(pt), probNa: probNa, dir: moveDir(pt), move: mv ? mv.text : null, joint: !!(mv && mv.joint), likely: mostLikelyText(pt),
             m12: m && isNum(m.cum_bp) ? (m.est ? "≈ " : "") + fmtSigned(m.cum_bp, 1) : null,
             m12Tip: m && isNum(m.cum_bp) ? "after the " + fmtDate(m.meeting) + " meeting" + DOT + hikesText(m.cum_bp) + (r.spread_note ? ". " + r.spread_note : "") : null };
  }
  function sortPills(list, active) {
    return '<div class="cb-pills" role="group" aria-label="Sort by"><span class="cb-pills-label">Sort by:</span>' + list.map(function (s) {
      return '<button type="button" class="cb-pill' + (s[0] === active ? " active" : "") + '" data-sort="' + esc(s[0]) + '" aria-pressed="' + (s[0] === active ? "true" : "false") + '">' + esc(s[1]) + "</button>";
    }).join('<span class="cb-pills-sep" aria-hidden="true">·</span>') + "</div>";
  }
  const UPCOMING_SORTS = [["next", "Date"], ["move", "Priced move"], ["m12", "12M"]];
  const MOVE_TIP = "Change in the implied rate at this meeting vs the current rate, in bp (e.g. 35.6% probability of +25 bp = +8.9 bp).";
  function upcomingHead(sortKey) { return '<div class="cb-tablehead"><h3>Upcoming meetings</h3>' + sortPills(UPCOMING_SORTS, sortKey) + "</div>"; }
  function upcomingTableHtml(paths, sortKey) {
    sortKey = sortKey || "next";
    const rows = sortRows(compareRows(paths), sortKey).map(function (r) {
      const u = upcomingCells(r), n = r.next, pt = r.point;
      const muted = function (tip) { return '<span class="muted" title="' + esc(tip) + '">' + NA + "</span>"; };
      const moveTd = u.move === null ? naTd(pt ? pt.na || pt.step_na : "no upcoming meeting")
        : u.joint ? '<td class="cb-n"><span class="cb-joint muted" title="' + esc(jointTip(pt)) + '">' + esc(u.move) + "</span></td>"
        : txtTd(pt.step_bp, esc(u.move), MOVE_TIP);
      return '<tr class="cb-row" data-href="' + esc(r.href) + '" data-ccy="' + esc(r.ccy) + '" tabindex="0">' +
        '<td class="cb-date">' + (n ? fmtDate(n.decision) : naSpan("no upcoming meeting")) + "</td>" +
        '<td class="cb-bank" title="' + esc(r.data_text) + '"><span class="cb-swatch" data-ccy="' + esc(r.ccy) + '"></span><a href="' + esc(r.href) + '">' + esc(r.name) + "</a></td>" +
        '<td class="cb-n">' + esc(currentText(r.current)) + "</td>" +
        (u.prob ? '<td class="cb-n">' + esc(u.prob) + "</td>" : '<td class="cb-n">' + muted(u.probNa) + "</td>") +
        "<td>" + (u.dir ? '<span class="' + (u.dir === "cut" ? "cb-dove" : "cb-hawk") + '">' + u.dir + "</span>" : muted(pt && pt.est ? u.probNa : "no move priced")) + "</td>" +
        moveTd +
        "<td>" + (u.likely ? '<span class="cb-likely' + (u.likely === "HOLD" ? "" : u.likely.indexOf("CUT") === 0 ? " cb-dove" : " cb-hawk") + '">' + u.likely + "</span>" : muted(u.probNa)) + "</td>" +
        (u.m12 ? txtTd(r.m12.cum_bp, esc(u.m12), u.m12Tip) : naTd(r.m12 && r.m12.na)) +
        deltaTd(r.d1w) + deltaTd(r.d3w) + "</tr>";
    }).join("");
    return upcomingHead(sortKey) + '<div class="cb-scroll"><table class="cb-table cb-rp cb-upcoming"><thead><tr><th>Next meeting</th><th>Bank</th><th class="cb-n">Policy rate</th>' +
      '<th class="cb-n" title="Probability of the move at the next meeting (EXACT / CURVE only; one decimal)">Probability</th><th title="Direction of the priced move">Hike/Cut</th>' +
      '<th class="cb-n" title="' + esc(MOVE_TIP) + '">Priced move (bp)</th><th title="n or n+1 moves of 25 bp, whichever is more likely">Most likely</th>' +
      '<th class="cb-n" title="bp priced vs the current rate after the last meeting within 12 months">12M (bp)</th>' +
      '<th class="cb-n" title="Change of the 12-month implied level over 5 business days, bp">Δ 1w</th><th class="cb-n" title="Change of the 12-month implied level over 15 business days, bp">Δ 3w</th>' +
      "</tr></thead><tbody>" + rows + "</tbody></table></div>" +
      '<div class="cb-sub">≈ = estimate (no contract isolates the meeting). ' + NA + " = not available, hover for the reason. Click a row for the bank page.</div>";
  }
  function upcomingListHtml(paths, sortKey) {
    sortKey = sortKey || "next";
    return upcomingHead(sortKey) + '<div class="ph-list cb-ph-list">' + sortRows(compareRows(paths), sortKey).map(function (r) {
      const u = upcomingCells(r), pt = r.point;
      const move = u.move === null ? NA : u.joint ? '<span class="cb-joint muted" title="' + esc(jointTip(pt)) + '">' + esc(u.move) + "</span>" : '<span class="' + tone(pt.step_bp).trim() + '">' + esc(u.move) + "</span>";
      const bits = [u.prob ? u.prob + (u.dir ? " " + u.dir : "") : null, u.likely].filter(Boolean).join(DOT);
      const d = function (m) { return m && isNum(m.v) ? '<span class="' + tone(m.v).trim() + '">' + esc((m.flag === "ESTIMATE" ? "≈ " : "") + fmtSigned(m.v, 1)) + "</span>" : NA; };
      return '<a class="ph-row cb-ph-cmp cb-ph-up" href="' + esc(r.href) + '"><span class="cb-swatch" data-ccy="' + esc(r.ccy) + '"></span>' +
        '<span class="cb-ph-bank"><b>' + esc(r.name) + '</b><span class="cb-ph-sub">' + (r.next ? fmtDate(r.next.decision) : NA) + DOT + esc(currentText(r.current)) + "</span></span>" +
        '<span class="cb-ph-r"><b class="cb-mono">' + move + '</b><span class="cb-ph-sub">' + esc(bits || NA) + '</span><span class="cb-ph-sub">12M ' +
        (u.m12 ? '<span class="' + tone(r.m12.cum_bp).trim() + '">' + esc(u.m12) + "</span>" : NA) + DOT + "1w " + d(r.d1w) + DOT + "3w " + d(r.d3w) + "</span></span>" + CHEV + "</a>";
    }).join("") + '</div><p class="ph-note">Priced move and 12M in bp. ≈ = estimate (no contract isolates the meeting). Tap a bank for its page.</p>';
  }

  // Pairs over 12 months, from the same paths as Compare: who do the rate expectations favour?
  const PAIRS_HELP = "Rate gap = base rate − quote rate. 12M change = bp priced by the base bank minus bp priced by the quote bank over the next 12 months: positive favors the base currency, negative the quote currency.";
  const PAIR_SORTS = [["change", "|12M change|"], ["gap", "Rate gap now"]];
  function legsWhy(legs) {                                           // "EUR: ...; USD: ..." - once when both legs share the reason
    const bad = legs.filter(function (t) { return t[1]; });
    if (bad.length === 2 && shortNa(bad[0][1]) === shortNa(bad[1][1])) return shortNa(bad[0][1]) + " (both legs)";
    return bad.map(function (t) { return t[0] + ": " + shortNa(t[1]); }).join("; ") || "n/a";
  }
  function pairCalc(p, paths) {
    const B = paths[p.base] || {}, Q = paths[p.quote] || {};
    const rate = function (x) { return x.current && isNum(x.current.rate) ? Number(x.current.rate) : null; };
    const m12 = function (x) { return x.m12 && isNum(x.m12.cum_bp) ? Number(x.m12.cum_bp) : null; };
    const rb = rate(B), rq = rate(Q), cb = m12(B), cq = m12(Q);
    const gap = rb !== null && rq !== null ? (rb - rq) * 100 : null;
    const change = cb !== null && cq !== null ? cb - cq : null;
    const lean = function (v) { return !isNum(v) || Math.abs(v) < 0.05 ? null : v > 0 ? p.base : p.quote; };
    const delta = function (k) {
      const a = B.delta && B.delta[k], b = Q.delta && Q.delta[k], va = !!(a && isNum(a.v)), vb = !!(b && isNum(b.v));
      if (va && vb) { const v = Number(a.v) - Number(b.v); return { v: v, est: a.flag === "ESTIMATE" || b.flag === "ESTIMATE", toward: lean(v), na: null }; }
      return { v: null, est: false, toward: null, na: legsWhy([[p.base, va ? null : (a && a.na) || "n/a"], [p.quote, vb ? null : (b && b.na) || "n/a"]]) };
    };
    const leg = function (x, c, ccy) { return (x.short || ccy) + " " + (c === null ? NA : (x.m12.est ? "≈ " : "") + fmtSigned(c, 0)); };
    return { pair: p.pair, display: p.display, href: p.href, base: p.base, quote: p.quote, names: (B.short || p.base) + " vs " + (Q.short || p.quote),
             gap: gap, gapNa: gap === null ? legsWhy([[p.base, rb === null ? "no current rate" : null], [p.quote, rq === null ? "no current rate" : null]]) : null,
             rates: (rb === null ? NA : fmtRate(rb) + "%") + " vs " + (rq === null ? NA : fmtRate(rq) + "%"),
             priced: leg(B, cb, p.base) + DOT + leg(Q, cq, p.quote),
             change: change, est: !!((B.m12 && B.m12.est) || (Q.m12 && Q.m12.est)), favors: lean(change),
             changeNa: change === null ? legsWhy([[p.base, cb === null ? (B.m12 && B.m12.na) || "n/a" : null], [p.quote, cq === null ? (Q.m12 && Q.m12.na) || "n/a" : null]]) : null,
             gap12: gap !== null && change !== null ? gap + change : null, d1w: delta("1w"), d3w: delta("3w") };
  }
  function sortPairRows(rows, key) {
    const val = function (r) { return key === "gap" ? r.gap : isNum(r.change) ? Math.abs(r.change) : null; };
    return rows.map(function (r, i) { return [r, i]; }).sort(function (a, b) {
      const x = val(a[0]), y = val(b[0]);
      if (!isNum(x) && !isNum(y)) return a[1] - b[1];
      if (!isNum(x)) return 1;
      if (!isNum(y)) return -1;
      return y - x || a[1] - b[1];
    }).map(function (t) { return t[0]; });
  }
  function favorsText(r) { return r.favors ? "favors " + r.favors : "neutral"; }
  function towardTip(r, d) { return d.toward ? "toward " + d.toward + " (Δ of the base bank's 12M minus Δ of the quote bank's 12M)" : "no change"; }
  function pairsTableHtml(rows, key) {
    key = key || "change";
    const body = sortPairRows(rows, key).map(function (r) {
      const ap = r.est ? "≈ " : "";
      const sub = function (t) { return '<div class="cb-sub2">' + esc(t) + "</div>"; };
      return '<tr class="cb-row" data-href="' + esc(r.href) + '" tabindex="0"><td class="cb-bank"><a href="' + esc(r.href) + '"><b>' + esc(r.display) + "</b></a>" + sub(r.names) + "</td>" +
        (isNum(r.gap) ? '<td class="cb-n">' + fmtSigned(r.gap, 1) + sub(r.rates) + "</td>" : naTd(r.gapNa)) +
        '<td class="cb-n cb-priced">' + esc(r.priced) + "</td>" +
        (isNum(r.change) ? txtTd(r.change, esc(ap + fmtSigned(r.change, 1)) + sub(favorsText(r)), "") : naTd(r.changeNa)) +
        (isNum(r.gap12) ? '<td class="cb-n">' + esc(ap + fmtSigned(r.gap12, 1)) + "</td>" : naTd(r.gapNa || r.changeNa)) +
        deltaTd(r.d1w, towardTip(r, r.d1w)) + deltaTd(r.d3w, towardTip(r, r.d3w)) + "</tr>";
    }).join("");
    return '<div class="cb-tablehead"><h3>Pairs <small class="muted">next 12 months</small></h3>' + sortPills(PAIR_SORTS, key) + "</div>" +
      '<div class="cb-scroll"><table class="cb-table cb-rp cb-pairs12"><thead><tr><th>Pair</th><th class="cb-n" title="Base rate minus quote rate, bp">Rate gap now (bp)</th>' +
      '<th class="cb-n" title="bp priced by each bank over the next 12 months (the 12M of Compare)">Priced 12M</th>' +
      '<th class="cb-n" title="bp priced by the base bank minus bp priced by the quote bank over the next 12 months">12M change (bp)</th>' +
      '<th class="cb-n" title="Rate gap now + 12M change">Rate gap in 12M (bp)</th>' +
      '<th class="cb-n" title="Change of the 12M change over 5 business days: the base bank’s Δ 1w minus the quote bank’s">Δ 1w</th>' +
      '<th class="cb-n" title="Change of the 12M change over 15 business days: the base bank’s Δ 3w minus the quote bank’s">Δ 3w</th></tr></thead><tbody>' + body + "</tbody></table></div>" +
      '<div class="cb-sub">≈ = at least one leg is an estimate (NZD; CAD after December). ' + NA + " = not available, hover for the reason.</div>";
  }
  function pairsListHtml(rows, key) {
    key = key || "change";
    return '<div class="cb-tablehead"><h3>Pairs</h3>' + sortPills(PAIR_SORTS, key) + '</div><div class="ph-list cb-ph-list">' + sortPairRows(rows, key).map(function (r) {
      const ap = r.est ? "≈ " : "";
      return '<a class="ph-row cb-ph-pair" href="' + esc(r.href) + '"><span class="cb-ph-bank"><b>' + esc(r.display) + '</b><span class="cb-ph-sub">' + esc(r.names) + DOT + "gap " + (isNum(r.gap) ? fmtSigned(r.gap, 1) : NA) + " → " + (isNum(r.gap12) ? ap + fmtSigned(r.gap12, 1) : NA) + "</span></span>" +
        '<span class="cb-ph-r"><b class="cb-mono' + tone(r.change) + '">' + (isNum(r.change) ? esc(ap + fmtSigned(r.change, 1)) : NA) + '</b><span class="cb-ph-sub">' + esc(isNum(r.change) ? favorsText(r) : shortNa(r.changeNa)) + "</span>" +
        '<span class="cb-ph-sub">1w ' + (isNum(r.d1w.v) ? fmtSigned(r.d1w.v, 1) : NA) + DOT + "3w " + (isNum(r.d3w.v) ? fmtSigned(r.d3w.v, 1) : NA) + "</span></span>" + CHEV + "</a>";
    }).join("") + '</div><p class="ph-note">' + esc(PAIRS_HELP) + "</p>";
  }
  function pairCards(r) {
    const ap = r.est ? "≈ " : "";
    const card = function (title, v, html, sub, tip, cls) {
      return '<section class="cb-card cb-pcard' + (cls ? " " + cls : "") + '"' + (tip ? ' title="' + esc(tip) + '"' : "") + "><h3>" + esc(title) + '</h3><div class="cb-bigrate cb-mono' + (v === null ? "" : tone(v)) + '">' + html + '</div><div class="cb-sub">' + esc(sub) + "</div></section>";
    };
    const na = function (why) { return '<span class="cb-na" title="' + esc(why) + '">' + NA + "</span>"; };
    const dCard = function (k, label) {
      const d = r[k];
      return card(label, isNum(d.v) ? d.v : null, isNum(d.v) ? esc((d.est ? "≈ " : "") + fmtSigned(d.v, 1) + " bp") : na(d.na), isNum(d.v) ? (d.toward ? "toward " + d.toward : "no change") : shortNa(d.na), isNum(d.v) ? towardTip(r, d) : d.na);
    };
    return '<div class="cb-grid cb-pcards cb-paircards">' +
      card("Rate gap now", null, isNum(r.gap) ? esc(fmtSigned(r.gap, 1) + " bp") : na(r.gapNa), r.rates) +
      card("Priced 12M", null, esc(r.priced), "bp priced by each bank over the next 12 months", "", "cb-pcard-text") +
      card("12M change", isNum(r.change) ? r.change : null, isNum(r.change) ? esc(ap + fmtSigned(r.change, 1) + " bp") : na(r.changeNa), isNum(r.change) ? favorsText(r) : shortNa(r.changeNa)) +
      card("Rate gap in 12M", null, isNum(r.gap12) ? esc(ap + fmtSigned(r.gap12, 1) + " bp") : na(r.gapNa || r.changeNa), "rate gap now + 12M change") +
      dCard("d1w", "Δ 1w") + dCard("d3w", "Δ 3w") + "</div>";
  }
  function upNextHtml(nd) {
    if (!nd) return "";
    const mv = moveText(nd.point);
    return '<div class="cb-upnext"><span class="cb-kicker">Up next</span> <span class="cb-swatch" data-ccy="' + esc(nd.ccy) + '"></span><a href="' + esc(nd.href) + '"><b>' + esc(nd.ccy) + "</b>" + DOT + esc(nd.short) + "</a>" +
      DOT + '<span class="cb-local-time" data-utc="' + esc((nd.time && nd.time.utc) || nd.sort_utc || "") + '" data-date="' + esc(nd.decision) + '" data-tbd="' + (nd.time && nd.time.tbd ? "1" : "") + '">' + fmtDate(nd.decision) + "</span>" +
      DOT + cdSpan(nd) + DOT + (mv ? "<b>" + esc(mv) + "</b>" : jointText(nd.point) ? jointSpan(nd.point) : naSpan(nd.point ? nd.point.na || nd.point.step_na : "n/a") + ' <span class="muted">' + esc(shortNa((nd.point && (nd.point.na || nd.point.step_na)) || "n/a")) + "</span>") + "</div>";
  }
  function localTimes() {
    document.querySelectorAll(".cb-local-time").forEach(function (el) {
      if (!el.dataset.utc || el.dataset.tbd) return;
      const d = new Date(el.dataset.utc);
      el.textContent = fmtDate(el.dataset.date) + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0") + " your time" + (el.dataset.tbd ? " (window start)" : "");
    });
  }
  function bankBar(active) {
    const items = [{ k: "compare", label: "Compare", href: CFG.urls.overview_page }, { k: "stance", label: "Stance", href: CFG.urls.overview_page + "#stance" },
                   { k: "pairs", label: "Pairs", href: CFG.urls.overview_page + "#pairs" }]
      .concat(BANK_ORDER.map(function (c) { return { k: c, label: c, href: bankUrlPage(c) }; }));
    return '<nav class="cb-bankbar" aria-label="Central banks">' + items.map(function (it) {
      return '<a class="cb-bankbar-item' + (it.k === active ? " active" : "") + '" data-k="' + esc(it.k) + '" href="' + esc(it.href) + '"' + (it.k === active ? ' aria-current="page"' : "") + ">" +
        (it.k === "compare" || it.k === "pairs" || it.k === "stance" ? "" : '<span class="cb-swatch" data-ccy="' + esc(it.k) + '"></span>') + esc(it.label) + "</a>";
    }).join("") + "</nav>";
  }
  function bankUrlPage(c) { return CFG.urls.overview_page.replace(/\.html$/, "").replace(/\/$/, "") + "/" + c.toLowerCase() + ".html"; }

  // ---- hover (RP style) -----------------------------------------------------------------------------------------------------------
  // A dashed vertical at the date under the cursor (bank page: the nearest meeting), a dashed horizontal at the cursor with its value
  // on the Y axis, and an HTML card beside the cursor that never leaves the chart. Mouse, tap and drag. The callbacks sit on the chart
  // instance ($cb), not in its options (Chart.js would call functions in plugin options as scriptable options).
  function dayOf(x) { return Math.floor(x / DAY_MS) * DAY_MS; }
  function isoOf(x) { return new Date(x).toISOString().slice(0, 10); }
  const crosshair = {
    id: "cbCross",
    afterEvent: function (chart, args) {
      const o = chart.$cb, e = args.event, a = chart.chartArea;
      if (!o || !a) return;
      const inside = e.x !== null && e.x >= a.left && e.x <= a.right && e.y >= a.top && e.y <= a.bottom;
      if (e.type === "mouseout" || !inside) {
        if (chart.$cross) { chart.$cross = null; o.hide(); args.changed = true; }
        return;
      }
      if (["mousemove", "touchstart", "touchmove", "click"].indexOf(e.type) < 0) return;
      let xv = chart.scales.x.getValueForPixel(e.x);
      if (o.snap) { xv = o.snap(xv); if (xv === null) return; }
      chart.$cross = { x: chart.scales.x.getPixelForValue(xv), y: e.y, xv: xv, yv: chart.scales.y.getValueForPixel(e.y) };
      o.show(chart, chart.$cross);
      args.changed = true;
    },
    afterDatasetsDraw: function (chart) {
      const cr = chart.$cross, o = chart.$cb;
      if (!cr || !o) return;
      const c = chart.ctx, a = chart.chartArea, col = colors();
      c.save();
      c.setLineDash([4, 4]); c.lineWidth = 1; c.strokeStyle = withAlpha(col.fg, 0.55);
      c.beginPath(); c.moveTo(cr.x, a.top); c.lineTo(cr.x, a.bottom); c.moveTo(a.left, cr.y); c.lineTo(a.right, cr.y); c.stroke();
      c.setLineDash([]);
      const txt = o.yLabel(cr.yv);
      c.font = "600 11px system-ui, sans-serif";
      const w = c.measureText(txt).width + 10, h = 18, x = Math.max(0, a.left - w - 2);
      c.fillStyle = col.fg; c.fillRect(x, cr.y - h / 2, w, h);
      c.fillStyle = col.surface; c.textBaseline = "middle"; c.textAlign = "left"; c.fillText(txt, x + 5, cr.y + 0.5);
      c.restore();
    }
  };
  function tipBox(canvas) {
    const wrap = canvas.parentNode;
    let el = wrap.querySelector(".cb-tt");
    if (!el) { el = document.createElement("div"); el.className = "cb-tt"; el.setAttribute("role", "status"); wrap.appendChild(el); }
    el.hidden = true;
    return el;
  }
  function placeTip(el, canvas, cr, html) {
    el.innerHTML = html;
    el.hidden = false;
    const W = el.parentNode.clientWidth, H = el.parentNode.clientHeight, w = el.offsetWidth, h = el.offsetHeight;
    const x = cr.x + canvas.offsetLeft, y = cr.y + canvas.offsetTop;
    let left = x + 14;
    if (left + w > W) left = x - w - 14;                               // flip to the left of the cursor near the right edge
    left = Math.max(0, Math.min(W - w, left));
    el.style.left = left + "px";
    el.style.top = Math.max(0, Math.min(H - h, y - h / 2)) + "px";
  }
  function hoverChart(chart, canvas, o) {
    const el = tipBox(canvas);
    chart.$cb = { snap: o.snap || null, yLabel: o.yLabel,
                  show: function (ch, cr) { placeTip(el, canvas, cr, o.html(ch, cr)); if (o.after) o.after(ch, cr); },
                  hide: function () { el.hidden = true; if (o.after) o.after(chart, null); } };
  }

  // the value of one bank at a date: the rate in force then = the last meeting decided on or before the date (0 bp before the first);
  // a meeting no contract isolates is n/a ("—"), an estimate carries ≈
  function bankValueAt(pj, t) {
    const cur = pj.current && isNum(pj.current.rate) ? Number(pj.current.rate) : null;
    let p = null;
    (pj.points || []).forEach(function (x) { if (ms(x.meeting) <= t) p = x; });
    if (!p) return { bp: 0, level: cur, est: false, na: null, meeting: null };
    if (!isNum(p.rate) || !isNum(p.cum_bp)) return { bp: null, level: null, est: !!p.est, na: p.na || p.step_na || "n/a", meeting: p.meeting };
    return { bp: Number(p.cum_bp), level: Number(p.rate), est: !!p.est, na: null, meeting: p.meeting };
  }
  function hoverRow(ccy, name, v, mode) {
    if (!isNum(v.bp)) return { ccy: ccy, name: name, value: NA, hikes: "", na: v.na, bp: null, level: null, text: name + " " + NA };
    const ap = v.est ? "≈ " : "", bp = fmtSigned(v.bp, 1) + " bp";
    const value = ap + (mode === "level" ? (isNum(v.level) ? fmtRate(v.level, 2) + "%" : NA) + DOT + bp : bp), hikes = hikesText(v.bp);
    return { ccy: ccy, name: name, value: value, hikes: hikes, na: null, bp: v.bp, level: v.level, est: v.est, text: name + " " + value + DOT + hikes };
  }
  // every bank shown at a date, highest first (n/a last); banks hidden in the legend are left out
  function compareHoverRows(paths, t, mode, hidden) {
    hidden = hidden || {};
    const key = function (r) { return mode === "level" ? r.level : r.bp; };
    return BANK_ORDER.filter(function (c) { return paths[c] && paths[c].points && !hidden[c]; }).map(function (c) {
      return hoverRow(c, paths[c].short || c, bankValueAt(paths[c], t), mode);
    }).sort(function (a, b) {
      const x = key(a), y = key(b);
      if (!isNum(x) && !isNum(y)) return BANK_ORDER.indexOf(a.ccy) - BANK_ORDER.indexOf(b.ccy);
      if (!isNum(x)) return 1;
      if (!isNum(y)) return -1;
      return y - x || BANK_ORDER.indexOf(a.ccy) - BANK_ORDER.indexOf(b.ccy);
    });
  }
  // the pair page: the rate gap (base − quote) at a date, from the two levels in force then
  function pairGapAt(B, Q, t) {
    const vb = bankValueAt(B, t), vq = bankValueAt(Q, t);
    if (!isNum(vb.level) || !isNum(vq.level)) return { bp: null, est: false, na: vb.na || vq.na || "n/a" };
    return { bp: (vb.level - vq.level) * 100, est: vb.est || vq.est, na: null };
  }
  function gapRow(B, Q, t) {
    const g = pairGapAt(B, Q, t);
    return { ccy: null, name: "Gap", value: isNum(g.bp) ? (g.est ? "≈ " : "") + fmtSigned(g.bp, 1) + " bp" : NA, hikes: "", na: g.na, bp: g.bp,
             text: "Gap " + (isNum(g.bp) ? (g.est ? "≈ " : "") + fmtSigned(g.bp, 1) + " bp" : NA) };
  }
  function ttRowsHtml(rows) {
    return rows.map(function (r) {
      return '<div class="cb-tt-row"' + (r.na ? ' title="' + esc(shortNa(r.na)) + '"' : "") + '><span class="cb-tt-sq" style="background:' + (r.ccy ? bankColor(r.ccy) : "currentColor") + '"></span><b>' + esc(r.name) + "</b> " +
        '<span class="cb-tt-v">' + esc(r.value) + "</span>" + (r.hikes ? '<span class="cb-tt-h">' + DOT + esc(r.hikes) + "</span>" : "") + "</div>";
    }).join("");
  }
  function tipTitle(t, asofMs) { return t <= dayOf(asofMs) ? "Now" + DOT + fmtDate(isoOf(t)) : fmtDate(isoOf(t)); }

  // the Compare chart: every bank, bp vs now (or the level), one point per decision, estimates hollow + dashed
  function compareChartModel(paths, mode) {
    const series = [];
    BANK_ORDER.forEach(function (c) {
      const pj = paths[c];
      if (!pj || !pj.points) return;
      const x0 = ms(pj.asof), y0 = mode === "level" ? pj.current.rate : 0;
      if (mode === "level" && !isNum(y0)) return;
      const data = [{ x: x0, y: y0, start: true }];
      pj.points.forEach(function (p) {
        if (!isNum(p.rate) || !isNum(p.cum_bp)) return;
        data.push({ x: ms(p.meeting), y: mode === "level" ? Number(p.rate) : Number(p.cum_bp), est: !!p.est, p: p });
      });
      if (data.length > 1) series.push({ ccy: c, short: pj.short, data: data });
    });
    return series;
  }
  const directLabels = {
    id: "cbDirectLabels",
    afterDatasetsDraw: function (chart) {
      const c = chart.ctx, fg = colors().fg, labels = [];
      chart.data.datasets.forEach(function (ds, i) {
        const meta = chart.getDatasetMeta(i);
        if (meta.hidden || !meta.data.length || !ds.cbLabel || !chart.isDatasetVisible(i)) return;
        const el = meta.data[meta.data.length - 1];
        labels.push({ text: ds.cbLabel, x: el.x + 6, y: el.y });
      });
      labels.sort(function (a, b) { return a.y - b.y; });
      for (let i = 1; i < labels.length; i++) if (labels[i].y - labels[i - 1].y < 12) labels[i].y = labels[i - 1].y + 12;   // no overlap
      c.save(); c.font = "600 11px system-ui, sans-serif"; c.fillStyle = fg; c.textBaseline = "middle";
      labels.forEach(function (l) { c.fillText(l.text, l.x, l.y); });
      c.restore();
    }
  };
  // ---- the bank legend above the chart (Compare, pair page): one toggle per bank, in BANK_ORDER --------------------------------------
  // The state is pure (tests): `hidden` = banks taken off the chart, its end label and the hover card; `focus` = the bank under the mouse
  // (the other lines fade to 20%), which never changes `hidden`.
  function legendState(ccys) {
    const st = { ccys: BANK_ORDER.filter(function (c) { return ccys.indexOf(c) >= 0; }), hidden: {}, focus: null };
    st.toggle = function (c) { if (st.ccys.indexOf(c) < 0) return; if (st.hidden[c]) delete st.hidden[c]; else st.hidden[c] = true; };
    st.showAll = function () { st.hidden = {}; };
    st.anyHidden = function () { return Object.keys(st.hidden).length > 0; };
    st.hover = function (c) { st.focus = c && st.ccys.indexOf(c) >= 0 && !st.hidden[c] ? c : null; };
    st.alpha = function (c) { return st.focus && st.focus !== c ? 0.2 : 1; };
    return st;
  }
  function legendHtml(st, names) {
    names = names || {};
    return '<div class="cb-legend" role="group" aria-label="Banks on the chart">' + st.ccys.map(function (c) {
      const off = !!st.hidden[c];
      return '<button type="button" class="cb-lg-item' + (off ? " off" : "") + '" data-ccy="' + esc(c) + '" aria-pressed="' + (off ? "false" : "true") + '" title="' +
        esc((off ? "Show " : "Hide ") + (names[c] || c)) + '"><span class="cb-lg-line" data-ccy="' + esc(c) + '" aria-hidden="true"></span>' + esc(c) + "</button>";
    }).join("") + (st.anyHidden() ? '<button type="button" class="cb-lg-all">Show all</button>' : "") + "</div>";
  }
  // a chart line in the legend focus: full colour for the focused bank (or every bank), 20% for the others
  function fadeColor(col, a) { return a >= 1 ? col : withAlpha(col, a); }
  function applyFocus(chart, st) {
    if (!chart) return;
    const c = colors();
    chart.data.datasets.forEach(function (d) {
      const a = st.alpha(d.cbCcy), col = fadeColor(d.cbColor, a);
      d.borderColor = col; d.backgroundColor = col; d.pointBorderColor = col;
      d.pointBackgroundColor = d.data.map(function (p) { return p.est ? c.surface : col; });
    });
    chart.update("none");
  }
  function wireLegend(box, st, names, redraw, chartOf) {
    const paint = function () {
      box.innerHTML = legendHtml(st, names);
      box.querySelectorAll(".cb-lg-line").forEach(function (el) { el.style.background = st.hidden[el.dataset.ccy] ? "" : bankColor(el.dataset.ccy); });
      box.querySelectorAll(".cb-lg-item").forEach(function (b) {
        b.addEventListener("click", function () { st.toggle(b.dataset.ccy); st.focus = null; paint(); redraw(); });
        const on = function () { st.hover(b.dataset.ccy); applyFocus(chartOf(), st); };
        const out = function () { st.hover(null); applyFocus(chartOf(), st); };
        b.addEventListener("mouseenter", on); b.addEventListener("focus", on);
        b.addEventListener("mouseleave", out); b.addEventListener("blur", out);
      });
      const all = box.querySelector(".cb-lg-all");
      if (all) all.addEventListener("click", function () { st.showAll(); paint(); redraw(); });
    };
    paint();
    return paint;
  }
  // Compare (every bank) and the pair page (two banks + a "Gap" row in the card): opts.extra(t) adds rows below the banks;
  // opts.legend = legendState: hidden banks are not drawn (line, end label, hover row)
  function drawCompareChart(canvas, paths, mode, opts) {
    opts = opts || {};
    const c = colors(), series = compareChartModel(paths, mode);
    const asof = Math.min.apply(null, series.map(function (s) { return s.data[0].x; }).concat([Date.now()]));
    const xMax = asof + 365 * DAY_MS + 20 * DAY_MS;
    const ds = series.map(function (s) {
      const col = bankColor(s.ccy);
      return { type: "line", label: s.ccy + " " + s.short, cbLabel: s.ccy, cbCcy: s.ccy, cbColor: col, hidden: !!(opts.legend && opts.legend.hidden[s.ccy]), data: s.data, borderColor: col, backgroundColor: col, borderWidth: 2, tension: 0, stepped: false,
               pointRadius: s.data.map(function (p) { return p.start ? 0 : 4; }), pointHoverRadius: 5, pointBorderWidth: 2, pointBorderColor: col,
               pointBackgroundColor: s.data.map(function (p) { return p.est ? c.surface : col; }),
               segment: { borderDash: function (ctx) { return ctx.p1.raw && ctx.p1.raw.est ? [6, 4] : undefined; } } };
    });
    const chart = new Chart(canvas, {
      data: { datasets: ds },
      plugins: [directLabels, crosshair],
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
        layout: { padding: { right: 34 } },
        interaction: { mode: "nearest", intersect: true },
        scales: Object.assign(baseScales(c, mode === "level" ? "Implied policy rate, %" : "bp priced vs the current rate"), { x: Object.assign(baseScales(c, "", true).x, { min: asof, max: xMax }) }),
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
          annotation: { annotations: mode === "level" ? {} : { zero: { type: "line", yMin: 0, yMax: 0, borderColor: withAlpha(c.muted, 0.6), borderWidth: 1 } } }
        }
      }
    });
    hoverChart(chart, canvas, {
      yLabel: function (v) { return mode === "level" ? Number(v).toFixed(2) + "%" : fmtSigned(v, 1); },
      html: function (ch, cr) {
        const t = dayOf(cr.xv), hidden = {};                   // a bank hidden by the legend is a hidden dataset
        ch.data.datasets.forEach(function (d, i) { if (!ch.isDatasetVisible(i)) hidden[d.cbCcy] = true; });
        const rows = compareHoverRows(paths, t, mode, hidden).concat(opts.extra ? opts.extra(t) : []);
        return '<div class="cb-tt-title">' + esc(tipTitle(t, asof)) + "</div>" + ttRowsHtml(rows);
      }
    });
    if (opts.legend && opts.legend.focus) applyFocus(chart, opts.legend);
    state.charts.push(chart);
    return chart;
  }
  // the bank page: cards, the Now / 1w / 3w chart and the per-meeting table
  // the Policy rate card (first of the four cards): the rate, its name, the overnight benchmark below
  function policyCard(d) {
    const pj = d.path, cur = pj.current, b = cur.benchmark;
    const on = b ? (b.estimated ? esc(b.name) : esc(b.name) + " " + (isNum(b.value) ? fmtRate(b.value, 3) + "%" : NA) + (b.date ? ' <span class="muted">(' + fmtDay(b.date) + ")</span>" : "")) : NA;
    return '<section class="cb-card cb-pcard cb-policycard"><h3>Policy rate</h3><div class="cb-bigrate">' + rateHtml(d.summary.rate) + "</div>" +
      '<div class="cb-sub">' + esc(d.bank.policy_rate || "") + "</div>" + (d.unverified.length ? '<div class="cb-badges">' + unverifiedChips(d) + "</div>" : "") +
      '<div class="cb-kicker cb-on-k">Overnight</div><div class="cb-sub">' + on + "</div></section>";
  }
  function probBar2(pt) {
    const pr = pt && pt.prob;
    if (!pr) return "";
    const cut = pr.dir === "cut", sgn = cut ? MINUS : "+", p = Number(pr.p) || 0;
    const lo = pr.n === 0 ? "Hold" : sgn + 25 * pr.n + " bp", hi = sgn + 25 * (pr.n + 1) + " bp";
    return '<div class="cb-prob2" role="img" aria-label="' + esc(lo + " " + pct(1 - p) + ", " + hi + " " + pct(p)) + '"><span class="cb-prob2-a" style="width:' + ((1 - p) * 100).toFixed(1) + '%">' + esc(lo) + " " + pct(1 - p) +
      '</span><span class="cb-prob2-b ' + (cut ? "cb-bar-dove" : "cb-bar-hawk") + '" style="width:' + (p * 100).toFixed(1) + '%">' + (p >= 0.3 ? esc(hi) + " " + pct(p) : p >= 0.12 ? pct(p) : "") + "</span></div>";
  }
  function infoTip(text) { return text ? ' <span class="cb-info" tabindex="0" role="note" title="' + esc(text) + '" aria-label="' + esc(text) + '">ⓘ</span>' : ""; }
  function pathCards(d) {
    const pj = d.path, n = pj.next, pt = firstPoint(pj), mv = moveText(pt), m12 = m12Text(pj.m12);
    const next = '<section class="cb-card cb-pcard"><h3>Next meeting</h3>' + (n ? '<div class="cb-bigdate">' + esc(weekday(n.decision)) + " " + fmtDate(n.decision) + ' <span class="cb-cdbig">' + cdSpan(n) + "</span></div>" +
      '<div class="cb-sub">' + esc(timeText(n.time) || "") + (n.effective ? DOT + "effective " + fmtDay(n.effective) : "") + " " + blackoutBadge(d.summary.next) + "</div>" +
      (mv ? '<div class="cb-bigrate ' + (pt.step_bp > 0 ? "cb-hawk" : pt.step_bp < 0 ? "cb-dove" : "") + '">' + esc(mv) + flagBadge(pt.flag) + "</div>" + probBar2(pt)
          : jointText(pt) ? '<div class="cb-sub">' + jointSpan(pt) + "</div>"
          : '<div class="cb-sub">' + NA + " " + esc(shortNa((pt && (pt.na || pt.step_na)) || "n/a")) + "</div>") : '<div class="cb-sub">' + NA + " no upcoming meeting</div>") + "</section>";
    const year = '<section class="cb-card cb-pcard"><h3>12 months</h3>' + (m12 ? '<div class="cb-bigrate ' + (pj.m12.cum_bp > 0 ? "cb-hawk" : pj.m12.cum_bp < 0 ? "cb-dove" : "") + '">' + esc(m12) + flagBadge(pj.m12.flag) + infoTip(pj.spread_note) + "</div>" +
      '<div class="cb-sub">Implied ' + fmtRate(pj.m12.rate, 3) + "% after the " + fmtDate(pj.m12.meeting) + " meeting</div>" : '<div class="cb-sub">' + NA + " " + esc(shortNa(pj.m12.na)) + "</div>") +
      "</section>";
    const one = function (k, label) {
      const m = pj.delta[k];
      return '<div class="cb-kv"><span>' + label + "</span>" + (m && isNum(m.v) ? valueSpan(m, 1, " bp") : '<span class="cb-na">' + esc(shortNa((m && m.na) || "n/a")) + "</span>") + "</div>";
    };
    const vs = function (k, fb) { const a = pj.history[k] && pj.history[k].asof; return a ? "vs " + fmtDay(a) : fb; };
    const rep = '<section class="cb-card cb-pcard"><h3>Repricing <small class="muted">12-month level</small></h3>' + one("1w", vs("1w", "vs 1 week ago")) + one("3w", vs("3w", "vs 3 weeks ago")) + "</section>";
    return '<div class="cb-grid cb-pcards cb-pcards4">' + policyCard(d) + next + year + rep + "</div>";
  }
  // the bank page chart: Current (the bank's colour, shaded against the current rate), 1w ago and 3w ago (two neutral colours that no
  // bank uses), solid lines with a point on each meeting; tabs above the chart show / hide a series, the hover snaps to the nearest meeting
  const SERIES = [["now", "Current"], ["1w", "1w ago"], ["3w", "3w ago"]];
  function seriesColor(key, ccy) {
    const c = colors();
    return key === "now" ? bankColor(ccy) : key === "1w" ? withAlpha(c.fg, 0.85) : withAlpha(c.muted, 0.75);
  }
  function seriesNa(pj, key) {                                       // why a series cannot be drawn (null = it can)
    if (key === "now") return pj.points.some(function (p) { return isNum(p.rate); }) ? null : pj.na || "no meeting priced";
    const h = pj.history[key];
    if (!h) return "no history";
    if (h.na) return h.na;
    return h.points.some(function (p) { return isNum(p.rate); }) ? null : "not priced on " + h.asof;
  }
  function seriesLabel(pj, key) {
    const h = key === "now" ? null : pj.history[key], s = SERIES.find(function (x) { return x[0] === key; });
    return s[1] + (h && h.asof ? DOT + fmtDay(h.asof) : "");
  }
  function seriesTabsHtml(pj, hidden) {
    return '<div class="cb-stabs" role="group" aria-label="Series">' + SERIES.map(function (s) {
      const k = s[0], na = seriesNa(pj, k), on = !na && !hidden[k];
      return '<button type="button" class="cb-stab' + (on ? " active" : "") + (na ? " cb-stab-na" : "") + '" data-series="' + k + '"' +
        (na ? ' aria-disabled="true" title="' + esc(shortNa(na)) + '"' : ' aria-pressed="' + (on ? "true" : "false") + '"') + ">" + esc(seriesLabel(pj, k)) + "</button>";
    }).join("") + "</div>";
  }
  function bankChartSeries(pj) {
    const cur = pj.current.rate, x0 = ms(pj.asof);
    const pts = function (list) { return list.filter(function (p) { return isNum(p.rate); }).map(function (p) { return { x: ms(p.meeting), y: Number(p.rate), est: !!p.est, m: p.meeting }; }); };
    const hist = function (k) { const h = pj.history[k]; return !h || h.na ? null : pts(h.points); };
    return { now: [{ x: x0, y: cur, start: true }].concat(pts(pj.points)), w1: hist("1w"), w3: hist("3w"), current: cur, meetings: pj.points.map(function (p) { return ms(p.meeting); }) };
  }
  // the hover card: one row per series shown (Current / 1w ago / 3w ago), Level · Δ vs today · Hikes; a series without a value = "—"
  // (Current: Δ = the cum_bp of the payload, as in Compare; 1w / 3w: their level minus today's rate)
  function bankTipRows(pj, meeting, hidden) {
    hidden = hidden || {};
    const cur = pj.current && isNum(pj.current.rate) ? Number(pj.current.rate) : null;
    return SERIES.filter(function (s) { return !hidden[s[0]]; }).map(function (s) {
      const k = s[0], label = s[1], h = k === "now" ? null : pj.history[k];
      const p = (k === "now" ? pj.points : (h && h.points) || []).find(function (x) { return x.meeting === meeting; });
      const na = h && h.na ? h.na : !p || !isNum(p.rate) ? (p && p.na) || "not priced" : null;
      if (na) return { key: k, label: label, level: NA, delta: "", hikes: "", na: na, text: label + " " + NA };
      const bp = k === "now" && isNum(p.cum_bp) ? Number(p.cum_bp) : cur === null ? null : (Number(p.rate) - cur) * 100;
      const level = (p.est ? "≈ " : "") + fmtRate(p.rate, 2) + "%", delta = isNum(bp) ? fmtSigned(bp, 1) + " bp" : NA, hikes = hikesText(bp);
      return { key: k, label: label, level: level, delta: delta, hikes: hikes, bp: bp, est: !!p.est, na: null, text: label + " " + level + DOT + delta + DOT + hikes };
    });
  }
  function bankTipHtml(pj, meeting, hidden) {
    return '<div class="cb-tt-title">' + fmtDate(meeting) + '</div><table class="cb-tt-table"><thead><tr><th></th><th>Level</th><th>Δ vs today</th><th>Hikes</th></tr></thead><tbody>' +
      bankTipRows(pj, meeting, hidden).map(function (r) {
        const sq = '<span class="cb-tt-sq" style="background:' + seriesColor(r.key, pj.ccy) + '"></span>';
        return r.na ? '<tr title="' + esc(shortNa(r.na)) + '"><td>' + sq + esc(r.label) + '</td><td colspan="3">' + NA + "</td></tr>"
          : "<tr><td>" + sq + esc(r.label) + "</td><td>" + esc(r.level) + '</td><td class="' + tone(r.bp).trim() + '">' + esc(r.delta) + "</td><td>" + esc(r.hikes) + "</td></tr>";
      }).join("") + "</tbody></table>";
  }
  function nearestX(xs, v) {
    let best = null;
    xs.forEach(function (x) { if (best === null || Math.abs(x - v) < Math.abs(best - v)) best = x; });
    return best;
  }
  function drawPathChart(canvas, pj, stepped, hidden, onMeeting) {
    hidden = hidden || {};
    const c = colors(), s = bankChartSeries(pj), col = seriesColor("now", pj.ccy);
    const st = stepped ? "before" : false;          // Chart.js "before" = horizontal at the previous level up to the point, then the jump (the rate changes at the meeting)
    const line = function (key, data, extra) {
      const cc = seriesColor(key, pj.ccy);
      return Object.assign({ type: "line", label: seriesLabel(pj, key), data: data, borderColor: cc, backgroundColor: cc, borderWidth: 2, stepped: st, tension: 0, hidden: !!hidden[key],
                             pointRadius: data.map(function (p) { return p.start ? 0 : 3.5; }), pointHoverRadius: 5, pointBorderColor: cc, pointBorderWidth: 2,
                             pointBackgroundColor: data.map(function (p) { return p.est ? c.surface : cc; }) }, extra || {});
    };
    const ds = [line("now", s.now, { backgroundColor: withAlpha(col, 0.14), fill: isNum(s.current) ? { value: s.current } : false, borderWidth: 2.4, order: 1 })];
    if (s.w1 && s.w1.length) ds.push(line("1w", s.w1, { order: 2 }));
    if (s.w3 && s.w3.length) ds.push(line("3w", s.w3, { order: 3, pointStyle: "rect" }));
    const xs = s.now.map(function (p) { return p.x; });
    const chart = new Chart(canvas, {
      data: { datasets: ds },
      plugins: [crosshair],
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
        interaction: { mode: "nearest", intersect: true },
        scales: Object.assign(baseScales(c, "Implied policy rate, %"), { x: Object.assign(baseScales(c, "", true).x, { min: Math.min.apply(null, xs), max: Math.max.apply(null, xs) + 15 * DAY_MS }) }),
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
          annotation: { annotations: isNum(s.current) ? { cur: { type: "line", yMin: s.current, yMax: s.current, borderColor: withAlpha(c.fg, 0.5), borderWidth: 1, borderDash: [2, 2],
                                                                label: { display: true, content: "current " + fmtRateAuto(s.current) + "%", position: "start", backgroundColor: withAlpha(c.surface, 0.9), color: c.muted, font: { size: 10 }, padding: 3 } } } : {} }
        }
      }
    });
    hoverChart(chart, canvas, {
      snap: function (v) { return s.meetings.length ? nearestX(s.meetings, v) : null; },
      yLabel: function (v) { return Number(v).toFixed(2) + "%"; },
      html: function (ch, cr) { return bankTipHtml(pj, isoOf(cr.xv), hidden); },
      after: function (ch, cr) { if (onMeeting) onMeeting(cr ? isoOf(cr.xv) : null); }
    });
    state.charts.push(chart);
    return chart;
  }
  function pathChartCard(d) {
    const pj = d.path;
    return '<section class="cb-card cb-chartcard"><h3>Implied path <small class="muted">next 12 months</small>' +
      '<span class="cb-toggle" role="group" aria-label="Line shape"><button type="button" class="cb-tbtn" data-shape="step" aria-pressed="false" title="Draw the path as steps (the rate changes at each meeting)">Step</button></span></h3>' +
      '<div id="cbSeriesTabs">' + seriesTabsHtml(pj, {}) + '</div><div class="cb-chart-wrap cb-chart-side"><canvas id="cbPathChart"></canvas></div>' + howToRead(esc(pathHelp(pj))) + "</section>";
  }
  function pathHelp(pj) {
    const bits = ["The rate implied after each meeting: now and 1 and 3 weeks ago", "shaded against the current rate (dotted line)", "click a tab above the chart to hide or show a series"];
    if (pj.points.some(function (p) { return p.est; })) bits.push("hollow points = estimates (no contract isolates the meeting, no probability)");
    return bits.join("; ") + ".";
  }
  function pathTableRows(pj) {
    const w1 = pj.history["1w"];
    return pj.points.map(function (p) {
      const hp = w1 && w1.points ? w1.points.find(function (x) { return x.meeting === p.meeting; }) : null;
      const dW = hp && isNum(hp.rate) && isNum(p.rate) ? (p.rate - hp.rate) * 100 : null;
      const moves = isNum(p.cum_bp) ? (p.est ? "≈ " : "") + hikesText(p.cum_bp) : null;
      return { meeting: p.meeting, est: !!p.est, rate: isNum(p.rate) ? (p.est ? "≈ " : "") + fmtRate(p.rate, 3) + "%" : null, prob: p.est ? null : probText(p.prob) || null,
               moves: moves, cum: p.cum_bp, dW: dW, na: p.na, dWna: (w1 && w1.na) || (hp ? hp.na : "not priced 1 week ago"), flag: p.flag };
    });
  }
  // a meeting of the calendar: the short projections label ("SEP", full name in its tooltip) and the tooltip of the date
  const PROJ_SHORT = { "Outlook Report": "Outlook", "Eurosystem staff projections": "Projections", "Conditional inflation forecast": "Forecast" };
  function projLabel(name) {
    if (!name) return null;
    if (PROJ_SHORT[name]) return PROJ_SHORT[name];
    const base = name.replace(/\s*\(.*\)\s*$/, ""), caps = base.split(/\s+/).filter(function (w) { return /^[A-Z]/.test(w); });
    return base.length <= 5 ? base : caps.length >= 2 ? caps.map(function (w) { return w[0]; }).join("") : base.split(/\s+/)[0];
  }
  function meetingInfo(m) {
    if (!m) return { label: null, name: null, tip: "" };
    const b = m.blackout, bits = ["Decision " + weekday(m.decision) + " " + fmtDate(m.decision) + (timeText(m.time) ? ", " + timeText(m.time) : "")];
    if (m.effective) bits.push("effective " + fmtDate(m.effective));
    if (m.has_presser) bits.push("press conference" + (m.conference_local ? " " + m.conference_local + " " + ((m.time && m.time.abbr) || "") : ""));
    if (b) bits.push("blackout " + fmtDay(b.start.slice(0, 10)) + " → " + fmtDay(b.end.slice(0, 10)) + (b.precision === "approximate" || !b.verified ? " (approximate)" : ""));
    if (m.has_projections) bits.push("projections: " + (m.projections_name || "yes"));
    return { label: m.has_projections ? projLabel(m.projections_name) || "Proj." : null, name: m.has_projections ? m.projections_name || "projections" : null, tip: bits.join(" · ").trim() };
  }
  // EXACT / CURVE are the normal case: no badge in the table, the method is in the tooltip of the date; any other method keeps its badge
  const QUIET_FLAGS = ["EXACT", "CURVE"];
  function methodText(flag) { const f = ((state.meta || {}).flags || {})[flag] || {}; return (f.label || flag) + (f.help ? " (" + f.help + ")" : ""); }
  function pathTableHtml(pj, calendar) {
    const cal = {};
    (calendar || []).forEach(function (m) { cal[m.decision] = m; });
    const rows = pathTableRows(pj).map(function (r) {
      const ap = r.est ? "≈ " : "", mi = meetingInfo(cal[r.meeting]), quiet = QUIET_FLAGS.indexOf(r.flag) >= 0;
      const tip = [mi.tip, quiet ? "Method: " + methodText(r.flag) : ""].filter(Boolean).join(" · ");
      return '<tr data-meeting="' + esc(r.meeting) + '"' + (r.est ? ' class="cb-est"' : "") + '><td class="cb-date"><span' + (tip ? ' class="cb-mdate" title="' + esc(tip) + '"' : "") + ">" + fmtDate(r.meeting) + "</span>" +
        (mi.label ? ' <span class="econ-flag cb-proj" title="' + esc(mi.name) + '">' + esc(mi.label) + "</span>" : "") + (quiet ? "" : flagBadge(r.flag)) + "</td>" +
        (r.rate ? '<td class="cb-n">' + esc(r.rate) + "</td>" : naTd(r.na)) +
        '<td class="cb-n">' + (r.prob ? esc(r.prob) : r.rate ? '<span class="muted" title="' + esc(r.est ? "estimate: no probability" : "no per-meeting probability") + '">' + NA + "</span>" : naSpan(r.na)) + "</td>" +
        (r.moves ? "<td>" + esc(r.moves) + "</td>" : "<td>" + naSpan(r.na) + "</td>") +
        (isNum(r.cum) ? txtTd(r.cum, esc(ap + fmtSigned(r.cum, 1))) : naTd(r.na)) +
        (isNum(r.dW) ? txtTd(r.dW, esc(ap + fmtSigned(r.dW, 1))) : naTd(r.dWna)) + "</tr>";
    }).join("");
    return '<section class="cb-card cb-pathtablecard" id="cbPathTable"><h3>By meeting <small class="muted">next 12 months</small></h3><div class="cb-scroll"><table class="cb-table cb-rp cb-pathtable"><thead><tr><th>Meeting</th><th class="cb-n">Implied rate</th>' +
      '<th class="cb-n" title="EXACT / CURVE only: whole 25 bp moves plus the probability of one more">Probability</th><th title="Cumulative 25 bp moves priced by this meeting">Hikes</th><th class="cb-n">Δ vs now (bp)</th><th class="cb-n" title="Change of the implied rate at this meeting over 5 business days">Δ vs 1w (bp)</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="6" class="muted">' + esc(pj.na || "no meeting in the next 12 months") + "</td></tr>") + "</tbody></table></div><div class=\"cb-sub\">≈ = estimate. " + NA + " = not available, hover for the reason.</div></section>";
  }
  // linked hover between "By meeting" and the chart: a row shows the crosshair + card at its meeting and enlarges the point; the chart
  // highlights the row of the meeting under the cursor
  function highlightRow(scope, meeting) {
    if (!scope) return;
    scope.querySelectorAll("tr[data-meeting]").forEach(function (tr) { tr.classList.toggle("cb-row-hl", tr.dataset.meeting === meeting); });
  }
  function showAtMeeting(chart, meeting) {
    if (!chart || !chart.$cb || !chart.chartArea) return false;
    const x = ms(meeting), a = chart.chartArea, ds = chart.data.datasets[0];
    const i = ds && !ds.hidden ? ds.data.findIndex(function (p) { return p.x === x && !p.start; }) : -1;
    const y = i >= 0 ? chart.scales.y.getPixelForValue(ds.data[i].y) : (a.top + a.bottom) / 2;
    chart.$cross = { x: chart.scales.x.getPixelForValue(x), y: y, xv: x, yv: chart.scales.y.getValueForPixel(y) };
    chart.setActiveElements(i >= 0 ? [{ datasetIndex: 0, index: i }] : []);
    chart.$cb.show(chart, chart.$cross);
    chart.update("none");
    return true;
  }
  function hideAtMeeting(chart) {
    if (!chart || !chart.$cb) return;
    chart.$cross = null;
    chart.setActiveElements([]);
    chart.$cb.hide();
    chart.update("none");
  }
  function wireLinkedHover(scope, chartOf) {
    if (!scope) return;
    scope.querySelectorAll("tr[data-meeting]").forEach(function (tr) {
      tr.addEventListener("mouseenter", function () { showAtMeeting(chartOf(), tr.dataset.meeting); });
      tr.addEventListener("mouseleave", function () { hideAtMeeting(chartOf()); });
    });
  }
  function wirePathChart(pj) {
    const canvas = document.getElementById("cbPathChart");
    if (!canvas) return;
    let stepped = false, chart = null;
    const table = document.getElementById("cbPathTable");
    const hidden = {};
    const paintTabs = function () {
      const box = document.getElementById("cbSeriesTabs");
      box.innerHTML = seriesTabsHtml(pj, hidden);
      box.querySelectorAll(".cb-stab").forEach(function (b) {
        b.style.setProperty("--stab", seriesColor(b.dataset.series, pj.ccy));
        b.addEventListener("click", function () {
          if (b.getAttribute("aria-disabled") === "true") return;
          hidden[b.dataset.series] = !hidden[b.dataset.series];
          destroyCharts(); state.redraw.forEach(function (fn) { fn(); });
        });
      });
    };
    registerRedraw(function () { paintTabs(); chart = drawPathChart(canvas, pj, stepped, hidden, function (m) { highlightRow(table, m); }); });
    wireLinkedHover(table, function () { return chart; });
    root.querySelectorAll(".cb-tbtn[data-shape]").forEach(function (b) {
      b.addEventListener("click", function () {
        stepped = !stepped;
        b.classList.toggle("active", stepped);
        b.setAttribute("aria-pressed", stepped ? "true" : "false");
        destroyCharts(); state.redraw.forEach(function (fn) { fn(); });
      });
    });
  }

  // ---- bank page ------------------------------------------------------------------------------------------------------------
  function unverifiedChips(d) {
    return d.unverified.map(function (t) { return '<span class="econ-flag flag-nc" title="' + esc(t.text) + '">' + esc(t.label) + "</span>"; }).join(" ");
  }
  function blackoutBadge(n) {
    const b = n && n.blackout;
    if (!b) return "";
    return '<span class="econ-flag cb-blackout" data-blackout-start="' + esc(b.start_utc) + '" data-blackout-end="' + esc(b.end_utc) + '" hidden title="' + esc("Quiet period until " + b.end + (b.precision === "approximate" || !b.verified ? " (approximate / unverified)" : "")) + '">in blackout</span>';
  }
  function probBars(h) {
    const list = (h.probabilities || []).slice().sort(function (a, b) { return a.moves - b.moves; });
    return '<div class="cb-bars">' + list.map(function (p) {
      const label = p.moves === 0 ? "Hold" : (h.direction === "cut" ? MINUS : "+") + 25 * p.moves + " bp";
      const cls = p.moves === 0 ? "" : h.direction === "cut" ? "cb-bar-dove" : "cb-bar-hawk";
      return '<div class="cb-bar-row"><span class="cb-bar-label">' + esc(label) + '</span><span class="cb-bar-track"><span class="cb-bar ' + cls + '" style="width:' + (p.p * 100).toFixed(1) + '%"></span></span><span class="cb-bar-pct">' + pct(p.p) + "</span></div>";
    }).join("") + "</div>";
  }
  function nextCard(d, dayMode) {
    const s = d.summary, h = s.horizon, n = s.next;
    let body;
    if (h.kind === "step") {
      body = '<div class="cb-bigrate ' + (h.step_bp > 0 ? "cb-hawk" : h.step_bp < 0 ? "cb-dove" : "") + '">' + fmtSigned(h.step_bp, 1) + ' bp' + flagBadge(h.flag) + staleBadge(h.stale) + "</div>" +
        '<div class="cb-sub">Implied step at the ' + fmtDate(h.meeting) + " meeting</div>" + probBars(h);
    } else if (h.kind === "window") {
      body = '<div class="cb-bigrate ' + (h.cum_bp > 0 ? "cb-hawk" : h.cum_bp < 0 ? "cb-dove" : "") + '">' + fmtSigned(h.cum_bp, 1) + " bp" + flagBadge(h.flag) + staleBadge(h.stale) + "</div>" +
        '<div class="cb-sub">Cumulative to the window ' + esc(fmtRange(h.window)) + ", which spans " + h.n_meetings + " meeting" + (h.n_meetings === 1 ? "" : "s") + ". An upper bound for the next meeting alone: no per-meeting step or probability can be read from a 3M window.</div>";
    } else {
      body = '<div class="cb-bigrate cb-na">' + NA + flagBadge(h.flag) + '</div><div class="cb-sub">' + esc(h.na || "n/a") + "</div>" +
        (isNum(h.level) ? '<div class="cb-sub">Raw level ' + fmtRate(h.level, 3) + "% (" + esc(((state.meta || {}).level_help || {})[h.level_kind] || "") + ")</div>" : "");
    }
    const when = n && n.decision ? '<div class="cb-sub">' + fmtDate(n.decision) + " " + cdSpan(n) + (timeText(n.time) ? DOT + esc(timeText(n.time)) : "") + "</div>" : "";
    return '<section class="cb-card cb-next' + (dayMode ? " cb-daycard" : "") + '">' + (dayMode ? '<div class="cb-daytag">Decision day</div>' : "") + "<h3>Next meeting</h3>" + when + body + "</section>";
  }
  // ---- bank page v3: zone 2 "The bank" (latest decision, projections, speeches, decision history) -------------------------------
  // Each block returns {title, sub, html}: the desktop wraps it in a card, the phone in an accordion (same order, same content).
  const MINUTES_TYPES = ["minutes", "account", "summary_of_opinions", "deliberations"];
  const PRESSER_LABEL = { presser_video: "Video", presser_transcript: "Transcript", opening_statement: "Opening statement" };
  function rateAfterText(x) { return x.lower !== null && x.lower !== undefined ? fmtRate(x.lower) + EN + fmtRate(x.upper) + "%" : fmtRateAuto(x.rate_after) + "%"; }
  function moveText2(x) {                                             // "+25 bp to 3.75–4.00%" · "Hold at 2.50%" · "−25 bp to 2.25%"
    if (!x) return "";
    return isNum(x.delta_bp) && Number(x.delta_bp) !== 0 ? fmtSigned(x.delta_bp, 0) + " bp to " + rateAfterText(x) : "Hold at " + rateAfterText(x);
  }
  function timelineFor(d, date) { return ((d.documents && d.documents.timeline) || []).find(function (t) { return t.meeting === date; }) || null; }
  // the links of one decision: statement, minutes (account / summary of opinions / deliberations), press conference; a document not out yet = muted
  function decisionLinks(stmt, followUp, conf) {
    const out = [];
    if (stmt && stmt.url) out.push(docAnchor(stmt, "Statement"));
    (followUp || []).filter(function (i) { return MINUTES_TYPES.indexOf(i.type) >= 0; }).forEach(function (i) {
      out.push(i.url ? docAnchor(i, i.label) : '<span class="muted" title="' + esc(i.na || "not published yet") + '">' + esc(i.label) + (i.expected ? " (expected " + fmtDay(i.expected) + ")" : "") + "</span>");
    });
    const press = (followUp || []).filter(function (i) { return PRESSER_LABEL[i.type] && i.url; });
    if (press.length) press.forEach(function (i) { out.push(docAnchor(i, PRESSER_LABEL[i.type])); });
    else if (conf) Object.keys(conf).forEach(function (k) { if (conf[k] && conf[k].url) out.push(docAnchor(conf[k], PRESSER_LABEL[k] || "Press conference")); });
    return out;
  }
  function votesPublished(v) { return !!v && v.kind !== "not_published"; }        // "consensus" (RBNZ) is shown; "not published" (ECB) is not
  function latestDecisionBlock(d) {
    const doc = d.documents, L = doc && doc.latest;
    if (!L) return null;
    const st = L.statement, dec = d.decisions.find(function (x) { return x.date === L.meeting; });
    const links = decisionLinks(st, L.follow_up, dec && dec.slots && dec.slots.conference);
    const row = '<div class="cb-ld-row"><b>' + fmtDate(L.meeting) + "</b>" + (dec ? '<span class="cb-ld-move' + tone(dec.delta_bp) + '">' + esc(moveText2(dec)) + "</span>" : "") +
      (votesPublished(L.votes) ? voteChip(L.votes) : "") + (links.length ? '<span class="cb-ld-links">' + links.join(DOT) + "</span>" : "") + "</div>";
    const changes = st && L.redline ? '<details class="cb-x"><summary>Changes vs ' + fmtDate(L.redline.prev_meeting) + ' <span class="muted">(+' + L.redline.added_words + " / −" + L.redline.removed_words +
      " words)</span></summary>" + '<div class="cb-stmt-body">' + redlineParas(st, L.redline) + "</div></details>" : "";
    const full = st ? '<details class="cb-x"><summary>Full statement <span class="muted">(' + st.paragraphs.length + " paragraphs)</span></summary>" + '<div class="cb-stmt-body">' + st.paragraphs.map(paraHtml).join("") +
      '</div><div class="cb-sub">Source: <a href="' + esc(st.url) + '" target="_blank" rel="noopener">' + esc(st.url) + "</a>" + (st.rate_after !== null ? DOT + "rate in the text: " + fmtRateAuto(st.rate_after) + "%" : "") +
      (st.license ? "<br>" + esc(st.license) : "") + "</div></details>" : '<div class="cb-sub">' + esc(L.na || "statement not collected") + "</div>";
    const manual = doc.manual_only && !/manual/i.test((!st && L.na) || "") ? '<div class="cb-sub muted">RBNZ texts come from a manual file: the site is behind a Cloudflare challenge.</div>' : "";
    return { title: "Latest decision", sub: fmtDay(L.meeting) + (dec ? DOT + esc(moveText2(dec)) : "") + (votesPublished(L.votes) && L.votes.label ? DOT + esc(L.votes.label) : ""),
             html: row + summarySlot(L.summary, true) + changes + full + manual };
  }

  // market vs the bank's own path: only the Fed dots and the RBNZ OCR track (null otherwise: the block is left out)
  function gapLean(v, who) { return !isNum(v) ? "" : Math.abs(v) < 0.5 ? "in line with the " + who : v > 0 ? "market above the " + who : "market below the " + who; }
  function projectionsBlock(d) {
    const g = d.gap, bank = d.chart.bank;
    if (g.kind === "dots") {
      const rows = Object.keys(g.years).map(function (y) {
        const m = g.years[y], yr = (bank.years || []).find(function (b) { return String(b.year) === y; }) || {};
        const tip = "Distribution of the " + (m.n_dots || "") + " dots: " + (yr.dots || []).map(function (x) { return fmtRate(x.level, 3) + "% ×" + x.count; }).join(", ");
        const mkt = isNum(m.market) ? fmtRate(m.market, 3) + "%" : NA;
        const gap = isNum(m.v) ? '<span class="' + tone(m.v).trim() + '">' + fmtSigned(m.v, 0) + " bp</span> (" + gapLean(m.v, "Fed") + ")" : naSpan(m.na);
        return '<li class="cb-pj-row" title="' + esc(tip) + '"><b>' + esc(y) + "</b>" + DOT + "dots " + fmtRate(m.bank, 3) + "%" + DOT + "market " + mkt + flagBadge(m.flag) + DOT + gap + "</li>";
      }).join("");
      return { title: "Market vs bank projections", sub: "FOMC dots" + DOT + "SEP of " + fmtDay(g.sep),
               html: '<ul class="cb-pj">' + rows + '</ul><div class="cb-sub">Market minus the median dot at year end, same basis; hover a year for the distribution of the dots. 2028: no meeting calendar yet, the last meeting is assumed to be the 2nd Wednesday of December.</div>' };
    }
    if (bank.kind === "ocr_track") {
      const track = bank.quarters.map(function (x) { return x.period.slice(0, 4) + " Q" + x.period.slice(-1) + " " + (isNum(x.value) ? fmtRate(x.value, 1) + "%" : NA); }).join(", ");
      const vm = bank.vs_market || { years: [], note: "" };
      const rows = vm.years.map(function (r) {
        const ap = r.est ? "≈ " : "";
        return '<li class="cb-pj-row" title="' + esc("Published OCR track (quarterly averages): " + track) + '"><b>' + esc(String(r.year)) + "</b>" + DOT + "RBNZ Q4 " + fmtRate(r.bank, 1) + "%" + DOT +
          "market " + ap + fmtRate(r.market, 2) + "%" + DOT + '<span class="' + tone(r.gap_bp).trim() + '">' + ap + fmtSigned(r.gap_bp, 0) + " bp</span> (" + gapLean(r.gap_bp, "RBNZ") + ")</li>";
      }).join("");
      return { title: "Market vs bank projections", sub: "RBNZ OCR track" + DOT + "MPS " + fmtDay(bank.source),
               html: (rows ? '<ul class="cb-pj">' + rows + "</ul>" : '<div class="cb-sub">' + NA + " no year with both the OCR track and a market level.</div>") +
                     '<div class="cb-sub">' + esc(vm.note || "") + " Hover a year for the full track.</div>" };
    }
    return null;
  }

  // speeches: the last 5 about monetary policy; everything else (older monetary ones, "other") under "Show all (N)"
  function speechSplit(speeches) {
    const all = (speeches || []).slice().sort(function (a, b) { return String(b.published).localeCompare(String(a.published)); });
    const top = all.filter(function (x) { return x.relevance === "monetary"; }).slice(0, 5);
    return { top: top, rest: all.filter(function (x) { return top.indexOf(x) < 0; }) };
  }
  function speechRow(x) {
    const chip = x.chair ? ' <span class="econ-flag cb-chip-chair" title="Chair / head of the decision body">chair</span>' : x.voter ? ' <span class="econ-flag cb-chip-voter" title="Votes at the meetings this year">voter</span>' : "";
    return '<li class="cb-sp-row' + (x.relevance === "other" ? " cb-dim" : "") + '"><span class="cb-sp-date">' + fmtDay(x.published) + '</span><span class="cb-sp-who">' + esc(x.speaker || NA) + chip + "</span>" +
      '<a class="cb-sp-title" href="' + esc(x.url) + '" target="_blank" rel="noopener"' + (x.relevance === "other" ? ' title="not about monetary policy / the outlook"' : "") + ">" + esc(x.title) + "</a>" +
      summaryDetails(x.summary, "Summary") + "</li>";
  }
  function speechesBlock(d) {
    const doc = d.documents;
    if (!doc) return null;
    const s = speechSplit(doc.speeches);
    const top = s.top.length ? '<ul class="cb-sp">' + s.top.map(speechRow).join("") + "</ul>" : '<div class="cb-sub">' + NA + " no speech about monetary policy in the last 60 days.</div>";
    const rest = s.rest.length ? '<details class="cb-x cb-sp-all"><summary>Show all (' + s.rest.length + ')</summary><ul class="cb-sp">' + s.rest.map(speechRow).join("") + "</ul></details>" : "";
    return { title: "Recent speeches", sub: s.top.length + " about monetary policy" + DOT + doc.n_speeches + " in the last 60 days", html: top + rest };
  }

  // the last 4 decisions: Date · Move · Rate after · Vote · vs market (bp) · Links; the summaries of a decision in one expander under its row
  const HISTORY_COLS = ["Date", "Move", "Rate after", "Vote", "vs market (bp)", "Links"];
  function decisionHistoryRows(d) {
    return d.decisions.slice().sort(function (a, b) { return String(b.date).localeCompare(String(a.date)); }).slice(0, 4).map(function (x) {
      const t = timelineFor(d, x.date);
      const sums = [];
      const add = function (slot, label) { if (slot && slot.status === "ready" && SUMMARIES[slot.doc_id]) sums.push({ slot: slot, label: label }); };
      if (t && t.statement) add(t.statement.summary, "Statement");
      ((t && t.follow_up) || []).forEach(function (i) { add(i.summary, i.label); });
      return { date: x.date, move: x.delta_bp, rate: rateAfterText(x), vote: x.slots && x.slots.votes, vs: x.vs_market,
               links: decisionLinks((t && t.statement) || (x.slots && x.slots.statement), t && t.follow_up, x.slots && x.slots.conference), sums: sums };
    });
  }
  function historyCols(rows) { return rows.length && rows.every(function (r) { return !votesPublished(r.vote); }) ? HISTORY_COLS.filter(function (c) { return c !== "Vote"; }) : HISTORY_COLS; }
  function decisionHistoryBlock(d) {
    const all = decisionHistoryRows(d), cols = historyCols(all), withVote = cols.indexOf("Vote") >= 0;
    const rows = all.map(function (r) {
      const vs = r.vs && isNum(r.vs.v) ? txtTd(r.vs.v, esc(fmtSigned(r.vs.v, 1)) + flagBadge(r.vs.flag), isNum(r.vs.implied_step_bp) ? "Decided step minus the step implied at T-1 (" + fmtSigned(r.vs.implied_step_bp, 1) + " bp)" : "Decided step minus the step implied at T-1")
        : naTd(r.vs && r.vs.na);
      return '<tr><td class="cb-date">' + fmtDate(r.date) + "</td>" + txtTd(r.move, esc(fmtSigned(r.move, 0))) + '<td class="cb-n">' + esc(r.rate) + "</td>" + (withVote ? "<td>" + (votesPublished(r.vote) ? voteChip(r.vote) : '<span class="muted" title="votes not published">' + NA + "</span>") + "</td>" : "") + vs +
        '<td class="cb-links-td">' + (r.links.join(DOT) || NA) + "</td></tr>" +
        (r.sums.length ? '<tr class="cb-sumrow"><td colspan="' + cols.length + '"><details class="cb-x"><summary>Summaries (' + r.sums.map(function (s) { return s.label; }).join(", ") + ")</summary>" +
          r.sums.map(function (s) { return '<div class="cb-sum-titled"><div class="cb-kicker">' + esc(s.label) + "</div>" + summaryBlock(SUMMARIES[s.slot.doc_id]) + "</div>"; }).join("") + "</details></td></tr>" : "");
    }).join("");
    const head = cols.map(function (h) {
      return h === "vs market (bp)" ? '<th class="cb-n" title="Decided step minus the step implied at T-1, bp (EXACT / CURVE / PROXY with a basis only)">' + h + "</th>" : "<th" + (h === "Move" || h === "Rate after" ? ' class="cb-n"' : "") + ">" + h + "</th>";
    }).join("");
    return { title: "Decision history", sub: "last " + Math.min(4, d.decisions.length) + " decisions",
             html: '<div class="cb-scroll"><table class="cb-table cb-rp cb-dhist"><thead><tr>' + head + "</tr></thead><tbody>" + (rows || '<tr><td colspan="' + cols.length + '" class="muted">no decision on record</td></tr>') + "</tbody></table></div>" };
  }
  function bankCard(b, cls) { return b ? '<section class="cb-card cb-bblock' + (cls ? " " + cls : "") + '"><h3>' + esc(b.title) + (b.sub ? ' <small class="muted">' + b.sub + "</small>" : "") + "</h3>" + b.html + "</section>" : ""; }

  // ---- zone 3: reference (closed): methodology + the sources / licences table + the full calendar ---------------------------------
  function sourcesTable(d) {
    const rows = d.sources.map(function (s) {
      return "<tr" + (s.stale ? ' class="cb-stale"' : "") + "><td>" + esc(s.id) + "</td><td>" + esc(s.role || "") + "</td><td>" + esc((s.methods || []).join(", ")) + "</td><td>" + (s.asof ? fmtDate(s.asof) + (isNum(s.lag_bd) ? " (" + s.lag_bd + " bd)" : "") : NA) +
        staleBadge(s.stale, "older than " + d.meta.stale_after_bd + " business days") + '</td><td class="cb-notes">' + esc(s.license || "") + (s.proxy ? " Proxy source." : "") + "</td></tr>";
    }).join("");
    return "<h4>Sources and licences</h4>" + '<div class="cb-scroll"><table class="cb-table cb-mini"><thead><tr><th>Source</th><th>Role</th><th>Method</th><th>As-of</th><th>License / attribution</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="5" class="muted">no market source for this currency</td></tr>') + "</tbody></table></div>" +
      "<p>Official series: " + esc(d.summary.rate.decision_status === "official" ? "decision confirmed by an official series" : "decision status: " + (d.summary.rate.decision_status || "n/a")) + ". Decisions, meeting calendars and Fed projections come from the banks' own pages.</p>";
  }
  function calendarTable(d) {
    const rows = d.calendar.map(function (m) {
      const b = m.blackout;
      const bo = b ? fmtDay(b.start.slice(0, 10)) + " → " + fmtDay(b.end.slice(0, 10)) + (b.precision === "approximate" || !b.verified ? ' <span class="econ-flag flag-nc" title="' + esc(b.note || "rule not confirmed on the official page") + '">approx.</span>' : "") : '<span class="cb-na" title="no published quiet-period rule found">' + NA + "</span>";
      return "<tr><td>" + weekday(m.decision) + " " + fmtDate(m.decision) + "</td><td>" + (m.first_day ? fmtDay(m.first_day) : NA) + "</td><td>" + fmtDate(m.effective) + "</td><td>" + esc(timeText(m.time) || NA) + "</td>" +
        "<td>" + (m.has_projections ? esc(m.projections_name || "yes") : NA) + "</td><td>" + (m.has_presser ? esc(m.conference_local ? m.conference_local + " " + m.time.abbr : "yes") : NA) + "</td><td>" + bo + "</td>" +
        '<td class="cb-notes">' + (m.source === "official" ? "official" : '<span title="date not read from an official page">' + esc(m.source || "n/a") + "</span>") + "</td></tr>";
    }).join("");
    return "<h4>Calendar and blackout</h4>" + '<div class="cb-scroll"><table class="cb-table cb-mini"><thead><tr><th>Decision</th><th>First day</th><th>Effective</th><th>Time</th><th>Projections</th><th>Conference</th><th>Blackout</th><th>Date source</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="8" class="muted">no upcoming meetings on record</td></tr>') + "</tbody></table></div>";
  }
  function referencePanel(d) {
    return methodPanel(d).replace(/<\/details>$/, "") + sourcesTable(d) + calendarTable(d) + "</details>";
  }
  // Phase 4: how long after each decision the collector had the statement (labels and numbers all come from the payload).
  function utcStamp(iso) { const p = parts(iso.slice(0, 10)); return p.d + " " + MONTHS[p.m - 1] + " " + iso.slice(11, 16); }
  function latencyBlock(l) {
    if (!l || !l.rows || !l.rows.length) return "";
    const s = l.summary;
    const rows = l.rows.map(function (r) {
      const delay = !r.measured ? '<span class="muted">before the trigger</span>' : isNum(r.minutes) ? r.minutes.toFixed(1) + " min" + (r.ok ? "" : " (over the target)") : NA;
      return "<tr><td>" + fmtDate(r.meeting) + "</td><td>" + (r.official_at ? esc(utcStamp(r.official_at)) : NA) + "</td><td>" + esc(utcStamp(r.first_seen_at)) + "</td><td>" + delay + "</td></tr>";
    }).join("");
    const head = s.n ? s.within + " of " + s.n + " measured decisions within " + s.target_minutes + " min" + (isNum(s.median_minutes) ? " (median " + s.median_minutes + " min)" : "") :
      "No decision measured yet (target: within " + s.target_minutes + " min).";
    return "<h4>" + esc(l.title) + "</h4><p>" + esc(l.text) + "</p><p>" + esc(head) + "</p>" +
      '<div class="cb-scroll"><table class="cb-table cb-mini"><thead><tr><th>Decision</th><th>Official time (UTC)</th><th>First seen (UTC)</th><th>Delay</th></tr></thead><tbody>' + rows + "</tbody></table></div>";
  }
  function methodPanel(d) {
    const meta = d.meta;
    const items = meta.methodology.map(function (m) { return "<dt>" + esc(m.title) + "</dt><dd>" + esc(m.text) + "</dd>"; }).join("");
    const cc = d.crosschecks.length ? '<h4>Cross-checks</h4><ul>' + d.crosschecks.map(function (c) {
      return "<li>" + esc(c.name) + DOT + esc(c.period) + DOT + (isNum(c.diff_bp) ? fmtSigned(c.diff_bp, 1) + " bp (" + esc(c.note) + ")" : "n/a: " + esc(c.na || "")) + "</li>";
    }).join("") + "</ul>" : "";
    const ck = d.consistency.length ? "<h4>Consistency of months without a meeting</h4><ul>" + d.consistency.map(function (c) { return "<li>" + esc(c.source + " " + c.period) + DOT + fmtSigned(c.dev_bp, 1) + " bp (" + esc(c.detail) + ")</li>"; }).join("") + "</ul>" : "";
    const sp = d.spread ? "<p>Overnight-to-policy spread used: <b>" + fmtSigned(d.spread.bp, 2) + " bp</b>, median of " + d.spread.n + " business days (" + fmtDate(d.spread.start) + EN + fmtDate(d.spread.end) + "; " + d.spread.excluded + " excluded) for " + esc(d.spread.benchmark) + " minus " + esc(d.spread.policy) + ".</p>" : "";
    const allNotes = d.notes.concat(d.path && d.path.spread_note && d.notes.indexOf(d.path.spread_note) < 0 ? [d.path.spread_note] : []);
    const notes = allNotes.length ? "<h4>Notes</h4><ul>" + allNotes.map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("") + "</ul>" : "";
    return '<details class="cb-card cb-method" id="cbMethod"><summary>How is this calculated?</summary><dl>' + items + "</dl>" + sp + notes + cc + ck + latencyBlock(d.latency) + "</details>";
  }
  // ---- official texts (phase 2a) -------------------------------------------------------------------------------------------
  function docAnchor(it, label) {
    if (!it || !it.url) return '<span class="cb-na" title="' + esc((it && it.na) || "n/a") + '">' + NA + "</span>";
    return '<a class="cb-doclink" href="' + esc(it.url) + '" target="_blank" rel="noopener" title="' + esc((it.title || it.label || "") + (it.published ? " · " + it.published : "")) + '">' + esc(label || it.label) + "</a>";
  }
  function confLinks(conf) {
    if (!conf) return '<span class="cb-na" title="no official video or transcript collected">' + NA + "</span>";
    return Object.keys(conf).map(function (k) { return docAnchor(conf[k], k === "presser_video" ? "video" : k === "presser_transcript" ? "transcript" : "statement"); }).join(" · ");
  }
  // ---- summaries: strictly factual, checked against the source before they are stored ---------------------------------------
  let SUMMARIES = {};                                               // doc_id -> summary of the page being rendered
  function changesBlock(c) {
    const items = c.changes.map(function (x) {
      return "<li>" + (x.paragraph ? "¶" + x.paragraph + ": " : "paragraph removed: ") + (x.removed ? "<del>" + esc(x.removed) + "</del>" : "") + (x.removed && x.added ? " → " : "") + (x.added ? "<ins>" + esc(x.added) + "</ins>" : "") + "</li>";
    }).join("");
    return '<details class="cb-sum-changes"><summary>Changes vs the statement of ' + fmtDate(c.vs_meeting) + " (+" + c.added_words + " / −" + c.removed_words + " words)</summary><ul>" + items + "</ul>" + (c.truncated ? '<div class="cb-sub">first changes only</div>' : "") + "</details>";
  }
  function markFragments(para, frags) {                             // the paragraph with the evidence fragments highlighted
    const spans = [];
    frags.forEach(function (f) { const i = f ? para.indexOf(f) : -1; if (i >= 0) spans.push([i, i + f.length]); });
    spans.sort(function (a, b) { return a[0] - b[0]; });
    let out = "", at = 0;
    spans.forEach(function (s) {
      if (s[0] < at) { if (s[1] > at) { out += "<mark>" + esc(para.slice(at, s[1])) + "</mark>"; at = s[1]; } return; }
      out += esc(para.slice(at, s[0])) + "<mark>" + esc(para.slice(s[0], s[1])) + "</mark>";
      at = s[1];
    });
    return out + esc(para.slice(at));
  }
  function pointHtml(text, ev) {                                    // a summary point; with its evidence: hover shows the source fragments, click the paragraph(s) and the link to the text
    if (!ev) return "<li>" + esc(text) + "</li>";
    const paras = ev.paragraphs.map(function (n) { return "\u00b6" + n; }).join(", ");
    const frags = ev.fragments.map(function (f) { return f.text; });
    const quoted = frags.map(function (f) { return "\u201c" + f + "\u201d"; }).join(" \u00b7 ");
    const texts = ev.texts ? ev.paragraphs.map(function (n) { return '<div class="cb-sum-para"><b>\u00b6' + n + "</b> " + markFragments(ev.texts[String(n)] || "", frags) + "</div>"; }).join("") : "";
    return '<li class="cb-sum-point" title="Source ' + esc(paras) + ": " + esc(quoted) + ' (click for the source)"><span class="cb-sum-text" tabindex="0" role="button" aria-expanded="false">' + esc(text) + "</span>" +
      '<div class="cb-sum-evidence" hidden><div class="cb-sum-frag"><b>Source ' + esc(paras) + "</b> " + esc(quoted) + ' <a href="' + esc(ev.href) + '" target="_blank" rel="noopener" title="Opens the bank\u2019s own page and highlights the passage">open the source \u2197</a></div>' + texts + "</div></li>";
  }
  document.addEventListener("click", function (e) {                 // one delegated handler: a point opens / closes its evidence
    const t = e.target.closest ? e.target.closest(".cb-sum-text") : null;
    if (!t || !t.nextElementSibling) return;
    const box = t.nextElementSibling, open = box.hidden;
    box.hidden = !open;
    t.setAttribute("aria-expanded", open ? "true" : "false");
  });
  document.addEventListener("keydown", function (e) {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList && e.target.classList.contains("cb-sum-text")) { e.preventDefault(); e.target.click(); }
  });
  function summaryBlock(s, compact) {                               // compact (Latest decision): the points and the footer only; the changes / quotes stay in their own blocks
    const pts = '<ul class="cb-sum-points">' + s.points.map(function (p, i) { return pointHtml(p, s.evidence && s.evidence[i]); }).join("") + "</ul>";
    const qs = '<div class="cb-sum-quotes">' + s.quotes.map(function (q) {
      return "<blockquote>\u201c" + esc(q.text) + "\u201d " + '<a href="' + esc(q.href) + '" target="_blank" rel="noopener" title="Opens the bank\u2019s own page and highlights the passage">source \u00b6' + q.paragraph + " \u2197</a></blockquote>";
    }).join("") + "</div>";
    const foot = '<div class="cb-sum-foot">' + esc(s.note.charAt(0).toUpperCase() + s.note.slice(1)) + " · " + (s.provider ? esc(s.provider) + " " : "") + esc(s.model) + " · " + esc(s.prompt_version) + " · generated " + fmtDate(s.generated) +
      (s.truncated ? " · covers the first part of a long document only" : "") + "</div>";
    const dropped = s.dropped && s.dropped.length ? '<div class="cb-sum-dropped" title="' + esc(s.dropped.map(function (d) { return "\u201c" + d.text + "\u201d: " + d.reason; }).join("\n")) + '">' + s.dropped.length + (s.dropped.length === 1 ? " point" : " points") + " removed by verification</div>" : "";
    if (compact) return '<div class="cb-summary">' + pts + dropped + foot + "</div>";
    return '<div class="cb-summary">' + pts + dropped + (s.changes ? changesBlock(s.changes) : "") + qs + foot + "</div>";
  }
  function summarySlot(slot, compact) {                             // the summary when ready; otherwise the pending marker, the reason in its tooltip
    if (slot && slot.status === "ready" && SUMMARIES[slot.doc_id]) return summaryBlock(SUMMARIES[slot.doc_id], compact);
    return '<div class="cb-summary-slot" title="' + esc((slot && slot.reason) || "not generated yet") + '">' + esc((slot && slot.label) || "summary pending") + "</div>";
  }
  function summaryDetails(slot, label) {                            // a collapsible summary under a document link; nothing when there is none
    if (!slot || slot.status !== "ready" || !SUMMARIES[slot.doc_id]) return "";
    return '<details class="cb-sum-doc"><summary>' + esc(label || "Summary") + "</summary>" + summaryBlock(SUMMARIES[slot.doc_id]) + "</details>";
  }
  function voteChip(v) {
    if (!v) return '<span class="cb-na" title="votes not collected yet">' + NA + "</span>";
    const tip = [v.evidence || "", v.for && v.for.length ? "For: " + v.for.join(", ") : "", (v.against || []).map(function (a) { return "Against: " + (a.name || "member(s)") + " (wanted: " + a.direction + ")" + (a.note ? " - " + a.note : ""); }).join("\n"), v.notes || ""].filter(Boolean).join("\n");
    return '<span class="cb-votes cb-votes-' + esc(v.kind) + '" title="' + esc(tip) + '">' + esc(v.label) + "</span>";
  }
  function paraHtml(p) { return "<p>" + esc(p) + "</p>"; }
  function redlineParas(st, rl) {
    return rl.paras.map(function (x) {
      if (x.kind === "same") return paraHtml(st.paragraphs[x.p] || "");
      if (x.kind === "removed") return '<p class="cb-rl-removed"><del>' + esc(x.ops[0][1]) + "</del></p>";
      return "<p>" + x.ops.map(function (o) { return o[0] === "+" ? "<ins>" + esc(o[1]) + "</ins>" : o[0] === "-" ? "<del>" + esc(o[1]) + "</del>" : esc(o[1]); }).join("") + "</p>";
    }).join("");
  }
  function renderBank(d) {
    if (!d.path) { root.innerHTML = PENDING; return; }
    destroyCharts();
    state.redraw = [];
    state.meta = d.meta;
    SUMMARIES = (d.documents && d.documents.summaries) || {};
    titleEl.innerHTML = esc(d.ccy) + DOT + esc(d.bank.short) + ' <span class="cb-title-sub">' + esc(d.bank.name) + "</span>";
    document.title = d.ccy + " " + d.bank.short + " | Central Banks | Dashboard";
    const anyStale = d.summary.stale || d.sources.some(function (s) { return s.stale; });
    metaEl.innerHTML = '<a href="' + esc(CFG.urls.overview_page) + '">' + "← All banks</a>" + DOT + esc(dataDateText(d.path) || "As of " + fmtDate(d.meta.asof)) + (anyStale ? staleBadge(true, "at least one source is older than " + d.meta.stale_after_bd + " business days") : "");
    const n = d.summary.next;
    const dayMode = dayModeOf(d, todayIn);
    if (isPhone()) {
      titleEl.innerHTML = esc(d.ccy) + DOT + esc(d.bank.short) + ' <span class="cb-title-sub">' + esc(d.bank.name) + DOT + esc(dataDateText(d.path) || "as of " + fmtDay(d.meta.asof)) + (anyStale ? staleBadge(true, "at least one source is older than " + d.meta.stale_after_bd + " business days") : "") + "</span>";
      metaEl.innerHTML = '<a class="cb-back" href="' + esc(CFG.urls.overview_page) + '">' + '<svg class="ph-ico" width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>All banks</a>';
      renderBankPhone(d, dayMode);
      if (!themeWatched) { themeWatched = true; watchTheme(); }
      tick();
      return;
    }
    const latest = latestDecisionBlock(d);
    const dayLatest = dayMode && latest ? '<section class="cb-card cb-bblock cb-daycard"><div class="cb-daytag">Decision day</div><h3>' + esc(latest.title) + "</h3>" + latest.html + "</section>" : "";
    root.innerHTML = bankBar(d.ccy) + (dayMode ? nextCard(d, true) + dayLatest : "") + pathCards(d) + '<div class="cb-pathgrid">' + pathTableHtml(d.path, d.calendar) + pathChartCard(d) + "</div>" + stanceZoneHtml(d) +
      '<h2 class="cb-zone">The bank</h2>' + (dayMode || !latest ? "" : bankCard({ title: latest.title, sub: "", html: latest.html })) + bankCard(projectionsBlock(d)) + bankCard(speechesBlock(d)) + bankCard(decisionHistoryBlock(d)) +
      referencePanel(d);
    wirePathChart(d.path);
    wireStanceZone(d);
    if (!themeWatched) { themeWatched = true; watchTheme(); }
    paintSwatches();
    document.querySelectorAll("[data-open-method]").forEach(function (a) { a.addEventListener("click", function () { document.getElementById("cbMethod").open = true; }); });
    tick();
  }

  // ---- stance (S2): the mandate benchmark - tab "Stance" on the overview and the zone "Stance and need" on the bank page ----------
  // Every number comes from the `stance` block of the JSON (src/cb_compute/payload.stance_page) and is rounded here by one rule (B.4):
  // pp with 2 decimals and a sign, bp as signed integers, inflation and rates as published. A rendered number carries data-k (its key in
  // the stance block) and data-f (its format) so the page can be checked against the payload. Texts are the payload's templates.
  const ST_FLAGS = {
    boundary: ["near a threshold", "The situation is within 0.05 pp of a threshold of the rule."],
    neutral_sensitive: ["neutral-sensitive", "Depends on where neutral is: the situation differs at the two ends of the neutral interval."],
    headline_basis: ["headline basis", "The bank does not project its underlying measure for this period: the headline projection is used."],
    estimate: ["estimate", "An input is an estimate (the market side or the publication cutoff)."],
    stale: ["stale", "An input is older than its freshness threshold"],
    pending: ["pending", "An input the rule needs has not been entered yet."],
    slack_held: ["slack held", "The bank publishes no slack projection for the 12-month period: today's slack is held."],
    no_slack: ["no slack estimate", "The bank publishes no estimate of spare capacity: the slack term is left out."],
    need_only: ["need only", "No neutral-rate estimate: only the need is shown (no stance, pressure or situation)."]
  };
  const ST_FLAG_ORDER = ["boundary", "neutral_sensitive", "headline_basis", "estimate", "stale", "pending", "slack_held", "no_slack", "need_only"];
  function fmtPP(v) { return isNum(v) ? fmtSigned(v, 2) : NA; }
  function fmtBP(v) {
    if (!isNum(v)) return NA;
    const n = Math.floor(Math.abs(Number(v)) + 0.5);
    return n === 0 ? "0" : (Number(v) > 0 ? "+" : MINUS) + n;
  }
  function fmtPub(v) { return isNum(v) ? String(Number(v)) : NA; }                       // inflation / rates as published
  const ST_FMT = { pp: fmtPP, bp: fmtBP, pub: fmtPub, lvl: function (v) { return isNum(v) ? Number(v).toFixed(2) : NA; },
                   bp100: function (v) { return fmtBP(isNum(v) ? Number(v) * 100 : v); } };
  // a number with its key and format; n/a = "—" with the reason
  function stNum(v, f, k, na, cls) {
    if (!isNum(v)) return '<span class="cb-na" title="' + esc(na || "n/a") + '">' + NA + "</span>";
    const txt = ST_FMT[f](v), zero = /^0(\.0+)?$/.test(txt);                  // a value that rounds to zero is neutral, not blue / red
    const tone = cls === "tone" ? (zero ? "" : Number(v) > 0 ? " cb-hawk" : Number(v) < 0 ? " cb-dove" : "") : (cls ? " " + cls : "");
    return '<span class="cb-st-n' + tone + '"' + (k ? ' data-k="' + esc(k) + '"' : "") + ' data-f="' + f + '" data-v="' + esc(v) + '">' + ST_FMT[f](v) + "</span>";
  }
  function stChip(label, sit, dir, extra) {
    if (!label) return '<span class="cb-st-chip cb-st-na" title="' + esc(extra || "no neutral estimate (need only)") + '">need only</span>';
    return '<span class="cb-st-chip cb-st-' + esc(sit) + (dir > 0 ? " up" : dir < 0 ? " down" : "") + '"' + (extra ? ' title="' + esc(extra) + '"' : "") + ">" + esc(label) + "</span>";
  }
  function stFlagChips(flags, when, s) {
    return (flags || []).filter(function (f) { return ST_FLAGS[f] && f !== "need_only"; }).sort(function (a, b) { return ST_FLAG_ORDER.indexOf(a) - ST_FLAG_ORDER.indexOf(b); }).map(function (f) {
      let tip = ST_FLAGS[f][1];
      if (f === "stale") tip += " (as of " + fmtDate(s && s.asof) + ").";
      if (f === "pending" && s && s.notes && s.notes.length) tip += " " + s.notes.join("; ");
      return '<span class="cb-st-flag" data-flag="' + esc(f) + '" title="' + esc((when ? when + ": " : "") + tip) + '">' + esc((when ? when + " " : "") + ST_FLAGS[f][0]) + "</span>";
    }).join("");
  }
  function stRate(r) {
    if (!r || !isNum(r.rate)) return NA;
    return r.range && isNum(r.lower) ? fmtRate(r.lower) + EN + fmtRate(r.upper) + "%" : fmtRateAuto(r.rate) + "%";
  }
  function stAsof(s) { return "as of " + fmtDate(s.asof); }

  // ---- the map: x = need, y = stance; hold band |1.5x − y| ≤ δ; hike zone below it, cut zone above; dotted x = ±ε ----
  function stanceMapModel(banks, params) {
    const k = params.inflation_response, d = params.delta_pp, e = params.epsilon_pp;
    const pts = [], out = [];
    BANK_ORDER.forEach(function (c) {
      const s = banks[c];
      if (!s) return;
      if (!isNum(s.stance.v) || !isNum(s.need.v)) { out.push({ ccy: c, reason: s.stance.na || s.need.na || "n/a" }); return; }
      pts.push({ ccy: c, x: Number(s.need.v), y: Number(s.stance.v), lo: Number(s.stance.lo), hi: Number(s.stance.hi),
                 x12: isNum(s.need_12m.v) ? Number(s.need_12m.v) : null, y12: Number(s.stance.v) });
    });
    const xs = [-2 * e, 2 * e], ys = [-1, 1];
    pts.forEach(function (p) { xs.push(p.x); if (p.x12 !== null) xs.push(p.x12); ys.push(p.lo, p.hi, p.y); });
    const pad = 0.25;
    const xr = [Math.floor((Math.min.apply(null, xs) - pad) * 4) / 4, Math.ceil((Math.max.apply(null, xs) + pad) * 4) / 4];
    const yr = [Math.floor((Math.min.apply(null, ys) - pad) * 4) / 4, Math.ceil((Math.max.apply(null, ys) + pad) * 4) / 4];
    const line = function (off) { return [{ x: xr[0], y: k * xr[0] + off }, { x: xr[1], y: k * xr[1] + off }]; };
    return { xr: xr, yr: yr, k: k, delta: d, eps: e, band: { lower: line(-d), upper: line(d) }, eps_lines: [-e, e],
             points: pts, whiskers: pts.map(function (p) { return { ccy: p.ccy, from: { x: p.x, y: p.lo }, to: { x: p.x, y: p.hi } }; }),
             arrows: pts.filter(function (p) { return p.x12 !== null; }).map(function (p) { return { ccy: p.ccy, from: { x: p.x, y: p.y }, to: { x: p.x12, y: p.y12 } }; }),
             excluded: out };
  }
  // the zone of a point: hike (G > δ, below the band), cut (G < −δ, above it), hold (inside)
  function stanceZone(m, x, y) { const g = m.k * x - y; return g > m.delta ? "hike" : g < -m.delta ? "cut" : "hold"; }
  function stanceTipHtml(c, s) {
    const row = function (lab, html) { return '<tr><td>' + lab + "</td><td>" + html + "</td></tr>"; };
    return '<div class="cb-tt-title"><span class="cb-tt-sq" style="background:' + bankColor(c) + '"></span>' + esc(c) + " · " + esc(stAsof(s)) + "</div>" +
      '<table class="cb-tt-table">' +
      row("Need", stNum(s.need.v, "pp", "need.v") + " pp") +
      row("Stance", stNum(s.stance.v, "pp", "stance.v") + " pp (" + stNum(s.stance.lo, "pp", "stance.lo") + " to " + stNum(s.stance.hi, "pp", "stance.hi") + ")") +
      row("Pressure", stNum(s.pressure.v, "bp100", "pressure.v") + " bp (" + stNum(s.pressure.lo, "bp100", "pressure.lo") + " to " + stNum(s.pressure.hi, "bp100", "pressure.hi") + ")") +
      row("Today", stChip(s.chip, s.situation.v, s.situation.direction)) +
      row("In 12m, held", stChip(s.chip_12m, s.situation_12m.v, s.situation_12m.direction, s.situation_12m.na)) + "</table>";
  }
  function drawStanceMap(canvas, st, small) {
    const c = colors(), m = stanceMapModel(st.banks, st.params);
    const hawk = c.blue, dove = c.red;
    const edge = function (data, fill, col) { return { type: "line", data: data, borderColor: withAlpha(col, 0.35), borderWidth: 1, pointRadius: 0, fill: fill, backgroundColor: withAlpha(col, 0.07), order: 9 }; };
    const ds = [
      edge(m.band.lower, "start", hawk),                                     // hike zone: below the band
      edge(m.band.upper, "end", dove),                                       // cut zone: above the band
      Object.assign(edge(m.band.lower, false, c.muted), { borderColor: "transparent" }),
      Object.assign(edge(m.band.upper, "-1", c.muted), { borderColor: "transparent", backgroundColor: withAlpha(c.muted, 0.16) })   // hold band
    ];
    m.whiskers.forEach(function (w) { ds.push({ type: "line", data: [w.from, w.to], borderColor: withAlpha(bankColor(w.ccy), 0.8), borderWidth: 2, pointRadius: 0, order: 3 }); });
    m.arrows.forEach(function (a) {
      const col = bankColor(a.ccy), right = a.to.x >= a.from.x;
      const mid = { x: a.from.x + (a.to.x - a.from.x) * 0.82, y: a.from.y };
      ds.push({ type: "line", data: [a.from, a.to], borderColor: col, borderWidth: 1.5, borderDash: [2, 3], pointRadius: 0, order: 4 });
      ds.push({ type: "scatter", data: [mid], pointStyle: "triangle", rotation: right ? 90 : -90, pointRadius: Math.abs(a.to.x - a.from.x) > 0.05 ? 4 : 0, backgroundColor: col, borderColor: col, order: 4 });
      ds.push({ type: "scatter", data: [a.to], pointRadius: 5, pointBorderWidth: 2, borderColor: col, backgroundColor: c.surface, order: 2, $cbLater: a.ccy });
    });
    m.points.forEach(function (p) { ds.push({ type: "scatter", data: [{ x: p.x, y: p.y }], pointRadius: small ? 5 : 6, pointHoverRadius: 8, backgroundColor: bankColor(p.ccy), borderColor: c.surface, borderWidth: 1.5, order: 1, $cbToday: p.ccy }); });
    // direct labels: for each dot the first of 8 positions around it that hits no other label, no dot (filled or hollow) and stays inside
    // the plot; the rectangles are kept on the chart ($cbLabels) for the automated check
    const labels = { id: "cbStLabels", afterDatasetsDraw: function (ch) {
      const g = ch.ctx, ca = ch.chartArea, fs = small ? 10 : 12, R = 7;
      g.save();
      g.font = fs + "px system-ui, sans-serif";
      g.textBaseline = "middle";
      const px = function (p) { return { x: ch.scales.x.getPixelForValue(p.x), y: ch.scales.y.getPixelForValue(p.y) }; };
      const dots = m.points.map(function (p) { return px(p); }).concat(m.arrows.map(function (a) { return px(a.to); }));
      const hit = function (r, o) { return r.x < o.x + o.w && o.x < r.x + r.w && r.y < o.y + o.h && o.y < r.y + r.h; };
      const placed = [];
      m.points.forEach(function (p) {
        const c0 = px(p), text = small ? p.ccy.slice(0, 1) + p.ccy.slice(1).toLowerCase() : p.ccy, w = g.measureText(text).width + 2, h = fs + 2;
        const cand = [[R, -h - 1], [R, 1], [-R - w, -h - 1], [-R - w, 1], [-w / 2, -h - R], [-w / 2, R], [R + 4, -h / 2], [-R - w - 4, -h / 2]];
        let best = null;
        for (let k = 0; k < cand.length && !best; k++) {
          const r = { x: c0.x + cand[k][0], y: c0.y + cand[k][1], w: w, h: h };
          const inside = r.x >= ca.left && r.x + r.w <= ca.right && r.y >= ca.top && r.y + r.h <= ca.bottom;
          const free = placed.every(function (o) { return !hit(r, o); }) && dots.every(function (d) { return !hit(r, { x: d.x - R, y: d.y - R, w: 2 * R, h: 2 * R }); });
          if (inside && free) best = r;
        }
        best = best || { x: c0.x + cand[0][0], y: c0.y + cand[0][1], w: w, h: h };
        placed.push(Object.assign({ ccy: p.ccy }, best));
        g.fillStyle = withAlpha(c.surface, 0.75);
        g.fillRect(best.x - 1, best.y, best.w, best.h);
        g.fillStyle = bankColor(p.ccy);
        g.fillText(text, best.x, best.y + h / 2);
      });
      ch.$cbLabels = placed;
      ch.$cbDots = dots;
      if (!small) {
        g.fillStyle = c.muted;
        g.fillText("hike zone (pressure up)", ca.right - 150, ca.bottom - 12);
        g.fillText("cut zone (pressure down)", ca.left + 8, ca.top + 12);
      }
      g.restore();
    } };
    const ann = {};
    m.eps_lines.forEach(function (x, i) { ann["eps" + i] = { type: "line", xMin: x, xMax: x, borderColor: withAlpha(c.muted, 0.8), borderWidth: 1, borderDash: [2, 3] }; });
    ann.zero = { type: "line", yMin: 0, yMax: 0, borderColor: withAlpha(c.muted, 0.5), borderWidth: 1 };
    const chart = new Chart(canvas, {
      data: { datasets: ds }, plugins: [labels],
      options: { responsive: true, maintainAspectRatio: false, animation: false, parsing: false, events: [],
        scales: { x: { type: "linear", min: m.xr[0], max: m.xr[1], grid: { color: withAlpha(c.border, 0.7) }, ticks: { color: c.muted, stepSize: 0.5, callback: function (v) { return Math.abs(v * 2 - Math.round(v * 2)) < 1e-9 ? v : ""; } }, title: { display: true, text: "Need, pp", color: c.muted } },
                  y: { type: "linear", min: m.yr[0], max: m.yr[1], grid: { color: withAlpha(c.border, 0.7) }, ticks: { color: c.muted, stepSize: 0.5, callback: function (v) { return Math.abs(v * 2 - Math.round(v * 2)) < 1e-9 ? v : ""; } }, title: { display: true, text: "Stance, pp", color: c.muted } } },
        plugins: { legend: { display: false }, tooltip: { enabled: false }, annotation: { annotations: ann } } }
    });
    const tip = tipBox(canvas);
    const near = function (ev) {
      const r = canvas.getBoundingClientRect(), x = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left, y = (ev.touches ? ev.touches[0].clientY : ev.clientY) - r.top;
      let best = null, bd = 18;
      m.points.forEach(function (p) {
        const dx = chart.scales.x.getPixelForValue(p.x) - x, dy = chart.scales.y.getPixelForValue(p.y) - y, dd = Math.sqrt(dx * dx + dy * dy);
        if (dd < bd) { bd = dd; best = p; }
      });
      return best ? { p: best, x: x, y: y } : null;
    };
    const move = function (ev) { const h = near(ev); if (!h) { tip.hidden = true; return; } placeTip(tip, canvas, { x: h.x, y: h.y }, stanceTipHtml(h.p.ccy, st.banks[h.p.ccy])); };
    canvas.addEventListener("mousemove", move);
    canvas.addEventListener("click", move);
    canvas.addEventListener("touchstart", move, { passive: true });
    canvas.addEventListener("mouseleave", function () { tip.hidden = true; });
    state.charts.push(chart);
    return chart;
  }
  // the text alternative of the map: one line per point (bank, need, stance, situation today and in 12 months)
  function stanceMapAlt(st, m) {
    return '<ul class="cb-sr-only" id="cbStanceMapAlt">' + m.points.map(function (p) {
      const s = st.banks[p.ccy];
      return "<li>" + esc(p.ccy) + ": need " + fmtPP(s.need.v) + " pp, stance " + fmtPP(s.stance.v) + " pp (neutral interval " + fmtPP(s.stance.lo) + " to " + fmtPP(s.stance.hi) +
        "), situation " + esc(s.chip || "n/a") + "; if the rate is held, in 12 months: need " + fmtPP(s.need_12m.v) + " pp, " + esc(s.chip_12m || "n/a") + "</li>";
    }).join("") + "</ul>";
  }
  function stanceMapCard(st) {
    const m = stanceMapModel(st.banks, st.params);
    const note = m.excluded.length ? '<p class="cb-sub cb-st-note">Not on the map (no neutral-rate estimate, need only): ' +
      m.excluded.map(function (x) { return '<span data-ccy="' + esc(x.ccy) + '"><b>' + esc(x.ccy) + "</b> — " + esc(x.reason) + " (need " + stNum(st.banks[x.ccy].need.v, "pp", "need.v") + " pp)</span>"; }).join("; ") + ".</p>" : "";
    return '<section class="cb-card cb-chartcard cb-st-mapcard"><h3>Need and stance <small class="muted">filled = today · whisker = the neutral interval · hollow = if the rate is held, in 12 months</small></h3>' +
      '<div class="cb-chart-wrap cb-st-map"><canvas id="cbStanceMap" role="img" aria-label="Map of need (x, pp) and stance (y, pp) of each bank with a neutral estimate; ' +
      'the points are listed below the chart" aria-describedby="cbStanceMapAlt"></canvas></div>' + stanceMapAlt(st, m) + note + "</section>";
  }

  // ---- the table (8 rows): Cycle and market | Mandate check ----
  function cycleCell(s) {
    const c = s.cycle || {};
    if (!c.direction) return '<span class="cb-na" title="' + esc(c.na || "n/a") + '">' + NA + "</span>";
    const arrow = c.direction === "hike" ? "↑" : "↓", prev = c.previous;
    return '<span title="' + esc(s.cycle_line || "") + '">' + arrow + c.count + (prev ? ' <span class="muted">after ' + (prev.direction === "hike" ? "↑" : "↓") + prev.count + "</span>" : "") +
      (c.holds_since ? ' <span class="muted">· ' + c.holds_since + " hold" + (c.holds_since === 1 ? "" : "s") + "</span>" : "") + "</span>";
  }
  const LENS_KIND = { own_path: "vs the bank's path", market: "since the projection cutoff", constant_rate: "vs the assumed constant rate", unpublished: "no published rate path" };
  function lensCell(s) {
    const l = s.lens;
    if (!isNum(l.v)) return '<span class="cb-na" title="' + esc(l.na || "n/a") + '">' + NA + '</span><div class="cb-st-subline">' + esc(LENS_KIND[l.kind] || "") + "</div>";
    const est = l.flag === "ESTIMATE";
    return (est ? "≈ " : "") + stNum(l.v, "bp", "lens.v", null, "tone") + '<div class="cb-st-subline">' + esc(LENS_KIND[l.kind] + (l.year ? " " + l.year : "")) + "</div>";
  }
  function stanceBar(s) {
    const c = s.calc || {}, n = c.neutral;
    if (!n || !isNum(c.rate)) return '<span class="cb-na" title="' + esc(s.stance.na) + '">' + NA + "</span>";
    const lo = Math.min(n.lo, c.rate) - 0.25, hi = Math.max(n.hi, c.rate) + 0.25, w = function (v) { return ((v - lo) / (hi - lo) * 100).toFixed(1) + "%"; };
    const tip = "Policy rate " + stRate(s.rate_display) + " vs neutral " + fmtPub(n.mid) + "% (" + fmtPub(n.lo) + EN + fmtPub(n.hi) + "%, " + n.flag + ", " + fmtDate(n.asof) + ")";
    return '<span class="cb-st-bar" title="' + esc(tip) + '"><span class="cb-st-bar-n" style="left:' + w(n.lo) + ";width:calc(" + w(n.hi) + " - " + w(n.lo) + ')"></span>' +
      '<span class="cb-st-bar-i" style="left:' + w(c.rate) + '"></span></span> ' + stNum(s.stance.v, "pp", "stance.v") + ' <small class="muted">pp</small>';
  }
  function needCell(s) {
    return stNum(s.need.v, "pp", "need.v", s.need.na) + " → " + stNum(s.need_12m.v, "pp", "need_12m.v", s.need_12m.na) +
      '<div class="cb-st-subline">headline gap ' + stNum(s.calc.gap_head, "pp", "calc.gap_head", "no headline inflation") + "</div>";
  }
  function situationCell(s) {
    return stChip(s.chip, s.situation.v, s.situation.direction, s.situation.na) + " → " + stChip(s.chip_12m, s.situation_12m.v, s.situation_12m.direction, s.situation_12m.na) +
      '<div class="cb-st-flags">' + stFlagChips(s.flags, "", s) + stFlagChips(s.flags_12m, "12m", s) + "</div>";
  }
  function pressureCell(s, unit) {
    if (!isNum(s.pressure.v)) return '<span class="cb-na" title="' + esc(s.pressure.na) + '">' + NA + "</span>";
    return stNum(s.pressure.v, "bp100", "pressure.v", null, "tone") + (unit ? ' <small class="muted">' + unit + "</small>" : "") + '<div class="cb-st-subline">' + stNum(s.pressure.lo, "bp100", "pressure.lo") + " to " + stNum(s.pressure.hi, "bp100", "pressure.hi") + "</div>";
  }
  const STANCE_SORTS = [["m12", "Market 12M"], ["pressure", "Pressure"], ["lens", "Bank vs market"]];
  function stanceRows(st, key) {
    const val = function (s) { return key === "pressure" ? s.pressure.v : key === "lens" ? s.lens.v : s.m12_bp; };
    return BANK_ORDER.filter(function (c) { return st.banks[c]; }).map(function (c) { return st.banks[c]; }).sort(function (a, b) {
      const va = val(a), vb = val(b);
      if (!isNum(va) && !isNum(vb)) return BANK_ORDER.indexOf(a.bank) - BANK_ORDER.indexOf(b.bank);
      if (!isNum(va)) return 1;
      if (!isNum(vb)) return -1;
      return Number(vb) - Number(va) || BANK_ORDER.indexOf(a.bank) - BANK_ORDER.indexOf(b.bank);
    });
  }
  function stancePills(key) {
    return '<span class="cb-pills"><span class="cb-pills-label">Sort by:</span>' + STANCE_SORTS.map(function (x) {
      return '<button type="button" class="cb-pill' + (x[0] === key ? " active" : "") + '" data-sort="' + x[0] + '" aria-pressed="' + (x[0] === key) + '">' + x[1] + "</button>";
    }).join("") + "</span>";
  }
  function m12Cell(s) { return isNum(s.m12_bp) ? (s.m12_est ? "≈ " : "") + stNum(s.m12_bp, "bp", "m12_bp", null, "tone") : '<span class="cb-na">' + NA + "</span>"; }
  function stanceTableHtml(st, key) {
    const rows = stanceRows(st, key).map(function (s) {
      return '<tr class="cb-row" tabindex="0" data-ccy="' + esc(s.bank) + '" data-href="' + esc(bankUrlPage(s.bank)) + '#cbStanceZone">' +
        '<td><span class="cb-swatch" data-ccy="' + esc(s.bank) + '"></span><b>' + esc(s.bank) + '</b> <small class="muted">' + esc(stRate(s.rate_display)) + "</small></td>" +
        "<td>" + cycleCell(s) + '</td><td class="cb-n">' + m12Cell(s) + '</td><td class="cb-n cb-grp-end">' + lensCell(s) + "</td>" +
        "<td>" + stanceBar(s) + '</td><td class="cb-n">' + needCell(s) + "</td><td>" + situationCell(s) + '</td><td class="cb-n">' + pressureCell(s) + "</td></tr>";
    }).join("");
    return '<div class="cb-tablehead"><h3>Stance by bank <small class="muted">' + esc(stAsof(stanceAny(st))) + "</small></h3>" + stancePills(key) + "</div>" +
      '<div class="cb-scroll"><table class="cb-table cb-st-table" id="cbStanceTable"><thead>' +
      '<tr class="cb-grp"><th></th><th colspan="3" class="cb-grp-end">Cycle and market</th><th colspan="4">Mandate check</th></tr>' +
      '<tr><th>Bank</th><th>Cycle</th><th class="cb-n">Mkt 12M (bp)</th><th class="cb-n cb-grp-end">Bank vs market (bp)</th><th>Stance</th><th class="cb-n">Need now → 12m (pp)</th><th>Situation</th><th class="cb-n">Pressure (bp)</th></tr>' +
      "</thead><tbody>" + rows + "</tbody></table></div>";
  }
  function stanceListHtml(st, key) {
    const items = stanceRows(st, key).map(function (s) {
      const kv = function (k, v) { return '<div class="cb-st-kv"><span class="muted">' + k + "</span><span>" + v + "</span></div>"; };
      return '<a class="cb-st-item" data-ccy="' + esc(s.bank) + '" href="' + esc(bankUrlPage(s.bank)) + '#cbStanceZone"><div class="cb-st-item-head"><span class="cb-swatch" data-ccy="' + esc(s.bank) + '"></span><b>' + esc(s.bank) + "</b> " +
        '<small class="muted">' + esc(stRate(s.rate_display)) + "</small><span class=\"cb-st-item-chips\">" + situationCell(s) + "</span></div>" +
        kv("Cycle", cycleCell(s)) + kv("Mkt 12M", m12Cell(s) + " bp") + kv("Bank vs market", lensCell(s)) + kv("Stance", stanceBar(s)) + kv("Need now → 12m", needCell(s)) + kv("Pressure", pressureCell(s, "bp")) + "</a>";
    }).join("");
    return '<div class="cb-tablehead"><h3>Stance by bank</h3>' + stancePills(key) + '</div><div class="cb-st-list">' + items + "</div>";
  }
  function stanceAny(st) { return st.banks[BANK_ORDER.find(function (c) { return st.banks[c]; })] || {}; }
  function stanceHowHtml(how, id) {
    return '<details class="cb-htr cb-st-how" id="' + (id || "cbStanceHow") + '"><summary>ⓘ How to read</summary><div class="cb-sub"><ul class="cb-st-howlist">' +
      (how || []).map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul></div></details>";
  }
  function renderStanceTab(box, st) {
    if (!st || st.na || !st.banks) { box.innerHTML = '<p class="cb-loading">' + esc((st && st.na) || "The stance is written by the next Central Banks refresh.") + "</p>"; return; }
    box.innerHTML = '<p class="cb-sub cb-st-intro">A mandate benchmark for each bank: the need its own mandate creates, where its rate stands against its own neutral estimate, and the rule that joins them. ' +
      '<a href="#stance-how" data-open-how="cbStanceHow">How to read</a></p>' + stanceHowHtml(st.how_to_read) +
      '<div class="desk-only" id="cbStanceTableBox"></div><div class="phone-only" id="cbStanceListBox"></div>' + stanceMapCard(st);
    sortable(document.getElementById("cbStanceTableBox"), "m12", function (k) { return stanceTableHtml(st, k); });
    sortable(document.getElementById("cbStanceListBox"), "m12", function (k) { return stanceListHtml(st, k); });
    const canvas = document.getElementById("cbStanceMap");
    registerRedraw(function () { drawStanceMap(canvas, st, isPhone()); });
    wireHowLinks(box);
  }
  function wireHowLinks(scope) {
    scope.querySelectorAll("[data-open-how]").forEach(function (a) {
      a.addEventListener("click", function (ev) { const d = document.getElementById(a.dataset.openHow); if (d) { ev.preventDefault(); d.open = true; d.scrollIntoView({ block: "nearest" }); } });
    });
  }

  // ---- bank page: zone "Stance and need" (below the trajectory zone) ----
  function calcLines(s) {
    const c = s.calc, n = c.neutral, inp = c.inputs || {}, r = s.rate_display || {};
    const L = [];
    const pi = function (v, k) { return stNum(v, "pub", k) + "%"; };
    const tgt = isNum(c.target) ? pi(c.target, "calc.target") : "band " + fmtPub(c.band[0]) + EN + fmtPub(c.band[1]) + "%";
    L.push("i = " + stNum(c.rate, "pub", "calc.rate") + "%" + (r.range ? " (midpoint of the target range " + fmtRate(r.lower) + EN + fmtRate(r.upper) + "%)" : "") + " · " + esc(((inp.rate || {}).source) || ""));
    L.push("π underlying = " + pi(c.pi_under, "calc.pi_under") + " (" + esc(((inp.pi_under || {}).asof) || "") + ") · π* = " + tgt);
    L.push("gap = " + (isNum(c.target) ? pi(c.pi_under, "calc.pi_under") + " − " + pi(c.target, "calc.target") : "distance to the band") + " = " + stNum(c.gap_under, "pp", "calc.gap_under") + " pp");
    L.push("y = " + stNum(c.y, "pp", "calc.y", "no slack estimate: the term is left out") + (c.y_how ? " (" + esc(c.y_how) + ")" : "") + " · y/3 = " + stNum(isNum(c.y) ? c.y / 3 : null, "pp", null, "0 (no slack)"));
    L.push("need x = gap + y/3 = " + stNum(c.x, "pp", "calc.x") + " pp · headline: gap " + stNum(c.gap_head, "pp", "calc.gap_head") + " → x " + stNum(c.x_headline, "pp", "calc.x_headline"));
    if (n) {
      L.push("neutral N = " + stNum(n.mid, "pub", "calc.neutral.mid") + "% (" + stNum(n.lo, "pub", "calc.neutral.lo") + EN + stNum(n.hi, "pub", "calc.neutral.hi") + "%) · " + esc(n.flag) + " · " + esc(fmtDate(n.asof)) + (n.stale ? " · stale" : ""));
      if (c.neutral_check) L.push("check: median of " + c.neutral_check.dots + " longer-run dots " + fmtPub(c.neutral_check.from_dots) + " → rounded " + fmtPub(c.neutral_check.rounded) + " vs published " + fmtPub(c.neutral_check.published) + (c.neutral_check.ok ? " ✓" : " — MISMATCH"));
      L.push("stance S = i − N = " + stNum(c.rate, "pub", "calc.rate") + " − " + stNum(n.mid, "pub", "calc.neutral.mid") + " = " + stNum(c.S, "pp", "calc.S") + " pp (" + stNum(c.S_lo, "pp", "calc.S_lo") + " to " + stNum(c.S_hi, "pp", "calc.S_hi") + ")");
      L.push("pressure G = 1.5 × " + stNum(c.x, "pp", "calc.x") + " − " + stNum(c.S, "pp", "calc.S") + " = " + stNum(c.G, "pp", "calc.G") + " pp = " + stNum(c.G, "bp100", "calc.G") + " bp (" + stNum(c.G_lo, "bp100", "calc.G_lo") + " to " + stNum(c.G_hi, "bp100", "calc.G_hi") + ")");
      L.push("situation today: " + stChip(s.chip, s.situation.v, s.situation.direction) + " (δ " + s.params.delta_pp + " pp, ε " + s.params.epsilon_pp + " pp)");
      L.push("pivot = π* + S/1.5 − y/3 = " + stNum(c.pivot, "lvl", "calc.pivot", s.pivot.na) + "% (underlying inflation at which the pressure is zero)");
    } else {
      L.push("neutral: n/a — " + esc(s.stance.na) + " (need only)");
    }
    L.push("in 12 months: π = " + stNum(c.pi_12m, "pub", "calc.pi_12m", s.need_12m.na) + "% (" + esc(c.pi_12m_period || "") + ", " + esc(c.pi_12m_basis || "") + ") · gap " + stNum(c.gap_12m, "pp", "calc.gap_12m") +
      " · y " + stNum(c.y_12m, "pp", "calc.y_12m", "no slack") + (c.y_12m_how ? " (" + esc(c.y_12m_how) + ")" : "") + " · x " + stNum(c.x_12m, "pp", "calc.x_12m"));
    if (n && isNum(c.G_12m)) L.push("G in 12 months = 1.5 × " + stNum(c.x_12m, "pp", "calc.x_12m") + " − " + stNum(c.S, "pp", "calc.S") + " = " + stNum(c.G_12m, "pp", "calc.G_12m") + " pp → " + stChip(s.chip_12m, s.situation_12m.v, s.situation_12m.direction));
    return L;
  }
  function stanceHistoryHtml(s) {
    const h = s.history || [];
    if (h.length < 5) return '<p class="cb-sub" data-st-history="text">History starts on ' + esc(h.length ? fmtDate(h[0].date) : "the first daily snapshot") + ".</p>";
    return '<div class="cb-chart-wrap cb-st-hist"><canvas id="cbStanceHist" aria-label="Pressure over time"></canvas></div>';
  }
  function drawStanceHistory(canvas, s, ccy) {
    const c = colors(), col = bankColor(ccy), h = s.history.filter(function (p) { return isNum(p.G_bp); });
    const pts = function (k) { return h.map(function (p) { return { x: ms(p.date), y: p[k] }; }); };
    const chart = new Chart(canvas, {
      data: { datasets: [{ type: "line", data: pts("G_lo_bp"), borderColor: "transparent", pointRadius: 0, fill: false },
                         { type: "line", data: pts("G_hi_bp"), borderColor: "transparent", pointRadius: 0, fill: "-1", backgroundColor: withAlpha(col, 0.15) },
                         { type: "line", data: pts("G_bp"), borderColor: col, backgroundColor: col, pointRadius: 2, borderWidth: 2 }] },
      options: { responsive: true, maintainAspectRatio: false, animation: false, parsing: false, events: [],
                 scales: { x: { type: "linear", ticks: { color: c.muted, callback: function (v) { return fmtDay(isoOf(v)); } }, grid: { color: withAlpha(c.border, 0.7) } },
                           y: { ticks: { color: c.muted }, grid: { color: withAlpha(c.border, 0.7) }, title: { display: true, text: "Pressure, bp", color: c.muted } } },
                 plugins: { legend: { display: false }, tooltip: { enabled: false },
                            annotation: { annotations: { zero: { type: "line", yMin: 0, yMax: 0, borderColor: withAlpha(c.muted, 0.6), borderWidth: 1 } } } } }
    });
    state.charts.push(chart);
  }
  function stanceZoneHtml(d) {
    const s = d.stance;
    if (!s || s.na) return '<h2 class="cb-zone" id="cbStanceZone">Stance and need</h2><p class="cb-loading">' + esc((s && s.na) || "The stance is written by the next Central Banks refresh.") + "</p>";
    const m = s.mandate || {}, nt = s.next_test, lens = s.lens;
    const head = '<div class="cb-st-head">' + stChip(s.chip, s.situation.v, s.situation.direction, s.situation.na) + ' <span class="muted">today →</span> ' +
      stChip(s.chip_12m, s.situation_12m.v, s.situation_12m.direction, s.situation_12m.na) + ' <span class="muted">in 12 months, rate held</span> ' +
      stFlagChips(s.flags, "", s) + stFlagChips(s.flags_12m, "12m", s) +
      ((s.flags || []).indexOf("mandate_review_pending") >= 0 ? ' <span class="cb-st-badge" title="The framework watch found a change on the bank\'s framework pages; the mandate entry is reviewed by hand.">Framework change detected — pending review</span>' : "") + "</div>";
    const lines = '<p class="cb-st-lines">' + [s.cycle_line, s.market_line].filter(Boolean).map(esc).join(" · ") + "</p>";
    const sum = '<p class="cb-st-summary">' + (s.summary || []).map(esc).join(" ") + "</p>";
    const p = d.path || {}, first = (p.points || []).find(function (x) { return p.next && x.meeting === p.next.decision; });
    const mk = '<section class="cb-card cb-st-card"><h3>Market · pricing</h3>' +
      '<div class="cb-st-kv"><span class="muted">Next meeting</span><span>' + (p.next ? esc(fmtDate(p.next.decision)) : NA) + (first && first.prob ? " · " + esc(probText(first.prob)) : first && isNum(first.cum_bp) ? " · ≈ " + fmtBP(first.cum_bp) + " bp" : "") + "</span></div>" +
      '<div class="cb-st-kv"><span class="muted">12 months</span><span>' + m12Cell(s) + " bp" + (p.m12 && p.m12.meeting ? ' <small class="muted">after ' + esc(fmtDay(p.m12.meeting)) + "</small>" : "") + "</span></div>" +
      '<div class="cb-st-kv"><span class="muted">Data</span><span>' + esc(dataDateText(p) || stAsof(s)) + "</span></div></section>";
    const bk = '<section class="cb-card cb-st-card"><h3>Bank · projections</h3>' +
      '<div class="cb-st-kv"><span class="muted">Bank vs market</span><span>' + lensCell(s) + "</span></div>" +
      '<div class="cb-st-kv"><span class="muted">Publication</span><span>' + esc(s.publication || (d.ccy === "USD" ? "FOMC Summary of Economic Projections" : NA)) + "</span></div>" +
      '<div class="cb-st-kv"><span class="muted">Horizon projection</span><span>' + (isNum(lens.horizon_gap) ? "gap " + stNum(lens.horizon_gap, "pp", "lens.horizon_gap") + " pp (" + esc(lens.horizon_period || "") + ") · " +
        '<span class="cb-st-cons ' + (lens.consistent ? "ok" : "no") + '">' + (lens.consistent ? "consistent" : "inconsistent") + "</span>" : '<span class="cb-na">' + NA + "</span>") + "</span></div></section>";
    const ru = '<section class="cb-card cb-st-card"><h3>Rule · mandate check</h3>' +
      '<div class="cb-st-kv"><span class="muted">Need now → 12m</span><span>' + needCell(s) + "</span></div>" +
      '<div class="cb-st-kv"><span class="muted">Stance</span><span>' + stanceBar(s) + "</span></div>" +
      '<div class="cb-st-kv"><span class="muted">Pressure</span><span>' + pressureCell(s, "bp") + "</span></div>" +
      '<dl class="cb-st-mandate">' +
      "<dt>Target</dt><dd>" + esc(m.target || NA) + (m.target_measure ? " · " + esc(m.target_measure) : "") + "</dd>" +
      "<dt>Underlying</dt><dd>" + esc(m.underlying || NA) + (m.rule_text ? ' <small class="muted">rule ' + esc(m.rule_text) + "</small>" : "") + "</dd>" +
      "<dt>Horizon</dt><dd>" + esc(m.horizon || NA) + "</dd><dt>Mandate</dt><dd>" + esc(m.mandate_type || NA) + "</dd>" +
      "<dt>Version</dt><dd>from " + esc(fmtDate(m.effective_from)) + (m.effective_to ? " to " + esc(fmtDate(m.effective_to)) : "") + "</dd>" +
      "<dt>Last verified</dt><dd>" + esc(fmtDate(m.last_verified)) + "</dd>" +
      "<dt>Source</dt><dd>" + (m.source_url ? '<a href="' + esc(m.source_url) + '" target="_blank" rel="noopener">' + esc(m.source_title || m.source_url) + "</a>" : NA) + "</dd></dl></section>";
    // Next test only with a date in the calendar (S2.1); the pivot stays, on its own when there is no date
    const pivot = isNum(s.pivot.v) ? '<span class="cb-st-pivot">Pivot ' + stNum(s.pivot.v, "lvl", "pivot.v") + '% <small class="muted">underlying inflation at which the pressure is zero</small></span>' : "";
    const test = '<div class="cb-st-test">' + (nt && nt.date ? '<span data-st-next>Next test: <b>' + esc(fmtDate(nt.date)) + "</b> " + esc(nt.title) +
      ' <small class="muted">schedule: ' + esc(nt.schedule) + "</small></span>" + (pivot ? " · " : "") : "") + pivot + "</div>";
    const calc = '<details class="cb-st-calc" id="cbStanceCalc"><summary>Show calculation</summary><ol class="cb-st-calclist">' + calcLines(s).map(function (l) { return "<li>" + l + "</li>"; }).join("") + "</ol></details>";
    const hist = '<div class="cb-st-histbox"><b>History</b> <small class="muted">pressure, bp, one point per daily snapshot</small>' + stanceHistoryHtml(s) + "</div>";
    const how = '<a href="' + esc(CFG.urls.overview_page) + '#stance-how" class="cb-st-howlink">How to read</a>';
    return '<h2 class="cb-zone" id="cbStanceZone">Stance and need</h2><section class="cb-st-zone">' + head + lines + sum +
      '<div class="cb-st-cards">' + mk + bk + ru + "</div>" + '<div class="cb-st-foot">' + test + calc + hist + how + "</div></section>";
  }
  function wireStanceZone(d) {
    const s = d.stance;
    if (!s || s.na) return;
    const cv = document.getElementById("cbStanceHist");
    if (cv) registerRedraw(function () { drawStanceHistory(cv, s, d.ccy); });
  }

  // ---- phone (Faza 11C), max-width 600px ------------------------------------------------------------------------------------
  // Overview: a list next to each desktop table (CSS shows one). Bank page: rendered either for the desktop or for the phone and
  // re-rendered when the viewport crosses 600px (one chart canvas, one id).
  function isPhone() { return !!(window.PhoneUI && window.PhoneUI.isPhone()); }
  const CHEV = '<svg class="ph-ico ph-chev" width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
  const CHEV_DOWN = '<svg class="ph-ico" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
  // bank page on the phone
  function acc(id, title, sub, body, open) {
    return '<section class="cb-acc" id="' + id + '"><button type="button" class="cb-acc-head" aria-expanded="' + (open ? "true" : "false") + '" aria-controls="' + id + '-body">' +
      '<span class="cb-acc-title"><b>' + esc(title) + "</b>" + (sub ? '<span class="cb-ph-sub">' + sub + "</span>" : "") + "</span>" + CHEV_DOWN + "</button>" +
      '<div class="cb-acc-body" id="' + id + '-body"' + (open ? "" : " hidden") + ">" + body + "</div></section>";
  }
  function miniTable(head, rows, empty) {
    return '<div class="cb-scroll"><table class="cb-table cb-mini cb-ph-table"><thead><tr>' + head.map(function (h, i) { return "<th" + (i ? ' class="cb-n"' : "") + ">" + h + "</th>"; }).join("") +
      "</tr></thead><tbody>" + (rows.join("") || '<tr><td colspan="' + head.length + '" class="muted">' + esc(empty || "n/a") + "</td></tr>") + "</tbody></table></div>";
  }
  function renderBankPhone(d, dayMode) {
    const blocks = [["cbAccLatest", latestDecisionBlock(d), true], ["cbAccProj", projectionsBlock(d), false], ["cbAccSpeeches", speechesBlock(d), false], ["cbAccDecisions", decisionHistoryBlock(d), false]];
    root.innerHTML = bankBar(d.ccy) + pathCards(d) + '<div class="cb-pathgrid">' + pathTableHtml(d.path, d.calendar) + pathChartCard(d) + "</div>" + stanceZoneHtml(d) + '<h2 class="cb-zone">The bank</h2><div class="cb-accs">' +
      blocks.filter(function (x) { return x[1]; }).map(function (x) { return acc(x[0], x[1].title, x[1].sub, x[1].html, x[2]); }).join("") +
      acc("cbAccMethod", "How is this calculated?", "methodology, sources, calendar", referencePanel(d).replace(/^<details[^>]*><summary>[^<]*<\/summary>/, "").replace(/<\/details>$/, "")
        .replace("<th>Official time (UTC)</th><th>First seen (UTC)</th>", "<th>Official, UTC</th><th>Seen, UTC</th>"), false) +
      "</div>";
    root.querySelectorAll(".cb-acc-head").forEach(function (b) {
      b.addEventListener("click", function () {
        const open = b.getAttribute("aria-expanded") !== "true";
        b.setAttribute("aria-expanded", String(open));
        document.getElementById(b.getAttribute("aria-controls")).hidden = !open;
      });
    });
    wirePathChart(d.path);
    wireStanceZone(d);
    paintSwatches();
  }

  // ---- pair page ------------------------------------------------------------------------------------------------------------
  // The same 12 months as Compare, from the two banks' `path`: the values of the Pairs table as cards, the two paths (bp vs now) with
  // the Compare hover plus a "Gap" row. The year-end fields of pairs.json are no longer shown.
  function renderPair(pj, p, b, q) {
    state.meta = pj.meta;
    destroyCharts();
    state.redraw = [];
    titleEl.innerHTML = esc(p.display) + ' <span class="cb-title-sub">' + esc(b.bank.short) + " vs " + esc(q.bank.short) + "</span>";
    document.title = p.display + " | Central Banks | Dashboard";
    if (!b.path || !q.path) { root.innerHTML = PENDING; return; }
    const paths = {};
    paths[p.base] = b.path; paths[p.quote] = q.path;
    const r = pairCalc(p, paths);
    metaEl.innerHTML = '<a href="' + esc(CFG.urls.overview_page) + '#pairs">← All pairs</a>' + DOT + esc(p.base + " " + (dataDateText(b.path) || "") + DOT + p.quote + " " + (dataDateText(q.path) || "")) +
      DOT + '<a href="' + esc(pj.banks[p.base].href) + '">' + esc(p.base) + " page</a>" + DOT + '<a href="' + esc(pj.banks[p.quote].href) + '">' + esc(p.quote) + " page</a>";
    root.innerHTML = bankBar("pairs") + '<p class="cb-sub cb-pairs-help">' + esc(PAIRS_HELP) + "</p>" + pairCards(r) +
      '<section class="cb-card cb-chartcard"><h3>Implied paths <small class="muted">' + esc(p.base) + " and " + esc(p.quote) + ", bp vs now, next 12 months</small></h3>" +
      '<div class="cb-legend-box" id="cbPairLegend"></div><div class="cb-chart-wrap cb-chart-tall"><canvas id="cbPairChart"></canvas></div>' +
      howToRead("Hover (or tap) for both banks at a date and the rate gap then. Hollow points and dashed segments = estimates. Click a bank above the chart to hide or show it.") + "</section>" +
      '<section class="cb-card cb-method-link"><a href="' + esc(pj.banks[p.base].href) + '#cbMethod">How is this calculated?</a></section>';
    const pairCanvas = document.getElementById("cbPairChart"), legend = legendState([p.base, p.quote]), names = {};
    names[p.base] = b.path.name || b.bank.short; names[p.quote] = q.path.name || q.bank.short;
    let chart = null;
    registerRedraw(function () {
      chart = drawCompareChart(pairCanvas, paths, "bp", { legend: legend, extra: function (t) { return [gapRow(b.path, q.path, t)]; } });
    });
    wireLegend(document.getElementById("cbPairLegend"), legend, names, function () { destroyCharts(); state.redraw.forEach(function (fn) { fn(); }); }, function () { return chart; });
    if (window.PhoneUI) window.PhoneUI.foldHowToRead();
    if (!themeWatched) { themeWatched = true; watchTheme(); }
    paintSwatches();
    tick();
  }

  // ---- boot -------------------------------------------------------------------------------------------------------------------
  function failHtml(e) { return '<p class="cb-fail">Failed to load Central Banks data (' + esc(e && e.message ? e.message : e) + ").</p>"; }
  function getJSON(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) { if (!r.ok) throw new Error(url + " HTTP " + r.status); return r.json(); });
  }
  function bankUrl(ccy) { return CFG.urls.bank.replace("{ccy}", String(ccy).toLowerCase()); }

  function boot() {
    if (!SP) { root.innerHTML = failHtml(new Error("score-palette.js missing")); return; }
    if (!window.Chart) { root.innerHTML = failHtml(new Error("Chart.js missing")); return; }
    let job;
    if (CFG.page === "overview") job = getJSON(CFG.urls.overview).then(renderOverview);
    else if (CFG.page === "bank") job = getJSON(bankUrl(CFG.ccy)).then(function (d) {
      renderBank(d);
      const mq = window.PhoneUI && window.PhoneUI.mq;                  // re-render when the viewport crosses the phone breakpoint
      if (mq && mq.addEventListener) mq.addEventListener("change", function () { renderBank(d); });
    });
    else if (CFG.page === "pair") {
      job = getJSON(CFG.urls.pairs).then(function (pj) {
        const p = pj.pairs.find(function (x) { return x.pair === CFG.pair; });
        if (!p) throw new Error("unknown pair " + CFG.pair);
        return Promise.all([getJSON(bankUrl(p.base)), getJSON(bankUrl(p.quote))]).then(function (bq) { renderPair(pj, p, bq[0], bq[1]); });
      });
    } else job = Promise.reject(new Error("unknown page " + CFG.page));
    job.catch(function (e) { root.innerHTML = failHtml(e); if (window.console) console.error(e); });
    setInterval(tick, 15000);
  }
  boot();
})();
