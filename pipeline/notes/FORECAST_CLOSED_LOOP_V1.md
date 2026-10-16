# Forecast closed-loop v1 (2026-10-16)

## Gap

Daily refit rebuilds conformal bands from **historical walk-forward** errors.
The live ledger (`data/forecasts.json`) freezes real published forecasts and
later records outcomes, but until now that ledger was **scorekeeping only** —
misses never adjusted the next cone.

First 1y ledger target matures 2027-09-30, so a long-only loop would sit idle
for a year. That is not operationally useful.

## Design (conservative, auditable)

1. **Short horizons on the live ledger** — each monthly freeze also records
   **30d** and **90d** Cruise forecasts (same `a`/`b`/bands as 1y/2y/4y).
   Existing long rows are never rewritten; missing short rows are appended
   onto older entries using that entry's frozen params (reconstructable
   published forecast, no peek ahead). Outcomes fill only when the target
   date arrives (unchanged rule).

2. **Correction estimator** (`pipeline/closed_loop.py`) — from matured ledger
   rows only:
   - `err = log10(actual) − log10(trend)` per matured call
   - Per-horizon mean with **shrinkage toward 0**:  
     `bias = n/(n+κ) * mean(err)`, `κ = 12`
   - Activate only when `n ≥ min_n` (3 per horizon, or pooled `n ≥ 4` for a
     global bias used as a weak prior on thin horizons)
   - **Cap** `|bias| ≤ 0.10` log10 (~×1.26 price)
   - **Band widen** (symmetric log offset add): if matured outer miss rate
     `> 0.20` and `n ≥ min_n`, add `widen = clip(miss_rate − 0.20, 0, 0.08)`
   - Identity correction when sample is too small (honest no-op)

3. **Apply inside `scenario_forecast.build()`** — for ≤4y (and 1m fan knots):
   - P50 residual gets `+ bias(h)` (interpolated across conformal knots)
   - Conformal P10/P90 offsets expand by `± widen(h)`
   - 10y/30y Idle/Redline corridor **unchanged** (different object; no
     independent ledger sample)

4. **Artifact** — every daily run writes `data/forecast_correction.json`
   (bias/widen/n/status/matured hashes). Idempotent; never mutates ledger
   forecast numbers.

5. **Honesty** — bands remain ≠ CIs. UI disclaimer notes the nudge is a
   shrunk ledger bias, not a recalibrated CI. Frozen ledger rows stay
   append-only aside from `outcome`.

## Why not refit a/b from ledger misses

Two monthly freezes and zero matured long calls: any direct parameter update
would overfit noise. Shrinkage + caps + short-horizon bootstrap is the
smallest closed loop that is still a real published→outcome→future path.

## Activation expectation

With 30d freezes from 2026-09/10, first matured 30d outcomes appear ~2026-10-30.
Correction stays `inactive_waiting_for_matured` until `min_n` is met, then
engages automatically on the next daily run.
