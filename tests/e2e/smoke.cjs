// End-to-end smoke test of the real page in headless Chromium.
//
//   python -m http.server 8765 &   (from the repo root)
//   npm i --no-save playwright@1.56.1 && npx playwright install chromium
//   BASE_URL=http://localhost:8765/ node tests/e2e/smoke.cjs
//
// Optional: ECHARTS_LOCAL=/path/to/echarts.min.js serves the pinned ECharts
// build from disk (sandboxes without CDN access); live API calls are always
// blocked so the run is deterministic and exercises the STALE fallbacks.
const path = require("path");
let chromium;
try {
  ({ chromium } = require("playwright"));
} catch (e) {
  ({ chromium } = require(path.join(require("child_process").execSync("npm root -g").toString().trim(), "playwright")));
}

const BASE = process.env.BASE_URL || "http://localhost:8765/";
const failures = [];
const check = (cond, msg) => {
  if (!cond) failures.push(msg);
  console.log(`${cond ? "ok  " : "FAIL"} ${msg}`);
};

/** Sticky site-nav sits over Models chips after scrollIntoView. force:true still
 *  misses on mobile touch contexts in CI — scroll the target clear of the nav,
 *  then fire a real DOM click (same handler path as a user tap). */
async function tapClearOfNav(page, selector) {
  await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    const nav = document.getElementById("site-nav");
    if (!el) throw new Error("missing " + sel);
    const navH = nav ? nav.getBoundingClientRect().height : 0;
    const top = el.getBoundingClientRect().top + window.scrollY;
    window.scrollTo(0, Math.max(0, top - navH - 12));
    el.click();
  }, selector);
  await page.waitForTimeout(80);
}

