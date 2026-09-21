// The evidence of a summary point on the Central Banks page, checked on the real static/cb.js in plain Node (no browser, no dependency): the pieces of the script that
// build a point and toggle its evidence are cut out of the file and run against a tiny fake document.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "cb.js"), "utf8");

function cut(from, to) {
  const a = src.indexOf(from), b = src.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error("not found in cb.js: " + from + " .. " + to);
  return src.slice(a, b);
}
const listeners = {};
const ctx = { document: { addEventListener: (t, f) => { listeners[t] = f; } } };
vm.createContext(ctx);
vm.runInContext(cut("  function esc(s) {", "  function fmtDate") + cut("  function markFragments(", "  function summaryBlock"), ctx);
const blockCtx = { document: { addEventListener: () => {} }, NA: "n/a", fmtDate: (d) => d };                                            // the whole summary block, with the two helpers it needs
vm.createContext(blockCtx);
vm.runInContext(cut("  function esc(s) {", "  function fmtDate") + cut("  function changesBlock(", "  function summarySlot"), blockCtx);

const F1 = "Economic activity is expanding at a solid pace.", F2 = "Inflation remains elevated.";
const ev = { paragraphs: [3, 4], fragments: [{ text: F1, paragraph: 3 }, { text: F2, paragraph: 4 }], coverage: 1,
             href: "https://bank.example/s.htm#:~:text=Economic%20activity&text=Inflation%20remains", texts: { "3": "Economic activity is expanding at a solid pace. While <b>uncertainty</b> remains elevated.", "4": "Inflation remains elevated. The rest." } };
const html = vm.runInContext("pointHtml", ctx)("The Committee says activity is expanding & inflation remains elevated.", ev);
const fail = (m) => { console.error("FAIL: " + m + "\n" + html); process.exit(1); };
if (!html.startsWith('<li class="cb-sum-point" title="Source ¶3, ¶4: “' + F1 + '” · “' + F2 + '” (click for the source)">')) fail("the hover text has every fragment");
if (!html.includes('class="cb-sum-text" tabindex="0" role="button" aria-expanded="false">The Committee says activity is expanding &amp; inflation remains elevated.</span>')) fail("the point is escaped and focusable");
if (!html.includes('<div class="cb-sum-evidence" hidden>')) fail("the evidence starts hidden");
if (!html.includes('<a href="https://bank.example/s.htm#:~:text=Economic%20activity&amp;text=Inflation%20remains" target="_blank" rel="noopener"')) fail("the anchor to the source text carries every fragment");
if (!html.includes("<b>¶3</b> <mark>" + F1 + "</mark> While &lt;b&gt;uncertainty&lt;/b&gt; remains elevated.")) fail("the paragraph, fragment highlighted, text escaped");
if (!html.includes("<b>¶4</b> <mark>" + F2 + "</mark> The rest.</div>") || html.split("<mark>").length !== 3) fail("each paragraph highlights its own fragment");

// two fragments in one paragraph, overlapping ones, and one that is not in the paragraph
const mark = vm.runInContext("markFragments", ctx);
if (mark("aa bb cc dd ee", ["bb cc", "dd ee"]) !== "aa <mark>bb cc</mark> <mark>dd ee</mark>") throw new Error("two fragments in one paragraph: " + mark("aa bb cc dd ee", ["bb cc", "dd ee"]));
if (mark("aa bb cc dd ee", ["bb cc dd", "cc dd ee"]) !== "aa <mark>bb cc dd</mark><mark> ee</mark>") throw new Error("overlapping fragments: " + mark("aa bb cc dd ee", ["bb cc dd", "cc dd ee"]));
if (mark("a <b> c", ["zz"]) !== "a &lt;b&gt; c") throw new Error("a fragment that is not there highlights nothing");

