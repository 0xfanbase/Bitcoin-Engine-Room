/* info.js -- educational info-panel toggles ("explain-everything" tooltips,
 * build spec Section 16 / line 337: definition + why-it-matters + source on
 * every metric label). Purely static-markup interaction with no dependency
 * on BER, fetched data, or any other module's boot order, so this attaches
 * immediately rather than waiting for ber:booted like every other asset
 * file does. The two section-level explainers (status-chip vocabulary,
 * audit-checks glossary) are native <details>/<summary> and need no JS at
 * all -- this file only drives the per-metric button+panel pairs.
 *
 * Reveal is hover- or focus-driven (mouse users see the description without
 * clicking); a click pins the panel open so touch users -- who never fire a
 * hover event -- get the same persistent-open behavior mouse users get by
 * clicking. A pinned panel ignores mouseleave/blur and only a second click
 * (or clicking a different toggle) closes it.
 */
(function () {
  "use strict";

  function openPanel(toggle, panel) {
    toggle.setAttribute("aria-expanded", "true");
    panel.hidden = false;
  }

  function closePanel(toggle, panel) {
    toggle.setAttribute("aria-expanded", "false");
    panel.hidden = true;
  }

  document.addEventListener("click", function (event) {
    const toggle = event.target.closest(".info-toggle");
    if (!toggle) return;
    const panel = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!panel) return;
    const isOpen = toggle.getAttribute("aria-expanded") === "true";
    if (isOpen && toggle.dataset.pinned === "true") {
      toggle.dataset.pinned = "false";
      closePanel(toggle, panel);
    } else {
      toggle.dataset.pinned = "true";
      openPanel(toggle, panel);
    }
  });

  document.querySelectorAll(".info-toggle").forEach(function (toggle) {
    const panel = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!panel) return;

    toggle.addEventListener("mouseenter", function () {
      openPanel(toggle, panel);
    });
    toggle.addEventListener("mouseleave", function () {
      if (toggle.dataset.pinned === "true") return;
      closePanel(toggle, panel);
    });
    toggle.addEventListener("focus", function () {
      openPanel(toggle, panel);
    });
    toggle.addEventListener("blur", function () {
      if (toggle.dataset.pinned === "true") return;
      closePanel(toggle, panel);
    });
  });
})();
