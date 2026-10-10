// Bank page v3 on the real static/cb.js in plain Node: the speeches filter (monetary only, at most 5, "Show all (N)"), the projections
// block only for banks with their own path (Fed dots, RBNZ OCR track), the Decision history columns, the projections label of "By meeting".
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "cb.js"), "utf8");

function cut(from, to) {
  const a = src.indexOf(from), b = src.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error("not found in cb.js: " + from + " .. " + to);
  return src.slice(a, b);
}
const ctx = { CFG: { urls: { overview_page: "/central-banks.html" } }, SP: undefined, root: null, metaEl: null, titleEl: null, window: {},
              document: { body: { classList: { contains: () => false } }, querySelectorAll: () => [], addEventListener() {} },
              getComputedStyle: () => ({ getPropertyValue: () => "" }), setInterval() {} };
vm.createContext(ctx);
vm.runInContext(cut("  const MINUS =", "  // ---- boot ---") +
                ";this.state = state; this.speechSplit = speechSplit; this.speechesBlock = speechesBlock; this.projectionsBlock = projectionsBlock; this.decisionHistoryRows = decisionHistoryRows;" +
                "this.decisionHistoryBlock = decisionHistoryBlock; this.HISTORY_COLS = HISTORY_COLS; this.pathTableHtml = pathTableHtml; this.projLabel = projLabel; this.meetingInfo = meetingInfo;" +
                "this.latestDecisionBlock = latestDecisionBlock; this.referencePanel = referencePanel; this.moveText2 = moveText2;", ctx);
let failures = 0;
const eq = (got, want, what) => { if (got !== want) { failures++; console.error("FAIL " + what + ": got " + JSON.stringify(got) + ", want " + JSON.stringify(want)); } };
const ok = (cond, what) => { if (!cond) { failures++; console.error("FAIL " + what); } };

// ---- speeches: monetary only, the last 5, the rest under "Show all (N)" ----
const sp = (day, rel, who) => ({ doc_id: "s" + day, speaker: who || "Speaker " + day, title: "Title " + day, url: "https://x/" + day, published: "2026-09-" + String(day).padStart(2, "0"), relevance: rel, voter: day % 2 === 0, chair: day === 30, type: "speech", summary: null });
const speeches = [sp(3, "monetary"), sp(30, "monetary"), sp(28, "other"), sp(25, "monetary"), sp(20, "monetary"), sp(18, "monetary"), sp(15, "other"), sp(10, "monetary"), sp(5, "monetary")];
const s = ctx.speechSplit(speeches);
eq(s.top.map((x) => x.published.slice(8)).join(","), "30,25,20,18,10", "the last 5 monetary speeches, newest first");
eq(s.rest.length, 4, "the rest: older monetary + other");
ok(s.rest.some((x) => x.relevance === "other") && s.rest.some((x) => x.published.endsWith("-05")), "the rest keeps 'other' and the older monetary ones");
const sb = ctx.speechesBlock({ documents: { speeches: speeches, n_speeches: 9 } });
ok(sb.html.indexOf("<summary>Show all (4)</summary>") > 0, "Show all (N), closed");
ok(sb.html.indexOf("Title 28") > sb.html.indexOf("Show all (4)"), "an 'other' speech only under Show all");
ok(sb.html.indexOf("cb-chip-chair") > 0 && sb.html.indexOf("cb-chip-voter") > 0, "chair / voter chips");
const few = ctx.speechesBlock({ documents: { speeches: [sp(3, "monetary")], n_speeches: 1 } });
ok(few.html.indexOf("Show all") < 0, "no Show all when nothing is left over");
ok(ctx.speechesBlock({ documents: { speeches: [sp(3, "other")], n_speeches: 1 } }).html.indexOf("no speech about monetary policy") > 0, "only 'other': the empty line + Show all (1)");

// ---- projections: only with the bank's own path ----
const dots = { gap: { kind: "dots", sep: "2026-09-16", na: null, years: { "2027": { v: 55.4, flag: "CURVE", na: null, market: 4.679, bank: 4.125, n_dots: 18 }, "2026": { v: -0.3, flag: "CURVE", na: null, market: 4.122, bank: 4.125, n_dots: 18 } } },
               chart: { bank: { kind: "dots", years: [{ year: 2027, dots: [{ level: 4.375, count: 8 }, { level: 4.125, count: 10 }] }] } } };
