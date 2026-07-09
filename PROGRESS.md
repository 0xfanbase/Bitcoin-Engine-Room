# Progress Log

How to use this file: read the latest entry (top of the list) plus `CLAUDE.md` before starting any new work in this repo. Add a new dated entry after each meaningful unit of work, newest first. This is the project's memory across sessions.

## Phase checklist

- [x] P1 — Skeleton & backfill
- [ ] P2 — Daily pipeline
- [ ] P3 — Frontend core
- [ ] P4 — Models & charts
- [ ] P5 — Audit & health panel
- [ ] P6 — Polish

## Log

### 2026-07-09 — Phase 1 complete

- Built the full P1 scope: `CLAUDE.md`, `pipeline/sources.py` (backfill-scope fetchers), `pipeline/validation.py`, `pipeline/backfill.py`, all JSON schemas, `sanity_rules.json` (two profiles), `model_constants.json` + `MODEL_METHODOLOGY.md`, and 42 pytest tests (all HTTP-mocked, no live calls in CI).
- Ran `python -m pipeline.backfill` for real against live APIs (`REQUESTS_CA_BUNDLE` set for the sandbox's TLS-intercepting proxy). Result: `price_daily` 5804 rows (2010-08-18 → 2026-07-08), `hashrate_daily` 6393 rows, `supply_daily` 6391 rows, `difficulty_daily` 6388 rows (all 2009-01-03 → 2026-07-08), `fng_daily` 3077 rows (2018-02-01 → 2026-07-09). Spot-checked price against known peaks (2013-11-29 ~$1014, 2017-12-17 ~$19,280, 2021-11-10 ~$66,954) — all plausible. Re-ran and confirmed idempotency (identical `series`, only `generated_at` differs).
- `pytest pipeline/tests -v` — 42/42 passed, including schema + backfill-sanity validation of the real committed history files.
- **Real-world discoveries during the live backfill run** (all logged in detail in `IMPROVEMENT_BACKLOG.md`, source table updated in `CLAUDE.md` Section 3): Coin Metrics Community API now returns 401 without a key (spec assumed no key needed) — `backfill.py` falls through to blockchain.info entirely for price/hashrate/supply; blockchain.info's hash-rate unit is TH/s, not GH/s; the supply chart is named `total-bitcoins` (not `total-bc`) and is per-block, not per-day; market-price and difficulty both carry placeholder 0.0 rows that needed filtering; difficulty's early-era volatility (30-300%+ swings, 2009-2013) meant the backfill sanity profile leaves the per-retarget swing check unenforced. These are exactly the class of issue the pre-build director review flagged as worth re-verifying rather than trusting as written.
- Reviewed `BTC_ENGINE_ROOM_BUILD_SPEC.md` (v1.0 + v1.1 addendum) via an independent director-level review before starting; see `docs/PHASE1_DIRECTOR_CORRECTIONS.md` for the full verdict and the corrections it produced.
- Next: P2 (daily pipeline) — `fetch_snapshot.py`, `health.json`, `daily.yml`. Consider resolving the Coin Metrics 401 (free API key) before P2 so the live/failover chain matches what CLAUDE.md documents.
