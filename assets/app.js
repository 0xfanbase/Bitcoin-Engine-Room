/* app.js -- boot, Digital Rain toggle wiring, H1 boot flourish, shared formatters.
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
      BER.setChip("chip-price", "STALE", "STALE · as of " + priceRow.date);
      void priceHealth;
    }

    // gauge-*-sub used to read "committed <date>" here, but that's now a
    // straight duplicate of the DAILY chip's own "DAILY · as of <date>"
    // text sitting directly below it -- dropped rather than just reworded
    // ("recorded <date>") so the same date isn't shown twice in a row; the
    // chip is the single source of truth for that date now. Difficulty's
    // sub is still overwritten with live retarget progress by live.js's
    // pollDifficultyAdjustment() when that succeeds -- unaffected here.
    const hashrateRow = lastRow(histories.hashrate_daily);
    if (hashrateRow) {
      document.getElementById("gauge-hashrate").textContent = BER.formatHashrate(hashrateRow.value);
      document.getElementById("gauge-hashrate-sub").textContent = ""; // no longer duplicates the DAILY chip's date -- see comment above
      paintDailyChip("chip-hashrate", health && health.metrics && health.metrics.hashrate_daily);
    }

    const difficultyRow = lastRow(histories.difficulty_daily);
    if (difficultyRow) {
      document.getElementById("gauge-difficulty").textContent = BER.formatDifficulty(difficultyRow.value);
      document.getElementById("gauge-difficulty-sub").textContent = ""; // pollDifficultyAdjustment() fills this with live retarget progress if it succeeds
      paintDailyChip("chip-difficulty", health && health.metrics && health.metrics.difficulty_daily);
    }

    const supplyRow = lastRow(histories.supply_daily);
    if (supplyRow) {
      document.getElementById("gauge-supply").textContent = BER.formatSupply(supplyRow.value);
      document.getElementById("gauge-supply-sub").textContent = ""; // no longer duplicates the DAILY chip's date -- see comment above
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