async function run(viewportName) {
  const mobile = viewportName === "mobile";
  const browser = await chromium.launch({ args: ["--proxy-bypass-list=<-loopback>"] });
  const ctx = await browser.newContext({
    viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 },
    isMobile: mobile,
    hasTouch: mobile,
    reducedMotion: "reduce",
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route(/^(https|wss):/, (route) => {
    const url = route.request().url();
    if (url.includes("cdn.jsdelivr.net/npm/echarts")) {
      if (process.env.ECHARTS_LOCAL) return route.fulfill({ path: process.env.ECHARTS_LOCAL, contentType: "application/javascript", headers: { "Access-Control-Allow-Origin": "*" } });
      return route.continue();
    }
    if (url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")) return route.continue();
    return route.abort();
  });

  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(200);
  const bootNav = await page.evaluate(() => {
    const live = document.querySelector('a[data-nav="live"]');
    const pill = document.querySelector(".site-nav-pill");
    return {
      liveActive: !!(live && live.classList.contains("is-active")),
      pillReady: !!(pill && pill.classList.contains("is-ready")),
    };
  });
  check(bootNav.liveActive, `[${viewportName}] Live is active on fresh load`);
  check(bootNav.pillReady, `[${viewportName}] nav pill ready on fresh load`);
  await page.locator("#power-law-card").scrollIntoViewIfNeeded();
  const ready = await page
    .waitForFunction(() => ["power-law-chart", "cycle-overlay-chart", "mayer-200wma-chart", "market-sentiment-chart", "track-record-chart"].every((id) => document.getElementById(id).dataset.status === "ready"), null, { timeout: 30000 })
    .then(() => true, () => false);
  check(ready, `[${viewportName}] every chart reaches status=ready`);

  const state = () => page.evaluate(() => ({ ...window.BER.chartsDebug.plState }));
  check((await state()).range === "2030", `[${viewportName}] hero defaults to the ->2030 view`);
  check(/pl=2030/.test(page.url()), `[${viewportName}] view state is reflected in the URL`);

  await tapClearOfNav(page, '[data-power-law-range="1y"]');
  check((await state()).range === "1y" && /pl=1y/.test(page.url()), `[${viewportName}] 1Y chip switches the view`);
  await tapClearOfNav(page, '[data-power-law-time="cal"]');
  check((await state()).timeScale === "cal", `[${viewportName}] calendar-time toggle`);
  await tapClearOfNav(page, '[data-power-law-time="log"]');
  await tapClearOfNav(page, '[data-power-law-range="2030"]');

  const headline = await page.textContent("#power-law-headline");
  check(/below trend|above trend/.test(headline) && /percentile/.test(headline), `[${viewportName}] headline reading rendered`);
  check((await page.locator("#track-record-tbody tr").count()) === 3, `[${viewportName}] track record lists 1y/2y/4y`);
  check((await page.locator("#power-law-projections-tbody tr").count()) >= 4, `[${viewportName}] projection table rendered`);

  if (!mobile) {
    const box = await page.locator("#power-law-chart").boundingBox();
    await page.mouse.move(box.x + box.width * 0.85, box.y + box.height * 0.5);
    await page.waitForTimeout(300);
    const tip = await page.evaluate(() => [...document.querySelectorAll("#power-law-chart div")].map((d) => d.textContent).join(" "));
    check(/Redline/.test(tip) && /Cruise/.test(tip) && /projection/.test(tip), "[desktop] tooltip shows model values in the future");

    await page.locator('[data-power-law-zoom="in"]').click({ force: true });
    check((await state()).view !== null, "[desktop] zoom-in button zooms");
    await page.keyboard.press("0");
    await page.locator("#power-law-chart").focus();
    await page.keyboard.press("0");
    check((await state()).view === null, "[desktop] key 0 resets the view");

    // Focused wheel: zoom in, then back out past where it started.
    await page.locator("#power-law-chart").click({ position: { x: box.width * 0.5, y: box.height * 0.5 } });
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    await page.mouse.wheel(0, -200);
    await page.waitForTimeout(200);
    const zoomedIn = (await state()).view;
    await page.mouse.wheel(0, 200);
    await page.waitForTimeout(150);
    await page.mouse.wheel(0, 200);
    await page.waitForTimeout(200);
    const zoomedOut = (await state()).view;
    check(zoomedIn && zoomedOut && zoomedOut.maxDay - zoomedOut.minDay > zoomedIn.maxDay - zoomedIn.minDay, "[desktop] focused wheel zooms in and back out");
    await page.keyboard.press("0");

    await page.locator("h1").click();
    const y0 = await page.evaluate(() => window.scrollY);
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    await page.mouse.wheel(0, 300);
    await page.waitForTimeout(300);
    check((await page.evaluate(() => window.scrollY)) > y0, "[desktop] plain wheel over an unfocused chart still scrolls the page");
  }

  if (mobile) {
    // Real touch swipes via CDP: vertical must scroll the page (the chart is
    // half the viewport), horizontal must pan the chart.
    const cdp = await ctx.newCDPSession(page);
    const swipe = async (x, y, dx, dy) => {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
      for (let i = 1; i <= 10; i++) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x + (dx * i) / 10, y: y + (dy * i) / 10 }] });
      await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await page.waitForTimeout(400);
    };
    // Keep chart top edge below sticky nav so CDP touches hit the canvas,
    // not the nav scrim (taller after safe-area padding).
    await page.evaluate(() => {
      const chart = document.getElementById("power-law-chart");
      const nav = document.getElementById("site-nav");
      const navH = nav ? nav.getBoundingClientRect().height : 0;
      const top = chart.getBoundingClientRect().top + window.scrollY;
      window.scrollTo(0, Math.max(0, top - navH - 16));
    });
    await page.waitForTimeout(100);
    const b = await page.locator("#power-law-chart").boundingBox();
    const sy0 = await page.evaluate(() => window.scrollY);
    await swipe(b.x + b.width / 2, b.y + b.height * 0.7, 0, -200);
    check((await page.evaluate(() => window.scrollY)) > sy0 + 50, "[mobile] vertical swipe on the hero chart scrolls the page");
    // Vertical pan-y scrolls the chart under the sticky nav; re-clear before
    // the horizontal dataZoom gesture or CDP touches hit the nav scrim.
    await page.evaluate(() => {
      const chart = document.getElementById("power-law-chart");
      const nav = document.getElementById("site-nav");
      const navH = nav ? nav.getBoundingClientRect().height : 0;
      const top = chart.getBoundingClientRect().top + window.scrollY;
      window.scrollTo(0, Math.max(0, top - navH - 16));
    });
    await page.waitForTimeout(100);
    const b2 = await page.locator("#power-law-chart").boundingBox();
    await swipe(b2.x + b2.width * 0.8, b2.y + b2.height / 2, -180, 0);
    if ((await state()).view === null) {
      await page.evaluate(() => {
        const chart = document.getElementById("power-law-chart");
        const nav = document.getElementById("site-nav");
        const navH = nav ? nav.getBoundingClientRect().height : 0;
        const top = chart.getBoundingClientRect().top + window.scrollY;
        window.scrollTo(0, Math.max(0, top - navH - 16));
      });
      const b3 = await page.locator("#power-law-chart").boundingBox();
      await swipe(b3.x + b3.width * 0.85, b3.y + b3.height * 0.55, -220, 0);
    }
    check((await state()).view !== null, "[mobile] horizontal swipe pans the hero chart");
  }

  await page.locator("#power-law-lookup-disclosure summary").click({ force: true });
  await page.fill("#lookup-date", "2030-06-01");
  await page.fill("#lookup-price", "250000");
  check(/Cruise \$/.test(await page.textContent("#lookup-date-out")), `[${viewportName}] date lookup`);
  check(/Cruise/.test(await page.textContent("#lookup-price-out")), `[${viewportName}] price lookup`);

  // Sticky nav + Forecast section (PR forecast-p1-calibration)
  check(await page.locator("#site-nav").count() === 1, `[${viewportName}] sticky site-nav present`);
  check(await page.locator(".site-nav-pill").count() === 1, `[${viewportName}] sliding nav pill present`);
  check(await page.locator("#forecast").count() === 1, `[${viewportName}] #forecast section present`);
  check(await page.locator('a[data-nav="forecast"]').count() === 1, `[${viewportName}] Forecast nav link`);
  const navCss = await page.evaluate(() => {
    const nav = document.getElementById("site-nav");
    const cs = getComputedStyle(nav);
    const pill = document.querySelector(".site-nav-pill");
    const active = document.querySelector(".site-nav-links a.is-active");
    return {
      position: cs.position,
      padTop: cs.paddingTop,
      bg: cs.backgroundColor,
      pillReady: pill && pill.classList.contains("is-ready"),
      pillWidth: pill ? parseFloat(getComputedStyle(pill).width) : 0,
      activeId: active ? active.getAttribute("data-nav") : null,
      viewportFit: (document.querySelector('meta[name="viewport"]') || {}).content || "",
    };
  });
  check(navCss.position === "sticky", `[${viewportName}] site-nav is sticky`);
  check(/viewport-fit=cover/.test(navCss.viewportFit), `[${viewportName}] viewport-fit=cover for safe-area`);
  check(navCss.pillReady && navCss.pillWidth > 20, `[${viewportName}] nav pill measured on active tab (${navCss.pillWidth})`);
  // By this point smoke has scrolled into #power-law-card (Models).
  check(navCss.activeId === "models" || navCss.activeId === "live", `[${viewportName}] scrollspy active tab is Models or Live (got ${navCss.activeId})`);
  // Tap Forecast — pill should follow; only Forecast gets is-active (Live muted)
  await page.locator('a[data-nav="forecast"]').click();
  await page.waitForTimeout(350);
  const afterTap = await page.evaluate(() => {
    const act = [...document.querySelectorAll(".site-nav-links a.is-active")].map((a) => a.getAttribute("data-nav"));
    const pill = document.querySelector(".site-nav-pill");
    const forecast = document.querySelector('a[data-nav="forecast"]');
    const fr = forecast.getBoundingClientRect();
    const pr = pill.getBoundingClientRect();
    const overlap = Math.min(fr.right, pr.right) - Math.max(fr.left, pr.left);
    return { act, overlap, pillW: pr.width, linkW: fr.width };
  });
  check(afterTap.act.length === 1 && afterTap.act[0] === "forecast", `[${viewportName}] tap Forecast → sole active tab`);
  check(afterTap.overlap > afterTap.linkW * 0.6, `[${viewportName}] sliding pill overlaps Forecast tab`);
  const scen = await page.evaluate(async () => {
    const r = await fetch("data/scenario_forecast.json");
    if (!r.ok) return { ok: false, status: r.status };
    const j = await r.json();
    return {
      ok: true,
      schema: j.schema_version,
      hasShare: Array.isArray(j.ensemble) && j.ensemble.some((e) => e.share != null),
      hids: (j.horizons || []).map((h) => h.id),
      family: j.method && j.method.family,
      forbidden: !!(j.holdings || j.wallet),
      closedLoop: !!(j.learning && j.learning.correction && j.learning.correction.status),
      hasClosedLoopEnsemble: Array.isArray(j.ensemble) && j.ensemble.some((e) => e.id === "closed_loop"),
    };
  });
  check(scen.ok, `[${viewportName}] scenario_forecast.json fetchable`);
  if (scen.ok) {
    check(scen.schema >= 3, `[${viewportName}] scenario schema_version>=3 (got ${scen.schema})`);
    check(!scen.hasShare, `[${viewportName}] ensemble has no decorative share weights`);
    check(!scen.forbidden, `[${viewportName}] no holdings/wallet fields`);
    check(scen.hids.includes("1y") && scen.hids.includes("2y"), `[${viewportName}] horizons include 1y+2y`);
    check(/ar1|conformal/i.test(scen.family || ""), `[${viewportName}] method family mentions ar1 (${scen.family})`);
    check(scen.closedLoop, `[${viewportName}] learning.correction.status present`);
    check(scen.hasClosedLoopEnsemble, `[${viewportName}] ensemble includes closed_loop`);
  }
  await page.goto(BASE + "/#forecast", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(500);
  const scrolled = await page.evaluate(() => {
    const el = document.getElementById("forecast");
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.top < window.innerHeight * 0.55;
  });
  check(scrolled, `[${viewportName}] #forecast hash deep-link scrolls into view`);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(overflow <= 0, `[${viewportName}] no horizontal page overflow (${overflow}px)`);

  // Instrument stack: Role/Trust note must wrap inside the card (nowrap used
  // to push prose past the panel edge on phones while page overflow stayed 0
  // because html { overflow-x: hidden } clips scrollWidth).
  await page.locator("#forecast-ensemble-card").scrollIntoViewIfNeeded();
  await page.waitForFunction(() => {
    const tb = document.getElementById("forecast-ensemble-tbody");
    return tb && tb.querySelectorAll("tr").length >= 3 && !/loading/i.test(tb.textContent || "");
  }, null, { timeout: 15000 }).catch(() => null);
  const stackFit = await page.evaluate(() => {
    const card = document.getElementById("forecast-ensemble-card");
    const table = document.getElementById("forecast-ensemble-table");
    if (!card || !table) return { ok: false, reason: "missing nodes" };
    const cr = card.getBoundingClientRect();
    const cells = [...table.querySelectorAll("tbody td")];
    if (!cells.length) return { ok: false, reason: "no cells" };
    let maxBleed = 0;
    for (const td of cells) {
      const r = td.getBoundingClientRect();
      maxBleed = Math.max(maxBleed, r.right - cr.right, cr.left - r.left);
    }
    const cs = getComputedStyle(table.querySelector("td:nth-child(2)") || table);
    return {
      ok: maxBleed <= 1.5,
      maxBleed,
      whiteSpace: cs.whiteSpace,
      rows: cells.length,
    };
  });
  check(stackFit.ok, `[${viewportName}] Instrument stack cells inside card (bleed ${stackFit.maxBleed}px, white-space=${stackFit.whiteSpace})`);

  check(errors.length === 0, `[${viewportName}] no uncaught page errors ${errors.length ? JSON.stringify(errors) : ""}`);
  await browser.close();
}

(async () => {
  await run("desktop");
  await run("mobile");
  if (failures.length) {
    console.error(`\n${failures.length} check(s) failed`);
    process.exit(1);
  }
  console.log("\nall checks passed");
})();
