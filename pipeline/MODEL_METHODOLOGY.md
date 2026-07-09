# Model methodology (pinned constants for fit_models.py, P4)

`fit_models.py` doesn't exist yet (it's P4 scope) but its fit methodology is pinned here in Phase 1, in machine-readable form in `model_constants.json`, so that later drift-audits (spec Section 11.3) measure real data drift rather than ambiguity about *how* the fit was done. Changing any of these constants is a deliberate methodology change, not a routine data update, and should be called out explicitly in a commit message and `PROGRESS.md`.

## Power law corridor (spec Section 8.1)

Model: `log10(price) = a + b * log10(d)`, where `d` = days since Bitcoin's genesis block (`2009-01-03`).

- **Fit start date: `2010-07-17`.** Price data before this is thin/illiquid (pre-exchange era); the spec itself says "full history from ~2010-07 onward." Fixed here so the fit window can't silently drift.
- **Sampling: daily, no downsampling.** Every row in `price_daily.json` from the fit start date onward is used.
- **Weighting: unweighted OLS.** Plain ordinary least squares in log-log space, equal weight per daily observation. This is a real methodology choice, not a neutral default — `log10(d)` compresses over time, so a year of recent data occupies far less x-axis span than a year from 2011 while contributing the same number of points, meaning recent history has outsized influence on the fit. Pinning "unweighted" here means that effect is a known, stable property of the method, not something that changes if a future session quietly reaches for a different weighting scheme.
- **Expected range** (`model_constants.json` → `power_law.expected_range`): `b ≈ 5.7–5.9`, `a ≈ −17.0 to −16.5`, `R² ≥ 0.95`, consistent with the publicly published Santostasi power-law fits. This is a sanity band for the *fit process*, not a target to reverse-engineer into.
- **Audit drift thresholds** are intentionally wider than the expected range: `b` triggers a WARN only outside `[5.4, 6.1]` (not right at the expected-range edges), because a single bad data point among ~5,800+ daily observations has negligible leverage on `b` — the audit's job is to catch bulk data corruption or a broken fit script, not to enforce the reference range as a hard gate. `R²` drop >0.01 day-over-day, or `b` moving >0.5% day-over-day, are the other drift signals (spec Section 11.3).
- **Bands (±1σ, ±2σ) are descriptive envelopes, not confidence intervals.** Log-price residuals here are heavily autocorrelated across multi-year cycles, so standard confidence-interval interpretation doesn't apply — site copy must never call them "confidence bands."

## 4-year cycle overlay (spec Section 8.2)

Halving dates and the next estimated halving are pinned in `model_constants.json` → `cycle_overlay`, sourced from the spec: `2012-11-28`, `2016-07-09`, `2020-05-11`, `2024-04-20`, next at height 1,050,000 (~2028-04).
