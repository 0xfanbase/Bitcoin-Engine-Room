# BTC ENGINE ROOM — Full Build Specification (v1.0)

> Paste-ready spec for Claude Code. Target: a free, public, live Bitcoin fundamentals + price-model dashboard hosted on GitHub Pages, with daily self-healing, self-auditing pipelines and a weekly self-improvement loop.
> Total running cost: **$0** beyond your existing Claude subscription.

---

## 0. Elevator pitch

**BTC Engine Room** shows the *machinery* of Bitcoin, live: block height, hash rate, difficulty, mempool, fees, supply — plus long-horizon price models (power law corridor, 4-year halving cycle overlay, Mayer Multiple, 200WMA). The differentiator vs. bitbo/lookintobitcoin/etc.: **the engine room is visible.** Every gauge shows its data source, freshness, validation status, and failover state. The site publishes its own daily audit report. Transparency *is* the product.

---

## 1. Legal & IP guardrails (read once, follow always)

**Verdict: what you're planning is fine, provided you follow these rules.** (Not legal advice — Claude is not a lawyer; if you ever commercialize this, get one.)

1. **Facts are not copyrightable.** Hash rate, difficulty, block height, price — nobody owns these numbers. Bitbo doesn't own the *concepts* of a power law chart, halving cycle chart, or rainbow chart either. Ideas and published mathematical models are free to implement.
2. **Never touch bitbo's servers.** Do not scrape bitbo.io or charts.bitbo.io, do not call their API without a key under their terms, do not hotlink their images. We pull everything from upstream public APIs (Section 4) — the same class of sources bitbo itself uses (bitbo publicly credits alternative.me for its Fear & Greed data).
3. **What IS protected:** bitbo's page design, layout, written copy, logo/name, chart styling, and the exact methodology/parameters of their *private/proprietary* charts. Do not copy any of it. Build your own visual identity (Section 9) and fit your own model coefficients from raw data (Section 8).
4. **Rainbow-style bands:** the classic "Bitcoin Rainbow Chart" (Blockchaincenter) has distinctive band labels and colors. Compute your own log-regression bands, use your own colors and your own labels (e.g., "Redline / Cruise / Idle"). Same math family, your own expression.
5. **Attribution requirements (do these):**
   - CoinGecko free/Demo tier **requires attribution** — add "Price data by CoinGecko" with a link in the footer.
   - alternative.me Fear & Greed — credit "Fear & Greed data by alternative.me".
   - Coin Metrics Community data — credit "Historical data by Coin Metrics Community".
   - mempool.space and blockchain.com — attribution not strictly demanded for basic use, but credit them anyway. A full "Data Sources" footer section is good practice and good optics.
6. **Naming:** don't use "bitbo" anywhere. "BTC Engine Room" is descriptive and low-risk; do a quick trademark search (WIPO Global Brand DB / USPTO TESS) before investing in the brand. Buy nothing — the GitHub Pages URL is free.
7. **Site disclaimers (footer):** "Educational tool. Not financial advice. Models are curve fits, not guarantees." — and an MIT license on your own code.

---

## 2. Product definition — how we beat what's out there

Researched landscape: bitbo (free tier = 1h refresh, paid = real-time; power law, S2F, cycle repeat, rainbow charts), lookintobitcoin, checkonchain, timechainstats, blockchain.com charts, mempool.space.

**Gaps we exploit:**

1. **Freshness for free.** Bitbo's free tier refreshes price hourly. We use mempool.space's free WebSocket → new blocks and stats tick in *seconds*, at $0.
2. **Radical transparency.** No one shows source, validation status, cross-source variance, and a public daily audit per metric. We do — the "Engine Health" panel is the signature feature.
3. **Honest models.** We show the power law/cycle models WITH residuals, R², refit-drift history, and confidence bands — not just a pretty rainbow. Positioning: "instruments, not horoscopes."
4. **Open source.** Entire pipeline is a public repo. Anyone can verify every number. Bitbo can't match that without destroying their paid tier.
5. **Zero backend = zero downtime cost.** Static site + client-side live fetch + committed daily snapshots. Nothing to crash at 3am.

**Non-goals (v1):** altcoins, accounts/logins, alerts, mobile app, monetization.

---

## 3. Architecture

**Pattern: "Static core, live skin."** Two data layers:

```
┌──────────────── LIVE LAYER (browser, every few sec/min) ───────────────┐
│  Browser ── WebSocket ──► mempool.space  (new blocks, mempool stats)   │
│  Browser ── fetch (60s) ─► mempool.space REST (price, fees, difficulty)│
│  Browser ── fetch (5m) ──► CoinGecko / Coinbase (price cross-check)    │
│          └─ on failure ──► render last committed snapshot + STALE badge│
└─────────────────────────────────────────────────────────────────────────┘

┌────────── HISTORICAL LAYER (GitHub Actions, daily 06:30 UTC) ──────────┐
│  fetch_snapshot.py ──► pull daily metrics from source chain (Sec. 4)   │
│        │                validate (schema + sanity bounds + cross-check)│
│        ▼                                                                │
│  data/history/*.json  ◄─ append last-good values                        │
│  fit_models.py ──► refit power law, cycle stats ──► data/models.json    │
│  audit.py ──► gap/variance/drift checks ──► data/audit/latest.json      │
│  git commit + push ──► GitHub Pages redeploys automatically             │
│  on failure ──► auto-open GitHub Issue via GITHUB_TOKEN                 │
└─────────────────────────────────────────────────────────────────────────┘
```

