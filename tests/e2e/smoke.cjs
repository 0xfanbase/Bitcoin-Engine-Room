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
  await page.locator("#power-law-card").scrollIntoViewIfNeeded();
  const ready = await page
    .waitForFunction(() => ["power-law-chart", "cycle-overlay-chart", "mayer-200wma-chart", "market-sentiment-chart", "track-record-chart"].every((id) => document.getElementById(id).dataset.status === "ready"), null, { timeout: 30000 })
    .then(() => true, () => false);
  check(ready, `[${viewportName}] every chart reaches status=ready`);

  const state = () => page.evaluate(() => ({ ...window.BER.chartsDebug.plState }));
  check((await state()).range === "2030", `[${viewportName}] hero defaults to the ->2030 view`);
  check(/pl=2030/.test(page.url()), `[${viewportName}] view state is reflected in the URL`);

  await page.click('[data-power-law-range="1y"]');
  check((await state()).range === "1y" && /pl=1y/.test(page.url()), `[${viewportName}] 1Y chip switches the view`);
  await page.click('[data-power-law-time="cal"]');
  check((await state()).timeScale === "cal", `[${viewportName}] calendar-time toggle`);
  await page.click('[data-power-law-time="log"]');
  await page.click('[data-power-law-range="2030"]');

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

    await page.click('[data-power-law-zoom="in"]');
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
    await page.locator("#power-law-chart").scrollIntoViewIfNeeded();
    const b = await page.locator("#power-law-chart").boundingBox();
    const sy0 = await page.evaluate(() => window.scrollY);
    await swipe(b.x + b.width / 2, b.y + b.height * 0.7, 0, -200);
    check((await page.evaluate(() => window.scrollY)) > sy0 + 50, "[mobile] vertical swipe on the hero chart scrolls the page");
    const b2 = await page.locator("#power-law-chart").boundingBox();
    await swipe(b2.x + b2.width * 0.7, b2.y + b2.height / 2, -150, 0);
    check((await state()).view !== null, "[mobile] horizontal swipe pans the hero chart");
  }

  await page.click("#power-law-lookup-disclosure summary");
  await page.fill("#lookup-date", "2030-06-01");
  await page.fill("#lookup-price", "250000");
  check(/Cruise \$/.test(await page.textContent("#lookup-date-out")), `[${viewportName}] date lookup`);
  check(/Cruise/.test(await page.textContent("#lookup-price-out")), `[${viewportName}] price lookup`);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(overflow <= 0, `[${viewportName}] no horizontal page overflow (${overflow}px)`);
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