const pb = ctx.projectionsBlock(dots);
ok(pb && pb.html.indexOf("<b>2027</b> · dots 4.125% · market 4.679%") > 0, "Fed: one row per year with dots and market");
ok(pb.html.indexOf("+55 bp</span> (market above the Fed)") > 0, "Fed: the gap in bp and its direction");
ok(pb.html.indexOf("(in line with the Fed)") > 0, "Fed: a gap under 0.5 bp is in line");
ok(pb.html.indexOf('title="Distribution of the 18 dots: 4.375% ×8, 4.125% ×10"') > 0, "Fed: the distribution in the tooltip");
ok(pb.html.indexOf("cb-flag-CURVE") > 0, "Fed: the method flag stays");
const ocr = { gap: { kind: "n/a", na: "no comparable horizon" }, chart: { bank: { kind: "ocr_track", source: "2026-08-12", finalised: "2026-08-05", note: "OCR track of the MPS.", quarters: [{ period: "2027Q1", value: 2.5 }, { period: "2027Q2", value: 2.75 }] } } };
const po = ctx.projectionsBlock(ocr);
ok(po && po.html.indexOf("<b>2027</b> · Q1 2.5% · Q2 2.8% · Q3 — · Q4 —") > 0, "RBNZ: the OCR track, one row per year");
ok(po.html.indexOf("OCR track of the MPS.") > 0, "RBNZ: the note stays");
eq(ctx.projectionsBlock({ gap: { kind: "n/a", na: "no bank path" }, chart: { bank: { kind: "n/a" } } }), null, "no own path: no block");

// ---- Decision history: the columns and one expander per decision ----
ctx.vm_summaries = null;
const dec = (date, bp, extra) => Object.assign({ date: date, effective: date, delta_bp: bp, rate_after: 2.5, lower: null, upper: null, consensus: 2.5, surprise_consensus_bp: 0,
  vs_market: { v: 3.2, flag: "CURVE", na: null, implied_step_bp: 21.8 }, reaction: { next: { v: 1 }, year: { v: 2 } },
  slots: { votes: { kind: "counted", label: "9–3" }, statement: { url: "https://x/st-" + date, label: "Statement" }, conference: null } }, extra || {});
const d = { decisions: [dec("2026-04-29", 0), dec("2026-09-16", 25), dec("2026-07-29", 0, { vs_market: { v: null, na: "no market history before 2026-10-08" } }), dec("2026-06-17", 0), dec("2026-03-18", -25)],
            documents: { timeline: [{ meeting: "2026-09-16", statement: { url: "https://x/st", label: "Statement", summary: { status: "ready", doc_id: "S1" } },
                                      follow_up: [{ type: "minutes", label: "Minutes", url: "https://x/min", summary: { status: "ready", doc_id: "M1" } }, { type: "presser_video", label: "Press conference video", url: "https://x/v" }] }] } };
const rows = ctx.decisionHistoryRows(d);
eq(rows.map((r) => r.date).join(","), "2026-09-16,2026-07-29,2026-06-17,2026-04-29", "the last 4 decisions, newest first");
eq(ctx.HISTORY_COLS.join(" | "), "Date | Move | Rate after | Vote | vs market (bp) | Links", "the six columns");
const html = ctx.decisionHistoryBlock(d).html;
ok(html.indexOf("<th>Date</th><th class=\"cb-n\">Move</th><th class=\"cb-n\">Rate after</th><th>Vote</th><th class=\"cb-n\" title=") > 0 && html.indexOf(">vs market (bp)</th><th>Links</th>") > 0, "the header");
["Effective", "Consensus", "vs consensus", "React."].forEach((w) => ok(html.indexOf(w) < 0, "no column " + w));
ok(/title="Decided step minus the step implied at T-1 \(\+21\.8 bp\)">\+3\.2/.test(html), "vs market keeps its tooltip");
ok(html.indexOf('title="no market history before 2026-10-08">—') > 0, "vs market n/a with the reason");
ok(html.indexOf(">Statement</a> · <a") > 0 && html.indexOf(">Minutes</a>") > 0 && html.indexOf(">Video</a>") > 0, "links: statement, minutes, press conference");
eq((html.match(/<details/g) || []).length, 0, "no summary loaded: no expander");
ctx.state.meta = { flags: {} };
vm.runInContext('SUMMARIES = { S1: { points: ["p1"], quotes: [], note: "factual", model: "m", prompt_version: "v", generated: "2026-09-21" }, M1: { points: ["p2"], quotes: [], note: "factual", model: "m", prompt_version: "v", generated: "2026-10-08" } };', ctx);
const html2 = ctx.decisionHistoryBlock(d).html;
eq((html2.match(/<details class="cb-x"><summary>Summaries/g) || []).length, 1, "one expander per decision that has summaries");
ok(html2.indexOf("Summaries (Statement, Minutes)") > 0, "the expander lists its summaries");

