/* Phone view (Faza 11): the pieces every page shares.
 *
 *   - navbar: the active tab is scrolled into view (centred, horizontally only)
 *     and a fade marks the edge where more tabs are;
 *   - the first-visit banner ("Best on desktop or tablet"): its visibility is
 *     decided in <head> (templates/_theme_head.html.j2), this only wires the ×;
 *   - "How to read": long descriptions marked [data-htr] fold under one button
 *     on the phone (closed by default); on desktop the button is hidden;
 *   - PhoneUI.sheet(): the one bottom sheet (filters and details);
 *   - PhoneUI.filterButton(): the full-width filter button + its sheet.
 *
 * Phone = max-width 600px (PhoneUI.isPhone()). DOM + textContent only.
 */
(function () {
  "use strict";

  const PHONE_MQ = window.matchMedia ? window.matchMedia("(max-width: 600px)") : null;
  const BANNER_KEY = "phone-banner-closed";
  const SVG_NS = "http://www.w3.org/2000/svg";

  function isPhone() { return !!(PHONE_MQ && PHONE_MQ.matches); }

  function h(tag, attrs) {
    const el = document.createElement(tag);
    for (const k in attrs || {}) {
      const v = attrs[k];
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (let i = 2; i < arguments.length; i++) {
      const c = arguments[i];
      if (c === null || c === undefined || c === false) continue;
      (Array.isArray(c) ? c : [c]).forEach((x) => {
        if (x !== null && x !== undefined && x !== false) el.appendChild(typeof x === "string" ? document.createTextNode(x) : x);
      });
    }
    return el;
  }
  function icon(d, size) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("width", size || 18); svg.setAttribute("height", size || 18);
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "ph-ico");
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    svg.appendChild(p);
    return svg;
  }
  const ICON = {
    close: "M6 6l12 12M18 6L6 18",
    chevronDown: "M6 9l6 6 6-6",
    chevronRight: "M9 6l6 6-6 6",
    check: "M5 12.5l4.2 4.2L19 7",
  };

  // ---------------------------------------------------------------- navbar
  function initNav() {
    const tabs = document.querySelector(".nav-tabs");
    const list = tabs && tabs.querySelector(".nav-links");
    if (!list) return;
    const update = () => {
      const max = list.scrollWidth - list.clientWidth;
      tabs.classList.toggle("fade-left", list.scrollLeft > 2);
      tabs.classList.toggle("fade-right", max > 2 && list.scrollLeft < max - 2);
    };
    const centre = () => {
      const active = list.querySelector("li.active");
      if (active && list.scrollWidth > list.clientWidth) {
        // scrollLeft only: scrollIntoView would also move the page vertically
        list.scrollLeft = active.offsetLeft - (list.clientWidth - active.offsetWidth) / 2;
      }
      update();
    };
    list.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", centre);
    centre();
  }

  // ---------------------------------------------------------------- banner
  function initBanner() {
    const x = document.querySelector(".phone-banner-x");
    if (!x) return;
    x.addEventListener("click", () => {
      try { localStorage.setItem(BANNER_KEY, "1"); } catch (_) { /* private mode: closed for this page only */ }
      document.documentElement.classList.add("banner-closed");
    });
  }

  // ---------------------------------------------------------------- How to read
  /* Every [data-htr] inside `root` (default: the page's <main>) folds under ONE
   * button inserted before the first of them. Idempotent: pages that build their
   * description later (cot.js, cb.js) call PhoneUI.foldHowToRead() again. */
  function foldHowToRead(root) {
    const main = root || document.querySelector("main");
    if (!main) return;
    const parts = Array.from(main.querySelectorAll("[data-htr]"));
    if (!parts.length) return;
    let btn = main.querySelector(":scope .htr-btn");
    if (!btn) {
      btn = h("button", { type: "button", class: "htr-btn", "aria-expanded": "false" },
        icon(ICON.chevronRight, 14), "How to read");
      btn.addEventListener("click", () => {
        const open = btn.getAttribute("aria-expanded") !== "true";
        btn.setAttribute("aria-expanded", String(open));
        main.classList.toggle("htr-open", open);
      });
    }
    if (btn.nextElementSibling !== parts[0]) parts[0].parentNode.insertBefore(btn, parts[0]);
    const ids = parts.map((p, i) => p.id || (p.id = "htr-" + i));
    btn.setAttribute("aria-controls", ids.join(" "));
  }

  // ---------------------------------------------------------------- sheet
  let current = null;

  /* opts: {title, body: Node, footer: Node?, full: bool, returnFocus: Element?,
   *        initialFocus: Element?, onClose: fn?, headExtra: Node?, label: string?}
   * Returns {el, body, close}. One sheet at a time. */
  function sheet(opts) {
    if (current) current.close(true);
    const returnFocus = opts.returnFocus || document.activeElement;
    const titleId = "ph-sheet-title";
    const closeBtn = h("button", { type: "button", class: "ph-sheet-x", "aria-label": "Close" }, icon(ICON.close, 20));
    const head = h("div", { class: "ph-sheet-head" },
      opts.titleNode || h("h2", { class: "ph-sheet-title", id: titleId, text: opts.title || "" }),
      opts.headExtra || null, closeBtn);
    if (opts.titleNode) opts.titleNode.id = opts.titleNode.id || titleId;
    const body = h("div", { class: "ph-sheet-body" }, opts.body || null);
    const panel = h("div", {
      class: "ph-sheet" + (opts.full ? " ph-sheet-full" : "") + (opts.cls ? " " + opts.cls : ""),
      role: "dialog", "aria-modal": "true", "aria-labelledby": opts.label ? null : titleId, "aria-label": opts.label || null,
    }, h("span", { class: "ph-grab", "aria-hidden": "true" }), head, body, opts.footer ? h("div", { class: "ph-sheet-foot" }, opts.footer) : null);
    const backdrop = h("div", { class: "ph-backdrop" });
    const scrollY = window.scrollY;
    document.body.appendChild(backdrop);
    document.body.appendChild(panel);
    // lock the page underneath (iOS ignores overflow:hidden on body alone)
    document.documentElement.classList.add("ph-locked");
    document.body.style.top = -scrollY + "px";

    function focusables() {
      return Array.from(panel.querySelectorAll("button, a[href], input, select, textarea, [tabindex]:not([tabindex='-1'])"))
        .filter((x) => !x.disabled && x.offsetParent !== null);
    }
    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); api.close(); return; }
      if (e.key !== "Tab") return;
      const f = focusables();
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
    const api = {
      el: panel, body: body,
      close(silent) {
        if (current !== api) return;
        current = null;
        document.removeEventListener("keydown", onKey, true);
        panel.remove(); backdrop.remove();
        document.documentElement.classList.remove("ph-locked");
        document.body.style.top = "";
        window.scrollTo(0, scrollY);
        if (opts.onClose) opts.onClose();
        if (!silent && returnFocus && document.contains(returnFocus)) returnFocus.focus();
      },
    };
    closeBtn.addEventListener("click", () => api.close());
    backdrop.addEventListener("click", () => api.close());
    document.addEventListener("keydown", onKey, true);
    current = api;
    (opts.initialFocus || closeBtn).focus();
    return api;
  }

  // ---------------------------------------------------------------- filter button
  /* opts: {label, noun ("pairs"), groups: [{label?, items:[{key, label, count?}]}],
   *        get(): Set (empty = all), set(Set), count(Set): number, id?}
   * Returns the button; call .refresh() after the page's state changes. */
  function filterButton(opts) {
    const all = [];
    opts.groups.forEach((g) => g.items.forEach((it) => all.push(it)));
    const valueEl = h("span", { class: "ph-filter-value" });
    const pill = h("span", { class: "ph-pill" });
    const btn = h("button", { type: "button", class: "ph-filter-btn", "aria-haspopup": "dialog", id: opts.id || null },
      h("span", { class: "ph-filter-main" }, h("span", { class: "ph-filter-label", text: opts.label }), valueEl, pill),
      icon(ICON.chevronDown, 18));
    function refresh() {
      const sel = opts.get();
      const n = sel.size === 0 || sel.size === all.length ? all.length : sel.size;
      const chosen = all.filter((it) => sel.has(it.key)).map((it) => it.label);
      valueEl.textContent = sel.size === 0 || sel.size === all.length ? "All" : (opts.summary ? opts.summary(chosen) : chosen.join(", "));
      pill.textContent = n + " of " + all.length;
    }
    btn.refresh = refresh;
    btn.addEventListener("click", () => {
      const rows = [];
      const showBtn = h("button", { type: "button", class: "ph-primary" });
      const clear = h("button", { type: "button", class: "ph-link", text: "Clear" });
      function sync() {
        const sel = opts.get();
        rows.forEach((r) => {
          const on = sel.has(r.key);
          r.input.checked = on;
          r.row.classList.toggle("is-on", on);
        });
        const n = opts.count(sel);
        showBtn.textContent = "Show " + n + " " + (n === 1 ? (opts.nounOne || opts.noun) : opts.noun);
        refresh();
      }
      const list = h("div", { class: "ph-checklist" });
      opts.groups.forEach((g) => {
        if (g.label) list.appendChild(h("div", { class: "ph-check-head", text: g.label }));
        g.items.forEach((it) => {
          const input = h("input", { type: "checkbox", class: "ph-check-input" });
          input.addEventListener("change", () => {
            const sel = new Set(opts.get());
            if (input.checked) sel.add(it.key); else sel.delete(it.key);
            opts.set(sel.size === all.length ? new Set() : sel);
            sync();
          });
          const row = h("label", { class: "ph-check-row" }, input,
            h("span", { class: "ph-check-box", "aria-hidden": "true" }, icon(ICON.check, 14)),
            h("span", { class: "ph-check-name", text: it.label }),
            it.count !== undefined ? h("span", { class: "ph-check-count", text: String(it.count) }) : null);
          rows.push({ key: it.key, input: input, row: row });
          list.appendChild(row);
        });
      });
      clear.addEventListener("click", () => { opts.set(new Set()); sync(); });
      const s = sheet({ title: opts.label, headExtra: clear, body: list, footer: showBtn, returnFocus: btn, cls: "ph-sheet-filter" });
      showBtn.addEventListener("click", () => s.close());
      sync();
    });
    refresh();
    return btn;
  }

  /* Segmented control: opts {items:[{key,label}], value, onChange(key), label} */
  function segmented(opts) {
    const wrap = h("div", { class: "ph-seg", role: "group", "aria-label": opts.label || null });
    const btns = opts.items.map((it) => {
      const b = h("button", { type: "button", class: "ph-seg-btn", text: it.label });
      b.addEventListener("click", () => { set(it.key); opts.onChange(it.key); });
      wrap.appendChild(b);
      return { key: it.key, b: b };
    });
    function set(k) { btns.forEach((x) => x.b.setAttribute("aria-pressed", String(x.key === k))); }
    set(opts.value);
    wrap.set = set;
    return wrap;
  }

  window.PhoneUI = { isPhone: isPhone, mq: PHONE_MQ, h: h, icon: icon, ICON: ICON, sheet: sheet,
    filterButton: filterButton, segmented: segmented, foldHowToRead: foldHowToRead,
    current: () => current };

  function init() {
    initNav();
    initBanner();
    foldHowToRead();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
