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