**Stack (all free):**
- **Hosting:** GitHub Pages, public repo (`https://<user>.github.io/btc-engine-room/`). Free custom domain support if you ever want one.
- **Automation:** GitHub Actions. Standard runners are free for public repos.
- **Pipeline:** Python 3.12 — `requests`, `numpy` (OLS fit), `jsonschema`. No pandas needed; keep deps minimal = fewer failure modes.
- **Frontend:** **Vanilla HTML/CSS/JS + Apache ECharts (CDN)**. No framework, no build step. Rationale: fastest load, trivially auditable by the self-audit job, log-log axes and dark themes are first-class in ECharts.
- **Testing:** `pytest` for pipeline; a tiny Playwright smoke test (optional, phase 5).

**Why not a server/DB?** Costs money, needs babysitting, and daily-granularity history in committed JSON (~a few MB over years) is well within GitHub limits. Live values never need storing — the chain and the APIs are the database.

---

## 4. Free data source matrix (primary → fallback chain)

| Metric | Primary | Fallback 1 | Fallback 2 |
|---|---|---|---|
| Block height (live) | mempool.space WS `blocks` | mempool.space REST `/api/blocks/tip/height` | blockchain.info `/q/getblockcount` |
| Price (live) | mempool.space `/api/v1/prices` | CoinGecko `/simple/price` | Coinbase `/v2/prices/BTC-USD/spot` |
| Hash rate | mempool.space `/api/v1/mining/hashrate/3d` | blockchain.com charts `hash-rate` | Coin Metrics `HashRate` |
| Difficulty + retarget ETA | mempool.space `/api/v1/difficulty-adjustment` | blockchain.info `/q/getdifficulty` | — |
| Fees (sat/vB) | mempool.space `/api/v1/fees/recommended` | — (mark stale) | — |
| Mempool size / next blocks | mempool.space WS `mempool-blocks`, `stats` | mempool.space REST `/api/mempool` | — |
| Circulating supply | blockchain.info `/q/totalbc` | Coin Metrics `SplyCur` | computed from height (subsidy schedule) |
| Avg block size, tx/day | blockchain.com charts `avg-block-size`, `n-transactions` | Coin Metrics | — |
| Full daily price history (backfill for models) | blockchain.com charts `market-price?timespan=all&sampled=false` | Coin Metrics Community `PriceUSD` | — |
| Fear & Greed | alternative.me `https://api.alternative.me/fng/` | — | — |
| Halving countdown | computed locally from block height (next halving at height 1,050,000) | — | — |

**Key source facts (verified July 2026):**
- **mempool.space** — free REST + WebSocket (`wss://mempool.space/api/v1/ws`), no auth. Rate limits undisclosed and enforced (HTTP 429; repeat abuse = ban). Rule: WebSocket for live, REST polling ≥60s intervals, exponential backoff on 429, honor `Retry-After`. One daily pipeline run is trivially within limits.
- **blockchain.com Charts API** — free JSON, full history: `https://api.blockchain.info/charts/{chart}?timespan=all&format=json&sampled=false`. Perfect for one-time backfill.
- **CoinGecko** — keyless public ~5–30 calls/min (variable); free **Demo key** gives a stable limit (docs cite 100/min, 10k calls/month cap; historical depth on Demo is limited to ~1 year — hence blockchain.com/Coin Metrics for deep history). **Attribution required.**
- **Coin Metrics Community API** — `https://api.coinmetrics.io/v4`, **no key needed** for community endpoints; full daily history for `PriceUSD`, `HashRate`, `SplyCur`, etc. Paginate via `next_page_url`.
- **alternative.me FNG** — free JSON API; credit them (even bitbo does).

**Hard rule for every fetcher:** set a descriptive User-Agent (`btc-engine-room/1.0 (+repo URL)`), timeout=15s, max 3 retries with jitter, cache aggressively, never poll faster than needed. Being a polite client IS the compliance strategy.

---

## 5. Repository structure

```
btc-engine-room/
├── CLAUDE.md                    # project brief + conventions for Claude Code (Sec. 14)
├── IMPROVEMENT_BACKLOG.md       # auto-appended by audit + manual ideas
├── README.md                    # what/why/how, data sources, disclaimers
├── LICENSE                      # MIT
├── index.html                   # the dashboard (single page)
├── assets/
│   ├── style.css
│   ├── app.js                   # boot, layout, panel orchestration
│   ├── live.js                  # WebSocket + polling + client failover
│   ├── charts.js                # ECharts configs (power law, cycle, etc.)
│   └── health.js                # Engine Health panel renderer
├── data/
│   ├── history/
│   │   ├── price_daily.json     # [[dateISO, close], ...] genesis→today
│   │   ├── hashrate_daily.json
│   │   ├── difficulty_daily.json
│   │   ├── supply_daily.json
│   │   └── fng_daily.json
│   ├── models.json              # fitted params, bands, projections
│   ├── health.json              # per-source status manifest
│   └── audit/
│       ├── latest.json
│       └── 2026-07-08.json ...  # keep 90 days, prune older
├── pipeline/
│   ├── sources.py               # one fetcher class per API, common retry/backoff
│   ├── fetch_snapshot.py        # daily append with failover chain
│   ├── backfill.py              # one-time full-history load
│   ├── fit_models.py            # power law OLS, cycle stats, Mayer, 200WMA
│   ├── audit.py                 # Sec. 11 checks → audit/*.json (+ backlog append)
│   ├── schemas/                 # jsonschema files for every data artifact
│   └── tests/                   # pytest: validators, model math, failover logic
└── .github/workflows/
    ├── daily.yml                # 06:30 UTC: snapshot → models → audit → commit
    └── ci.yml                   # on push/PR: pytest + schema-validate all /data
```

