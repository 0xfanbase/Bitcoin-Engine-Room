// Unit tests for assets/model-math.js -- run with `node --test tests/js`
// (Node's built-in runner: no npm install, no build step).
const test = require("node:test");
const assert = require("node:assert/strict");
const M = require("../../assets/model-math.js");
const models = require("../../data/models.json");

const G = "2009-01-03";
const pl = models.power_law;
const close = (a, b, tol) => assert.ok(Math.abs(a - b) <= tol, `${a} not within ${tol} of ${b}`);

test("day/date round trip", () => {
  assert.equal(M.dayFromDate("2009-01-04", G), 1);
  assert.equal(M.isoFromDay(M.dayFromDate("2030-01-01", G), G), "2030-01-01");
});

test("modelAt reproduces the pipeline's own published projections", () => {
  for (const p of pl.projections) {
    const m = M.modelAt(M.dayFromDate(p.date, G), pl);
    close(m.trend, p.trend, p.trend * 1e-5);
    close(m.idle, p.floor, p.floor * 1e-4);
    close(m.redline, p.ceiling, p.ceiling * 1e-4);
    close(m.innerLow, p.inner_low, p.inner_low * 1e-4);
    close(m.innerHigh, p.inner_high, p.inner_high * 1e-4);
    assert.ok(m.idle < m.innerLow && m.innerLow < m.trend && m.trend < m.innerHigh && m.innerHigh < m.redline);
  }
});

test("modelAt matches models.json current trend price", () => {
  // a/b are published to 6 decimals, so allow a relative 1e-5 rounding gap.
  close(M.modelAt(M.dayFromDate(pl.current.date, G), pl).trend, pl.current.trend_price, pl.current.trend_price * 1e-5);
});

test("bandOffsets falls back to symmetric sigma only without empirical offsets", () => {
  const legacy = { params: { a: -17, b: 5.8, sigma: 0.3 }, bands: {} };
  assert.deepEqual(M.bandOffsets(legacy).outer, [-0.6, 0.6]);
  assert.equal(M.bandOffsets(legacy).empirical, false);
  assert.equal(M.bandOffsets(pl).empirical, true);
});

test("fanAt interpolates inside the published span and is null outside", () => {
  const fan = pl.trend_uncertainty.fan;
  const first = fan[0];
  const f = M.fanAt(first.day, fan);
  close(Math.log10(f.low), first.low, 1e-9);
  assert.equal(M.fanAt(first.day - 10, fan), null);
  assert.equal(M.fanAt(fan[fan.length - 1].day + 10, fan), null);
  const mid = M.fanAt((fan[3].day + fan[4].day) / 2, fan);
  assert.ok(mid.low < mid.mid && mid.mid < mid.high);
});

test("crossingDays inverts the model exactly", () => {
  const days = M.crossingDays(250000, pl);
  close(M.modelAt(days.trend, pl).trend, 250000, 0.01);
  close(M.modelAt(days.redline, pl).redline, 250000, 0.01);
  close(M.modelAt(days.idle, pl).idle, 250000, 0.01);
  assert.ok(days.redline < days.trend && days.trend < days.idle);
  assert.equal(M.dayWhenLineReaches(-5, 1, 1, 0), null);
});

test("every range preset sets both ends, and future presets reach past today", () => {
  const ctx = { todayDay: 6478, firstDay: 593, endDay: M.dayFromDate("2035-12-31", G), genesis: G };
  for (const r of M.RANGE_PRESETS) {
    const [lo, hi] = M.rangeBounds(r, ctx);
    assert.ok(lo >= ctx.firstDay - 400 && hi > ctx.todayDay, r);
  }
  const [lo1, hi1] = M.rangeBounds("1y", ctx);
  close(hi1 - ctx.todayDay, ctx.todayDay - lo1, 1e-9);
  assert.equal(M.rangeBounds("2030", ctx)[1], M.dayFromDate("2030-12-31", G));
  assert.deepEqual(M.rangeBounds("all", ctx), [ctx.firstDay, ctx.endDay]);
});

test("toX/fromX invert for both time scales", () => {
  for (const mode of ["log", "cal"]) close(M.fromX(M.toX(4321, mode), mode), 4321, 1e-6);
});

test("dollarTicks densify for narrow spans and stay inside the range", () => {
  assert.deepEqual(M.dollarTicks(0, 6), [0, 1, 2, 3, 4, 5, 6]);
  const mid = M.dollarTicks(4, 5.5);
  assert.ok(mid.includes(Number(Math.log10(2e4).toFixed(6))));
  const narrow = M.dollarTicks(4.8, 5.2);
  assert.ok(narrow.length >= 4);
  for (const t of narrow) assert.ok(t >= 4.8 - 1e-9 && t <= 5.2 + 1e-9);
});

test("formatDollarCompact / Full", () => {
  assert.equal(M.formatDollarCompact(0.0712), "$0.071");
  assert.equal(M.formatDollarCompact(250000), "$250K");
  assert.equal(M.formatDollarCompact(1890000), "$1.89M");
  assert.equal(M.formatDollarCompact(3e9), "$3B");
  assert.equal(M.formatDollarFull(85115.4), "$85,115");
  assert.equal(M.formatDollarFull(0.5), "$0.50");
});

test("yearTicks never places two labels closer than minGap", () => {
  const ticks = M.yearTicks(593, 9858, G, (d) => Math.log10(d) * 900, 44);
  for (let i = 1; i < ticks.length; i++) {
    assert.ok(Math.log10(ticks[i].day) * 900 - Math.log10(ticks[i - 1].day) * 900 >= 44);
  }
});

test("timeTicks switches to months for short windows", () => {
  const today = 6478;
  const ticks = M.timeTicks(today - 365, today + 365, G, (d) => d, 1);
  assert.ok(ticks.length >= 6);
  assert.ok(ticks.some((t) => /^[A-Z][a-z]{2} \d\d$/.test(t.label)));
});

test("movingAverage and toCsv", () => {
  assert.deepEqual(M.movingAverage([1, 2, 3, 4], 2), [null, 1.5, 2.5, 3.5]);
  assert.deepEqual(M.movingAverage([1, 2], 1), [1, 2]);
  assert.equal(M.toCsv(["a", "b"], [[1, 'x,"y"']]), 'a,b\n1,"x,""y"""\n');
});

test("view state round-trips and rejects junk", () => {
  const q = M.serializeViewState("?foo=1", { range: "2035", timeScale: "cal" });
  assert.deepEqual(M.parseViewState(q), { range: "2035", timeScale: "cal" });
  assert.match(q, /foo=1/);
  assert.deepEqual(M.parseViewState("?pl=evil&plt=<x>"), { range: null, timeScale: null });
});
