# CLAUDE.md — BTC Engine Room

## 1. Project purpose & status

BTC Engine Room is a free, public Bitcoin fundamentals + price-model dashboard: block height, hash rate, difficulty, mempool, fees, supply, plus long-horizon price models (power law corridor, 4-year halving cycle overlay, Mayer Multiple, 200WMA). The differentiator is radical transparency — every gauge shows its data source, freshness, validation status, and failover state, and the site publishes its own daily audit report. Total running cost is $0 beyond an existing Claude subscription.

**Current phase:** P6 (Polish) complete — all six build phases (P1–P6) are done. Check `PROGRESS.md`'s phase checklist and log for live status before starting new work. Source of truth for everything below: `docs/BTC_ENGINE_ROOM_BUILD_SPEC.md` (the full spec) and `docs/PHASE1_DIRECTOR_CORRECTIONS.md` (the corrections layered on top of it — read both, the corrections supersede the spec where they conflict). `IMPROVEMENT_BACKLOG.md` records every subsequent real-world correction found while building — check it too before trusting any endpoint, message-shape, or chart-axis detail below at face value. Known open items (all honestly surfaced by `audit.py`, not hidden): committed JSON exceeds spec Section 11.6's <2MB payload budget, hashrate has no cross-source variance check (only price does), and CSS/JS are intentionally unminified (CLAUDE.md's own hard rule forbids a build step without explicit owner approval) — see `IMPROVEMENT_BACKLOG.md`'s P4/P5/P6 entries. Ongoing work from here is the weekly `/improve` ritual against the backlog, not a new phase.

## 2. Architecture summary

"Static core, live skin," two layers:

- **Live layer** (browser, seconds-to-minutes): WebSocket to mempool.space for blocks/mempool stats, REST polling (≥60s) for price/fees/difficulty, cross-checked against CoinGecko/Coinbase. On total failure, render the last committed snapshot with a STALE badge — never a blank gauge.
- **Historical layer** (GitHub Actions, daily): fetch → validate (schema + sanity bounds + cross-source check) → append to `data/history/*.json` → refit models → audit → commit → GitHub Pages redeploys automatically. On repeated failure, auto-open a GitHub issue.

Stack: GitHub Pages (hosting) + GitHub Actions (automation) + Python 3.12 (`requests`, `numpy`, `jsonschema` — no pandas) for the pipeline + vanilla HTML/CSS/JS + Apache ECharts (CDN) for the frontend, no framework, no build step.

## 3. Data source chains (corrected, then live-verified)

| Metric | Primary (as designed) | Fallback | Notes |
|---|---|---|---|
| Price backfill | Coin Metrics Community (`PriceUSD`) | blockchain.com Charts `market-price` | **Coin Metrics is currently returning 401 Unauthorized even without a key** (live-verified 2026-07-09, contradicts the spec's assumption) — `backfill.py` catches this and falls through entirely to blockchain.info. Current committed `price_daily.json` is 100% `blockchain_info`-sourced. See `IMPROVEMENT_BACKLOG.md`. Positive-value rows only (pre-2010-08 placeholder zeros filtered). |
| Hash rate backfill | Coin Metrics Community (`HashRate`) | blockchain.com Charts `hash-rate` | Same Coin Metrics 401 as above — currently 100% blockchain.info. Canonical unit EH/s; blockchain.info's native unit is **TH/s** (verified against its own `unit` field and a real-magnitude check), not GH/s — divide by 1e6, not 1e9. |
| Supply backfill | Coin Metrics Community (`SplyCur`) | blockchain.com Charts `total-bitcoins` | Same Coin Metrics 401 — currently 100% blockchain.info. Chart name is `total-bitcoins`, not `total-bc` (404s). Native granularity is per-block, not per-day, even with `sampled=false` — collapsed to one row per date (last value of the day). Monotonic, ≤ 21,000,000. |
| Difficulty backfill | blockchain.com Charts `difficulty` | — (single source) | No Coin Metrics community equivalent; sparse step-function series, not daily. Positive-value rows only. Backfill sanity check does not enforce a max-swing threshold — 2009-2013 retargets legitimately moved 30-300%+. |
| Fear & Greed backfill | alternative.me `?limit=0` | — | Data starts ~2018-02-01, not genesis |
| Price (live, daily snapshot) | mempool.space `/api/v1/prices` | CoinGecko → Coinbase | Cross-checked against CoinGecko regardless of which wins (`cross_source_variance_warn` in health.json) |
| Hash rate (live, daily snapshot) | mempool.space `/api/v1/mining/hashrate/3d` (`currentHashrate`, H/s → EH/s ÷1e18) | blockchain.info Charts `hash-rate` (latest point) | |
| Difficulty (live, daily snapshot) | mempool.space `/api/v1/mining/hashrate/3d` (`currentDifficulty`) | blockchain.info `/q/getdifficulty` (plain-text scientific notation) | **Not** `/v1/difficulty-adjustment` — that endpoint returns retarget progress/ETA fields only, no raw difficulty value, despite its name suggesting otherwise (live-verified 2026-07-09) |
| Supply (live, daily snapshot) | blockchain.info `/q/totalbc` (satoshis → BTC ÷1e8) | computed from tip height via `pipeline/subsidy.py`'s closed-form subsidy schedule | Coin Metrics dropped from this chain (401, see above); subsidy-schedule fallback matched a real observed value within 0.00006% |
| Fear & Greed (live, daily snapshot) | alternative.me `?limit=1` | — (sole source) | |
| Block height | mempool.space WS `blocks` (browser, P3) / REST `/blocks/tip/height` (pipeline, used only as a subsidy-schedule input) | blockchain.info `/q/getblockcount` | Not stored as its own history file — spec Section 5 doesn't list one; it's live-only / a computation input |
| Fees (live, browser only) | mempool.space `/api/v1/fees/recommended` | — (mark stale) | No fallback per spec; browser-only (P3), not part of the daily pipeline — no history file |

**Follow-up (logged in `IMPROVEMENT_BACKLOG.md`):** either obtain a free Coin Metrics API key and wire it back into `CoinMetricsClient` for backfill AND daily-snapshot chains, or formally demote Coin Metrics to documented-fallback status throughout.

`pipeline/fetch_snapshot.py` (P2) implements the live-snapshot chains above: schema + `sanity_rules.json`'s `live_snapshot` profile gate every candidate value; a source that fails validation is treated as a chain failure and the next source is tried (spec Section 6). Total chain failure carries the last known value forward under today's date and marks the metric `STALE` in `data/health.json`, incrementing `consecutive_failures`; 3+ consecutive failures on any metric opens/updates a GitHub issue (`pipeline/gh_issues.py`, labels `auto`+`data-outage`), auto-closed on recovery. Requires `GITHUB_TOKEN` in the environment (present automatically in `daily.yml`'s Actions run via `secrets.GITHUB_TOKEN`); issue automation is skipped silently, not fatal, when absent or non-functional (e.g. local runs).

## 4. Hard rules (non-negotiable)

- Never scrape bitbo or any other dashboard site. Only pull from the APIs defined in `pipeline/sources.py`.
- Polite-client discipline on every fetcher: descriptive User-Agent (`btc-engine-room/1.0 (+https://github.com/0xfanbase/Bitcoin-Engine-Room)`), timeout=15s, max 3 retries with exponential backoff + jitter, honor `429`/`Retry-After`.
- The attribution footer (CoinGecko, alternative.me, Coin Metrics credits) is load-bearing — it lands in P3 (moved up from the spec's original P6) and must never be removed once built.
- Sanity bounds (`pipeline/sanity_rules.json`) are law — never weaken them to make a check or an audit pass.
- $0 constraint: no paid services, keys, or domains, ever, without explicit owner approval.
- No frameworks or build step without explicit owner approval.
- License stays MIT (already shipped) — do not switch to PolyForm Noncommercial or anything else.
- Repo slug is `0xfanbase/Bitcoin-Engine-Room`, Pages URL `https://0xfanbase.github.io/Bitcoin-Engine-Room/` — already resolved, never propose a rename. (Corrected 2026-07-09: the repo moved out from under an earlier `fandamentals/...` slug partway through Phase 1; GitHub's own move notice on `git push` was the signal. Every reference — schema `$id`s, the User-Agent string, `gh_issues.py`'s fallback, `index.html`'s footer link — was swept and fixed in one pass. If this ever moves again, grep for the old slug across the whole repo, not just the obvious spots.)

## 5. Repository layout & phase map

```
bitcoin-engine-room/
├── CLAUDE.md, PROGRESS.md, IMPROVEMENT_BACKLOG.md, README.md, LICENSE   # P1
├── docs/                          # P1 — spec + director corrections
├── index.html, assets/            # P3 (style.css, app.js, live.js, health.js) + P4 (charts.js) + post-v1 (rain.js, 2026-07-09; rebuilt as the site's sole identity, 2026-07-09)
├── data/history/*.json            # P1 backfilled, P2 appends one row/day live
├── data/models.json               # P4
├── data/health.json               # P2
├── data/audit/                    # P5 — latest.json + one dated copy per day, 90-day retention
├── pipeline/
│   ├── sources.py                 # P1 backfill fetchers + P2 live/failover clients
│   ├── validation.py              # P1 backfill checks + P2 live-snapshot checks, reused by P5
│   ├── subsidy.py                 # P2 — block-subsidy math (supply fallback + halving countdown)
│   ├── gh_issues.py                # P2 (data-outage) + P5 (audit-fail) issue automation, shared mechanics
│   ├── backfill.py                # P1
│   ├── fetch_snapshot.py          # P2
│   ├── fit_models.py              # P4 — power law/cycle/Mayer/200WMA (deviation dial removed 2026-07-09, see IMPROVEMENT_BACKLOG.md)
│   ├── audit.py                   # P5 — continuity/variance/drift/staleness/sanity-replay/site-integrity
│   ├── sanity_rules.json          # P1 (live_snapshot) + P2 (consumed by fetch_snapshot.py)
│   ├── model_constants.json, MODEL_METHODOLOGY.md   # P1, consumed by P4's fit_models.py
│   ├── known_gaps.json            # /improve (2026-07-09) -- verified, cited continuity-gap allowlist, consumed by audit.py
│   ├── schemas/                   # P1 + P2 (health.schema.json) + P4 (models.schema.json) + P5 (audit.schema.json) + /improve (known_gaps.schema.json)
│   └── tests/                     # P1 + P2 + P4 + P5
├── .claude/commands/               # P6 — improve.md + health-report.md
└── .github/workflows/
    ├── ci.yml                     # P1
    └── daily.yml                  # P2, extended P5 to run fetch_snapshot -> fit_models -> audit
```

All six phases (P1–P6) are complete as of this writing. There is no P7 in the spec — from here, work is either the weekly `/improve` ritual against `IMPROVEMENT_BACKLOG.md`, or a deliberate new feature the project owner asks for (e.g. resolving the Coin Metrics 401 or the JSON payload budget). The Matrix/Digital-Rain theme, originally deferred post-v1, shipped 2026-07-09 by owner request — see Section 6 rule 9.

Do not create stub files for anything marked as a later phase — an absent file is the clearest signal of what's in scope right now.

## 6. Creative direction (Engine Room Design Authority)

Produced by an independent director-level review before Phase 1 began (full writeup and rationale in `docs/PHASE1_DIRECTOR_CORRECTIONS.md`). These are standing calls for anyone doing visual design, theme, or copy work on this project — follow them without needing to re-litigate:

1. **Single-instrument identity (rewritten 2026-07-09):** BTC Engine Room is one instrument, not a theme picker — "a green-phosphor terminal watching the Bitcoin network, standing in a room full of falling code. Cinema behind the glass, instrumentation on the glass." Every pixel that encodes data (numerals, badges, sources, disclaimers) must read as if the rain weren't there; every pixel that encodes nothing may be as cinematic as the frame budget allows. The `engine` theme is cut outright, not deprioritized — see the dated note below.
2. Block arrival is the page's one theatrical *event*, expressed jointly by three things firing together on genuine new-block arrival and silent between events: the odometer roll, the Block Rail sweep (`.block-rail`, driven from `live.js` — its position between blocks is elapsed time since the last block, data, not decoration, crawling at ~0.5px/s on its 320px-capped width), and `rain.js`'s block-arrival density/burst surge (~2s ramp, ~2s decay, coupled via a `ber:block` `CustomEvent` `live.js` dispatches on the same real height-change). The screensaver test now governs the page's *chrome* specifically (cards, headings, controls): if you still notice a chrome element after staring at it for 10 seconds, cut it. The rain itself is the documented, opt-in exception to that test — see rule 9.
3. Cut the "Nothing stops this train" masthead tagline entirely — it turns instrumentation into advocacy. At most, it can survive as an easter egg (tooltip/console-log) with the Lyn Alden credit intact — never in the masthead, never as a caption on the Block Rail, and never as a rain-decode target (rule 9's IP-safety list forbids film quotes anywhere, which this would risk brushing up against).
4. The single accent color (phosphor green `#33FF66`) is a *data* color — numbers, needles, chart bands, chart reference-zone borders — never a surface color; panels/borders stay in dim/border tones. Firebox red (`--fail`) is for genuine faults only, never decorative.
5. **Typography (amended 2026-07-09):** every numeral in the DOM is IBM Plex Mono with tabular figures, no exceptions — unchanged. Archivo Black is retired site-wide (dropped from the Google Fonts URL entirely). The masthead H1 and section-plate (`.plate`) headings now render in IBM Plex Mono 600, uppercase, letterspaced ~0.08–0.12em — "the site's own name becomes terminal output." IBM Plex Sans stays for explanatory prose (info panels, disclaimers, body copy) — mono paragraphs are objectively harder to read, and legibility is a direct, standing owner instruction, not just a preference. One-line story: mono is the machine (data, headings, labels), sans is the human explaining it. (The rain canvas's own glyphs are scenery, not DOM text, and use a system CJK-capable font stack — see rule 9; this doesn't touch the numeral rule.)
6. **Chrome (amended 2026-07-09):** riveted corners are retired — they were `engine`-brand whisper-volume skeuomorphism specifically, and the new chrome is terminal chrome: 1px hairlines, near-sharp corners (`border-radius` reduced from the old 6–8px card radius to 2–3px project-wide) on `--panel` surfaces, with the rain itself supplying the texture. No images/noise overlays, unchanged. Glow budget stays status lamps (radius ≤ 6px) plus the rain canvas's near-white/hot-white glyph heads (color contrast alone reads as glow) — no `text-shadow`/`shadowBlur` anywhere, ever, on DOM text or canvas, unchanged and non-negotiable.
7. The power-law chart is the hero: full-width, log-log by default, translucent accent-color bands, labels "Redline / Cruise / Idle" (never the rainbow chart's labels), fit stats (b, R², σ, last refit) printed on the chart face like a calibration plate.
8. ~~The light theme is editorial (warm paper, serif masthead, zero glow/rivets)~~ — removed 2026-07-09. ~~The `engine` theme is the brand, ~70% of design effort~~ — also removed 2026-07-09, see the dated note below. No theme picker of any kind currently ships; the site has exactly one visual identity.
9. **Digital Rain — now the whole site, not an alternate (rewritten 2026-07-09).** Canvas: `#digital-rain-canvas` (renamed from `#matrix-rain-canvas` in the same sweep that renamed the id — element ids are visible in page source), `position: fixed; inset: 0; z-index: -1`, implemented in `assets/rain.js`. Per frame: (1) fill the whole canvas with the translucent near-black `--rain-fade` token (`rgba(2,8,4,0.055)`) — passive trail-fade, no per-glyph alpha bookkeeping; (2) each active column steps by exactly one row at a time on its own per-column tick interval (speed tiers 1/2/3 ticks, weighted ~50/35/15 — stepped motion, never a smooth continuous fall, is what reads as the film's rain); (3) a 3-step hot-head gradient trails each step (new glyph → `--rain-head2` → `--rain-trail`); (4) ~1.5%/frame of older, already-settled trail glyphs flicker to a different character in place (shimmering trails, not just falling ones); (5) ~1/40 respawning columns run "hot" for their first 3 glyphs (one brightness step up). Density `ACTIVE_FRACTION` = 0.9 (was 0.6 under the old alternate-theme treatment). **Capped at 20fps, unchanged and deliberate** — raising it would smooth away the film's stepped look and cost more battery for a worse effect; the choppiness is the aesthetic, not a compromise. Glyph set: half-width katakana U+FF66–U+FF9D plus digits 0–9, drawn with a system CJK-capable font stack (`"MS Gothic", "Osaka-Mono", "Noto Sans Mono CJK JP", monospace`) — never IBM Plex Mono (no katakana coverage) and never a "Matrix code font" download (see IP safety below). Hard-paused on `document.hidden`. Fully disabled under `prefers-reduced-motion: reduce` (flat `--bg` fill, no static glyph scatter) **and** user-togglable via the Rain ON/OFF rocker in the header (`localStorage` key `ber_rain`, default on, no cookie) — committing the entire visual identity to an animated background is only responsible if a visitor can stop it without losing the site; this is the pressure valve that makes rule 2's screensaver-test exemption defensible. No `shadowBlur`/`backdrop-filter` anywhere, ever.
   **IP safety (explicit director ruling, do not re-litigate without genuinely new ground):** the falling-glyph-on-black trope is generic and unprotectable — reimplemented thousands of times since 1999 without incident — but the film's specific glyph *typeface* is a real derivative-work risk, and the film's *title* is a trademark risk. **Fine, do freely:** falling rain at any density, phosphor green on near-black, katakana+digits from ordinary system fonts, mirrored glyphs, variable speeds, bright heads, burst columns, trail mutation, density surges, decode/scramble effects that resolve into *our own copy* (the site's own name, our own labels). **Veto, never ship:** the film's title anywhere user-facing (UI copy, `<title>`, meta description, README marketing prose, screenshot captions, social posts) — the public name is **"Digital Rain," only and always**; film quotes or near-paraphrases anywhere, including console-log easter eggs and code comments that ship to the client (a console easter egg is still publication); any font traced from the film's proprietary glyph typeface — system fonts only, no exceptions; film stills/frames anywhere in the repo, docs, or marketing; character names, iconography (sunglasses/trenchcoat silhouettes, pills), or "enter the ___"-style button copy.
10. **Readability law (new, 2026-07-09):** no DOM text ever sits directly over live, unbacked rain. Every text-bearing element sits on a surface of ≥0.92 bg-family opacity; primary data numerals (the masthead) get ≥0.96. Concretely: the masthead is a full-bleed (100vw) scrim band, not a card — `rgba(2,8,4,0.96)` flat for its *entire* height (every real data element inside it, odometer through the halving stats, sits on uniform solid backing; the feathered fade into the rain lives in a separate strip *below* the masthead's own box, in the page gutter, where there's no text to protect) — a floating card around the odometer would read as just another gauge, while a full-width band reads as the terminal's fixed header glass. Section-plate headings (`.plate`) are panel-backed (`var(--panel)`) with `--ink` (not the dimmer `--ink-dim`) text. The footer and any standalone `.info-disclosure` outside a gauge/chart card are panel-backed for the same reason. This law was derived from real contrast math (a fresh rain head glyph directly behind text computes to as low as ~1.4:1 against bare `--bg` — a total failure) — don't relax it without redoing that math.

**Dated note (2026-07-09, second entry — the first documents the light/dark cut earlier the same day):** the project owner explicitly requested, and Fable (director review) signed off on, killing the `engine` theme entirely and committing the site to Digital Rain as its sole, considerably more cinematic identity, plus removing the "Deviation Dial" projection (a toy composite explicitly self-labeled as non-rigorous, with one input crude-by-design from only 3 historical data points) in favor of a "Market Sentiment" chart built on the site's own real, already-collected Fear & Greed history. `engine` is not preserved in code — recoverable from git history, same as the earlier `light` cut (logged in `IMPROVEMENT_BACKLOG.md`). This is settled the same way every prior call in this section was: don't re-litigate without genuinely new ground.

**Process:** for any *major* new creative/design/naming decision beyond what's captured above, spawn an `Agent` (Agent tool, `model: "fable"`) to get an independent director-level opinion before implementing — the same process used to produce this section and both rounds of 2026-07-09 amendments. Treat the bullets above as settled; only re-consult for genuinely new ground.

## 7. Testing & commit conventions

- `pytest pipeline/tests -v` must be fully green before any commit.
- One logical change per commit.
- Commit message style: `feat(pipeline): ...`, `fix(...): ...`, `docs(...): ...`; P2's daily bot commits will use `chore(data): snapshot YYYY-MM-DD [skip ci]`.
- Update `PROGRESS.md` in the same commit as each meaningful unit of work.
- Never commit secrets. None of the Phase 1/2 data sources require API keys.

## 8. Project memory pointers

- `PROGRESS.md` — session-to-session log and phase checklist; read it first.
- `IMPROVEMENT_BACKLOG.md` — audit-fed + manually logged ideas and judgment calls.
- `data/audit/` — daily audit reports (`latest.json` + one dated copy per day, 90-day retention).
- `docs/BTC_ENGINE_ROOM_BUILD_SPEC.md` + `docs/PHASE1_DIRECTOR_CORRECTIONS.md` — full spec and the corrections layered on it.
- `.claude/commands/improve.md` — the weekly self-improvement ritual (spec Section 12): reads the backlog + audit history + health.json, picks one item, implements, verifies, commits on approval. Guardrails (never weaken sanity bounds, never add a paid dependency, never increase polling frequency, never touch workflow files/attribution footer as a side effect) are non-negotiable and spelled out there.
- `.claude/commands/health-report.md` — read-only 5-line status digest from audit history.

## 9. Environment notes

Sandboxed dev environments may sit behind a TLS-intercepting proxy. If API calls fail certificate verification, set `REQUESTS_CA_BUNDLE` to that environment's CA bundle path — never disable TLS verification to work around it.
