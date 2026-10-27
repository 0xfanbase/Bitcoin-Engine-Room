/**
 * Forecast honesty: Idle is not labeled P10 past the conformal horizon.
 * Runs the real forecast-ui.js painters against a fixture document.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    className: "",
    id: "",
    title: "",
    colSpan: 1,
    dataset: {},
    childNodes: [],
    _text: "",
    appendChild(c) {
      this.childNodes.push(c);
      return c;
    },
    set textContent(v) {
      this._text = v == null ? "" : String(v);
      this.childNodes.length = 0;
    },
    get textContent() {
      if (this.childNodes.length) return this.childNodes.map((c) => c.textContent || "").join("");
      return this._text;
    },
  };
  return el;
}

function textOf(el) {
  return el ? el.textContent : "";
}

test("corridor horizons render Idle/Cruise/Redline and conformal stays P10", async () => {
  const src = fs.readFileSync(path.join(__dirname, "../../assets/forecast-ui.js"), "utf8");
  assert.equal(src.includes("innerHTML"), false);

  const byId = {};
  const listeners = {};
  const options = [];
  const document = {
    getElementById: (id) => byId[id] || null,
    createElement: (tag) => makeEl(tag),
    createTextNode: (t) => ({ textContent: String(t) }),
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener: (ev, fn) => {
      (listeners[ev] = listeners[ev] || []).push(fn);
    },
  };
  const ids = [
    "forecast-ranges",
    "forecast-disclaimer",
    "forecast-ribbon-disclaimer",
    "forecast-ensemble-tbody",
    "forecast-matured-tbody",
    "forecast-learning-summary",
    "forecast-fan-chart",
    "forecast-fan-stats",
    "forecast-fan-headline",
    "forecast-learning-chart",
    "forecast-cagr-tbody",
    "forecast-walkforward-miss",
  ];
  for (const id of ids) {
    const el = makeEl("div");
    el.id = id;
    byId[id] = el;
  }

  const doc = {
    as_of: "2026-10-27",
    spot: 100000,
    method: { disclaimer: "Beyond 4y this is a power-law residual ribbon, not a probability. Not a confidence interval." },
    horizons: [
      {
        id: "1y",
        label: "1 year",
        days: 365,
        in_live_ledger: true,
        band_source: "conformal@365d",
        band_labels: { low: "P10", mid: "P50", high: "P90", kind: "conformal" },
        coverage_earned: true,
        conformal_indep_windows: 12.8,
        p10: 50000,
        p50: 110000,
        p90: 180000,
        target_date: "2027-10-27",
      },
      {
        id: "4y",
        label: "4 years",
        days: 1461,
        in_live_ledger: true,
        band_source: "conformal@1461d",
        band_labels: { low: "P10", mid: "P50", high: "P90", kind: "conformal" },
        coverage_earned: false,
        conformal_indep_windows: 2.5,
        p10: 80000,
        p50: 200000,
        p90: 400000,
        target_date: "2030-10-27",
      },
      {
        id: "10y",
        label: "10 years",
        days: 3652,
        in_live_ledger: false,
        band_source: "idle_redline_corridor",
        band_labels: { low: "Idle", mid: "Cruise", high: "Redline", kind: "corridor" },
        coverage_earned: false,
        conformal_indep_windows: null,
        p10: 400000,
        p50: 900000,
        p90: 2000000,
        idle: 400000,
        redline: 2000000,
        cruise: 900000,
        cagr_to_idle: 0.15,
        cagr_to_cruise: 0.25,
        target_date: "2036-10-27",
        caveat: "Scenario only — corridor is Models Idle/Redline.",
      },
      {
        id: "30y",
        label: "30 years",
        days: 10957,
        in_live_ledger: false,
        band_source: "idle_redline_corridor",
        p10: 5000000,
        p50: 14000000,
        p90: 40000000,
        cruise: 14000000,
        target_date: "2056-10-27",
      },
    ],
    fan: [
      { date: "2026-10-27", days: 0, p10: 90000, p50: 100000, p90: 110000, cruise: 100000, band_source: "asof_tight", band_labels: { low: "P10", mid: "P50", high: "P90", kind: "conformal" } },
      { date: "2030-10-27", days: 1461, p10: 80000, p50: 200000, p90: 400000, cruise: 180000, band_source: "conformal@1461d", band_labels: { low: "P10", mid: "P50", high: "P90", kind: "conformal" } },
      { date: "2032-10-27", days: 2192, p10: 200000, p50: 400000, p90: 800000, cruise: 350000, band_source: "taper_conformal_to_corridor", band_labels: { low: "Idle", mid: "P50", high: "Redline", kind: "taper" }, idle: 200000, redline: 800000 },
      { date: "2046-10-27", days: 7305, p10: 2000000, p50: 5000000, p90: 12000000, cruise: 5000000, band_source: "idle_redline_corridor", band_labels: { low: "Idle", mid: "Cruise", high: "Redline", kind: "corridor" }, idle: 2000000, redline: 12000000 },
    ],
    history: [{ date: "2026-10-20", price: 100000 }],
    ensemble: [
      { id: "idle_redline_cap", name: "Idle / Redline corridor", role: "P10/P90 for 10y & 30y only", trust_note: "stale" },
      { id: "closed_loop", name: "Ledger closed-loop nudge", role: "should be replaced", trust_note: "not a CI recalibration" },
    ],
    learning: {
      matured: [],
      miss_by_year: [{ year: 2024, mae_factor: 1.2 }],
      backtest_horizons: [{ horizon_days: 365, trend_mae_factor: 1.994 }],
      ledger_note: "Closed-loop applies shrunk ledger bias when matured sample clears min_n.",
      correction: { status: "inactive_waiting_for_matured", n_matured_total: 0 },
    },
  };

  const window = {
    BER: {},
    echarts: {
      init() {
        return {
          setOption(opt) {
            options.push(opt);
          },
          resize() {},
          getOption() {
            return options[options.length - 1];
          },
        };
      },
    },
    addEventListener: () => {},
    location: { hash: "" },
    document,
  };
  const context = {
    window,
    document,
    console,
    Promise,
    setTimeout,
    setInterval: () => 0,
    clearInterval: () => {},
    requestAnimationFrame: (f) => f(),
    fetch: async () => ({ ok: true, json: async () => doc }),
  };
  context.window.document = document;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(src, context);
  listeners.DOMContentLoaded[0]();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));

  const cards = byId["forecast-ranges"].childNodes.map((c) => c.textContent);
  const y1 = cards.find((t) => t.includes("1 year"));
  const y4 = cards.find((t) => t.includes("4 years"));
  const y10 = cards.find((t) => t.includes("10 years"));
  const y30 = cards.find((t) => t.includes("30 years"));
  assert.match(y1, /P10/);
  assert.match(y1, /P90/);
  assert.doesNotMatch(y1, /Idle/);
  assert.match(y1, /indep 12\.8/);
  assert.doesNotMatch(y1, /unearned/);
  assert.match(y4, /P10/);
  assert.match(y4, /unearned/);
  assert.match(y4, /indep 2\.5/);
  assert.match(y10, /Idle/);
  assert.match(y10, /Cruise/);
  assert.match(y10, /Redline/);
  assert.doesNotMatch(y10, /\bP10\b/);
  assert.doesNotMatch(y10, /\bP90\b/);
  assert.match(y10, /unearned/);
  assert.match(y10, /indep —/);
  assert.match(y30, /Idle/);
  assert.match(y30, /Redline/);
  assert.doesNotMatch(y30, /\bP10\b/);
  assert.match(y30, /unearned/);

  assert.equal(
    textOf(byId["forecast-ribbon-disclaimer"]).trim(),
    "Beyond 4y this is a power-law residual ribbon, not a probability."
  );
  const scar = textOf(byId["forecast-walkforward-miss"]);
  assert.match(scar, /1y walk-forward miss/);
  assert.match(scar, /×1\.99/);
  assert.match(scar, /about ×2/);
  const sum = textOf(byId["forecast-learning-summary"]);
  assert.match(sum, /no live recalibration/);
  assert.doesNotMatch(sum, /applies shrunk/);

  const stack = textOf(byId["forecast-ensemble-tbody"]);
  assert.match(stack, /Idle \/ Redline for 10y, 20y, and 30y/);
  assert.doesNotMatch(stack, /P10\/P90 for 10y/);
  assert.match(stack, /no live recalibration/);
  assert.doesNotMatch(stack, /CI recalibration/);

  const cagr = textOf(byId["forecast-cagr-tbody"]);
  assert.match(cagr, /10 years/);
  assert.match(cagr, /30 years/);
  assert.match(cagr, /15\.0%/);
  assert.match(cagr, /25\.0%/);
  assert.doesNotMatch(cagr, /P10/);

  const names = options[0].series.map((s) => s.name);
  assert.ok(names.includes("P10") && names.includes("Idle") && names.includes("Redline"));
  const byName = Object.fromEntries(options[0].series.map((s) => [s.name, s.data]));
  assert.equal(byName.P10.length >= 2, true);
  assert.equal(byName.P10.some((row) => row[0] === "2046-10-27"), false);
  assert.equal(byName.Idle.some((row) => row[0] === "2046-10-27"), true);
  assert.equal(byName.Idle.some((row) => row[0] === "2032-10-27"), true);
  const headline = textOf(byId["forecast-fan-headline"]);
  assert.match(headline, /Idle–Redline/);
  assert.doesNotMatch(headline, /P10–P90 envelope/);
});
