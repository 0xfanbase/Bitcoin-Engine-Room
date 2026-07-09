/* info.js -- educational info-panel toggles ("explain-everything" tooltips,
 * build spec Section 16 / line 337: definition + why-it-matters + source on
 * every metric label). Purely static-markup interaction with no dependency
 * on BER, fetched data, or any other module's boot order, so this attaches
 * immediately rather than waiting for ber:booted like every other asset
 * file does. The two section-level explainers (status-chip vocabulary,
 * audit-checks glossary) are native <details>/<summary> and need no JS at
 * all -- this file only drives the per-metric button+panel pairs.
 */
(function () {
  "use strict";

  document.addEventListener("click", function (event) {
    const toggle = event.target.closest(".info-toggle");
    if (!toggle) return;
    const panel = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!panel) return;
    const isOpen = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!isOpen));
    panel.hidden = isOpen;
  });
})();
