/* health.js -- renders the Engine Health panel from data/health.json
 * (per-source status lamps + last snapshot age) and the audit panel from
 * data/audit/latest.json (pass/fail with expandable detail), per spec
 * Section 9. Reads window.BER.health, already fetched once by app.js's
 * boot() -- no duplicate network request; audit.json is fetched here since
 * nothing else needs it.
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

  // Mitigation for GitHub Actions' best-effort cron scheduling (spec
  // Section 6): flag the last snapshot age in red once it's suspiciously old.
  const SNAPSHOT_AGE_RED_FLAG_HOURS = 48;

  function fetchJSON(path) {
    return fetch(path, { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      return r.json();
    });
  }

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

  function renderHealthGrid() {
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

    const ageHours = (Date.now() - Date.parse(health.generated_at)) / 3600000;
    const ageText = ageHours < 1 ? "<1h ago" : `${Math.round(ageHours)}h ago`;
    summary.innerHTML = "";
    summary.append(`${okCount}/${entries.length} sources OK · last snapshot `);
    const ageSpan = document.createElement("span");
    ageSpan.textContent = ageText;
    if (ageHours >= SNAPSHOT_AGE_RED_FLAG_HOURS) ageSpan.classList.add("is-overdue");
    summary.append(ageSpan);

    grid.innerHTML = "";
    entries.forEach(([metric, record]) => {
      grid.appendChild(renderRow(metric, record));
    });
  }

  function renderFinding(finding) {
    const li = document.createElement("li");
    li.dataset.severity = finding.severity;
    const check = document.createElement("span");
    check.className = "finding-check";
    check.textContent = `[${finding.severity}] ${finding.check}` + (finding.metric ? ` (${finding.metric})` : "");
    li.appendChild(check);
    li.append(finding.detail);
    return li;
  }

  async function renderAuditPanel() {
    const chip = document.getElementById("audit-result-chip");
    const text = document.getElementById("audit-result-text");
    const generatedAt = document.getElementById("audit-generated-at");
    const findingsList = document.getElementById("audit-findings");
    if (!chip || !text || !findingsList) return;

    let audit;
    try {
      audit = await fetchJSON("data/audit/latest.json");
    } catch (e) {
      text.textContent = "unavailable";
      return;
    }

    chip.dataset.status = audit.result;
    text.textContent = audit.result;
    if (generatedAt) generatedAt.textContent = "audited " + audit.generated_at;

    findingsList.innerHTML = "";
    if (!audit.findings.length) {
      const li = document.createElement("li");
      li.textContent = "No findings -- all checks clean.";
      findingsList.appendChild(li);
      return;
    }
    audit.findings.forEach((f) => findingsList.appendChild(renderFinding(f)));
  }

  document.addEventListener("ber:booted", () => {
    renderHealthGrid();
    renderAuditPanel();
  });
})();
