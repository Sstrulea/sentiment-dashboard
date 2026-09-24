/* Generic theme toggle used by the site-wide navbar.
   Stores preference in localStorage under key "cot-theme" for continuity
   with the original COT-page implementation.

   Audit 9D: the theme is first applied from <head> (templates/_theme_head.html.j2)
   on <html>, before the first paint. Without a saved choice it follows
   prefers-color-scheme (and its live changes); a saved choice always wins.
   The "dark" class is kept on BOTH <html> (CSS) and <body> (the chart scripts
   read document.body.classList). */
(function () {
  "use strict";

  const KEY = "cot-theme";
  const mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function stored() {
    try {
      const v = localStorage.getItem(KEY);
      return v === "dark" || v === "light" ? v : null;
    } catch (_) { return null; }
  }

  function save(theme) {
    try { localStorage.setItem(KEY, theme); } catch (_) { /* ignore */ }
  }

  function preferred() {
    return stored() || (mq && mq.matches ? "dark" : "light");
  }

  function apply(theme) {
    const dark = theme === "dark";
    document.documentElement.classList.toggle("dark", dark);
    if (document.body) document.body.classList.toggle("dark", dark);
    const btn = document.getElementById("themeBtn");
    if (btn) {
      btn.setAttribute("aria-pressed", dark ? "true" : "false");
      btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    }
  }

  // This script lives inside <body>, right after the navbar: body exists.
  apply(preferred());

  function wire() {
    const btn = document.getElementById("themeBtn");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const goDark = !document.documentElement.classList.contains("dark");
      save(goDark ? "dark" : "light");
      apply(goDark ? "dark" : "light");
    });
    apply(preferred());
  }

  // Cross-tab sync.
  window.addEventListener("storage", (e) => {
    if (e.key === KEY) apply(preferred());
  });

  // No saved choice: follow the system theme as it changes.
  if (mq) {
    const onChange = () => { if (!stored()) apply(preferred()); };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
