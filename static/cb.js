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
  function fmtMonthYear(x) { const d = new Date(x); return MONTHS[d.getUTCMonth()] + " " + String(d.getUTCFullYear()).slice(2); }
  function weekday(iso) { return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date(ms(iso)).getUTCDay()]; }
  function todayIn(tz) {
    try { return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date()); }
    catch (e) { return new Date().toISOString().slice(0, 10); }
  }
  function daysBetween(a, b) { return Math.round((ms(b) - ms(a)) / DAY_MS); }
  function pct(p) { return (Number(p) * 100).toFixed(p >= 0.995 || p < 0.005 ? 0 : 1) + "%"; }

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
  function probsText(h) {
    return (h.probabilities || []).slice().sort(function (a, b) { return a.moves - b.moves; }).map(function (p) {
      const label = p.moves === 0 ? "hold" : (h.direction === "cut" ? MINUS : "+") + 25 * p.moves;
      return pct(p.p) + " " + label;
    }).join(DOT);
  }

  // ---- overview ------------------------------------------------------------------------------------------------------------
  // A value that is a LEVEL (BKBM base, sovereign proxy), not a policy-equivalent bp: shown as a level with its method badge and its basis
  // label ("BKBM base" / "sovereign proxy"); the tooltip says why there is no bp (and, for NZD, why the GAP is n/a).
  function levelCell(m, r, what) {
    const meta = state.meta || {};
    const label = (meta.level_label || {})[m.level_kind] || m.level_kind;
    const tip = [what, (meta.level_help || {})[m.level_kind] || "", m.na ? "No bp: " + m.na : "", m.level_kind === "bkbm" && r.gap && r.gap.na ? "GAP n/a: " + r.gap.na : ""].filter(Boolean).join("\n");
    return '<td class="econ-cell cb-lvlcell' + (m.stale ? " cb-stale" : "") + '" title="' + esc(tip) + '"><span class="cb-lvl">' + fmtRate(m.level, 2) + "%</span>" + flagBadge(m.flag) + staleBadge(m.stale) +
      '<div class="cb-prob">' + esc(label) + "</div></td>";
  }
  function horizonCell(r) {
    const h = r.horizon || {};
    if (h.kind === "step") {
      const cell = numCell({ v: h.step_bp, flag: h.flag, stale: h.stale }, { scale: 25, dp: 1, extra: '<div class="cb-prob">' + esc(probsText(h)) + "</div>",
                                                                          tip: "Implied step at the " + fmtDate(h.meeting) + " meeting" });
      return cell;
    }
    if (h.kind === "window") {
      const extra = '<div class="cb-prob">' + h.n_meetings + " mtg" + (h.n_meetings === 1 ? "" : "s") + DOT + "window " + esc(fmtRange(h.window)) + "</div>";
      return numCell({ v: h.cum_bp, flag: h.flag, stale: h.stale }, { scale: 100, dp: 1, extra: extra,
        tip: "Cumulative bp to the first window that covers the next meeting; the window average spans " + h.n_meetings + " meeting(s), so it is an upper bound for the next meeting alone." });
    }
    if (isNum(h.level)) return levelCell({ level: h.level, level_kind: h.level_kind, flag: h.flag, stale: h.stale, na: h.na }, r, "Level at the " + fmtDate(h.meeting) + " meeting");
    return '<td class="econ-cell cell-na cb-na" title="' + esc(h.na || r.na || "n/a") + '">' + NA + "</td>";
  }
  function repricingTip(rep) {
    return ["1w", "1m"].map(function (w) {
      const x = rep[w];
      if (!x) return w + ": n/a";
      const y = x.years || {};
      const one = function (m) { return isNum(m && m.v) ? fmtSigned(m.v, 1) + " bp" : "n/a"; };
      return w + " (" + (x.bd || "?") + " bd, vs " + fmtDate(x.prev) + "): end-2026 " + one(y["2026"]) + ", end-2027 " + one(y["2027"]) + ", next step " + one(x.step);
    }).join("\n");
  }
  function repricingCell(r) {
    const m = r.repricing["1w"].years["2026"], tip = repricingTip(r.repricing);
    if (!isNum(m.v)) return '<td class="econ-cell cell-na cb-na" title="' + esc((m.na || "n/a") + "\n" + tip) + '">' + NA + "</td>";
    return numCell(m, { scale: 25, dp: 1, tip: "Change of the implied level at end-2026 over 5 business days\n" + tip });
  }
  function lastDecisionCell(r) {
    const d = r.last_decision;
    if (!d) return '<td class="econ-cell cell-na cb-na" title="no decision on record">' + NA + "</td>";
    const hasSp = isNum(d.surprise_consensus_bp);
    const cls = d.delta_bp > 0 ? "cb-hawk" : d.delta_bp < 0 ? "cb-dove" : "";
    return '<td class="econ-cell cb-lastdec" title="' + esc("Decided " + fmtDate(d.date) + ", effective " + fmtDate(d.effective) + ", status " + d.status + "\n" + (hasSp ? "surprise vs consensus " + fmtSigned(d.surprise_consensus_bp, 1) + " bp" : "no consensus on record")) + '">' +
      fmtDay(d.date) + DOT + '<span class="cb-val ' + cls + '">' + fmtSigned(d.delta_bp, 0) + " bp</span>" +
      '<div class="cb-prob">' + (hasSp ? "surprise " + fmtSigned(d.surprise_consensus_bp, 1) : "surprise " + NA) + "</div></td>";
  }
  function reactionCell(r) {
    const a = r.reaction.next, b = r.reaction.year;
    if (!isNum(a.v) && !isNum(b.v)) return '<td class="econ-cell cell-na cb-na" title="' + esc("next meeting: " + a.na + "\nyear end: " + b.na) + '">' + NA + "</td>";
    const tip = "Change of the implied level between the close of T-1 and T (" + fmtDate(r.reaction.date) + ")\nnext meeting: " + (isNum(a.v) ? fmtSigned(a.v, 1) + " bp" : a.na) +
      "\nlast meeting of the year: " + (isNum(b.v) ? fmtSigned(b.v, 1) + " bp" : b.na);
    return '<td class="econ-cell cb-num"' + tint(isNum(a.v) ? a.v : b.v, 10) + ' title="' + esc(tip) + '">' + valueSpan(a, 1, "", true) + '<span class="cb-sep"> / </span>' + valueSpan(b, 1, "", true) +
      flagBadge(isNum(a.v) ? a.flag : b.flag) + "</td>";
  }
  function gapCell(r) {
    const g = r.gap;
    if (g.kind === "n/a" || !g.years || !Object.keys(g.years).length) return '<td class="econ-cell cell-na cb-na" title="' + esc(g.na || "n/a") + '">' + NA + "</td>";
    const tip = Object.keys(g.years).map(function (y) {
      const m = g.years[y];
      return y + ": market " + fmtRate(m.market, 3) + " vs dots " + fmtRate(m.bank, 3) + " = " + (isNum(m.v) ? fmtSigned(m.v, 1) + " bp" : "n/a");
    }).join("\n");
    return numCell(g.headline, { scale: 50, dp: 1, tip: "GAP = market - bank (Fed dots), end-2026 headline\n" + tip });
  }
  function endCell(m, y, r) {
    if (!isNum(m.v) && isNum(m.level)) return levelCell(m, r, "Level at the last meeting of " + y + " (" + fmtDate(m.meeting) + ")");
    if (!isNum(m.v)) return '<td class="econ-cell cell-na cb-na" title="' + esc(m.na || "n/a") + '">' + NA + "</td>";
    return numCell(m, { scale: 100, dp: 1, tip: "Cumulative bp at the last meeting of " + y + " (" + fmtDate(m.meeting) + ")" });
  }
  function bankTable(ov) {
    const rows = ov.banks.map(function (r) {
      const n = r.next;
      const nextHtml = n && n.decision
        ? '<div class="cb-next-date">' + fmtDate(n.decision) + '</div><div class="cb-prob">' + cdSpan(n) + (n.time && n.time.tbd ? DOT + '<span title="The BoJ announces at an irregular time on the day">' + esc(timeText(n.time)) + "</span>" : "") + "</div>"
        : '<span class="cb-na" title="' + esc((n && n.na) || "n/a") + '">' + NA + "</span>";
      return '<tr class="cb-row" data-href="' + esc(r.href) + '" tabindex="0">' +
        '<td class="cb-bank" title="' + esc(r.bank.name) + '"><a href="' + esc(r.href) + '"><b>' + esc(r.ccy) + "</b>" + DOT + esc(r.bank.short) + "</a>" + staleBadge(r.stale) + "</td>" +
        '<td class="econ-cell cb-ratecell">' + rateHtml(r.rate) + "</td>" +
        '<td class="econ-cell cb-nextcell">' + nextHtml + "</td>" +
        horizonCell(r) +
        endCell(r.end["2026"], "2026", r) + endCell(r.end["2027"], "2027", r) +
        gapCell(r) +
        repricingCell(r) +
        lastDecisionCell(r) + reactionCell(r) + "</tr>";
    }).join("");
    return '<div class="cb-scroll"><table class="cb-table cb-compact"><thead><tr>' +
      "<th>Bank</th><th>Rate %</th><th>Next meeting</th>" +
      '<th title="EXACT / CURVE: implied step and probability at the next meeting. UPPER BOUND: cumulative bp to the first window that covers it and the number of meetings it spans.">Next mtg / horizon</th>' +
      '<th title="Cumulative bp at the last meeting of 2026 (implied policy rate minus the base rate)">End-2026</th>' +
      '<th title="Cumulative bp at the last meeting of 2027">End-2027</th>' +
      '<th title="Market minus bank, bp: Fed dots median vs the implied rate at the end of 2026. Other banks publish no comparable path.">GAP</th>' +
      '<th title="Change of the implied level at end-2026 over 5 business days; 1 month, end-2027 and the next-meeting step are in the tooltip.">Repricing 1w</th>' +
      '<th title="Date, change in bp and surprise vs consensus (decided rate minus consensus, bp)">Last decision</th>' +
      '<th title="Change of the implied level between the close of T-1 and T of the last decision: at the next meeting / at the last meeting of the year">Reaction</th>' +
      "</tr></thead><tbody>" + rows + "</tbody></table></div>";
  }

  function pairTable(pj) {
    const rows = pj.pairs.map(function (p) {
      const cur = p.current;
      const imp = function (y) {
        const m = p.implied[y];
        return numCell(m.diff_bp, { scale: 200, dp: 1, extra: isNum(m.cum_bp.v) ? '<div class="cb-prob">' + fmtSigned(m.cum_bp.v, 1) + " bp vs today</div>" : "",
                                    tip: "Implied " + p.display + " rate differential at the last meeting of " + y + " (base " + EN.replace("–", MINUS) + " quote), bp" });
      };
      const rep = function (w, y) { return numCell(p.repricing[w][y], { scale: 25, dp: 1, tip: "Change of the implied differential at end-" + y + " over " + (w === "1w" ? "5" : "21") + " business days, bp" }); };
      return '<tr class="cb-row" data-href="' + esc(p.href) + '" tabindex="0">' +
        '<td class="cb-bank"><a href="' + esc(p.href) + '"><b>' + esc(p.display) + "</b></a>" + '<div class="cb-prob">' + esc(p.base) + " " + MINUS + " " + esc(p.quote) + "</div></td>" +
        numCell(cur, { scale: 300, dp: 1, noBadge: true, tip: "Current policy-rate differential (carry perspective), bp" }) + imp("2026") + imp("2027") + rep("1w", "2026") + rep("1w", "2027") + rep("1m", "2026") + rep("1m", "2027") + "</tr>";
    }).join("");
    return '<div class="cb-scroll"><table class="cb-table"><thead><tr>' +
      '<th>Pair</th><th title="Base rate minus quote rate, bp">Now (bp)</th><th title="Implied differential at the last meeting of 2026, bp">End-2026</th><th title="Implied differential at the last meeting of 2027, bp">End-2027</th>' +
      '<th title="Change of the implied differential over 5 business days">1w end-2026</th><th>1w end-2027</th><th title="Over 21 business days">1m end-2026</th><th>1m end-2027</th>' +
      "</tr></thead><tbody>" + rows + "</tbody></table></div>";
  }

  function wireRows(scope) {
    scope.querySelectorAll("tr.cb-row").forEach(function (tr) {
      const go = function () { window.location.href = tr.dataset.href; };
      tr.addEventListener("click", function (ev) { if (ev.target.closest("a")) return; go(); });
      tr.addEventListener("keydown", function (ev) { if (ev.key === "Enter") go(); });
    });
  }

  function legendHtml(meta) {
    const badges = Object.keys(meta.flags).filter(function (k) { return k !== "DECIDED"; }).map(function (k) {
      return '<span class="cb-legend-item">' + flagBadge(k) + '<span class="muted"> ' + esc(meta.flags[k].help) + "</span></span>";
    }).join("");
    return '<div class="econ-legend cb-legend" data-htr><span class="muted">Market-implied policy paths from futures / OIS curves. <span class="cb-hawk"><b>Blue = hawkish</b></span> (higher rates than the base), ' +
      '<span class="cb-dove"><b>red = dovish</b></span>; the sign is always written out. Grey = stale source. <b>' + NA + "</b> = not available: hover for the reason. Click a row for the bank page.</span>" +
      '<div class="cb-legend-badges">' + badges + "</div></div>";
  }

  function renderOverview(ov) {
    state.meta = ov.meta;
    titleEl.textContent = "Central Banks";
    metaEl.innerHTML = "As of " + esc(fmtDate(ov.meta.asof)) + DOT + ov.banks.length + " banks" + DOT + "stale after " + ov.meta.stale_after_bd + " business days";
    root.innerHTML = legendHtml(ov.meta) +
      '<div class="cb-tabs" role="tablist"><button type="button" class="cb-tab active" data-tab="banks" role="tab" aria-selected="true">Banks</button>' +
      '<button type="button" class="cb-tab" data-tab="pairs" role="tab" aria-selected="false">Pairs</button></div>' +
      '<section class="cb-tablewrap"><div id="cbBanks"><div class="desk-only">' + bankTable(ov) + "</div>" + bankList(ov) + '</div><div id="cbPairs" hidden><p class="muted cb-loading">Loading pairs…</p></div></section>';
    wireRows(document.getElementById("cbBanks"));
    let pairsLoaded = false;
    root.querySelectorAll(".cb-tab").forEach(function (b) {
      b.addEventListener("click", function () {
        root.querySelectorAll(".cb-tab").forEach(function (x) { x.classList.toggle("active", x === b); x.setAttribute("aria-selected", x === b ? "true" : "false"); });
        document.getElementById("cbBanks").hidden = b.dataset.tab !== "banks";
        document.getElementById("cbPairs").hidden = b.dataset.tab !== "pairs";
        if (b.dataset.tab === "pairs" && !pairsLoaded) {
          pairsLoaded = true;
          getJSON(CFG.urls.pairs).then(function (pj) {
            document.getElementById("cbPairs").innerHTML = '<div class="desk-only">' + pairTable(pj) + "</div>" + pairList(pj);
            wireRows(document.getElementById("cbPairs"));
          }).catch(function (e) { document.getElementById("cbPairs").innerHTML = failHtml(e); });
        }
      });
    });
    if (window.location.hash === "#pairs") root.querySelector('.cb-tab[data-tab="pairs"]').click();
    if (window.PhoneUI) window.PhoneUI.foldHowToRead();
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
    const step = span > 40 ? 6 : span > 20 ? 3 : 2;
    const d0 = new Date(min), ticks = [];
    let y = d0.getUTCFullYear(), m = d0.getUTCMonth();
    m = Math.ceil(m / step) * step;
    for (;;) {
      const v = Date.UTC(y + Math.floor(m / 12), m % 12, 1);
      if (v > max) break;
      if (v >= min) ticks.push({ value: v });
      m += step;
    }
    scale.ticks = ticks;
  }
  function baseScales(c, yTitle) {
    return {
      x: { type: "linear", grid: { color: withAlpha(c.border, 0.7) }, ticks: { color: c.muted, callback: function (v) { return fmtMonthYear(v); }, maxRotation: 0 }, afterBuildTicks: monthTicks },
      y: { grid: { color: withAlpha(c.border, 0.7) }, ticks: { color: c.muted }, title: { display: true, text: yTitle, color: c.muted } }
    };
  }
  function destroyCharts() { state.charts.forEach(function (ch) { ch.destroy(); }); state.charts = []; }
  function registerRedraw(fn) {
    state.redraw.push(fn);
    fn();
  }
  function watchTheme() {
    new MutationObserver(function () { destroyCharts(); state.redraw.forEach(function (fn) { fn(); }); }).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  }

  function bankChartModel(d) {
    const ch = d.chart, asofMs = ms(ch.asof);
    const hist = ch.history.map(function (s) { return { x: ms(s.date), y: s.rate, tip: "Policy rate " + fmtRate(s.rate) + "% from " + fmtDate(s.date) + (s.lower !== null && s.lower !== undefined ? " (range " + fmtRate(s.lower) + EN + fmtRate(s.upper) + ")" : "") }; });
    const last = hist[hist.length - 1];
    if (last && last.x < asofMs) hist.push({ x: asofMs, y: last.y, tip: "" });
    const stepPts = [], windows = [], proxy = [];
    ch.market.forEach(function (p) {
      const x = ms(p.effective);
      if ((p.method === "EXACT" || p.method === "CURVE") && isNum(p.rate)) {
        stepPts.push({ x: x, y: p.rate, tip: p.method + " " + fmtRate(p.rate, 3) + "% after " + fmtDate(p.meeting) + " (" + fmtSigned(p.cum_bp, 1) + " bp vs base)" });
      } else if (p.method === "WINDOW" && isNum(p.level)) {
        const key = p.window.join("|");
        if (!windows.some(function (w) { return w.key === key; })) {
          windows.push({ key: key, x0: ms(p.window[0]), x1: ms(p.window[1]), y: p.level, bkbm: p.level_kind === "bkbm",
                         tip: (p.level_kind === "bkbm" ? "BKBM (not the OCR) " : "") + fmtRate(p.level, 3) + "% average over " + fmtRange(p.window) + " (upper bound: " + (p.interior || 0) + " other meeting(s) inside)" });
        }
      } else if (p.method === "PROXY_CURVE" || p.method === "CURVE" || p.flag === "PROXY") {
        if (isNum(p.level)) proxy.push({ x: x, y: p.level, tip: "Sovereign proxy level " + fmtRate(p.level, 3) + "% (not policy-equivalent) for the interval from " + fmtDate(p.effective) });
      }
    });
    if (stepPts.length) {
      const startX = Math.max(asofMs, last ? last.x : asofMs);
      stepPts.unshift({ x: startX, y: ch.base, tip: "Base rate " + fmtRate(ch.base) + "%" });
    }
    return { hist: hist, step: stepPts, windows: windows, proxy: proxy, asofMs: asofMs };
  }
  function bankPathModel(ch) {
    const b = ch.bank, out = { medians: [], dots: [], quarters: [] };
    if (b.kind === "dots") {
      b.years.forEach(function (y) {
        const x = Date.UTC(y.year, 11, 31);
        if (isNum(y.median)) out.medians.push({ x: x, y: y.median, tip: "FOMC median dot " + y.year + ": " + fmtRate(y.median, 3) + "%" });
        y.dots.forEach(function (dt) { out.dots.push({ x: x, y: dt.level, r: 3 + 2.2 * dt.count, tip: dt.count + " participant(s) at " + fmtRate(dt.level, 3) + "% (" + y.year + ")" }); });
      });
    } else if (b.kind === "ocr_track") {
      b.quarters.forEach(function (q) {
        const y = +q.period.slice(0, 4), qn = +q.period.slice(-1);
        const x0 = Date.UTC(y, (qn - 1) * 3, 1), x1 = Date.UTC(y, qn * 3, 1);
        out.quarters.push({ x: x0, y: q.value, tip: "OCR track " + q.period + ": " + fmtRate(q.value, 1) + "%" }, { x: x1, y: q.value, tip: "OCR track " + q.period + ": " + fmtRate(q.value, 1) + "%" });
      });
    }
    return out;
  }

  function drawBankChart(canvas, d) {
    const c = colors(), m = bankChartModel(d), bp = bankPathModel(d.chart);
    const allX = [].concat(m.hist, m.step, m.proxy, bp.medians, bp.quarters).map(function (p) { return p.x; })
      .concat(m.windows.map(function (w) { return w.x1; }));
    const xMin = m.hist.length ? m.hist[0].x : m.asofMs - 365 * DAY_MS;
    const xMax = Math.max.apply(null, allX.concat([m.asofMs])) + 30 * DAY_MS;
    const now = Date.now();
    const line = function (label, data, color, extra) {
      return Object.assign({ type: "line", label: label, data: data, borderColor: color, backgroundColor: color, stepped: "after", pointRadius: 0, pointHoverRadius: 4, borderWidth: 2.2, spanGaps: false, order: 2 }, extra || {});
    };
    const ds = [line("Policy rate", m.hist, c.fg, { borderWidth: 2.6, order: 1 })];
    if (m.step.length) ds.push(line("Market-implied path", m.step, c.accent, { pointRadius: 3.5, borderWidth: 2.4 }));
    if (m.windows.length) ds.push({ type: "line", label: "3M window (upper bound)", data: [], borderColor: withAlpha(c.accent, 0.55), backgroundColor: withAlpha(c.accent, 0.55), borderWidth: 7 });
    if (m.proxy.length) ds.push(line("Sovereign proxy (raw level, not policy-equivalent)", m.proxy, c.warn, { borderDash: [2, 5], pointRadius: 3, borderWidth: 2 }));
    if (bp.medians.length) {
      ds.push({ type: "scatter", label: "FOMC median dot", data: bp.medians, borderColor: c.bank, backgroundColor: c.bank, pointStyle: "rectRot", pointRadius: 7, order: 0 });
      ds.push({ type: "scatter", label: "Dot distribution (size = participants)", data: bp.dots, borderColor: withAlpha(c.bank, 0.7), backgroundColor: withAlpha(c.bank, 0.28), pointRadius: bp.dots.map(function (p) { return p.r; }), order: 3 });
    }
    if (bp.quarters.length) ds.push(line("RBNZ OCR track (MPS, quarterly avg)", bp.quarters, c.bank, { stepped: false, borderDash: [7, 4], borderWidth: 2.4 }));
    const annotations = {};
    d.chart.meetings.forEach(function (mt, i) {
      const x = ms(mt.decision);
      if (x > xMax) return;
      annotations["m" + i] = { type: "line", xMin: x, xMax: x, borderColor: withAlpha(c.muted, 0.35), borderWidth: 1, borderDash: [3, 4] };
    });
    if (now >= xMin && now <= xMax) {
      annotations.today = { type: "line", xMin: now, xMax: now, borderColor: c.muted, borderWidth: 1.5,
                            label: { display: true, content: "today", position: "start", backgroundColor: withAlpha(c.surface, 0.9), color: c.muted, font: { size: 10 }, padding: 3 } };
    }
    m.windows.forEach(function (w, i) {
      annotations["w" + i] = { type: "line", xMin: w.x0, xMax: w.x1, yMin: w.y, yMax: w.y, borderColor: withAlpha(w.bkbm ? c.muted : c.accent, 0.55), borderWidth: 7,
                               label: { display: true, content: w.bkbm ? "BKBM ≤" : "≤", position: "center", backgroundColor: "transparent", color: c.fg, font: { size: 11, weight: "bold" }, padding: 0 } };
    });
    const chart = new Chart(canvas, {
      data: { datasets: ds },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
        interaction: { mode: "nearest", intersect: true, axis: "xy" },
        scales: Object.assign(baseScales(c, "Policy rate, %"), { x: Object.assign(baseScales(c, "").x, { min: xMin, max: xMax }) }),
        plugins: {
          legend: { position: "bottom", labels: { color: c.fg, usePointStyle: true, boxWidth: 8, filter: function (it) { return !!it.text; } } },
          tooltip: { callbacks: { title: function () { return ""; }, label: function (ctx) { const r = ctx.raw || {}; return r.tip || (ctx.dataset.label + ": " + fmtRate(ctx.parsed.y, 3) + "%"); } } },
          annotation: { annotations: annotations }
        }
      }
    });
    state.charts.push(chart);
    return chart;
  }

  // ---- bank page ------------------------------------------------------------------------------------------------------------
  function levelNote(m) {
    if (!m || !isNum(m.level) || isNum(m.v)) return "";
    return '<div class="cb-sub">Level ' + fmtRate(m.level, 3) + "%" + DOT + esc(((state.meta || {}).level_help || {})[m.level_kind] || "") + "</div>";
  }
  function unverifiedChips(d) {
    return d.unverified.map(function (t) { return '<span class="econ-flag flag-nc" title="' + esc(t.text) + '">' + esc(t.label) + "</span>"; }).join(" ");
  }
  function projLine(n) {
    const bits = [];
    if (n.has_projections && n.projections_name) bits.push("publishes " + esc(n.projections_name));
    if (n.has_presser) bits.push("press conference" + (n.conference && n.conference.local ? " " + esc(n.conference.local) + " " + esc((n.time || {}).abbr || "") : ""));
    return bits.join(DOT);
  }
  function blackoutBadge(n) {
    const b = n && n.blackout;
    if (!b) return "";
    return '<span class="econ-flag cb-blackout" data-blackout-start="' + esc(b.start_utc) + '" data-blackout-end="' + esc(b.end_utc) + '" hidden title="' + esc("Quiet period until " + b.end + (b.precision === "approximate" || !b.verified ? " (approximate / unverified)" : "")) + '">in blackout</span>';
  }
  function headerCard(d) {
    const s = d.summary, n = s.next;
    const nextBlock = n && n.decision
      ? '<div class="cb-kicker">Next meeting</div><div class="cb-bigdate">' + esc(weekday(n.decision)) + " " + fmtDate(n.decision) + ' <span class="cb-cdbig">' + cdSpan(n) + "</span></div>" +
        '<div class="cb-sub">Effective ' + fmtDate(n.effective) + (timeText(n.time) ? DOT + esc(timeText(n.time)) : "") + (projLine(n) ? DOT + projLine(n) : "") + "</div>" +
        '<div class="cb-badges">' + blackoutBadge(n) + "</div>"
      : '<div class="cb-kicker">Next meeting</div><div class="cb-sub">' + esc((n && n.na) || "n/a") + "</div>";
    return '<section class="cb-card cb-head"><div class="cb-head-main"><div class="cb-kicker">Policy rate</div><div class="cb-bigrate">' + rateHtml(s.rate) + "</div>" +
      '<div class="cb-sub">' + esc(d.bank.policy_rate || "") + "</div>" + (d.unverified.length ? '<div class="cb-badges">' + unverifiedChips(d) + "</div>" : "") + "</div>" +
      '<div class="cb-head-next">' + nextBlock + "</div></section>";
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
  function horizonCard(d, y) {
    const hz = d.horizons[y];
    const rp = hz.repricing;
    const window = hz.window ? '<div class="cb-sub">Window ' + esc(fmtRange(hz.window)) + (isNum(hz.interior) ? DOT + hz.interior + " other meeting(s) inside" : "") + (hz.pre_days ? DOT + hz.pre_days + " day(s) of the window before the effective date" : "") + "</div>" : "";
    const head = isNum(hz.v)
      ? '<div class="cb-bigrate ' + (hz.v > 0 ? "cb-hawk" : hz.v < 0 ? "cb-dove" : "") + '">' + fmtSigned(hz.v, 1) + " bp" + flagBadge(hz.flag) + staleBadge(hz.stale) + "</div>" +
        '<div class="cb-sub">Implied ' + fmtRate(hz.rate, 3) + "% after the " + fmtDate(hz.meeting) + " meeting</div>"
      : (isNum(hz.level)
        ? '<div class="cb-bigrate"><span class="cb-lvl">' + fmtRate(hz.level, 3) + "%</span>" + flagBadge(hz.flag) + staleBadge(hz.stale) + '</div><div class="cb-sub"><b>' + esc(((state.meta || {}).level_label || {})[hz.level_kind] || "level") + "</b>" + DOT + esc(((state.meta || {}).level_help || {})[hz.level_kind] || "") + "</div>" +
          '<div class="cb-sub">No bp: ' + esc(hz.na || "n/a") + (hz.level_kind === "bkbm" && d.gap && d.gap.na ? "<br>GAP n/a: " + esc(d.gap.na) : "") + "</div>"
        : '<div class="cb-bigrate cb-na" title="' + esc(hz.na || "n/a") + '">' + NA + flagBadge(hz.flag) + '</div><div class="cb-sub">' + esc(hz.na || "n/a") + "</div>");
    const one = function (m) { return isNum(m && m.v) ? valueSpan(m, 1, " bp") : '<span class="cb-na" title="' + esc((m && m.na) || "n/a") + '">' + NA + "</span>"; };
    return '<section class="cb-card cb-horizon"><h3>End-' + y + '</h3>' + head + window +
      '<div class="cb-kv"><span>Repricing 1w</span>' + one(rp["1w"]) + "</div>" + '<div class="cb-kv"><span>Repricing 1m</span>' + one(rp["1m"]) + "</div></section>";
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
  function pathTable(d) {
    const rows = d.trajectory.map(function (p) {
      const where = p.window ? fmtDay(p.effective) + " → " + esc(fmtRange(p.window)) : p.method === "CURVE" || p.method === "PROXY_CURVE" ? fmtDay(p.effective) : fmtDay(p.effective);
      const notes = [];
      if (p.upper_bound && p.interior !== null && p.interior !== undefined) notes.push(p.interior + " interior mtg(s), " + (p.pre_days || 0) + " d pre-effective");
      const levelOnly = isNum(p.level) && !isNum(p.rate);
      if (levelOnly) notes.push(((state.meta || {}).level_help || {})[p.level_kind] || "");
      (p.notes || []).forEach(function (t) { if (notes.indexOf(t) < 0) notes.push(t); });
      if (p.na && !(p.notes || []).length) notes.push(shortNa(p.na));
      else if (p.na && !/BKBM/.test(p.na)) notes.push(shortNa(p.na));
      const rate = isNum(p.rate) ? fmtRate(p.rate, 3) : isNum(p.level) ? '<span class="cb-lvl" title="' + esc(((state.meta || {}).level_help || {})[p.level_kind] || "") + '">[' + fmtRate(p.level, 3) + "]</span>" : NA;
      const step = isNum(p.step_bp) ? fmtSigned(p.step_bp, 1) : '<span class="cb-na" title="' + esc(p.step_na || "n/a") + '">' + NA + "</span>";
      return "<tr" + (p.stale ? ' class="cb-stale"' : "") + "><td>" + fmtDate(p.meeting) + "</td><td>" + where + '</td><td class="cb-n">' + rate + "</td>" +
        numCell({ v: p.cum_bp, flag: null, stale: p.stale, na: p.na }, { scale: 100, dp: 1, noBadge: true }) + '<td class="cb-n">' + step + "</td><td>" + flagBadge(p.flag).trim() + staleBadge(p.stale) + '</td><td class="cb-notes">' + esc(shortNa(notes.filter(Boolean).join("; "))) + "</td></tr>";
    }).join("");
    return '<section class="cb-card"><h3>Implied path by meeting</h3><div class="cb-scroll"><table class="cb-table cb-mini"><thead><tr><th>Meeting</th><th>Effective / window</th><th>Rate %</th><th>Cum bp</th><th>Step bp</th><th>Method</th><th>Notes</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="7" class="muted">' + esc(d.summary.na || "no upcoming meeting") + "</td></tr>") + "</tbody></table></div></section>";
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

  function chartHelp(d) {
    const ms_ = d.chart.market, bits = ["Solid line = policy-rate history"];
    if (ms_.some(function (p) { return (p.method === "EXACT" || p.method === "CURVE") && isNum(p.rate); })) bits.push("blue = market-implied path (steps on the effective dates)");
    if (ms_.some(function (p) { return p.method === "WINDOW" && isNum(p.level); })) bits.push("bars marked \u2264 = 3M windows (upper bounds; BKBM = not the OCR)");
    if (ms_.some(function (p) { return isNum(p.level) && p.level_kind === "sovereign_proxy"; })) bits.push("dotted = sovereign proxy, not policy-equivalent");
    if (d.chart.bank.kind === "dots") bits.push("diamonds / circles = FOMC median dot / dot distribution (size = participants)");
    if (d.chart.bank.kind === "ocr_track") bits.push("purple dashed = RBNZ OCR track (quarterly averages)");
    bits.push("vertical dashes = upcoming meetings");
    return bits.join("; ") + "." + (ms_.length ? "" : " " + (d.summary.na || "No market path for this currency."));
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

  let themeWatched = false;
  function renderBank(d) {
    destroyCharts();
    state.redraw = [];
    state.meta = d.meta;
    SUMMARIES = (d.documents && d.documents.summaries) || {};
    titleEl.innerHTML = esc(d.ccy) + DOT + esc(d.bank.short) + ' <span class="cb-title-sub">' + esc(d.bank.name) + "</span>";
    document.title = d.ccy + " " + d.bank.short + " | Central Banks | Dashboard";
    const anyStale = d.summary.stale || d.sources.some(function (s) { return s.stale; });
    metaEl.innerHTML = '<a href="' + esc(CFG.urls.overview_page) + '">' + "← All banks</a>" + DOT + "As of " + esc(fmtDate(d.meta.asof)) + (anyStale ? staleBadge(true, "at least one source is older than " + d.meta.stale_after_bd + " business days") : "");
    const n = d.summary.next;
    const dayMode = !!(n && n.decision && todayIn((n.time || {}).tz || "UTC") === n.decision);
    if (isPhone()) {
      titleEl.innerHTML = esc(d.ccy) + DOT + esc(d.bank.short) + ' <span class="cb-title-sub">' + esc(d.bank.name) + DOT + "as of " + esc(fmtDay(d.meta.asof)) + (anyStale ? staleBadge(true, "at least one source is older than " + d.meta.stale_after_bd + " business days") : "") + "</span>";
      metaEl.innerHTML = '<a class="cb-back" href="' + esc(CFG.urls.overview_page) + '">' + '<svg class="ph-ico" width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>All banks</a>';
      renderBankPhone(d, dayMode);
      if (!themeWatched) { themeWatched = true; watchTheme(); }
      tick();
      return;
    }
    root.innerHTML = (dayMode ? nextCard(d, true) + latestDecisionCard(d, true) : "") + headerCard(d) +
      '<section class="cb-card cb-chartcard"><h3>Policy-rate trajectory <small class="muted">market-implied vs history' + (d.chart.bank.kind !== "n/a" ? " and the bank’s own path" : "") + '</small></h3><div class="cb-chart-wrap"><canvas id="cbChart"></canvas></div>' +
      '<div class="cb-sub">' + esc(chartHelp(d)) + "</div></section>" +
      '<div class="cb-grid">' + (dayMode ? "" : nextCard(d, false)) + horizonCard(d, "2026") + horizonCard(d, "2027") + "</div>" + gapCard(d) + pathTable(d) + (dayMode ? "" : latestDecisionCard(d, false)) + decisionsCard(d) + documentsCard(d) + calendarCard(d) + footerCard(d) + methodPanel(d);
    wireRedline(d);
    const canvas = document.getElementById("cbChart");
    registerRedraw(function () { drawBankChart(canvas, d); });
    if (!themeWatched) { themeWatched = true; watchTheme(); }
    document.querySelectorAll("[data-open-method]").forEach(function (a) { a.addEventListener("click", function () { document.getElementById("cbMethod").open = true; }); });
    tick();
  }

  // ---- phone (Faza 11C), max-width 600px ------------------------------------------------------------------------------------
  // Overview: a list next to each desktop table (CSS shows one). Bank page: rendered either for the desktop or for the phone and
  // re-rendered when the viewport crosses 600px (one chart canvas, one id).
  function isPhone() { return !!(window.PhoneUI && window.PhoneUI.isPhone()); }
  const CHEV = '<svg class="ph-ico ph-chev" width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
  const CHEV_DOWN = '<svg class="ph-ico" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
  function methodLabel(flag) {
    if (!flag) return "";
    const f = ((state.meta || {}).flags || {})[flag] || {};
    return '<span class="cb-ph-m cb-ph-m-' + esc(flag) + '">' + esc(String(f.label || flag).toLowerCase()) + "</span>";
  }
  function dominantProb(h) {
    const list = h.probabilities || [];
    if (!list.length) return "";
    const top = list.slice().sort(function (a, b) { return b.p - a.p; })[0];
    return Math.round(top.p * 100) + "% " + (top.moves === 0 ? "hold" : (h.direction === "cut" ? MINUS : "+") + 25 * top.moves);
  }
  function daysTo(n) {                                                   // whole days to next.time.utc; BoJ (no time): the date
    const utc = n.time && n.time.utc;
    const d = utc ? Math.floor((Date.parse(utc) - Date.now()) / DAY_MS) : daysBetween(todayIn((n.time || {}).tz || "UTC"), n.decision);
    return d > 0 ? "in " + d + " d" : d === 0 ? "today" : "announced";
  }
  function shortReason(h) {
    if (h.flag === "PROXY" || /^PROXY/.test(h.method || "")) return "proxy only";
    const t = shortNa(h.na || "n/a");
    if (/no market path/i.test(t)) return "no market path";
    return t.split(/[|:(;]/)[0].trim();
  }
  function rateText(r) {
    if (!r || !isNum(r.value)) return NA;
    return (r.range && isNum(r.lower) ? fmtRate(r.lower) + EN + fmtRate(r.upper) : fmtRate(r.value)) + "%";
  }
  function rateNote(r) {
    if (!r) return "";
    if (r.pending) return '<span class="cb-ph-warn">from ' + fmtDay(r.from) + "</span>";
    if (r.decision_status && r.decision_status !== "official" && r.decision_status !== "bis") return '<span class="cb-ph-warn">unconfirmed</span>';
    return "";
  }
  function pricedIn(r) {
    const h = r.horizon || {};
    const v = h.kind === "step" ? h.step_bp : h.kind === "window" ? h.cum_bp : null;
    if (isNum(v)) {
      const sub = [methodLabel(h.flag), h.kind === "window" ? h.n_meetings + " mtg" + (h.n_meetings === 1 ? "" : "s") : esc(dominantProb(h)), h.stale ? "stale" : ""].filter(Boolean).join(DOT);
      const num = h.stale ? '<span class="cb-ph-num cb-ph-stale">' + fmtSigned(v, 1) + "</span>"
        : '<span class="cb-ph-chip"' + tint(v, 25) + ">" + fmtSigned(v, 1) + "</span>";
      return num + '<span class="cb-ph-sub">' + sub + "</span>";
    }
    if (isNum(h.level) && h.level_kind === "bkbm") {
      return '<span class="cb-ph-num">' + fmtRate(h.level, 2) + '%</span><span class="cb-ph-sub">' + methodLabel(h.flag) + DOT + "BKBM level</span>";
    }
    return '<span class="cb-ph-num cb-ph-na">' + NA + '</span><span class="cb-ph-sub">' + esc(shortReason(h)) + "</span>";
  }
  function bankList(ov) {
    const rows = ov.banks.map(function (r) {
      const n = r.next, m = r.repricing["1w"].years["2026"];
      const next = n && n.decision ? '<b>' + fmtDay(n.decision) + '</b><span class="cb-ph-sub">' + daysTo(n) + "</span>" : '<span class="cb-ph-na">' + NA + "</span>";
      return '<a class="ph-row cb-ph-grid" href="' + esc(r.href) + '">' +
        '<span class="cb-ph-bank"><span><b>' + esc(r.ccy) + '</b> <span class="cb-ph-short">' + esc(r.bank.short) + '</span></span><span class="cb-ph-rate">' + rateText(r.rate) + "</span>" + rateNote(r.rate) + "</span>" +
        '<span class="cb-ph-next">' + next + "</span>" +
        '<span class="cb-ph-priced">' + pricedIn(r) + "</span>" +
        '<span class="cb-ph-1w">' + (isNum(m.v) ? fmtSigned(m.v, 1) : '<span class="cb-ph-na">' + NA + "</span>") + "</span>" + CHEV + "</a>";
    }).join("");
    return '<div class="phone-only"><div class="ph-list cb-ph-list"><div class="ph-list-head cb-ph-grid"><span>Bank · rate</span><span>Next</span><span class="cb-ph-r">Priced in</span><span class="cb-ph-r">1W</span><span></span></div>' + rows + "</div>" +
      '<p class="ph-note">Tap a bank for its page: the path by meeting, decisions, documents and speeches.</p></div>';
  }
  function pairList(pj) {
    const cell = function (m) {
      if (!m || !isNum(m.v)) return '<span class="cb-ph-na" title="' + esc((m && m.na) || "n/a") + '">' + NA + "</span>";
      return "<b" + (m.stale ? ' class="cb-ph-stale"' : "") + ">" + fmtSigned(m.v, 1) + "</b>" + (m.flag ? '<span class="cb-ph-sub">' + methodLabel(m.flag) + "</span>" : "");
    };
    const rows = pj.pairs.map(function (p) {
      return '<a class="ph-row cb-ph-pgrid" href="' + esc(p.href) + '"><span class="cb-ph-bank"><b>' + esc(p.display) + '</b><span class="cb-ph-sub">' + esc(p.base) + " " + MINUS + " " + esc(p.quote) + "</span></span>" +
        '<span class="cb-ph-r">' + cell(p.current) + '</span><span class="cb-ph-r">' + cell(p.implied["2026"].diff_bp) + '</span><span class="cb-ph-r">' + cell(p.implied["2027"].diff_bp) + "</span>" + CHEV + "</a>";
    }).join("");
    return '<div class="phone-only"><div class="ph-list cb-ph-list"><div class="ph-list-head cb-ph-pgrid"><span>Pair</span><span class="cb-ph-r">Now</span><span class="cb-ph-r">End-2026</span><span class="cb-ph-r">End-2027</span><span></span></div>' + rows + "</div>" +
      '<p class="ph-note">Differentials in bp, base minus quote. Tap a pair for its page: the paths, the differential and repricing.</p></div>';
  }

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
  function phoneTop(d) {
    const s = d.summary, n = s.next, h = s.horizon || {}, last = d.decisions[0];
    const cell = function (k, big, sub, cls) { return '<div class="cb-ph-kpi"><div class="cb-kicker">' + k + '</div><div class="cb-ph-big' + (cls ? " " + cls : "") + '">' + big + '</div><div class="cb-ph-sub">' + sub + "</div></div>"; };
    const rate = cell("Policy rate", esc(rateText(s.rate).replace("%", "")), esc(d.bank.policy_rate || "%") + (rateNote(s.rate) ? DOT + rateNote(s.rate) : ""));
    const next = n && n.decision ? cell("Next meeting", esc(weekday(n.decision)) + " " + fmtDay(n.decision), daysTo(n) + (timeText(n.time) ? DOT + esc(timeText(n.time)) : "")) : cell("Next meeting", NA, esc((n && n.na) || "n/a"));
    const v = h.kind === "step" ? h.step_bp : h.kind === "window" ? h.cum_bp : null;
    const priced = isNum(v)
      ? cell("Priced in by then", fmtSigned(v, 1) + " bp", [methodLabel(h.flag), h.kind === "window" ? h.n_meetings + " meeting" + (h.n_meetings === 1 ? "" : "s") : esc(dominantProb(h)), h.stale ? "stale" : ""].filter(Boolean).join(DOT),
             h.stale ? "cb-ph-stale" : v > 0 ? "cb-hawk" : v < 0 ? "cb-dove" : "")
      : isNum(h.level) && h.level_kind === "bkbm" ? cell("Priced in by then", fmtRate(h.level, 2) + "%", methodLabel(h.flag) + DOT + "BKBM level")
      : cell("Priced in by then", NA, esc(shortReason(h)), "cb-ph-na");
    const sp = last && isNum(last.surprise_consensus_bp) ? (Number(last.surprise_consensus_bp) === 0 ? "no surprise" : "surprise " + fmtSigned(last.surprise_consensus_bp, 1) + " bp") : "no consensus";
    const lastC = last ? cell("Last decision", fmtSigned(last.delta_bp, 0) + " bp", fmtDay(last.date) + DOT + sp, last.delta_bp > 0 ? "cb-hawk" : last.delta_bp < 0 ? "cb-dove" : "") : cell("Last decision", NA, "no decision on record");
    return '<section class="cb-ph-top">' + rate + next + priced + lastC + "</section>";
  }
  function renderBankPhone(d, dayMode) {
    const doc = d.documents, L = doc && doc.latest;
    const staleSrc = d.sources.filter(function (x) { return x.stale; })[0];
    const pathRows = d.trajectory.map(function (p) {
      const rate = isNum(p.rate) ? fmtRate(p.rate, 3) : isNum(p.level) ? "[" + fmtRate(p.level, 3) + "]" : NA;
      return "<tr" + (p.stale ? ' class="cb-stale"' : "") + "><td>" + fmtDate(p.meeting) + '</td><td class="cb-n">' + rate + '</td><td class="cb-n">' + (isNum(p.cum_bp) ? fmtSigned(p.cum_bp, 1) : NA) + "</td></tr>";
    });
    const pathNote = [staleSrc ? "Grey: the source is from " + fmtDay(staleSrc.asof) + (isNum(staleSrc.lag_bd) ? ", " + staleSrc.lag_bd + " business days old." : ".") : "",
                      d.trajectory.some(function (p) { return p.upper_bound; }) ? "Upper bounds from 3M windows." : ""].filter(Boolean).join(" ");
    const kinds = ["history"].concat(d.chart.market.length ? ["market path"] : []).concat(d.chart.bank.kind === "dots" ? ["FOMC dots"] : d.chart.bank.kind === "ocr_track" ? ["OCR track"] : []);
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
      const fu = function (types) { return t.follow_up.filter(function (i) { return types.indexOf(i.type) >= 0 && i.url; }).map(function (i) { return docAnchor(i, i.label); }).join("<br>") || NA; };
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
    const horizons = ["2026", "2027"].map(function (y) {
      const hz = d.horizons[y];
      return "<tr><td>End-" + y + '</td><td class="cb-n">' + (isNum(hz.v) ? fmtSigned(hz.v, 1) + " bp" : isNum(hz.level) ? fmtRate(hz.level, 3) + "%" : NA) + '</td><td class="cb-n">' +
        (isNum(hz.repricing["1w"].v) ? fmtSigned(hz.repricing["1w"].v, 1) : NA) + '</td><td class="cb-n">' + (isNum(hz.repricing["1m"].v) ? fmtSigned(hz.repricing["1m"].v, 1) : NA) + "</td></tr>";
    });
    root.innerHTML = phoneTop(d) + '<div class="cb-accs">' +
      acc("cbAccChart", "Policy-rate trajectory", "chart: " + kinds.join(", "), '<div class="cb-chart-wrap"><canvas id="cbChart"></canvas></div><div class="cb-sub">' + esc(chartHelp(d)) + "</div>", false) +
      acc("cbAccPath", "Implied path by meeting", d.trajectory.length + " meeting" + (d.trajectory.length === 1 ? "" : "s"), miniTable(["Meeting", "Rate %", "Total, bp"], pathRows, d.summary.na || "no upcoming meeting") + (pathNote ? '<div class="cb-sub">' + esc(pathNote) + "</div>" : ""), true) +
      acc("cbAccHorizon", "End-2026 and end-2027", "implied change and repricing", miniTable(["Horizon", "Implied", "1w", "1m"], horizons), false) +
      acc("cbAccGap", gapTitle, gapSub, gapBody, false) +
      latest +
      acc("cbAccDecisions", "Last decisions", d.decisions.length + " on record", miniTable(["Date", "Δ bp", "Rate after %"], decRows), false) +
      (doc ? acc("cbAccDocs", "Documents", "last four meetings", miniTable(["Meeting", "Statement", "Minutes", "Press conf."], docRows), false) +
             acc("cbAccSpeeches", "Speeches and testimony", "last 60 days", miniTable(["Date", "Speaker", "Title"], spRows, "no speeches collected in the last 60 days"), false) : "") +
      acc("cbAccCalendar", "Calendar and blackout", "", miniTable(["Decision", "Time", "Blackout"], calRows, "no upcoming meetings on record"), false) +
      acc("cbAccSources", "Sources", "", miniTable(["Source", "Role", "As-of"], srcRows, "no market source for this currency"), false) +
      acc("cbAccMethod", "How is this calculated?", "", methodPanel(d).replace(/^<details[^>]*><summary>[^<]*<\/summary>/, "").replace(/<\/details>$/, ""), false) +
      "</div>" + '<p class="ph-note">Every section opens in place; the chart and the tables keep their desktop content, with fewer columns.</p>';
    const canvas = document.getElementById("cbChart");
    let drawn = false;
    root.querySelectorAll(".cb-acc-head").forEach(function (b) {
      b.addEventListener("click", function () {
        const open = b.getAttribute("aria-expanded") !== "true";
        b.setAttribute("aria-expanded", String(open));
        document.getElementById(b.getAttribute("aria-controls")).hidden = !open;
        if (open && b.parentNode.id === "cbAccChart" && !drawn) { drawn = true; registerRedraw(function () { drawBankChart(canvas, d); }); }
      });
    });
    wireRedline(d);
  }

  // ---- pair page ------------------------------------------------------------------------------------------------------------
  function ratePath(d) {
    const m = bankChartModel(d);
    const pts = m.hist.map(function (p) { return { x: p.x, y: p.y, mkt: false }; });
    const last = pts.length ? pts[pts.length - 1].x : m.asofMs;
    let lastX = m.asofMs;
    d.chart.market.forEach(function (p) {
      if ((p.method === "EXACT" || p.method === "CURVE" || p.method === "WINDOW") && isNum(p.rate) && p.level_kind === "policy") {
        pts.push({ x: ms(p.effective), y: p.rate, mkt: true, upper: p.method === "WINDOW" });
        lastX = Math.max(lastX, ms(p.effective));
      }
    });
    pts.sort(function (a, b) { return a.x - b.x; });
    const hasMarket = pts.some(function (p) { return p.mkt; });
    return { pts: pts, lastX: hasMarket ? lastX : m.asofMs, hasMarket: hasMarket, asofMs: m.asofMs };
  }
  function stepVal(pts, x) {
    let v = null;
    for (let i = 0; i < pts.length; i++) { if (pts[i].x <= x) v = pts[i].y; else break; }
    return v;
  }
  function pairMetricRow(label, m, o) {
    o = o || {};
    if (!m || !isNum(m.v)) return "<tr><td>" + esc(label) + '</td><td class="econ-cell cell-na cb-na">' + NA + '</td><td class="cb-notes" title="' + esc((m && m.na) || "n/a") + '">' + esc(shortNa((m && m.na) || "n/a")) + "</td></tr>";
    return "<tr><td>" + esc(label) + "</td>" + numCell(m, { scale: o.scale || 100, dp: 1, unit: " bp" }) + '<td class="cb-notes">' + esc(o.note || "") + "</td></tr>";
  }
  function renderPair(pj, p, b, q) {
    state.meta = pj.meta;
    titleEl.innerHTML = esc(p.display) + ' <span class="cb-title-sub">' + esc(p.base) + " " + MINUS + " " + esc(p.quote) + "</span>";
    document.title = p.display + " | Central Banks | Dashboard";
    metaEl.innerHTML = '<a href="' + esc(CFG.urls.overview_page) + '#pairs">← All pairs</a>' + DOT + "As of " + esc(fmtDate(pj.meta.asof)) + DOT + '<a href="' + esc(pj.banks[p.base].href) + '">' + esc(p.base) + " page</a>" + DOT + '<a href="' + esc(pj.banks[p.quote].href) + '">' + esc(p.quote) + " page</a>";
    const rows = [];
    rows.push(pairMetricRow("Now: " + p.base + " rate " + MINUS + " " + p.quote + " rate (carry)", p.current, { scale: 300, note: fmtRateAuto(b.summary.rate.value) + "% " + MINUS + " " + fmtRateAuto(q.summary.rate.value) + "%" }));
    ["2026", "2027"].forEach(function (y) {
      const m = p.implied[y];
      rows.push(pairMetricRow("End-" + y + ": implied differential", m.diff_bp, { scale: 200, note: isNum(m.diff_pp) ? fmtSigned(m.diff_pp * 100, 1) + " bp = " + fmtRate(m.diff_pp, 3) + " pp" : "" }));
      rows.push(pairMetricRow("End-" + y + ": change vs today (cumulative bp differential)", m.cum_bp, { scale: 100 }));
    });
    ["1w", "1m"].forEach(function (w) {
      ["2026", "2027"].forEach(function (y) { rows.push(pairMetricRow("Repricing " + w + ", end-" + y + " differential", p.repricing[w][y], { scale: 25, note: w === "1w" ? "5 business days" : "21 business days" })); });
    });
    const flagLine = p.flag ? '<div class="cb-sub">Weakest method across the two legs: ' + flagBadge(p.flag) + "</div>" : '<div class="cb-sub">No implied metric is available for this pair.</div>';
    root.innerHTML = '<section class="cb-card cb-chartcard"><h3>Policy-rate paths <small class="muted">' + esc(p.base) + " and " + esc(p.quote) + '</small></h3><div class="cb-chart-wrap"><canvas id="cbPairChart"></canvas></div>' +
      '<div class="cb-sub" id="cbPairNote"></div></section>' +
      '<section class="cb-card cb-chartcard"><h3>Differential ' + esc(p.base) + " " + MINUS + " " + esc(p.quote) + ' <small class="muted">bp</small></h3><div class="cb-chart-wrap cb-chart-short"><canvas id="cbDiffChart"></canvas></div>' +
      '<div class="cb-sub">Drawn only where both legs have a policy-equivalent path; dashed = market-implied.</div></section>' +
      '<section class="cb-card"><h3>' + esc(p.display) + " metrics</h3>" + flagLine + '<div class="cb-scroll"><table class="cb-table cb-mini cb-pairtable"><thead><tr><th>Metric</th><th>Value</th><th>Detail</th></tr></thead><tbody>' + rows.join("") + "</tbody></table></div>" +
      '<div class="cb-sub">Each metric is n/a on its own: differentials of level need both legs policy-equivalent, repricing needs a change of level on both legs. The flag is the weaker of the two legs.</div></section>' +
      '<section class="cb-card cb-method-link"><a href="' + esc(pj.banks[p.base].href) + '#cbMethod">How is this calculated?</a></section>';
    const pb = ratePath(b), pq = ratePath(q);
    const notes = [];
    [[p.base, pb, b], [p.quote, pq, q]].forEach(function (t) { if (!t[1].hasMarket) notes.push(t[0] + ": no policy-equivalent market path (" + shortNa((t[2].summary.horizon && t[2].summary.horizon.na) || "n/a") + ") - history only."); });
    document.getElementById("cbPairNote").textContent = notes.join(" ");
    const pairCanvas = document.getElementById("cbPairChart"), diffCanvas = document.getElementById("cbDiffChart");
    registerRedraw(function () {
      const c = colors();
      const xMin = Math.min(pb.pts.length ? pb.pts[0].x : pb.asofMs, pq.pts.length ? pq.pts[0].x : pq.asofMs);
      const xMax = Math.max(pb.lastX, pq.lastX) + 30 * DAY_MS;
      const seg = function (color) { return { borderDash: function (ctx) { return ctx.p1.raw && ctx.p1.raw.mkt ? [6, 4] : undefined; } }; };
      const mk = function (label, path, color) {
        return { type: "line", label: label, data: path.pts, borderColor: color, backgroundColor: color, stepped: "after", pointRadius: function (ctx) { return ctx.raw && ctx.raw.mkt ? 3 : 0; }, borderWidth: 2.4, segment: seg(color) };
      };
      state.charts.push(new Chart(pairCanvas, {
        data: { datasets: [mk(p.base + " (" + b.bank.short + ")", pb, c.accent), mk(p.quote + " (" + q.bank.short + ")", pq, c.warn)] },
        options: { responsive: true, maintainAspectRatio: false, animation: false, parsing: false, interaction: { mode: "nearest", intersect: false, axis: "x" },
                   scales: Object.assign(baseScales(c, "Policy rate, %"), { x: Object.assign(baseScales(c, "").x, { min: xMin, max: xMax }) }),
                   plugins: { legend: { position: "bottom", labels: { color: c.fg, usePointStyle: true, boxWidth: 8 } },
                              tooltip: { callbacks: { title: function (items) { return items.length ? fmtDate(new Date(items[0].parsed.x).toISOString()) : ""; },
                                                     label: function (ctx) { const r = ctx.raw; return ctx.dataset.label + ": " + fmtRate(ctx.parsed.y, 3) + "%" + (r.mkt ? (r.upper ? " (3M window, upper bound)" : " (market-implied)") : ""); } } } } }
      }));
      const end = Math.min(pb.lastX, pq.lastX);
      const xs = Array.from(new Set(pb.pts.map(function (a) { return a.x; }).concat(pq.pts.map(function (a) { return a.x; })))).filter(function (x) { return x <= end; }).sort(function (a, b2) { return a - b2; });
      const diff = [];
      xs.forEach(function (x) {
        const yb = stepVal(pb.pts, x), yq = stepVal(pq.pts, x);
        if (yb !== null && yq !== null) diff.push({ x: x, y: (yb - yq) * 100, mkt: x > pb.asofMs });
      });
      if (diff.length && diff[diff.length - 1].x < end) diff.push({ x: end, y: diff[diff.length - 1].y, mkt: true });
      const annotations = {};
      const now = Date.now();
      if (now >= xMin && now <= xMax) annotations.today = { type: "line", xMin: now, xMax: now, borderColor: c.muted, borderWidth: 1.5, label: { display: true, content: "today", position: "start", backgroundColor: withAlpha(c.surface, 0.9), color: c.muted, font: { size: 10 }, padding: 3 } };
      state.charts.push(new Chart(diffCanvas, {
        data: { datasets: [{ type: "line", label: p.display + " differential (bp)", data: diff, borderColor: c.fg, backgroundColor: c.fg, stepped: "after", pointRadius: 0, borderWidth: 2.4, segment: seg(c.fg) }] },
        options: { responsive: true, maintainAspectRatio: false, animation: false, parsing: false, interaction: { mode: "nearest", intersect: false, axis: "x" },
                   scales: Object.assign(baseScales(c, "bp"), { x: Object.assign(baseScales(c, "").x, { min: xMin, max: xMax }) }),
                   plugins: { legend: { display: false }, annotation: { annotations: annotations },
                              tooltip: { callbacks: { title: function (items) { return items.length ? fmtDate(new Date(items[0].parsed.x).toISOString()) : ""; }, label: function (ctx) { return fmtSigned(ctx.parsed.y, 1) + " bp"; } } } } }
      }));
    });
    watchTheme();
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
    if (CFG.page !== "overview" && !window.Chart) { root.innerHTML = failHtml(new Error("Chart.js missing")); return; }
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
