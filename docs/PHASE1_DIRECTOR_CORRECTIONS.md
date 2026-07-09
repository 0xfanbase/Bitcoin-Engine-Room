# Phase 1 Director Corrections

This document records the director-level review of `BTC_ENGINE_ROOM_BUILD_SPEC.md` that was conducted before Phase 1 implementation began, and the specific corrections it produced. It exists for provenance: CLAUDE.md and the Phase 1 code reference these decisions by name, and this file is where the reasoning behind them lives.

## Review process

The spec was reviewed end-to-end by a separate model instance acting in a "creative/technical director" capacity, prompted to critique — not rubber-stamp — the spec. Its review covered feasibility, credibility of technical claims, scope, and creative direction. The corrections below were accepted and are binding for Phase 1 and beyond unless explicitly revisited.

**Standing process:** for future major creative/design/naming decisions on this project, spawn an `Agent` (Agent tool, `model: "fable"`) to get an independent director-level opinion before implementing, the same way this review was produced. Treat the creative-direction bullets in CLAUDE.md as settled; only re-consult for genuinely new decisions.

## Review verdict (summary)

The spec is well-formed and buildable — the architecture (Section 3's "static core, live skin" split), the "encode sanity bounds as data, not code" principle (Section 6), and publishing model residuals/R²/drift instead of just a pretty chart (Section 8) are all sound and worth keeping exactly as specified.

Weaker points found and corrected:

- **Sections 9 and 16 contradict each other.** Section 9's restraint clause ("the odometer is the one theatrical element ... everything else is quiet") is undercut by Section 16's steam-wisp and digital-rain atmosphere layers. **Section 9 wins:** no atmosphere canvases in v1.
- **A couple of Section 4's "verified" facts were off:** CoinGecko's Demo tier is 30 calls/min, not 100/min (irrelevant to the daily pipeline, but worth knowing); blockchain.com Charts API is a weaker backfill primary than Coin Metrics Community API (undocumented-ish, historically flakier). Swapped below.
- **The four-theme system (Section 16) is over-scoped for a $0 solo v1.** The token-swap architecture itself is cheap; the two bespoke atmosphere canvases (steam wisps, digital rain) are not, and a canvas that looks bad reads as a screensaver. Ship three token-only themes (engine/dark/light) at P3; defer Matrix/Digital-Rain and all atmosphere canvases to a named post-v1 release.
- **The "autonomous self-improving loop" (Section 18) is real but oversold as written.** It needs backlog dedupe and an "open-PR guard" to avoid burning quota on duplicate work, and the frontend-blind verifier (pytest + schema checks only) means it should stay scoped to pipeline paths, and stay manual (Section 12's `/improve` ritual) until a month of stable dailies plus a promoted Playwright smoke test.
- **Monetization stays off** per spec Section 17.3 — confirmed out of scope for now, nothing to build or track here.

## Corrections locked in for Phase 1

1. **Backfill source swap.** Coin Metrics Community API (`api.coinmetrics.io/v4`, no key, paginated via `next_page_url`) becomes **primary** for price (`PriceUSD`), hash rate (`HashRate`), and supply (`SplyCur`) backfill. blockchain.com Charts API (`api.blockchain.info/charts/...`) becomes the fallback for those three. Difficulty has no direct Coin Metrics community equivalent, so blockchain.info Charts stays **primary** (single-source) for difficulty. alternative.me's Fear & Greed API supports `?limit=0` for full history (not mentioned in the original spec — added here); note its data only starts ~2018-02-01, it does not go back to Bitcoin's genesis.
2. **Every history row carries a `source` field.** Object form `{"date", "value", "source"}` (or, for FNG, `{"date", "value", "classification", "source"}`) — never a bare `[date, value]` tuple. Splicing different providers' estimators into one series without recording which source produced which row is the single most expensive thing to retrofit once months of history have accumulated.
3. **`pipeline/sanity_rules.json` and pinned model-fit-methodology constants are written in Phase 1**, even though `fetch_snapshot.py` (P2), `fit_models.py` (P4), and `audit.py` (P5) consume them later. Bounds-as-data and fit constants need to exist before history accumulates, or later drift-audits have nothing stable to compare against.
4. **Repo naming is already resolved.** GitHub slug is `fandamentals/bitcoin-engine-room` (Pages URL `https://fandamentals.github.io/bitcoin-engine-room/`). No rename; use this slug consistently wherever the spec says `btc-engine-room`.
5. **Attribution footer moves up to P3** (from P6 in the original phase plan). Section 1 calls it load-bearing — CoinGecko's Demo/free tier *requires* attribution — so it shouldn't wait for "polish."
6. **Stay MIT.** The repo already ships MIT; do not switch to PolyForm Noncommercial as Section 17.4 floats as an option. PolyForm is source-available, not open source, and would contradict the "fully open pipeline" positioning that is the project's actual moat (Section 2, point 4).

## Creative direction (Engine Room Design Authority)

These are the director's standing calls for future sessions working on visual design, themes, or copy. They are captured in full in `CLAUDE.md`'s Creative Direction section — this is the provenance note for where they came from and why.

1. Engine theme is the brand (spend ~70% of design effort there); light and dark are conveniences (~15% each). Every marketing screenshot uses the engine theme.
2. Section 9's restraint clause outranks Section 16's atmosphere ideas — the block-height odometer is the *only* theatrical element on the page. v1 engine theme ships with no atmosphere canvas. "Screensaver test": if an effect is still noticeable after staring at it for 10 seconds, cut it.
3. Cut the "Nothing stops this train" masthead tagline entirely. A bullish slogan under the masthead undercuts "instruments, not horoscopes." At most, it survives as an easter egg (a tooltip or console-log message) with the Lyn Alden credit intact — never in the masthead itself.
4. Amber (`#FFB000`) is a data color — numbers, needles, chart bands — never a surface color; panels and borders stay steel and brass. Firebox red appears only for genuine faults, never decoratively.
5. Every numeral on the page is IBM Plex Mono with tabular figures, no exceptions. Archivo Black appears only in the masthead and section plates, never in body copy.
6. Skeuomorphism at whisper volume: riveted corners are pure CSS, only noticeable on close inspection; no images, textures, or noise overlays. Status lamps are the only glow on the page, radius ≤ 6px.
7. The power-law chart is the hero: full-width, log-log by default, translucent amber band fills, band labels "Redline / Cruise / Idle" (never the rainbow chart's labels). Fit stats (b, R², σ, last refit) printed on the chart face in mono, like an instrument calibration plate.
8. The light theme is editorial — warm paper, serif masthead, zero glow, zero rivets — not "the engine theme with a white background." If it reads that way, reject it.
9. The Matrix/Digital-Rain theme (built post-v1, not in this project's early phases) needs the exact near-white head-glyph + translucent-black-fill trail technique, the half-width-katakana-plus-digits glyph set, no `backdrop-filter` on mobile, and ≥7:1 numeral contrast. Label it "Digital Rain," never anything Warner Bros. owns.
10. Theme default is `engine`, persisted via `localStorage` (not a cookie — a static site never reads it server-side), set by an inline pre-paint `<head>` script, with no `prefers-color-scheme` override of the engine default. The theme switcher must be visible in the first viewport.
