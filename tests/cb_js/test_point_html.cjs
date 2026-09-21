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
vm.runInContext(cut("  function esc(s) {", "  function fmtDate") + cut("  function markFragment(", "  function summaryBlock") , ctx);

const ev = { paragraphs: [3, 4], paragraph: 3, fragment: "Economic activity is expanding at a solid pace.", coverage: 1,
             href: "https://bank.example/s.htm#:~:text=Economic%20activity", texts: { "3": "Economic activity is expanding at a solid pace. While <b>uncertainty</b> remains elevated.", "4": "Inflation remains elevated." } };
const html = vm.runInContext("pointHtml", ctx)("The Committee says activity is expanding & inflation remains elevated.", ev);
const fail = (m) => { console.error("FAIL: " + m + "\n" + html); process.exit(1); };
if (!html.startsWith('<li class="cb-sum-point" title="Source ¶3, ¶4: “Economic activity is expanding at a solid pace.” (click for the source)">')) fail("the hover text is the source fragment");
if (!html.includes('class="cb-sum-text" tabindex="0" role="button" aria-expanded="false">The Committee says activity is expanding &amp; inflation remains elevated.</span>')) fail("the point is escaped and focusable");
if (!html.includes('<div class="cb-sum-evidence" hidden>')) fail("the evidence starts hidden");
if (!html.includes('<a href="https://bank.example/s.htm#:~:text=Economic%20activity" target="_blank" rel="noopener"')) fail("the anchor to the source text");
if (!html.includes("<b>¶3</b> <mark>Economic activity is expanding at a solid pace.</mark> While &lt;b&gt;uncertainty&lt;/b&gt; remains elevated.")) fail("the paragraph, fragment highlighted, text escaped");
if (!html.includes("<b>¶4</b> Inflation remains elevated.</div>") || html.split("<mark>").length !== 2) fail("the second paragraph is shown without a highlight");

const legacy = vm.runInContext("pointHtml", ctx)("A plain <point>", null);
if (legacy !== "<li>A plain &lt;point&gt;</li>") fail("a point without evidence is a plain item: " + legacy);
const noTexts = vm.runInContext("pointHtml", ctx)("p", Object.assign({}, ev, { texts: null }));
if (noTexts.includes("cb-sum-para") || !noTexts.includes("open the source")) fail("a document whose text is not committed shows the fragment and the link only");

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
console.log("point evidence ok");
