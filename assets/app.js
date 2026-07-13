/* app.js -- boot, Digital Rain toggle wiring, H1 boot flourish, shared formatters.
 *
 * Boot order matters for the "never blank gauges" rule (spec Section 10.3):
 * 1. Paint every gauge from data/health.json's per-metric last_date/
 *    last_value (a small digest carried alongside the existing tip_height
 *    field, same mechanics) immediately, so there's meaningful content even
 *    if the network is unavailable or live.js's fetches all fail -- without
 *    downloading any of the five much larger data/history/*.json files,
 *    three of which nothing on the page reads beyond that same last row.
 * 2. ber:booted fires right after, not gated on any further download --
 *    live.js upgrades price/fees/mempool/block-height to real-time, and
 *    charts.js separately (and lazily) fetches the two full histories
 *    (price, Fear & Greed) it actually charts.
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

  BER.daysAgo = function (isoDate) {
    if (!isoDate) return null;
    const then = new Date(isoDate + "T00:00:00Z").getTime();
    const now = Date.now();
    return Math.floor((now - then) / 86400000);
  };

  // ---------- odometer digits + halving countdown (shared with live.js) ----------
  // Defined here (not live.js) and called by both this module's committed-
  // data fallback paint and live.js's real-time odometer updates, so the
  // halving-countdown math has exactly one implementation instead of two
  // copies that could drift apart.

  const NEXT_HALVING_HEIGHT = 1_050_000;
  const AVG_BLOCK_MINUTES = 10;

  BER.renderOdometerDigits = function (height) {
    const digits = String(height).padStart(7, "0").split("");
    const el = document.getElementById("odometer");
    if (!el) return;
    const nodes = el.querySelectorAll(".odometer-digit");
    digits.forEach((d, i) => {
      if (nodes[i]) nodes[i].textContent = d;
    });
  };

  BER.renderHalvingCountdown = function (height) {
    const blocksRemaining = Math.max(NEXT_HALVING_HEIGHT - height, 0);
    const el = document.getElementById("stat-halving-blocks");
    if (el) el.textContent = blocksRemaining.toLocaleString("en-US");

    const etaEl = document.getElementById("stat-halving-eta");
    if (etaEl) {
      if (blocksRemaining === 0) {
        etaEl.textContent = "any block now";
      } else {
        const minutesRemaining = blocksRemaining * AVG_BLOCK_MINUTES;
        const eta = new Date(Date.now() + minutesRemaining * 60_000);
        etaEl.textContent = eta.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }) + " (est.)";
      }
    }
  };

  // ---------- status chip rendering ----------

  BER.setChip = function (id, status, label) {
    const el = document.getElementById(id);
    if (!el) return;
    el.dataset.status = status;
    const span = el.querySelector("span:last-child");
    if (span) span.textContent = label != null ? label : status;
  };

  // ---------- H1 boot flourish ----------
  // One-time scramble-to-settle over <=700ms, never re-loops, skipped under
  // reduced-motion (director ruling). Latin caps + digits only -- this runs
  // in DOM text in IBM Plex Mono, which has no katakana coverage, and
  // never touches any numeral or data element.
  const SCRAMBLE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
  const SCRAMBLE_DURATION_MS = 700;

  function scrambleH1() {
    const el = document.getElementById("site-title");
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const target = el.textContent;
    const start = performance.now();
    function frame(now) {
      const progress = Math.min((now - start) / SCRAMBLE_DURATION_MS, 1);
      const revealCount = Math.floor(progress * target.length);
      let out = "";
      for (let i = 0; i < target.length; i++) {
        if (target[i] === " " || i < revealCount) out += target[i];
        else out += SCRAMBLE_CHARS[(Math.random() * SCRAMBLE_CHARS.length) | 0];
      }
      el.textContent = out;
      if (progress < 1) requestAnimationFrame(frame);
      else el.textContent = target;
    }
    requestAnimationFrame(frame);
  }

  // ---------- boot: paint from committed data ----------

  // Default cache mode (not "no-store"): health.json is a daily-immutable
  // committed file, so the browser's normal HTTP cache can serve repeat
  // same-day visits a 304 instead of a full re-download.
  async function fetchJSON(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  }

  // Daily-cadence gauges (hashrate/difficulty/supply/fng) are refreshed once
  // a day by fetch_snapshot.py, not continuously -- their chip reflects
  // health.json's status, not a live connection state (that's live.js's job
  // for price/fees/mempool/block height). A healthy once-a-day refresh gets
  // its own neutral DAILY status rather than the amber DELAYED used
  // elsewhere for genuinely degraded live metrics -- amber next to a
  // perfectly healthy gauge reads as "something's wrong" to a newcomer.
  // Every chip label leads with the status word itself (DAILY/STALE), same
  // pattern live.js's pollPrice() now uses (LIVE/DELAYED + source), so the
  // vocabulary is consistent everywhere a chip appears on the page.
  function paintDailyChip(chipId, health) {
    if (!health) {
      BER.setChip(chipId, "STALE", "STALE · no data");
      return;
    }
    if (health.status === "OK") {
      BER.setChip(chipId, "DAILY", "DAILY · as of " + health.last_success_date);
    } else {
      const age = BER.daysAgo(health.stale_since);
      BER.setChip(chipId, "STALE", age != null ? `STALE · stale ${age}d` : "STALE · stale");
    }
  }

  async function boot() {
    localStorage.removeItem("ber_theme"); // one-time cleanup, nothing reads this key anymore
    localStorage.removeItem("ber_rain"); // one-time cleanup: the rain ON/OFF rocker was removed, 2026-07-09
    scrambleH1();

    let health = null;
    try {
      health = await fetchJSON("data/health.json");
    } catch (e) {
      console.warn("health.json unavailable", e);
    }
    BER.health = health;

    // Block height/halving stats: paint from the daily pipeline's committed
    // tip_height immediately, same "never blank" treatment the 5 history
    // metrics below already get, so the odometer and halving countdown show
    // real numbers instead of dashes/"—" forever if mempool.space is
    // unreachable when the page loads. live.js is free to upgrade this to a
    // live reading exactly as it already does for price -- its own
    // onNewBlock() already guards first-paint vs. a real change via its
    // private `lastBlockHeight` (starts null regardless of what we paint
    // here), so painting the odometer here first cannot cause a false
    // flash/rail-arrival/rain-surge "new block" event once live.js connects
    // and sees the same height.
    if (health && health.tip_height && typeof health.tip_height.height === "number") {
      const height = health.tip_height.height;
      BER.renderOdometerDigits(height);
      BER.renderHalvingCountdown(height);
      const captionEl = document.getElementById("odometer-caption");
      if (captionEl) captionEl.textContent = "block height · as of " + health.tip_height.date;
    }

    // Gauges paint straight from health.json's per-metric last_date/
    // last_value (added alongside tip_height for exactly this reason) --
    // it's already fetched above, so this costs zero extra network requests.
    // Previously this painted from each metric's full data/history/*.json
    // file, which meant downloading ~3MB combined (three of the five files
    // entirely unused beyond their own last row) before the gauges, the
    // WebSocket connection, or any polling could even start -- see
    // IMPROVEMENT_BACKLOG.md. Full history is still fetched, but lazily and
    // only for the two metrics (price, Fear & Greed) charts.js actually
    // charts in full.
    const metricHealth = (m) => health && health.metrics && health.metrics[m];

    const priceHealth = metricHealth("price_daily");
    if (priceHealth && priceHealth.last_value != null) {
      document.getElementById("stat-price").textContent = BER.formatUSD(priceHealth.last_value);
      // Price is a live.js-owned metric; paint committed value now with a
      // STALE chip, live.js will upgrade it if a live fetch succeeds.
      BER.setChip("chip-price", "STALE", "STALE · as of " + priceHealth.last_date);
    }

    // gauge-*-sub used to read "committed <date>" here, but that's now a
    // straight duplicate of the DAILY chip's own "DAILY · as of <date>"
    // text sitting directly below it -- dropped rather than just reworded
    // ("recorded <date>") so the same date isn't shown twice in a row; the
    // chip is the single source of truth for that date now. Difficulty's
    // sub is still overwritten with live retarget progress by live.js's
    // pollDifficultyAdjustment() when that succeeds -- unaffected here.
    const hashrateHealth = metricHealth("hashrate_daily");
    if (hashrateHealth && hashrateHealth.last_value != null) {
      document.getElementById("gauge-hashrate").textContent = BER.formatHashrate(hashrateHealth.last_value);
      document.getElementById("gauge-hashrate-sub").textContent = ""; // no longer duplicates the DAILY chip's date -- see comment above
      paintDailyChip("chip-hashrate", hashrateHealth);
    }

    const difficultyHealth = metricHealth("difficulty_daily");
    if (difficultyHealth && difficultyHealth.last_value != null) {
      document.getElementById("gauge-difficulty").textContent = BER.formatDifficulty(difficultyHealth.last_value);
      document.getElementById("gauge-difficulty-sub").textContent = ""; // pollDifficultyAdjustment() fills this with live retarget progress if it succeeds
      paintDailyChip("chip-difficulty", difficultyHealth);
    }

    const supplyHealth = metricHealth("supply_daily");
    if (supplyHealth && supplyHealth.last_value != null) {
      document.getElementById("gauge-supply").textContent = BER.formatSupply(supplyHealth.last_value);
      document.getElementById("gauge-supply-sub").textContent = ""; // no longer duplicates the DAILY chip's date -- see comment above
      paintDailyChip("chip-supply", supplyHealth);
    }

    const fngHealth = metricHealth("fng_daily");
    if (fngHealth && fngHealth.last_value != null) {
      document.getElementById("gauge-fng").textContent = fngHealth.last_value;
      document.getElementById("gauge-fng-sub").textContent = fngHealth.last_classification + " · " + fngHealth.last_date;
      paintDailyChip("chip-fng", fngHealth);
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
