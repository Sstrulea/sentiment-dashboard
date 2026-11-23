// stance-ui-v6 on the real static/cb.js in plain Node: the formats, the segments (data-k / data-f / data-v), the three Why rows without
// a conclusion, n/a with the reason (NZD, CHF), the notes, the sorting, the phone cards, the bank zone. The banks come from a v6 payload
// rendered by `python -m src.cb_render` (tests/cb_js/fixtures/stance_v6_2026-11-20.json = the overview stance rendered at 2026-11-20, checked by tests/test_cb_stance_v6.py).
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "cb.js"), "utf8");
function cut(from, to) { const a = src.indexOf(from), b = src.indexOf(to, a); if (a < 0 || b < 0) throw new Error("cut " + from); return src.slice(a, b); }
const ctx = { CFG: { urls: { overview_page: "/central-banks.html" } }, SP: undefined, root: null, metaEl: null, titleEl: null, window: {},
              document: { body: { classList: { contains: () => false } }, querySelectorAll: () => [], addEventListener() {} },
              getComputedStyle: () => ({ getPropertyValue: () => "" }), setInterval() {} };
vm.createContext(ctx);
vm.runInContext(cut("  const MINUS =", "  // ---- boot ---") + ";this.ST_FMT = ST_FMT; this.stBar = stBar; this.stanceRows = stanceRows; this.stanceTableHtml = stanceTableHtml;" +
                "this.stanceListHtml = stanceListHtml; this.stanceZoneHtml = stanceZoneHtml; this.whyCell = whyCell; this.stSegs = stSegs;" +
                "this.state = state;", ctx);
let failures = 0;
const eq = (g, w, what) => { if (g !== w) { failures++; console.error("FAIL " + what + ": got " + JSON.stringify(g) + ", want " + JSON.stringify(w)); } };
const ok = (c, what) => { if (!c) { failures++; console.error("FAIL " + what); } };
const strip = (h) => h.replace(/<[^>]+>/g, "").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">");

// formats
const F = ctx.ST_FMT;
eq(F.pp(0.18), "+0.18", "pp"); eq(F.pp(-0.53), "−0.53", "pp minus"); eq(F.pp(0), "0.00", "pp zero");
eq(F.bp(29.6), "+29.6", "bp"); eq(F.bp(-11.441856419561436), "−11.4", "bp minus"); eq(F.d2(-0.1), "−0.10", "d2"); eq(F.d2(1.38), "1.38", "d2 positive");
eq(F.r3(4.421), "4.421", "r3"); eq(F.r3(4.48), "4.480", "r3 pads"); eq(F.pub(-1.4), "−1.4", "pub"); eq(F.pub(3), "3", "pub integer"); eq(F.pub(0), "0", "pub zero");
ok(ctx.stBar(1, 2, "pos").indexOf("left:50%;width:25.00%") > 0, "+1 pp on a 2 pp scale: half of one side");
ok(ctx.stBar(-3, 2, "need").indexOf("left:0.00%;width:50.00%") > 0, "capped at -2 pp");

// the real payload
const fix = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "stance_v6_2026-11-20.json"), "utf8"));
ctx.state.meta = { flags: { ESTIMATE: { label: "ESTIMATE", help: "h" } } };
const banks = fix.banks, st = { scale_pp: 2, banks: banks };

// segments: every number carries data-k / data-f / data-v, the value at its key
const usd = banks.USD;
const wh = ctx.whyCell(usd);
const nums = [...wh.matchAll(/data-k="([^"]+)" data-f="([^"]+)" data-v="([^"]+)">([^<]+)</g)];
ok(nums.length >= 10, "numbers in Why carry data-k (" + nums.length + ")");
nums.forEach((m) => { const v = m[1].split(".").reduce((o, k) => o && o[k], usd); eq(Number(m[3]), v, "data-v = payload " + m[1]); eq(m[4], F[m[2]](v), "text = format " + m[1]); });

// Why: three rows with their labels, no conclusion
const t = strip(wh);
eq((wh.match(/class="cb-v6-why"/g) || []).length, 3, "three Why rows");
ok(t.indexOf("Taylor rule") === 0 && t.indexOf("Bank") > 0 && t.indexOf("Market") > t.indexOf("Bank"), "labels in order");
ok(wh.indexOf('<div class="cb-v6-reading">Tightening pressure, sensitive to the neutral range</div>') > 0, "the reading in bold");
ok(t.indexOf("today +1.08 (+0.78 to +1.78): Core PCE 3.1% (2026-09), output gap 0.2") > 0, "Taylor today");
ok(t.indexOf("Dots (SEP 16 Sep 2026): 2026 4.125%, 2027 4.125%") > 0 && t.indexOf("Last decision 28 Oct: vote 10–2 (2 for a hike)") > 0, "Bank row");
ok(t.indexOf("9 Dec: +23.0 bp, 90.8% hike · 27 Jan: +39.8 bp, 67.2% hike") > 0, "Market row: the By meeting numbers, probText");
ok(t.indexOf("vs dots, end-2027: market 4.421% vs 4.125% = +29.6 bp") > 0, "Market row: the lens");
ok(wh.indexOf('title="3.2 + 1.5 × (3.1 − 2) + 0.5 × 0.2 − 3.875 = +1.08; neutral 2.9 to 3.9 → +0.78 to +1.78"') > 0, "formula on hover");
["likely", "Likely", "cb-v5-concl", "cb-v5-move", "cb-v5-hold", "More hikes", "on hold"].forEach((w) => ok(ctx.stanceTableHtml(st, "need").indexOf(w) < 0, "no conclusion: " + w));

