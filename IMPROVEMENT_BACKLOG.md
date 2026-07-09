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
