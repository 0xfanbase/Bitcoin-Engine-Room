/* live.js -- WebSocket odometer + REST polling + client-side failover.
 *
 * Spec Section 7:
 * 1. WebSocket first (blocks/mempool-blocks/stats). Auto-reconnect with
 *    capped backoff (max 60s); after 3 failed reconnects, degrade to REST
 *    polling for block height too.
 * 2. REST polling, staggered: price+fees every 60s, difficulty-adjustment
 *    every 10min. Single in-flight request per endpoint. Paused when
 *    document.hidden.
 * 3. Client price failover: mempool.space -> CoinGecko -> Coinbase. All
 *    failing keeps whatever app.js already painted from committed data,
 *    with a STALE chip.
 * 4. Every gauge carries LIVE (green) / DELAYED (amber, REST-only) / STALE
 *    (amber, committed snapshot) -- this module owns block height, price,
 *    fees, mempool (the true "live" metrics); app.js/health.js own the
 *    daily-cadence gauges' chips from health.json.
 */
(function () {
  "use strict";

  const BER = (window.BER = window.BER || {});

  const WS_URL = "wss://mempool.space/api/v1/ws";
  const REST_BASE = "https://mempool.space/api";
  const PRICE_POLL_MS = 60_000;
  const DIFFICULTY_POLL_MS = 10 * 60_000;
  const WS_MAX_BACKOFF_MS = 60_000;
  const WS_MAX_RECONNECT_ATTEMPTS_BEFORE_DEGRADE = 3;
  const NEXT_HALVING_HEIGHT = 1_050_000;
  const AVG_BLOCK_MINUTES = 10;

  let ws = null;
  let wsReconnectAttempts = 0;
  let wsDegraded = false;
  let lastBlockHeight = null;
  let lastBlockTimestamp = null;
  let heightPollTimer = null;

  // ---------- odometer / block height ----------

  function renderOdometer(height, { flash } = {}) {
    const digits = String(height).padStart(7, "0").split("");
    const el = document.getElementById("odometer");
    if (!el) return;
    const nodes = el.querySelectorAll(".odometer-digit");
    digits.forEach((d, i) => {
      if (nodes[i]) nodes[i].textContent = d;
    });
    if (flash && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      nodes.forEach((n) => {
        n.classList.add("flash");
        setTimeout(() => n.classList.remove("flash"), 600);
      });
    }
    renderHalvingCountdown(height);
  }

  function renderHalvingCountdown(height) {
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
  }

  function onNewBlock(height, timestampSec) {
    const isFirstPaint = lastBlockHeight === null;
    lastBlockHeight = height;
    lastBlockTimestamp = timestampSec;
    renderOdometer(height, { flash: !isFirstPaint });
    const captionEl = document.getElementById("odometer-caption");
    if (captionEl) captionEl.textContent = "block height · block found " + timeAgo(timestampSec);
  }

  function timeAgo(timestampSec) {
    if (!timestampSec) return "just now";
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - timestampSec));
    if (seconds < 60) return seconds + "s ago";
    if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
    return Math.floor(seconds / 3600) + "h ago";
  }

  // ---------- WebSocket ----------

  function connectWebSocket() {
    if (document.hidden) return; // reconnect will be retried on visibilitychange
    try {
      ws = new WebSocket(WS_URL);
    } catch (e) {
      scheduleReconnect();
      return;
    }

    ws.addEventListener("open", () => {
      wsReconnectAttempts = 0;
      wsDegraded = false;
      stopHeightPolling();
      ws.send(JSON.stringify({ action: "want", data: ["blocks", "stats", "mempool-blocks"] }));
      setEngineStatus("LIVE", "engine running");
    });

    ws.addEventListener("message", (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch (e) {
        return;
      }
      handleWsMessage(msg);
    });

    ws.addEventListener("close", scheduleReconnect);
    ws.addEventListener("error", () => ws && ws.close());
  }

  function handleWsMessage(msg) {
    // Live-verified 2026-07 (direct wss:// connection, this sandbox's proxy
    // doesn't support WS upgrades so it couldn't be checked through it): the
    // real payload's key is "blocks" (a plural array of recent blocks), never
    // a singular "block". "mempoolInfo" carries the accurate full mempool
    // count (`.size`); "mempool-blocks" is only the next few projected block
    // templates and must never overwrite mempoolInfo's count with its much
    // smaller nTx sum -- only used as a fallback when mempoolInfo is absent.
    if (Array.isArray(msg.blocks) && msg.blocks.length) {
      const tip = msg.blocks[msg.blocks.length - 1];
      if (tip && typeof tip.height === "number") onNewBlock(tip.height, tip.timestamp);
    }
    if (msg.mempoolInfo) {
      renderMempool(msg.mempoolInfo.size);
    } else if (Array.isArray(msg["mempool-blocks"]) && msg["mempool-blocks"].length) {
      const totalTx = msg["mempool-blocks"].reduce((sum, b) => sum + (b.nTx || 0), 0);
      if (totalTx) renderMempool(totalTx);
    }
  }

  function scheduleReconnect() {
    if (ws) {
      ws.removeEventListener("close", scheduleReconnect);
      ws = null;
    }
    wsReconnectAttempts += 1;
    if (wsReconnectAttempts > WS_MAX_RECONNECT_ATTEMPTS_BEFORE_DEGRADE) {
      degradeToPolling();
      return;
    }
    const delay = Math.min(1000 * 2 ** wsReconnectAttempts, WS_MAX_BACKOFF_MS);
    setTimeout(connectWebSocket, delay);
  }

  function degradeToPolling() {
    wsDegraded = true;
    setEngineStatus("DELAYED", "REST-only (WebSocket unavailable)");
    startHeightPolling();
    // Keep trying to recover the WebSocket in the background at the capped interval.
    setTimeout(connectWebSocket, WS_MAX_BACKOFF_MS);
  }

  function startHeightPolling() {
    if (heightPollTimer) return;
    const poll = async () => {
      try {
        const res = await fetch(`${REST_BASE}/blocks/tip/height`, { cache: "no-store" });
        if (!res.ok) throw new Error("bad status " + res.status);
        const height = parseInt(await res.text(), 10);
        onNewBlock(height, Math.floor(Date.now() / 1000));
      } catch (e) {
        // leave last known odometer value in place -- never blank it
      }
    };
    poll();
    heightPollTimer = setInterval(poll, PRICE_POLL_MS);
  }

  function stopHeightPolling() {
    if (heightPollTimer) {
      clearInterval(heightPollTimer);
      heightPollTimer = null;
    }
  }

  function renderMempool(count) {
    if (count == null) return;
    const el = document.getElementById("gauge-mempool");
    if (el) el.textContent = Number(count).toLocaleString("en-US");
    BER.setChip("chip-mempool", "LIVE", "live");
  }

  // ---------- REST polling: price (with client failover), fees, difficulty ----------

  async function fetchPriceWithFailover() {
    const chain = [
      { name: "mempool_space", fn: () => fetch(`${REST_BASE}/v1/prices`).then((r) => r.json()).then((d) => d.USD) },
      {
        name: "coingecko",
        fn: () =>
          fetch("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd")
            .then((r) => r.json())
            .then((d) => d.bitcoin.usd),
      },
      {
        name: "coinbase",
        fn: () =>
          fetch("https://api.coinbase.com/v2/prices/BTC-USD/spot")
            .then((r) => r.json())
            .then((d) => parseFloat(d.data.amount)),
      },
    ];

    for (const source of chain) {
      try {
        const value = await source.fn();
        if (typeof value === "number" && value > 0) return { value, source: source.name };
      } catch (e) {
        continue;
      }
    }
    return null;
  }

  let priceInFlight = false;
  async function pollPrice() {
    if (priceInFlight || document.hidden) return;
    priceInFlight = true;
    try {
      const result = await fetchPriceWithFailover();
      if (result) {
        document.getElementById("stat-price").textContent = BER.formatUSD(result.value);
        BER.setChip("chip-price", result.source === "mempool_space" ? "LIVE" : "DELAYED", result.source);
      }
      // On total failure, leave the committed-data STALE display from app.js untouched.
    } finally {
      priceInFlight = false;
    }
  }

  let feesInFlight = false;
  async function pollFees() {
    if (feesInFlight || document.hidden) return;
    feesInFlight = true;
    try {
      const res = await fetch(`${REST_BASE}/v1/fees/recommended`, { cache: "no-store" });
      if (!res.ok) throw new Error("bad status");
      const fees = await res.json();
      const el = document.getElementById("gauge-fees");
      if (el) el.textContent = `${fees.fastestFee} / ${fees.halfHourFee} / ${fees.economyFee}`;
      BER.setChip("chip-fees", "LIVE", "live");
    } catch (e) {
      BER.setChip("chip-fees", "STALE", "unavailable");
    } finally {
      feesInFlight = false;
    }
  }

  let difficultyInFlight = false;
  async function pollDifficultyAdjustment() {
    if (difficultyInFlight || document.hidden) return;
    difficultyInFlight = true;
    try {
      const res = await fetch(`${REST_BASE}/v1/difficulty-adjustment`, { cache: "no-store" });
      if (!res.ok) throw new Error("bad status");
      const adj = await res.json();
      const sub = document.getElementById("gauge-difficulty-sub");
      if (sub && typeof adj.progressPercent === "number") {
        const sign = adj.difficultyChange >= 0 ? "+" : "";
        sub.textContent = `retarget ${adj.progressPercent.toFixed(1)}% · ${sign}${adj.difficultyChange.toFixed(2)}%`;
      }
    } catch (e) {
      // leave the committed-data sub-text from app.js in place
    } finally {
      difficultyInFlight = false;
    }
  }

  // ---------- engine status lamp ----------

  function setEngineStatus(status, text) {
    const dot = document.getElementById("engine-status-dot");
    const label = document.getElementById("engine-status-text");
    if (!dot || !label) return;
    const color =
      status === "LIVE" ? "var(--ok)" : status === "DELAYED" ? "var(--warn)" : "var(--fail)";
    dot.style.background = color;
    label.textContent = text;
  }

  // ---------- lifecycle ----------

  function pauseAllPolling() {
    stopHeightPolling();
  }

  function resumeAllPolling() {
    if (wsDegraded) startHeightPolling();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      pauseAllPolling();
    } else {
      resumeAllPolling();
      pollPrice();
      pollFees();
    }
  });

  document.addEventListener("ber:booted", () => {
    connectWebSocket();
    pollPrice();
    pollFees();
    pollDifficultyAdjustment();
    setInterval(pollPrice, PRICE_POLL_MS);
    setInterval(pollFees, PRICE_POLL_MS);
    setInterval(pollDifficultyAdjustment, DIFFICULTY_POLL_MS);
  });
})();
