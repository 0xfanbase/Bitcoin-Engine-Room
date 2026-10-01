"""Shared model-honesty math (Phases A-D, 2026-10-01), used by both
fit_models.py (what the site shows) and backtest.py (how well what the site
shows would have done, scored walk-forward with no look-ahead).

Every function here is pure: arrays in, plain numbers/dicts out, so the same
code path that draws the bands is the one the backtest grades. Methodology
constants live in model_constants.json -> "honesty"; see
MODEL_METHODOLOGY.md's "Model honesty" section before changing any of it.

Conventions: `x` = log10(days since genesis), `y` = log10(price),
`d` = days since genesis (float). Residual = y - (a + b*x).
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np


def ols_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """(a, b, sigma) of y = a + b*x; sigma is the residual sample std."""
    b, a = np.polyfit(x, y, 1)
    resid = y - (a + b * x)
    sigma = float(np.std(resid, ddof=1)) if len(resid) > 1 else 0.0
    return float(a), float(b), sigma


def residual_quantiles(resid: np.ndarray, quantiles: list[float]) -> list[float]:
    """Empirical residual quantiles (log10 units). Chosen over +/-k*sigma
    because the real residual distribution is skewed (long upside tail,
    short downside) -- a symmetric gaussian band puts the floor far below
    anywhere price has actually been. Walk-forward calibration of both is
    reported in data/backtest.json."""
    return [float(q) for q in np.quantile(resid, quantiles)]


def coverage(resid: np.ndarray, lo: float, hi: float) -> float:
    """Share of residuals inside [lo, hi]."""
    if len(resid) == 0:
        return 0.0
    return float(np.mean((resid >= lo) & (resid <= hi)))


def bootstrap_trend(
    x: np.ndarray,
    y: np.ndarray,
    *,
    block_days: int,
    reps: int,
    seed: int,
) -> np.ndarray:
    """Circular moving-block bootstrap of the residuals around the fitted
    trend, refit each replicate -> array of (a, b) pairs, shape (reps, 2).

    Residuals are autocorrelated over multi-year cycles, so resampling single
    days would wildly understate parameter uncertainty; resampling whole
    `block_days`-long blocks keeps that structure. Seeded so the nightly
    refit is deterministic (no day-to-day jitter from randomness alone)."""
    a, b, _ = ols_fit(x, y)
    fitted = a + b * x
    resid = y - fitted
    n = len(resid)
    block = max(1, min(block_days, n))
    rng = np.random.default_rng(seed)
    out = np.empty((reps, 2))
    n_blocks = n // block + 1
    offsets = np.arange(block)
    for i in range(reps):
        starts = rng.integers(0, n, size=n_blocks)
        idx = ((starts[:, None] + offsets[None, :]) % n).ravel()[:n]
        bb, aa = np.polyfit(x, fitted + resid[idx], 1)
        out[i] = (aa, bb)
    return out


def ar1_residual(resid: np.ndarray, sample_days: int) -> tuple[float, float]:
    """(phi, innovation_sigma) of an AR(1) fit to residuals sampled every
    `sample_days` -- the speed at which price has historically drifted back
    toward the trend line. phi is clipped to [0, 0.999] so a pathological
    fit can never produce explosive or oscillating forecasts."""
    r = resid[::sample_days]
    if len(r) < 3:
        return 0.0, float(np.std(resid)) if len(resid) else 0.0
    phi = float(np.clip(np.polyfit(r[:-1], r[1:], 1)[0], 0.0, 0.999))
    innov = r[1:] - phi * r[:-1]
    return phi, float(np.std(innov, ddof=1))


def ar1_path(r0: float, phi: float, innov_sigma: float, steps: int) -> list[tuple[float, float]]:
    """[(expected_residual, residual_sd), ...] for 1..steps AR(1) steps ahead."""
    out = []
    for h in range(1, steps + 1):
        mean = r0 * phi**h
        if phi < 1.0:
            var = innov_sigma**2 * (1 - phi ** (2 * h)) / (1 - phi**2) if phi > 0 else innov_sigma**2
        else:
            var = innov_sigma**2 * h
        out.append((mean, math.sqrt(var)))
    return out


def half_life_days(phi: float, sample_days: int) -> float | None:
    if phi <= 0 or phi >= 1:
        return None
    return sample_days * math.log(0.5) / math.log(phi)


def month_starts(start: date, end: date) -> list[date]:
    """First-of-month dates in [start, end]."""
    out = []
    d = date(start.year, start.month, 1)
    if d < start:
        d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    while d <= end:
        out.append(d)
        d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return out


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


def spearman(u: np.ndarray, v: np.ndarray) -> float | None:
    if len(u) < 3:
        return None
    ru = np.argsort(np.argsort(u)).astype(float)
    rv = np.argsort(np.argsort(v)).astype(float)
    c = np.corrcoef(ru, rv)[0, 1]
    return None if np.isnan(c) else float(c)
