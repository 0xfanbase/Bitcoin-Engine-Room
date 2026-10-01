/* model-math.js -- pure, DOM-free math and formatting shared by charts.js
 * (2026-10-01 UX overhaul). Everything here is a plain function of its
 * inputs so it can be unit-tested under Node (`node --test tests/js`) with
 * no browser and no build step; in the page it attaches to window.BER.math.
 *
 * Units: `day` = days since genesis (float), price values in USD, "log"
 * values are log10(USD). Model inputs come straight from data/models.json's
 * power_law block -- this file never invents a number the pipeline didn't
 * publish, it only evaluates the published formula at a requested day.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else (root.BER = root.BER || {}).math = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const DAY_MS = 86400000;

  function dayFromDate(dateStr, genesis) {
    return Math.round((Date.parse(dateStr + "T00:00:00Z") - Date.parse(genesis + "T00:00:00Z")) / DAY_MS);
  }

  function dateFromDay(day, genesis) {
    return new Date(Date.parse(genesis + "T00:00:00Z") + Math.round(day) * DAY_MS);
  }

  function isoFromDay(day, genesis) {
    return dateFromDay(day, genesis).toISOString().slice(0, 10);
  }

  // ---------- model evaluation ----------

  function trendLog10(day, a, b) {
    return a + b * Math.log10(day);
  }

  // Band offsets (log10) from models.json -> power_law.bands. Falls back to
  // the pre-2026-10-01 symmetric +/-2 sigma / +/-1 sigma definition only if an
  // older models.json without empirical offsets is ever served (cached CDN
  // copy mid-deploy) -- never silently mixes the two.
  function bandOffsets(pl) {
    const bands = pl.bands || {};
    const sigma = pl.params.sigma;
    return {
      outer: bands.outer_offsets_log10 || [-2 * sigma, 2 * sigma],
      inner: bands.inner_offsets_log10 || [-sigma, sigma],
      empirical: Boolean(bands.outer_offsets_log10),
    };
  }

  function modelAt(day, pl) {
    const { a, b } = pl.params;
    const t = trendLog10(day, a, b);
    const off = bandOffsets(pl);
    return {
      day,
      trend: Math.pow(10, t),
      idle: Math.pow(10, t + off.outer[0]),
      innerLow: Math.pow(10, t + off.inner[0]),
      innerHigh: Math.pow(10, t + off.inner[1]),
      redline: Math.pow(10, t + off.outer[1]),
    };
  }

  // Bootstrap trend fan (log10 percentiles at log-spaced days). Linear
  // interpolation in log(day) between published points; null outside the
  // published span (the fan only exists from the last data day forward).
  function fanAt(day, fan) {
    if (!fan || fan.length < 2 || day < fan[0].day || day > fan[fan.length - 1].day) return null;
    const ld = Math.log10(day);
    for (let i = 1; i < fan.length; i++) {
      if (day <= fan[i].day) {
        const p = fan[i - 1];
        const q = fan[i];
        const span = Math.log10(q.day) - Math.log10(p.day);
        const w = span > 0 ? (ld - Math.log10(p.day)) / span : 0;
        const lerp = (k) => p[k] + (q[k] - p[k]) * w;
        return { low: Math.pow(10, lerp("low")), mid: Math.pow(10, lerp("mid")), high: Math.pow(10, lerp("high")) };
      }
    }
    return null;
  }

  function scenarioAt(day, scenario) {
    if (!scenario) return null;
    return Math.pow(10, trendLog10(day, scenario.a, scenario.b));
  }

  // Short-term path: nearest published step within half a step of `day`.
  function shortTermAt(day, shortTerm) {
    if (!shortTerm || !shortTerm.path || !shortTerm.path.length) return null;
    const half = (shortTerm.sample_days || 30) / 2;
    let best = null;
    for (const p of shortTerm.path) {
      const dist = Math.abs(p.day - day);
      if (dist <= half && (!best || dist < Math.abs(best.day - day))) best = p;
    }
    return best;
  }

  // Inverse: the day on which a model line (offset applied in log10 space)
  // reaches `price`. Returns null for a non-positive price or a slope <= 0.
  function dayWhenLineReaches(price, a, b, offsetLog10) {
    if (!(price > 0) || !(b > 0)) return null;
    return Math.pow(10, (Math.log10(price) - offsetLog10 - a) / b);
  }

  function crossingDays(price, pl) {
    const { a, b } = pl.params;
    const off = bandOffsets(pl);
    return {
      redline: dayWhenLineReaches(price, a, b, off.outer[1]),
      trend: dayWhenLineReaches(price, a, b, 0),
      idle: dayWhenLineReaches(price, a, b, off.outer[0]),
    };
  }

  // Share of history inside a band, for copy ("~95% of history").
  function pct(x, digits) {
    return (x * 100).toFixed(digits == null ? 0 : digits) + "%";
  }

  // ---------- view ranges ----------

  // Every preset sets BOTH ends of the time axis (director ruling 1): a range
  // chip that only crops history while the axis still runs to 2035 is what
  // made "1Y" show nine years of empty future.
  const RANGE_PRESETS = ["1y", "2030", "2035", "all"];

  function rangeBounds(range, ctx) {
    const { todayDay, firstDay, endDay, genesis } = ctx;
    const yearDays = 365.25;
    switch (range) {
      case "1y":
        return [todayDay - yearDays, todayDay + yearDays];
      case "2030":
        return [Math.max(firstDay, todayDay - 8 * yearDays), dayFromDate("2030-12-31", genesis)];
      case "2035":
        return [Math.max(firstDay, todayDay - 12 * yearDays), endDay];
      default:
        return [firstDay, endDay];
    }
  }

  // x-axis transform: "log" plots log10(day) (the model's native space, where
  // the power law is a straight line); "cal" plots plain days (calendar time,
  // where the future gets its true share of the width).
  function toX(day, mode) {
    return mode === "cal" ? day : Math.log10(day);
  }

  function fromX(x, mode) {
    return mode === "cal" ? x : Math.pow(10, x);
  }

  // ---------- axis ticks + formatting ----------

  // Price ticks in log10 space: whole decades for wide views, 1-2-5 per
  // decade for medium, 1..9 for narrow -- so a 1-year view still gets
  // readable gridlines instead of a single "$100K".
  function dollarTicks(minLog, maxLog) {
    const span = maxLog - minLog;
    const mults = span > 3.5 ? [1] : span > 1.2 ? [1, 2, 5] : [1, 1.5, 2, 3, 4, 5, 6, 7, 8, 9];
    const ticks = [];
    for (let dec = Math.floor(minLog) - 1; dec <= Math.ceil(maxLog); dec++) {
      for (const m of mults) {
        const v = dec + Math.log10(m);
        if (v >= minLog - 1e-9 && v <= maxLog + 1e-9) ticks.push(Number(v.toFixed(6)));
      }
    }
    return ticks;
  }

  // Instrument-style compact dollars (axis graduations, chips): $0.01, $25,
  // $1.5K, $250K, $1.2M, $3B. Up to 3 significant digits, trailing zeros cut.
  function formatDollarCompact(price) {
    if (!isFinite(price)) return "–";
    if (price < 1) return "$" + Number(price.toPrecision(2)).toString();
    const units = [
      [1e9, "B"],
      [1e6, "M"],
      [1e3, "K"],
    ];
    for (const [div, suffix] of units) {
      if (price >= div) return "$" + Number((price / div).toPrecision(3)).toString() + suffix;
    }
    return "$" + Number(price.toPrecision(3)).toString();
  }

  // Full-precision readout for tooltips/lookups: whole dollars above $10.
  function formatDollarFull(price) {
    if (!isFinite(price)) return "–";
    if (price < 10) return "$" + price.toFixed(2);
    return "$" + Math.round(price).toLocaleString("en-US");
  }

  // Year ticks, thinned so labels never collide: keep a tick only if it sits
  // at least `minGap` (in the same units as `toPos`) from the last kept one.
  function yearTicks(minDay, maxDay, genesis, toPos, minGap) {
    const start = dateFromDay(minDay, genesis).getUTCFullYear();
    const end = dateFromDay(maxDay, genesis).getUTCFullYear();
    const out = [];
    let lastPos = -Infinity;
    for (let y = start; y <= end + 1; y++) {
      const day = dayFromDate(`${y}-01-01`, genesis);
      if (day < minDay || day > maxDay) continue;
      const pos = toPos(day);
      if (pos - lastPos >= minGap) {
        out.push({ day, label: String(y) });
        lastPos = pos;
      }
    }
    return out;
  }

  // Year ticks for long windows; for windows under ~3 years, quarter (or
  // month, under a year) ticks labelled "Jan 2027"-style so a 1-year view
  // isn't left with only two labels.
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function timeTicks(minDay, maxDay, genesis, toPos, minGap) {
    const spanYears = (maxDay - minDay) / 365.25;
    if (spanYears >= 3) return yearTicks(minDay, maxDay, genesis, toPos, minGap);
    const step = spanYears >= 1.5 ? 3 : 1;
    const start = dateFromDay(minDay, genesis);
    let y = start.getUTCFullYear();
    let m = start.getUTCMonth();
    const out = [];
    let lastPos = -Infinity;
    for (let guard = 0; guard < 60; guard++) {
      const iso = `${y}-${String(m + 1).padStart(2, "0")}-01`;
      const day = dayFromDate(iso, genesis);
      if (day > maxDay) break;
      if (day >= minDay && m % step === 0) {
        const pos = toPos(day);
        if (pos - lastPos >= minGap) {
          out.push({ day, label: m === 0 ? String(y) : `${MONTHS[m]} ${String(y).slice(2)}` });
          lastPos = pos;
        }
      }
      m += 1;
      if (m === 12) {
        m = 0;
        y += 1;
      }
    }
    return out;
  }

  // ---------- misc ----------

  function movingAverage(values, window) {
    if (window <= 1) return values.slice();
    const out = new Array(values.length);
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      sum += values[i];
      if (i >= window) sum -= values[i - window];
      out[i] = i >= window - 1 ? sum / window : null;
    }
    return out;
  }

  function toCsv(header, rows) {
    const esc = (v) => {
      const s = v == null ? "" : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    return [header, ...rows].map((r) => r.map(esc).join(",")).join("\n") + "\n";
  }

  // Shareable chart state in the query string: ?pl=2030&plt=cal
  function parseViewState(search) {
    const params = new URLSearchParams(search || "");
    const range = params.get("pl");
    const t = params.get("plt");
    return {
      range: RANGE_PRESETS.includes(range) ? range : null,
      timeScale: t === "cal" || t === "log" ? t : null,
    };
  }

  function serializeViewState(search, state) {
    const params = new URLSearchParams(search || "");
    params.set("pl", state.range);
    params.set("plt", state.timeScale);
    return "?" + params.toString();
  }

  return {
    DAY_MS,
    RANGE_PRESETS,
    dayFromDate,
    dateFromDay,
    isoFromDay,
    trendLog10,
    bandOffsets,
    modelAt,
    fanAt,
    scenarioAt,
    shortTermAt,
    dayWhenLineReaches,
    crossingDays,
    pct,
    rangeBounds,
    toX,
    fromX,
    dollarTicks,
    formatDollarCompact,
    formatDollarFull,
    yearTicks,
    timeTicks,
    movingAverage,
    toCsv,
    parseViewState,
    serializeViewState,
  };
});
