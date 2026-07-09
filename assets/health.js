/* health.js -- renders the Engine Health panel from data/health.json.
 * Reads window.BER.health, already fetched once by app.js's boot() --
 * no duplicate network request. Full audit pass/fail detail (spec Section
 * 11) lands in P5; this is the per-source status-lamp table only.
 */
(function () {
  "use strict";

  const METRIC_LABELS = {
    price_daily: "Price",
    hashrate_daily: "Hash rate",
    difficulty_daily: "Difficulty",
    supply_daily: "Supply",
    fng_daily: "Fear & Greed",
  };

  function renderRow(metric, record) {
    const row = document.createElement("div");
    row.className = "health-row";

    const name = document.createElement("span");
    name.className = "metric-name";
    name.textContent = METRIC_LABELS[metric] || metric;

    const chip = document.createElement("span");
    chip.className = "status-chip";
    const status = record.status === "OK" ? "LIVE" : "STALE";
    chip.dataset.status = status;
    const dot = document.createElement("span");
    dot.className = "status-dot";
    const label = document.createElement("span");
    label.textContent = record.source ? record.source : record.status;
    chip.appendChild(dot);
    chip.appendChild(label);

    row.appendChild(name);
    row.appendChild(chip);
    return row;
  }

  function render() {
    const grid = document.getElementById("health-grid");
    const summary = document.getElementById("health-summary");
    if (!grid || !summary) return;

    const health = window.BER && window.BER.health;
    if (!health) {
      summary.textContent = "Health report unavailable.";
      return;
    }

    const entries = Object.entries(health.metrics || {});
    const okCount = entries.filter(([, r]) => r.status === "OK").length;
    summary.textContent = `${okCount}/${entries.length} sources OK · generated ${health.generated_at}`;

    grid.innerHTML = "";
    entries.forEach(([metric, record]) => {
      grid.appendChild(renderRow(metric, record));
    });
  }

  document.addEventListener("ber:booted", render);
})();
