/* charts.js -- PROJECTIONS section: power law corridor (the hero chart,
 * full-width log-log per director rule #7), 4-year cycle overlay, Mayer
 * Multiple / 200WMA strip, and Market Sentiment (Fear & Greed history).
 * Apache ECharts (CDN, spec Section 3). Colors are read from the current
 * CSS custom properties directly -- single source of truth stays the
 * design tokens, no separate hardcoded ECharts theme registrations.
 *
 * Everything in this module is loaded lazily, on an IntersectionObserver
 * watching the Price Models section, not on ber:booted: the ECharts CDN
 * script (~340KB gzipped) and the two full history files this module
 * charts (price_daily.json, fng_daily.json -- the other three history
 * files are never fetched at all now that app.js paints gauges from
 * health.json's last_value digest) used to load unconditionally on every
 * visit, mobile included, even though every chart sits below the fold.
 * See IMPROVEMENT_BACKLOG.md.
 */
(function () {
  "use strict";

  const BER = (window.BER = window.BER || {});
  const ECHARTS_CDN_URL = "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js";
  let charts = {};
  let modelsDoc = null;
  let priceHistorySeries = [];
  let fngHistorySeries = [];
  let powerLawRange = "all";

  // Wheel-to-zoom trap fix: ECharts' "inside" dataZoom prevents the page's
  // native wheel-scroll the instant a pointer lands on the chart, REGARDLESS
  // of zoomOnMouseWheel's modifier-key setting -- a long-standing ECharts
  // bug (apache/echarts#10079), not something zoomOnMouseWheel: "shift"
  // alone fixes despite that being its documented purpose. Confirmed in
  // isolation: with `zoomOnMouseWheel: "shift"` alone, a plain (no-shift)
  // wheel over the chart still doesn't scroll the page at all. The
  // documented workaround (same GitHub issue) is what's used here instead:
  // start every chart with zoomLock: true (wheel/drag do nothing chart-side,
  // so the page scrolls normally), and only flip zoomLock: false for the
  // instant Shift is actually held, locking again on keyup -- verified this
  // combination lets a plain wheel scroll the page, a Shift+wheel zoom the
  // chart without moving the page, and scrolling resume normally the moment
  // Shift is released. Touch devices (no Shift key to hold) start unlocked
  // instead, so pinch-zoom keeps working there; moveOnMouseMove: false
  // separately stops a single-finger drag from being read as chart-pan
  // (which is what would otherwise trap a touch scroll/swipe).
  const CHARTS_COARSE_POINTER = window.matchMedia("(pointer: coarse)").matches;

  function setChartsZoomLock(locked) {
    Object.values(charts).forEach((c) => {
      if (!c) return;
      const dz = c.getOption().dataZoom;
      if (!dz || !dz.length) return;
      c.setOption({ dataZoom: dz.map(() => ({ zoomLock: locked })) });
    });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Shift") setChartsZoomLock(false);
  });
  document.addEventListener("keyup", (e) => {
    if (e.key === "Shift") setChartsZoomLock(true);
  });

  // Default (not "no-store") cache mode, unlike live.js's genuinely
  // real-time endpoints: these are daily-immutable committed files, so
  // letting the browser's normal HTTP cache (conditional requests / 304s)
  // work saves a full re-download on repeat same-day visits, mobile
  // cellular especially.
  function fetchJSON(path) {
    return fetch(path).then((r) => {
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

  // One shared tooltip look across all 4 charts -- previously the power-law
  // chart had its own custom (and too-low-contrast: --ink-dim text on an
  // --ink background is ~2.8:1) styling while the other three used
  // ECharts' default white tooltip, off-identity. `extra` lets a chart add
  // its own formatter/valueFormatter on top of the shared base.
  function baseTooltip(colors, extra) {
    return Object.assign(
      {
        backgroundColor: "rgba(4, 16, 8, 0.96)",
        borderColor: colors.border,
        borderWidth: 1,
        textStyle: { color: colors.ink, fontFamily: colors.fontData, fontSize: 12 },
      },
      extra
    );
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

  // Power-law y-axis ticks land on exact decade boundaries (log10 space,
  // interval: 1), so abbreviation is always a clean "1" + unit, never a
  // rounding artifact. Director ruling (Fable, mobile-legibility review):
  // instrument readouts abbreviate ("1.00M" on a multimeter) -- spelled-out,
  // comma-grouped dollars are prose convention, not instrument convention.
  // Applies on every viewport, not just mobile: one code path, no
  // width-measurement JS, kills the whole axis-label-clipping bug class
  // instead of patching around it per breakpoint. The tooltip keeps full
  // precision via toLocaleString -- this formatter is for axis graduations
  // only.
  function formatAxisDollar(v) {
    const n = Math.round(v);
    if (n < 0) return "$" + Math.pow(10, n).toFixed(-n);
    if (n >= 9) return "$" + Math.pow(10, n - 9).toLocaleString() + "B";
    if (n >= 6) return "$" + Math.pow(10, n - 6).toLocaleString() + "M";
    if (n >= 3) return "$" + Math.pow(10, n - 3).toLocaleString() + "K";
    return "$" + Math.pow(10, n).toLocaleString();
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

  // Zoom (director-minimal: `inside` dataZoom adds no visible chrome) --
  // Shift+wheel or pinch to zoom, double-click/tap resets; see the zoomLock
  // comment above for why plain wheel/drag no longer get hijacked into
  // chart-panning. No slider/toolbox, so the screensaver test still applies
  // cleanly; the interaction hint lives in each chart's caption (visible on
  // touch, not just a hover-only title attribute).
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

    let filtered = priceHistorySeries;
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
          axisLabel: { color: colors.inkDim, formatter: formatAxisDollar },
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
        tooltip: baseTooltip(colors, {
          trigger: "axis",
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
        }),
        dataZoom: [
          { type: "inside", xAxisIndex: 0, filterMode: "none", zoomLock: !CHARTS_COARSE_POINTER, moveOnMouseMove: false },
          { type: "inside", yAxisIndex: 0, filterMode: "none", zoomLock: !CHARTS_COARSE_POINTER, moveOnMouseMove: false },
        ],
      },
      true
    );

    const statsEl = document.getElementById("power-law-stats");
    if (statsEl) {
      statsEl.textContent = `b=${pl.params.b} · R²=${pl.params.r_squared} · σ=${pl.params.sigma} · last refit ${modelsDoc.generated_at.slice(0, 10)} · ${pl.current.deviation_pct}% vs trend`;
    }

    const summaryEl = document.getElementById("power-law-summary");
    if (summaryEl) {
      const dev = pl.current.deviation_pct;
      const direction = dev >= 0 ? "above" : "below";
      summaryEl.textContent = `Today: price is about ${Math.abs(dev).toFixed(0)}% ${direction} the long-run trend line -- ${describeCorridorPosition(dev, sigma)}.`;
    }
  }

  // Plain-language read of where today's price sits in the corridor, for
  // readers who aren't going to parse "b=5.62 -51.54% vs trend" -- derived
  // from the same deviation_pct/sigma the calibration plate already shows,
  // not a new number.
  function describeCorridorPosition(deviationPct, sigma) {
    const logRatio = Math.log10(1 + deviationPct / 100);
    const fraction = Math.min(Math.max((logRatio + 2 * sigma) / (4 * sigma), 0), 1);
    if (fraction <= 0.15) return "at the corridor floor (Idle)";
    if (fraction <= 0.4) return "in the lower half of the corridor";
    if (fraction <= 0.6) return "near the trend line (Cruise)";
    if (fraction <= 0.85) return "in the upper half of the corridor";
    return "at the corridor ceiling (Redline)";
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
        // 2012's +10,000% cycle otherwise owns the whole y-axis, squashing
        // every later cycle (including the current one) into a flat line
        // near 0%. Deselected by default, not hidden -- its legend chip is
        // still there, one tap away, so nothing is actually removed from
        // the chart, just given sane default axis scale.
        legend: { top: 0, textStyle: { color: colors.inkDim, fontSize: 11 }, selected: { 2012: false } },
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
        tooltip: baseTooltip(colors, { trigger: "axis", valueFormatter: (v) => v.toFixed(1) + "%" }),
        dataZoom: [
          { type: "inside", xAxisIndex: 0, filterMode: "none", zoomLock: !CHARTS_COARSE_POINTER, moveOnMouseMove: false },
          { type: "inside", yAxisIndex: 0, filterMode: "none", zoomLock: !CHARTS_COARSE_POINTER, moveOnMouseMove: false },
        ],
      },
      true
    );

    const statsEl = document.getElementById("cycle-overlay-stats");
    const current = modelsDoc.cycle_overlay.current_epoch;
    if (statsEl && current) {
      const sign = current.pct_performance >= 0 ? "+" : "";
      statsEl.textContent = `${current.pct_complete_of_avg_epoch}% of the way through this cycle · ${sign}${current.pct_performance}% since halving`;
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
        // No axis `name` on either side (director ruling, mobile-legibility
        // review): a value-axis name duplicating a legend item duplicating
        // the plate-stats line above the chart fails the screensaver test
        // on any viewport, and on narrow ones it collided with the legend
        // outright. The right axis's `%` formatter already disambiguates
        // which axis is which.
        yAxis: [
          { type: "value", position: "left", axisLabel: { color: colors.inkDim }, splitLine: { lineStyle: { color: colors.border, opacity: 0.3 } } },
          { type: "value", position: "right", axisLabel: { color: colors.inkDim, formatter: "{value}%" }, splitLine: { show: false } },
        ],
        series: [
          {
            // Short, plate-stats-vocabulary legend labels ("Mayer 0.8662 ..."
            // above already uses this exact wording) -- fits one legend row
            // at mobile widths without wrapping or colliding.
            name: "Mayer",
            type: "line",
            showSymbol: false,
            data: mayerSeries,
            lineStyle: { color: colors.accent, width: 1.5 },
            itemStyle: { color: colors.accent },
            yAxisIndex: 0,
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { type: "dashed", color: colors.inkDim, opacity: 0.6, width: 1 },
              // Label cut, line kept (director ruling): "price = 200-day avg"
              // is explanatory prose sitting on the plot face, off-identity
              // per the mono-is-the-machine/sans-is-the-human typography
              // rule -- the info panel already explains Mayer=1.0 the same
              // way. A bare hairline at the axis's %-formatted zero line
              // reads as the reference point on its own.
              label: { show: false },
              data: [{ yAxis: 1 }],
            },
          },
          {
            name: "200WMA dist",
            type: "line",
            showSymbol: false,
            data: wmaDistanceSeries,
            lineStyle: { color: colors.ink, width: 1 },
            itemStyle: { color: colors.ink },
            yAxisIndex: 1,
          },
        ],
        tooltip: baseTooltip(colors, { trigger: "axis" }),
        dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "none", zoomLock: !CHARTS_COARSE_POINTER, moveOnMouseMove: false }],
      },
      true
    );

    const statsEl = document.getElementById("mayer-200wma-stats");
    const mayer = modelsDoc.mayer_multiple.current;
    const wma = modelsDoc.wma_200.current;
    if (statsEl && mayer && wma) {
      statsEl.textContent = `Mayer ${mayer.multiple} (${mayer.percentile}th pctile) · 200WMA dist ${wma.distance_pct}%`;
    }

    const summaryEl = document.getElementById("mayer-200wma-summary");
    if (summaryEl && mayer) {
      const pctFromAvg = (mayer.multiple - 1) * 100;
      const direction = pctFromAvg >= 0 ? "above" : "below";
      summaryEl.textContent = `Mayer Multiple is ${mayer.multiple} -- price is about ${Math.abs(pctFromAvg).toFixed(0)}% ${direction} its own 200-day average.`;
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
    { from: 0, to: 25, opacity: 0.18, label: "Extreme Fear" },
    { from: 25, to: 45, opacity: 0.1, label: "Fear" },
    { from: 45, to: 55, opacity: 0.04, label: "Neutral" },
    { from: 55, to: 75, opacity: 0.1, label: "Greed" },
    { from: 75, to: 100, opacity: 0.18, label: "Extreme Greed" },
  ];

  function renderMarketSentiment(colors) {
    const chart = getOrInitChart("market-sentiment-chart");
    if (!chart) return;
    const points = fngHistorySeries.map((r) => [r.date, r.value]);

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
              label: {
                show: true,
                formatter: "{b}",
                position: "insideTop",
                color: colors.inkDim,
                fontFamily: colors.fontData,
                fontSize: 9,
              },
              data: SENTIMENT_ZONES.map((z) => [
                { yAxis: z.from, name: z.label, itemStyle: { color: colors.border, opacity: z.opacity } },
                { yAxis: z.to },
              ]),
            },
          },
        ],
        tooltip: baseTooltip(colors, { trigger: "axis" }),
        dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "none", zoomLock: !CHARTS_COARSE_POINTER, moveOnMouseMove: false }],
      },
      true
    );

    const statsEl = document.getElementById("market-sentiment-stats");
    const latest = fngHistorySeries.length ? fngHistorySeries[fngHistorySeries.length - 1] : null;
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

  // Coalesces the resize storm mobile browsers fire when the URL bar
  // collapses/expands on scroll -- without this, every one of those events
  // was calling .resize() (a real layout recompute) on up to 4 chart
  // instances back to back.
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
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("ECharts CDN script failed to load"));
      document.head.appendChild(script);
    });
  }

  let loadStarted = false;
  async function loadAndRender() {
    if (loadStarted) return;
    loadStarted = true;
    initPowerLawControls();

    const [echartsResult, modelsResult, priceResult, fngResult] = await Promise.allSettled([
      loadEchartsScript(),
      fetchJSON("data/models.json"),
      fetchJSON("data/history/price_daily.json"),
      fetchJSON("data/history/fng_daily.json"),
    ]);

    if (echartsResult.status === "rejected") {
      console.warn("ECharts unavailable -- projections section stays empty", echartsResult.reason);
      return;
    }
    if (modelsResult.status === "rejected") {
      console.warn("models.json unavailable -- projections section stays empty", modelsResult.reason);
      return;
    }
    modelsDoc = modelsResult.value;
    // Partial failure on either history file still renders every chart that
    // doesn't depend on it, same "never blank the rest of the page over one
    // failure" spirit as the live-snapshot failover chains.
    if (priceResult.status === "fulfilled") priceHistorySeries = priceResult.value.series || [];
    else console.warn("price_daily.json unavailable -- power law chart's actual-price line stays empty", priceResult.reason);
    if (fngResult.status === "fulfilled") fngHistorySeries = fngResult.value.series || [];
    else console.warn("fng_daily.json unavailable -- market sentiment chart stays empty", fngResult.reason);

    renderAll();
  }

  // Price Models is below the fold on every viewport, mobile especially --
  // defer the ~340KB(gz) ECharts CDN script and this module's own data
  // fetches until the user is actually approaching the section instead of
  // paying for both on every page load regardless of whether anyone scrolls
  // that far (see IMPROVEMENT_BACKLOG.md). rootMargin starts the load while
  // the section is still a scroll away so charts are ready, not popping in
  // mid-scroll.
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
})();