---

## 6. Daily pipeline logic (fetch_snapshot.py)

Pseudocode Claude Code should implement faithfully:

```
for metric in METRICS:
    for source in metric.source_chain:            # priority order
        try:
            raw = source.fetch(timeout=15, retries=3, backoff=exp+jitter)
            value = source.parse(raw)
            if not passes_schema(value): raise ValidationError
            if not passes_sanity(metric, value, history): raise SanityError
            record(metric, value, source=source.name, status="OK")
            break
        except Exception as e:
            log(metric, source, e); continue
    else:                                          # all sources failed
        carry_forward_last_good(metric)
        record(metric, status="STALE", stale_since=...)

append_to_history(records)                         # idempotent: skip if date exists
write(health.json)                                 # per-metric: source used, latency,
                                                   # status, consecutive_failures
if any(consecutive_failures >= 3):
    open_or_update_github_issue(labels=["auto","data-outage"])
```

**Sanity bounds (encode as data, not code — `pipeline/sanity_rules.json`):**
- Price: 1,000 < USD < 10,000,000; |Δ day| < 40%.
- Block height: strictly increasing; Δ per day within 100–200 blocks.
- Hash rate: within ±50% of trailing 30-day median.
- Difficulty: changes only at retarget boundaries; |Δ| < 30% per retarget.
- Supply: monotonic, ≤ 21,000,000; consistent with height×subsidy schedule ±0.1%.
- Cross-check: mempool.space price vs CoinGecko price within 1.5% → else flag `VARIANCE_WARN` in health.json (still record primary).

**Workflow (daily.yml) essentials:**
- `on: schedule: cron '30 6 * * *'` + `workflow_dispatch` (manual re-run button).
- `permissions: contents: write, issues: write`.
- Commit as github-actions bot: `chore(data): snapshot 2026-07-08 [skip ci]`.
- **GitHub cron caveats (plan for them):** runs are best-effort — 5–30 min delays are routine; scheduled workflows are auto-disabled after 60 days with no commits. Our job commits daily, which resets the inactivity timer — but if the job is ever disabled (or breaks) that self-reset stops. Mitigations: (a) audit panel on the site shows "last snapshot age" in red if >48h — you'll see it; (b) optional free external ping via healthchecks.io; (c) keep GitHub email notifications on.

---

## 7. Live layer logic (live.js)

1. **WebSocket first:** connect `wss://mempool.space/api/v1/ws`, send `{"action":"want","data":["blocks","stats","mempool-blocks"]}`. On `block` → tick the block odometer, update height/subsidy/halving countdown. On `stats` → mempool count/vsize. Auto-reconnect with capped backoff (max 60s); after 3 failed reconnects, degrade to REST polling.
2. **REST polling (staggered):** price + fees every 60s; difficulty-adjustment every 10 min. Single in-flight request per endpoint; pause all polling when `document.hidden` (battery + politeness).
3. **Client failover:** price falls mempool.space → CoinGecko → Coinbase. If all fail (e.g., ad-blocker), render values from the last committed `data/history` snapshot and show an amber **STALE** chip with age ("as of 06:30 UTC").
4. **Every gauge carries a status chip:** ● LIVE (green) / ● DELAYED (amber, REST-only) / ● STALE (amber, snapshot) — fed by live.js + health.json. This is the transparency signature.

---

## 8. Models & math (fit_models.py — fit your OWN parameters)

All models refit daily from `price_daily.json`. Publishing the fit process is the moat; copying someone's constants is the IP trap. Reference points below are for sanity-checking your fit only.

1. **Power Law Corridor** (model family published openly by Giovanni Santostasi — credit him by name on the chart):
   - Model: `log10(price) = a + b·log10(d)`, where `d` = days since genesis (2009-01-03).
   - Fit: OLS in log-log space over full history from ~2010-07 onward; report `a`, `b`, `R²`, `σ` (std of log10 residuals).
   - Bands: trend ±1σ and ±2σ (floor ≈ trend×10^(−2σ), ceiling ≈ trend×10^(+2σ)).
   - Sanity: published full-history fits land around `b ≈ 5.7–5.9`, `a ≈ −16.5 to −17`, `R² ≈ 0.95`. If your fit is wildly off, the audit flags it.
   - Output: params + current deviation (% above/below trend, and z-score) + projections table (2027/2028/2030/2035 floor/trend/ceiling).
