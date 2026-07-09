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
 * disabled under prefers-reduced-motion, and user-togglable via the Rain
 * ON/OFF rocker (localStorage `ber_rain`, default on) -- committing the
 * entire visual identity to an animated background is only responsible
 * if a visitor can stop the animation without losing the site (rule 2's
 * screensaver-test exemption is deliberate and opt-out-able).
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
  const RAIN_STORAGE_KEY = "ber_rain";

  let canvas = null;
  let ctx = null;
  let columns = [];
  let intervalId = null;
  let running = false;
  let surgeStartMs = null;

  function reducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function rainPreference() {
    const stored = localStorage.getItem(RAIN_STORAGE_KEY);
    return stored !== "off"; // default on
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
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
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
    const style = getComputedStyle(document.documentElement);
    const fade = style.getPropertyValue("--rain-fade").trim() || "rgba(2, 8, 4, 0.055)";
    const colors = {
      head: style.getPropertyValue("--rain-head").trim() || "#e6ffe6",
      head2: style.getPropertyValue("--rain-head2").trim() || "#9dffb0",
      trail: style.getPropertyValue("--rain-trail").trim() || "#00b33c",
    };

    ctx.fillStyle = fade;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.font = FONT_SIZE + 'px "MS Gothic", "Osaka-Mono", "Noto Sans Mono CJK JP", monospace';

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
    if (running || reducedMotion() || !rainPreference()) return;
    if (!canvas) {
      canvas = document.getElementById("digital-rain-canvas");
      if (!canvas) return;
      ctx = canvas.getContext("2d");
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
    if (rainPreference() && !reducedMotion()) start();
    else stop();
  }

  function setRainOn(on) {
    localStorage.setItem(RAIN_STORAGE_KEY, on ? "on" : "off");
    sync();
    document.dispatchEvent(new CustomEvent("ber:rain-changed", { detail: { on } }));
  }

  window.BER = window.BER || {};
  window.BER.setRainOn = setRainOn;
  window.BER.isRainOn = rainPreference;

  window.addEventListener("resize", () => {
    if (running) resize();
  });

  document.addEventListener("ber:booted", sync);
  document.addEventListener("ber:block", () => {
    surgeStartMs = performance.now();
  });
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (mq.addEventListener) mq.addEventListener("change", sync);
  }
})();
