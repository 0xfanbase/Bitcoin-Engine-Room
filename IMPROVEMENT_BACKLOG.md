# Improvement Backlog

Fed by two sources: manual ideas logged by whoever's working on the project, and (from Phase 5 onward) automatic findings appended by `pipeline/audit.py`. The weekly `/improve` ritual (Section 12 of the build spec) reads this file, picks the single highest-value item, implements it, and removes or checks off the entry.

**Entry format:**
```
### [PHASE-tag] Short title (YYYY-MM-DD)
Source: manual | audit-auto
Description: ...
Suggested fix: ...
```

Per spec Section 19: "where the spec is silent, choose the simplest option and log it here." Judgment calls made during Phase 1 that weren't spelled out in the spec are logged below for traceability.

---

### [P1] Two-profile sanity rules instead of one flat set (2026-07-09)
Source: manual
Description: Spec Section 6's sanity bounds (e.g. `price > 1000`) are written for validating one new live day, not 2010-era backfilled history where BTC traded under $1. Applying them unmodified to the full historical series would make every pre-2017 row fail.
Suggested fix (implemented): split `pipeline/sanity_rules.json` into a `live_snapshot` profile (Section 6's numbers, for P2's `fetch_snapshot.py`) and a `backfill_absolute` profile (looser, full-history-safe bounds, for this phase's pytest suite).

### [P1] Added `pipeline/validation.py` as a shared module (2026-07-09)
Source: manual
Description: The spec's file list (Section 5) doesn't name a shared validation module, but `backfill.py` (P1), `fetch_snapshot.py` (P2), and `audit.py` (P5) all need identical sanity-bound and cross-source-variance logic.
Suggested fix (implemented): one small, pure-function module (`pipeline/validation.py`) holding the shared checks, to avoid writing the same logic three times.

### [P1] Self-contained JSON Schemas, no cross-file `$ref` (2026-07-09)
Source: manual
Description: The spec doesn't specify whether schemas should share definitions via `$ref`. With only 5 history schemas plus 2 small config schemas, a `$ref` resolver adds complexity with no real payoff yet.
Suggested fix (implemented): each schema file in `pipeline/schemas/` is fully self-contained. Revisit if/when the schema count grows enough that duplication becomes painful.

### [P1] Hash-rate cross-source variance threshold widened (2026-07-09)
Source: manual (from director review)
Description: A tight variance threshold (matching price's ~1.5%) would flag `VARIANCE_WARN` constantly for hash rate, since different providers' estimators use different smoothing windows and legitimately disagree by double digits.
Suggested fix (implemented): `live_snapshot.cross_source_variance.hashrate_pct` set to ~0.18 (18%) instead of a tight threshold, with the rationale recorded inline in `sanity_rules.json`.

### [P1] Coin Metrics Community API now requires authorization (2026-07-09)
Source: manual (discovered running `backfill.py` live)
Description: The spec (Section 4) states Coin Metrics' community endpoints need no key. Live testing during Phase 1's backfill run shows `GET /v4/timeseries/asset-metrics` now returns `401 {"error":{"type":"unauthorized","message":"Requested resource requires authorization."}}` even for community metrics (`PriceUSD`, `HashRate`, `SplyCur`), with no key supplied. This is exactly the kind of "verified" fact the director review flagged as worth re-checking before hardcoding.
Suggested fix (implemented for P1): `backfill.py` catches the failure and falls through entirely to blockchain.com Charts for price/hashrate/supply backfill -- the current committed `data/history/*.json` files are 100% `blockchain_info`-sourced, not the CM-primary/BI-fallback split the corrections doc describes. Follow-up for a later phase: either sign up for a free Coin Metrics API key and wire it into `CoinMetricsClient` (still $0), or formally demote Coin Metrics to a documented fallback and stop treating it as primary in CLAUDE.md's source table.

### [P1] blockchain.info hash-rate unit is TH/s, not GH/s (2026-07-09)
Source: manual (discovered running `backfill.py` live)
Description: The chart's own `unit` field reports "Hash Rate TH/s". The original GH/s assumption would have under-converted by 1000x (e.g. reporting ~0.9 EH/s instead of ~900 EH/s for the current network).
Suggested fix (implemented): `BlockchainInfoChartsClient.UNIT_CONVERSIONS["hash-rate"]` divides by 1e6 (TH/s -> EH/s), not 1e9.

### [P1] blockchain.info supply chart is `total-bitcoins`, not `total-bc`; is per-block, not per-day (2026-07-09)
Source: manual (discovered running `backfill.py` live)
Description: `total-bc` 404s. The correct chart name is `total-bitcoins`. Even with `sampled=false`, it returns one point per block (~924k points across history) rather than one per day, so multiple points can land on the same UTC date.
Suggested fix (implemented): `METRIC_CONFIG["supply"]["bi_chart"] = "total-bitcoins"`; `BlockchainInfoChartsClient.parse_chart_values` now collapses to one row per date (keeping the last/latest value of that day) for every chart, which is a no-op for genuinely-daily charts and required for this one.

### [P1] blockchain.info's market-price and difficulty charts carry placeholder 0.0 rows (2026-07-09)
Source: manual (discovered running `backfill.py` live)
Description: Both charts include `0.0` rows before Bitcoin had a real exchange price (pre-mid-2010) or before difficulty tracking began -- not real observations, and disallowed by the schemas' `exclusiveMinimum: 0`.
Suggested fix (implemented): `build_series_for_metric` in `backfill.py` filters out non-positive rows for `price` and `difficulty` before writing. `price_daily.json` consequently starts 2010-08-18, not earlier -- consistent with the spec's own guidance that model-fit history should start ~2010-07 onward (Section 8.1).

### [P1] Difficulty's backfill sanity threshold left unenforced, not copied from live_snapshot (2026-07-09)
Source: manual (discovered running `backfill.py` live)
Description: Applying `live_snapshot`'s 30%-per-retarget bound to the full backfilled series flagged 30+ "violations" from 2010-2013, when Bitcoin's difficulty legitimately swung 30-300%+ per retarget during the CPU->GPU->FPGA->ASIC hardware transitions. A single fixed threshold doesn't fit both that era and the stable modern one.
Suggested fix (implemented): `backfill_absolute.difficulty.max_pct_change_between_distinct_values` is `null` (unenforced) with the rationale recorded inline in `sanity_rules.json`; the `min`/`max` bounds and schema `exclusiveMinimum` remain the real corruption check for backfilled difficulty data.

### [P2] mempool.space's difficulty-adjustment endpoint has no raw difficulty value (2026-07-09)
Source: manual (discovered running `fetch_snapshot.py` live)
Description: `/api/v1/difficulty-adjustment` only returns retarget PROGRESS fields (`progressPercent`, `difficultyChange`, `estimatedRetargetDate`, etc.) despite its name suggesting it might carry the current difficulty. The raw `currentDifficulty` value actually lives in `/api/v1/mining/hashrate/3d`'s payload (which also carries `currentHashrate`).
Suggested fix (implemented): `MempoolSpaceClient.fetch_difficulty()` now calls `/v1/mining/hashrate/3d` and reads `currentDifficulty` from it, same endpoint `fetch_hashrate()` already used for `currentHashrate`. CLAUDE.md's source table corrected.

### [P2] Idempotent-skip path fabricated a misleading STALE health record (2026-07-09)
Source: manual (discovered running `fetch_snapshot.py` live -- fng_daily's backfill had already reached today, so its first live run hit the idempotent-skip branch with no prior health.json to reuse)
Description: When a metric's history already has today's date recorded (skip -- nothing to fetch) and there's no prior `health.json` entry to carry forward, the original code fell back to a hardcoded blank record with `status: "STALE"`. That's wrong: the data present IS valid and current, just not fetched by *this* run.
Suggested fix (implemented): added `_health_record_from_existing()`, which derives a proper `OK` record (real source, real last-success date) from the already-committed row instead of fabricating STALE.

### [P2] Off-by-one in the subsidy-schedule supply calculation (2026-07-09)
Source: manual (caught by a self-authored pytest test, `test_supply_after_first_epoch_matches_full_epoch_payout`, before this ever ran live)
Description: `estimate_supply_at_height()` treated `height` as a block *count* rather than a 0-indexed block *number* -- height 209,999 (the last block before the first halving) was computed as 209,999 x 50 BTC instead of the correct 210,000 x 50 BTC, since blocks 0 through 209,999 inclusive is 210,000 blocks.
Suggested fix (implemented): `blocks_mined = height + 1` before applying the epoch/remainder math. Re-verified against the real observed supply (~20,053,881 BTC at tip height ~957,246) -- estimate now within 0.00006%.

### [P2] Block height and fees are live-only, not stored as history (2026-07-09)
Source: manual (design decision, spec is ambiguous)
Description: Spec Section 6's pipeline pseudocode implies every metric gets `append_to_history()`, but Section 5's repo structure only lists 5 history files (price/hashrate/difficulty/supply/fng) -- no `block_height_daily.json` or `fees_daily.json`. `sanity_rules.json`'s `live_snapshot.block_height` rule was pinned in P1 "for P2" but P2 doesn't actually snapshot block height into its own series.
Suggested fix (implemented): block height is fetched transiently in `fetch_snapshot.py` only as an input to `supply_daily`'s subsidy-schedule cross-check/fallback (via `MempoolSpaceClient.fetch_tip_height()`), never persisted as its own history file. Fees have no pipeline role at all -- they're browser-only (P3's live.js, polled client-side, never historically stored) per spec Section 4/7. `sanity_rules.json`'s `block_height` rule stays defined for that transient use and for potential P3 client-side reuse.

### [P2] Difficulty transitions from sparse (P1) to dense-daily (P2+) cadence (2026-07-09)
Source: manual (design decision, spec is silent on this transition)
Description: P1's backfilled `difficulty_daily.json` is a sparse step function (one row per retarget, per spec Section 5's original intent). From P2 onward, `fetch_snapshot.py` appends one row every calendar day regardless of whether difficulty actually changed (repeating the same value between retargets) -- consistent with every other metric's daily cadence and simpler than tracking "did it change" specially, but does mean the series' row-spacing character changes partway through.
Suggested fix (implemented): schema's `series` description now notes the P1-sparse/P2-dense split explicitly so it isn't mistaken for a data gap later. No functional issue -- schema and sanity checks handle both densities fine.

### [P2] GitHub issue automation couldn't be fully verified locally (2026-07-09)
Source: manual (environment limitation, not a code defect)
Description: This sandbox has a `GITHUB_TOKEN` env var set, but it's not a real GitHub PAT with API access to this repo (a real live call returned `403 Forbidden`) -- `_handle_github_issue()` correctly caught the exception and logged a warning without failing the snapshot, which is the intended graceful-degradation behavior, but the actual open-issue/auto-close flow against a real repo has only been unit-tested (`test_gh_issues.py`, `test_fetch_snapshot.py`'s 3-consecutive-failures case), not exercised against the live GitHub API.
Suggested fix: none needed for now -- `daily.yml` sets `permissions: issues: write` and passes `secrets.GITHUB_TOKEN`, which should be a properly-scoped token once this runs in real GitHub Actions. Worth a manual check the first time `daily.yml` actually fires (or is triggered via `workflow_dispatch`) that an issue really opens/closes as expected.

### [P2] healthchecks.io ping not implemented (2026-07-09)
Source: manual
Description: Spec Section 6 lists an external healthchecks.io ping as an "optional" mitigation for GitHub's cron best-effort/60-day-auto-disable behavior, alongside the on-site "last snapshot age" red-flag (P5) and keeping GitHub email notifications on (a personal account setting, not a repo artifact).
Suggested fix: not implemented, since it requires an external account signup outside this repo's control. Revisit in P5/P6 if the owner wants the extra safety net -- it's a two-line curl call added to the end of `daily.yml`.

### [P3] mempool.space WebSocket message shape corrected against a real connection (2026-07-09)
Source: manual (verified via a direct wss:// connection outside this sandbox's proxy -- see below -- since Chromium testing through the proxy wasn't possible)
Description: Spec Section 7.1's `{"block": {...}}` (singular) description doesn't match reality. A real subscription (`{"action":"want","data":["blocks","stats","mempool-blocks"]}`) returns a single combined message per update containing whichever of `blocks` (a plural array of recent blocks, most-recent last), `mempoolInfo` (accurate full mempool tx count in `.size`), `mempool-blocks` (only the next few projected block templates), `fees`, `da` (difficulty-adjustment progress), `vBytesPerSecond`, and `transactions` are relevant -- never a singular `block` key. Also found: `live.js` was calling `renderMempool()` twice per message when both `mempoolInfo` and `mempool-blocks` were present, with `mempool-blocks`' much smaller `nTx` sum incorrectly overwriting `mempoolInfo`'s accurate count second.
Suggested fix (implemented): `handleWsMessage` now only handles the plural `blocks` key (the singular-`block` branch was unreachable dead code, removed); `mempool-blocks`' `nTx` sum is used only as a fallback when `mempoolInfo` is absent, never as a second overriding call.

### [P3] This sandbox's proxy does not support WebSocket upgrades -- frontend live-network testing used mocks instead (2026-07-09)
Source: manual (environment limitation, not a code defect -- confirmed explicitly in `/root/.ccr/README.md`: "Not supported through the proxy (report, do not work around): ... WebSocket upgrades")
Description: Headless Chromium routed through this sandbox's HTTPS_PROXY hangs indefinitely on `new WebSocket('wss://mempool.space/api/v1/ws')` (readyState stays CONNECTING). A direct `wss://` connection via Python's `websockets` library (which doesn't read `HTTPS_PROXY`) succeeded and was used to confirm the real message shape above -- an incidental, not deliberate, proxy bypass; no TLS verification was disabled for it. Chromium's `ignore-certificate-errors`/`ignoreHTTPSErrors` route was tried and rejected by the environment's own safety policy, correctly, since that would have blanket-disabled TLS verification rather than trusting the sandbox's specific CA bundle.
Suggested fix (implemented): verified `live.js`'s WebSocket/fetch DOM-update logic deterministically by stubbing `window.WebSocket` and `window.fetch` in-page with the real confirmed response shapes (same principle as the pipeline's `responses`-mocked pytest suite) -- confirmed block-height odometer updates, halving countdown, price/fees polling, mempool count, and the offline/STALE-chip fallback (gauges never blank) all work correctly. The real WebSocket path itself will only be exercised for real once deployed to GitHub Pages (no interposing proxy there); worth a manual check after first deploy.

### [P3] Frontend has no automated test suite yet (2026-07-09)
Source: manual (design decision, matches spec)
Description: Spec Section 3 explicitly calls the Playwright smoke test "optional, phase 5" -- P3's verification here was manual (Playwright driven ad hoc from the scratchpad, not committed to the repo).
Suggested fix: revisit in P5/P6 if a standing frontend smoke test is wanted in CI; not required by P3's acceptance criteria.

### [P3] Repo moved out from under the `fandamentals/...` slug mid-build (2026-07-09)
Source: manual (discovered via GitHub's own move notice on `git push`)
Description: Every reference to the repo slug/Pages URL (schema `$id`s, `sources.py`'s User-Agent string, `gh_issues.py`'s `GITHUB_REPOSITORY` fallback, `index.html`'s footer link, `docs/PHASE1_DIRECTOR_CORRECTIONS.md`, CLAUDE.md) was written against `fandamentals/bitcoin-engine-room` during P1, before the repo moved to `0xfanbase/Bitcoin-Engine-Room`.
Suggested fix (implemented): swept and corrected every reference in one pass (14 files) once the move was noticed. Nothing depended on the old slug being reachable (schema `$id`s aren't fetched over network; `GITHUB_REPOSITORY` is always set correctly by real Actions runs regardless of the hardcoded fallback), so no data was affected -- this was a documentation/identifier correction, not a functional bug.