2. **4-Year Cycle Overlay:** slice price by halving epochs (2012-11-28, 2016-07-09, 2020-05-11, 2024-04-20 → next ~2028 at height 1,050,000). Normalize each epoch to its halving-day price, plot % performance vs days-since-halving, current epoch highlighted. Add "days into epoch / % complete" stat.
3. **Mayer Multiple:** price / 200-day SMA, with historical percentile of today's value.
4. **200-Week MA:** value + price distance; long-term floor narrative.
5. **Deviation dial (composite):** average of power-law z-score, Mayer percentile, and cycle-position percentile → one needle from "Idle" (undervalued) to "Redline" (overheated). Label it clearly as a toy composite, methodology published inline.
6. Explicitly **skip Stock-to-Flow in v1** (widely criticized post-2022; add later behind a "legacy models" toggle if wanted).

`models.json` also stores yesterday's params so the audit can compute **drift** (Sec. 11).

---

## 9. Design spec — "the engine room"

Aesthetic thesis: **a ship's engine room / industrial control panel**, not another fintech dashboard. The subject dictates the look: instruments, status lamps, riveted panels, phosphor readouts.

- **Palette (tokens):** `--steel-black #0C0F12` (bg), `--panel #151A20` (cards, 1px `#232B33` border), `--instrument-amber #FFB000` (primary data/accents — classic gauge phosphor), `--signal-green #2FD97B` (OK/live), `--warn-red #FF4747` (faults), `--dial-cream #E8E3D5` (headings/labels). No purple gradients, no glassmorphism.
- **Type:** display = **Archivo Expanded/Black** (industrial signage feel) for the masthead and section plates; data = **IBM Plex Mono** for every number (tabular figures everywhere); labels = IBM Plex Sans. All via Google Fonts (free).
- **Signature element:** the **block-height odometer** — large mechanical-style rolling digits at the top that physically tick over when the WebSocket announces a new block, with a subtle amber flash and "block found · 2m ago" line beneath. It's live proof the engine is running.
- **Layout:** masthead (odometer + price + halving countdown) → "GAUGES" grid of instrument cards (hash rate, difficulty + retarget dial, fees, mempool, supply, F&G) → "PROJECTIONS" (power law chart full-width log-log, cycle overlay, Mayer/200WMA strip, deviation dial) → **"ENGINE HEALTH"** (per-source status lamps, last snapshot age, latest audit pass/fail with expandable detail) → footer (data source credits per Sec. 1.5, disclaimers, GitHub link).
- Section headers styled as stamped metal plates (uppercase, letter-spaced, hairline top rule). Status lamps are small circles with a soft glow — the only glow on the page.
- **Restraint:** the odometer is the one theatrical element. Everything else is quiet, dense, precise. Respect `prefers-reduced-motion` (no odometer roll animation, just swap). Responsive to 375px. Visible keyboard focus.
- Charts: dark ECharts theme matching tokens; power-law bands as translucent amber fills; your own band names ("Redline / Cruise / Idle" — never the rainbow chart's labels).

---

## 10. Self-healing spec (summary of behaviors)

1. **Source failover chains** per metric (Sec. 4/6), pipeline **and** client side.
2. **Retry discipline:** exponential backoff + jitter, honor 429/Retry-After, hard timeouts.
3. **Last-known-good carry-forward** with explicit STALE flags — never blank gauges, never silently fake freshness.
4. **Schema + sanity validation** rejects poisoned data before it enters history (a wrong number is worse than a missing one).
5. **Idempotent, re-runnable pipeline:** re-running a day's job can never duplicate or corrupt history; `workflow_dispatch` = one-click manual heal.
6. **Auto-issue creation** after 3 consecutive source failures, with logs attached; issue auto-closes on recovery.
7. **CI gate:** every push runs pytest + validates every `/data/*.json` against schemas — a bad merge can't take the site down (static hosting can't 500 anyway).

## 11. Self-audit spec (audit.py, runs after each snapshot)

Checks → `data/audit/latest.json` (+ dated copy, keep 90 days), rendered on-site:

