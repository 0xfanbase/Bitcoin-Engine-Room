/* charts.js -- PROJECTIONS section: power law corridor (the hero chart,
 * full-width log-log per director rule #7), 4-year cycle overlay, Mayer
 * Multiple / 200WMA strip, and Market Sentiment (Fear & Greed history).
 * Apache ECharts (CDN, spec Section 3). Colors are read from the current
 * CSS custom properties directly -- single source of truth stays the
 * design tokens, no separate hardcoded ECharts theme registrations.
 */
(function () {
  "use strict";

  const BER = (window.BER = window.BER || {});
  let charts = {};
  let modelsDoc = null;
  let powerLawRange = "all";

  function fetchJSON(path) {
    return fetch(path, { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      return r.json();
    });
  }

  function prefersReducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function colorTokens() {
    const style = getComputedStyle(document.documentElement);
    const get = (name) => style.getPropertyValue(name).trim();
    return {
      ink: get("--ink"),
      inkDim: get("--ink-dim"),
      accent: get("--accent"),
      ok: get("--ok"),
      warn: get("--warn"),
      fail: get("--fail"),
      border: get("--panel-border"),
      fontData: get("--font-data").split(",")[0].replace(/"/g, ""),
    };
  }

  function daysSinceGenesis(dateStr, genesis) {
    const d = Date.parse(dateStr + "T00:00:00Z");
    const g = Date.parse(genesis + "T00:00:00Z");
    return Math.round((d - g) / 86400000);
  }

  function yearFromDays(days, genesis) {
    const g = Date.parse(genesis + "T00:00:00Z");
    return new Date(g + days * 86400000).getUTCFullYear();
  }

  function dateFromDays(days, genesis) {
    const g = Date.parse(genesis + "T00:00:00Z");
    return new Date(g + days * 86400000);
  }

  const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function formatDateShort(d) {
    return `${MONTH_ABBR[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
  }

  // One evenly-spaced tick per calendar year, keyed by exact day-offset so the
  // formatter can look values up directly (ECharts drops the `index` arg it'd
  // otherwise pass a formatter once `customValues` is set). Log-scale compresses
  // recent years into a shrinking fraction of the axis width as day-numbers grow,
  // so per-year is already the practical ceiling for *static* label density near
  // "today" -- the tooltip formatter below carries exact month/day precision on
  // hover instead of cramming more static labels into that compressed region.
  function buildYearTicks(minDay, maxDay, genesis) {
    const startYear = yearFromDays(minDay, genesis);
    const endYear = yearFromDays(maxDay, genesis);
    const ticks = new Map();
    for (let y = startYear; y <= endYear; y++) {
      const day = daysSinceGenesis(`${y}-01-01`, genesis);
      if (day >= minDay && day <= maxDay) ticks.set(day, String(y));
    }
    return ticks;
  }

  // Zoom/pan (director-minimal: `inside` dataZoom adds no visible chrome --
  // mouse wheel + drag on desktop, pinch + drag on touch -- and a
  // double-click resets the view. No slider/toolbox, so the screensaver
  // test still applies cleanly; the .chart-canvas title attribute (set in
  // index.html) is the sole hint that the interaction exists.
  function getOrInitChart(id) {
    if (!charts[id]) {
      const el = document.getElementById(id);
      if (!el || typeof echarts === "undefined") return null;
      const chart = echarts.init(el);
      el.addEventListener("dblclick", () => {
        chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
      });
      charts[id] = chart;
    }
    return charts[id];
  }

  // ---------- Power Law Corridor (the hero) ----------

  function renderPowerLaw(colors) {
    if (!modelsDoc) return;
    const chart = getOrInitChart("power-law-chart");
    if (!chart) return;
    const pl = modelsDoc.power_law;
    const genesis = pl.params.genesis_date;

    const priceHistory = (BER.histories && BER.histories.price_daily && BER.histories.price_daily.series) || [];
    let filtered = priceHistory;
    if (powerLawRange !== "all") {
      const years = powerLawRange === "1y" ? 1 : 4;
      const cutoff = new Date();
      cutoff.setUTCFullYear(cutoff.getUTCFullYear() - years);
      const cutoffStr = cutoff.toISOString().slice(0, 10);
      filtered = priceHistory.filter((r) => r.date >= cutoffStr);
    }
    // Series values are log10(price), not price, plotted on a linear y-axis
    // (formatted back to dollars for display) rather than a genuine log
    // y-axis. This is a workaround for a real ECharts 5.5.1 rendering bug,
    // not a style choice: stacking two areaStyle line series (the standard
    // "invisible floor + visible band" technique for filling between two
    // curves) renders a diagonal compositing artifact instead of the
    // intended band, on ANY axis type, not just log -- confirmed in
    // isolation with flat, non-log test data (stack: 'x' on two constant
    // series produces a diagonal wipe between them instead of a clean
    // boundary; removing `stack` renders correctly; this is the actual bug,
    // not "log axes can't be stacked"). The corridor is instead drawn by a
    // single `type: 'custom'` series whose renderItem builds the ceiling
    // curve forward and the floor curve backward into one closed polygon,
    // sidestepping stacking entirely. The log10 transform is kept anyway
    // (rather than reverting to type:"log") because it makes the corridor's
    // ±2σ width a constant additive offset instead of a multiplicative one,
    // which is what the model actually defines it as.
    const actualPoints = filtered.map((r) => [daysSinceGenesis(r.date, genesis), Math.log10(r.value)]);
    const todayDay = daysSinceGenesis(pl.current.date, genesis);

    const { a, b, sigma } = pl.params;
    const minDay = Math.max(actualPoints.length ? actualPoints[0][0] : 1, 1);
    const maxDay = daysSinceGenesis("2035-12-31", genesis);
    const steps = 80;
    const trendPoints = [];
    const floorPoints = [];
    const ceilPoints = [];
    for (let i = 0; i <= steps; i++) {
      const d = minDay * Math.pow(maxDay / minDay, i / steps);
      const trendLog10 = a + b * Math.log10(d);
      trendPoints.push([d, trendLog10]);
      floorPoints.push([d, trendLog10 - 2 * sigma]);
      ceilPoints.push([d, trendLog10 + 2 * sigma]);
    }

    const yearTicks = buildYearTicks(minDay, maxDay, genesis);
    const yearTickValues = Array.from(yearTicks.keys());

    chart.setOption(
      {
        backgroundColor: "transparent",
        animation: !prefersReducedMotion(),
        textStyle: { fontFamily: colors.fontData, color: colors.inkDim },
        grid: { left: 60, right: 50, top: 20, bottom: 40 },
        xAxis: {
          type: "log",
          min: minDay,
          max: maxDay,
          axisLine: { lineStyle: { color: colors.border } },
          axisLabel: {
            color: colors.inkDim,
            customValues: yearTickValues,
            formatter: (val) => yearTicks.get(val) || "",
            hideOverlap: true,
          },
          axisTick: { customValues: yearTickValues },
          splitLine: { show: false },
        },
        yAxis: {
          type: "value",
          min: (val) => Math.floor(val.min),
          max: (val) => Math.ceil(val.max),
          interval: 1,
          axisLine: { lineStyle: { color: colors.border } },
          axisLabel: { color: colors.inkDim, formatter: (v) => "$" + Math.round(Math.pow(10, v)).toLocaleString() },
          splitLine: { lineStyle: { color: colors.border, opacity: 0.3 } },
        },
        series: [
          {
            name: "Idle (floor)",
            type: "line",
            data: floorPoints,
            showSymbol: false,
            lineStyle: { opacity: 0 },
            silent: true,
            endLabel: { show: true, formatter: "Idle", color: colors.inkDim, fontFamily: colors.fontData, fontSize: 10 },
          },
          {
            name: "Redline (ceiling)",
            type: "line",
            data: ceilPoints,
            showSymbol: false,
            lineStyle: { opacity: 0 },
            silent: true,
            endLabel: { show: true, formatter: "Redline", color: colors.inkDim, fontFamily: colors.fontData, fontSize: 10 },
          },
          {
            name: "Corridor band",
            type: "custom",
            silent: true,
            data: [floorPoints[0]],
            renderItem: function (params, api) {
              const points = [];
              for (let i = 0; i < ceilPoints.length; i++) points.push(api.coord(ceilPoints[i]));
              for (let i = floorPoints.length - 1; i >= 0; i--) points.push(api.coord(floorPoints[i]));
              return { type: "polygon", shape: { points }, style: { fill: colors.accent, opacity: 0.12 } };
            },
          },
          {
            name: "Cruise (trend)",
            type: "line",
            data: trendPoints,
            showSymbol: false,
            lineStyle: { color: colors.accent, width: 1.5, type: "dashed" },
            endLabel: { show: true, formatter: "Cruise", color: colors.accent, fontFamily: colors.fontData, fontSize: 10 },
          },
          {
            name: "Price",
            type: "line",
            data: actualPoints,
            showSymbol: false,
            lineStyle: { color: colors.ink, width: 2 },
            markLine: {
              silent: true,
              symbol: "none",
              label: {
                formatter: "Today",
                color: colors.inkDim,
                fontFamily: colors.fontData,
                fontSize: 10,
                position: "insideEndTop",
              },
              lineStyle: { type: "dashed", color: colors.inkDim, opacity: 0.6, width: 1 },
              data: [{ xAxis: todayDay }],
            },
            markPoint: {
              silent: true,
              symbol: "circle",
              symbolSize: 8,
              itemStyle: { color: colors.ink, borderColor: colors.accent, borderWidth: 1.5 },
              label: { show: false },
              data: [{ coord: [todayDay, Math.log10(pl.current.price)] }],
            },
          },
        ],
        tooltip: {
          trigger: "axis",
          backgroundColor: colors.ink,
          textStyle: { color: colors.inkDim },
          formatter: (params) => {
            if (!params || !params.length) return "";
            const day = params[0].axisValue;
            const rows = params
              .filter((p) => p.seriesName === "Cruise (trend)" || p.seriesName === "Price")
              .map(
                (p) =>
                  `${p.marker} ${p.seriesName}: $${Math.round(Math.pow(10, p.data[1])).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
              )
              .join("<br/>");
            return `${formatDateShort(dateFromDays(day, genesis))}<br/>${rows}`;
          },
        },
        dataZoom: [
          { type: "inside", xAxisIndex: 0, filterMode: "none" },
          { type: "inside", yAxisIndex: 0, filterMode: "none" },
        ],
      },
      true
    );

    const statsEl = document.getElementById("power-law-stats");
    if (statsEl) {
      statsEl.textContent = `b=${pl.params.b} · R²=${pl.params.r_squared} · σ=${pl.params.sigma} · last refit ${modelsDoc.generated_at.slice(0, 10)} · ${pl.current.deviation_pct}% vs trend`;
    }
  }

  function initPowerLawControls() {
    document.querySelectorAll("[data-power-law-range]").forEach((btn) => {
      btn.addEventListener("click", () => {
        powerLawRange = btn.dataset.powerLawRange;
        document.querySelectorAll("[data-power-law-range]").forEach((b) => {
          b.classList.toggle("is-active", b === btn);
          b.setAttribute("aria-pressed", String(b === btn));
        });
        renderPowerLaw(colorTokens());
      });
    });
  }

  // ---------- 4-Year Cycle Overlay ----------

  function renderCycleOverlay(colors) {
    const chart = getOrInitChart("cycle-overlay-chart");
    if (!chart) return;
    const epochs = modelsDoc.cycle_overlay.epochs;

    // Historical cycles all render in the same muted --ink-dim tone, dimmer
    // for older cycles and brighter for more recent ones (an ordinal recency
    // ramp, not a hue-per-index palette) -- the old palette assigned --ok to
    // one historical line, and --ok is literally the same hex as --accent, so
    // that cycle and the live one were rendering in an identical color.
    const historicalEpochs = epochs.filter((e) => !e.is_current);
    const historicalOpacity = (epoch) => {
      const i = historicalEpochs.indexOf(epoch);
      const n = Math.max(historicalEpochs.length - 1, 1);
      return 0.35 + (0.4 * i) / n;
    };

    const series = epochs.map((epoch) => {
      const year = epoch.halving_date.slice(0, 4);
      const color = epoch.is_current ? colors.accent : colors.inkDim;
      const opacity = epoch.is_current ? 1 : historicalOpacity(epoch);
      const lastIdx = epoch.days_since_halving.length - 1;
      const s = {
        name: year,
        type: "line",
        showSymbol: false,
        data: epoch.days_since_halving.map((d, j) => [d, epoch.pct_performance[j]]),
        lineStyle: { width: epoch.is_current ? 2.5 : 1.25, color, opacity },
        itemStyle: { color },
        z: epoch.is_current ? 10 : 1,
        endLabel: {
          show: true,
          formatter: () => year,
          color,
          opacity: epoch.is_current ? 1 : Math.min(opacity + 0.25, 1),
          fontFamily: colors.fontData,
          fontSize: 11,
        },
        emphasis: { focus: "series", lineStyle: { opacity: 1, width: epoch.is_current ? 2.5 : 2 } },
        blur: { lineStyle: { opacity: 0.12 } },
      };
      if (epoch.is_current) {
        s.markPoint = {
          silent: true,
          symbol: "circle",
          symbolSize: 9,
          itemStyle: { color: colors.accent, borderColor: colors.ink, borderWidth: 1.5 },
          label: {
            show: true,
            formatter: () => `${epoch.pct_performance[lastIdx].toFixed(0)}%`,
            color: colors.ink,
            fontFamily: colors.fontData,
            fontSize: 11,
            position: "top",
            distance: 6,
          },
          data: [{ coord: [epoch.days_since_halving[lastIdx], epoch.pct_performance[lastIdx]] }],
        };
      }
      return s;
    });

    chart.setOption(
      {
        backgroundColor: "transparent",
        animation: !prefersReducedMotion(),
        textStyle: { fontFamily: colors.fontData, color: colors.inkDim },
        grid: { left: 55, right: 45, top: 30, bottom: 35 },
        legend: { top: 0, textStyle: { color: colors.inkDim, fontSize: 11 } },
        labelLayout: { moveOverlap: "shiftY" },
        xAxis: {
          type: "value",
          name: "days since halving",
          nameLocation: "middle",
          nameGap: 22,
          axisLine: { lineStyle: { color: colors.border } },
          axisLabel: { color: colors.inkDim },
          splitLine: { show: false },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: colors.inkDim, formatter: "{value}%" },
          axisLine: { lineStyle: { color: colors.border } },
          splitLine: { lineStyle: { color: colors.border, opacity: 0.3 } },
        },
        series,
        tooltip: { trigger: "axis", valueFormatter: (v) => v.toFixed(1) + "%" },
        dataZoom: [
          { type: "inside", xAxisIndex: 0, filterMode: "none" },
          { type: "inside", yAxisIndex: 0, filterMode: "none" },
        ],
      },
      true
    );

    const statsEl = document.getElementById("cycle-overlay-stats");
    const current = modelsDoc.cycle_overlay.current_epoch;
    if (statsEl && current) {
      statsEl.textContent = `${current.pct_complete_of_avg_epoch}% into epoch · ${current.pct_performance}%`;
    }
  }

  // ---------- Mayer Multiple / 200WMA strip ----------

  function renderMayerAnd200wma(colors) {
    const chart = getOrInitChart("mayer-200wma-chart");
    if (!chart) return;
    const mayerSeries = modelsDoc.mayer_multiple.series.map((r) => [r.date, r.value]);
    const wmaDistanceSeries = modelsDoc.wma_200.series.map((r) => [r.date, r.distance_pct]);

    chart.setOption(
      {
        backgroundColor: "transparent",
        animation: !prefersReducedMotion(),
        textStyle: { fontFamily: colors.fontData, color: colors.inkDim },
        grid: { left: 50, right: 50, top: 30, bottom: 35 },
        legend: { top: 0, textStyle: { color: colors.inkDim, fontSize: 11 } },
        xAxis: { type: "time", axisLine: { lineStyle: { color: colors.border } }, axisLabel: { color: colors.inkDim } },
        yAxis: [
          { type: "value", name: "Mayer", position: "left", axisLabel: { color: colors.inkDim }, splitLine: { lineStyle: { color: colors.border, opacity: 0.3 } } },
          { type: "value", name: "200WMA dist %", position: "right", axisLabel: { color: colors.inkDim, formatter: "{value}%" }, splitLine: { show: false } },
        ],
        series: [
          { name: "Mayer Multiple", type: "line", showSymbol: false, data: mayerSeries, lineStyle: { color: colors.accent, width: 1.5 }, yAxisIndex: 0 },
          { name: "200WMA distance", type: "line", showSymbol: false, data: wmaDistanceSeries, lineStyle: { color: colors.ink, width: 1 }, yAxisIndex: 1 },
        ],
        tooltip: { trigger: "axis" },
        dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "none" }],
      },
      true
    );

    const statsEl = document.getElementById("mayer-200wma-stats");
    const mayer = modelsDoc.mayer_multiple.current;
    const wma = modelsDoc.wma_200.current;
    if (statsEl && mayer && wma) {
      statsEl.textContent = `Mayer ${mayer.multiple} (${mayer.percentile}th pctile) · 200WMA dist ${wma.distance_pct}%`;
    }
  }

  // ---------- Market Sentiment (Fear & Greed history) ----------
  // Replaces the former "Deviation Dial" (director ruling, 2026-07-09):
  // that composite averaged three percentile ranks and was labeled a toy
  // in its own methodology note, presenting a non-model with model-grade
  // visual weight. Fear & Greed is real, sourced daily data -- but it's
  // sentiment, not valuation, so the card says so and the classification
  // zones render as dim reference bands (--panel-border, graduated
  // opacity), never accent -- rule 4 stays accent-as-data-only even for a
  // "zone," since these are structural reference chrome, not a reading.
  const SENTIMENT_ZONES = [
    { from: 0, to: 25, opacity: 0.18 }, // Extreme Fear
    { from: 25, to: 45, opacity: 0.1 }, // Fear
    { from: 45, to: 55, opacity: 0.04 }, // Neutral
    { from: 55, to: 75, opacity: 0.1 }, // Greed
    { from: 75, to: 100, opacity: 0.18 }, // Extreme Greed
  ];

  function renderMarketSentiment(colors) {
    const chart = getOrInitChart("market-sentiment-chart");
    if (!chart) return;
    const series = (BER.histories && BER.histories.fng_daily && BER.histories.fng_daily.series) || [];
    const points = series.map((r) => [r.date, r.value]);

    chart.setOption(
      {
        backgroundColor: "transparent",
        animation: !prefersReducedMotion(),
        textStyle: { fontFamily: colors.fontData, color: colors.inkDim },
        grid: { left: 45, right: 20, top: 20, bottom: 35 },
        xAxis: { type: "time", axisLine: { lineStyle: { color: colors.border } }, axisLabel: { color: colors.inkDim } },
        yAxis: {
          type: "value",
          min: 0,
          max: 100,
          axisLine: { lineStyle: { color: colors.border } },
          axisLabel: { color: colors.inkDim },
          splitLine: { lineStyle: { color: colors.border, opacity: 0.3 } },
        },
        series: [
          {
            name: "Fear & Greed",
            type: "line",
            showSymbol: false,
            data: points,
            lineStyle: { color: colors.accent, width: 1.5 },
            markArea: {
              silent: true,
              data: SENTIMENT_ZONES.map((z) => [
                { yAxis: z.from, itemStyle: { color: colors.border, opacity: z.opacity } },
                { yAxis: z.to },
              ]),
            },
          },
        ],
        tooltip: { trigger: "axis" },
        dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "none" }],
      },
      true
    );

    const statsEl = document.getElementById("market-sentiment-stats");
    const latest = series.length ? series[series.length - 1] : null;
    if (statsEl && latest) {
      statsEl.textContent = `${latest.value} · ${latest.classification} · as of ${latest.date}`;
    }
  }

  // ---------- lifecycle ----------

  function renderAll() {
    if (!modelsDoc) return;
    const colors = colorTokens();
    renderPowerLaw(colors);
    renderCycleOverlay(colors);
    renderMayerAnd200wma(colors);
    renderMarketSentiment(colors);
  }

  function resizeAll() {
    Object.values(charts).forEach((c) => c && c.resize());
  }

  document.addEventListener("ber:booted", async () => {
    initPowerLawControls();
    try {
      modelsDoc = await fetchJSON("data/models.json");
    } catch (e) {
      console.warn("models.json unavailable -- projections section stays empty", e);
      return;
    }
    renderAll();
  });

  window.addEventListener("resize", resizeAll);
})();
