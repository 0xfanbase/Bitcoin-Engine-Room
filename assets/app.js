/* app.js -- boot, theme switcher, shared formatters.
 *
 * Boot order matters for the "never blank gauges" rule (spec Section 10.3):
 * 1. Paint every gauge from the last COMMITTED data/history/*.json row +
 *    data/health.json immediately, so there's meaningful content even if
 *    the network is unavailable or live.js's fetches all fail.
 * 2. live.js then upgrades price/fees/mempool/block-height to real-time.
 * 3. health.js renders the Engine Health panel from the same health.json
 *    this module already fetched (shared via window.BER.health, no
 *    duplicate fetch).
 */
(function () {
  "use strict";

  const BER = (window.BER = window.BER || {});

  // ---------- formatters ----------

  BER.formatUSD = function (value) {
    if (value == null) return "—";
    return "$" + Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 });
  };

  BER.formatCompact = function (value, unit) {
    if (value == null) return "—";
    return Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 }) + (unit ? " " + unit : "");
  };

  BER.formatHashrate = function (eh) {
    if (eh == null) return "—";
    return Number(eh).toLocaleString("en-US", { maximumFractionDigits: 1 }) + " EH/s";
  };

  BER.formatDifficulty = function (raw) {
    if (raw == null) return "—";
    return (Number(raw) / 1e12).toLocaleString("en-US", { maximumFractionDigits: 2 }) + " T";
  };

  BER.formatSupply = function (btc) {
    if (btc == null) return "—";
    return (Number(btc) / 1e6).toLocaleString("en-US", { maximumFractionDigits: 3 }) + "M BTC";
  };

  BER.formatDate = function (isoDate) {
    if (!isoDate) return "—";
    return isoDate;
  };

  BER.daysAgo = function (isoDate) {
    if (!isoDate) return null;
    const then = new Date(isoDate + "T00:00:00Z").getTime();
    const now = Date.now();
    return Math.floor((now - then) / 86400000);
  };

  // ---------- status chip rendering ----------

  BER.setChip = function (id, status, label) {
    const el = document.getElementById(id);
    if (!el) return;
    el.dataset.status = status;
    const span = el.querySelector("span:last-child");
    if (span) span.textContent = label != null ? label : status;
  };

  // ---------- theme switcher ----------

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("ber_theme", theme);
    document.querySelectorAll(".theme-chip").forEach((chip) => {
      const isActive = chip.dataset.themeChoice === theme;
      chip.setAttribute("aria-pressed", String(isActive));
    });
  }

  function initThemeSwitcher() {
    const current = document.documentElement.dataset.theme || "engine";
    document.querySelectorAll(".theme-chip").forEach((chip) => {
      chip.setAttribute("aria-pressed", String(chip.dataset.themeChoice === current));
      chip.addEventListener("click", () => applyTheme(chip.dataset.themeChoice));
    });
  }

  BER.applyTheme = applyTheme;

  // ---------- boot: paint from committed data ----------

  async function fetchJSON(path) {
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  }

  function lastRow(doc) {
    return doc && doc.series && doc.series.length ? doc.series[doc.series.length - 1] : null;
  }

  // Daily-cadence gauges (hashrate/difficulty/supply/fng) are refreshed once
  // a day by fetch_snapshot.py, not continuously -- their chip reflects
  // health.json's status, not a live connection state (that's live.js's job
  // for price/fees/mempool/block height).
  function paintDailyChip(chipId, health) {
    if (!health) {
      BER.setChip(chipId, "STALE", "no data");
      return;
    }
    if (health.status === "OK") {
      BER.setChip(chipId, "DELAYED", "as of " + health.last_success_date);
    } else {
      const age = BER.daysAgo(health.stale_since);
      BER.setChip(chipId, "STALE", age != null ? `stale ${age}d` : "stale");
    }
  }

  async function boot() {
    initThemeSwitcher();

    let health = null;
    try {
      health = await fetchJSON("data/health.json");
    } catch (e) {
      console.warn("health.json unavailable", e);
    }
    BER.health = health;

    const metrics = ["price_daily", "hashrate_daily", "difficulty_daily", "supply_daily", "fng_daily"];
    const histories = {};
    await Promise.all(
      metrics.map(async (m) => {
        try {
          histories[m] = await fetchJSON(`data/history/${m}.json`);
        } catch (e) {
          console.warn(m + " unavailable", e);
        }
      })
    );
    BER.histories = histories;

    const priceRow = lastRow(histories.price_daily);
    if (priceRow) {
      document.getElementById("stat-price").textContent = BER.formatUSD(priceRow.value);
      const priceHealth = health && health.metrics && health.metrics.price_daily;
      // Price is a live.js-owned metric; paint committed value now with a
      // STALE chip, live.js will upgrade it if a live fetch succeeds.
      BER.setChip("chip-price", "STALE", "as of " + priceRow.date);
      void priceHealth;
    }

    const hashrateRow = lastRow(histories.hashrate_daily);
    if (hashrateRow) {
      document.getElementById("gauge-hashrate").textContent = BER.formatHashrate(hashrateRow.value);
      document.getElementById("gauge-hashrate-sub").textContent = "committed " + hashrateRow.date;
      paintDailyChip("chip-hashrate", health && health.metrics && health.metrics.hashrate_daily);
    }

    const difficultyRow = lastRow(histories.difficulty_daily);
    if (difficultyRow) {
      document.getElementById("gauge-difficulty").textContent = BER.formatDifficulty(difficultyRow.value);
      document.getElementById("gauge-difficulty-sub").textContent = "committed " + difficultyRow.date;
      paintDailyChip("chip-difficulty", health && health.metrics && health.metrics.difficulty_daily);
    }

    const supplyRow = lastRow(histories.supply_daily);
    if (supplyRow) {
      document.getElementById("gauge-supply").textContent = BER.formatSupply(supplyRow.value);
      document.getElementById("gauge-supply-sub").textContent = "committed " + supplyRow.date;
      paintDailyChip("chip-supply", health && health.metrics && health.metrics.supply_daily);
    }

    const fngRow = lastRow(histories.fng_daily);
    if (fngRow) {
      document.getElementById("gauge-fng").textContent = fngRow.value;
      document.getElementById("gauge-fng-sub").textContent = fngRow.classification + " · " + fngRow.date;
      paintDailyChip("chip-fng", health && health.metrics && health.metrics.fng_daily);
    }

    // Fees and mempool have no committed history (live-only per spec Section
    // 4/5) -- they stay at their placeholder STALE state until live.js
    // (or its absence) resolves them.

    document.dispatchEvent(new CustomEvent("ber:booted"));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
