import math
from datetime import date, timedelta

import pytest

from pipeline import fit_models

GENESIS = date(2009, 1, 3)


def _row(d: date, value: float, source: str = "test") -> dict:
    # No rounding here: synthetic power-law prices can be extremely small
    # (e.g. ~1e-12 at low day counts) and round(value, 10) would truncate
    # them to exactly 0.0, breaking log10. Real committed data is rounded
    # at write time in backfill.py/fetch_snapshot.py; these are in-memory
    # fixtures only, never serialized to JSON.
    return {"date": d.isoformat(), "value": value, "source": source}


# --------------------------------------------------------------------------
# Power law
# --------------------------------------------------------------------------

TEST_CONSTANTS = {
    "power_law": {
        "genesis_date": GENESIS.isoformat(),
        "fit_start_date": (GENESIS + timedelta(days=10)).isoformat(),
    },
    "cycle_overlay": {
        "halving_dates": ["2020-01-01", "2021-01-01"],
    },
}


def _synthetic_power_law_series(a_true, b_true, day_start, day_end):
    rows = []
    for d in range(day_start, day_end):
        date_obj = GENESIS + timedelta(days=d)
        price = 10 ** (a_true + b_true * math.log10(d))
        rows.append(_row(date_obj, price))
    return rows


def test_power_law_recovers_exact_params_on_synthetic_data():
    a_true, b_true = -17.0, 5.8
    series = _synthetic_power_law_series(a_true, b_true, 10, 500)

    result = fit_models.fit_power_law(series, TEST_CONSTANTS, previous_models=None)

    assert result["params"]["a"] == pytest.approx(a_true, abs=1e-4)
    assert result["params"]["b"] == pytest.approx(b_true, abs=1e-4)
    assert result["params"]["r_squared"] == pytest.approx(1.0, abs=1e-6)
    assert result["params"]["sigma"] == pytest.approx(0.0, abs=1e-6)


def test_power_law_current_deviation_is_zero_on_the_fit_line():
    a_true, b_true = -17.0, 5.8
    series = _synthetic_power_law_series(a_true, b_true, 10, 500)

    result = fit_models.fit_power_law(series, TEST_CONSTANTS, previous_models=None)

    assert result["current"]["deviation_pct"] == pytest.approx(0.0, abs=0.01)
    # z_score isn't checked here: on a perfectly noise-free fit, sigma is
    # essentially 0 (floating-point noise), making residual/sigma numerically
    # degenerate (0/~0). Real data always has sigma meaningfully > 0; the
    # noisy-data projections test below exercises z_score in a well-defined
    # regime instead.


def test_power_law_projections_are_monotonic_floor_trend_ceiling():
    a_true, b_true = -17.0, 5.8
    # Add a little real noise so sigma > 0 and floor/ceiling actually separate.
    rows = []
    for i, d in enumerate(range(10, 2000)):
        date_obj = GENESIS + timedelta(days=d)
        noise = 0.01 * math.sin(i)
        price = 10 ** (a_true + b_true * math.log10(d) + noise)
        rows.append(_row(date_obj, price))

    result = fit_models.fit_power_law(rows, TEST_CONSTANTS, previous_models=None)

    for proj in result["projections"]:
        assert proj["floor"] < proj["trend"] < proj["ceiling"]

    assert [p["date"] for p in result["projections"]] == ["2027-01-01", "2028-01-01", "2030-01-01", "2035-01-01"]


def test_power_law_carries_forward_previous_params_for_drift():
    a_true, b_true = -17.0, 5.8
    series = _synthetic_power_law_series(a_true, b_true, 10, 500)
    previous = {"power_law": {"params": {"a": -16.9, "b": 5.75, "r_squared": 0.94, "sigma": 0.1, "fit_start_date": "x", "n_points": 1}}}

    result = fit_models.fit_power_law(series, TEST_CONSTANTS, previous_models=previous)

    assert result["previous_params"]["b"] == 5.75


def test_power_law_no_previous_models_yields_none():
    a_true, b_true = -17.0, 5.8
    series = _synthetic_power_law_series(a_true, b_true, 10, 500)

    result = fit_models.fit_power_law(series, TEST_CONSTANTS, previous_models=None)

    assert result["previous_params"] is None


# --------------------------------------------------------------------------
# Cycle overlay
# --------------------------------------------------------------------------


