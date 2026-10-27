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

  // Defense-in-depth: never concatenate pipeline JSON into HTML.
  // Poisoned scenario_forecast.json (compromised Actions push / bad PR)
  // must not become executable markup — textContent / createElement only.
  function mk(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null && text !== "") node.textContent = String(text);
    return node;
  }

  // P10/P90 are real names only through the conformal horizon. Past that the
  // edges are the Models Idle–Redline corridor (p10/p90 keys stay as aliases).
  const CONFORMAL_LABEL_MAX_DAYS = 1461;
  const UNEARNED_INDEP_BELOW = 5;
  const RIBBON_DISCLAIMER =
    "Beyond 4y this is a power-law residual ribbon, not a probability.";

  function bandLabels(h) {
    const labels = h && h.band_labels;
    if (labels && labels.low && labels.mid && labels.high) return labels;
    const src = String((h && h.band_source) || "").split("+")[0];
    const days = Number((h && h.days) || 0);
    if (src.indexOf("taper") === 0) return { low: "Idle", mid: "P50", high: "Redline", kind: "taper" };
    if (src.indexOf("idle_redline") === 0 || days >= 3652) {
      return { low: "Idle", mid: "Cruise", high: "Redline", kind: "corridor" };
    }
    if (days > CONFORMAL_LABEL_MAX_DAYS) return { low: "Idle", mid: "P50", high: "Redline", kind: "taper" };
    return { low: "P10", mid: "P50", high: "P90", kind: "conformal" };
  }

  function fmtWindows(n) {
    const x = Number(n);
    if (!isFinite(x)) return "—";
    return Math.abs(x - Math.round(x)) < 0.05 ? String(Math.round(x)) : x.toFixed(1);
  }

  function isUnearned(h) {
    if (h && typeof h.coverage_earned === "boolean") return !h.coverage_earned;
    const labels = bandLabels(h || {});
    if (labels.kind !== "conformal") return true;
    const n = h && h.conformal_indep_windows;
    if (n == null || n === "") return true;
    return Number(n) < UNEARNED_INDEP_BELOW;
  }

  function closedLoopState(doc) {
    const corr = (doc && doc.learning && doc.learning.correction) || {};
    const n = Number(corr.n_matured_total || 0);
    const active = corr.status === "active" && n > 0;
    return { active: active, n: n };
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
    const ribbon = document.getElementById("forecast-ribbon-disclaimer");
    if (ribbon) ribbon.textContent = RIBBON_DISCLAIMER;
    root.textContent = "";
    for (const h of doc.horizons || []) {
      const labels = bandLabels(h);
      const card = mk(
        "div",
        "forecast-range" + (h.in_live_ledger ? " fr-ledger" : " fr-scenario")
      );
      const head = mk("div", "fr-h");
      head.appendChild(document.createTextNode((h.label || "") + " "));
      head.appendChild(
        mk(
          "span",
          h.in_live_ledger ? "fr-tag" : "fr-tag fr-tag-scenario",
          h.in_live_ledger ? "ledger" : "scenario"
        )
      );
      const nInd = h.conformal_indep_windows;
      const indep = mk(
        "span",
        "fr-indep numeral",
        nInd == null || nInd === "" ? "indep —" : "indep " + fmtWindows(nInd)
      );
      indep.title =
        "Approximate independent (non-overlapping) windows behind this band. Unearned when fewer than 5, or when the band is the Idle–Redline corridor.";
      head.appendChild(indep);
      if (isUnearned(h)) {
        const badge = mk("span", "fr-tag fr-tag-unearned", "unearned");
        badge.title = "Sample too thin to earn a calibrated percentile, or this horizon is the corridor.";
        head.appendChild(badge);
      }
      card.appendChild(head);
      const midValue = labels.mid === "Cruise" && h.cruise != null ? h.cruise : h.p50;
      card.appendChild(mk("div", "fr-mid numeral", labels.mid + " " + fmtUSD(midValue)));
      const lo = labels.low === "Idle" && h.idle != null ? h.idle : h.p10;
      const hi = labels.high === "Redline" && h.redline != null ? h.redline : h.p90;
      card.appendChild(
        mk("div", "fr-band numeral", labels.low + " " + fmtUSD(lo) + " · " + labels.high + " " + fmtUSD(hi))
      );
      const xBits = [];
      if (h.target_date) xBits.push(String(h.target_date));
      if (h.multiple_vs_spot_p50 != null) xBits.push("×" + h.multiple_vs_spot_p50 + " spot");
      card.appendChild(mk("div", "fr-x", xBits.join(" · ")));
      const src = h.band_source ? String(h.band_source).replace(/_/g, " ") : "";
      const caveatBits = [];
      if (src) caveatBits.push(src);
      if (h.caveat) caveatBits.push(String(h.caveat));
      if (caveatBits.length) card.appendChild(mk("div", "fr-caveat", caveatBits.join(" · ")));
      root.appendChild(card);
    }
  }

  function paintCagr(doc) {
    const tb = document.getElementById("forecast-cagr-tbody");
    if (!tb) return;
    tb.textContent = "";
    const spot = Number(doc && doc.spot);
    const rows = (doc.horizons || []).filter((h) => bandLabels(h).kind === "corridor");
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = mk("td", null, "No Idle–Cruise horizons in this file.");
      td.colSpan = 3;
      tr.appendChild(td);
      tb.appendChild(tr);
      return;
    }
    function rate(h, key, future) {
      if (h[key] != null && isFinite(Number(h[key]))) return Number(h[key]);
      const days = Number(h.days);
      if (!spot || !future || !days) return null;
      const years = days / 365.25;
      if (years <= 0 || future <= 0) return null;
      return Math.pow(future / spot, 1 / years) - 1;
    }
    function fmtPct(x) {
      if (x == null || !isFinite(x)) return "—";
      return (x * 100).toFixed(1) + "%";
    }
    for (const h of rows) {
      const labels = bandLabels(h);
      const lo = labels.low === "Idle" && h.idle != null ? h.idle : h.p10;
      const mid = h.cruise != null ? h.cruise : h.p50;
      const tr = document.createElement("tr");
      tr.appendChild(mk("td", null, h.label || h.id || ""));
      tr.appendChild(mk("td", "numeral", fmtPct(rate(h, "cagr_to_idle", lo))));
      tr.appendChild(mk("td", "numeral", fmtPct(rate(h, "cagr_to_cruise", mid))));
      tb.appendChild(tr);
    }
  }

  function paintWalkforward(doc) {
    const el = document.getElementById("forecast-walkforward-miss");
    if (!el) return;
    const rows = (doc && doc.learning && doc.learning.backtest_horizons) || [];
    const y = rows.find((h) => h && h.horizon_days === 365);
    const f = y && y.trend_mae_factor;
    if (f == null || !isFinite(Number(f))) {
      el.textContent = "1y walk-forward miss unavailable.";
      return;
    }
    const n = Number(f);
    const shown = n >= 10 ? n.toFixed(1) : n.toFixed(2);
    const about = Math.round(n) === 2 ? " (about ×2)" : "";
    el.textContent = "";
    el.appendChild(document.createTextNode("1y walk-forward miss "));
    el.appendChild(mk("span", "numeral", "×" + shown + about));
    el.appendChild(
      document.createTextNode(
        ". Track Record scar — typical factor, either direction. Not a confidence interval."
      )
    );
  }

  function ensembleRole(row, doc) {
    if (!row) return "";
    if (row.id === "idle_redline_cap") return "Idle / Redline for 10y, 20y, and 30y";
    const loop = closedLoopState(doc);
    if (row.id === "closed_loop") {
      return loop.active ? "Shrunk nudge from matured ledger misses" : "Inactive — no live recalibration";
    }
    return row.role || "";
  }

  function ensembleNote(row, doc) {
    if (!row) return "";
    if (row.id === "idle_redline_cap") {
      return "Same empirical residual envelope as Models — a scenario ribbon, not a calibrated percentile.";
    }
    const loop = closedLoopState(doc);
    if (row.id === "closed_loop") {
      if (loop.active) return "Capped nudge from " + loop.n + " matured calls. Not a confidence interval.";
      return loop.n + " matured. Forecasts are not adjusted from live outcomes.";
    }
    return row.trust_note || "";
  }

  function paintEnsemble(doc) {
    const tb = document.getElementById("forecast-ensemble-tbody");
    if (!tb) return;
    tb.textContent = "";
    for (const row of doc.ensemble || []) {
      const tr = document.createElement("tr");
      tr.appendChild(mk("td", null, row.name || row.id || ""));
      tr.appendChild(mk("td", null, ensembleRole(row, doc)));
      tr.appendChild(mk("td", null, ensembleNote(row, doc)));
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
      const loop = closedLoopState(doc);
      const corrBit = loop.active
        ? " Closed-loop active (matured n=" + loop.n + ") — capped nudge only, not a confidence interval."
        : " Closed-loop inactive (" + loop.n + " matured) — no live recalibration.";
      sum.textContent = matured.length
        ? matured.length +
          " matured ledger call(s). Walk-forward miss-by-year chart below mirrors Track Record." +
          corrBit
        : "Live ledger has no matured horizons yet — miss-by-year still comes from the walk-forward backtest." +
          corrBit;
    }
    tb.textContent = "";
    if (!matured.length) {
      const tr = document.createElement("tr");
      const td = mk(
        "td",
        null,
        "No matured ledger rows yet — entries appear after their horizon elapses. Ledger freezes 30d / 90d / 1y / 2y / 4y."
      );
      td.colSpan = 4;
      tr.appendChild(td);
      tb.appendChild(tr);
      BER._forecastMissByYear = miss;
      return;
    }
    for (const m of matured.slice(-12)) {
      const tr = document.createElement("tr");
      const actual =
        m.outcome && typeof m.outcome === "object" ? m.outcome.actual : m.outcome;
      tr.appendChild(mk("td", "numeral", m.issued || ""));
      tr.appendChild(mk("td", "numeral", (m.horizon_days || "") + "d"));
      tr.appendChild(mk("td", "numeral", fmtUSD(m.trend)));
      tr.appendChild(mk("td", "numeral", fmtUSD(actual)));
      tb.appendChild(tr);
    }
    BER._forecastMissByYear = miss;
  }

  function fanEdge(p, asOf) {
    const labels = p && p.band_labels;
    if (labels && labels.low) return labels.low === "P10" ? "P10" : "Idle";
    const src = String((p && p.band_source) || "").split("+")[0];
    if (src.indexOf("taper") === 0 || src.indexOf("idle") === 0) return "Idle";
    if (p && p.days != null && Number(p.days) > CONFORMAL_LABEL_MAX_DAYS) return "Idle";
    if (asOf && p && p.date) {
      const days = (Date.parse(p.date) - Date.parse(asOf)) / 86400000;
      if (days > CONFORMAL_LABEL_MAX_DAYS) return "Idle";
    }
    return "P10";
  }

  function renderFanChart(doc, echarts) {
    const el = document.getElementById("forecast-fan-chart");
    if (!el || !echarts || !doc) return;
    const hist = doc.history || [];
    const fan = doc.fan || [];
    const asOf = doc.as_of;
    const p10 = [];
    const p90 = [];
    const idle = [];
    const redline = [];
    for (const p of fan) {
      const edge = fanEdge(p, asOf);
      if (edge === "P10") {
        p10.push([p.date, p.p10]);
        p90.push([p.date, p.p90]);
      } else {
        idle.push([p.date, p.idle != null ? p.idle : p.p10]);
        redline.push([p.date, p.redline != null ? p.redline : p.p90]);
      }
    }
    // Share the conformal boundary so the ribbon does not gap when the name changes.
    if (p10.length && idle.length) {
      idle.unshift(p10[p10.length - 1]);
      redline.unshift(p90[p90.length - 1]);
    }
    const head = document.getElementById("forecast-fan-headline");
    if (head) head.textContent = "P50 with P10–P90 through 4y; Idle–Redline corridor beyond";
    const chart = echarts.init(el, null, { renderer: "canvas" });
    chart.setOption({
      backgroundColor: "transparent",
      legend: {
        type: "scroll",
        bottom: 0,
        textStyle: { color: "#9dffb0", fontSize: 10 },
        itemWidth: 14,
        itemHeight: 8,
        data: ["spot", "P90", "P10", "Redline", "Idle", "P50 (AR1→Cruise)", "Cruise"],
      },
      grid: { left: 48, right: 16, top: 16, bottom: 52 },
      tooltip: {
        trigger: "axis",
        valueFormatter: (v) => fmtUSD(v),
      },
      xAxis: {
        type: "time",
        axisLabel: { color: "#6f9f80" },
        axisLine: { lineStyle: { color: "rgba(51,255,102,0.25)" } },
      },
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
          data: p90,
          lineStyle: { width: 1, color: "rgba(51,255,102,0.55)" },
          z: 2,
        },
        {
          name: "P10",
          type: "line",
          showSymbol: false,
          data: p10,
          lineStyle: { width: 1, color: "rgba(51,255,102,0.55)" },
          z: 2,
        },
        {
          name: "Redline",
          type: "line",
          showSymbol: false,
          data: redline,
          lineStyle: { width: 1.25, type: "dashed", color: "rgba(51,255,102,0.75)" },
          z: 2,
        },
        {
          name: "Idle",
          type: "line",
          showSymbol: false,
          data: idle,
          lineStyle: { width: 1.25, type: "dashed", color: "rgba(51,255,102,0.75)" },
          z: 2,
        },
        {
          name: "P50 (AR1→Cruise)",
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
    if (stats && doc.horizons) {
      const y = doc.horizons.find((h) => h.id === "1y") || doc.horizons[1];
      if (y) stats.textContent = "1y P50 " + fmtUSD(y.p50) + " · as of " + doc.as_of;
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
      if (doc.holdings || doc.wallet) throw new Error("refusing private fields");
      BER.scenarioForecast = doc;
      paintRanges(doc);
      paintCagr(doc);
      paintWalkforward(doc);
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
        let n = 0;
        const t = setInterval(() => {
          n++;
          if (tryChart() || n > 40) clearInterval(t);
        }, 250);
      }
    } catch (e) {
      console.warn("scenario_forecast unavailable", e);
      const disc = document.getElementById("forecast-disclaimer");
      if (disc)
        disc.textContent =
          "Scenario forecast unavailable — check data/scenario_forecast.json after the daily pipeline.";
    }
  }

  function initNav() {
    const links = [...document.querySelectorAll(".site-nav-links a")];
    const linksRoot = document.querySelector(".site-nav-links");
    const pill = document.querySelector(".site-nav-pill");
    const nav = document.getElementById("site-nav");
    if (!links.length || !linksRoot) return;
    const map = Object.fromEntries(
      links.map((a) => [a.getAttribute("data-nav"), a])
    );
    const ids = ["live", "models", "forecast", "health"];
    let activeId = null;
    let lockUntil = 0;

    function movePill(link, instant) {
      if (!pill || !link) return;
      const rootRect = linksRoot.getBoundingClientRect();
      const r = link.getBoundingClientRect();
      const x = r.left - rootRect.left + linksRoot.scrollLeft;
      const w = r.width;
      if (instant) {
        pill.style.transition = "none";
      }
      pill.style.width = Math.max(0, w) + "px";
      pill.style.transform = "translate3d(" + x + "px,0,0)";
      pill.classList.add("is-ready");
      if (instant) {
        void pill.offsetWidth;
        pill.style.transition = "";
      }
    }

    function setActive(id, opts) {
      opts = opts || {};
      if (!id || !map[id]) return;
      const changed = id !== activeId;
      activeId = id;
      if (changed) {
        links.forEach((a) =>
          a.classList.toggle("is-active", a.getAttribute("data-nav") === id)
        );
      }
      movePill(map[id], !!opts.instant);
      if (opts.fromTap) lockUntil = performance.now() + 700;
    }

    function navOffset() {
      // Sticky bar height already includes env(safe-area-inset-top) padding.
      return (nav ? nav.getBoundingClientRect().height : 64) + 8;
    }

    function scrollToId(id, behavior) {
      const el = document.getElementById(id);
      if (!el) return;
      const top = el.getBoundingClientRect().top + window.scrollY - navOffset();
      window.scrollTo({ top: Math.max(0, top), behavior: behavior || "auto" });
    }

    function pickSection() {
      if (performance.now() < lockUntil) return;
      // Near document end → last section (Health can't always reach the probe).
      const atEnd =
        window.scrollY + window.innerHeight >=
        document.documentElement.scrollHeight - 4;
      if (atEnd) {
        setActive(ids[ids.length - 1], {});
        return;
      }
      // Probe just below the sticky nav so the section under the bar wins.
      const probe = navOffset() + 12;
      let best = ids[0];
      for (let i = 0; i < ids.length; i++) {
        const el = document.getElementById(ids[i]);
        if (!el) continue;
        if (el.getBoundingClientRect().top <= probe) best = ids[i];
      }
      setActive(best, {});
    }

    links.forEach((a) => {
      a.addEventListener("click", (e) => {
        const id = a.getAttribute("data-nav");
        if (!id || !map[id]) return;
        e.preventDefault();
        setActive(id, { fromTap: true });
        const reduce =
          window.matchMedia &&
          window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        scrollToId(id, reduce ? "auto" : "smooth");
        if (history.replaceState) {
          history.replaceState(null, "", "#" + id);
        } else {
          location.hash = id;
        }
      });
    });

    let ticking = false;
    window.addEventListener(
      "scroll",
      () => {
        if (ticking) return;
        ticking = true;
        requestAnimationFrame(() => {
          pickSection();
          ticking = false;
        });
      },
      { passive: true }
    );
    window.addEventListener("resize", () => {
      if (activeId && map[activeId]) movePill(map[activeId], true);
      pickSection();
    });

    const hashId = location.hash ? location.hash.slice(1) : "";
    if (hashId && map[hashId]) {
      setActive(hashId, { instant: true });
      requestAnimationFrame(() => {
        scrollToId(hashId, "auto");
        requestAnimationFrame(() => {
          setActive(hashId, { instant: true });
          pickSection();
        });
      });
    } else {
      setActive("live", { instant: true });
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    initNav();
    bootForecast();
  });
})();
