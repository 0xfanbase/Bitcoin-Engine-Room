# Forecast calibration v3 (2026-10-15)

## What we challenged after #23

| Rule in #23 | Walk-forward / Models evidence | Verdict |
|---|---|---|
| √t growth of log half-width | AR(1) innovation SD **saturates** by ~1–2y (~0.30 log10). WF AR1-error P10–P90 width is **0.95 → 0.85 → 0.75** at 1y/2y/4y — it does **not** keep growing. √t made 4y P90 ~$4.3M and 30y P90 ~$19B. | **Replace** |
| Single coverage inflate from Idle–Redline 1y outer | Outer band is a different object (2.5–97.5). Inflate was 1.0 anyway (cov 0.84≥0.80). Does not fix per-horizon shape. | **Demote** to metadata only |
| AR(1) P50 | Matches Models `short_term` (~0.7% at 1y). WF: AR1 halves 90d MAE vs trend. | **Keep** |
| In-sample residual q10/q90 × scale | Ignores that forecast error is AR1-relative; misses WF asymmetry (fat left tail at 1–2y). | **Replace** for ≤4y |
| Cycle tilt into P50 | 4 epochs, ~2.4 independent 4y windows. Overlay stays on Models. | **Skip** (not earned) |
| Decorative shares | Already removed in #23. | Keep out |

## Coherent slice chosen (not kitchen-sink)

1. **P50** — power-law Cruise + AR(1) mean residual (unchanged family).
2. **P10/P90 for 1m / 1y / 2y / 4y** — **per-horizon conformal** offsets: walk-forward quantiles of  
   `log10(actual) − (trend_h + AR1_mean_h)` at that horizon (monthly origins since 2013, refit causal). Asymmetric.
3. **P10/P90 for 10y / 30y** — **Idle/Redline corridor** from `models.json` (empirical 2.5/97.5 residual offsets) around Cruise. No √t. Tagged scenario-only; independent sample ≈ 0.
4. **Fan** — interpolate conformal knots through 4y, then taper offsets toward Idle/Redline by 10y.
5. **Honesty** — bands ≠ CIs; publish conformal n + approx independent windows; ledger labels unchanged.

## Why not port watcher conformal wholesale

Would dual-math private payloads and disagree with Models AR1. Reimplemented on BER allow-list only (price history + same AR1 helper as `fit_models`).

## UI implication: 2y P90 may sit near/under 1y P90

Walk-forward AR1-relative errors show **cycle mean-reversion**: the 90th percentile upside shrinks from 1y→2y→4y while the left tail stays fat. That is an honest feature of the sample (indep windows ~12.8 / 5.9 / 2.4), not a bug. Do not force monotone price bands.
