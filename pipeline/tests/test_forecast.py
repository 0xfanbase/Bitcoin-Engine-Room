import math
from datetime import date

import numpy as np
import pytest

from pipeline import forecast


def test_ols_fit_recovers_line():
    x = np.linspace(1, 4, 200)
    a, b, sigma = forecast.ols_fit(x, -3 + 2 * x)
    assert a == pytest.approx(-3) and b == pytest.approx(2) and sigma == pytest.approx(0, abs=1e-12)


def test_residual_quantiles_and_coverage_are_consistent():
    resid = np.linspace(-1, 1, 1001)
    lo, hi = forecast.residual_quantiles(resid, [0.025, 0.975])
    assert lo == pytest.approx(-0.95) and hi == pytest.approx(0.95)
    assert forecast.coverage(resid, lo, hi) == pytest.approx(0.95, abs=0.002)
    assert forecast.coverage(np.array([]), -1, 1) == 0.0


def test_bootstrap_is_deterministic_and_centered():
    rng = np.random.default_rng(0)
    x = np.linspace(2.5, 3.8, 2000)
    y = -16 + 5.6 * x + 0.05 * np.sin(np.arange(2000) / 100) + rng.normal(0, 0.02, 2000)
    one = forecast.bootstrap_trend(x, y, block_days=200, reps=60, seed=7)
    two = forecast.bootstrap_trend(x, y, block_days=200, reps=60, seed=7)
    assert one.shape == (60, 2)
    assert np.array_equal(one, two)
    assert np.median(one[:, 1]) == pytest.approx(5.6, abs=0.05)


def test_ar1_recovers_persistence_and_path_decays():
    rng = np.random.default_rng(1)
    r = [0.0]
    for _ in range(3000):
        r.append(0.8 * r[-1] + rng.normal(0, 0.1))
    phi, innov = forecast.ar1_residual(np.array(r), 1)
    assert phi == pytest.approx(0.8, abs=0.03)
    assert innov == pytest.approx(0.1, abs=0.01)
    path = forecast.ar1_path(1.0, 0.5, 0.1, 3)
    assert [m for m, _ in path] == pytest.approx([0.5, 0.25, 0.125])
    sds = [sd for _, sd in path]
    assert sds == sorted(sds)  # uncertainty only grows with horizon
    assert sds[-1] < 0.1 / math.sqrt(1 - 0.25) + 1e-9  # bounded by the stationary sd


def test_ar1_phi_clipped_to_non_explosive():
    phi, _ = forecast.ar1_residual(np.array([1.0, 2.0, 4.0, 8.0, 16.0]), 1)
    assert 0.0 <= phi <= 0.999


def test_half_life():
    assert forecast.half_life_days(0.5, 30) == pytest.approx(30)
    assert forecast.half_life_days(0.0, 30) is None
    assert forecast.half_life_days(1.0, 30) is None


def test_month_starts_handles_year_rollover_and_mid_month_start():
    assert forecast.month_starts(date(2020, 11, 15), date(2021, 2, 1)) == [date(2020, 12, 1), date(2021, 1, 1), date(2021, 2, 1)]
    assert forecast.month_starts(date(2020, 1, 1), date(2020, 1, 31)) == [date(2020, 1, 1)]


def test_spearman():
    u = np.arange(10.0)
    assert forecast.spearman(u, u**3) == pytest.approx(1.0)
    assert forecast.spearman(u, -u) == pytest.approx(-1.0)
    assert forecast.spearman(u[:2], u[:2]) is None
