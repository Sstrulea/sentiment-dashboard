/* Shared "How the score is computed" help modal wiring — included on both
 * /economic and /strength (templates/_help_scoring_modal.html.j2 provides
 * the markup + text ONCE; this file is the one place the open/close
 * behavior lives, so neither page's own script duplicates it). Same
 * pattern as /economic's own #econDetailModal: X button, backdrop click,
 * and Escape all close it; body.modal-open while open.
 */
(function () {
  "use strict";

  function wire() {
    const btn = document.getElementById("helpScoringBtn");
    const modal = document.getElementById("helpScoringModal");
    if (!btn || !modal) return;

    function open() {
      modal.hidden = false;
      document.body.classList.add("modal-open");
    }
    function close() {
      modal.hidden = true;
      document.body.classList.remove("modal-open");
    }

    btn.addEventListener("click", open);
    modal.querySelector(".modal-close").addEventListener("click", close);
    modal.querySelector(".modal-backdrop").addEventListener("click", close);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !modal.hidden) close();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