// n/a with the reason
const tab = ctx.stanceTableHtml(st, "need");
ok(strip(ctx.whyCell(banks.NZD)).indexOf("n/a: RBNZ neutral due 9 Dec 2026") > 0, "NZD Taylor n/a with the reason");
ok(strip(ctx.whyCell(banks.CHF)).indexOf("n/a: SNB publishes no neutral") > 0, "CHF Taylor n/a with the reason");
ok(tab.indexOf('data-na="position">SNB publishes no neutral<') > 0 && tab.indexOf('data-na="position">RBNZ neutral due 9 Dec 2026<') > 0, "Position n/a with the reason");
ok(strip(ctx.whyCell(banks.NZD)).indexOf("≈ +30.4 bp ESTIMATE") > 0, "ESTIMATE: ≈ and the badge, no probability");
ok(strip(ctx.whyCell(banks.CAD)).indexOf("data 13 Nov Montréal Exchange stale") > 0, "CAD stale badge");

// column subheads
ok(tab.indexOf("real rate − real neutral, with the bank's forecast") > 0 && tab.indexOf("bank's inflation forecast − target") > 0 &&
   tab.indexOf("Taylor rule, bank path, market pricing") > 0, "column subheads");
eq((tab.match(/<tr class="cb-row"/g) || []).length, 8, "one row per bank, 4 columns");
eq((tab.match(/<th>/g) || []).length, 4, "Bank | Position | Need | Why");

// notes
const eur = JSON.parse(JSON.stringify(banks.EUR));
ok(strip(ctx.stanceTableHtml({ scale_pp: 2, banks: { EUR: eur } }, "need")).indexOf("(from Feb 2025)") > 0, "stale neutral note under Position");
eur.need.lines.push({ segs: ["Newer ECB forecast out on 17 Dec 2026, not yet entered."], cls: "note", note: "need_forecast" });
eur.need.lines[2].segs.push(" ", { note: "update overdue", name: "need" });
const hn = ctx.stanceTableHtml({ scale_pp: 2, banks: { EUR: eur } }, "need");
ok(hn.indexOf('data-note="need_forecast"') > 0 && hn.indexOf('data-note="need">update overdue<') > 0, "Need notes: newer forecast, overdue core");
ok(hn.indexOf("cb-v6-note") > 0, "notes are grey");

// sorting
eq(ctx.stanceRows(st, "need").map((s) => s.bank).join(","), "GBP,JPY,EUR,CAD,USD,AUD,NZD,CHF", "Need, descending (default)");
eq(ctx.stanceRows(st, "position").map((s) => s.bank).join(","), "AUD,USD,EUR,CAD,GBP,JPY,NZD,CHF", "Position, n/a last");
eq(ctx.stanceRows(st, "bank").map((s) => s.bank).join(","), "USD,EUR,GBP,JPY,CAD,AUD,NZD,CHF", "Bank order");

// phone cards: Why under Position / Need
const ph = ctx.stanceListHtml(st, "need");
eq((ph.match(/cb-v5-card-why/g) || []).length, 8, "phone: Why under each card");
ok(ph.indexOf("cb-v5-card-grid") < ph.indexOf("cb-v5-card-why"), "Why after Position / Need");
eq((ph.match(/class="cb-v6-why"/g) || []).length, 24, "phone: three Why rows per card");

// bank zone
const z = ctx.stanceZoneHtml({ stance: Object.assign(JSON.parse(JSON.stringify(usd)), { scale_pp: 2, mandate: { target: "2%" }, next_test: { date: null }, history: [{ date: "2026-11-15" }] }) });
ok(z.indexOf("Stance and need") > 0 && z.indexOf("Mandate") > 0 && z.indexOf("History starts on 15 Nov 2026") > 0 && z.indexOf("data-st-next") < 0, "bank zone");
ok(z.indexOf("How to read it") > 0 && (z.match(/class="cb-v6-why"/g) || []).length === 3, "bank zone: Why rows and How to read link");
if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("stance v6: all checks passed");
