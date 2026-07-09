/* charts.js -- PROJECTIONS section: power law corridor (the hero chart,
 * full-width log-log per director rule #7), 4-year cycle overlay, Mayer
 * Multiple / 200WMA strip, and the deviation dial. Apache ECharts (CDN,
 * spec Section 3). Re-themed on 'ber:theme-changed' by reading the current
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

  function themeColors() {
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

  function getOrInitChart(id) {
    if (!charts[id]) {
      const el = document.getElementById(id);
      if (!el || typeof echarts === "undefined") return null;
      charts[id] = echarts.init(el);
    }
    return charts[id];
  }

  // ---------- Power Law Corridor (the hero) ----------

  function renderPowerLaw(colors) {
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
    const actualPoints = filtered.map((r) => [daysSinceGenesis(r.date, genesis), r.value]);

    const { a, b, sigma } = pl.params;
    const minDay = Math.max(actualPoints.length ? actualPoints[0][0] : 1, 1);
    const maxDay = daysSinceGenesis("2035-12-31", genesis);
    const steps = 80;
    const trendPoints = [];
    const floorPoints = [];
    const bandPoints = [];
    for (let i = 0; i <= steps; i++) {
      const d = minDay * Math.pow(maxDay / minDay, i / steps);
      const trendLog10 = a + b * Math.log10(d);
      const floor = Math.pow(10, trendLog10 - 2 * sigma);
      const ceiling = Math.pow(10, trendLog10 + 2 * sigma);
      trendPoints.push([d, Math.pow(10, trendLog10)]);
      floorPoints.push([d, floor]);
      bandPoints.push([d, ceiling - floor]);
    }

    const yearFormatter = (val) => String(yearFromDays(val, genesis));

    chart.setOption(
      {
        backgroundColor: "transparent",
        animation: !prefersReducedMotion(),
        textStyle: { fontFamily: colors.fontData, color: colors.inkDim },
        grid: { left: 60, right: 20, top: 20, bottom: 40 },
        xAxis: {
          type: "log",
          min: minDay,
          max: maxDay,
          axisLine: { lineStyle: { color: colors.border } },
          axisLabel: { color: colors.inkDim, formatter: yearFormatter },
          splitLine: { show: false },
        },
        yAxis: {
          type: "log",
          min: (val) => Math.pow(10, Math.floor(Math.log10(val.min))),
          max: (val) => Math.pow(10, Math.ceil(Math.log10(val.max))),
          axisLine: { lineStyle: { color: colors.border } },
          axisLabel: { color: colors.inkDim, formatter: (v) => "$" + Number(v).toLocaleString() },
          splitLine: { lineStyle: { color: colors.border, opacity: 0.3 } },
        },
        series: [
          { name: "Idle (floor)", type: "line", data: floorPoints, showSymbol: false, lineStyle: { opacity: 0 }, stack: "band", silent: true },
          { name: "Redline band", type: "line", data: bandPoints, showSymbol: false, lineStyle: { opacity: 0 }, areaStyle: { color: colors.accent, opacity: 0.12 }, stack: "band", silent: true },
          { name: "Cruise (trend)", type: "line", data: trendPoints, showSymbol: false, lineStyle: { color: colors.accent, width: 1.5, type: "dashed" } },
          { name: "Price", type: "line", data: actualPoints, showSymbol: false, lineStyle: { color: colors.ink, width: 2 } },
        ],
        tooltip: {
          trigger: "axis",
          backgroundColor: colors.ink,
          textStyle: { color: colors.inkDim },
          valueFormatter: (v) => "$" + Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 }),
        },
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
        document.querySelectorAll("[data-power-law-range]").forEach((b) => b.classList.toggle("is-active", b === btn));
        renderPowerLaw(themeColors());
      });
    });
  }

  // ---------- 4-Year Cycle Overlay ----------

  function renderCycleOverlay(colors) {
    const chart = getOrInitChart("cycle-overlay-chart");
    if (!chart) return;
    const epochs = modelsDoc.cycle_overlay.epochs;
    const palette = [colors.inkDim, colors.border === colors.inkDim ? colors.warn : colors.warn, colors.ok, colors.accent];

    const series = epochs.map((epoch, i) => ({
      name: epoch.halving_date.slice(0, 4),
      type: "line",
      showSymbol: false,
      data: epoch.days_since_halving.map((d, j) => [d, epoch.pct_performance[j]]),
      lineStyle: { width: epoch.is_current ? 2.5 : 1, color: epoch.is_current ? colors.accent : palette[i % palette.length] },
      z: epoch.is_current ? 10 : 1,
    }));

    chart.setOption(
      {
        backgroundColor: "transparent",
        animation: !prefersReducedMotion(),
        textStyle: { fontFamily: colors.fontData, color: colors.inkDim },
        grid: { left: 55, right: 15, top: 30, bottom: 35 },
        legend: { top: 0, textStyle: { color: colors.inkDim, fontSize: 11 } },
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

  // ---------- Deviation dial ----------

  function renderDeviationDial(colors) {
    const chart = getOrInitChart("deviation-dial-chart");
    if (!chart) return;
    const dial = modelsDoc.deviation_dial;

    chart.setOption(
      {
        backgroundColor: "transparent",
        animation: !prefersReducedMotion(),
        series: [
          {
            type: "gauge",
            min: 0,
            max: 100,
            startAngle: 200,
            endAngle: -20,
            axisLine: {
              lineStyle: {
                width: 14,
                color: [
                  [0.333, colors.ok],
                  [0.667, colors.warn],
                  [1, colors.fail],
                ],
              },
            },
            pointer: { itemStyle: { color: colors.ink } },
            axisTick: { show: false },
            splitLine: { length: 10, lineStyle: { color: colors.inkDim } },
            axisLabel: { color: colors.inkDim, fontSize: 10, distance: 18 },
            detail: {
              valueAnimation: true,
              formatter: () => dial.label,
              color: colors.ink,
              fontFamily: colors.fontData,
              fontSize: 20,
              offsetCenter: [0, "70%"],
            },
            data: [{ value: dial.score_0_100 }],
          },
        ],
      },
      true
    );
  }

  // ---------- lifecycle ----------

  function renderAll() {
    if (!modelsDoc) return;
    const colors = themeColors();
    renderPowerLaw(colors);
    renderCycleOverlay(colors);
    renderMayerAnd200wma(colors);
    renderDeviationDial(colors);
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

  document.addEventListener("ber:theme-changed", () => {
    // Re-init (not just setOption) per spec Section 16.1.4 -- cheap at this chart count.
    Object.values(charts).forEach((c) => c && c.dispose());
    charts = {};
    renderAll();
  });

  window.addEventListener("resize", resizeAll);
})();