// nothing of the document reaches the markup unescaped - not the point, not the fragments in the tooltip, not the paragraph
const hostile = vm.runInContext("pointHtml", ctx)("a <i>point</i>", { paragraphs: [1], fragments: [{ text: 'say "hi" <b>now</b>', paragraph: 1 }], coverage: 1, href: 'https://x/"y', texts: { "1": 'he said "hi" <b>now</b> & left' } });
if (hostile.includes('"hi"') && hostile.split('"hi"').length > 1 && /title="[^"]*"hi"/.test(hostile)) throw new Error("a quote in a fragment breaks out of the tooltip attribute: " + hostile);
const title = hostile.match(/^<li class="cb-sum-point" title="([^"]*)">/);
if (!title || !title[1].includes("say &quot;hi&quot; &lt;b&gt;now&lt;/b&gt;")) throw new Error("the tooltip does not carry the escaped fragment: " + hostile);
if (!hostile.includes("say &quot;hi&quot; &lt;b&gt;now&lt;/b&gt;") || hostile.includes("<i>") || hostile.includes("<b>now") || !hostile.includes('href="https://x/&quot;y"')) throw new Error("unescaped markup: " + hostile);
if (!hostile.includes("he said &quot;hi&quot; &lt;b&gt;now&lt;/b&gt; &amp; left") && !hostile.includes("<mark>say")) { /* the fragment is not in the paragraph text: the paragraph is shown as it is, escaped */ }

const legacy = vm.runInContext("pointHtml", ctx)("A plain <point>", null);
if (legacy !== "<li>A plain &lt;point&gt;</li>") fail("a point without evidence is a plain item: " + legacy);
const noTexts = vm.runInContext("pointHtml", ctx)("p", Object.assign({}, ev, { texts: null }));
if (noTexts.includes("cb-sum-para") || !noTexts.includes("open the source")) fail("a document whose text is not committed shows the fragments and the link only");

// the click handler opens and closes the evidence box next to the point
const box = { hidden: true };
const text = { nextElementSibling: box, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
const target = { closest: (sel) => (sel === ".cb-sum-text" ? text : null) };
listeners.click({ target });
if (box.hidden !== false || text.attrs["aria-expanded"] !== "true") fail("a click opens the evidence");
listeners.click({ target });
if (box.hidden !== true || text.attrs["aria-expanded"] !== "false") fail("a second click closes it");
let clicked = 0;
listeners.keydown({ key: "Enter", target: { classList: { contains: (c) => c === "cb-sum-text" }, click: () => { clicked++; } }, preventDefault() {} });
if (clicked !== 1) fail("Enter on a focused point clicks it");
listeners.click({ target: { closest: () => null } });                                                 // a click elsewhere does nothing
// the note of what verification removed
const base = { points: ["p one", "p two", "p three"], evidence: [null, null, null], quotes: [], note: "factual summary", model: "m", prompt_version: "x-v6", generated: "2026-09-21", provider: "openai", changes: null, truncated: false };
const block = vm.runInContext("summaryBlock", blockCtx);
const none = block(Object.assign({}, base, { dropped: [] }));
if (none.includes("cb-sum-dropped")) throw new Error("no note when nothing was removed: " + none);
if (block(base).includes("cb-sum-dropped")) throw new Error("no note for a record without the field");
const one = block(Object.assign({}, base, { dropped: [{ text: 'a "bad" <point>', reason: "not supported: 'bonds'" }] }));
if (!one.includes('<div class="cb-sum-dropped" title="\u201ca &quot;bad&quot; &lt;point&gt;\u201d: not supported: \'bonds\'">1 point removed by verification</div>')) throw new Error("one point removed: " + one);
const two = block(Object.assign({}, base, { dropped: [{ text: "x", reason: "r1" }, { text: "y", reason: "r2" }] }));
if (!two.includes("2 points removed by verification") || !two.includes("\u201cx\u201d: r1\n\u201cy\u201d: r2")) throw new Error("two points removed: " + two);
if (two.indexOf("cb-sum-points") > two.indexOf("cb-sum-dropped")) throw new Error("the note comes after the points");
console.log("point evidence ok");
