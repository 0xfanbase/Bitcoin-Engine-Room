/* rain.js -- Digital Rain: canvas glyph-trail effect, and the site's only
 * background. BTC Engine Room is now a single instrument (director ruling,
 * CLAUDE.md Section 6 rule 1): "a green-phosphor terminal watching the
 * Bitcoin network, standing in a room full of falling code."
 *
 * Per frame: (1) a translucent near-black fill over the whole canvas is
 * what creates the fading trails passively -- no per-glyph alpha
 * bookkeeping; (2) each active column steps by exactly one row at a time
 * (never a smooth continuous fall -- the film's rain is stepped, and
 * variable per-column intervals are what produce the speed variation);
 * (3) a short hot-head gradient (new glyph -> head2 -> trail) trails each
 * step; (4) a small fraction of older, already-settled trail glyphs
 * occasionally flicker to a different character in place, matching the
 * film's most recognizable secondary property; (5) rare "burst" columns
 * draw one brightness step brighter for their first few glyphs after
 * respawning. On a genuine new-block arrival (the page's one theatrical
 * *event*, alongside the odometer and Block Rail -- CLAUDE.md rule 2),
 * density and burst odds ramp up for ~2s and decay back over ~2s: a surge,
 * never a loop.
 *
 * No shadowBlur/text-shadow anywhere, ever (rule 6) -- color contrast alone
 * reads as glow. Capped at 20fps -- the choppiness IS the aesthetic, not a
 * compromise (raising it would smooth away the film's stepped look and
 * cost more battery for a worse effect). Paused on hidden tab. Fully
 * disabled under prefers-reduced-motion -- the site's one on/off signal;
 * there is no separate in-app rocker (removed 2026-07-09, owner request).
 *
 * IP note (director ruling): the falling-glyph-on-black trope is generic
 * and unprotectable, reimplemented thousands of times since 1999 without
 * incident -- but the film's own specific glyph typeface is a protected
 * derivative-work risk, so this draws half-width katakana + digits from
 * ordinary system font stacks only, never a "Matrix code font." Public
 * name is "Digital Rain," always -- never the film's title, anywhere in
 * UI copy, code comments, or element ids.
 */
