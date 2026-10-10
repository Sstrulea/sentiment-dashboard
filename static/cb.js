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
      '<div class="cb-chart-wrap cb-compare-wrap"><canvas id="cbCompareChart"></canvas></div>' +
      '<div class="cb-sub" data-htr>One line per bank from today, a point on each decision date. Hover (or tap / drag) for every bank at a date: the rate in force after the last meeting on or before it, ' +
      'in bp vs now and in 25 bp moves. Hollow points and dashed segments = estimates (no contract isolates the meeting). Click a legend item to hide a bank. ' +
      '<span class="cb-hawk"><b>Blue = hawkish</b></span>, <span class="cb-dove"><b>red = dovish</b></span> in the tables; the sign is always written out.</div></section>' +
      '<div class="desk-only" id="cbCompareTable"></div><div class="phone-only" id="cbCompareList"></div></section>' +
      '<section id="cbPairs" hidden><p class="muted cb-loading">Loading pairs…</p></section>';
    let mode = "bp";
    const canvas = document.getElementById("cbCompareChart");
    registerRedraw(function () { drawCompareChart(canvas, ov.paths, mode); });
    root.querySelectorAll(".cb-tbtn[data-mode]").forEach(function (b) {
      b.addEventListener("click", function () {
        mode = b.dataset.mode;
        root.querySelectorAll(".cb-tbtn[data-mode]").forEach(function (x) { x.classList.toggle("active", x === b); x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
        destroyCharts(); state.redraw.forEach(function (fn) { fn(); });
      });
    });
    sortable(document.getElementById("cbCompareTable"), "next", function (k) { return upcomingTableHtml(ov.paths, k); });
    sortable(document.getElementById("cbCompareList"), "next", function (k) { return upcomingListHtml(ov.paths, k); });
    let pairsLoaded = false;
    const show = function (tab) {                                     // Compare / Pairs: the two first items of the bank bar (#pairs)
      root.querySelectorAll(".cb-bankbar-item").forEach(function (x) {
        const on = (x.dataset.k === tab);
        x.classList.toggle("active", on);
        if (on) x.setAttribute("aria-current", "page"); else x.removeAttribute("aria-current");
      });
      document.getElementById("cbCompare").hidden = tab !== "compare";
      document.getElementById("cbPairs").hidden = tab !== "pairs";
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
    root.querySelectorAll('.cb-bankbar-item[data-k="compare"], .cb-bankbar-item[data-k="pairs"]').forEach(function (a) {
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        if (history.replaceState) history.replaceState(null, "", a.dataset.k === "pairs" ? "#pairs" : window.location.pathname);
        show(a.dataset.k);
      });
    });
    show(window.location.hash === "#pairs" ? "pairs" : "compare");
    if (window.PhoneUI) window.PhoneUI.foldHowToRead();
    if (!hashWired) {
      hashWired = true;
      window.addEventListener("hashchange", function () { show(window.location.hash === "#pairs" ? "pairs" : "compare"); });
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
  function paintSwatches() { document.querySelectorAll(".cb-swatch[data-ccy]").forEach(function (el) { el.style.background = bankColor(el.dataset.ccy); }); }

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
    const items = [{ k: "compare", label: "Compare", href: CFG.urls.overview_page }, { k: "pairs", label: "Pairs", href: CFG.urls.overview_page + "#pairs" }]
      .concat(BANK_ORDER.map(function (c) { return { k: c, label: c, href: bankUrlPage(c) }; }));
    return '<nav class="cb-bankbar" aria-label="Central banks">' + items.map(function (it) {
      return '<a class="cb-bankbar-item' + (it.k === active ? " active" : "") + '" data-k="' + esc(it.k) + '" href="' + esc(it.href) + '"' + (it.k === active ? ' aria-current="page"' : "") + ">" +
        (it.k === "compare" || it.k === "pairs" ? "" : '<span class="cb-swatch" data-ccy="' + esc(it.k) + '"></span>') + esc(it.label) + "</a>";
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
    chart.$cb = { snap: o.snap || null, yLabel: o.yLabel, show: function (ch, cr) { placeTip(el, canvas, cr, o.html(ch, cr)); }, hide: function () { el.hidden = true; } };
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
  // Compare (every bank) and the pair page (two banks + a "Gap" row in the card): opts.extra(t) adds rows below the banks
  function drawCompareChart(canvas, paths, mode, opts) {
    opts = opts || {};
    const c = colors(), series = compareChartModel(paths, mode);
    const asof = Math.min.apply(null, series.map(function (s) { return s.data[0].x; }).concat([Date.now()]));
    const xMax = asof + 365 * DAY_MS + 20 * DAY_MS;
    const ds = series.map(function (s) {
      const col = bankColor(s.ccy);
      return { type: "line", label: s.ccy + " " + s.short, cbLabel: s.ccy, cbCcy: s.ccy, data: s.data, borderColor: col, backgroundColor: col, borderWidth: 2, tension: 0, stepped: false,
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
          legend: { position: "bottom", labels: { color: c.fg, usePointStyle: true, boxWidth: 8 } },
          tooltip: { enabled: false },
          annotation: { annotations: mode === "level" ? {} : { zero: { type: "line", yMin: 0, yMax: 0, borderColor: withAlpha(c.muted, 0.6), borderWidth: 1 } } }
        }
      }
    });
    hoverChart(chart, canvas, {
      yLabel: function (v) { return mode === "level" ? Number(v).toFixed(2) + "%" : fmtSigned(v, 1); },
      html: function (ch, cr) {
        const t = dayOf(cr.xv), hidden = {};
        ch.data.datasets.forEach(function (d, i) { if (!ch.isDatasetVisible(i)) hidden[d.cbCcy] = true; });
        const rows = compareHoverRows(paths, t, mode, hidden).concat(opts.extra ? opts.extra(t) : []);
        return '<div class="cb-tt-title">' + esc(tipTitle(t, asof)) + "</div>" + ttRowsHtml(rows);
      }
    });
    state.charts.push(chart);
    return chart;
  }
  // the bank page: cards, the Now / 1w / 3w chart and the per-meeting table
  function pathHeader(d) {
    const pj = d.path, cur = pj.current, b = cur.benchmark;
    const on = b ? (b.estimated ? esc(b.name) : esc(b.name) + " " + (isNum(b.value) ? fmtRate(b.value, 3) + "%" : NA) + (b.date ? ' <span class="muted">(' + fmtDay(b.date) + ")</span>" : "")) : NA;
    return '<section class="cb-card cb-head cb-path-head"><div class="cb-head-main"><div class="cb-kicker">Policy rate</div><div class="cb-bigrate">' + rateHtml(d.summary.rate) + "</div>" +
      '<div class="cb-sub">' + esc(d.bank.policy_rate || "") + "</div>" + (d.unverified.length ? '<div class="cb-badges">' + unverifiedChips(d) + "</div>" : "") + "</div>" +
      '<div class="cb-head-next"><div class="cb-kicker">Overnight</div><div class="cb-sub">' + on + '</div><div class="cb-kicker">Market data</div><div class="cb-sub">' +
      (pj.data_asof ? fmtDate(pj.data_asof) + (pj.data_label ? DOT + esc(pj.data_label) : "") : NA) + "</div></div></section>";
  }
  function probBar2(pt) {
    const pr = pt && pt.prob;
    if (!pr) return "";
    const cut = pr.dir === "cut", sgn = cut ? MINUS : "+", p = Number(pr.p) || 0;
    const lo = pr.n === 0 ? "Hold" : sgn + 25 * pr.n + " bp", hi = sgn + 25 * (pr.n + 1) + " bp";
    return '<div class="cb-prob2" role="img" aria-label="' + esc(lo + " " + pct(1 - p) + ", " + hi + " " + pct(p)) + '"><span class="cb-prob2-a" style="width:' + ((1 - p) * 100).toFixed(1) + '%">' + esc(lo) + " " + pct(1 - p) +
      '</span><span class="cb-prob2-b ' + (cut ? "cb-bar-dove" : "cb-bar-hawk") + '" style="width:' + (p * 100).toFixed(1) + '%">' + (p >= 0.12 ? esc(hi) + " " + pct(p) : "") + "</span></div>";
  }
  function pathCards(d) {
    const pj = d.path, n = pj.next, pt = firstPoint(pj), mv = moveText(pt), m12 = m12Text(pj.m12);
    const next = '<section class="cb-card cb-pcard"><h3>Next meeting</h3>' + (n ? '<div class="cb-bigdate">' + esc(weekday(n.decision)) + " " + fmtDate(n.decision) + ' <span class="cb-cdbig">' + cdSpan(n) + "</span></div>" +
      '<div class="cb-sub">' + esc(timeText(n.time) || "") + (n.effective ? DOT + "effective " + fmtDay(n.effective) : "") + " " + blackoutBadge(d.summary.next) + "</div>" +
      (mv ? '<div class="cb-bigrate ' + (pt.step_bp > 0 ? "cb-hawk" : pt.step_bp < 0 ? "cb-dove" : "") + '">' + esc(mv) + flagBadge(pt.flag) + "</div>" + probBar2(pt)
          : jointText(pt) ? '<div class="cb-sub">' + jointSpan(pt) + "</div>"
          : '<div class="cb-sub">' + NA + " " + esc(shortNa((pt && (pt.na || pt.step_na)) || "n/a")) + "</div>") : '<div class="cb-sub">' + NA + " no upcoming meeting</div>") + "</section>";
    const year = '<section class="cb-card cb-pcard"><h3>12 months</h3>' + (m12 ? '<div class="cb-bigrate ' + (pj.m12.cum_bp > 0 ? "cb-hawk" : pj.m12.cum_bp < 0 ? "cb-dove" : "") + '">' + esc(m12) + flagBadge(pj.m12.flag) + "</div>" +
      '<div class="cb-sub">Implied ' + fmtRate(pj.m12.rate, 3) + "% after the " + fmtDate(pj.m12.meeting) + " meeting</div>" : '<div class="cb-sub">' + NA + " " + esc(shortNa(pj.m12.na)) + "</div>") +
      (pj.spread_note ? '<div class="cb-sub muted">' + esc(pj.spread_note) + "</div>" : "") + "</section>";
    const one = function (k, label) {
      const m = pj.delta[k];
      return '<div class="cb-kv"><span>' + label + "</span>" + (m && isNum(m.v) ? valueSpan(m, 1, " bp") : '<span class="cb-na">' + esc(shortNa((m && m.na) || "n/a")) + "</span>") + "</div>";
    };
    const vs = function (k, fb) { const a = pj.history[k] && pj.history[k].asof; return a ? "vs " + fmtDay(a) : fb; };
    const rep = '<section class="cb-card cb-pcard"><h3>Repricing <small class="muted">12-month level</small></h3>' + one("1w", vs("1w", "vs 1 week ago")) + one("3w", vs("3w", "vs 3 weeks ago")) + "</section>";
    return '<div class="cb-grid cb-pcards">' + next + year + rep + "</div>";
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
  function drawPathChart(canvas, pj, stepped, hidden) {
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
      html: function (ch, cr) { return bankTipHtml(pj, isoOf(cr.xv), hidden); }
    });
    state.charts.push(chart);
    return chart;
  }
  function pathChartCard(d) {
    const pj = d.path;
    return '<section class="cb-card cb-chartcard"><h3>Implied path <small class="muted">next 12 months</small>' +
      '<span class="cb-toggle" role="group" aria-label="Line shape"><button type="button" class="cb-tbtn" data-shape="step" aria-pressed="false" title="Draw the path as steps (the rate changes at each meeting)">Step</button></span></h3>' +
      '<div id="cbSeriesTabs">' + seriesTabsHtml(pj, {}) + '</div><div class="cb-chart-wrap"><canvas id="cbPathChart"></canvas></div><div class="cb-sub">' + esc(pathHelp(pj)) + "</div></section>";
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
  function pathTableHtml(pj) {
    const rows = pathTableRows(pj).map(function (r) {
      const ap = r.est ? "≈ " : "";
      return "<tr" + (r.est ? ' class="cb-est"' : "") + '><td class="cb-date">' + fmtDate(r.meeting) + flagBadge(r.flag) + "</td>" +
        (r.rate ? '<td class="cb-n">' + esc(r.rate) + "</td>" : naTd(r.na)) +
        '<td class="cb-n">' + (r.prob ? esc(r.prob) : r.rate ? '<span class="muted" title="' + esc(r.est ? "estimate: no probability" : "no per-meeting probability") + '">' + NA + "</span>" : naSpan(r.na)) + "</td>" +
        (r.moves ? "<td>" + esc(r.moves) + "</td>" : "<td>" + naSpan(r.na) + "</td>") +
        (isNum(r.cum) ? txtTd(r.cum, esc(ap + fmtSigned(r.cum, 1))) : naTd(r.na)) +
        (isNum(r.dW) ? txtTd(r.dW, esc(ap + fmtSigned(r.dW, 1))) : naTd(r.dWna)) + "</tr>";
    }).join("");
    return '<section class="cb-card"><h3>By meeting <small class="muted">next 12 months</small></h3><div class="cb-scroll"><table class="cb-table cb-rp cb-pathtable"><thead><tr><th>Meeting</th><th class="cb-n">Implied rate</th>' +
      '<th class="cb-n" title="EXACT / CURVE only: whole 25 bp moves plus the probability of one more">Probability</th><th title="Cumulative 25 bp moves priced by this meeting">Hikes</th><th class="cb-n">Δ vs now (bp)</th><th class="cb-n" title="Change of the implied rate at this meeting over 5 business days">Δ vs 1w (bp)</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="6" class="muted">' + esc(pj.na || "no meeting in the next 12 months") + "</td></tr>") + "</tbody></table></div><div class=\"cb-sub\">≈ = estimate. " + NA + " = not available, hover for the reason.</div></section>";
  }
  function wirePathChart(pj) {
    const canvas = document.getElementById("cbPathChart");
    if (!canvas) return;
    let stepped = false;
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
    registerRedraw(function () { paintTabs(); drawPathChart(canvas, pj, stepped, hidden); });
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
  function gapCard(d) {
    const g = d.gap, bank = d.chart.bank;
    if (g.kind === "dots") {
      const rows = Object.keys(g.years).map(function (y) {
        const m = g.years[y];
        const dots = ((bank.years || []).find(function (b) { return String(b.year) === y; }) || {}).dots || [];
        return "<tr><td>" + y + "</td>" + '<td class="cb-n">' + fmtRate(m.bank, 3) + '</td><td class="cb-n">' + (m.n_dots || NA) + '</td><td class="cb-n">' + fmtRate(m.market, 3) + flagBadge(m.flag) + "</td>" +
          numCell(m, { scale: 50, dp: 1, noBadge: true }) + '<td class="cb-dots">' + dots.map(function (x) { return fmtRate(x.level, 3) + "×" + x.count; }).join("  ") + "</td></tr>" +
          (m.na && !isNum(m.v) ? "" : "") + (m.period === null && false ? "" : "");
      }).join("");
      return '<section class="cb-card cb-gap"><h3>GAP vs the FOMC dots <small class="muted">SEP of ' + fmtDate(g.sep) + '</small></h3><div class="cb-scroll"><table class="cb-table cb-mini"><thead><tr><th>Year</th><th>Dots median %</th><th>n</th><th>Market %</th><th>GAP bp</th><th>Distribution</th></tr></thead><tbody>' + rows +
        '</tbody></table></div><div class="cb-sub">GAP = market minus bank, on the same basis. 2028: no meeting calendar yet, the last meeting is assumed to be the 2nd Wednesday of December (Fed pattern).</div></section>';
    }
    if (bank.kind === "ocr_track") {
      const byYear = {};
      bank.quarters.forEach(function (x) { const y = x.period.slice(0, 4); (byYear[y] = byYear[y] || {})[x.period.slice(-1)] = x.value; });
      const q = Object.keys(byYear).sort().map(function (y) {
        return "<tr><td>" + esc(y) + "</td>" + [1, 2, 3, 4].map(function (k) { return '<td class="cb-n">' + (isNum(byYear[y][k]) ? fmtRate(byYear[y][k], 1) : NA) + "</td>"; }).join("") + "</tr>";
      }).join("");
      return '<section class="cb-card cb-gap"><h3>Bank path: RBNZ OCR track <small class="muted">MPS ' + fmtDate(bank.source) + ", projections finalised " + fmtDate(bank.finalised) + '</small></h3>' +
        '<div class="cb-sub">' + esc(bank.note) + '</div><div class="cb-sub"><b>GAP ' + NA + "</b>: " + esc(g.na || "n/a") + '</div><div class="cb-scroll"><table class="cb-table cb-mini cb-ocr"><thead><tr><th>Year</th><th>Q1 %</th><th>Q2 %</th><th>Q3 %</th><th>Q4 %</th></tr></thead><tbody>' + q + "</tbody></table></div></section>";
    }
    return '<section class="cb-card cb-gap"><h3>GAP</h3><div class="cb-sub">' + NA + " " + esc(g.na || "n/a") + "</div></section>";
  }
  function decisionSummaries(rows) {                              // one collapsible summary per decision that has one; else the pending marker (reason in the tooltip)
    const ready = rows.filter(function (x) { return x.summary && x.summary.status === "ready" && SUMMARIES[x.summary.doc_id]; });
    if (!ready.length) return summarySlot(rows[0] && rows[0].summary);
    return '<div class="cb-sum-list">' + ready.map(function (x) { return summaryDetails(x.summary, fmtDate(x.date) + " · statement summary"); }).join("") + "</div>";
  }
  function decisionsCard(d) {
    const rows = d.decisions.map(function (x) {
      const cons = isNum(x.surprise_consensus_bp) ? { v: x.surprise_consensus_bp, flag: null, na: "" } : { v: null, na: "no consensus on record" };
      return "<tr><td>" + fmtDate(x.date) + "</td>" + '<td class="econ-cell cb-num"' + tint(x.delta_bp, 25) + ">" + fmtSigned(x.delta_bp, 0) + "</td>" +
        '<td class="cb-n">' + (x.lower !== null && x.lower !== undefined ? fmtRate(x.lower) + EN + fmtRate(x.upper) : fmtRateAuto(x.rate_after)) + "</td><td>" + fmtDate(x.effective) + "</td>" +
        '<td class="cb-n">' + (isNum(x.consensus) ? fmtRateAuto(x.consensus) : NA) + "</td>" + numCell(cons, { scale: 10, dp: 1, noBadge: true }) +
        numCell(x.vs_market, { scale: 10, dp: 1, tip: isNum(x.vs_market.implied_step_bp) ? "Step implied at T-1: " + fmtSigned(x.vs_market.implied_step_bp, 1) + " bp" : "" }) +
        numCell(x.reaction.next, { scale: 10, dp: 1, tip: x.reaction.next.target ? "Level implied for the " + fmtDate(x.reaction.next.target) + " meeting, T minus T-1" : "" }) +
        numCell(x.reaction.year, { scale: 10, dp: 1, tip: x.reaction.year.target ? "Level implied for the " + fmtDate(x.reaction.year.target) + " meeting, T minus T-1" : "" }) +
        '<td class="cb-slot">' + voteChip(x.slots.votes) + '</td><td class="cb-slot">' + (x.slots.statement ? docAnchor(x.slots.statement, "text") : '<span class="cb-na" title="statement not collected">' + NA + "</span>") + "</td>" +
        '<td class="cb-slot">' + confLinks(x.slots.conference) + "</td></tr>";
    }).join("");
    return '<section class="cb-card"><h3>Last decisions</h3><div class="cb-scroll"><table class="cb-table cb-mini"><thead><tr><th>Date</th><th>Δ bp</th><th>Rate after %</th><th>Effective</th><th>Consensus %</th>' +
      '<th title="Decided rate minus consensus, bp">vs consensus</th><th title="Decided step minus the step implied at T-1, bp (EXACT / CURVE / PROXY with a basis only)">vs market T-1</th>' +
      '<th title="Change of the implied level at the next meeting, T minus T-1, bp">React. next</th><th title="Change of the implied level at the last meeting of the year, bp">React. year-end</th>' +
      '<th title="Votes for / against; hover for the names and the direction of each dissent">Votes</th><th title="The bank\u2019s own statement">Statement</th><th title="Official video / transcript / introductory statement of the press conference">Press conf.</th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
      decisionSummaries(d.decisions) + "</section>";
  }
  function calendarCard(d) {
    const rows = d.calendar.map(function (m) {
      const b = m.blackout;
      const bo = b ? fmtDay(b.start.slice(0, 10)) + " → " + fmtDay(b.end.slice(0, 10)) + (b.precision === "approximate" || !b.verified ? ' <span class="econ-flag flag-nc" title="' + esc(b.note || "rule not confirmed on the official page") + '">approx.</span>' : "") : '<span class="cb-na" title="no published quiet-period rule found">' + NA + "</span>";
      return "<tr><td>" + weekday(m.decision) + " " + fmtDate(m.decision) + "</td><td>" + (m.first_day ? fmtDay(m.first_day) : NA) + "</td><td>" + fmtDate(m.effective) + "</td><td>" + esc(timeText(m.time) || NA) + "</td>" +
        "<td>" + (m.has_projections ? esc(m.projections_name || "yes") : NA) + "</td><td>" + (m.has_presser ? esc(m.conference_local ? m.conference_local + " " + m.time.abbr : "yes") : NA) + "</td><td>" + bo + "</td>" +
        '<td class="cb-notes">' + (m.source === "official" ? "official" : '<span title="date not read from an official page">' + esc(m.source || "n/a") + "</span>") + "</td></tr>";
    }).join("");
    return '<section class="cb-card"><h3>Calendar and blackout</h3><div class="cb-scroll"><table class="cb-table cb-mini"><thead><tr><th>Decision</th><th>First day</th><th>Effective</th><th>Time</th><th>Projections</th><th>Conference</th><th>Blackout</th><th>Date source</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="8" class="muted">no upcoming meetings on record</td></tr>') + "</tbody></table></div></section>";
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
    const notes = d.notes.length ? "<h4>Notes</h4><ul>" + d.notes.map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("") + "</ul>" : "";
    return '<details class="cb-card cb-method" id="cbMethod"><summary>How is this calculated?</summary><dl>' + items + "</dl>" + sp + notes + cc + ck + latencyBlock(d.latency) + "</details>";
  }
  function footerCard(d) {
    const rows = d.sources.map(function (s) {
      return "<tr" + (s.stale ? ' class="cb-stale"' : "") + "><td>" + esc(s.id) + '</td><td>' + esc(s.role || "") + "</td><td>" + esc((s.methods || []).join(", ")) + "</td><td>" + (s.asof ? fmtDate(s.asof) + (isNum(s.lag_bd) ? " (" + s.lag_bd + " bd)" : "") : NA) +
        staleBadge(s.stale, "older than " + d.meta.stale_after_bd + " business days") + '</td><td class="cb-notes">' + esc(s.license || "") + (s.proxy ? " Proxy source." : "") + "</td></tr>";
    }).join("");
    const official = '<div class="cb-sub">Official series: ' + esc(d.summary.rate.decision_status === "official" ? "decision confirmed by an official series" : "decision status: " + (d.summary.rate.decision_status || "n/a")) + ". Decisions, meeting calendars and Fed projections come from the banks' own pages.</div>";
    return '<section class="cb-card cb-foot"><h3>Sources</h3><div class="cb-scroll"><table class="cb-table cb-mini"><thead><tr><th>Source</th><th>Role</th><th>Method</th><th>As-of</th><th>License / attribution</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="5" class="muted">no market source for this currency</td></tr>') + "</tbody></table></div>" + official +
      '<div class="cb-sub"><a href="#cbMethod" data-open-method>How is this calculated?</a></div></section>';
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
  function summaryBlock(s) {
    const pts = '<ul class="cb-sum-points">' + s.points.map(function (p, i) { return pointHtml(p, s.evidence && s.evidence[i]); }).join("") + "</ul>";
    const qs = '<div class="cb-sum-quotes">' + s.quotes.map(function (q) {
      return "<blockquote>\u201c" + esc(q.text) + "\u201d " + '<a href="' + esc(q.href) + '" target="_blank" rel="noopener" title="Opens the bank\u2019s own page and highlights the passage">source \u00b6' + q.paragraph + " \u2197</a></blockquote>";
    }).join("") + "</div>";
    const foot = '<div class="cb-sum-foot">' + esc(s.note.charAt(0).toUpperCase() + s.note.slice(1)) + " · " + (s.provider ? esc(s.provider) + " " : "") + esc(s.model) + " · " + esc(s.prompt_version) + " · generated " + fmtDate(s.generated) +
      (s.truncated ? " · covers the first part of a long document only" : "") + "</div>";
    const dropped = s.dropped && s.dropped.length ? '<div class="cb-sum-dropped" title="' + esc(s.dropped.map(function (d) { return "\u201c" + d.text + "\u201d: " + d.reason; }).join("\n")) + '">' + s.dropped.length + (s.dropped.length === 1 ? " point" : " points") + " removed by verification</div>" : "";
    return '<div class="cb-summary">' + pts + dropped + (s.changes ? changesBlock(s.changes) : "") + qs + foot + "</div>";
  }
  function summarySlot(slot) {                                      // the summary when ready; otherwise the pending marker, the reason in its tooltip
    if (slot && slot.status === "ready" && SUMMARIES[slot.doc_id]) return summaryBlock(SUMMARIES[slot.doc_id]);
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
  function votesBlock(v) {
    if (!v) return '<div class="cb-sub">Votes: not collected yet.</div>';
    const against = (v.against || []).map(function (a) {
      return '<li><b>' + esc(a.name || "member(s)") + '</b> <span class="econ-flag cb-dir cb-dir-' + esc(a.direction) + '">' + esc(a.direction === "hold" ? "wanted no change" : a.direction === "raise" ? "wanted higher" : "wanted lower") + "</span>" + (a.note ? '<div class="cb-sub">' + esc(a.note) + "</div>" : "") + "</li>";
    }).join("");
    return '<div class="cb-votes-block"><div class="cb-kicker">Votes</div><div class="cb-bigrate">' + voteChip(v) + '</div>' +
      (v.kind === "not_published" || v.kind === "consensus" ? '<div class="cb-sub">' + esc(v.evidence || "") + "</div>" : "") +
      (v.for && v.for.length ? '<div class="cb-sub"><b>For:</b> ' + esc(v.for.join(", ")) + "</div>" : "") + (against ? '<ul class="cb-against">' + against + "</ul>" : "") +
      '<div class="cb-sub">Source: ' + esc(v.source) + (v.notes ? " · " + esc(v.notes) : "") + "</div></div>";
  }
  function linksBlock(items) {
    return '<div class="cb-links">' + (items || []).map(function (it) {
      return '<div class="cb-linkitem"><span class="cb-kicker">' + esc(it.label) + "</span>" + (it.url ? docAnchor(it, it.published ? fmtDate(it.published) : "open") : '<span class="cb-na" title="' + esc(it.na || "n/a") + '">' + esc(it.expected ? "expected " + fmtDate(it.expected) : NA) + "</span>") + summaryDetails(it.summary, "Summary") + "</div>";
    }).join("") + "</div>";
  }
  function latestDecisionCard(d, dayMode) {
    const doc = d.documents, L = doc && doc.latest;
    if (!L) return "";
    const st = L.statement;
    const body = st
      ? '<details class="cb-stmt"' + (dayMode ? " open" : "") + "><summary>Statement text · " + fmtDate(L.meeting) + " (" + st.paragraphs.length + " paragraphs, " + esc(st.format) + ")</summary>" +
        (L.redline ? '<label class="cb-rl-toggle"><input type="checkbox" id="cbRlToggle"> Show changes vs the statement of ' + fmtDate(L.redline.prev_meeting) + ' <span class="cb-sub">(+' + L.redline.added_words + " / −" + L.redline.removed_words + " words, similarity " + (L.redline.similarity * 100).toFixed(0) + "%)</span></label>" : "") +
        '<div class="cb-stmt-body" id="cbStmtBody">' + st.paragraphs.map(paraHtml).join("") + '</div><div class="cb-sub">Source: <a href="' + esc(st.url) + '" target="_blank" rel="noopener">' + esc(st.url) + "</a>" + (st.rate_after !== null ? " · rate in the text: " + fmtRateAuto(st.rate_after) + "%" : "") + "<br>" + esc(st.license || "") + "</div></details>"
      : '<div class="cb-sub">' + esc(L.na || "n/a") + "</div>";
    return '<section class="cb-card cb-latest' + (dayMode ? " cb-daycard" : "") + '" id="cbLatest">' + (dayMode ? '<div class="cb-daytag">Decision day</div>' : "") + "<h3>Latest decision · " + fmtDate(L.meeting) + "</h3>" +
      '<div class="cb-grid">' + '<div>' + votesBlock(L.votes) + "</div><div>" + '<div class="cb-kicker">Documents</div>' + linksBlock(L.follow_up) + "</div></div>" + body +
      summarySlot(L.summary) + "</section>";
  }
  function wireRedline(d) {
    const box = document.getElementById("cbRlToggle");
    if (!box) return;
    const L = d.documents.latest;
    box.addEventListener("change", function () {
      document.getElementById("cbStmtBody").innerHTML = box.checked ? redlineParas(L.statement, L.redline) : L.statement.paragraphs.map(paraHtml).join("");
    });
  }
  function documentsCard(d) {
    const doc = d.documents;
    if (!doc) return "";
    const rows = doc.timeline.map(function (t) {
      const fu = function (types) { return t.follow_up.filter(function (i) { return types.indexOf(i.type) >= 0; }).map(function (i) { return i.url ? docAnchor(i, i.label) + summaryDetails(i.summary, "summary") : '<span class="cb-na" title="' + esc(i.na || "n/a") + '">' + esc(i.label) + " " + NA + "</span>"; }).join("<br>") || NA; };
      return "<tr><td>" + fmtDate(t.meeting) + "</td><td>" + (t.statement ? docAnchor(t.statement, "Statement") + summaryDetails(t.statement.summary, "summary") : '<span class="cb-na">' + NA + "</span>") + "</td><td>" + fu(["minutes", "account", "summary_of_opinions", "deliberations"]) +
        "</td><td>" + fu(["presser_video", "presser_transcript", "opening_statement"]) + "</td><td>" + voteChip(t.votes) + "</td></tr>";
    }).join("");
    const sp = doc.speeches.slice(0, 12).map(function (x) {
      return '<tr class="' + (x.relevance === "other" ? "cb-dim" : "") + '"><td>' + fmtDate(x.published) + "</td><td>" + esc(x.speaker || NA) + (x.role ? '<div class="cb-prob">' + esc(x.role) + "</div>" : "") +
        (x.chair ? ' <span class="econ-flag cb-chip-chair" title="Chair / head of the decision body">chair</span>' : x.voter ? ' <span class="econ-flag cb-chip-voter" title="Votes at the meetings this year">voter</span>' : "") + "</td>" +
        '<td class="cb-notes"><a href="' + esc(x.url) + '" target="_blank" rel="noopener">' + esc(x.title) + "</a></td><td>" + esc(x.type) + '</td><td title="' + esc(x.relevance === "other" ? "kept but marked: not about monetary policy / the outlook" : "monetary policy / inflation / outlook") + '">' + esc(x.relevance || NA) + (x.via === "bis" ? ' <span class="cb-prob" title="from the BIS feed (backfill): posting date, not the speech date">BIS</span>' : "") + "</td></tr>" +
        (x.summary && x.summary.status === "ready" && SUMMARIES[x.summary.doc_id] ? '<tr class="cb-sumrow"><td colspan="5">' + summaryDetails(x.summary, "Summary of this " + x.type) + "</td></tr>" : "");
    }).join("");
    return '<section class="cb-card"><h3>Documents <small class="muted">last four meetings and recent speeches</small></h3>' + (doc.manual_only ? '<div class="cb-sub">RBNZ: the site is behind a Cloudflare challenge; texts only from the manual file (data/cb/manual/documents.yaml).</div>' : "") +
      '<div class="cb-scroll"><table class="cb-table cb-mini"><thead><tr><th>Meeting</th><th>Statement</th><th>Minutes / account / summary</th><th>Press conference</th><th>Votes</th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
      '<h3 class="cb-h3-gap">Speeches and testimony <small class="muted">' + doc.n_speeches + " in the last 60 days; a dimmed row is not about monetary policy</small></h3>" +
      (sp ? '<div class="cb-scroll"><table class="cb-table cb-mini"><thead><tr><th>Date</th><th>Speaker</th><th>Title</th><th>Type</th><th>Relevance</th></tr></thead><tbody>' + sp + "</tbody></table></div>" : '<div class="cb-sub">' + NA + " no speeches collected in the last 60 days.</div>") + "</section>";
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
    root.innerHTML = bankBar(d.ccy) + (dayMode ? nextCard(d, true) + latestDecisionCard(d, true) : "") + pathHeader(d) + pathCards(d) + pathChartCard(d) + pathTableHtml(d.path) +
      gapCard(d) + (dayMode ? "" : latestDecisionCard(d, false)) + decisionsCard(d) + documentsCard(d) + calendarCard(d) + footerCard(d) + methodPanel(d);
    wireRedline(d);
    wirePathChart(d.path);
    if (!themeWatched) { themeWatched = true; watchTheme(); }
    paintSwatches();
    document.querySelectorAll("[data-open-method]").forEach(function (a) { a.addEventListener("click", function () { document.getElementById("cbMethod").open = true; }); });
    tick();
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
    const doc = d.documents, L = doc && doc.latest;
    const g = d.gap;
    let gapTitle = "GAP", gapSub = "", gapBody;
    if (g.kind === "dots") {
      gapTitle = "Gap vs the FOMC dots";
      gapSub = Object.keys(g.years).map(function (y) { return y + " " + (isNum(g.years[y].v) ? fmtSigned(g.years[y].v, 1) + " bp" : NA); }).join(DOT);
      gapBody = miniTable(["Year", "Dots %", "Market %", "GAP bp"], Object.keys(g.years).map(function (y) {
        const m = g.years[y];
        return "<tr><td>" + y + '</td><td class="cb-n">' + fmtRate(m.bank, 3) + '</td><td class="cb-n">' + fmtRate(m.market, 3) + '</td><td class="cb-n">' + (isNum(m.v) ? fmtSigned(m.v, 1) : NA) + "</td></tr>";
      })) + '<div class="cb-sub">GAP = market minus bank, on the same basis. SEP of ' + fmtDate(g.sep) + ".</div>";
    } else if (d.chart.bank.kind === "ocr_track") {
      gapTitle = "Bank path: RBNZ OCR track";
      gapSub = "GAP " + NA;
      const byYear = {};
      d.chart.bank.quarters.forEach(function (x) { const y = x.period.slice(0, 4); (byYear[y] = byYear[y] || {})[x.period.slice(-1)] = x.value; });
      gapBody = miniTable(["Year", "Q1 / Q2 / Q3 / Q4 %"], Object.keys(byYear).sort().map(function (y) {
        return "<tr><td>" + esc(y) + '</td><td class="cb-n">' + [1, 2, 3, 4].map(function (k) { return isNum(byYear[y][k]) ? fmtRate(byYear[y][k], 1) : NA; }).join(" / ") + "</td></tr>";
      })) + '<div class="cb-sub">' + esc(d.chart.bank.note || "") + " GAP " + NA + ": " + esc(g.na || "n/a") + "</div>";
    } else {
      gapSub = NA;
      gapBody = '<div class="cb-sub">' + NA + " " + esc(g.na || "n/a") + "</div>";
    }
    const latest = L ? (function () {
      const bits = [];
      const dec = d.decisions.find(function (x) { return x.date === L.meeting; });
      if (dec) bits.push(fmtSigned(dec.delta_bp, 0) + " bp");
      if (L.votes && L.votes.label) bits.push(esc(L.votes.label));
      const have = [L.statement ? "statement" : ""].concat((L.follow_up || []).filter(function (i) { return i.url; }).map(function (i) {
        return /minutes|account|summary|deliberations/.test(i.type) ? "minutes" : /presser|opening/.test(i.type) ? "press conference" : "";
      })).filter(function (x, i, a) { return x && a.indexOf(x) === i; });
      if (have.length) bits.push(have.join(", "));
      const html = latestDecisionCard(d, false).replace(/^<section[^>]*>/, "").replace(/<\/section>$/, "").replace(/<h3>[\s\S]*?<\/h3>/, "");
      return acc("cbAccLatest", "Latest decision" + DOT + fmtDay(L.meeting), bits.join(DOT), html, dayMode);
    })() : "";
    const decRows = d.decisions.map(function (x) {
      return "<tr><td>" + fmtDate(x.date) + '</td><td class="cb-n econ-cell cb-num"' + tint(x.delta_bp, 25) + ">" + fmtSigned(x.delta_bp, 0) + '</td><td class="cb-n">' +
        (x.lower !== null && x.lower !== undefined ? fmtRate(x.lower) + EN + fmtRate(x.upper) : fmtRateAuto(x.rate_after)) + "</td></tr>";
    });
    const docRows = doc ? doc.timeline.map(function (t) {
      const SHORT = { presser_video: "Video", presser_transcript: "Transcript", opening_statement: "Opening" };      // short labels: the column is ~80px
      const fu = function (types) { return t.follow_up.filter(function (i) { return types.indexOf(i.type) >= 0 && i.url; }).map(function (i) { return docAnchor(i, SHORT[i.type] || i.label); }).join("<br>") || NA; };
      return "<tr><td>" + fmtDay(t.meeting) + "</td><td>" + (t.statement ? docAnchor(t.statement, "Statement") : NA) + "</td><td>" + fu(["minutes", "account", "summary_of_opinions", "deliberations"]) + "</td><td>" + fu(["presser_video", "presser_transcript", "opening_statement"]) + "</td></tr>";
    }) : [];
    const spRows = doc ? doc.speeches.slice(0, 12).map(function (x) {
      return '<tr class="' + (x.relevance === "other" ? "cb-dim" : "") + '"><td>' + fmtDay(x.published) + "</td><td>" + esc(x.speaker || NA) + '</td><td class="cb-notes"><a href="' + esc(x.url) + '" target="_blank" rel="noopener">' + esc(x.title) + "</a></td></tr>";
    }) : [];
    const calRows = d.calendar.map(function (m) {
      const b = m.blackout;
      return "<tr><td>" + weekday(m.decision) + " " + fmtDay(m.decision) + "</td><td>" + esc(timeText(m.time) || NA) + "</td><td>" + (b ? fmtDay(b.start.slice(0, 10)) + " → " + fmtDay(b.end.slice(0, 10)) : NA) + "</td></tr>";
    });
    const srcRows = d.sources.map(function (x) {
      return "<tr" + (x.stale ? ' class="cb-stale"' : "") + "><td>" + esc(x.id) + "</td><td>" + esc(x.role || "") + '</td><td class="cb-n">' + (x.asof ? fmtDay(x.asof) : NA) + staleBadge(x.stale) + "</td></tr>";
    });
    root.innerHTML = bankBar(d.ccy) + pathHeader(d) + pathCards(d) + pathChartCard(d) + pathTableHtml(d.path) + '<div class="cb-accs">' +
      acc("cbAccGap", gapTitle, gapSub, gapBody, false) +
      latest +
      acc("cbAccDecisions", "Last decisions", d.decisions.length + " on record", miniTable(["Date", "Δ bp", "Rate after %"], decRows), false) +
      (doc ? acc("cbAccDocs", "Documents", "last four meetings", miniTable(["Meeting", "Statement", "Minutes", "Press"], docRows), false) +
             acc("cbAccSpeeches", "Speeches and testimony", "last 60 days", miniTable(["Date", "Speaker", "Title"], spRows, "no speeches collected in the last 60 days"), false) : "") +
      acc("cbAccCalendar", "Calendar and blackout", "", miniTable(["Decision", "Time", "Blackout"], calRows, "no upcoming meetings on record"), false) +
      acc("cbAccSources", "Sources", "", miniTable(["Source", "Role", "As-of"], srcRows, "no market source for this currency"), false) +
      acc("cbAccMethod", "How is this calculated?", "", methodPanel(d).replace(/^<details[^>]*><summary>[^<]*<\/summary>/, "").replace(/<\/details>$/, "")
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
    paintSwatches();
    wireRedline(d);
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
      '<div class="cb-chart-wrap"><canvas id="cbPairChart"></canvas></div><div class="cb-sub">Hover (or tap) for both banks at a date and the rate gap then. Hollow points and dashed segments = estimates.</div></section>' +
      '<section class="cb-card cb-method-link"><a href="' + esc(pj.banks[p.base].href) + '#cbMethod">How is this calculated?</a></section>';
    const pairCanvas = document.getElementById("cbPairChart");
    registerRedraw(function () {
      drawCompareChart(pairCanvas, paths, "bp", { extra: function (t) { return [gapRow(b.path, q.path, t)]; } });
    });
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
