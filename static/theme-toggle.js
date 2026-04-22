/* Generic theme toggle used by the site-wide navbar.
   Stores preference in localStorage under key "cot-theme" for continuity
   with the original COT-page implementation. */
(function () {
  "use strict";

  const KEY = "cot-theme";

  function apply(theme) {
    if (theme === "dark") document.body.classList.add("dark");
    else document.body.classList.remove("dark");
  }

  function stored() {
    try { return localStorage.getItem(KEY); } catch (_) { return null; }
  }

  function save(theme) {
    try { localStorage.setItem(KEY, theme); } catch (_) { /* ignore */ }
  }

  // Apply early to reduce flash of wrong theme. Safe even if body isn't
  // ready yet — class is latched onto body, which exists by the time this
  // script runs (it lives inside <body>).
  apply(stored() === "dark" ? "dark" : "light");

  function wire() {
    const btn = document.getElementById("themeBtn");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const goDark = !document.body.classList.contains("dark");
      apply(goDark ? "dark" : "light");
      save(goDark ? "dark" : "light");
      btn.setAttribute("aria-pressed", goDark ? "true" : "false");
      btn.setAttribute("aria-label", goDark ? "Switch to light theme" : "Switch to dark theme");
    });
    btn.setAttribute(
      "aria-pressed",
      document.body.classList.contains("dark") ? "true" : "false",
    );
    btn.setAttribute(
      "aria-label",
      document.body.classList.contains("dark")
        ? "Switch to light theme"
        : "Switch to dark theme",
    );
  }

  // Cross-tab sync.
  window.addEventListener("storage", (e) => {
    if (e.key === KEY) apply(e.newValue === "dark" ? "dark" : "light");
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