// ---- latest decision: one row, the summary visible, two closed expanders ----
eq(ctx.moveText2({ delta_bp: 25, lower: 3.75, upper: 4.0, rate_after: 3.875 }), "+25 bp to 3.75–4.00%", "move text");
eq(ctx.moveText2({ delta_bp: 0, lower: null, rate_after: 2.5 }), "Hold at 2.50%", "hold text");
const L = { documents: { manual_only: true, timeline: [], latest: { meeting: "2026-09-16", votes: { kind: "unanimous", label: "12–0" }, summary: { status: "ready", doc_id: "S1" }, follow_up: [],
  statement: { url: "https://x/st", paragraphs: ["a", "b"], rate_after: 3.875, license: "", format: "html" }, redline: { prev_meeting: "2026-07-29", added_words: 3, removed_words: 1, paras: [{ kind: "same", p: 0 }] } } },
  decisions: [dec("2026-09-16", 25, { lower: 3.75, upper: 4.0 })] };
const lb = ctx.latestDecisionBlock(L);
ok(lb.html.indexOf("+25 bp to 3.75–4.00%") > 0 && lb.html.indexOf(">12–0<") > 0, "the row: move and vote chip");
ok(lb.html.indexOf('<ul class="cb-sum-points">') > 0 && lb.html.indexOf("cb-sum-quotes") < 0, "the summary points are visible (compact)");
ok(lb.html.indexOf("<summary>Changes vs 29 Jul 2026") > 0 && lb.html.indexOf("<summary>Full statement") > 0 && lb.html.indexOf("<details class=\"cb-x\" open") < 0, "two closed expanders");
ok(lb.html.indexOf("manual file") > 0, "the RBNZ manual note");

// ---- By meeting: the projections label and the tooltip of the date ----
eq(ctx.projLabel("SEP (dot plot)"), "SEP", "SEP");
eq(ctx.projLabel("Monetary Policy Report"), "MPR", "MPR");
eq(ctx.projLabel("Statement on Monetary Policy"), "SMP", "SMP");
eq(ctx.projLabel("Monetary Policy Statement (OCR track)"), "MPS", "MPS");
eq(ctx.projLabel("Eurosystem staff projections"), "Projections", "ECB");
const pj = { na: null, history: { "1w": { na: "history starts 2026-10-08", points: [] } },
  points: [{ meeting: "2026-10-28", rate: 3.93, cum_bp: 5.5, est: false, prob: { dir: "hike", n: 0, p: 0.226 }, flag: "CURVE" }, { meeting: "2026-12-09", rate: 4.13, cum_bp: 25.3, est: false, prob: { dir: "hike", n: 0, p: 0.79 }, flag: "CURVE" }] };
const cal = [{ decision: "2026-10-28", effective: "2026-10-29", has_projections: false, projections_name: null, has_presser: true, conference_local: "14:30", time: { local: "14:00", abbr: "EDT", tz: "America/New_York" },
               blackout: { start: "2026-10-17T00:00:00-04:00", end: "2026-10-29T23:59:00-04:00", precision: "exact", verified: true } },
             { decision: "2026-12-09", effective: "2026-12-10", has_projections: true, projections_name: "SEP (dot plot)", has_presser: true, conference_local: "14:30", time: { local: "14:00", abbr: "EST", tz: "America/New_York" }, blackout: null }];
const t = ctx.pathTableHtml(pj, cal);
ok(t.indexOf('<span class="econ-flag cb-proj" title="SEP (dot plot)">SEP</span>') > 0, "the SEP label with the full name in its tooltip");
eq((t.match(/cb-proj/g) || []).length, 1, "only the meeting with projections has the label");
ok(t.indexOf('title="Decision Wed 28 Oct 2026, 14:00 EDT · effective 29 Oct 2026 · press conference 14:30 EDT · blackout 17 Oct → 29 Oct"') > 0, "the date tooltip: time, effective, conference, blackout");
ok(ctx.pathTableHtml(pj).indexOf("cb-proj") < 0, "no calendar: no label");
ok(t.indexOf("press conference 14:30 EST · projections: SEP (dot plot)") > 0, "the date tooltip names the projections");

if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("ok - bank page v3");