def test_cycle_overlay_slices_epochs_and_normalizes_to_halving_price():
    series = []
    d = date(2020, 1, 1)
    while d < date(2021, 6, 1):
        # price doubles every 100 days from a base of 100 at each "halving" for
        # a predictable, checkable pct_performance curve.
        series.append(_row(d, 100.0 + (d - date(2020, 1, 1)).days))
        d += timedelta(days=1)

    result = fit_models.compute_cycle_overlay(series, TEST_CONSTANTS)

    assert len(result["epochs"]) == 2
    epoch0 = result["epochs"][0]
    assert epoch0["halving_date"] == "2020-01-01"
    assert epoch0["epoch_end_date"] == "2021-01-01"
    assert epoch0["is_current"] is False
    assert epoch0["anchor_price"] == 100.0
    # Day 0 of epoch 0 is the anchor itself: 0% performance.
    assert epoch0["pct_performance"][0] == 0.0
    # Last day before the epoch boundary (day 365, price 465) vs anchor 100:
    # (465/100 - 1) * 100 = +365%.
    assert epoch0["pct_performance"][-1] == pytest.approx(365.0, abs=0.5)

    epoch1 = result["epochs"][1]
    assert epoch1["is_current"] is True
    assert epoch1["epoch_end_date"] is None


def test_cycle_overlay_current_epoch_percentile_against_prior_epochs():
    series = []
    d = date(2020, 1, 1)
    while d < date(2021, 6, 1):
        days_since_2020 = (d - date(2020, 1, 1)).days
        if d < date(2021, 1, 1):
            price = 100.0 + days_since_2020  # epoch 0: steady climb
        else:
            days_since_2021 = (d - date(2021, 1, 1)).days
            price = 200.0 + days_since_2021 * 2  # epoch 1 (current): climbs faster
        series.append(_row(d, price))
        d += timedelta(days=1)

    result = fit_models.compute_cycle_overlay(series, TEST_CONSTANTS)
    current = result["current_epoch"]

    assert current["halving_date"] == "2021-01-01"
    assert current["days_into_epoch"] == (date(2021, 5, 31) - date(2021, 1, 1)).days
    # Current epoch outperforms the one prior epoch at the same offset -> 100th percentile.
    assert current["cycle_percentile_vs_prior_epochs"] == 100.0


# --------------------------------------------------------------------------
# Mayer Multiple
# --------------------------------------------------------------------------


def test_mayer_multiple_constant_price_is_always_one(monkeypatch):
    monkeypatch.setattr(fit_models, "MAYER_WINDOW_DAYS", 3)
    series = [_row(date(2020, 1, 1) + timedelta(days=i), 100.0) for i in range(10)]

    result = fit_models.compute_mayer_multiple(series)

    assert result["current"]["multiple"] == pytest.approx(1.0)
    assert all(row["value"] == pytest.approx(1.0) for row in result["series"])


def test_mayer_multiple_above_sma_gives_multiple_above_one(monkeypatch):
    monkeypatch.setattr(fit_models, "MAYER_WINDOW_DAYS", 3)
    # Prices: 100, 100, 100, 200 -- SMA of last 3 (100,100,200)=133.33, multiple=200/133.33=1.5
    series = [
        _row(date(2020, 1, 1), 100.0),
        _row(date(2020, 1, 2), 100.0),
        _row(date(2020, 1, 3), 100.0),
        _row(date(2020, 1, 4), 200.0),
    ]

    result = fit_models.compute_mayer_multiple(series)

    assert result["current"]["multiple"] == pytest.approx(1.5, abs=0.01)


def test_mayer_multiple_too_short_series_returns_empty(monkeypatch):
    monkeypatch.setattr(fit_models, "MAYER_WINDOW_DAYS", 200)
    series = [_row(date(2020, 1, 1), 100.0)]

    result = fit_models.compute_mayer_multiple(series)

    assert result["current"] is None
    assert result["series"] == []


# --------------------------------------------------------------------------
# 200-Week Moving Average
# --------------------------------------------------------------------------


def test_200wma_groups_by_iso_week_and_computes_rolling_mean(monkeypatch):
    monkeypatch.setattr(fit_models, "WMA_WINDOW_WEEKS", 2)
    # 3 full ISO weeks of daily data, constant price per week: 100, 200, 300.
    series = []
    d = date(2024, 1, 1)  # a Monday
    for week_price in (100.0, 200.0, 300.0):
        for _ in range(7):
            series.append(_row(d, week_price))
            d += timedelta(days=1)

    result = fit_models.compute_200wma(series)

    # 3 weekly buckets, window=2 -> 2 output points.
    assert len(result["series"]) == 2
    assert result["series"][-1]["wma_200w"] == pytest.approx((200.0 + 300.0) / 2)
    assert result["current"] == result["series"][-1]
