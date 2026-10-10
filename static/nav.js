/* Grouped navigation (templates/_navbar.html.j2, menu in _nav_config.html.j2).
 *
 *   - a group's link opens the last page visited in that group: the active
 *     sub-page is remembered per group (localStorage "nav-last-<group>") and
 *     every group link's href is swapped for its remembered page. Without JS
 *     (or storage) the href stays the group's first page;
 *   - the sub-page row scrolls sideways when it does not fit: the active
 *     sub-tab / chip is brought into view (horizontally only).
 */
(function () {
  "use strict";

  const PREFIX = "nav-last-";

  function remember() {
    const sub = document.querySelector(".nav-sub");
    const cur = sub && sub.querySelector('a[aria-current="page"]');
    if (!cur) return;
    try { localStorage.setItem(PREFIX + sub.dataset.navGroup, cur.getAttribute("href")); } catch (_) { /* private mode */ }
  }

  function restore() {
    document.querySelectorAll("a.nav-group[data-nav-group]").forEach((a) => {
      let last = null;
      try { last = localStorage.getItem(PREFIX + a.dataset.navGroup); } catch (_) { return; }
      // only a page still listed in that group (a removed page falls back to the first one)
      if (last && (a.dataset.navPages || "").split(" ").indexOf(last) >= 0) a.setAttribute("href", last);
    });
  }

  function centreActive() {
    const list = document.querySelector(".nav-sub ul");
    const active = list && list.querySelector('a[aria-current="page"]');
    if (!active || list.scrollWidth <= list.clientWidth) return;
    const li = active.parentNode;
    // scrollLeft only: scrollIntoView would also move the page vertically (the ul is
    // position: relative, so offsetLeft is measured from it)
    list.scrollLeft = li.offsetLeft - (list.clientWidth - li.offsetWidth) / 2;
  }

  function init() {
    remember();
    restore();
    centreActive();
    window.addEventListener("resize", centreActive);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
