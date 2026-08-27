/* Shared divergent score-color palette (blue = bullish/positive, red =
 * bearish/negative) used by every page that colors a signed score cell —
 * /economic and /carry alike. Moved out of economic-chart.js so /carry can
 * reuse the exact same colors/gradient math without duplicating it. Load
 * this script BEFORE any page script that reads window.ScorePalette.
 */
(function () {
  "use strict";

  const COT_BLUE = "21,101,192";   // #1565c0  (bullish / positive)
  const COT_RED = "211,47,47";     // #d32f2f  (bearish / negative)

  // Continuous divergent gradient using the COT endpoints (intense blue ↔ intense
  // red), as a tint over the cell so it adapts to light/dark themes. `score` is
  // signed; `scale` is the magnitude that saturates to full intensity. Returns
  // an inline-style string ("" for neutral → no tint).
  function gradientStyle(score, scale) {
    if (score === null || score === undefined || Number.isNaN(Number(score))) return "";
    const t = Math.max(-1, Math.min(1, Number(score) / scale));
    const mag = Math.pow(Math.abs(t), 0.7);          // ease so low values still read
    if (mag < 0.001) return "";                       // neutral → transparent
    const rgb = t > 0 ? COT_BLUE : COT_RED;
    const a = mag.toFixed(3);
    const fg = mag >= 0.55 ? "#fff" : "";
    return "background:rgba(" + rgb + "," + a + ")" + (fg ? ";color:" + fg : "");
  }
  function styleAttr(s) { return s ? ' style="' + s + '"' : ""; }

  window.ScorePalette = { COT_BLUE: COT_BLUE, COT_RED: COT_RED, gradientStyle: gradientStyle, styleAttr: styleAttr };
})();
