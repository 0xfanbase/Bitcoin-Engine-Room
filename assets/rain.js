/* rain.js -- Digital Rain (matrix theme only): canvas glyph-trail effect.
 *
 * Director spec (CLAUDE.md Section 6 rule 9): one <canvas>, fixed/inset:0,
 * behind all content. Per frame: (1) a translucent near-black fill over the
 * whole canvas is what creates the fading trails -- no per-glyph alpha
 * bookkeeping; (2) for each active column, redraw the previous head glyph
 * in trail green, then draw a new head glyph in near-white. That two-step
 * is what produces the signature white-hot head / phosphor trail.
 *
 * No shadowBlur (color contrast alone reads as glow -- rule 6's glow budget
 * stays status-lamps-plus-rain-heads, nothing on DOM text). No
 * backdrop-filter anywhere. Capped at 20fps, paused on hidden tab, fully
 * disabled under prefers-reduced-motion (screensaver test governs the
 * engine theme; this theme is the deliberate, opt-in exception).
 */
(function () {
  "use strict";

  const DIGITS = "0123456789";
  let KATAKANA = "";
  for (let cp = 0xff66; cp <= 0xff9d; cp++) KATAKANA += String.fromCharCode(cp);
  const GLYPHS = DIGITS + KATAKANA;

  const FONT_SIZE = 16;
  const COLUMN_WIDTH = 20;
  const FRAME_MS = 50; // 20fps cap
  const ACTIVE_FRACTION = 0.6;

  let canvas = null;
  let ctx = null;
  let columns = [];
  let intervalId = null;
  let running = false;

  function reducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function isMatrixTheme() {
    return document.documentElement.dataset.theme === "matrix";
  }

  function randomGlyph() {
    return GLYPHS[(Math.random() * GLYPHS.length) | 0];
  }

  function makeColumn() {
    return {
      active: Math.random() < ACTIVE_FRACTION,
      y: Math.random() * -400,
      speed: 0.5 + Math.random() * 0.5,
      prevGlyph: null,
      prevY: null,
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

  function draw() {
    if (!ctx || !canvas) return;
    const style = getComputedStyle(document.documentElement);
    const fade = style.getPropertyValue("--rain-fade").trim() || "rgba(2, 8, 4, 0.07)";
    const headColor = style.getPropertyValue("--rain-head").trim() || "#e6ffe6";
    const trailColor = style.getPropertyValue("--rain-trail").trim() || "#00b33c";

    ctx.fillStyle = fade;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.font = FONT_SIZE + 'px "MS Gothic", "Osaka-Mono", monospace';

    columns.forEach((col, i) => {
      if (!col.active) {
        // A dormant column has no other path back to life -- without this,
        // each column can only ever turn inactive (never reactivate), so the
        // whole rain decays to nothing within a couple of minutes. Give it
        // the same per-frame odds of restarting as an active column has of
        // going dormant, so the population reaches a steady state instead.
        if (Math.random() < 0.02) {
          col.active = true;
          col.y = Math.random() * -100;
          col.prevGlyph = null;
        }
        return;
      }
      const x = i * COLUMN_WIDTH;

      if (col.prevGlyph !== null) {
        ctx.fillStyle = trailColor;
        ctx.fillText(col.prevGlyph, x, col.prevY);
      }

      const glyph = randomGlyph();
      ctx.fillStyle = headColor;
      ctx.fillText(glyph, x, col.y);
      col.prevGlyph = glyph;
      col.prevY = col.y;

      col.y += FONT_SIZE * col.speed;
      if (col.y > canvas.height + FONT_SIZE && Math.random() < 0.02) {
        col.y = Math.random() * -100;
        col.prevGlyph = null;
        col.active = Math.random() < ACTIVE_FRACTION;
      }
    });
  }

  function start() {
    if (running || reducedMotion() || !isMatrixTheme()) return;
    if (!canvas) {
      canvas = document.getElementById("matrix-rain-canvas");
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

  function syncToTheme() {
    if (isMatrixTheme() && !reducedMotion()) start();
    else stop();
  }

  window.addEventListener("resize", () => {
    if (running) resize();
  });

  document.addEventListener("ber:theme-changed", syncToTheme);
  document.addEventListener("ber:booted", syncToTheme);
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (mq.addEventListener) mq.addEventListener("change", syncToTheme);
  }
})();