1. **Continuity:** no missing dates in any history series; gaps listed.
2. **Cross-source variance:** price/hashrate agreement within thresholds.
3. **Model drift:** power-law `b` moved >0.5% day-over-day, or R² dropped >0.01 → WARN (usually means bad input data, not a broken market).
4. **Staleness:** any metric STALE >24h → WARN, >72h → FAIL.
5. **Sanity replay:** re-validate the last 30 days of history against sanity rules.
6. **Site integrity:** index.html parses; all local asset links resolve; JSON payload total <5MB budget (revised 2026-07-09 from the original 2MB — a project-owner call once real payload size, ~3.5MB+ across 15+ years of daily history for 5 metrics plus fitted models, was actually measured against the arbitrary original number; see IMPROVEMENT_BACKLOG.md's P4 entry).
7. Result: `PASS / WARN / FAIL` + findings array. WARN/FAIL findings are **auto-appended to IMPROVEMENT_BACKLOG.md** — the audit literally feeds the improvement loop.

## 12. Self-improvement loop (honest, $0 design)

Truly autonomous "AI improves the site nightly in CI" requires API credits (claude-code-action bills API usage) — that breaks your $0 rule. The free version is better anyway: **you stay editor-in-chief.**

1. Create `.claude/commands/improve.md` — a custom slash command that instructs Claude Code to: read `data/audit/` (last 7 days) + `data/health.json` + `IMPROVEMENT_BACKLOG.md` → pick the single highest-value item → implement it → run pytest + schema checks → summarize the diff → commit on approval.
2. **Weekly ritual (15 min, on your subscription):** open Claude Code in the repo, run `/improve`, review, ship. That's the "self-improving" engine — audit findings accumulate automatically as fuel all week.
3. Add `/health-report` command too: Claude Code reads audit history and writes you a 5-line status digest.
4. Guardrails in CLAUDE.md: never weaken sanity bounds to make an audit pass; never add a paid dependency; never increase polling frequency; all data-format changes must update schemas + tests in the same commit.

## 13. Build phases (acceptance criteria)

1. **P1 — Skeleton & backfill:** repo, CLAUDE.md, backfill.py fills full daily history (price from 2010, hashrate/difficulty/supply), schemas + pytest pass. ✅ `data/history/` complete & schema-valid.
2. **P2 — Daily pipeline:** fetch_snapshot with failover/sanity/health.json, daily.yml commits successfully 3 days straight (use workflow_dispatch to simulate). ✅ health.json accurate; forced-failure test → STALE carry-forward works.
3. **P3 — Frontend core:** index.html with design tokens, gauges rendering from committed data, live.js WebSocket odometer + polling + client failover. ✅ Site live on GitHub Pages; unplug network → STALE chips appear.
4. **P4 — Models & charts:** fit_models.py + power law/cycle/Mayer charts + deviation dial + projections table. ✅ Fitted `b` within sanity range; charts readable on mobile.
5. **P5 — Audit & health panel:** audit.py, on-site Engine Health + audit panel, auto-issues, backlog auto-append. ✅ Injected bad datum → audit FAIL + issue opened.
6. **P6 — Polish:** attribution footer, README, reduced-motion, Lighthouse ≥90 perf/a11y, `/improve` + `/health-report` commands. ✅ First `/improve` session completed.

## 14. CLAUDE.md — include verbatim

Project purpose (1 para) · architecture summary (Sec. 3 diagram) · source chains table · hard rules: **never scrape bitbo or any dashboard site; only APIs in sources.py; polite-client rules; attribution footer is load-bearing — never remove; sanity bounds are law; $0 constraint; no frameworks/build step without explicit owner approval** · test & commit conventions · pointers to backlog and audit dirs.

## 15. Kickoff prompt for Claude Code

> Read BTC_ENGINE_ROOM_BUILD_SPEC.md in full. We are building Phase 1 only today: initialize the repo per Section 5, write CLAUDE.md per Section 14, implement pipeline/sources.py and backfill.py per Sections 4 & 6, define all JSON schemas, and write pytest coverage for validators and failover logic. Follow the spec exactly; where the spec is silent, choose the simplest option and note it in IMPROVEMENT_BACKLOG.md. Do not start the frontend yet.

---

*Spec prepared 2026-07-08. Sources verified: mempool.space API docs, blockchain.com Charts API, CoinGecko pricing/docs, Coin Metrics Community API v4, alternative.me FNG, GitHub Actions/Pages docs. Model reference: Santostasi power law (publicly published).*

---

# v1.1 ADDENDUM (2026-07-09) — Themes, USP & Monetization, Autonomous Loop

> Sections 16–19 below extend the spec. Section 18 **supersedes** Section 12's conservative take on autonomy.

---

## 16. Theme system — four modes, cookie-persisted

### 16.1 Architecture (build this exactly)

1. **Token-swap design.** Every color/shadow/font decision in the app must reference a CSS custom property (`--bg`, `--panel`, `--ink`, `--accent`, `--ok`, `--warn`, `--fail`, `--font-display`, `--font-data`, `--glow`). Themes are just `[data-theme="X"]` blocks that redefine tokens — zero component changes per theme.
2. **Persistence:** cookie `ber_theme` (Max-Age 1 year, SameSite=Lax, path=/). Selection logic, in a tiny **inline `<head>` script before CSS paint** (prevents flash-of-wrong-theme):
   ```
   theme = readCookie('ber_theme') || 'engine'   // Train Engine is the default
   document.documentElement.dataset.theme = theme
   ```
   No `prefers-color-scheme` override — the brief says engine wins unless the user chose otherwise.
3. **Switcher:** four labeled chips in the sticky header (☀ Light · ● Dark · 🚂 Engine · 🌧 Rain). Writes cookie + swaps `data-theme` instantly. Keyboard accessible; `aria-pressed` states.
4. **Charts re-theme too:** register one ECharts theme object per mode; on switch, dispose + re-init charts with the matching theme (cheap at this chart count). Chart palettes derive from the same tokens.
5. **Per-theme background layers** live in a single `<canvas id="atmosphere">` behind content: no-op (light/dark), subtle animated steam/smoke wisps (engine), digital rain (matrix). One canvas manager, per-theme renderer modules.
6. **Accessibility floor for ALL themes:** body text contrast ≥ 4.5:1, data numerals ≥ 7:1, visible focus rings per-theme, all canvas effects disabled under `prefers-reduced-motion`, canvas paused when `document.hidden`.

### 16.2 The four themes

**A. `light` — "Daylight" (Claude-like, by explicit request)**
Warm paper feel: bg `#F4F1EA`, panels `#FFFFFF` with soft `#E5DFD3` borders, ink `#1F1E1B`, accent terracotta `#C9633E`, ok `#2E7D4F`, warn `#B7791F`. Display face swaps to a high-contrast serif (e.g., Source Serif 4) for the masthead; data stays IBM Plex Mono. Calm, editorial, zero glow. This is the "read it over morning coffee" mode.

**B. `dark` — "Night Watch"**
Neutral pro dark, deliberately distinct from Engine: bg `#0E1116`, panels `#161B22`, ink `#E6EDF3`, accent electric blue `#4C9AFF`, ok `#3FB950`, warn `#D29922`. No amber, no texture, no theatrics — the "trader with 6 tabs open" mode.

**C. `engine` — "Nothing Stops This Train" (DEFAULT)**
The Section 9 engine-room spec, pushed toward a **steam locomotive footplate** (the Lyn Alden fiscal-dominance thesis is the muse): steel black `#0C0F12`, iron panels `#151A20` with faint riveted-corner details (pure CSS, no images), instrument amber `#FFB000`, brass `#B08D57` hairlines, signal green `#2FD97B`, firebox red `#FF4747` for faults. Signature moments: the block-height **odometer** styled as a locomotive drive-wheel counter; difficulty-retarget rendered as a **pressure gauge** dial; a slow steam-wisp drift in the atmosphere canvas; section plates stamped like boiler plates. Tagline under the masthead: *"Nothing stops this train."* — with a credit line "Phrase after Lyn Alden's fiscal thesis" linking to her site. (Short phrases aren't copyrightable, but it's strongly associated with her brand — crediting is both classy and protective. If you'd rather own it fully, variant: "THE TRAIN DOES NOT STOP.")

**D. `matrix` — "Digital Rain"**
Goal: cinematic, not cheesy. bg true black `#000500`, phosphor green ink `#00E676` (bright) / `#0A4F2A` (dim), panels = translucent `rgba(0,20,8,0.72)` with 1px `#00E67633` borders and `backdrop-filter: blur(2px)` so the rain reads *through* the UI. Data font stays mono (already perfect for the look).
**Rain renderer spec (assets/rain.js):**
- Full-viewport canvas, `devicePixelRatio`-aware; column width ≈ 14–18px; per-column independent fall speed and reset offset.
- Glyph set: **half-width katakana** (U+FF66–U+FF9D) + digits 0-9 + a few Latin caps — this specific mix is what makes it read "authentic". Random glyph mutation per cell per frame (~2–5% of cells) so streams "shimmer".
- Head glyph near-white `#CFFFE0`, then trail fading through bright→dim green via per-frame translucent black fill (`rgba(0,5,0,0.08)`) instead of clearing — this produces the iconic phosphor trails cheaply.
- Occasional "bold stream" (2% of columns, brighter + faster) for depth; subtle CRT scanline overlay (repeating-linear-gradient at 3% opacity) on top of everything.
- **Performance budget:** cap at 30fps via timestamp check in rAF; column count scales with viewport (max ~120 desktop / ~48 mobile); pause on `document.hidden`; kill entirely under `prefers-reduced-motion` (static faint green grid instead). Rain sits at z-index 0 with a 35% black dimmer layer between rain and content so numbers stay readable.
- **IP note:** katakana digital-rain is a generic, endlessly-reproduced visual effect and fine to build from scratch. Do NOT use Warner Bros. marks: no film stills, no character names/imagery, no Matrix logo/typeface lockups, and don't market the site with the film's name. UI label "Digital Rain" (safe) — "Matrix mode" as a colloquial label in the switcher tooltip is common practice and low-risk, but Digital Rain is the clean choice.

### 16.3 Comprehensive, user-oriented UX (applies across themes)
1. **Progressive depth:** masthead answers "price/height/halving now" in 3 seconds → gauges answer "network health" in 30 → projections reward 5 minutes. Never make the casual visitor scroll past math to get the price.
2. **Explain-everything tooltips:** every metric label has an ⓘ popover: one-plain-English-sentence definition + "why it matters" + source + last-updated. This is the on-ramp bitbo lacks for newcomers.
3. **Number ergonomics:** compact by default (`825.5 EH/s`, `$1.27T`), exact-on-hover; thin-space thousands separators; tabular numerals everywhere so values don't jiggle.
4. **Chart controls:** timeframe chips (1Y/4Y/ALL), log/linear toggle, band show/hide; every chart has a copy-link anchor (`#power-law`) for sharing.
5. **Status language:** LIVE/DELAYED/STALE chips (Sec. 7) + one global "engine status" lamp in the header that summarizes health.json.
6. **Mobile:** gauges collapse to a 2-col grid, charts get simplified band labels, atmosphere canvases auto-reduce density. Test at 375px.
7. **Later (backlog):** `?theme=` URL param for sharing themed screenshots, keyboard shortcuts (t = cycle theme), zh-HK locale.

---

## 17. USP, competition & monetization (research summary)

### 17.1 The USP, in one line
**"The only Bitcoin dashboard that audits itself in public — real-time for free, with every number traceable to its source."**
Supporting pillars: (1) free real-time via WebSocket where bitbo's free tier refreshes hourly and holds real-time for paid plans; (2) the Engine Health + daily audit panel (nobody does this); (3) honest models with published fits/R²/residuals; (4) the theme experience layer (Engine/Rain modes make it *shareable* — screenshots are the growth loop); (5) open pipeline = verifiable trust.

### 17.2 Competition (yes, it's crowded — that's fine)
bitbo.io (market leader; claims 1M+ users/mo; monetizes via Pro subscriptions with real-time refresh + 60+ private charts, a paid API with plans up to 1M calls/mo, TradingView indicator packs, alerts, monthly reports — plus "best sites to buy BTC" affiliate placements on the free dashboard), LookIntoBitcoin, Checkonchain, TimeChainStats, NewHedge, Bitcoin Magazine Pro, blockchain.com charts, mempool.space.
**How to win as a solo, $0-budget entrant:** don't out-feature them — out-*trust* and out-*charm* them. Radical transparency (their paywalled real-time is your free tier), one signature aesthetic they can't copy without looking derivative, unique free tools that earn links/SEO (power-law calculator with shareable permalinks, retarget countdown, "engine status" API-as-a-badge), and distribution through open source (Hacker News, r/Bitcoin, X — "I built a self-auditing Bitcoin dashboard" is a strong Show HN).

### 17.3 Revenue options, ranked for YOUR situation
> ⚠️ **Career gate first:** you're stepping into a Director CFCR Digital Assets seat at a global bank. Virtually any monetized crypto site — *especially* one carrying exchange affiliate links or crypto sponsor ads — will need **outside business interest (OBI) disclosure and pre-approval** under SCB's compliance policies, and exchange-referral income is an obvious conflict for someone regulating digital-asset risk. Rule: **ship it free and unmonetized now; seek OBI clearance before switching anything on.** A free educational tool is an easy disclosure; an affiliate revenue stream is not.

1. **Reputation dividend (best ROI, zero conflict):** the site as the public proof-of-craft for your compliance-advisory second income — "the AML director who builds self-auditing data infrastructure" is a killer differentiator in your network. Realistically worth more than years of ad pennies.
2. **Donations:** Lightning tips + GitHub Sponsors. Trivial income, zero conflict, explicitly fine on GitHub Pages ToS (donation buttons are named as permitted).
3. **Display ads — the Google Ads answer:** two different Google regimes people conflate. *Google Ads advertiser certification* (for crypto exchanges buying ads) is NOT your problem. *AdSense publisher policy* is — and crypto market-data/educational sites are not auto-banned; approval hinges on site quality, original content, transparent ownership (finance = YMYL scrutiny). So AdSense is *possible*, but poor fit: crypto audiences are heavy ad-block users, dashboard RPMs are modest, and it uglifies the product. Better fits at scale: **EthicalAds/Carbon-style single tasteful placement** or **direct sponsorship** ("This engine is fueled by ___") at $X/month flat. All of this = OBI disclosure first.
4. **Pro tier later (the bitbo model):** alerts, API, custom layouts. Requires accounts + backend + payments → no longer $0, and the strongest conflict profile. Park it.
5. **Exchange/hardware-wallet affiliates:** how bitbo monetizes its free tier. For you, the highest-conflict option. Avoid while employed in bank compliance.

### 17.4 Hosting ToS & the open-source question ("how to stack this")
- **GitHub Pages ToS:** not allowed to run a site "primarily directed at facilitating commercial transactions or providing SaaS"; donation buttons OK; a modestly-monetized legitimate project site is a gray-but-generally-tolerated zone. Also: free-plan Pages requires a **public repo**.
- **Decision matrix:**
  - *Stay open (recommended):* transparency IS the moat — closing the source deletes your differentiator. Your real moats are the domain/brand, accumulated history data continuity, SEO age, and shipping velocity — none of which a repo-cloner gets. bitbo thrives with zero source secrecy on its free data. License: **MIT** if relaxed, or **PolyForm Noncommercial** (source-visible, commercial reuse prohibited) if clone-anxiety is real — you keep trust while blocking commercial copycats.
  - *If/when monetizing:* keep GitHub for repo + Actions, **deploy to Cloudflare Pages** (free tier, commercial use permitted, private repos supported, generous bandwidth) with a ~US$10/yr domain. This cleanly exits the GitHub Pages commercial gray zone. Migration is a one-hour job — the site is static files; nothing else changes.
  - *Fully closed + free hosting:* private repo + Cloudflare Pages works today at $0 — but you lose the open-source distribution channel, which is your cheapest marketing. Not recommended for launch.
- **Stack recommendation:** Phase A (now): public repo, GitHub Pages, MIT, no monetization. Phase B (traction + OBI clearance): custom domain on Cloudflare Pages, donations + one sponsor slot. Phase C (only if it's a real business, likely post-banking-career): pro tier, and revisit licensing.

---

## 18. Autonomous build + self-improving loop — how it really works (supersedes Sec. 12)

**Verdict: yes, genuinely possible in 2026 — including on your subscription — with guardrails.** Two corrections to Section 12: (a) Anthropic's official claude-code-action supports `CLAUDE_CODE_OAUTH_TOKEN` (generate once via `claude setup-token`, store as a repo secret) so scheduled CI runs draw from your Pro/Max subscription instead of a per-token API bill; (b) Anthropic announced a June 15, 2026 change moving programmatic usage (Agent SDK, `claude -p`, GitHub Actions) to a separate API-rate credit pool — then **paused it on June 16**, so subscription coverage currently stands. Treat this as **volatile policy**: design the loop to be quota-light, and re-check Anthropic's terms before scaling it up. Also note the Feb 2026 ToS clarification: OAuth tokens are for *official* tools only — the official action qualifies; third-party harnesses do not.

### 18.1 How Anthropic's own engineers do it (published patterns)
1. **Autonomous inner loop:** auto-accept mode + "write code → run tests → iterate" loops on abstract problems, starting from a clean git state, with the human reviewing the ~80%-complete result.
2. **Memory across sessions:** an initializer agent sets up the environment plus a progress file (e.g., `claude-progress.txt`); every later session makes *incremental* progress and leaves structured notes — git history + progress file are the agent's memory between context windows.
3. **Writer/verifier separation:** a model grading its own work is too generous, so a second agent (or the `/goal` command's independent grader) checks completion; subagent reviewers catch what the implementer rationalized.
4. **Loop primitives (2026):** the Ralph-loop pattern/plugin and native `/loop` keep kicking the agent back until a stated success criterion holds (`--max-iterations` capped); **routines** (launched April 2026) schedule recurring agent work; **Dynamic Workflows** (research preview, May 2026) move orchestration into a script outside the context window. The Claude Code lead has described his job now as "writing loops" rather than prompts.
5. **Universal guardrails:** verifiable success conditions ("pytest green, no files outside /pipeline"), turn/iteration budgets, and git-visible progress.

### 18.2 Your build loop (one-time, local, subscription)
Per phase in Section 13: open Claude Code → plan mode → approve plan → auto-accept loop with the phase's acceptance criteria as the **test oracle** ("loop until `pytest` passes and criteria X/Y/Z verified; commit after each meaningful unit; update PROGRESS.md"). Add `PROGRESS.md` to the repo root and reference it in CLAUDE.md so any fresh session self-orients in seconds. Optionally wrap phases in the Ralph loop with `--max-iterations 15`. You review each phase like an engineering manager — this is exactly the Anthropic pattern, and it will build P1–P6 in a handful of evenings.

### 18.3 Your ongoing self-improvement loop (scheduled, CI, subscription)
```
[daily.yml] snapshot → audit → findings auto-append to IMPROVEMENT_BACKLOG.md
        │
[improve.yml — cron, Tue & Fri 22:00 HKT]
  1. anthropics/claude-code-action@v1 with claude_code_oauth_token secret
  2. Prompt: read backlog + last 7 audits + health.json → pick ONE item
     → implement → run pytest + schema checks → update PROGRESS.md
  3. claude_args: --max-turns 25, Sonnet-class model (quota-light)
  4. Output: a branch + PR (the action commits to a branch and links a PR
     for you — it deliberately does not merge into main itself)
        │
[ci.yml on the PR] pytest + schema validation + JSON-size budget = the VERIFIER
        │
[You, on your phone, 2 min] read Claude's PR summary → merge or comment
  "@claude the odometer animation stutters on mobile, fix that instead" → it iterates
```
**Guardrails (non-negotiable, encode in CLAUDE.md + branch protection):** main is protected — the loop can never push to it directly; CI must be green to merge; one improvement per run; hard `--max-turns`; the loop may never edit sanity rules, workflow files, or the attribution footer; and if the backlog is empty it exits without inventing work. **Cost reality:** each run burns your shared subscription quota (same pool as your interactive Claude use) — twice-weekly with capped turns is sustainable; nightly Opus-class runs are how people blow through Max limits. If Anthropic un-pauses the billing change, this workflow moves to metered credits — the design survives, only the meter changes; at ~8 capped Sonnet runs/month the API-rate fallback would be dollars, not hundreds.

**So: "autonomously built and self-looping" = 90% true today.** The honest 10%: a human merge click stays in the loop — by the action's design and by good sense (an unattended loop without a verifier "ships bugs with high confidence"). Your role shrinks to reviewing PRs the way you'd review an analyst's work.

---

## 19. Updated kickoff prompt for Claude Code (replaces Sec. 15)

> Read BTC_ENGINE_ROOM_BUILD_SPEC.md in full, including the v1.1 addendum (Sections 16–19). Create PROGRESS.md and CLAUDE.md first (Sections 14, 18.2). Then build Phase 1 per Section 13 in an autonomous loop: repo init, pipeline/sources.py, backfill.py, all JSON schemas, pytest coverage — loop until pytest is fully green and Phase 1 acceptance criteria verified, committing after each meaningful unit and updating PROGRESS.md. The theme system (Section 16) lands in Phase 3; implement tokens-first from day one so themes are a token swap, with `engine` as the cookie-default. Where the spec is silent, choose the simplest option and log it in IMPROVEMENT_BACKLOG.md.

*Addendum research verified 2026-07-09: bitbo free/pro tiers & monetization mix, GitHub Pages commercial-use terms, AdSense publisher vs Google Ads advertiser crypto policies, claude-code-action OAuth/subscription auth & June 2026 billing pause, Anthropic long-running-agent harness posts, Ralph loop / routines / Dynamic Workflows.*
