/* COT Positioning page (audit 10B).
 *
 * Built in the browser from the payload written by src/cot_payload.py:
 *   /data/cot/index.json          weeks, meta, instruments
 *   /data/cot/<YYYY-MM-DD>.json   one report week
 *   /data/cot/series.json         net spec / net comm / open interest per report date
 *
 * DOM only (createElement + textContent): no HTML strings are ever parsed.
 * The view lives in the URL (week, markets, view, sort, x, q, sym), so a saved
 * link reopens the same view; plain /cot opens the defaults.
 */
(function () {
  "use strict";

  const app = document.querySelector("main.cot-page");
  if (!app) return;
  const ROOT = app.dataset.dataRoot || "/data/cot/";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const RED = "211,47,47";
  const BLUE = "21,101,192";
  const COMM_COLOR = "#d95926";
  const gradientStyle = (window.ScorePalette && window.ScorePalette.gradientStyle) || function () { return ""; };

  // Category order and grouping (data/contracts.yaml keys).
  const CATS = [
    { key: "fx", label: "Forex", group: "Financial", desc: "currency futures vs USD" },
    { key: "indices", label: "Indices", group: "Financial" },
    { key: "rates", label: "US Treasuries", group: "Financial" },
    { key: "metals", label: "Metals", group: "Commodities" },
    { key: "energy", label: "Energy", group: "Commodities" },
    { key: "crypto", label: "Crypto", group: "Financial" },
    { key: "ags", label: "Grains & Softs", group: "Commodities" },
    { key: "livestock", label: "Livestock", group: "Commodities" },
  ];
  const MENU_ORDER = ["fx", "indices", "rates", "crypto", "metals", "energy", "ags", "livestock"];
  const DEFAULT_MARKETS = ["fx", "metals"];
  const CAT = Object.fromEntries(CATS.map((c) => [c.key, c]));
  const SORT_KEYS = ["cot", "p6", "p3", "flow", "net", "long", "d1w"];

  // Pairs shown on /economic, used to explain what a currency's COT means there.
  const PAIRS = {
    EUR: ["EUR/USD", "EUR/JPY", "EUR/GBP"], GBP: ["GBP/USD", "GBP/JPY", "EUR/GBP"],
    JPY: ["USD/JPY", "EUR/JPY", "GBP/JPY"], CHF: ["USD/CHF", "EUR/CHF", "GBP/CHF"],
    CAD: ["USD/CAD", "CAD/JPY", "EUR/CAD"], AUD: ["AUD/USD", "AUD/JPY", "EUR/AUD"],
    NZD: ["NZD/USD", "NZD/JPY", "EUR/NZD"],
  };
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  const state = { week: null, markets: new Set(DEFAULT_MARKETS), view: "spec", sort: null, x: false, q: "", sym: null };
  const data = { index: null, series: null, weeks: {}, current: null, note: "" };
  const ui = {};

  // ---------------------------------------------------------------- helpers
  function h(tag, attrs) {
    const el = document.createElement(tag);
    if (attrs) setAttrs(el, attrs);
    for (let i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function s(tag, attrs) {
    const el = document.createElementNS(SVG_NS, tag);
    if (attrs) setAttrs(el, attrs);
    for (let i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function setAttrs(el, attrs) {
    for (const k in attrs) {
      const v = attrs[k];
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.setAttribute("class", v);
      else if (k === "text") el.textContent = v;
      else if (k === "style") el.style.cssText = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  function append(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) { c.forEach((x) => append(el, x)); return; }
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  const isNum = (v) => typeof v === "number" && isFinite(v);
  const MINUS = "−";
  function signed(str, v) { return (v > 0 ? "+" : v < 0 ? MINUS : "") + str; }
  function fmtAbs(v) {
    const a = Math.abs(v);
    if (a >= 1e6) return (a / 1e6).toFixed(2) + "M";
    if (a >= 1e3) return (a / 1e3).toFixed(1) + "K";
    return String(Math.round(a));
  }
  function fmtK(v, withSign) {
    if (!isNum(v)) return "—";
    const str = fmtAbs(v);
    return withSign === false ? (v < 0 ? MINUS + str : str) : signed(str, Math.round(v) === 0 && Math.abs(v) < 1e3 ? 0 : v);
  }
  function fmtPct(v, dp) {
    if (!isNum(v)) return "—";
    const d = dp === undefined ? 1 : dp;
    const r = Number(v.toFixed(d));
    return signed(Math.abs(r).toFixed(d) + "%", r);
  }
  function pctInt(p) { return isNum(p) ? Math.round(p * 100) : null; }
  function ordinal(n) {
    const t = n % 100;
    if (t >= 11 && t <= 13) return n + "th";
    return n + (["th", "st", "nd", "rd"][n % 10] || "th");
  }
  function parseDate(iso) { const [y, m, d] = iso.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); }
  function fmtDay(iso) { const d = parseDate(iso); return d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear(); }
  function fmtDayW(iso) { const d = parseDate(iso); return DAYS[d.getUTCDay()] + " " + fmtDay(iso); }
  function bucharest(isoUtc, withYear) {
    const d = new Date(isoUtc);
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Bucharest", weekday: "short", day: "numeric", month: "numeric",
      year: withYear ? "numeric" : undefined, hour: "2-digit", minute: "2-digit", hour12: false,
    }).formatToParts(d);
    const g = (t) => (parts.find((p) => p.type === t) || {}).value || "";
    return g("weekday") + " " + g("day") + " " + MONTHS[Number(g("month")) - 1] + (withYear ? " " + g("year") : "") + ", " + g("hour") + ":" + g("minute");
  }
  function marketsText(keys) {
    const labels = CATS.filter((c) => keys.has(c.key)).map((c) => c.label);
    if (labels.length === CATS.length) return "all markets";
    if (labels.length <= 1) return labels.join("");
    return labels.slice(0, -1).join(", ") + " and " + labels[labels.length - 1];
  }
  const sideName = (v) => (v === "spec" ? "speculators" : "commercials");
  const sideShort = (v) => (v === "spec" ? "specs" : "comms");
  // Reading colour: speculators are read against the crowd (long = red); comms the other way.
  function readRgb(signedMeaning) { return signedMeaning > 0 ? BLUE : RED; }

  // ---------------------------------------------------------------- URL state
  function readUrl() {
    const q = new URLSearchParams(location.search);
    state.week = q.get("week");
    const m = q.get("markets");
    state.markets = new Set(m === null ? DEFAULT_MARKETS : m.split(",").filter((k) => CAT[k]));
    state.view = q.get("view") === "comm" ? "comm" : "spec";
    const so = (q.get("sort") || "").split(":");
    state.sort = SORT_KEYS.includes(so[0]) && (so[1] === "asc" || so[1] === "desc") ? { key: so[0], dir: so[1] } : null;
    state.x = q.get("x") === "1";
    state.q = q.get("q") || "";
    state.sym = q.get("sym");
  }
  function writeUrl() {
    const q = new URLSearchParams();
    if (state.week && data.index && state.week !== data.index.latest) q.set("week", state.week);
    const def = DEFAULT_MARKETS.slice().sort().join(",");
    const cur = CATS.filter((c) => state.markets.has(c.key)).map((c) => c.key);
    if (cur.slice().sort().join(",") !== def) q.set("markets", cur.join(","));
    if (state.view === "comm") q.set("view", "comm");
    if (state.sort) q.set("sort", state.sort.key + ":" + state.sort.dir);
    if (state.x) q.set("x", "1");
    if (state.q) q.set("q", state.q);
    if (state.sym) q.set("sym", state.sym);
    const qs = q.toString();
    history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
  }

  // ---------------------------------------------------------------- data
  function getJson(name) {
    return fetch(ROOT + name, { credentials: "same-origin" }).then((r) => {
      if (!r.ok) throw new Error(name + " " + r.status);
      return r.json();
    });
  }
  function loadWeek(week) {
    if (data.weeks[week]) return Promise.resolve(data.weeks[week]);
    return getJson(week + ".json").then((w) => { data.weeks[week] = w; return w; });
  }
  function seriesSlice(sym, key, week, n) {
    const sr = data.series;
    if (!sr || !sr.instruments[sym]) return { dates: [], values: [] };
    let end = sr.dates.length - 1;
    while (end >= 0 && sr.dates[end] > week) end--;
    const start = Math.max(0, end - n + 1);
    return { dates: sr.dates.slice(start, end + 1), values: sr.instruments[sym][key].slice(start, end + 1) };
  }

  // ---------------------------------------------------------------- small pieces
  function chip(cell, opts) {
    const o = opts || {};
    const title = o.title;
    if (!isNum(cell)) {
      return h("span", { class: "cot-chip cot-chip-none", title: o.noneTitle || null, text: "—" });
    }
    const el = h("span", { class: "cot-chip" + (cell === 0 ? " cot-chip-zero" : "") + (o.cls ? " " + o.cls : ""), title: title || null, text: signed(String(Math.abs(cell)), cell) });
    if (cell !== 0) el.style.cssText = gradientStyle(cell, 4);
    return el;
  }
  function crowdTitle(sc) {
    return "Crowding " + signed(String(Math.abs(sc.level)), sc.level) + " + Flow " + signed(String(Math.abs(sc.flow)), sc.flow) +
      " = " + signed(String(Math.abs(sc.cell)), sc.cell) + ". Same value as the COT column on Economic.";
  }
  const NOT_IN_MODEL = "Not in the model: only the 8 currencies, the dollar index, gold and silver are scored";
  function cotChip(inst) {
    if (!inst.in_model || !inst.score) return chip(null, { noneTitle: NOT_IN_MODEL });
    return chip(inst.score.cell, { title: crowdTitle(inst.score) });
  }
  function triangle(up, rgb) {
    return s("svg", { width: 9, height: 9, viewBox: "0 0 9 9", "aria-hidden": "true", class: "cot-tri", style: "fill:rgb(" + rgb + ")" },
      s("path", { d: up ? "M4.5 1L8.5 8H0.5Z" : "M4.5 8L8.5 1H0.5Z" }));
  }
  function newBadge(dir) {
    return h("span", { class: "cot-new", title: dir === "low" ? "Entered the bottom 5% of its 3-year range this week" : "Entered the top 5% of its 3-year range this week", text: "NEW" });
  }
  function crowdState(P) { return P > 70 ? "crowded long" : P < 30 ? "crowded short" : "near the middle"; }
  function pctTitle(P, win, view, sym) {
    const st = crowdState(P);
    let read;
    if (view === "spec") read = st === "crowded long" ? "Read contrarian: bearish for " + sym + "." : st === "crowded short" ? "Read contrarian: bullish for " + sym + "." : "Read contrarian: neutral.";
    else read = st === "crowded long" ? "Read with the hedgers: bullish for " + sym + "." : st === "crowded short" ? "Read with the hedgers: bearish for " + sym + "." : "Neutral.";
    return ordinal(P) + " percentile of the last " + (win === 6 ? "26" : "156") + " weekly reports (" + (win === 6 ? "6M" : "3Y") + "): " +
      sideName(view) + " are more long than in " + P + "% of those weeks (" + st + "). " + read;
  }
  function pctBar(p, view, width) {
    const P = pctInt(p);
    const t = (p - 0.5) / 0.5;
    const mag = Math.pow(Math.abs(t), 0.7);
    const alpha = Math.max(mag, 0.18);
    const meaning = (p > 0.5 ? -1 : 1) * (view === "spec" ? 1 : -1);  // + = bullish reading
    const rgb = readRgb(meaning);
    const track = h("span", { class: "cot-track" + (width === 112 ? " cot-track-main" : ""), style: width === 112 ? null : "width:" + width + "px" });
    const left = Math.min(p, 0.5) * 100, w = Math.abs(p - 0.5) * 100;
    track.appendChild(h("span", { class: "cot-fill " + (p >= 0.5 ? "cot-fill-r" : "cot-fill-l"), style: "left:" + left.toFixed(1) + "%;width:" + w.toFixed(1) + "%;background:rgba(" + rgb + "," + alpha.toFixed(3) + ")" }));
    track.appendChild(h("span", { class: "cot-tick" }));
    const ext = P >= 95 || P <= 5;
    const size = ext ? 10 : 8;
    const k = Math.round(((1 + alpha) / 2) * 100);
    const dotColor = mag < 0.2 ? "var(--muted)" : "color-mix(in srgb, rgb(" + rgb + ") " + k + "%, var(--surface))";
    track.appendChild(h("span", { class: "cot-dot", style: "left:calc(" + (p * 100).toFixed(1) + "% - " + size / 2 + "px);width:" + size + "px;height:" + size + "px;top:" + (ext ? -2 : -1) + "px;background:" + dotColor }));
    return { track: track, P: P, ext: ext };
  }
  function pctCell(p, view, sym, win) {
    if (!isNum(p)) return h("span", { class: "cot-muted", text: "—" });
    const b = pctBar(p, view, 112);
    return h("span", { class: "cot-pct", title: pctTitle(b.P, win, view, sym) }, b.track,
      h("span", { class: "cot-pct-num" + (b.ext ? " is-ext" : ""), text: String(b.P) }));
  }
  function flowTitle(side, view) {
    const verb = side.chg4 >= 0 ? "added" : "cut";
    const base = (view === "spec" ? "Speculators " : "Commercials ") + verb + " " + fmtAbs(side.chg4) + " contracts in the last 4 weeks — " +
      Math.abs(side.z4w).toFixed(1) + "× a typical 4-week move (last 52 weeks).";
    return view === "spec" ? base + " Beyond 0.5× the model counts it: +1 when buying, −1 when selling." : base;
  }
  function flowCell(side, view) {
    if (!isNum(side.chg4) || !isNum(side.z4w)) return h("span", { class: "cot-muted", text: "—" });
    const strong = Math.abs(side.z4w) >= 0.5;
    const up = side.chg4 >= 0;
    const meaning = (up ? 1 : -1) * (view === "spec" ? 1 : -1);
    const mark = strong ? triangle(up, readRgb(meaning)) : h("span", { class: "cot-dash", text: "—" });
    return h("span", { class: "cot-stack cot-right", title: flowTitle(side, view) },
      h("span", { class: "cot-flow" + (Math.abs(side.z4w) >= 2 ? " is-strong" : "") }, mark, fmtK(side.chg4)),
      h("span", { class: "cot-sub", text: Math.abs(side.z4w).toFixed(1) + "× usual" }));
  }
  function netCell(side) {
    return h("span", { class: "cot-stack cot-right" },
      h("span", { class: "cot-net", text: fmtK(side.net) }),
      h("span", { class: "cot-sub", text: fmtPct(isNum(side.pct_oi) ? side.pct_oi * 100 : null) + " of market" }));
  }
  function longCell(side) {
    if (!isNum(side.long_share)) return h("span", { class: "cot-muted", text: "—" });
    const L = Math.round(side.long_share * 100);
    return h("span", { class: "cot-long", title: L + "% of this group's open positions are long, " + (100 - L) + "% short" },
      h("span", { class: "cot-long-bar" }, h("span", { class: "cot-long-l", style: "width:" + L + "%" }), h("span", { class: "cot-long-s" })),
      h("span", { class: "cot-long-num", text: L + "%" }));
  }
  function d1wCell(side, oi) {
    const share = isNum(side.d1w) && isNum(oi) && oi ? side.d1w / oi : null;
    const big = isNum(share) && Math.abs(share) >= 0.05;
    return h("span", { class: "cot-stack cot-right" },
      h("span", { class: "cot-d1w" + (big ? " is-big" : ""), text: fmtK(side.d1w) }),
      h("span", { class: "cot-sub", text: fmtPct(isNum(share) ? share * 100 : null) + " of market" }));
  }
  function sparkline(sym, view, week) {
    const sl = seriesSlice(sym, view === "spec" ? "spec_net" : "comm_net", week, 156);
    const vals = sl.values;
    const svg = s("svg", { width: 132, height: 30, viewBox: "0 0 132 30", "aria-hidden": "true", class: "cot-spark" });
    const nums = vals.filter(isNum);
    if (nums.length < 2) return svg;
    const lo = Math.min(0, Math.min.apply(null, nums)), hi = Math.max(0, Math.max.apply(null, nums));
    const n = vals.length;
    const X = (i) => 3 + (n > 1 ? (i * 126) / (n - 1) : 0);
    const Y = (v) => (hi === lo ? 15 : 27 - ((v - lo) / (hi - lo)) * 24);
    const x6 = X(Math.max(0, n - 26));
    svg.appendChild(s("rect", { x: x6.toFixed(1), y: 0, width: (132 - x6).toFixed(1), height: 30, class: "cot-spark-6m" }));
    svg.appendChild(s("line", { x1: 0, x2: 132, y1: Y(0).toFixed(1), y2: Y(0).toFixed(1), class: "cot-spark-zero" }));
    let d = "", pen = false, last = null;
    vals.forEach((v, i) => {
      if (!isNum(v)) { pen = false; return; }
      d += (pen ? " L" : (d ? " M" : "M")) + X(i).toFixed(1) + "," + Y(v).toFixed(1);
      pen = true; last = [X(i), Y(v)];
    });
    svg.appendChild(s("path", { d: d, class: "cot-spark-line" }));
    if (last) svg.appendChild(s("circle", { cx: last[0].toFixed(1), cy: last[1].toFixed(1), r: 3, class: "cot-spark-end" }));
    return svg;
  }

  // ---------------------------------------------------------------- filtering & sorting
  function inMarkets(inst) { return state.markets.has(inst.category); }
  function side(inst) { return inst[state.view] || {}; }
  function isExtreme(inst) {
    const sd = side(inst);
    return [sd.p6, sd.p3].some((p) => isNum(p) && (p >= 0.95 || p <= 0.05));
  }
  function matchesQuery(inst) {
    if (!state.q) return true;
    const q = state.q.toLowerCase();
    return inst.symbol.toLowerCase().includes(q) || (inst.name || "").toLowerCase().includes(q);
  }
  function sortValue(inst, key) {
    const sd = side(inst);
    switch (key) {
      case "cot": return inst.in_model && inst.score ? inst.score.cell : null;
      case "p6": return sd.p6;
      case "p3": return sd.p3;
      case "flow": return sd.z4w;
      case "net": return sd.net;
      case "long": return sd.long_share;
      case "d1w": return sd.d1w;
    }
    return null;
  }
  // Default order (no ?sort=): COT score descending, bullish on top, in both views. Ties, and the rows without a score
  // (after the scored ones), go by the reading of the view's 3Y percentile, bullish first: speculators are read against
  // the crowd (low p3 first), commercials with the hedgers (high p3 first).
  function defaultOrder(a, b) {
    const ca = sortValue(a, "cot"), cb = sortValue(b, "cot");
    const sa = isNum(ca), sb = isNum(cb);
    if (sa !== sb) return sa ? -1 : 1;
    if (sa && ca !== cb) return cb - ca;
    const pa = sortValue(a, "p3"), pb = sortValue(b, "p3");
    const qa = isNum(pa), qb = isNum(pb);
    if (qa !== qb) return qa ? -1 : 1;
    if (qa && pa !== pb) return state.view === "spec" ? pa - pb : pb - pa;
    return a.symbol.localeCompare(b.symbol);
  }
  function sortRows(rows) {
    if (!state.sort) {
      return rows.slice().sort((a, b) => (a.missing !== b.missing ? (a.missing ? 1 : -1) : defaultOrder(a, b)));
    }
    const key = state.sort.key;
    const dir = state.sort.dir;
    const mul = dir === "asc" ? 1 : -1;
    return rows.slice().sort((a, b) => {
      if (a.missing !== b.missing) return a.missing ? 1 : -1;
      const va = sortValue(a, key), vb = sortValue(b, key);
      const na = !isNum(va), nb = !isNum(vb);
      if (na || nb) return na === nb ? a.symbol.localeCompare(b.symbol) : na ? 1 : -1;
      return va === vb ? a.symbol.localeCompare(b.symbol) : (va - vb) * mul;
    });
  }
  function visibleGroups() {
    const week = data.current;
    const out = [];
    CATS.forEach((c) => {
      if (!state.markets.has(c.key)) return;
      let rows = week.instruments.filter((i) => i.category === c.key && matchesQuery(i));
      if (state.x) rows = rows.filter((i) => !i.missing && isExtreme(i));
      if (rows.length) out.push({ cat: c, rows: sortRows(rows) });
    });
    return out;
  }

  // ---------------------------------------------------------------- header
  function buildHeader() {
    const intro = h("p", { class: "cot-intro", "data-htr": true },
      "Weekly CFTC positioning of large speculators and commercials. ",
      h("span", { class: "cot-em", text: "Percentile" }),
      " = where this week’s net position sits in its own range over the last 6 months (26 reports) and 3 years (156 reports): 0 = most short, 100 = most long. ",
      h("span", { class: "cot-em", text: "COT" }),
      " is the score used on Economic, from −4 to +4; click a row to see how it is built.");
    ui.prev = h("button", { type: "button", class: "cot-btn cot-icon-btn", "aria-label": "Previous week", onclick: () => stepWeek(1) }, chevron("M15 18l-6-6 6-6"));
    ui.next = h("button", { type: "button", class: "cot-btn cot-icon-btn", "aria-label": "Next week", onclick: () => stepWeek(-1) }, chevron("M9 18l6-6-6-6"));
    ui.weekSel = h("select", { class: "cot-week-select", "aria-label": "Report week", onchange: (e) => setWeek(e.target.value) });
    data.index.weeks.forEach((w) => ui.weekSel.appendChild(h("option", { value: w, text: fmtDay(w) })));
    const weekBox = h("label", { class: "cot-btn cot-week" }, h("span", { class: "cot-muted", text: "Week" }), ui.weekSel, chevron("M6 9l6 6 6-6"));
    const csv = h("button", { type: "button", class: "cot-btn", onclick: downloadCsv },
      s("svg", { width: 15, height: 15, viewBox: "0 0 24 24", "aria-hidden": "true", class: "cot-ico" }, s("path", { d: "M12 3v12M7 10l5 5 5-5M5 21h14" })), "CSV");
    ui.asOf = h("span");
    ui.nextRel = h("span");
    ui.note = h("p", { class: "cot-note", role: "status" });
    ui.phHead = h("div", { class: "phone-only cot-ph-head" });
    const right = h("div", { class: "cot-head-right desk-only" },
      h("div", { class: "cot-head-controls" }, h("div", { class: "cot-weeknav" }, ui.prev, weekBox, ui.next), csv),
      ui.asOf, ui.nextRel);
    return h("div", { class: "cot-headwrap" },
      h("div", { class: "cot-head" }, h("div", { class: "cot-head-left" }, h("h1", { text: "COT Positioning" }), ui.phHead, intro), right),
      ui.note);
  }
  function chevron(d) {
    return s("svg", { width: 16, height: 16, viewBox: "0 0 24 24", "aria-hidden": "true", class: "cot-ico" }, s("path", { d: d }));
  }
  function updateHeader() {
    const w = data.current;
    ui.weekSel.value = w.as_of;
    const i = data.index.weeks.indexOf(w.as_of);
    ui.next.disabled = i <= 0;
    ui.prev.disabled = i >= data.index.weeks.length - 1;
    clear(ui.asOf);
    append(ui.asOf, ["Positions as of ", h("span", { class: "cot-strong", text: fmtDayW(w.as_of) }),
      w.fetched_at ? " · fetched " + bucharest(w.fetched_at, false) + " Bucharest" : ""]);
    const latest = w.as_of === data.index.latest;
    ui.nextRel.textContent = (latest ? "Next report usually " + bucharest(w.next_release, false) : "Released usually " + bucharest(w.released, false)) +
      " Bucharest · CFTC Legacy, futures + options";
    ui.note.textContent = data.note;
    ui.note.hidden = !data.note;
    updatePhoneHeader();
  }

  // ---------------------------------------------------------------- legend
  function buildLegend() {
    const chips = h("span", { class: "cot-legend-chips" });
    for (let v = -4; v <= 4; v++) chips.appendChild(chip(v, { cls: "cot-chip-sm" }));
    const sampleL = pctBar(0.96, "spec", 64), sampleS = pctBar(0.11, "spec", 64);
    return h("div", { class: "cot-legend", "data-htr": true },
      h("div", { class: "cot-legend-row" },
        h("span", { class: "cot-legend-item" }, h("span", { class: "cot-legend-title", text: "Colours" }), chips,
          h("span", {}, h("span", { class: "cot-em", text: "blue = bullish" }), " for the asset, ", h("span", { class: "cot-em", text: "red = bearish" }))),
        h("span", { class: "cot-legend-item" },
          h("span", { class: "cot-pct", title: pctTitle(96, 3, "spec", "the asset") }, sampleL.track), h("span", { text: "crowded long" }),
          h("span", { class: "cot-pct", title: pctTitle(11, 3, "spec", "the asset") }, sampleS.track), h("span", { text: "crowded short" })),
        h("span", { class: "cot-legend-item" }, newBadge("high"), h("span", { text: "entered a 3Y extreme this week" }))),
      h("div", { class: "cot-legend-text" },
        "Percentile bars start from the middle (50) and take the colour of what the positioning means for the asset. Speculators are read ",
        h("span", { class: "cot-em", text: "against the crowd" }),
        ": when they are crowded long, the risk is a move down, so the bar is red. ",
        h("span", { class: "cot-em", text: "Market" }), " = all contracts still open (open interest)."));
  }

  // ---------------------------------------------------------------- cards
  function card(title, body) {
    return h("section", { class: "cot-card" }, h("h2", { class: "cot-card-title", text: title }), body);
  }
  function renderCards() {
    const v = state.view, sn = sideShort(v);
    const rows = data.current.instruments.filter((i) => inMarkets(i) && !i.missing);
    const sd = (i) => i[v] || {};
    clear(ui.cards);
    // 1. Largest weekly shifts
    const shifts = rows.filter((i) => isNum(sd(i).d1w) && i.oi).sort((a, b) => Math.abs(sd(b).d1w / b.oi) - Math.abs(sd(a).d1w / a.oi)).slice(0, 3);
    ui.cards.appendChild(card("Largest weekly shifts · " + sn, shifts.length ? shifts.map((i) => h("div", { class: "cot-card-line" },
      h("span", { class: "cot-card-sym", text: i.symbol }), h("span", { class: "cot-num", text: fmtK(sd(i).d1w) }),
      h("span", { class: "cot-card-sub", text: fmtPct((sd(i).d1w / i.oi) * 100) + " of market" }))) : h("p", { class: "cot-card-empty", text: "No data." })));
    // 2. New extremes
    const flips = rows.filter((i) => sd(i).flip3y);
    const where = marketsText(state.markets);
    const ext = [];
    flips.slice(0, 3).forEach((i) => {
      const p = sd(i), P = pctInt(p.p3), hi = p.flip3y === "high";
      const what = P >= 100 ? "at 3Y high" : P <= 1 ? "at 3Y low" : "entered " + (hi ? "top" : "bottom") + " 5% of 3Y range (" + P + ")";
      const net = p.net || 0;
      const opposite = hi ? net < 0 : net > 0;
      const netTxt = (opposite ? "still net " : "net ") + (net >= 0 ? "long " : "short ") + fmtK(net);
      const bearish = v === "spec" ? hi : !hi;
      ext.push(h("div", { class: "cot-card-ext" },
        h("span", {}, h("span", { class: "cot-card-sym-inline", text: i.symbol }), " ", h("span", { class: "cot-card-em", text: sn + " " + what })),
        h("span", { class: "cot-card-sub", text: netTxt + " · " + (bearish ? "bearish" : "bullish") + " read for " + i.symbol })));
    });
    if (flips.length > 3) ext.push(h("div", { class: "cot-card-sub", text: "+" + (flips.length - 3) + " more with NEW in the table." }));
    ext.push(h("div", { class: "cot-card-note", text: (flips.length ? "No other new extremes in " : "No new extremes in ") + where + "." }));
    ui.cards.appendChild(card("New extremes this week · 3Y", ext));
    // 3. Most crowded
    const byP3 = rows.filter((i) => isNum(sd(i).p3)).sort((a, b) => sd(b).p3 - sd(a).p3);
    const longs = byP3.slice(0, 3), shorts = byP3.slice(-3).reverse();
    const longRgb = v === "spec" ? RED : BLUE, shortRgb = v === "spec" ? BLUE : RED;
    const grid = h("div", { class: "cot-card-grid" }, h("span", { class: "cot-card-sub", text: "Crowded long" }), h("span", { class: "cot-card-sub", text: "Crowded short" }));
    for (let k = 0; k < 3; k++) {
      [[longs[k], longRgb], [shorts[k], shortRgb]].forEach(([i, rgb]) => {
        grid.appendChild(i ? h("div", { class: "cot-card-line" }, h("span", { class: "cot-dotmark", style: "background:rgb(" + rgb + ")" }),
          h("span", { class: "cot-card-sym", text: i.symbol }), h("span", { class: "cot-num", text: String(pctInt(sd(i).p3)) })) : h("span"));
      });
    }
    ui.cards.appendChild(card("Most crowded · " + sn + ", 3Y", grid));
    // 4. Strongest 4-week flow
    const flows = rows.filter((i) => isNum(sd(i).z4w)).sort((a, b) => Math.abs(sd(b).z4w) - Math.abs(sd(a).z4w)).slice(0, 4);
    ui.cards.appendChild(card("Strongest 4-week flow · " + sn, flows.map((i) => {
      const p = sd(i), up = p.chg4 >= 0;
      return h("div", { class: "cot-card-line" }, h("span", { class: "cot-card-sym", text: i.symbol }),
        triangle(up, readRgb((up ? 1 : -1) * (v === "spec" ? 1 : -1))), h("span", { class: "cot-num", text: fmtK(p.chg4) }),
        h("span", { class: "cot-card-sub", text: Math.abs(p.z4w).toFixed(1) + "× usual" }));
    })));
  }

  // ---------------------------------------------------------------- controls
  function buildControls() {
    ui.mktBtn = h("button", { type: "button", class: "cot-btn cot-mkt-btn", "aria-haspopup": "dialog", "aria-expanded": "false", onclick: toggleMenu });
    ui.menu = h("div", { class: "cot-menu", role: "dialog", "aria-label": "Markets", hidden: true });
    ui.specBtn = h("button", { type: "button", class: "cot-seg-btn", onclick: () => setView("spec"), text: "Speculators" });
    ui.commBtn = h("button", { type: "button", class: "cot-seg-btn", onclick: () => setView("comm"), text: "Commercials" });
    ui.xBox = h("input", { type: "checkbox", onchange: (e) => { state.x = e.target.checked; changed(); } });
    ui.search = h("input", { type: "search", placeholder: "Symbol", "aria-label": "Find a symbol", oninput: (e) => { state.q = e.target.value.trim(); changed(); } });
    return h("div", { class: "cot-controls desk-only" },
      h("div", { class: "cot-controls-left" },
        h("div", { class: "cot-mkt" }, ui.mktBtn, ui.menu),
        h("div", { class: "cot-seg", role: "group", "aria-label": "Trader group" }, ui.specBtn, ui.commBtn),
        h("label", { class: "cot-check" }, ui.xBox, "Extremes only")),
      h("label", { class: "cot-search" },
        s("svg", { width: 15, height: 15, viewBox: "0 0 24 24", "aria-hidden": "true", class: "cot-ico" }, s("circle", { cx: 11, cy: 11, r: 7 }), s("path", { d: "M20 20l-3.5-3.5" })),
        ui.search));
  }
  function counts() {
    const c = {};
    data.current.instruments.forEach((i) => { c[i.category] = (c[i.category] || 0) + 1; });
    return c;
  }
  function updateControls() {
    const n = state.markets.size;
    clear(ui.mktBtn);
    const labels = CATS.filter((c) => state.markets.has(c.key)).map((c) => c.label);
    const txt = n === CATS.length ? "All markets" : n > 3 ? n + " markets" : n === 0 ? "None" : labels.join(", ");
    append(ui.mktBtn, [h("span", { class: "cot-mkt-label" }, h("span", { class: "cot-muted", text: "Markets" }), h("span", { class: "cot-strong", text: txt }),
      n === CATS.length ? null : h("span", { class: "cot-pill", text: n + " of " + CATS.length })), chevron("M6 9l6 6 6-6")]);
    ui.specBtn.setAttribute("aria-pressed", String(state.view === "spec"));
    ui.commBtn.setAttribute("aria-pressed", String(state.view === "comm"));
    ui.xBox.checked = state.x;
    if (ui.search.value !== state.q) ui.search.value = state.q;
    buildMenu();
  }
  function buildMenu() {
    clear(ui.menu);
    const c = counts();
    ["Financial", "Commodities"].forEach((g) => {
      const box = h("div", { class: "cot-menu-group", role: "group", "aria-label": g }, h("div", { class: "cot-menu-head", text: g }));
      MENU_ORDER.map((k) => CAT[k]).filter((cat) => cat.group === g).forEach((cat) => {
        const on = state.markets.has(cat.key);
        const input = h("input", { type: "checkbox", class: "cot-menu-input", checked: on, onchange: (e) => {
          if (e.target.checked) state.markets.add(cat.key); else state.markets.delete(cat.key);
          changed(); focusMenuItem(cat.key);
        } });
        input.dataset.cat = cat.key;
        box.appendChild(h("label", { class: "cot-menu-row" + (on ? " is-on" : "") }, input, h("span", { class: "cot-menu-box", "aria-hidden": "true" },
          s("svg", { width: 12, height: 12, viewBox: "0 0 12 12", class: "cot-menu-tick" }, s("path", { d: "M2.5 6.2l2.3 2.3 4.7-5" }))),
          h("span", { class: "cot-menu-name", text: cat.label }), h("span", { class: "cot-menu-count", text: String(c[cat.key] || 0) })));
      });
      ui.menu.appendChild(box);
    });
    ui.menu.appendChild(h("div", { class: "cot-menu-foot" },
      h("button", { type: "button", class: "cot-link", text: "Reset to Forex + Metals", onclick: () => { state.markets = new Set(DEFAULT_MARKETS); changed(); } }),
      h("button", { type: "button", class: "cot-link", text: "Select all", onclick: () => { state.markets = new Set(CATS.map((x) => x.key)); changed(); } })));
  }
  function focusMenuItem(key) {
    const el = ui.menu.querySelector('input[data-cat="' + key + '"]');
    if (el) el.focus();
  }
  function toggleMenu(open) {
    const want = typeof open === "boolean" ? open : ui.menu.hidden;
    ui.menu.hidden = !want;
    ui.mktBtn.setAttribute("aria-expanded", String(want));
    if (want) { const first = ui.menu.querySelector("input"); if (first) first.focus(); }
  }
  document.addEventListener("click", (e) => {
    if (!ui.menu || ui.menu.hidden) return;
    if (!ui.menu.contains(e.target) && !ui.mktBtn.contains(e.target)) toggleMenu(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (ui.menu && !ui.menu.hidden) { toggleMenu(false); ui.mktBtn.focus(); e.stopPropagation(); return; }
    if (state.sym) closePanel();
  });

  // ---------------------------------------------------------------- table
  const COLS = [
    { key: null, label: "Instrument", width: 190, align: "left" },
    { key: "cot", label: "COT", width: 64, align: "center" },
    { key: "p6", label: "6M percentile", width: 168, align: "left" },
    { key: "p3", label: "3Y percentile", width: 168, align: "left" },
    { key: "flow", label: "Flow 4w", width: 96, align: "right" },
    { key: "net", label: "Net", width: 104, align: "right" },
    { key: "long", label: "Long share", width: 118, align: "left" },
    { key: "d1w", label: "Δ 1 week", width: 104, align: "right" },
    { key: null, label: "Net, 3 years", width: 156, align: "left" },
  ];
  function buildTable() {
    ui.table = h("table", { class: "cot-table" });
    ui.tableWrap = h("div", { class: "cot-table-wrap desk-only" }, ui.table);
    return ui.tableWrap;
  }
  function sortHeader(col) {
    const active = state.sort && state.sort.key === col.key;
    const isDefault = !state.sort && col.key === "cot";
    const dir = active ? state.sort.dir : isDefault ? "desc" : null;
    const btn = h("button", { type: "button", class: "cot-sort" + (dir ? " is-active" : ""), "data-sort": col.key, onclick: () => cycleSort(col.key) }, col.label);
    if (dir) btn.appendChild(s("svg", { width: 10, height: 10, viewBox: "0 0 10 10", "aria-hidden": "true", class: "cot-sort-ico" },
      s("path", { d: dir === "desc" ? "M5 8L1.5 3.5h7z" : "M5 2l3.5 4.5h-7z" })));
    return { btn: btn, dir: dir };
  }
  function cycleSort(key) {
    const cur = state.sort && state.sort.key === key ? state.sort.dir : null;
    state.sort = cur === null ? { key: key, dir: "desc" } : cur === "desc" ? { key: key, dir: "asc" } : null;
    changed();
    // the header is rebuilt: keep keyboard focus on the same column
    const again = ui.table.querySelector('button.cot-sort[data-sort="' + key + '"]');
    if (again) again.focus();
  }
  function renderTable() {
    clear(ui.table);
    const who = sideName(state.view);
    const cg = h("colgroup");
    COLS.forEach((c) => cg.appendChild(h("col", { class: "cot-c-" + (c.key || (c.width === 190 ? "inst" : "spark")) })));
    ui.table.appendChild(cg);
    const thead = h("thead");
    thead.appendChild(h("tr", { class: "cot-groups" },
      h("th", { class: "cot-g-empty" }),
      h("th", { colspan: 1, class: "cot-g cot-g-score" }, h("span", { text: "Score" })),
      h("th", { colspan: 3, class: "cot-g" }, h("span", { text: "Crowding & flow · " + who })),
      h("th", { colspan: 4, class: "cot-g" }, h("span", { text: "Position · " + who }))));
    const tr = h("tr", { class: "cot-cols" });
    COLS.forEach((c, i) => {
      const th = h("th", { scope: "col", class: "cot-col cot-" + c.align + (i === 0 ? " cot-col-first" : "") });
      if (c.key) {
        const sh = sortHeader(c);
        if (sh.dir) th.setAttribute("aria-sort", sh.dir === "desc" ? "descending" : "ascending");
        th.appendChild(sh.btn);
      } else th.textContent = c.label;
      tr.appendChild(th);
    });
    thead.appendChild(tr);
    ui.table.appendChild(thead);

    const tbody = h("tbody");
    const groups = visibleGroups();
    renderPhoneTable(groups);
    groups.forEach((g) => {
      tbody.appendChild(h("tr", { class: "cot-cat" }, h("td", { colspan: 9 }, g.cat.label + " ",
        h("span", { class: "cot-cat-sub", text: g.rows.length + (g.cat.desc ? " · " + g.cat.desc : "") }))));
      g.rows.forEach((inst) => tbody.appendChild(row(inst)));
    });
    if (!groups.length) {
      tbody.appendChild(h("tr", { class: "cot-empty" }, h("td", { colspan: 9 },
        h("span", { text: "No markets match these filters. " }),
        h("button", { type: "button", class: "cot-link", text: "Reset filters", onclick: resetFilters }))));
    }
    const hidden = CATS.filter((c) => !state.markets.has(c.key));
    if (hidden.length && groups.length) {
      tbody.appendChild(h("tr", { class: "cot-hidden" }, h("td", { colspan: 9 },
        hidden.length + " more market" + (hidden.length === 1 ? " is" : "s are") + " hidden: " + hidden.map((c) => c.label).join(", ") + ". ",
        h("button", { type: "button", class: "cot-link", text: "Show all", onclick: () => { state.markets = new Set(CATS.map((x) => x.key)); changed(); } }))));
    }
    ui.table.appendChild(tbody);
  }
  function row(inst) {
    const v = state.view, sd = inst[v] || {};
    const first = h("td", { class: "cot-inst" }, h("span", { class: "cot-inst-top" }, h("span", { class: "cot-sym", text: inst.symbol }),
      !inst.missing && sd.flip3y ? newBadge(sd.flip3y) : null), h("span", { class: "cot-name", text: inst.name }));
    const tr = h("tr", { class: "cot-row" + (state.sym === inst.symbol ? " is-selected" : ""), tabindex: 0, "aria-label": inst.symbol + " " + inst.name + ": open details" });
    tr.dataset.sym = inst.symbol;
    tr.addEventListener("click", () => openPanel(inst.symbol, tr));
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openPanel(inst.symbol, tr); } });
    tr.appendChild(first);
    if (inst.missing) {
      tr.appendChild(h("td", { colspan: 8, class: "cot-missing", text: "No report this week (thin market, CFTC skips it)" + (inst.last_report ? " · last report " + fmtDay(inst.last_report) : "") }));
      return tr;
    }
    tr.appendChild(h("td", { class: "cot-center" }, cotChip(inst)));
    tr.appendChild(h("td", {}, pctCell(sd.p6, v, inst.symbol, 6)));
    tr.appendChild(h("td", {}, pctCell(sd.p3, v, inst.symbol, 3)));
    tr.appendChild(h("td", { class: "cot-right" }, flowCell(sd, v)));
    tr.appendChild(h("td", { class: "cot-right" }, netCell(sd)));
    tr.appendChild(h("td", {}, longCell(sd)));
    tr.appendChild(h("td", { class: "cot-right" }, d1wCell(sd, inst.oi)));
    tr.appendChild(h("td", { class: "cot-spark-cell" }, sparkline(inst.symbol, v, data.current.as_of)));
    return tr;
  }
  function resetFilters() {
    state.markets = new Set(DEFAULT_MARKETS);
    state.x = false;
    state.q = "";
    changed();
  }

  // ---------------------------------------------------------------- detail panel
  function buildPanel() {
    ui.backdrop = h("div", { class: "cot-backdrop", hidden: true, onclick: closePanel });
    ui.panel = h("aside", { class: "cot-panel", role: "dialog", "aria-modal": "true", "aria-label": "Instrument details", hidden: true });
    ui.panel.addEventListener("keydown", trapFocus);
    document.body.appendChild(ui.backdrop);
    document.body.appendChild(ui.panel);
  }
  function trapFocus(e) {
    if (e.key !== "Tab") return;
    const f = Array.from(ui.panel.querySelectorAll("button, a[href], [tabindex]:not([tabindex='-1'])")).filter((x) => !x.hidden && x.offsetParent !== null);
    if (!f.length) return;
    const firstEl = f[0], lastEl = f[f.length - 1];
    if (e.shiftKey && document.activeElement === firstEl) { e.preventDefault(); lastEl.focus(); }
    else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
  }
  function openPanel(sym, fromEl) {
    const inst = data.current.instruments.find((i) => i.symbol === sym);
    if (!inst) return;
    if (isPhone()) { openPhoneDetail(inst, fromEl); return; }
    ui.returnFocus = fromEl || document.activeElement;
    state.sym = sym;
    writeUrl();
    renderPanel(inst);
    ui.panel.hidden = false;
    ui.backdrop.hidden = false;
    document.body.classList.add("cot-panel-open");
    ui.table.querySelectorAll("tr.cot-row").forEach((r) => r.classList.toggle("is-selected", r.dataset.sym === sym));
    const close = ui.panel.querySelector(".cot-close");
    if (close) close.focus();
  }
  function closePanel() {
    if (ui.phSheet) { ui.phSheet.close(); return; }
    if (!state.sym) return;
    state.sym = null;
    writeUrl();
    ui.panel.hidden = true;
    ui.backdrop.hidden = true;
    document.body.classList.remove("cot-panel-open");
    ui.table.querySelectorAll("tr.cot-row.is-selected").forEach((r) => r.classList.remove("is-selected"));
    const back = ui.returnFocus && document.contains(ui.returnFocus) ? ui.returnFocus : ui.table.querySelector("tr.cot-row");
    if (back) back.focus();
  }
  function describeSide(sym, sd, who) {
    const P6 = pctInt(sd.p6), P3 = pctInt(sd.p3);
    const part = (P, span) => P === null ? null : P >= 100 ? "the most long of the last " + span : P <= 0 ? "the most short of the last " + span : "more long than in " + P + "% of the last " + span;
    const a = part(P6, "6 months"), b = part(P3, "3 years");
    return who + "’ " + sym + " position is " + [a, b].filter(Boolean).join(" and ");
  }
  function whyScore(inst) {
    const sc = inst.score, sd = inst.spec, sym = inst.symbol;
    const steps = [];
    // 1 — Crowding
    const lvlTitle = sc.level < 0 ? "Crowded long" : sc.level > 0 ? "Crowded short" : "Not crowded";
    let t1 = describeSide(sym, sd, "Speculators") + (isNum(sc.blend) ? " (average " + Math.round(sc.blend) + ")." : ".");
    t1 += sc.level < 0 ? " When a trade is this crowded, few buyers are left, so the model reads it against the crowd."
      : sc.level > 0 ? " When a trade is this crowded, few sellers are left, so the model reads it against the crowd."
      : " That is near the middle of its range, so crowding adds nothing.";
    steps.push(["", sc.level, lvlTitle, t1]);
    // 2 — Flow
    const z = sc.z, chg = sc.chg4;
    const flowTitleTxt = sc.flow > 0 ? (sc.level < 0 ? "Still buying" : "Buying") : sc.flow < 0 ? "Selling" : "No clear flow";
    let t2;
    if (!isNum(chg) || !isNum(z)) t2 = "Not enough reports yet to measure the 4-week flow.";
    else {
      t2 = "They " + (chg >= 0 ? "added " : "cut ") + fmtAbs(chg) + " contracts in 4 weeks, " + Math.abs(z).toFixed(1) + "× a typical 4-week move.";
      t2 += sc.flow > 0 ? " Fresh buying counts in " + sym + "’s favour." : sc.flow < 0 ? " Fresh selling counts against " + sym + "." : " Below 0.5×, so the model does not count it.";
    }
    steps.push(["+", sc.flow, flowTitleTxt, t2]);
    // 3 — Total
    const cellTitle = (sc.cell < 0 ? "Bearish" : sc.cell > 0 ? "Bullish" : "Neutral") + " for " + sym;
    const L = signed(String(Math.abs(sc.level)), sc.level), F = signed(String(Math.abs(sc.flow)), sc.flow);
    let t3;
    if (sc.level && sc.flow && Math.sign(sc.level) !== Math.sign(sc.flow)) t3 = Math.abs(sc.level) > Math.abs(sc.flow) ? "Crowding " + L + " outweighs flow " + F + "." : "Crowding " + L + " and flow " + F + " cancel out.";
    else if (sc.level && sc.flow) t3 = "Crowding " + L + " and flow " + F + " point the same way." + (Math.abs(sc.level + sc.flow) > 4 ? " Capped at ±4." : "");
    else if (sc.level) t3 = "Crowding " + L + " alone; no clear flow.";
    else if (sc.flow) t3 = "Flow " + F + " alone; crowding is neutral.";
    else t3 = "No crowding and no clear flow.";
    t3 += " " + pairsSentence(sym, sc.cell);
    steps.push(["=", sc.cell, cellTitle, t3]);
    return steps;
  }
  function pairsSentence(sym, cell) {
    if (sym === "DXY") return "It is the COT of the US Dollar (DXY) row on Economic.";
    if (sym === "GOLD" || sym === "SILVER") return "It is the COT of " + sym + " in Economic’s cross-asset table.";
    const pairs = PAIRS[sym];
    if (!pairs) return "";
    const firsts = pairs.filter((p) => p.startsWith(sym + "/")), seconds = pairs.filter((p) => p.endsWith("/" + sym));
    const list = (a) => a.length <= 1 ? a.join("") : a.slice(0, -1).join(", ") + " and " + a[a.length - 1];
    if (!cell) return sym + " is in " + list(pairs) + "; a neutral score adds nothing to those pairs.";
    const own = cell > 0 ? "bullish" : "bearish", opp = cell > 0 ? "bearish" : "bullish";
    if (firsts.length && seconds.length) return sym + " is the first currency in " + list(firsts) + ", where it reads " + own + " for the pair, and the second in " + list(seconds) + ", where it reads " + opp + ".";
    if (firsts.length) return sym + " is the first currency in " + list(firsts) + ", so there it reads " + own + " for the pair.";
    return sym + " is the second currency in " + list(seconds) + ", so there it reads " + opp + " for the pair.";
  }
  function section(title, body) {
    return h("section", { class: "cot-psec" }, h("h3", { class: "cot-psec-title", text: title }), body);
  }
  function renderPanel(inst) {
    clear(ui.panel);
    const cat = CAT[inst.category];
    const sub = [inst.name, cat ? cat.label : inst.category_label, [inst.exchange, inst.quote ? "quoted vs " + inst.quote : null].filter(Boolean).join(", ")].filter(Boolean).join(" · ");
    ui.panel.appendChild(h("div", { class: "cot-panel-head" },
      h("div", { class: "cot-panel-id" }, h("div", { class: "cot-panel-titlerow" }, h("span", { class: "cot-panel-sym", text: inst.symbol }), inst.missing ? null : cotChip(inst)),
        h("span", { class: "cot-muted", text: sub })),
      h("button", { type: "button", class: "cot-close", "aria-label": "Close", onclick: closePanel },
        s("svg", { width: 18, height: 18, viewBox: "0 0 24 24", "aria-hidden": "true", class: "cot-ico" }, s("path", { d: "M6 6l12 12M18 6L6 18" })))));
    const body = h("div", { class: "cot-panel-body" });
    ui.panel.appendChild(body);
    if (inst.missing) {
      body.appendChild(h("p", { class: "cot-muted", text: "No report this week (thin market, CFTC skips it)" + (inst.last_report ? " · last report " + fmtDay(inst.last_report) : "") + "." }));
      body.appendChild(chartSection(inst));
      return;
    }
    if (inst.in_model && inst.score) {
      const steps = whyScore(inst);
      const box = h("div", { class: "cot-steps" });
      steps.forEach(([op, val, title, text]) => box.appendChild(h("div", { class: "cot-step" },
        h("span", { class: "cot-step-op", text: op }), chip(val, { cls: "cot-chip-step" }),
        h("div", { class: "cot-step-text" }, h("span", { class: "cot-strong", text: title }), h("span", { class: "cot-step-desc", text: text })))));
      const rules = h("ul", { class: "cot-rules", hidden: true },
        h("li", { text: "Crowding: the average (blend) of the speculators’ 6M and 3Y percentiles, read against the crowd: ≥85 → −3, ≥70 → −2, ≥55 → −1, 45–55 → 0, ≥30 → +1, ≥15 → +2, below 15 → +3." }),
        h("li", { text: "Flow: the 4-week change in net position; beyond 0.5× a typical 4-week move (last 52 weeks) it counts +1 when buying, −1 when selling." }),
        h("li", { text: "COT = Crowding + Flow, capped at ±4." }),
        h("li", { text: "On a currency pair: the base currency’s COT minus the quote currency’s (the USD leg is 0)." }));
      const toggle = h("button", { type: "button", class: "cot-link cot-rules-toggle", "aria-expanded": "false", text: "How each step is scored ›" });
      toggle.addEventListener("click", () => { rules.hidden = !rules.hidden; toggle.setAttribute("aria-expanded", String(!rules.hidden)); });
      body.appendChild(section("Why COT is " + signed(String(Math.abs(inst.score.cell)), inst.score.cell) + " for " + inst.symbol, [box, toggle, rules]));
    } else {
      body.appendChild(section("Not in the COT score", h("p", { class: "cot-step-desc",
        text: describeSide(inst.symbol, inst.spec, "Speculators") + ". Only the 8 currencies, the dollar index, gold and silver are scored; for the others this page shows the positioning only." })));
    }
    body.appendChild(chartSection(inst));
    body.appendChild(thisWeek(inst));
    body.appendChild(lastReports(inst));
    const link = economicLink(inst);
    if (link) body.appendChild(link);
  }
  function economicLink(inst) {
    const sym = inst.symbol;
    if (!inst.in_model) return null;
    if (PAIRS[sym]) {
      const p = PAIRS[sym];
      return h("a", { class: "cot-link cot-panel-link", href: "/economic?ccy=" + encodeURIComponent(sym), text: "Open " + p.slice(0, -1).join(", ") + " and " + p[p.length - 1] + " on Economic →" });
    }
    if (sym === "DXY") return h("a", { class: "cot-link cot-panel-link", href: "/economic?ccy=USD", text: "Open the US Dollar (DXY) row on Economic →" });
    return h("a", { class: "cot-link cot-panel-link", href: "/economic", text: "Open " + sym + " on Economic →" });
  }
  function thisWeek(inst) {
    const a = inst.spec, b = inst.comm;
    const grid = h("div", { class: "cot-tw" }, h("span"), h("span", { class: "cot-tw-h", text: "Specs" }), h("span", { class: "cot-tw-h cot-tw-comm", text: "Comms" }));
    const rows = [
      ["6M percentile", (x) => (isNum(x.p6) ? String(pctInt(x.p6)) : "—")],
      ["3Y percentile", (x) => (isNum(x.p3) ? String(pctInt(x.p3)) : "—")],
      ["Net", (x) => fmtK(x.net)],
      ["Net, % of market", (x) => fmtPct(isNum(x.pct_oi) ? x.pct_oi * 100 : null)],
      ["Long share", (x) => (isNum(x.long_share) ? Math.round(x.long_share * 100) + "%" : "—")],
      ["Δ 1 week", (x) => fmtK(x.d1w)],
    ];
    rows.forEach(([label, f]) => { grid.appendChild(h("span", { class: "cot-muted", text: label })); grid.appendChild(h("span", { class: "cot-num cot-right", text: f(a) })); grid.appendChild(h("span", { class: "cot-num cot-right", text: f(b) })); });
    const mkt = h("div", { class: "cot-sub-line", text: "Market (all contracts still open, “open interest”): " + fmtK(inst.oi, false) + (isNum(inst.oi_d1w) ? ", " + fmtK(inst.oi_d1w) + " this week" : "") });
    return section("This week", [grid, mkt]);
  }
  function lastReports(inst) {
    const sp = seriesSlice(inst.symbol, "spec_net", data.current.as_of, 9);
    const cm = seriesSlice(inst.symbol, "comm_net", data.current.as_of, 9);
    const oi = seriesSlice(inst.symbol, "oi", data.current.as_of, 9);
    const t = h("table", { class: "cot-last" });
    t.appendChild(h("thead", {}, h("tr", {}, ["Week", "Specs", "Δ", "Comms", "Market"].map((x, i) => h("th", { class: i ? "cot-right" : "", text: x })))));
    const tb = h("tbody");
    for (let i = sp.dates.length - 1; i >= Math.max(0, sp.dates.length - 8); i--) {
      const v = sp.values[i], pv = i > 0 ? sp.values[i - 1] : null;
      tb.appendChild(h("tr", {},
        h("td", { class: "cot-last-date", text: fmtDay(sp.dates[i]) }),
        h("td", { class: "cot-right cot-num", text: fmtK(v) }),
        h("td", { class: "cot-right cot-num cot-muted", text: isNum(v) && isNum(pv) ? fmtK(v - pv) : "—" }),
        h("td", { class: "cot-right cot-num", text: fmtK(cm.values[i]) }),
        h("td", { class: "cot-right cot-num cot-muted", text: fmtK(oi.values[i], false) })));
    }
    t.appendChild(tb);
    return section("Last 8 reports", t);
  }
  function axisK(v) {
    const a = Math.abs(v);
    const str = a >= 1e6 ? +(a / 1e6).toFixed(2) + "M" : a >= 1e3 ? +(a / 1e3).toFixed(1) + "K" : String(Math.round(a));
    return signed(str, v);
  }
  function niceStep(span) {
    const raw = span / 6, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
  }
  function chartSection(inst) {
    const sp = seriesSlice(inst.symbol, "spec_net", data.current.as_of, 156);
    const cm = seriesSlice(inst.symbol, "comm_net", data.current.as_of, 156);
    const oi = seriesSlice(inst.symbol, "oi", data.current.as_of, 156);
    const legend = h("div", { class: "cot-chart-legend" },
      h("span", {}, h("span", { class: "cot-swatch cot-swatch-spec" }), "Speculators"),
      h("span", {}, h("span", { class: "cot-swatch cot-swatch-comm" }), "Commercials"),
      h("span", {}, h("span", { class: "cot-swatch cot-swatch-6m" }), "6M window"));
    const W = 468, H = 210, L = 52, R = 458, T = 10, B = 184;
    const svg = s("svg", { width: W, height: H, viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Net positions over 3 years", class: "cot-chart" });
    const all = sp.values.concat(cm.values).filter(isNum);
    const n = sp.dates.length;
    const wrap = h("div", { class: "cot-chart-wrap" }, svg);
    if (all.length < 2 || n < 2) return section("Net position · 3 years", [legend, wrap]);
    let lo = Math.min(0, Math.min.apply(null, all)), hi = Math.max(0, Math.max.apply(null, all));
    const step = niceStep(hi - lo || 1);
    lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
    const X = (i) => L + (i * (R - L)) / (n - 1);
    const Y = (v) => B - ((v - lo) / (hi - lo)) * (B - T);
    const x6 = X(Math.max(0, n - 26));
    svg.appendChild(s("rect", { x: x6.toFixed(1), y: T, width: (R - x6).toFixed(1), height: B - T, class: "cot-chart-6m" }));
    svg.appendChild(s("text", { x: ((x6 + R) / 2).toFixed(1), y: T + 12, class: "cot-chart-lbl cot-mid", text: "6M" }));
    for (let v = lo; v <= hi + step / 2; v += step) {
      svg.appendChild(s("line", { x1: L, x2: R, y1: Y(v).toFixed(1), y2: Y(v).toFixed(1), class: Math.abs(v) < step / 2 ? "cot-chart-zero" : "cot-chart-grid" }));
      svg.appendChild(s("text", { x: L - 8, y: (Y(v) + 4).toFixed(1), class: "cot-chart-lbl cot-end", text: Math.abs(v) < step / 2 ? "0" : axisK(v) }));
    }
    let lastYear = null;
    sp.dates.forEach((d, i) => {
      const y = d.slice(0, 4);
      if (lastYear !== null && y !== lastYear) {
        svg.appendChild(s("line", { x1: X(i).toFixed(1), x2: X(i).toFixed(1), y1: B, y2: B + 4, class: "cot-chart-tick" }));
        svg.appendChild(s("text", { x: X(i).toFixed(1), y: B + 17, class: "cot-chart-lbl cot-mid", text: y }));
      }
      lastYear = y;
    });
    const path = (vals) => { let d = "", pen = false; vals.forEach((v, i) => { if (!isNum(v)) { pen = false; return; } d += (pen ? " L" : d ? " M" : "M") + X(i).toFixed(1) + "," + Y(v).toFixed(1); pen = true; }); return d; };
    svg.appendChild(s("path", { d: path(cm.values), class: "cot-chart-comm" }));
    svg.appendChild(s("path", { d: path(sp.values), class: "cot-chart-spec" }));
    const lastIdx = (vals) => { for (let i = vals.length - 1; i >= 0; i--) if (isNum(vals[i])) return i; return -1; };
    const ic = lastIdx(cm.values), is_ = lastIdx(sp.values);
    if (ic >= 0) svg.appendChild(s("circle", { cx: X(ic).toFixed(1), cy: Y(cm.values[ic]).toFixed(1), r: 4, class: "cot-chart-end-comm" }));
    if (is_ >= 0) svg.appendChild(s("circle", { cx: X(is_).toFixed(1), cy: Y(sp.values[is_]).toFixed(1), r: 4, class: "cot-chart-end-spec" }));
    // crosshair + tooltip
    const cross = s("line", { y1: T, y2: B, class: "cot-chart-cross", visibility: "hidden" });
    svg.appendChild(cross);
    const tip = h("div", { class: "cot-chart-tip", hidden: true });
    wrap.appendChild(tip);
    const hit = s("rect", { x: L, y: T, width: R - L, height: B - T, class: "cot-chart-hit" });
    svg.appendChild(hit);
    const move = (e) => {
      const r = svg.getBoundingClientRect();
      const px = ((e.clientX - r.left) / r.width) * W;
      const i = Math.max(0, Math.min(n - 1, Math.round(((px - L) / (R - L)) * (n - 1))));
      const x = X(i);
      cross.setAttribute("x1", x.toFixed(1)); cross.setAttribute("x2", x.toFixed(1)); cross.setAttribute("visibility", "visible");
      clear(tip);
      append(tip, [h("div", { class: "cot-strong", text: fmtDay(sp.dates[i]) }),
        h("div", {}, h("span", { class: "cot-swatch cot-swatch-spec" }), "Specs " + fmtK(sp.values[i])),
        h("div", {}, h("span", { class: "cot-swatch cot-swatch-comm" }), "Comms " + fmtK(cm.values[i])),
        h("div", { class: "cot-muted", text: "Market " + fmtK(oi.values[i], false) })]);
      tip.hidden = false;
      const left = (x / W) * r.width;
      tip.style.left = Math.min(Math.max(left + 12, 0), r.width - 150) + "px";
      tip.style.top = "8px";
    };
    hit.addEventListener("mousemove", move);
    hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); tip.hidden = true; });
    return section("Net position · 3 years", [legend, wrap]);
  }

  // ---------------------------------------------------------------- CSV
  function downloadCsv() {
    const rows = [];
    visibleGroups().forEach((g) => g.rows.forEach((i) => rows.push(i)));
    const q = (x) => '"' + String(x === null || x === undefined ? "" : x).replace(/"/g, '""') + '"';
    const r = (x, d) => (isNum(x) ? (d ? x.toFixed(d) : String(Math.round(x))) : "");
    const lines = [["week", "category", "symbol", "name", "view", "cot", "level", "flow", "p6", "p3", "z4w", "chg4", "net", "pct_market", "long_share", "d1w", "oi"].join(",")];
    rows.forEach((i) => {
      const sd = i[state.view] || {}, sc = i.in_model && i.score ? i.score : {};
      lines.push([data.current.as_of, q(CAT[i.category] ? CAT[i.category].label : i.category), q(i.symbol), q(i.name), q(state.view),
        r(sc.cell), r(sc.level), r(sc.flow), r(isNum(sd.p6) ? sd.p6 * 100 : null, 1), r(isNum(sd.p3) ? sd.p3 * 100 : null, 1),
        r(sd.z4w, 2), r(sd.chg4), r(sd.net), r(isNum(sd.pct_oi) ? sd.pct_oi * 100 : null, 1),
        r(isNum(sd.long_share) ? sd.long_share * 100 : null, 1), r(sd.d1w), r(i.oi)].join(","));
    });
    const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv;charset=utf-8" });
    const a = h("a", { href: URL.createObjectURL(blob), download: "cot-" + data.current.as_of + "-" + state.view + ".csv" });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ---------------------------------------------------------------- phone (Faza 11F)
  // The design of 24 Sep (docs/design/cot-v2/Mobile.html) with the header folded: Symbol (+ net) | COT | 3Y pct | Δ 1 wk,
  // the cards on one scrollable row, Markets full width, Spec / Comm, then Extremes only and the week. A row opens the same
  // detail as the desktop panel, as a full-screen sheet (PhoneUI.sheet). Desktop is untouched: CSS shows one or the other.
  function isPhone() { return !!(window.PhoneUI && window.PhoneUI.isPhone()); }
  function buildPhoneControls() {
    const P = window.PhoneUI;
    const all = CATS.map((c) => c.key);
    const counts = {};
    data.current.instruments.forEach((i) => { counts[i.category] = (counts[i.category] || 0) + 1; });
    const group = (g) => ({ label: g, items: MENU_ORDER.map((k) => CAT[k]).filter((c) => c.group === g).map((c) => ({ key: c.key, label: c.label, count: counts[c.key] || 0 })) });
    ui.phMkt = P.filterButton({
      label: "Markets", noun: "markets", nounOne: "market", groups: [group("Financial"), group("Commodities")],
      get: () => state.markets,
      set: (sel) => { state.markets = sel.size ? sel : new Set(all); changed(); },
      count: (sel) => data.current.instruments.filter((i) => (!sel.size || sel.has(i.category))).length,
      summary: (chosen) => (chosen.length > 3 ? chosen.length + " markets" : chosen.join(", ")),
    });
    ui.phSeg = P.segmented({ label: "Trader group", value: state.view,
      items: [{ key: "spec", label: "Speculators" }, { key: "comm", label: "Commercials" }], onChange: (k) => setView(k) });
    ui.phX = h("input", { type: "checkbox", onchange: (e) => { state.x = e.target.checked; changed(); } });
    ui.phPrev = h("button", { type: "button", class: "cot-btn cot-icon-btn cot-ph-40", "aria-label": "Previous week", onclick: () => stepWeek(1) }, chevron("M15 18l-6-6 6-6"));
    ui.phNext = h("button", { type: "button", class: "cot-btn cot-icon-btn cot-ph-40", "aria-label": "Next week", onclick: () => stepWeek(-1) }, chevron("M9 18l6-6-6-6"));
    ui.phWeek = h("select", { class: "cot-week-select", "aria-label": "Report week", onchange: (e) => setWeek(e.target.value) });
    data.index.weeks.forEach((w) => ui.phWeek.appendChild(h("option", { value: w, text: fmtDay(w) })));
    return h("div", { class: "phone-only cot-ph-controls" }, ui.phMkt, ui.phSeg,
      h("div", { class: "cot-ph-row" }, h("label", { class: "cot-check cot-ph-check" }, ui.phX, "Extremes only"),
        h("div", { class: "cot-weeknav" }, ui.phPrev, h("label", { class: "cot-btn cot-week cot-ph-40" }, h("span", { class: "cot-muted", text: "Week" }), ui.phWeek, chevron("M6 9l6 6 6-6")), ui.phNext)));
  }
  function updatePhoneHeader() {
    if (!ui.phHead) return;
    const w = data.current;
    clear(ui.phHead);
    const latest = w.as_of === data.index.latest;
    append(ui.phHead, ["Positions as of ", h("span", { class: "cot-strong", text: fmtDayW(w.as_of) }), h("br"),
      (latest ? "Next report " + bucharest(w.next_release, false) : "Released " + bucharest(w.released, false)) + " Bucharest"]);
    if (ui.phWeek) {
      ui.phWeek.value = w.as_of;
      const i = data.index.weeks.indexOf(w.as_of);
      ui.phNext.disabled = i <= 0;
      ui.phPrev.disabled = i >= data.index.weeks.length - 1;
    }
  }
  function updatePhoneControls() {
    if (!ui.phMkt) return;
    ui.phMkt.refresh();
    ui.phSeg.set(state.view);
    ui.phX.checked = state.x;
  }
  function renderPhoneTable(groups) {
    if (!ui.phTable) return;
    clear(ui.phTable);
    const v = state.view;
    const t = h("table", { class: "cot-ph-table" },
      h("thead", {}, h("tr", {}, h("th", { text: "Symbol" }), h("th", { class: "cot-center", text: "COT" }), h("th", { text: "3Y pct" }), h("th", { class: "cot-right", text: "Δ 1 wk" }))));
    const tb = h("tbody");
    groups.forEach((g) => {
      tb.appendChild(h("tr", { class: "cot-cat" }, h("td", { colspan: 4 }, g.cat.label + " ", h("span", { class: "cot-cat-sub", text: String(g.rows.length) }))));
      g.rows.forEach((inst) => {
        const sd = inst[v] || {};
        const tr = h("tr", { class: "cot-row", tabindex: 0, "aria-label": inst.symbol + " " + inst.name + ": open details" });
        tr.dataset.sym = inst.symbol;
        tr.addEventListener("click", () => openPanel(inst.symbol, tr));
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openPanel(inst.symbol, tr); } });
        tr.appendChild(h("td", { class: "cot-ph-sym" }, h("span", { class: "cot-inst-top" }, h("span", { class: "cot-sym", text: inst.symbol }),
          !inst.missing && sd.flip3y ? newBadge(sd.flip3y) : null), h("span", { class: "cot-name", text: inst.missing ? "no report" : fmtK(sd.net, false) })));
        if (inst.missing) {
          tr.appendChild(h("td", { colspan: 3, class: "cot-missing", text: "No report this week" + (inst.last_report ? " · last " + fmtDay(inst.last_report) : "") }));
        } else {
          const share = isNum(sd.d1w) && isNum(inst.oi) && inst.oi ? sd.d1w / inst.oi : null;
          const p3 = isNum(sd.p3) ? pctBar(sd.p3, v, 48) : null;
          tr.appendChild(h("td", { class: "cot-center" }, cotChip(inst)));
          tr.appendChild(h("td", {}, p3 ? h("span", { class: "cot-pct", title: pctTitle(p3.P, 3, v, inst.symbol) }, p3.track,
            h("span", { class: "cot-pct-num" + (p3.ext ? " is-ext" : ""), text: String(p3.P) })) : h("span", { class: "cot-muted", text: "—" })));
          tr.appendChild(h("td", { class: "cot-right" }, h("span", { class: "cot-stack" },
            h("span", { class: "cot-d1w" + (isNum(share) && Math.abs(share) >= 0.05 ? " is-big" : ""), text: fmtK(sd.d1w) }),
            h("span", { class: "cot-sub", text: fmtPct(isNum(share) ? share * 100 : null) + " mkt" }))));
        }
        tb.appendChild(tr);
      });
    });
    if (!groups.length) {
      tb.appendChild(h("tr", { class: "cot-empty" }, h("td", { colspan: 4 }, h("span", { text: "No markets match these filters. " }),
        h("button", { type: "button", class: "cot-link", text: "Reset filters", onclick: resetFilters }))));
    }
    const hidden = CATS.filter((c) => !state.markets.has(c.key));
    if (hidden.length && groups.length) {
      tb.appendChild(h("tr", { class: "cot-hidden" }, h("td", { colspan: 4 }, hidden.length + " more market" + (hidden.length === 1 ? " is" : "s are") + " hidden. ",
        h("button", { type: "button", class: "cot-link", text: "Show all", onclick: () => { state.markets = new Set(CATS.map((x) => x.key)); changed(); } }))));
    }
    t.appendChild(tb);
    ui.phTable.appendChild(t);
    ui.phTable.appendChild(h("p", { class: "ph-note", text: "Tap a row for the chart, why the score is what it is, and the last 8 reports." }));
  }
  function openPhoneDetail(inst, fromEl) {
    if (ui.phSheet) { const old = ui.phSheet; ui.phSheet = null; old.close(true); }
    state.sym = inst.symbol;
    writeUrl();
    renderPanel(inst);                                     // the desktop panel's content, moved into the sheet
    const title = ui.panel.querySelector(".cot-panel-id");
    const body = ui.panel.querySelector(".cot-panel-body");
    clear(ui.panel);
    ui.phReturn = fromEl || null;
    const sheet = window.PhoneUI.sheet({ titleNode: title, body: body, full: true, cls: "cot-ph-sheet", returnFocus: fromEl || null,
      onClose: () => {
        if (ui.phSheet !== sheet) return;
        ui.phSheet = null;
        if (state.sym) { state.sym = null; writeUrl(); }
      } });
    ui.phSheet = sheet;
  }

  // ---------------------------------------------------------------- flow
  function changed() {
    writeUrl();
    updateControls();
    updatePhoneControls();
    renderCards();
    renderTable();
  }
  function setView(v) { state.view = v; changed(); }
  function stepWeek(delta) {
    const i = data.index.weeks.indexOf(data.current.as_of) + delta;
    if (i >= 0 && i < data.index.weeks.length) setWeek(data.index.weeks[i]);
  }
  function setWeek(week) {
    if (!week || week === (data.current && data.current.as_of)) return;
    data.note = "";
    ui.tableWrap.classList.add("is-loading");
    ui.cards.classList.add("is-loading");
    loadWeek(week).then((w) => {
      data.current = w;
      state.week = week;
      ui.tableWrap.classList.remove("is-loading");
      ui.cards.classList.remove("is-loading");
      updateHeader();
      changed();
      if (state.sym) {
        const inst = w.instruments.find((i) => i.symbol === state.sym);
        if (!inst) closePanel();
        else if (ui.phSheet) openPhoneDetail(inst, ui.phReturn);
        else renderPanel(inst);
      }
    }).catch(fail);
  }
  function fail(err) {
    clear(app);
    app.appendChild(h("p", { class: "cot-loading", text: "Could not load the COT data (" + (err && err.message ? err.message : err) + ")." }));
  }
  function start() {
    readUrl();
    Promise.all([getJson("index.json"), getJson("series.json")]).then(([index, series]) => {
      data.index = index;
      data.series = series;
      let week = state.week;
      if (week && !index.weeks.includes(week)) { data.note = "Week " + week + " is not available; showing the latest."; week = null; }
      week = week || index.latest;
      return loadWeek(week).then((w) => {
        data.current = w;
        state.week = w.as_of;
        clear(app);
        ui.cards = h("div", { class: "cot-cards" });
        app.appendChild(buildHeader());
        app.appendChild(buildLegend());
        app.appendChild(ui.cards);
        app.appendChild(buildControls());
        if (window.PhoneUI) app.appendChild(buildPhoneControls());
        app.appendChild(buildTable());
        ui.phTable = h("div", { class: "phone-only cot-ph-tablewrap" });
        app.appendChild(ui.phTable);
        app.appendChild(h("p", { class: "cot-source", text: "Source: CFTC Commitments of Traders, Legacy report, futures and options combined. Positions are as of Tuesday and published on Friday at 15:30 ET. Percentile = rank within the trailing 26 / 156 reports. COT = Crowding (speculator 6M and 3Y percentiles, read against the crowd) + Flow (4-week change beyond 0.5× a typical move), capped at ±4." }));
        buildPanel();
        updateHeader();
        changed();
        if (window.PhoneUI) window.PhoneUI.foldHowToRead(app);
        if (state.sym) {
          const sym = state.sym;
          state.sym = null;
          const tr = ui.table.querySelector('tr.cot-row[data-sym="' + CSS.escape(sym) + '"]');
          openPanel(sym, tr);
        }
      });
    }).catch(fail);
  }
  start();
})();
