# Progress Log

How to use this file: read the latest entry (top of the list) plus `CLAUDE.md` before starting any new work in this repo. Add a new dated entry after each meaningful unit of work, newest first. This is the project's memory across sessions.

## Phase checklist

- [ ] P1 — Skeleton & backfill
- [ ] P2 — Daily pipeline
- [ ] P3 — Frontend core
- [ ] P4 — Models & charts
- [ ] P5 — Audit & health panel
- [ ] P6 — Polish

## Log

### 2026-07-09 — Phase 1 kicked off

- Reviewed `BTC_ENGINE_ROOM_BUILD_SPEC.md` (v1.0 + v1.1 addendum) via an independent director-level review; see `docs/PHASE1_DIRECTOR_CORRECTIONS.md` for the full verdict and the corrections it produced.
- Committed the spec and the corrections doc into `docs/`.
- Starting Phase 1 build per spec Section 13 P1 / Section 19: CLAUDE.md, `pipeline/sources.py`, `pipeline/backfill.py`, schemas, pytest coverage, then a real backfill run against live APIs.
