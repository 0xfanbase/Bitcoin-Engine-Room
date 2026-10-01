/* charts.js -- PRICE MODELS section: Power Law Corridor (the hero), 4-Year
 * Cycle Overlay, Market Sentiment, Mayer Multiple / 200-week MA, and Track
 * Record. Apache ECharts (CDN, SRI-pinned). Colors come from the CSS custom
 * properties -- the design tokens stay the single source of truth.
 *
 * 2026-10-01 rebuild (owner-approved UX overhaul, director ruling in
 * docs/UX_OVERHAUL_DIRECTOR_RULING.md). All model math and formatting lives
 * in assets/model-math.js (window.BER.math) so it is unit-tested under Node;
 * this file only wires data into ECharts and the DOM.
 *
 * Everything here loads lazily on an IntersectionObserver watching the Price
 * Models section: the ECharts script (~340KB gz) and the history files the
 * charts need are not in the critical path of a visit that never scrolls
 * this far. See IMPROVEMENT_BACKLOG.md.
 */
(function () {
  "use strict";

  const BER = (window.BER = window.BER || {});
  const M = BER.math;
  const ECHARTS_CDN_URL = "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js";
  // Subresource Integrity: exact pinned version, so this hash never changes
  // until the pinned version does. A mismatch fails the load the same way a
  // network failure does (error state below), never a partial script.
  const ECHARTS_CDN_INTEGRITY = "sha384-Mx5lkUEQPM1pOJCwFtUICyX45KNojXbkWdYhkKUKsbv391mavbfoAmONbzkgYPzR";

  const charts = {};
  let modelsDoc = null;
  let backtestDoc = null;
  let ledgerDoc = null;
  let priceHistorySeries = [];
  let fngHistorySeries = [];

  const COARSE_POINTER = window.matchMedia("(pointer: coarse)").matches;
  const HOVER_CAPABLE = window.matchMedia("(hover: hover)").matches;
  const END_DATE = "2035-12-31";
  const MIN_WINDOW_DAYS = 45;
  const ZOOM_HINT_KEY = "ber_zoom_hint";

  // Power-law view state. `view` is null when a preset owns the window, or
  // {minDay, maxDay} after a manual zoom/pan. Default preset is "2030", not
  // "all" (director ruling 1): ALL-by-default is what made the future read
  // as missing.
  const urlState = M.parseViewState(window.location.search);
  const plState = {
    range: urlState.range || "2030",
    timeScale: urlState.timeScale || "log",
    view: null,
    focused: false,
  };
  let sentimentSmoothing = 7;

  // ---------- generic helpers ----------

  // Default (not "no-store") cache mode: these are daily-immutable committed
  // files, so the browser's normal HTTP cache (304s) saves re-downloads.
  function fetchJSON(path) {
    return fetch(path).then((r) => {
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      return r.json();
    });
  }

  function prefersReducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // Full font stack, not just its first family: a canvas given only
  // "IBM Plex Mono" falls back to the browser default (a serif) whenever the
  // web font isn't loaded at draw time. Charts also re-render once
  // document.fonts settles (see loadAndRender).
  function colorTokens() {
    const style = getComputedStyle(document.documentElement);
    const get = (name) => style.getPropertyValue(name).trim();
    return {
      ink: get("--ink"),
      inkDim: get("--ink-dim"),
      accent: get("--accent"),
      fail: get("--fail"),
      border: get("--panel-border"),
      fontData: get("--font-data") || "ui-monospace, monospace",
    };
  }

  function rgba(hex, alpha) {
    const h = hex.replace("#", "");
    const n = parseInt(h.length === 3 ? h.replace(/(.)/g, "$1$1") : h, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }

  // One shared tooltip look across every chart.
  function baseTooltip(colors, extra) {
    return Object.assign(
      {
        backgroundColor: "rgba(4, 16, 8, 0.96)",
        borderColor: colors.border,
        borderWidth: 1,
        textStyle: { color: colors.ink, fontFamily: colors.fontData, fontSize: 12 },
        confine: true,
      },
      extra
    );
  }

  function crossPointer(colors, labelFormatter) {
    return {
      type: "cross",
      snap: false,
      lineStyle: { color: colors.inkDim, opacity: 0.5, width: 1, type: [2, 3] },
      crossStyle: { color: colors.inkDim, opacity: 0.5, width: 1, type: [2, 3] },
      label: {
        backgroundColor: "rgba(4, 16, 8, 0.96)",
        borderColor: colors.border,
        borderWidth: 1,
        color: colors.ink,
        fontFamily: colors.fontData,
        fontSize: 10,
        formatter: labelFormatter,
      },
    };
  }

  function formatDateShort(d) {
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
    return el;
  }

  // Loading / error states (director ruling 11): a hairline frame with one
  // dim line of mono text; --fail only when a fetch genuinely failed.
  function setChartStatus(id, status, message) {
    const el = document.getElementById(id);
    if (!el) return;
    el.dataset.status = status;
    el.dataset.message = message || "";
  }

  function getOrInitChart(id) {
    if (!charts[id]) {
      const el = document.getElementById(id);
      if (!el || typeof echarts === "undefined") return null;
      charts[id] = echarts.init(el);
    }
    return charts[id];
  }

  // ======================================================================
  // Power Law Corridor (the hero)
  // ======================================================================

  function plContext() {
    const pl = modelsDoc.power_law;
    const genesis = pl.params.genesis_date;
    const firstDate = priceHistorySeries.length ? priceHistorySeries[0].date : pl.params.fit_start_date;
    return {
      pl,
      genesis,
      todayDay: M.dayFromDate(pl.current.date, genesis),
      firstDay: Math.max(M.dayFromDate(firstDate, genesis), 1),
      endDay: M.dayFromDate(END_DATE, genesis),
    };
  }

  function plWindow(ctx) {
    if (plState.view) return [plState.view.minDay, plState.view.maxDay];
    return M.rangeBounds(plState.range, ctx);
  }

  function clampWindow(minDay, maxDay, ctx) {
    let span = Math.max(maxDay - minDay, MIN_WINDOW_DAYS);
    const fullSpan = ctx.endDay - ctx.firstDay;
    span = Math.min(span, fullSpan);
    let lo = Math.max(minDay, ctx.firstDay);
    let hi = lo + span;
    if (hi > ctx.endDay) {
      hi = ctx.endDay;
      lo = hi - span;
    }
    return [lo, hi];
  }

  // Price rows as [day, log10] once per load (the series is ~6k rows).
  let plPriceCache = null;
  function plPricePoints(genesis) {
    if (!plPriceCache) {
      plPriceCache = priceHistorySeries
        .filter((r) => r.value > 0)
        .map((r) => [M.dayFromDate(r.date, genesis), Math.log10(r.value)]);
    }
    return plPriceCache;
  }

  function priceOnDay(day, genesis) {
    const pts = plPricePoints(genesis);
    let lo = 0;
    let hi = pts.length - 1;
    if (!pts.length || day < pts[0][0] - 1 || day > pts[hi][0] + 1) return null;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (pts[mid][0] < day) lo = mid + 1;
      else hi = mid;
    }
    const cand = [pts[lo], pts[lo - 1]].filter(Boolean);
    const best = cand.reduce((p, q) => (Math.abs(q[0] - day) < Math.abs(p[0] - day) ? q : p));
    return Math.abs(best[0] - day) <= 1 ? Math.pow(10, best[1]) : null;
  }

  function halvingDays(ctx) {
    const co = modelsDoc.cycle_overlay || {};
    const out = (co.halving_dates || []).map((d) => ({ day: M.dayFromDate(d, ctx.genesis), label: d, est: false }));
    if (co.next_halving_est_date) {
      const est = co.next_halving_est_date.length === 7 ? co.next_halving_est_date + "-15" : co.next_halving_est_date;
      out.push({ day: M.dayFromDate(est, ctx.genesis), label: co.next_halving_est_date, est: true });
    }
    return out;
  }

  function renderPowerLaw(colors) {
    if (!modelsDoc) return;
    const chart = getOrInitChart("power-law-chart");
    if (!chart) return;
    const ctx = plContext();
    const { pl, genesis } = ctx;
    const mode = plState.timeScale;
    const X = (day) => M.toX(day, mode);
    const [minDay, maxDay] = plWindow(ctx);
    const xMin = X(minDay);
    const xMax = X(maxDay);

    // Visible price (one neighbour either side so the line meets the edges).
    const allPrice = plPricePoints(genesis);
    let i0 = allPrice.findIndex((p) => p[0] >= minDay);
    if (i0 === -1) i0 = allPrice.length;
    let i1 = i0;
    while (i1 < allPrice.length && allPrice[i1][0] <= maxDay) i1++;
    const visiblePrice = allPrice.slice(Math.max(i0 - 1, 0), Math.min(i1 + 1, allPrice.length));
    const priceData = visiblePrice.map((p) => [X(p[0]), p[1]]);

    // Model curves sampled evenly in screen space, whichever time scale.
    const N = 220;
    const sampleDays = [];
    for (let i = 0; i <= N; i++) sampleDays.push(M.fromX(xMin + ((xMax - xMin) * i) / N, mode));
    const off = M.bandOffsets(pl);
    const { a, b } = pl.params;
    const curve = (offset) => sampleDays.map((d) => [X(d), M.trendLog10(d, a, b) + offset]);
    const idle = curve(off.outer[0]);
    const innerLow = curve(off.inner[0]);
    const trend = curve(0);
    const innerHigh = curve(off.inner[1]);
    const redline = curve(off.outer[1]);

    // Trend-uncertainty fan: future only.
    const fan = pl.trend_uncertainty && pl.trend_uncertainty.fan;
    const fanDays = sampleDays.filter((d) => M.fanAt(d, fan));
    const fanLow = fanDays.map((d) => [X(d), Math.log10(M.fanAt(d, fan).low)]);
    const fanHigh = fanDays.map((d) => [X(d), Math.log10(M.fanAt(d, fan).high)]);

    // Recent-window scenario: drawn from its own fit start onward.
    const sc = pl.scenario;
    const scStart = sc ? M.dayFromDate(sc.fit_start_date, genesis) : Infinity;
    const scenarioData = sc ? sampleDays.filter((d) => d >= scStart).map((d) => [X(d), Math.log10(M.scenarioAt(d, sc))]) : [];

    // <=1-year short-term path (trend + mean-reverting residual).
    const st = pl.short_term && pl.short_term.path ? pl.short_term.path : [];
    const stAnchor = [X(ctx.todayDay), Math.log10(pl.current.price)];
    const stVisible = st.filter((p) => p.day >= minDay && p.day <= maxDay);
    const stCenter = stVisible.length ? [stAnchor, ...stVisible.map((p) => [X(p.day), Math.log10(p.center)])] : [];

    const projections = (pl.projections || [])
      .map((p) => ({ ...p, day: M.dayFromDate(p.date, genesis) }))
      .filter((p) => p.day >= minDay && p.day <= maxDay);

    const cycleTops = (pl.cycle_tops || [])
      .map((t) => ({ ...t, day: M.dayFromDate(t.date, genesis) }))
      .filter((t) => t.day >= minDay && t.day <= maxDay);

    const halvings = halvingDays(ctx).filter((h) => h.day >= minDay && h.day <= maxDay);

    // y auto-fit to everything that can be visible in this window.
    const ys = [];
    priceData.forEach((p) => ys.push(p[1]));
    idle.forEach((p) => ys.push(p[1]));
    redline.forEach((p) => ys.push(p[1]));
    fanLow.forEach((p) => ys.push(p[1]));
    fanHigh.forEach((p) => ys.push(p[1]));
    let yMin = Math.min(...ys);
    let yMax = Math.max(...ys);
    const pad = Math.max((yMax - yMin) * 0.04, 0.02);
    yMin -= pad;
    yMax += pad;
    const yTicks = M.dollarTicks(yMin, yMax);

    // Year ticks thinned by real pixel spacing so labels never collide.
    const plotWidth = Math.max(chart.getWidth() - 110, 120);
    const pxPerUnit = plotWidth / (xMax - xMin || 1);
    const years = M.timeTicks(minDay, maxDay, genesis, (d) => X(d) * pxPerUnit, 44);
    const yearLabel = new Map(years.map((t) => [X(t.day).toFixed(6), t.label]));

    const accent = colors.accent;
    const ink = colors.ink;
    const inkDim = colors.inkDim;
    const narrow = chart.getWidth() < 560;

    const polygonSeries = (name, upper, lower, fill, z) => ({
      name,
      type: "custom",
      silent: true,
      z,
      itemStyle: { color: legendColor[name] || fill },
      data: upper.length ? [0] : [],
      renderItem: (params, api) => {
        const pts = [];
        for (let i = 0; i < upper.length; i++) pts.push(api.coord(upper[i]));
        for (let i = lower.length - 1; i >= 0; i--) pts.push(api.coord(lower[i]));
        return { type: "polygon", shape: { points: pts }, style: { fill } };
      },
      tooltip: { show: false },
    });

    // Legend swatches in the site's own palette (ECharts' default rainbow
    // palette is off-identity: accent is the only data hue).
    const legendColor = {
      "Idle–Redline": rgba(accent, 0.55),
      "Trend range": rgba(accent, 0.8),
      "12-mo path": inkDim,
      "Since-2017 fit": inkDim,
      Halvings: inkDim,
      "Cycle tops": accent,
    };
    // Lines never show hover dots: the axis pointer + tooltip carry values.
    const line = (o) =>
      Object.assign({ type: "line", symbol: "none", showSymbol: false, emphasis: { disabled: true }, labelLayout: { moveOverlap: "shiftY" }, itemStyle: { color: legendColor[o.name] || accent } }, o);

    const edgeLabel = (text, color, keepNarrow) => ({ show: !narrow || Boolean(keepNarrow), formatter: text, color, fontFamily: colors.fontData, fontSize: narrow ? 9 : 10 });

    const series = [
      polygonSeries("Idle–Redline", redline, idle, rgba(accent, 0.1), 1),
      polygonSeries("Idle–Redline", innerHigh, innerLow, rgba(accent, 0.18), 1),
      line({ name: "Idle–Redline", data: redline, silent: true, lineStyle: { opacity: 0 }, endLabel: edgeLabel("Redline", inkDim), z: 2 }),
      line({ name: "Idle–Redline", data: idle, silent: true, lineStyle: { opacity: 0 }, endLabel: edgeLabel("Idle", inkDim), z: 2 }),
      polygonSeries("Trend range", fanHigh, fanLow, rgba(accent, 0.07), 2),
      line({ name: "Trend range", data: fanHigh, silent: true, lineStyle: { color: accent, width: 1, type: [4, 4], opacity: 0.6 }, z: 3 }),
      line({ name: "Trend range", data: fanLow, silent: true, lineStyle: { color: accent, width: 1, type: [4, 4], opacity: 0.6 }, z: 3 }),
      line({ name: "12-mo path", data: stCenter, silent: true, lineStyle: { color: inkDim, width: 1.25, type: [3, 3] }, z: 4 }),
      line({
        name: "Since-2017 fit",
        data: scenarioData,
        showSymbol: false,
        silent: true,
        lineStyle: { color: inkDim, width: 1, type: [1, 3] },
        endLabel: edgeLabel(sc ? sc.label : "", inkDim),
        z: 4,
      }),
      line({
        name: "Cruise",
        data: trend,
        showSymbol: false,
        silent: true,
        lineStyle: { color: accent, width: 1.5 },
        endLabel: edgeLabel("Cruise", accent, true),
        z: 5,
      }),
      line({
        name: "Price",
        data: priceData,
        showSymbol: false,
        silent: true,
        lineStyle: { color: ink, width: 1.75 },
        z: 6,
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { type: [2, 3], color: inkDim, opacity: 0.5, width: 1 },
          data: ctx.todayDay >= minDay && ctx.todayDay <= maxDay ? [{ xAxis: X(ctx.todayDay) }] : [],
        },
        markPoint: {
          silent: true,
          symbol: "circle",
          symbolSize: 8,
          itemStyle: { color: ink, borderColor: accent, borderWidth: 1.5 },
          label: { show: false },
          data: ctx.todayDay >= minDay && ctx.todayDay <= maxDay ? [{ coord: [X(ctx.todayDay), Math.log10(pl.current.price)] }] : [],
        },
      }),
      line({
        name: "Halvings",
        data: [],
        silent: true,
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { color: inkDim, opacity: 0.35, width: 1, type: [2, 4] },
          data: halvings.map((h) => ({ xAxis: X(h.day) })),
        },
      }),
      {
        name: "Projections",
        type: "scatter",
        data: projections.map((p) => [X(p.day), Math.log10(p.trend)]),
        symbol: "diamond",
        symbolSize: 7,
        silent: true,
        itemStyle: { color: "transparent", borderColor: accent, borderWidth: 1.5 },
        z: 7,
      },
      {
        name: "Cycle tops",
        type: "scatter",
        data: cycleTops.map((t) => ({
          value: [X(t.day), Math.log10(t.price)],
          itemStyle: t.confirmed ? undefined : { opacity: 0.5, borderType: "dashed" },
        })),
        symbol: "circle",
        symbolSize: 8,
        silent: true,
        itemStyle: { color: "transparent", borderColor: accent, borderWidth: 1.5, opacity: 0.85 },
        z: 7,
      },
    ];

    const legendItems = ["Idle–Redline", "Trend range", "12-mo path", "Since-2017 fit", "Halvings", "Cycle tops"];

    chart.setOption(
      {
        backgroundColor: "transparent",
        animation: false,
        textStyle: { fontFamily: colors.fontData, color: inkDim },
        grid: { left: 8, right: narrow ? 44 : 92, top: narrow ? 62 : 34, bottom: 28, containLabel: true },
        legend: {
          data: legendItems.map((name) => ({ name, itemStyle: { color: legendColor[name] }, lineStyle: { color: legendColor[name] } })),
          top: 0,
          right: 0,
          left: narrow ? 0 : "auto",
          type: "plain",
          itemWidth: 14,
          itemHeight: 8,
          itemGap: 10,
          textStyle: { color: inkDim, fontSize: 11, fontFamily: colors.fontData },
          inactiveColor: colors.border,
          pageIconColor: inkDim,
          pageTextStyle: { color: inkDim },
        },
        xAxis: {
          type: "value",
          min: xMin,
          max: xMax,
          axisLine: { lineStyle: { color: colors.border } },
          axisTick: { customValues: years.map((t) => X(t.day)), lineStyle: { color: colors.border } },
          axisLabel: {
            color: inkDim,
            customValues: years.map((t) => X(t.day)),
            formatter: (v) => yearLabel.get(Number(v).toFixed(6)) || "",
          },
          splitLine: { show: false },
          axisPointer: { label: { formatter: (p) => formatDateShort(M.dateFromDay(M.fromX(p.value, mode), genesis)) } },
        },
        yAxis: {
          type: "value",
          min: yMin,
          max: yMax,
          axisLine: { lineStyle: { color: colors.border } },
          axisTick: { customValues: yTicks },
          axisLabel: { color: inkDim, customValues: yTicks, formatter: (v) => M.formatDollarCompact(Math.pow(10, v)) },
          splitLine: { show: false },
          axisPointer: { label: { formatter: (p) => M.formatDollarCompact(Math.pow(10, p.value)) } },
        },
        // Gridlines drawn as a markLine set on an empty series: ECharts'
        // splitLine ignores customValues, so a 1-2-5 grid needs this.
        series: series.concat([
          {
            name: "_grid",
            type: "line",
            data: [],
            silent: true,
            markLine: {
              silent: true,
              symbol: "none",
              label: { show: false },
              lineStyle: { color: colors.border, opacity: 0.45, width: 1, type: "solid" },
              data: yTicks.map((v) => ({ yAxis: v })),
            },
            z: 0,
          },
        ]),
        tooltip: baseTooltip(colors, {
          trigger: "axis",
          axisPointer: crossPointer(colors),
          formatter: (params) => powerLawTooltip(params, ctx, mode),
        }),
        toolbox: {
          show: true,
          itemSize: 0,
          showTitle: false,
          right: -100,
          feature: { dataZoom: { yAxisIndex: "none", brushStyle: { borderColor: inkDim, borderWidth: 1, color: rgba(accent, 0.06) } } },
        },
        // No ECharts `inside` dataZoom on the hero: its axis min/max ARE the
        // current window, so ECharts' own wheel/drag zoom could never widen
        // or pan, and on touch it swallowed vertical page scrolls. Wheel,
        // pinch and drag are handled in initPowerLawGestures() instead; the
        // toolbox's box-zoom still drives a `datazoom` event.
      },
      // Merge (not notMerge): a full replace on every zoom step disposed the
      // tooltip mid-hover (ECharts then threw on its pending reposition).
      // Series are replaced wholesale; legend selection persists by merge.
      { replaceMerge: ["series"] }
    );

    // Drag-to-box-zoom on fine pointers (director ruling 2). On touch, a
    // drag pans and a pinch zooms instead.
    if (!COARSE_POINTER) {
      chart.dispatchAction({ type: "takeGlobalCursor", key: "dataZoomSelect", dataZoomSelectActive: true });
    }

    setChartStatus("power-law-chart", "ready");
    updatePowerLawText(ctx);
    updatePowerLawAria(ctx, minDay, maxDay);
  }

  function powerLawTooltip(params, ctx, mode) {
    if (!params || !params.length) return "";
    const { pl, genesis } = ctx;
    const xValue = params[0].axisValue;
    const day = M.fromX(xValue, mode);
    if (!(day > 0)) return "";
    const m = M.modelAt(day, pl);
    const future = day > ctx.todayDay + 0.5;
    const rows = [];
    const row = (label, value, dim) => rows.push(`<span style="opacity:${dim ? 0.7 : 1}">${label}</span> ${value}`);
    row("Redline", M.formatDollarFull(m.redline));
    row("Cruise ", M.formatDollarFull(m.trend));
    row("Idle   ", M.formatDollarFull(m.idle));
    const price = priceOnDay(day, genesis);
    if (price != null) row("Price  ", M.formatDollarFull(price));
    const fan = M.fanAt(day, pl.trend_uncertainty && pl.trend_uncertainty.fan);
    if (future && fan) row("Trend range", `${M.formatDollarCompact(fan.low)}–${M.formatDollarCompact(fan.high)}`, true);
    const st = future ? M.shortTermAt(day, pl.short_term) : null;
    if (st) row("12-mo path", `${M.formatDollarCompact(st.center)} (${M.formatDollarCompact(st.inner_low)}–${M.formatDollarCompact(st.inner_high)})`, true);
    if (pl.scenario && day >= M.dayFromDate(pl.scenario.fit_start_date, genesis)) row(pl.scenario.label, M.formatDollarFull(M.scenarioAt(day, pl.scenario)), true);

    const near = (list, getDay, tol) => {
      let best = null;
      list.forEach((item) => {
        const dist = Math.abs(getDay(item) - day);
        if (dist <= tol && (!best || dist < best.dist)) best = { item, dist };
      });
      return best && best.item;
    };
    const tol = Math.max(10, (day * 0.004));
    const top = near(pl.cycle_tops || [], (t) => M.dayFromDate(t.date, genesis), tol);
    if (top) {
      const sign = top.sigma_vs_trend >= 0 ? "+" : "";
      rows.push(`○ Cycle top ${formatDateShort(new Date(top.date + "T00:00:00Z"))}: ${M.formatDollarFull(top.price)} (${sign}${top.sigma_vs_trend.toFixed(2)}σ)`);
    }
    const halving = near(halvingDays(ctx), (h) => h.day, tol * 2);
    if (halving) rows.push(`┆ Halving ${halving.est ? halving.label + " (est.)" : formatDateShort(new Date(halving.label + "T00:00:00Z"))}`);
    if (future) rows.push(`<span style="opacity:0.7">projection · uncertainty widens with distance</span>`);
    return `${formatDateShort(M.dateFromDay(day, genesis))}<br/>${rows.join("<br/>")}`;
  }

  // ---------- power-law text, controls, lookup, export ----------

  function bandCoverageText() {
    const pl = modelsDoc.power_law;
    const cov = pl.bands && pl.bands.in_sample_coverage;
    const oneYear = backtestDoc && backtestDoc.horizons ? backtestDoc.horizons.find((h) => h.horizon_days === 365) : null;
    const parts = [];
    if (cov) parts.push(`Idle–Redline holds ${M.pct(cov.outer)} of history, the inner band ${M.pct(cov.inner)}`);
    if (oneYear && oneYear.trend_mae_factor != null && oneYear.coverage_outer != null) parts.push(`tested forward, the 1-year trend forecast has typically missed by ×${oneYear.trend_mae_factor.toFixed(1)}${oneYear.trend_bias_pct < 0 ? ", usually on the high side," : ""} and landed inside Idle–Redline ${M.pct(oneYear.coverage_outer)} of the time`);
    return parts.join("; ");
  }

  function describeCorridorPosition(ctx) {
    const { pl } = ctx;
    const m = M.modelAt(ctx.todayDay, pl);
    const where = describeCorridorZone(ctx, m);
    return `${where} (today Idle ${M.formatDollarCompact(m.idle)} · inner band ${M.formatDollarCompact(m.innerLow)}–${M.formatDollarCompact(m.innerHigh)} · Cruise ${M.formatDollarCompact(m.trend)} · Redline ${M.formatDollarCompact(m.redline)})`;
  }

  // The bands are skewed (long upside tail), so "39% below trend" can still
  // be inside the inner band -- printing the numbers removes the apparent
  // contradiction with the headline.
  function describeCorridorZone(ctx, m) {
    const { pl } = ctx;
    const pos = (Math.log10(pl.current.price) - Math.log10(m.idle)) / (Math.log10(m.redline) - Math.log10(m.idle));
    if (pos <= 0.05) return "at the corridor floor (Idle)";
    if (pl.current.price < m.innerLow) return "below the inner band, in the lower part of the corridor";
    if (pl.current.price <= m.innerHigh) return "inside the inner band around the trend";
    if (pos < 0.95) return "above the inner band, in the upper part of the corridor";
    return "at the corridor ceiling (Redline)";
  }

  function ordinal(n) {
    const v = Math.round(n);
    const s = ["th", "st", "nd", "rd"];
    const r = v % 100;
    return v + (s[(r - 20) % 10] || s[r] || s[0]);
  }

  function updatePowerLawText(ctx) {
    const { pl } = ctx;
    const p = pl.params;
    setText("power-law-stats", `b ${p.b.toFixed(2)} · R² ${p.r_squared.toFixed(3)} · σ ${p.sigma.toFixed(2)} · refit ${modelsDoc.generated_at.slice(0, 10).replace(/-/g, "\u2011")}`);

    const dev = pl.current.deviation_pct;
    const co = modelsDoc.cycle_overlay && modelsDoc.cycle_overlay.current_epoch;
    const headline = [
      `${M.formatDollarFull(pl.current.price)}`,
      `${Math.abs(dev).toFixed(0)}% ${dev >= 0 ? "above" : "below"} trend`,
      `${ordinal(pl.current.residual_percentile)} percentile of history`,
    ];
    if (co) headline.push(`halving cycle ${Math.round(co.pct_complete_of_avg_epoch)}% through`);
    setText("power-law-headline", headline.join(" · "));

    setText(
      "power-law-summary",
      `Today price is ${describeCorridorPosition(ctx)}. The Cruise line, extended, passes ${projectionPhrase(pl)}.`
    );
    setText("power-law-cycle-tops-summary", describeCycleTops(pl));
    setText("power-law-bands-note", bandCoverageText());
    renderCycleTopsTable(pl);
    renderProjectionTable(ctx);
  }

  function projectionPhrase(pl) {
    const p2030 = (pl.projections || []).find((p) => p.date.startsWith("2030"));
    if (!p2030) return "its published projections below";
    let text = `${M.formatDollarCompact(p2030.trend)} on Jan 1, 2030 (trend range ${M.formatDollarCompact(p2030.trend_low)}–${M.formatDollarCompact(p2030.trend_high)}; Idle–Redline ${M.formatDollarCompact(p2030.floor)}–${M.formatDollarCompact(p2030.ceiling)})`;
    if (pl.scenario) {
      const scDay = M.dayFromDate("2030-01-01", pl.params.genesis_date);
      text += `; the ${pl.scenario.label} says ${M.formatDollarCompact(M.scenarioAt(scDay, pl.scenario))}`;
    }
    return text;
  }

  function renderProjectionTable(ctx) {
    const tbody = document.getElementById("power-law-projections-tbody");
    if (!tbody) return;
    tbody.textContent = "";
    (ctx.pl.projections || []).forEach((p) => {
      const tr = document.createElement("tr");
      [
        p.date.slice(0, 4),
        M.formatDollarCompact(p.floor),
        M.formatDollarCompact(p.trend),
        M.formatDollarCompact(p.ceiling),
        `${M.formatDollarCompact(p.trend_low)}–${M.formatDollarCompact(p.trend_high)}`,
      ].forEach((text) => {
        const td = document.createElement("td");
        td.className = "numeral";
        td.textContent = text;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function updatePowerLawAria(ctx, minDay, maxDay) {
    const el = document.getElementById("power-law-chart");
    if (!el) return;
    const from = M.isoFromDay(minDay, ctx.genesis);
    const to = M.isoFromDay(maxDay, ctx.genesis);
    el.setAttribute(
      "aria-label",
      `Power law corridor chart, ${from} to ${to}, ${plState.timeScale === "cal" ? "calendar" : "logarithmic"} time axis. ` +
        `Current price ${M.formatDollarFull(ctx.pl.current.price)}, trend ${M.formatDollarFull(ctx.pl.current.trend_price)}. ` +
        "Use plus and minus to zoom, arrow keys to pan, 0 to reset. Projection values are listed in the table below the chart."
    );
  }

  // Cycle-top copy (unchanged in substance from the 2026-07-26 ruling; the
  // Redline threshold now comes from the empirical band, not a fixed 2σ).
  function describeCycleTops(pl) {
    const maxima = pl.cycle_top_era_maxima_sigma || [];
    if (maxima.length < 2) return "";
    const first = maxima[0];
    const last = maxima[maxima.length - 1];
    if (first.sigma_vs_trend <= 0 || last.sigma_vs_trend <= 0) return "";
    const fmt = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}σ`;
    const firstYear = first.date.slice(0, 4);
    const monotonic = maxima.every((m, i) => i === 0 || m.sigma_vs_trend <= maxima[i - 1].sigma_vs_trend + 1e-9);
    return monotonic
      ? `Each halving era's biggest run above trend has been smaller than the last -- from ${fmt(first.sigma_vs_trend)} in ${firstYear} to ${fmt(last.sigma_vs_trend)} so far this era.`
      : `Each halving era's biggest run above trend has varied -- ${fmt(first.sigma_vs_trend)} in ${firstYear}, ${fmt(last.sigma_vs_trend)} so far this era.`;
  }

  function redlineSigma(pl) {
    const off = M.bandOffsets(pl);
    return pl.params.sigma ? off.outer[1] / pl.params.sigma : 2;
  }

  function redlineFreeSinceDate(pl) {
    const tops = pl.cycle_tops || [];
    const threshold = redlineSigma(pl);
    let lastAt = -1;
    tops.forEach((t, i) => {
      if (t.sigma_vs_trend >= threshold) lastAt = i;
    });
    if (lastAt === -1 || lastAt === tops.length - 1) return null;
    return tops[lastAt].date;
  }

  const CYCLE_TOP_KIND_LABEL = {
    confirmed_top: "confirmed top",
    era_max_sigma: "era peak",
    current_era_high: "current era's high",
    unconfirmed_top: "unconfirmed top",
  };

  function cycleTopStatus(t) {
    const label = CYCLE_TOP_KIND_LABEL[t.kind] || t.kind;
    if (t.confirmed) return label;
    const dd = t.drawdown_so_far_pct;
    return `${label} so far (${dd >= 0 ? `down ${dd}% since` : `up ${Math.abs(dd)}% since`})`;
  }

  function renderCycleTopsTable(pl) {
    const tbody = document.getElementById("power-law-cycle-tops-tbody");
    if (tbody) {
      tbody.textContent = "";
      (pl.cycle_tops || []).forEach((t) => {
        const tr = document.createElement("tr");
        const sign = t.sigma_vs_trend >= 0 ? "+" : "";
        [
          [formatDateShort(new Date(t.date + "T00:00:00Z")), true],
          [M.formatDollarFull(t.price), true],
          [`${sign}${t.sigma_vs_trend.toFixed(2)}σ`, true],
          [cycleTopStatus(t), false],
        ].forEach(([text, numeral]) => {
          const td = document.createElement("td");
          if (numeral) td.className = "numeral";
          td.textContent = text;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }
    const base =
      "An observed pattern in past data, measured against today's fitted trend -- not a law; a future cycle can break it in either direction.";
    const since = redlineFreeSinceDate(pl);
    setText("power-law-cycle-tops-caveat", since ? `${base} No cycle top since ${formatDateShort(new Date(since + "T00:00:00Z"))} has reached Redline.` : base);
  }

  function syncPowerLawControls() {
    document.querySelectorAll("[data-power-law-range]").forEach((b) => {
      const active = !plState.view && b.dataset.powerLawRange === plState.range;
      b.classList.toggle("is-active", active);
      b.setAttribute("aria-pressed", String(active));
    });
    document.querySelectorAll("[data-power-law-time]").forEach((b) => {
      const active = b.dataset.powerLawTime === plState.timeScale;
      b.classList.toggle("is-active", active);
      b.setAttribute("aria-pressed", String(active));
    });
    try {
      const next = M.serializeViewState(window.location.search, plState);
      window.history.replaceState(null, "", next + window.location.hash);
    } catch (e) {
      /* file:// or sandboxed iframe -- sharing state is a convenience only */
    }
  }

  function rerenderPowerLaw() {
    renderPowerLaw(colorTokens());
    syncPowerLawControls();
  }

  // Zoom/pan in the CURRENT axis space (log or calendar), so "zoom in"
  // always means the same visual amount whichever scale is showing.
  function zoomPowerLaw(factor, centerFrac) {
    if (!modelsDoc) return;
    const ctx = plContext();
    const mode = plState.timeScale;
    const [lo, hi] = plWindow(ctx);
    const xLo = M.toX(lo, mode);
    const xHi = M.toX(hi, mode);
    const c = xLo + (xHi - xLo) * (centerFrac == null ? 0.5 : centerFrac);
    const nLo = c - (c - xLo) * factor;
    const nHi = c + (xHi - c) * factor;
    const [minDay, maxDay] = clampWindow(M.fromX(nLo, mode), M.fromX(nHi, mode), ctx);
    plState.view = { minDay, maxDay };
    rerenderPowerLaw();
  }

  function panPowerLaw(frac) {
    if (!modelsDoc) return;
    const ctx = plContext();
    const mode = plState.timeScale;
    const [lo, hi] = plWindow(ctx);
    const xLo = M.toX(lo, mode);
    const xHi = M.toX(hi, mode);
    const shift = (xHi - xLo) * frac;
    const span = hi - lo;
    let nLo = M.fromX(xLo + shift, mode);
    let nHi = M.fromX(xHi + shift, mode);
    if (nLo < ctx.firstDay) {
      nLo = ctx.firstDay;
      nHi = mode === "cal" ? nLo + span : M.fromX(M.toX(nLo, mode) + (xHi - xLo), mode);
    }
    if (nHi > ctx.endDay) {
      nHi = ctx.endDay;
      nLo = mode === "cal" ? nHi - span : M.fromX(M.toX(nHi, mode) - (xHi - xLo), mode);
    }
    const [minDay, maxDay] = clampWindow(nLo, nHi, ctx);
    plState.view = { minDay, maxDay };
    rerenderPowerLaw();
  }

  function resetPowerLawView() {
    plState.view = null;
    rerenderPowerLaw();
  }

  // ECharts' own wheel / pinch / box-zoom fire `datazoom`; translate the
  // resulting window back into our view state and re-render (which also
  // re-fits y and re-thins the tick labels) on the next frame.
  let pendingZoomFrame = 0;
  function onPowerLawDataZoom() {
    if (pendingZoomFrame) return;
    pendingZoomFrame = requestAnimationFrame(() => {
      pendingZoomFrame = 0;
      const chart = charts["power-law-chart"];
      if (!chart || !modelsDoc) return;
      const opt = chart.getOption();
      const dz = (opt.dataZoom || []).find((z) => z.startValue != null) || (opt.dataZoom || [])[0];
      const xAxis = opt.xAxis[0];
      const startV = dz && dz.startValue != null ? dz.startValue : xAxis.min;
      const endV = dz && dz.endValue != null ? dz.endValue : xAxis.max;
      if (startV == null || endV == null || endV <= startV) return;
      const ctx = plContext();
      const [minDay, maxDay] = clampWindow(M.fromX(startV, plState.timeScale), M.fromX(endV, plState.timeScale), ctx);
      plState.view = { minDay, maxDay };
      rerenderPowerLaw();
    });
  }

  function setPowerLawFocus(focused) {
    plState.focused = focused;
    if (focused && !COARSE_POINTER) showZoomHint();
  }

  // Fraction (0..1) of the plot's x-range under a client X coordinate.
  function plotFraction(clientX) {
    const chart = charts["power-law-chart"];
    const el = document.getElementById("power-law-chart");
    if (!chart || !el) return 0.5;
    const ax = chart.getOption().xAxis[0];
    const left = chart.convertToPixel({ xAxisIndex: 0 }, ax.min);
    const right = chart.convertToPixel({ xAxisIndex: 0 }, ax.max);
    const px = clientX - el.getBoundingClientRect().left;
    if (!(right > left)) return 0.5;
    return Math.min(Math.max((px - left) / (right - left), 0), 1);
  }

  function plotWidthPx() {
    const chart = charts["power-law-chart"];
    if (!chart) return 1;
    const ax = chart.getOption().xAxis[0];
    return Math.max(chart.convertToPixel({ xAxisIndex: 0 }, ax.max) - chart.convertToPixel({ xAxisIndex: 0 }, ax.min), 1);
  }

  // Wheel zooms only once the chart has focus (one click) or with Shift held
  // -- an unfocused chart never swallows a page scroll; Shift+wheel on a
  // focused chart pans. Touch: pinch zooms around the fingers, a mostly
  // horizontal one-finger drag pans, and vertical swipes stay with the
  // browser (CSS touch-action: pan-y). Coalesced to one render per frame.
  function initPowerLawGestures(el) {
    let pending = null;
    const flush = () => {
      const op = pending;
      pending = null;
      if (!op) return;
      if (op.zoom !== 1) zoomPowerLaw(op.zoom, op.center);
      if (op.pan) panPowerLaw(op.pan);
    };
    const queue = (zoom, center, pan) => {
      if (!pending) {
        pending = { zoom: 1, center, pan: 0 };
        requestAnimationFrame(flush);
      }
      pending.zoom *= zoom;
      pending.center = center;
      pending.pan += pan;
    };

    el.addEventListener(
      "wheel",
      (e) => {
        if (!plState.focused && !e.shiftKey) return;
        e.preventDefault();
        if (plState.focused && e.shiftKey) queue(1, 0.5, Math.sign(e.deltaY || e.deltaX) * 0.08);
        else queue(e.deltaY < 0 ? 0.85 : 1 / 0.85, plotFraction(e.clientX), 0);
      },
      { passive: false }
    );

    const pts = new Map();
    let lastPinch = null;
    let drag = null;
    el.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "touch") return;
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      drag = pts.size === 1 ? { x: e.clientX, y: e.clientY, lastX: e.clientX, horizontal: null } : null;
      lastPinch = null;
    });
    el.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "touch" || !pts.has(e.pointerId)) return;
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pts.size === 2) {
        const [p, q] = [...pts.values()];
        const dist = Math.hypot(p.x - q.x, p.y - q.y);
        if (lastPinch && dist > 0) queue(lastPinch / dist, plotFraction((p.x + q.x) / 2), 0);
        lastPinch = dist;
        return;
      }
      if (drag) {
        const dx = e.clientX - drag.x;
        const dy = e.clientY - drag.y;
        if (drag.horizontal === null && Math.hypot(dx, dy) > 10) drag.horizontal = Math.abs(dx) > Math.abs(dy) * 1.5;
        if (drag.horizontal) {
          queue(1, 0.5, -(e.clientX - drag.lastX) / plotWidthPx());
          drag.lastX = e.clientX;
        }
      }
    });
    const end = (e) => {
      pts.delete(e.pointerId);
      lastPinch = null;
      if (!pts.size) drag = null;
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  }


  function showZoomHint() {
    let seen = false;
    try {
      seen = window.localStorage.getItem(ZOOM_HINT_KEY) === "1";
      window.localStorage.setItem(ZOOM_HINT_KEY, "1");
    } catch (e) {
      /* storage blocked -- show the hint, just don't remember it */
    }
    if (seen) return;
    const hint = document.getElementById("power-law-zoom-hint");
    if (!hint) return;
    hint.hidden = false;
    setTimeout(() => {
      hint.hidden = true;
    }, 3500);
  }

  function initPowerLawControls() {
    document.querySelectorAll("[data-power-law-range]").forEach((btn) => {
      btn.addEventListener("click", () => {
        plState.range = btn.dataset.powerLawRange;
        plState.view = null;
        rerenderPowerLaw();
      });
    });
    document.querySelectorAll("[data-power-law-time]").forEach((btn) => {
      btn.addEventListener("click", () => {
        plState.timeScale = btn.dataset.powerLawTime;
        rerenderPowerLaw();
      });
    });
    document.querySelectorAll("[data-power-law-zoom]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const action = btn.dataset.powerLawZoom;
        if (action === "in") zoomPowerLaw(0.6);
        else if (action === "out") zoomPowerLaw(1 / 0.6);
        else if (action === "left") panPowerLaw(-0.25);
        else if (action === "right") panPowerLaw(0.25);
        else resetPowerLawView();
      });
    });

    const el = document.getElementById("power-law-chart");
    if (el) {
      el.addEventListener("focus", () => setPowerLawFocus(true));
      el.addEventListener("blur", () => setPowerLawFocus(false));
      el.addEventListener("pointerdown", () => {
        if (document.activeElement !== el) el.focus({ preventScroll: true });
      });
      el.addEventListener("dblclick", resetPowerLawView);
      initPowerLawGestures(el);
      el.addEventListener("keydown", (e) => {
        const keys = { "+": () => zoomPowerLaw(0.6), "=": () => zoomPowerLaw(0.6), "-": () => zoomPowerLaw(1 / 0.6), _: () => zoomPowerLaw(1 / 0.6), 0: resetPowerLawView, ArrowLeft: () => panPowerLaw(-0.15), ArrowRight: () => panPowerLaw(0.15) };
        const fn = keys[e.key];
        if (fn) {
          e.preventDefault();
          fn();
        }
        if (e.key === "Escape") el.blur();
      });
    }

    initLookup();
    initExport();
    syncPowerLawControls();
  }

  // G5: model at a date / when a price is reached.
  function initLookup() {
    const dateInput = document.getElementById("lookup-date");
    const priceInput = document.getElementById("lookup-price");
    if (dateInput) {
      dateInput.addEventListener("input", () => {
        if (!modelsDoc) return;
        const ctx = plContext();
        const v = dateInput.value;
        if (!/^\d{4}-\d{2}-\d{2}$/.test(v)) {
          setText("lookup-date-out", "");
          return;
        }
        const day = M.dayFromDate(v, ctx.genesis);
        if (day < 1) {
          setText("lookup-date-out", "before Bitcoin existed");
          return;
        }
        const m = M.modelAt(day, ctx.pl);
        const fan = M.fanAt(day, ctx.pl.trend_uncertainty && ctx.pl.trend_uncertainty.fan);
        const price = priceOnDay(day, ctx.genesis);
        let text = `${v} · Idle ${M.formatDollarFull(m.idle)} · Cruise ${M.formatDollarFull(m.trend)} · Redline ${M.formatDollarFull(m.redline)}`;
        if (fan) text += ` · trend range ${M.formatDollarCompact(fan.low)}–${M.formatDollarCompact(fan.high)}`;
        if (price != null) text += ` · actual ${M.formatDollarFull(price)}`;
        setText("lookup-date-out", text);
      });
    }
    if (priceInput) {
      priceInput.addEventListener("input", () => {
        if (!modelsDoc) return;
        const ctx = plContext();
        const price = Number(String(priceInput.value).replace(/[$,\s]/g, ""));
        if (!(price > 0)) {
          setText("lookup-price-out", "");
          return;
        }
        const days = M.crossingDays(price, ctx.pl);
        const target = M.formatDollarCompact(price);
        const phrase = (name, d) => {
          if (d == null) return `${name}: –`;
          const when = formatDateShort(M.dateFromDay(d, ctx.genesis));
          return d <= ctx.todayDay ? `${name} passed ${target} on ${when}` : `${name} reaches it ${when}`;
        };
        setText("lookup-price-out", [phrase("Redline", days.redline), phrase("Cruise", days.trend), phrase("Idle", days.idle)].join(" · "));
      });
    }
  }

  // G8: PNG / CSV / link.
  function initExport() {
    const png = document.getElementById("power-law-export-png");
    const csv = document.getElementById("power-law-export-csv");
    const link = document.getElementById("power-law-copy-link");
    const download = (href, name) => {
      const a = document.createElement("a");
      a.href = href;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
    };
    if (png) {
      png.addEventListener("click", (e) => {
        e.preventDefault();
        const chart = charts["power-law-chart"];
        if (!chart) return;
        download(chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#020804" }), "btc-power-law-corridor.png");
      });
    }
    if (csv) {
      csv.addEventListener("click", (e) => {
        e.preventDefault();
        if (!modelsDoc) return;
        const ctx = plContext();
        const [minDay, maxDay] = plWindow(ctx);
        const step = Math.max(1, Math.round((maxDay - minDay) / 2000));
        const rows = [];
        for (let d = Math.ceil(minDay); d <= maxDay; d += step) {
          const m = M.modelAt(d, ctx.pl);
          const price = priceOnDay(d, ctx.genesis);
          rows.push([M.isoFromDay(d, ctx.genesis), price == null ? "" : price.toFixed(2), m.idle.toFixed(2), m.innerLow.toFixed(2), m.trend.toFixed(2), m.innerHigh.toFixed(2), m.redline.toFixed(2)]);
        }
        const body = M.toCsv(["date", "price_usd", "idle", "inner_low", "cruise", "inner_high", "redline"], rows);
        download(URL.createObjectURL(new Blob([body], { type: "text/csv" })), "btc-power-law-corridor.csv");
      });
    }
    if (link) {
      link.addEventListener("click", (e) => {
        e.preventDefault();
        const url = window.location.origin + window.location.pathname + M.serializeViewState(window.location.search, plState) + "#power-law-card";
        const done = () => {
          link.textContent = "link copied";
          setTimeout(() => (link.textContent = "copy link"), 2000);
        };
        if (navigator.clipboard) navigator.clipboard.writeText(url).then(done, () => window.prompt("Copy this link", url));
        else window.prompt("Copy this link", url);
      });
    }
  }

  // ======================================================================
  // 4-Year Cycle Overlay -- log "x since halving" (director ruling 10)
  // ======================================================================

  function renderCycleOverlay(colors) {
    const chart = getOrInitChart("cycle-overlay-chart");
    if (!chart) return;
    const epochs = modelsDoc.cycle_overlay.epochs;
    const historical = epochs.filter((e) => !e.is_current);
    const opacityFor = (epoch) => {
      const i = historical.indexOf(epoch);
      return 0.35 + (0.4 * i) / Math.max(historical.length - 1, 1);
    };
    const toLogMult = (pct) => Math.log10(Math.max(1 + pct / 100, 0.01));
    const ys = [];

    const series = epochs.map((epoch) => {
      const year = epoch.halving_date.slice(0, 4);
      const color = epoch.is_current ? colors.accent : colors.inkDim;
      const opacity = epoch.is_current ? 1 : opacityFor(epoch);
      const data = epoch.days_since_halving.map((d, j) => {
        const y = toLogMult(epoch.pct_performance[j]);
        ys.push(y);
        return [d, y];
      });
      const last = data[data.length - 1];
      const s = {
        name: year,
        type: "line",
        showSymbol: false,
        data,
        lineStyle: { width: epoch.is_current ? 2.5 : 1.25, color, opacity },
        itemStyle: { color },
        z: epoch.is_current ? 10 : 1,
        endLabel: { show: true, formatter: () => year, color, opacity: epoch.is_current ? 1 : Math.min(opacity + 0.25, 1), fontFamily: colors.fontData, fontSize: 11 },
        emphasis: { focus: "series", lineStyle: { opacity: 1, width: epoch.is_current ? 2.5 : 2 } },
        blur: { lineStyle: { opacity: 0.12 } },
      };
      if (epoch.is_current && last) {
        s.markPoint = {
          silent: true,
          symbol: "circle",
          symbolSize: 9,
          itemStyle: { color: colors.accent, borderColor: colors.ink, borderWidth: 1.5 },
          label: { show: false },
          data: [{ coord: last }],
        };
        s.markLine = {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { color: colors.inkDim, type: [2, 3], opacity: 0.6, width: 1 },
          data: [{ yAxis: 0 }],
        };
      }
      return s;
    });

    const yMin = Math.min(...ys, 0) - 0.05;
    const yMax = Math.max(...ys) + 0.1;
    const ticks = M.dollarTicks(yMin, yMax);
    const fmtMult = (v) => {
      const m = Math.pow(10, v);
      return "×" + (m >= 10 ? Math.round(m).toLocaleString("en-US") : Number(m.toPrecision(2)).toString());
    };

    chart.setOption(
      {
        backgroundColor: "transparent",
        animation: !prefersReducedMotion(),
        textStyle: { fontFamily: colors.fontData, color: colors.inkDim },
        grid: { left: 8, right: 45, top: 30, bottom: 35, containLabel: true },
        legend: { top: 0, itemWidth: 14, itemHeight: 8, icon: "roundRect", textStyle: { color: colors.inkDim, fontSize: 11 }, inactiveColor: colors.border },
        labelLayout: { moveOverlap: "shiftY" },
        xAxis: {
          type: "value",
          name: "days since halving",
          nameLocation: "middle",
          nameGap: 22,
          min: 0,
          max: 1461,
          interval: 365,
          axisLine: { lineStyle: { color: colors.border } },
          axisLabel: { color: colors.inkDim },
          splitLine: { show: false },
        },
        yAxis: {
          type: "value",
          min: yMin,
          max: yMax,
          axisLabel: { color: colors.inkDim, customValues: ticks, formatter: fmtMult },
          axisTick: { customValues: ticks },
          axisLine: { lineStyle: { color: colors.border } },
          splitLine: { show: false },
        },
        series,
        tooltip: baseTooltip(colors, {
          trigger: "axis",
          axisPointer: { type: "line", lineStyle: { color: colors.inkDim, opacity: 0.5, type: [2, 3] } },
          formatter: (params) => {
            if (!params.length) return "";
            const day = Math.round(params[0].axisValue);
            const rows = params
              .filter((p) => p.data)
              .map((p) => `${p.marker} ${p.seriesName}: ×${Math.pow(10, p.data[1]).toFixed(2)}`);
            return `day ${day} after halving<br/>${rows.join("<br/>")}`;
          },
        }),
      },
      true
    );
    setChartStatus("cycle-overlay-chart", "ready");

    const current = modelsDoc.cycle_overlay.current_epoch;
    if (current) {
      const mult = 1 + current.pct_performance / 100;
      setText("cycle-overlay-stats", `${Math.round(current.pct_complete_of_avg_epoch)}% through · ×${mult.toFixed(2)} since halving`);
      const el = document.getElementById("cycle-overlay-chart");
      if (el) el.setAttribute("aria-label", `Cycle overlay: the current cycle is ${current.days_into_epoch} days past its halving, up ×${mult.toFixed(2)}, ranked at the ${current.cycle_percentile_vs_prior_epochs}th percentile of prior cycles at the same point.`);
    }
  }

  // ======================================================================
  // Mayer Multiple / 200-week MA -- two stacked panels (director ruling 10)
  // ======================================================================

  function renderMayerAnd200wma(colors) {
    const chart = getOrInitChart("mayer-200wma-chart");
    if (!chart) return;
    const priceData = priceHistorySeries.filter((r) => r.value > 0).map((r) => [r.date, Math.log10(r.value)]);
    const wmaData = modelsDoc.wma_200.series.map((r) => [r.date, Math.log10(r.wma_200w)]);
    const mayerData = modelsDoc.mayer_multiple.series.map((r) => [r.date, r.value]);
    const ys = priceData.map((p) => p[1]);
    const yMin = Math.min(...ys) - 0.15;
    const yMax = Math.ceil(Math.max(...ys));
    const ticks = M.dollarTicks(yMin, yMax);
    const zoneColor = rgba(colors.accent, 0.08);

    chart.setOption(
      {
        backgroundColor: "transparent",
        animation: !prefersReducedMotion(),
        textStyle: { fontFamily: colors.fontData, color: colors.inkDim },
        axisPointer: { link: [{ xAxisIndex: "all" }] },
        legend: { top: 0, itemWidth: 14, itemHeight: 8, icon: "roundRect", textStyle: { color: colors.inkDim, fontSize: 11 }, inactiveColor: colors.border, data: ["Price", "200-week MA", "Mayer"] },
        grid: [
          { left: 8, right: 16, top: 30, height: "46%", containLabel: true },
          { left: 8, right: 16, top: "64%", bottom: 28, containLabel: true },
        ],
        xAxis: [
          { type: "time", gridIndex: 0, axisLabel: { show: false }, axisLine: { onZero: false, lineStyle: { color: colors.border } }, axisTick: { show: false } },
          { type: "time", gridIndex: 1, axisLabel: { color: colors.inkDim, hideOverlap: true }, axisLine: { onZero: false, lineStyle: { color: colors.border } } },
        ],
        yAxis: [
          {
            type: "value",
            gridIndex: 0,
            min: yMin,
            max: yMax,
            axisLabel: { color: colors.inkDim, customValues: ticks, formatter: (v) => M.formatDollarCompact(Math.pow(10, v)) },
            axisTick: { customValues: ticks },
            splitLine: { show: false },
          },
          {
            type: "value",
            gridIndex: 1,
            min: 0,
            max: 4,
            interval: 1,
            axisLabel: { color: colors.inkDim, formatter: (v) => v.toFixed(0) },
            splitLine: { lineStyle: { color: colors.border, opacity: 0.3 } },
          },
        ],
        series: [
          { name: "Price", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, data: priceData, lineStyle: { color: colors.ink, width: 1.25 }, itemStyle: { color: colors.ink } },
          { name: "200-week MA", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, data: wmaData, lineStyle: { color: colors.accent, width: 1.5 }, itemStyle: { color: colors.accent } },
          {
            name: "Mayer",
            type: "line",
            xAxisIndex: 1,
            yAxisIndex: 1,
            showSymbol: false,
            data: mayerData,
            lineStyle: { color: colors.accent, width: 1.25 },
            itemStyle: { color: colors.accent },
            markArea: { silent: true, itemStyle: { color: zoneColor }, data: [[{ yAxis: 0 }, { yAxis: 0.8 }], [{ yAxis: 2.4 }, { yAxis: 4 }]] },
            markLine: { silent: true, symbol: "none", label: { show: false }, lineStyle: { type: [2, 3], color: colors.inkDim, opacity: 0.6, width: 1 }, data: [{ yAxis: 1 }] },
          },
        ],
        tooltip: baseTooltip(colors, {
          trigger: "axis",
          axisPointer: { type: "line", lineStyle: { color: colors.inkDim, opacity: 0.5, type: [2, 3] } },
          formatter: (params) => {
            if (!params.length) return "";
            const date = formatDateShort(new Date(params[0].axisValue));
            const rows = params.map((p) => {
              const v = p.data[1];
              const text = p.seriesName === "Mayer" ? v.toFixed(2) : M.formatDollarFull(Math.pow(10, v));
              return `${p.marker} ${p.seriesName}: ${text}`;
            });
            return `${date}<br/>${rows.join("<br/>")}`;
          },
        }),
        dataZoom: [{ type: "inside", xAxisIndex: [0, 1], filterMode: "none", zoomLock: !COARSE_POINTER, moveOnMouseMove: false }],
      },
      true
    );
    setChartStatus("mayer-200wma-chart", "ready");

    const mayer = modelsDoc.mayer_multiple.current;
    const wma = modelsDoc.wma_200.current;
    if (mayer && wma) {
      setText("mayer-200wma-stats", `Mayer ${mayer.multiple.toFixed(2)} (${ordinal(mayer.percentile)} pctile) · ${wma.distance_pct >= 0 ? "+" : ""}${wma.distance_pct.toFixed(0)}% vs 200WMA`);
      const pctFromAvg = (mayer.multiple - 1) * 100;
      const sig = signalLine("mayer_multiple");
      setText(
        "mayer-200wma-summary",
        `Price is ${Math.abs(pctFromAvg).toFixed(0)}% ${pctFromAvg >= 0 ? "above" : "below"} its 200-day average and ${Math.abs(wma.distance_pct).toFixed(0)}% ${wma.distance_pct >= 0 ? "above" : "below"} its 200-week average.${sig}`
      );
    }
  }

  function signalLine(name) {
    if (!backtestDoc) return "";
    const s = (backtestDoc.signals || []).find((x) => x.signal === name);
    if (!s || s.spearman_vs_fwd_1y == null) return "";
    const r = s.spearman_vs_fwd_1y;
    const strength = Math.abs(r) < 0.2 ? "little" : Math.abs(r) < 0.4 ? "some" : "a fairly strong";
    const direction = r < 0 ? "high readings were followed by lower returns" : "high readings were, if anything, followed by higher returns";
    return ` Tested point-in-time since ${s.first_origin.slice(0, 4)}, this has had ${strength} relationship with the next year's return (rank corr. ${r.toFixed(2)}: ${direction}).`;
  }

  // ======================================================================
  // Market Sentiment (Fear & Greed history)
  // ======================================================================
  // Sentiment, not valuation. Zones are dim reference bands (never accent);
  // their names live on a right-hand axis so they never sit on the data.
  const SENTIMENT_ZONES = [
    { from: 0, to: 25, opacity: 0.18, label: "Extreme Fear" },
    { from: 25, to: 45, opacity: 0.1, label: "Fear" },
    { from: 45, to: 55, opacity: 0.04, label: "Neutral" },
    { from: 55, to: 75, opacity: 0.1, label: "Greed" },
    { from: 75, to: 100, opacity: 0.18, label: "Extreme Greed" },
  ];

  function renderMarketSentiment(colors) {
    const chart = getOrInitChart("market-sentiment-chart");
    if (!chart) return;
    if (!fngHistorySeries.length) {
      setChartStatus("market-sentiment-chart", "error", "sentiment history unavailable");
      return;
    }
    const values = fngHistorySeries.map((r) => r.value);
    const smoothed = M.movingAverage(values, sentimentSmoothing);
    const points = fngHistorySeries.map((r, i) => [r.date, smoothed[i] == null ? null : Number(smoothed[i].toFixed(1))]);
    const zoneMids = SENTIMENT_ZONES.map((z) => (z.from + z.to) / 2);
    const zoneByMid = new Map(SENTIMENT_ZONES.map((z) => [(z.from + z.to) / 2, z.label]));
    const narrow = chart.getWidth() < 420;

    chart.setOption(
      {
        backgroundColor: "transparent",
        animation: !prefersReducedMotion(),
        textStyle: { fontFamily: colors.fontData, color: colors.inkDim },
        grid: { left: 8, right: narrow ? 64 : 78, top: 14, bottom: 30, containLabel: true },
        xAxis: { type: "time", splitNumber: narrow ? 4 : 8, axisLine: { lineStyle: { color: colors.border } }, axisLabel: { color: colors.inkDim, hideOverlap: true } },
        yAxis: [
          { type: "value", min: 0, max: 100, interval: 25, axisLine: { lineStyle: { color: colors.border } }, axisLabel: { color: colors.inkDim }, splitLine: { show: false } },
          {
            type: "value",
            min: 0,
            max: 100,
            position: "right",
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: { color: colors.inkDim, fontSize: 9, margin: 6, customValues: zoneMids, formatter: (v) => (narrow ? (zoneByMid.get(v) || "").replace("Extreme ", "Ext. ") : zoneByMid.get(v) || "") },
          },
        ],
        series: [
          {
            name: "Fear & Greed",
            type: "line",
            showSymbol: false,
            connectNulls: false,
            data: points,
            lineStyle: { color: colors.accent, width: 1.25 },
            itemStyle: { color: colors.accent },
            markArea: {
              silent: true,
              label: { show: false },
              data: SENTIMENT_ZONES.map((z) => [{ yAxis: z.from, itemStyle: { color: colors.border, opacity: z.opacity } }, { yAxis: z.to }]),
            },
          },
        ],
        tooltip: baseTooltip(colors, {
          trigger: "axis",
          axisPointer: { type: "line", lineStyle: { color: colors.inkDim, opacity: 0.5, type: [2, 3] } },
          formatter: (params) => {
            const p = params[0];
            if (!p) return "";
            const idx = p.dataIndex;
            const raw = fngHistorySeries[idx];
            const smooth = sentimentSmoothing > 1 && p.data[1] != null ? ` (${sentimentSmoothing}-day avg ${p.data[1]})` : "";
            return `${formatDateShort(new Date(raw.date + "T00:00:00Z"))}<br/>${raw.value} · ${raw.classification}${smooth}`;
          },
        }),
        dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "none", zoomLock: !COARSE_POINTER, moveOnMouseMove: false }],
      },
      true
    );
    setChartStatus("market-sentiment-chart", "ready");

    const latest = fngHistorySeries[fngHistorySeries.length - 1];
    setText("market-sentiment-stats", `${latest.value} · ${latest.classification} · ${latest.date}`);
    document.querySelectorAll("[data-sentiment-window]").forEach((b) => {
      const active = Number(b.dataset.sentimentWindow) === sentimentSmoothing;
      b.classList.toggle("is-active", active);
      b.setAttribute("aria-pressed", String(active));
    });
  }

  function initSentimentControls() {
    document.querySelectorAll("[data-sentiment-window]").forEach((btn) => {
      btn.addEventListener("click", () => {
        sentimentSmoothing = Number(btn.dataset.sentimentWindow);
        if (modelsDoc) renderMarketSentiment(colorTokens());
      });
    });
  }

  // ======================================================================
  // Track Record -- the model graded against reality (director ruling 8)
  // ======================================================================

  // trend_bias_pct < 0 means outcomes came in BELOW the forecast on
  // average, i.e. the forecasts ran high.
  function biasPhrase(biasPct) {
    const n = Math.abs(biasPct).toFixed(0);
    return biasPct < 0 ? `ran ${n}% high` : `ran ${n}% low`;
  }

  function renderTrackRecord(colors) {
    if (!backtestDoc) {
      setChartStatus("track-record-chart", "error", "backtest unavailable");
      return;
    }
    const oneYear = backtestDoc.horizons.find((h) => h.horizon_days === 365);
    if (oneYear && oneYear.trend_mae_factor != null && oneYear.trend_bias_pct != null && oneYear.coverage_outer != null) {
      setText(
        "track-record-stats",
        `1y miss ×${oneYear.trend_mae_factor.toFixed(1)} · ${biasPhrase(oneYear.trend_bias_pct)} · in band ${M.pct(oneYear.coverage_outer)}`
      );
    }

    const chart = getOrInitChart("track-record-chart");
    if (chart) {
      const rows = backtestDoc.miss_by_year || [];
      chart.setOption(
        {
          backgroundColor: "transparent",
          animation: false,
          textStyle: { fontFamily: colors.fontData, color: colors.inkDim },
          grid: { left: 8, right: 8, top: 12, bottom: 22, containLabel: true },
          xAxis: { type: "category", data: rows.map((r) => String(r.year).slice(2)), axisLabel: { color: colors.inkDim, fontSize: 10, formatter: (v) => "'" + v }, axisLine: { lineStyle: { color: colors.border } }, axisTick: { show: false } },
          yAxis: { type: "value", min: 1, axisLabel: { color: colors.inkDim, fontSize: 10, formatter: (v) => "×" + v }, splitLine: { lineStyle: { color: colors.border, opacity: 0.3 } } },
          series: [
            {
              type: "bar",
              data: rows.map((r) => r.mae_factor),
              itemStyle: { color: rgba(colors.accent, 0.6) },
              barMaxWidth: 14,
              markLine: { silent: true, symbol: "none", label: { show: false }, lineStyle: { type: [2, 3], color: colors.inkDim, width: 1 }, data: [{ yAxis: 1 }] },
            },
          ],
          tooltip: baseTooltip(colors, {
            trigger: "axis",
            formatter: (params) => {
              const r = rows[params[0].dataIndex];
              if (!r || r.mae_factor == null) return "";
              return `forecasts made in ${r.year} (1y ahead)<br/>typical miss ×${r.mae_factor.toFixed(2)} · ${biasPhrase(r.bias_pct)}`;
            },
          }),
        },
        true
      );
      setChartStatus("track-record-chart", "ready");
    }

    const tbody = document.getElementById("track-record-tbody");
    if (tbody) {
      tbody.textContent = "";
      const fx = (v) => (v == null ? "–" : `×${v.toFixed(2)}`);
      backtestDoc.horizons.forEach((h) => {
        const tr = document.createElement("tr");
        [`${Math.round(h.horizon_days / 365)}y`, fx(h.trend_mae_factor), fx(h.random_walk_mae_factor), h.coverage_outer == null ? "–" : M.pct(h.coverage_outer), h.approx_independent_windows == null ? "–" : `~${Math.round(h.approx_independent_windows)}`].forEach((text) => {
          const td = document.createElement("td");
          td.className = "numeral";
          td.textContent = text;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }

    renderLedger();
    renderSignals();
  }

  function renderLedger() {
    const tbody = document.getElementById("ledger-tbody");
    if (!tbody) return;
    tbody.textContent = "";
    const entries = (ledgerDoc && ledgerDoc.entries) || [];
    const rows = [];
    entries
      .slice()
      .reverse()
      .forEach((e) =>
        e.horizons.forEach((h) => rows.push({ issued: e.issued, h }))
      );
    rows.slice(0, 12).forEach(({ issued, h }) => {
      const tr = document.createElement("tr");
      const outcome = h.outcome
        ? `${M.formatDollarCompact(h.outcome.actual)} (×${h.outcome.miss_factor.toFixed(2)}${h.outcome.inside_outer ? "" : ", outside band"})`
        : `due ${h.target_date}`;
      [issued, `${Math.round(h.horizon_days / 365)}y`, M.formatDollarCompact(h.trend), `${M.formatDollarCompact(h.floor)}–${M.formatDollarCompact(h.ceiling)}`, outcome].forEach((text, i) => {
        const td = document.createElement("td");
        if (i < 4) td.className = "numeral";
        td.textContent = text;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    const matured = rows.filter((r) => r.h.outcome).length;
    setText(
      "ledger-note",
      ledgerDoc && ledgerDoc.started
        ? `Live ledger started ${ledgerDoc.started}: one forecast frozen per month, never edited. ${matured ? `${matured} matured so far.` : "The first one matures a year after it was made -- until then this only proves the forecasts were recorded in advance."}`
        : ""
    );
  }

  const SIGNAL_LABELS = {
    power_law_z_realtime: "Distance from power-law trend (point-in-time)",
    wma200_distance: "Distance from 200-week MA",
    price_vs_hashrate_fit: "Price vs hash-rate fit",
    mayer_multiple: "Mayer Multiple",
    fear_greed: "Fear & Greed",
  };

  function renderSignals() {
    const tbody = document.getElementById("signals-tbody");
    if (!tbody || !backtestDoc) return;
    tbody.textContent = "";
    (backtestDoc.signals || [])
      .slice()
      .sort((p, q) => Math.abs(q.spearman_vs_fwd_1y || 0) - Math.abs(p.spearman_vs_fwd_1y || 0))
      .forEach((s) => {
        const tr = document.createElement("tr");
        [SIGNAL_LABELS[s.signal] || s.signal, s.spearman_vs_fwd_1y == null ? "–" : s.spearman_vs_fwd_1y.toFixed(2), s.first_origin ? s.first_origin.slice(0, 4) : "–", s.approx_independent_windows == null ? "–" : `~${Math.round(s.approx_independent_windows)}`].forEach((text, i) => {
          const td = document.createElement("td");
          if (i > 0) td.className = "numeral";
          td.textContent = text;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
  }

  // ======================================================================
  // lifecycle
  // ======================================================================

  function safely(id, fn) {
    try {
      fn();
    } catch (err) {
      console.error(`${id} failed to render`, err);
      setChartStatus(id, "error", "chart failed to render");
    }
  }

  function renderAll() {
    if (!modelsDoc) return;
    const colors = colorTokens();
    safely("power-law-chart", () => renderPowerLaw(colors));
    safely("cycle-overlay-chart", () => renderCycleOverlay(colors));
    safely("mayer-200wma-chart", () => renderMayerAnd200wma(colors));
    safely("market-sentiment-chart", () => renderMarketSentiment(colors));
    safely("track-record-chart", () => renderTrackRecord(colors));
    syncPowerLawControls();
    // Hovering one time-axis chart moves the crosshair on the other
    // (director ruling 11). Cycle overlay (days-since-halving) and the
    // power-law chart (log/day axis) aren't calendar axes, so they stay out.
    const linked = ["mayer-200wma-chart", "market-sentiment-chart"].map((id) => charts[id]).filter(Boolean);
    if (linked.length === 2) {
      linked.forEach((c) => (c.group = "ber-time"));
      echarts.connect("ber-time");
    }
  }

  function resizeAll() {
    Object.values(charts).forEach((c) => c && c.resize());
    // Year-tick thinning depends on width, so the hero re-renders.
    if (modelsDoc && charts["power-law-chart"]) renderPowerLaw(colorTokens());
  }

  function debounce(fn, ms) {
    let timer = null;
    return function debounced(...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(null, args), ms);
    };
  }

  function loadEchartsScript() {
    if (window.echarts) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = ECHARTS_CDN_URL;
      script.integrity = ECHARTS_CDN_INTEGRITY;
      script.crossOrigin = "anonymous";
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("ECharts CDN script failed to load"));
      document.head.appendChild(script);
    });
  }

  const CHART_IDS = ["power-law-chart", "cycle-overlay-chart", "mayer-200wma-chart", "market-sentiment-chart", "track-record-chart"];

  let loadStarted = false;
  async function loadAndRender() {
    if (loadStarted) return;
    loadStarted = true;
    CHART_IDS.forEach((id) => setChartStatus(id, "loading", "loading model…"));
    initPowerLawControls();
    initSentimentControls();

    const [echartsResult, modelsResult, priceResult, fngResult, backtestResult, ledgerResult] = await Promise.allSettled([
      loadEchartsScript(),
      fetchJSON("data/models.json"),
      fetchJSON("data/history/price_daily.json"),
      fetchJSON("data/history/fng_daily.json"),
      fetchJSON("data/backtest.json"),
      fetchJSON("data/forecasts.json"),
    ]);

    if (echartsResult.status === "rejected") {
      console.warn("ECharts unavailable", echartsResult.reason);
      CHART_IDS.forEach((id) => setChartStatus(id, "error", "chart library failed to load -- the numbers above and the tables below still stand"));
      return;
    }
    if (modelsResult.status === "rejected") {
      console.warn("models.json unavailable", modelsResult.reason);
      CHART_IDS.forEach((id) => setChartStatus(id, "error", "model data failed to load"));
      return;
    }
    modelsDoc = modelsResult.value;
    if (priceResult.status === "fulfilled") priceHistorySeries = priceResult.value.series || [];
    else console.warn("price_daily.json unavailable", priceResult.reason);
    if (fngResult.status === "fulfilled") fngHistorySeries = fngResult.value.series || [];
    else console.warn("fng_daily.json unavailable", fngResult.reason);
    if (backtestResult.status === "fulfilled") backtestDoc = backtestResult.value;
    if (ledgerResult.status === "fulfilled") ledgerDoc = ledgerResult.value;

    renderAll();
    const pl = charts["power-law-chart"];
    if (pl) {
      pl.on("datazoom", onPowerLawDataZoom);
      pl.on("legendselectchanged", () => {});
    }
    // Canvas text is drawn once; if the web font arrives after first paint,
    // redraw so axis labels don't stay in a fallback face.
    if (document.fonts && document.fonts.status !== "loaded") {
      document.fonts.ready.then(() => renderAll());
    }
  }

  const lazySection = document.getElementById("power-law-card");
  if (lazySection && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          observer.disconnect();
          loadAndRender();
        }
      },
      { rootMargin: "600px 0px" }
    );
    observer.observe(lazySection);
  } else {
    document.addEventListener("ber:booted", loadAndRender);
  }

  window.addEventListener("resize", debounce(resizeAll, 150));

  // Test hook (Playwright E2E): read-only view of state, no behaviour.
  BER.chartsDebug = { plState, charts: () => charts };
})();
