/* forecast-ui.js — public Forecast section (scenario fans from BER models).
 * Loads data/scenario_forecast.json only. Never references holdings or private watcher APIs.
 */
(function () {
  "use strict";
  const BER = (window.BER = window.BER || {});

  function fmtUSD(v) {
    if (v == null || !isFinite(v)) return "—";
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    if (v >= 1e3) return "$" + Math.round(v).toLocaleString("en-US");
    return "$" + Number(v).toFixed(0);
  }

  function paintRanges(doc) {
    const root = document.getElementById("forecast-ranges");
    const disc = document.getElementById("forecast-disclaimer");
    if (!root || !doc) return;
    if (disc) {
      disc.textContent =
        (doc.method && doc.method.disclaimer) ||
        "Scenario fans, not confidence intervals. Not financial advice.";
    }
    root.textContent = "";
    for (const h of doc.horizons || []) {
      const el = document.createElement("div");
      el.className = "forecast-range";
      el.innerHTML =
        '<div class="fr-h">' +
        h.label +
        '</div><div class="fr-mid numeral">' +
        fmtUSD(h.p50) +
        '</div><div class="fr-band numeral">P10 ' +
        fmtUSD(h.p10) +
        " · P90 " +
        fmtUSD(h.p90) +
        '</div><div class="fr-x">' +
        (h.target_date || "") +
        (h.multiple_vs_spot_p50 != null ? " · ×" + h.multiple_vs_spot_p50 + " spot" : "") +
        "</div>";
      root.appendChild(el);
    }
  }

  function paintEnsemble(doc) {
    const tb = document.getElementById("forecast-ensemble-tbody");
    if (!tb) return;
    tb.textContent = "";
    for (const row of doc.ensemble || []) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" +
        (row.name || row.id) +
        "</td><td>" +
        (row.role || "") +
        '</td><td class="numeral">' +
        (row.share != null ? Math.round(row.share * 100) + "%" : "—") +
        "</td><td>" +
        (row.trust_note || "") +
        "</td>";
      tb.appendChild(tr);
    }
  }

  function paintMatured(doc) {
    const tb = document.getElementById("forecast-matured-tbody");
    const sum = document.getElementById("forecast-learning-summary");
    if (!tb) return;
    const matured = (doc.learning && doc.learning.matured) || [];
    const miss = (doc.learning && doc.learning.miss_by_year) || [];
    if (sum) {
      sum.textContent = matured.length
        ? matured.length + " matured ledger call(s). Walk-forward miss-by-year chart below mirrors Track Record."
        : "Live ledger has no matured horizons yet — miss-by-year still comes from the walk-forward backtest.";
    }
    tb.textContent = "";
    if (!matured.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = '<td colspan="4">No matured ledger rows yet — entries appear after their horizon elapses.</td>';
      tb.appendChild(tr);
      return;
    }
    for (const m of matured.slice(-12)) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        '<td class="numeral">' +
        (m.issued || "") +
        '</td><td class="numeral">' +
        (m.horizon_days || "") +
        "d</td><td class=\"numeral\">" +
        fmtUSD(m.trend) +
        '</td><td class="numeral">' +
        fmtUSD(m.outcome) +
        "</td>";
      tb.appendChild(tr);
    }
    // stash miss series for chart when echarts ready
    BER._forecastMissByYear = miss;
  }

  function renderFanChart(doc, echarts) {
    const el = document.getElementById("forecast-fan-chart");
    if (!el || !echarts || !doc) return;
    const hist = doc.history || [];
    const fan = doc.fan || [];
    const histDates = hist.map((p) => p.date);
    const histPrices = hist.map((p) => p.price);
    const fanDates = fan.map((p) => p.date);
    const p10 = fan.map((p) => p.p10);
    const p50 = fan.map((p) => p.p50);
    const p90 = fan.map((p) => p.p90);
    const cruise = fan.map((p) => p.cruise);
    const chart = echarts.init(el, null, { renderer: "canvas" });
    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 48, right: 16, top: 24, bottom: 32 },
      tooltip: {
        trigger: "axis",
        valueFormatter: (v) => fmtUSD(v),
      },
      xAxis: { type: "time", axisLabel: { color: "#6f9f80" }, axisLine: { lineStyle: { color: "rgba(51,255,102,0.25)" } } },
      yAxis: {
        type: "log",
        axisLabel: { color: "#6f9f80", formatter: (v) => fmtUSD(v) },
        splitLine: { lineStyle: { color: "rgba(51,255,102,0.08)" } },
      },
      series: [
        {
          name: "spot",
          type: "line",
          showSymbol: false,
          data: hist.map((p) => [p.date, p.price]),
          lineStyle: { width: 1.5, color: "#33ff66" },
          z: 5,
        },
        {
          name: "P90",
          type: "line",
          showSymbol: false,
          data: fan.map((p) => [p.date, p.p90]),
          lineStyle: { width: 1, color: "rgba(51,255,102,0.35)" },
          areaStyle: { color: "rgba(51,255,102,0.08)" },
          z: 1,
        },
        {
          name: "P10",
          type: "line",
          showSymbol: false,
          data: fan.map((p) => [p.date, p.p10]),
          lineStyle: { width: 1, color: "rgba(51,255,102,0.35)" },
          z: 2,
        },
        {
          name: "P50 anchored",
          type: "line",
          showSymbol: false,
          data: fan.map((p) => [p.date, p.p50]),
          lineStyle: { width: 2, color: "#9dffb0" },
          z: 4,
        },
        {
          name: "Cruise",
          type: "line",
          showSymbol: false,
          data: fan.map((p) => [p.date, p.cruise]),
          lineStyle: { width: 1, type: "dashed", color: "rgba(51,255,102,0.35)" },
          z: 3,
        },
      ],
    });
    el.dataset.status = "ready";
    const stats = document.getElementById("forecast-fan-stats");
    if (stats && doc.horizons && doc.horizons[1]) {
      const y = doc.horizons[1];
      stats.textContent = "1y P50 " + fmtUSD(y.p50) + " · as of " + doc.as_of;
    }
    BER._forecastFanChart = chart;
    window.addEventListener("resize", () => chart.resize(), { passive: true });
  }

  function renderMissChart(echarts) {
    const el = document.getElementById("forecast-learning-chart");
    const miss = BER._forecastMissByYear || [];
    if (!el || !echarts || !miss.length) {
      if (el) el.dataset.status = "empty";
      return;
    }
    const chart = echarts.init(el, null, { renderer: "canvas" });
    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 40, right: 12, top: 16, bottom: 28 },
      xAxis: {
        type: "category",
        data: miss.map((m) => String(m.year)),
        axisLabel: { color: "#6f9f80" },
      },
      yAxis: {
        type: "value",
        name: "× miss",
        axisLabel: { color: "#6f9f80" },
        splitLine: { lineStyle: { color: "rgba(51,255,102,0.08)" } },
      },
      series: [
        {
          type: "bar",
          data: miss.map((m) => m.mae_factor),
          itemStyle: { color: "rgba(51,255,102,0.55)" },
        },
      ],
      tooltip: { trigger: "axis" },
    });
    el.dataset.status = "ready";
  }

  async function bootForecast() {
    try {
      const res = await fetch("data/scenario_forecast.json");
      if (!res.ok) throw new Error("HTTP " + res.status);
      const doc = await res.json();
      // Client-side scrub belt-and-suspenders
      if (doc.holdings || doc.wallet) throw new Error("refusing private fields");
      BER.scenarioForecast = doc;
      paintRanges(doc);
      paintEnsemble(doc);
      paintMatured(doc);
      const tryChart = () => {
        if (window.echarts) {
          renderFanChart(doc, window.echarts);
          renderMissChart(window.echarts);
          return true;
        }
        return false;
      };
      if (!tryChart()) {
        // charts.js loads echarts lazily; retry a few times after Models IO
        let n = 0;
        const t = setInterval(() => {
          n++;
          if (tryChart() || n > 40) clearInterval(t);
        }, 250);
      }
    } catch (e) {
      console.warn("scenario_forecast unavailable", e);
      const disc = document.getElementById("forecast-disclaimer");
      if (disc) disc.textContent = "Scenario forecast unavailable — check data/scenario_forecast.json after the daily pipeline.";
    }
  }

  function initNav() {
    const links = [...document.querySelectorAll(".site-nav-links a")];
    if (!links.length) return;
    const map = Object.fromEntries(links.map((a) => [a.getAttribute("data-nav"), a]));
    const ids = ["live", "models", "forecast", "health"];
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (!e.isIntersecting) return;
          links.forEach((a) => a.classList.remove("is-active"));
          const a = map[e.target.id];
          if (a) a.classList.add("is-active");
        });
      },
      { rootMargin: "-25% 0px -55% 0px", threshold: 0.01 }
    );
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (el) io.observe(el);
    });
    // Hash on load
    if (location.hash && map[location.hash.slice(1)]) {
      map[location.hash.slice(1)].classList.add("is-active");
    } else if (map.live) map.live.classList.add("is-active");
  }

  document.addEventListener("DOMContentLoaded", () => {
    initNav();
    bootForecast();
  });
})();