(function () {
  "use strict";

  const DIGITS = "0123456789";
  let KATAKANA = "";
  for (let cp = 0xff66; cp <= 0xff9d; cp++) KATAKANA += String.fromCharCode(cp);
  const GLYPHS = DIGITS + KATAKANA;

  const FONT_SIZE = 16;
  const COLUMN_WIDTH = 20;
  const FRAME_MS = 50; // 20fps cap -- deliberate, not a budget compromise
  const ACTIVE_FRACTION = 0.9;
  const REACTIVATE_CHANCE = 0.02; // per-frame odds a dormant column restarts
  const MUTATE_CHANCE = 0.015; // per-frame odds an established trail glyph flickers
  const HOT_COLUMN_CHANCE = 1 / 40; // odds a respawning column runs "hot" for a few glyphs
  const HOT_GLYPH_COUNT = 3;
  const HISTORY_LENGTH = 10; // trail cells eligible for in-place mutation
  const SURGE_HOLD_MS = 2000;
  const SURGE_DECAY_MS = 2000;

  const FONT_STRING = FONT_SIZE + 'px "MS Gothic", "Osaka-Mono", "Noto Sans Mono CJK JP", monospace';
  // Mobile browsers fire `resize` with only a small height delta when the
  // URL bar collapses/expands on scroll (typically 50-100px) -- honoring
  // that like a real resize would wipe the canvas bitmap (any
  // width/height reassignment clears it) and rebuild the whole rain mid-
  // scroll, the single most common mobile gesture. Only a genuine width
  // change or a height change bigger than this gets a real reset.
  const HEIGHT_JITTER_PX = 150;

  let canvas = null;
  let ctx = null;
  let columns = [];
  let intervalId = null;
  let running = false;
  let surgeStartMs = null;
  let lastWidth = 0;
  let lastHeight = 0;
  // Colors are read from CSS custom properties once, not every draw() call
  // (20x/sec, forever): this is a single-identity site with no theme
  // switch at runtime, so they never change after start() reads them --
  // getComputedStyle() forces a style recalc, real CPU cost repeated
  // needlessly on every frame on a low-power mobile CPU.
  let cachedFade = null;
  let cachedColors = null;

  function reducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // Adaptive yield, not a toggle: extends the reduced-motion family rather
  // than reviving the removed on/off rocker (CLAUDE.md Section 6 rule 9's
  // dated note). An always-on 20fps full-viewport canvas paint has a real
  // battery/thermal/data cost that a plugged-in desktop never pays but a
  // phone in someone's hand does -- so the instrument declines to start (or
  // stops if already running) when the device itself signals it's under
  // pressure, the same way it already declines under prefers-reduced-motion.
  let lowBattery = false; // best-effort, updated asynchronously by watchBattery()

  function dataSaverActive() {
    const conn = navigator.connection || navigator.webkitConnection || navigator.mozConnection;
    if (conn && conn.saveData) return true;
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-data: reduce)").matches);
  }

  function shouldYield() {
    return reducedMotion() || dataSaverActive() || lowBattery;
  }

  function watchBattery() {
    if (!navigator.getBattery) return; // not supported on most browsers -- best-effort only
    navigator
      .getBattery()
      .then((battery) => {
        const update = () => {
          lowBattery = battery.level < 0.2 && !battery.charging;
          sync();
        };
        update();
        battery.addEventListener("levelchange", update);
        battery.addEventListener("chargingchange", update);
      })
      .catch(() => {});
  }

  function readColorTokens() {
    const style = getComputedStyle(document.documentElement);
    cachedFade = style.getPropertyValue("--rain-fade").trim() || "rgba(2, 8, 4, 0.055)";
    cachedColors = {
      head: style.getPropertyValue("--rain-head").trim() || "#e6ffe6",
      head2: style.getPropertyValue("--rain-head2").trim() || "#9dffb0",
      trail: style.getPropertyValue("--rain-trail").trim() || "#00b33c",
    };
  }

  function randomGlyph() {
    return GLYPHS[(Math.random() * GLYPHS.length) | 0];
  }

  function pickSpeedTier() {
    // Weighted roughly 50/35/15 -- most columns step every tick, some every
    // other tick, a few lag every third. Variable stepping, not variable
    // per-frame distance, is what reads as the film's rain.
    const r = Math.random();
    if (r < 0.5) return 1;
    if (r < 0.85) return 2;
    return 3;
  }

  function makeColumn() {
    return {
      active: Math.random() < ACTIVE_FRACTION,
      y: Math.random() * -400,
      speedTier: pickSpeedTier(),
      tickCounter: 0,
      history: [], // {y, glyph}, oldest first, capped at HISTORY_LENGTH
      hotRemaining: 0,
    };
  }

  function resize() {
    if (!canvas) return;
    const width = window.innerWidth;
    const height = window.innerHeight;
    const isFirstResize = lastWidth === 0 && lastHeight === 0;
    if (!isFirstResize && width === lastWidth && Math.abs(height - lastHeight) <= HEIGHT_JITTER_PX) {
      return; // URL-bar jitter, not a real resize -- see HEIGHT_JITTER_PX comment above
    }
    lastWidth = width;
    lastHeight = height;
    canvas.width = width;
    canvas.height = height;
    // Resizing a canvas resets its whole drawing-context state, font
    // included -- must be reapplied every real resize, just not every frame.
    ctx.font = FONT_STRING;
    const colCount = Math.ceil(canvas.width / COLUMN_WIDTH);
    const next = [];
    for (let i = 0; i < colCount; i++) next.push(columns[i] || makeColumn());
    columns = next;
  }

  function surgeIntensity() {
    if (surgeStartMs == null) return 0;
    const elapsed = performance.now() - surgeStartMs;
    if (elapsed <= SURGE_HOLD_MS) return 1;
    if (elapsed <= SURGE_HOLD_MS + SURGE_DECAY_MS) return 1 - (elapsed - SURGE_HOLD_MS) / SURGE_DECAY_MS;
    surgeStartMs = null;
    return 0;
  }

  function stepColumn(col, colors) {
    // Hot-head gradient: the entry from 2 steps ago finalizes to trail
    // color, 1 step ago moves to the mid-bright "neck" color, then the new
    // glyph is drawn at near-white. Older entries beyond this window just
    // keep fading passively via the whole-canvas overlay.
    const len = col.history.length;
    if (len >= 1) {
      const prev = col.history[len - 1];
      ctx.fillStyle = col.hotRemaining > 0 ? "#ffffff" : colors.head2;
      ctx.fillText(prev.glyph, col.x, prev.y);
    }
    if (len >= 2) {
      const prior = col.history[len - 2];
      ctx.fillStyle = colors.trail;
      ctx.fillText(prior.glyph, col.x, prior.y);
    }

    const glyph = randomGlyph();
    ctx.fillStyle = col.hotRemaining > 0 ? "#ffffff" : colors.head;
    ctx.fillText(glyph, col.x, col.y);
    col.history.push({ y: col.y, glyph });
    if (col.history.length > HISTORY_LENGTH) col.history.shift();
    if (col.hotRemaining > 0) col.hotRemaining -= 1;

    col.y += FONT_SIZE;
  }

  function draw() {
    if (!ctx || !canvas) return;
    const colors = cachedColors;
    ctx.fillStyle = cachedFade;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    const surge = surgeIntensity();
    const effectiveReactivate = REACTIVATE_CHANCE + (1 - REACTIVATE_CHANCE) * surge;
    const effectiveHotChance = HOT_COLUMN_CHANCE * (1 + 3 * surge); // quadrupled at full surge

    columns.forEach((col, i) => {
      col.x = i * COLUMN_WIDTH;

      if (!col.active) {
        if (Math.random() < effectiveReactivate) {
          col.active = true;
          col.y = Math.random() * -100;
          col.speedTier = pickSpeedTier();
          col.history = [];
          col.hotRemaining = Math.random() < effectiveHotChance ? HOT_GLYPH_COUNT : 0;
        }
        return;
      }

      // Mutation pass: established trail glyphs (outside the 2-entry hot
      // window still finalizing) occasionally flicker to a new character,
      // in place, at their already-decaying brightness -- the film's
      // trails shimmer, they don't just fall.
      const settledCount = Math.max(col.history.length - 2, 0);
      for (let h = 0; h < settledCount; h++) {
        if (Math.random() < MUTATE_CHANCE) {
          col.history[h].glyph = randomGlyph();
          ctx.fillStyle = colors.trail;
          ctx.fillText(col.history[h].glyph, col.x, col.history[h].y);
        }
      }

      col.tickCounter += 1;
      if (col.tickCounter < col.speedTier) return;
      col.tickCounter = 0;

      stepColumn(col, colors);

      if (col.y > canvas.height + FONT_SIZE && Math.random() < 0.02) {
        col.y = Math.random() * -100;
        col.history = [];
        col.speedTier = pickSpeedTier();
        col.active = Math.random() < ACTIVE_FRACTION + (1 - ACTIVE_FRACTION) * surge;
        col.hotRemaining = Math.random() < effectiveHotChance ? HOT_GLYPH_COUNT : 0;
      }
    });
  }

  function start() {
    if (running || shouldYield()) return;
    if (!canvas) {
      canvas = document.getElementById("digital-rain-canvas");
      if (!canvas) return;
      ctx = canvas.getContext("2d");
      readColorTokens();
    }
    resize();
    running = true;
    intervalId = setInterval(() => {
      if (document.hidden) return;
      draw();
    }, FRAME_MS);
  }

  function stop() {
    running = false;
    if (intervalId) {
      clearInterval(intervalId);
      intervalId = null;
    }
    if (ctx && canvas) ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  function sync() {
    if (shouldYield()) stop();
    else start();
  }

  window.addEventListener("resize", () => {
    if (running) resize();
  });

  document.addEventListener("ber:block", () => {
    surgeStartMs = performance.now();
  });
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (mq.addEventListener) mq.addEventListener("change", sync);
    const dataMq = window.matchMedia("(prefers-reduced-data: reduce)");
    if (dataMq.addEventListener) dataMq.addEventListener("change", sync);
  }
  const conn = navigator.connection || navigator.webkitConnection || navigator.mozConnection;
  if (conn && conn.addEventListener) conn.addEventListener("change", sync);
  watchBattery();

  // Starts immediately rather than waiting for ber:booted: this canvas is
  // static markup already in the DOM by the time this deferred script
  // runs, with no dependency on any fetched data. The rain is the cheapest
  // thing on the page to show -- it shouldn't sit gated behind app.js's
  // health.json fetch (or, before that fix, the ~3MB of history files boot
  // used to wait on) just because it happened to listen for the same
  // "booted" event as modules that genuinely need that data.
  sync();
})();
