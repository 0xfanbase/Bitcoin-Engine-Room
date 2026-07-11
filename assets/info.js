/* info.js -- educational info-panel toggles ("explain-everything" tooltips,
 * build spec Section 16 / line 337: definition + why-it-matters + source on
 * every metric label). Purely static-markup interaction with no dependency
 * on BER, fetched data, or any other module's boot order, so this attaches
 * immediately rather than waiting for ber:booted like every other asset
 * file does. The two section-level explainers (status-chip vocabulary,
 * audit-checks glossary) are native <details>/<summary> and need no JS to
 * open individually -- but they carry the same circular "i" bullet as
 * every .info-toggle (::before in style.css), so #expand-all-toggle treats
 * them as part of "all the i's" too, not just the 15 button-driven panels.
 *
 * Reveal is click-only. It used to also open on hover/focus, but on the
 * masthead stats -- whose box width tracks their content -- opening the
 * panel widened the box and shifted the toggle out from under the pointer,
 * firing mouseleave -> close -> mouseenter -> open in an unending loop.
 * Click-only removes the feedback loop entirely and gives touch users --
 * who never fire a hover event -- the same behavior mouse users get,
 * instead of a second, hover-only code path.
 *
 * #expand-all-toggle is a single top-of-page control that opens (or closes)
 * every info-panel and info-disclosure on the page in one action,
 * independent of any individual toggle's own state.
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

  function panelFor(toggle) {
    return document.getElementById(toggle.getAttribute("aria-controls"));
  }

  document.addEventListener("click", function (event) {
    const toggle = event.target.closest(".info-toggle");
    if (!toggle) return;
    const panel = panelFor(toggle);
    if (!panel) return;
    if (toggle.getAttribute("aria-expanded") === "true") {
      closePanel(toggle, panel);
    } else {
      openPanel(toggle, panel);
    }
  });

  const expandAllToggle = document.getElementById("expand-all-toggle");
  if (expandAllToggle) {
    expandAllToggle.addEventListener("click", function () {
      const expanding = expandAllToggle.getAttribute("aria-pressed") !== "true";
      document.querySelectorAll(".info-toggle").forEach(function (toggle) {
        const panel = panelFor(toggle);
        if (!panel) return;
        if (expanding) {
          openPanel(toggle, panel);
        } else {
          closePanel(toggle, panel);
        }
      });
      document.querySelectorAll(".info-disclosure").forEach(function (disclosure) {
        disclosure.open = expanding;
      });
      expandAllToggle.setAttribute("aria-pressed", String(expanding));
      expandAllToggle.classList.toggle("is-active", expanding);
      expandAllToggle.textContent = expanding ? "Collapse all descriptions" : "Expand all descriptions";
    });
  }
})();
