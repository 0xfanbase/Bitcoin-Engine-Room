import json
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
# Cycle tops (owner request, 2026-07-26)
# --------------------------------------------------------------------------


def test_identify_cycle_tops_confirms_after_drawdown_and_flags_trailing_unconfirmed_high():
    rows = [
        _row(date(2020, 1, 1), 100.0),
        _row(date(2020, 1, 2), 1000.0),  # running high
        _row(date(2020, 1, 3), 500.0),  # -50% from the high: not enough to confirm (threshold 70%)
        _row(date(2020, 1, 4), 250.0),  # -75%: confirms 2020-01-02 as a top
        _row(date(2020, 1, 5), 200.0),  # still seeking the bottom
        _row(date(2020, 1, 6), 400.0),  # +100% off the 200 low: confirms the bottom (threshold 50%)
        _row(date(2020, 1, 7), 600.0),  # new running high
        _row(date(2020, 1, 8), 450.0),  # -25% off 600: not enough to confirm -> stays a provisional top
    ]

    tops = fit_models.identify_cycle_tops(rows, 0.70, 0.50)

    # date/price/confirmed only -- era membership and any "how far has it
    # fallen" figure are compute_cycle_tops' job, not this function's (it has
    # no idea what a halving era is).
    assert tops == [
        {"date": "2020-01-02", "price": 1000.0, "confirmed": True},
        {"date": "2020-01-07", "price": 600.0, "confirmed": False},
    ]


def test_identify_cycle_tops_no_drawdown_ever_reaches_threshold():
    # A steady climb with small pullbacks (never >=70% from any running high)
    # confirms nothing -- only the final running high is reported, provisional.
    rows = [_row(date(2020, 1, 1) + timedelta(days=i), 100.0 + i * 10 - (5 if i % 3 == 0 else 0)) for i in range(20)]

    tops = fit_models.identify_cycle_tops(rows, 0.70, 0.50)

    assert len(tops) == 1
    assert tops[0]["confirmed"] is False


def test_identify_cycle_tops_two_full_cycles_in_order():
    rows = []
    # Cycle 1: 100 -> 1000 -> 100 (90% drawdown, confirms), then recovers.
    for price in (100, 1000, 500, 200, 100):
        rows.append(_row(date(2020, 1, 1) + timedelta(days=len(rows)), float(price)))
    # Bottom confirmed once price is +50% off the 100 low (150+).
    for price in (160, 2000, 1000, 400, 160):
        rows.append(_row(date(2020, 1, 1) + timedelta(days=len(rows)), float(price)))

    tops = fit_models.identify_cycle_tops(rows, 0.70, 0.50)

    confirmed = [t for t in tops if t["confirmed"]]
    assert [t["price"] for t in confirmed] == [1000.0, 2000.0]
    # Chronological order, first cycle before second.
    assert confirmed[0]["date"] < confirmed[1]["date"]


def test_identify_cycle_tops_empty_series_returns_empty():
    assert fit_models.identify_cycle_tops([], 0.70, 0.50) == []


def test_compute_cycle_tops_unions_three_admission_rules_without_duplicating():
    # Trend pinned flat at $1 (a=0, b=0, sigma=1) so sigma_vs_trend reduces to
    # exactly log10(price) -- isolates compute_cycle_tops' era-admission
    # logic from the power-law fit itself, which the test above covers.
    genesis = date(2009, 1, 3)
    a, b, sigma = 0.0, 0.0, 1.0
    constants = {
        "power_law": {"cycle_top_drawdown_confirm_pct": 0.70, "cycle_top_recovery_confirm_pct": 0.50},
        "cycle_overlay": {"halving_dates": ["2018-01-01"]},
    }

    rows = [
        _row(date(2015, 1, 1), 10.0),
        _row(date(2015, 3, 1), 1000.0),  # era A's confirmed top (crashes 95% next)
        _row(date(2015, 5, 1), 50.0),  # confirms 2015-03-01 -> seeking bottom
        _row(date(2015, 6, 1), 100.0),  # +100% off the low -> confirms bottom, seeking top resumes
        _row(date(2016, 1, 1), 5000.0),  # era A's TRUE max-sigma day -- never itself crashes 70%, never confirmed
        _row(date(2016, 6, 1), 2000.0),  # -60% off 5000: not enough to confirm
        _row(date(2017, 6, 1), 3000.0),  # still below 5000, still not confirmed
        # -- 2018-01-01 halving boundary --
        _row(date(2019, 1, 1), 4000.0),  # era A's running high (5000) persists across the boundary
        _row(date(2021, 1, 1), 8000.0),  # new running high, era B
        _row(date(2022, 1, 1), 2000.0),  # -75% off 8000 -> confirms 2021-01-01
        _row(date(2022, 6, 1), 3200.0),  # +60% off the low -> confirms bottom
        _row(date(2023, 1, 1), 6000.0),  # new running high -- series ends here, unconfirmed
    ]

    tops = fit_models.compute_cycle_tops(rows, constants, genesis, a, b, sigma)
    by_date = {t["date"]: t for t in tops}

    assert set(by_date) == {"2015-03-01", "2016-01-01", "2021-01-01", "2023-01-01"}
    assert [t["date"] for t in tops] == sorted(by_date)  # output is date-sorted

    assert by_date["2015-03-01"]["kind"] == "confirmed_top"
    assert by_date["2015-03-01"]["confirmed"] is True
    assert by_date["2015-03-01"]["sigma_vs_trend"] == pytest.approx(math.log10(1000.0), abs=1e-4)

    # Era A's real peak-sigma day is a DIFFERENT date than its ZigZag-confirmed
    # top -- both ship, rather than the disagreement being resolved by fiat.
    assert by_date["2016-01-01"]["kind"] == "era_max_sigma"
    assert by_date["2016-01-01"]["confirmed"] is True  # era A is closed (a later halving already happened)
    assert by_date["2016-01-01"]["sigma_vs_trend"] == pytest.approx(math.log10(5000.0), abs=1e-4)

    # Era B's confirmed top is ALSO its max-sigma and max-price day -- one
    # entry, not three duplicates of the same date.
    assert by_date["2021-01-01"]["kind"] == "confirmed_top"

    assert by_date["2023-01-01"]["kind"] == "current_era_high"
    assert by_date["2023-01-01"]["confirmed"] is False
    assert by_date["2023-01-01"]["drawdown_so_far_pct"] == 0.0


def test_cycle_top_era_maxima_is_one_value_per_era_and_a_subset_of_cycle_tops():
    genesis = date(2009, 1, 3)
    a, b, sigma = 0.0, 0.0, 1.0
    constants = {
        "power_law": {"cycle_top_drawdown_confirm_pct": 0.70, "cycle_top_recovery_confirm_pct": 0.50},
        "cycle_overlay": {"halving_dates": ["2018-01-01"]},
    }
    rows = [
        _row(date(2015, 1, 1), 10.0),
        _row(date(2016, 1, 1), 5000.0),  # era A max (sigma = log10(5000))
        _row(date(2019, 1, 1), 4000.0),  # era B max so far (sigma = log10(4000))
    ]

    tops = fit_models.compute_cycle_tops(rows, constants, genesis, a, b, sigma)
    maxima = fit_models.cycle_top_era_maxima(rows, constants, genesis, a, b, sigma)

    # {date, sigma_vs_trend} pairs, not bare numbers -- site copy needs to
    # know WHEN each era's peak happened, and that date is not reliably
    # borrowed from any other entry (see the divergence test below).
    assert maxima == [
        {"date": "2016-01-01", "sigma_vs_trend": pytest.approx(math.log10(5000.0), abs=1e-4)},
        {"date": "2019-01-01", "sigma_vs_trend": pytest.approx(math.log10(4000.0), abs=1e-4)},
    ]
    # Every era maximum is a (date, value) pair some entry in cycle_tops
    # actually carries -- never computed independently of the admitted set.
    top_by_date = {t["date"]: t["sigma_vs_trend"] for t in tops}
    for m in maxima:
        assert m["sigma_vs_trend"] == pytest.approx(top_by_date[m["date"]], abs=1e-4)


def test_compute_cycle_tops_open_era_price_and_sigma_maxima_can_differ_and_both_get_drawdown():
    # A non-flat trend (b=1: trend price == day-number) so the open era's
    # peak-SIGMA day and peak-RAW-PRICE day can genuinely differ -- the same
    # shape the real committed history already has (2024-12-17 era_max_sigma
    # vs. 2025-10-07 current_era_high). Also arranges for ZigZag's own
    # unconfirmed running high to sit in an ALREADY-CLOSED era. Regression
    # for two bugs an Opus-5 audit (2026-07-26) found in an earlier version:
    # (1) an era-loop-admitted entry could have no drawdown_so_far_pct at
    # all, rendering "-undefined% so far" downstream; (2) a stale,
    # closed-era ZigZag high was unconditionally labeled "current_era_high"
    # regardless of which era it actually fell in.
    genesis = date(2009, 1, 3)
    a, b, sigma = 0.0, 1.0, 1.0  # trend price == day-number; sigma_vs_trend = log10(price/day)
    constants = {
        "power_law": {"cycle_top_drawdown_confirm_pct": 0.70, "cycle_top_recovery_confirm_pct": 0.50},
        "cycle_overlay": {"halving_dates": ["2015-01-01"]},
    }

    def day_of(d):
        return (d - genesis).days

    p1_date, p2_date = date(2016, 1, 1), date(2019, 1, 1)
    d1, d2 = day_of(p1_date), day_of(p2_date)
    p1_price = d1 * 8.0  # higher price/day ratio (sigma) ...
    p2_price = d2 * 6.0  # ... than this one, despite a higher raw price
    assert p2_price > p1_price and (p1_price / d1) > (p2_price / d2)  # sanity-check the construction

    rows = [
        _row(date(2009, 2, 1), 10.0),
        _row(date(2010, 1, 1), 100_000.0),  # becomes ZigZag's tracked running high
        _row(date(2010, 6, 1), 20_000.0),  # -80%: confirms 2010-01-01 as a top
        _row(date(2010, 7, 1), 35_000.0),  # +75% off the low: confirms the bottom, seeking-top resumes
        # -- 2015-01-01 halving boundary --
        # Neither point below ever exceeds 35,000 (ZigZag's tracked high
        # never moves off the pre-halving 2010-07-01) or falls below 10,500
        # (30% of 35,000 -- crossing that would confirm 2010-07-01 as a top).
        _row(p1_date, p1_price),
        _row(p2_date, p2_price),
    ]
    assert 10_500 < p1_price < 35_000 and 10_500 < p2_price < 35_000

    tops = fit_models.compute_cycle_tops(rows, constants, genesis, a, b, sigma)
    by_date = {t["date"]: t for t in tops}

    # ZigZag's own unconfirmed high is a stale, pre-halving date: "unconfirmed_top", not "current_era_high".
    assert by_date["2010-07-01"]["kind"] == "unconfirmed_top"
    assert by_date["2010-07-01"]["confirmed"] is False
    assert "drawdown_so_far_pct" in by_date["2010-07-01"]

    # The open era's peak-sigma day and peak-price day are genuinely different dates, both newly admitted.
    p1_entry, p2_entry = by_date[p1_date.isoformat()], by_date[p2_date.isoformat()]
    assert p1_entry["kind"] == "era_max_sigma"
    assert p2_entry["kind"] == "current_era_high"

    # Formerly the exact P1 bug: an era-loop-admitted entry with no
    # drawdown_so_far_pct at all. Both are present and numeric now.
    assert isinstance(p1_entry["drawdown_so_far_pct"], float)
    assert isinstance(p2_entry["drawdown_so_far_pct"], float)
    # p2 is the era's (and the whole series') own raw-price maximum, so
    # nothing "since" it could have risen further -- exactly 0, never negative.
    assert p2_entry["drawdown_so_far_pct"] == 0.0
    # p1 has a LOWER raw price than p2, which comes later in the same
    # series -- price has since risen past it, so its "drawdown" is negative
    # (a gain, not a fall). Scoring by sigma-vs-trend rather than raw price is
    # exactly why era_max_sigma can pick a point later ones out-earn in
    # dollar terms; the frontend must render this as "up X%", not "down -X%".
    assert p1_entry["drawdown_so_far_pct"] < 0


def test_fit_power_law_cycle_tops_are_scored_against_the_same_fit():
    a_true, b_true = -17.0, 5.8
    rows = []
    for i, d in enumerate(range(10, 3000)):
        noise = 0.01 * math.sin(i)  # tiny wobble: never enough on its own to hit a 70% drawdown
        price = 10 ** (a_true + b_true * math.log10(d) + noise)
        rows.append(_row(GENESIS + timedelta(days=d), price))

    # Overwrite two adjacent days with a deliberate spike-then-crash: the only
    # swing in the series big enough for the ZigZag detector (70%/50%
    # thresholds) to confirm, isolating it as the sole confirmed top.
    spike_i, crash_i = 1500, 1501
    spike_day = 10 + spike_i
    spike_price = 10 ** (a_true + b_true * math.log10(spike_day) + 1.0)  # 10x the true trend
    crash_day = 10 + crash_i
    crash_price = 10 ** (a_true + b_true * math.log10(crash_day) - 1.0)  # 0.1x the true trend
    rows[spike_i] = _row(GENESIS + timedelta(days=spike_day), spike_price)
    rows[crash_i] = _row(GENESIS + timedelta(days=crash_day), crash_price)

    result = fit_models.fit_power_law(rows, TEST_CONSTANTS, previous_models=None)
    confirmed = [t for t in result["cycle_tops"] if t["confirmed"]]

    # The spike is also, by a wide margin, the whole series' max-sigma day, so
    # the era_max_sigma rule agrees with the ZigZag rule instead of adding a
    # second confirmed entry -- proof the union dedupes rather than doubling
    # up when two admission rules point at the same date.
    assert len(confirmed) == 1
    assert confirmed[0]["kind"] == "confirmed_top"
    assert confirmed[0]["date"] == (GENESIS + timedelta(days=spike_day)).isoformat()
    assert confirmed[0]["price"] == pytest.approx(spike_price)
    assert "drawdown_so_far_pct" not in confirmed[0]
    # The spike sits ~1.0 above the TRUE trend in log10 space, and the fit's
    # own sigma stays small (dominated by the 0.01-amplitude noise elsewhere),
    # so scoring it against THIS fit's a/b/sigma should read as many sigma
    # above trend -- proof sigma_vs_trend uses the corridor's own fit, not a
    # separately-fit curve through the tops.
    assert confirmed[0]["sigma_vs_trend"] > 5


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


# --------------------------------------------------------------------------
# run_fit orchestration: carried_forward exclusion
# --------------------------------------------------------------------------


def test_run_fit_excludes_carried_forward_rows_from_every_model(tmp_path, monkeypatch):
    """A carried_forward row is a repeat of the last real observation written
    under a fresh date during an outage (fetch_snapshot.py) -- real for
    display, but not a genuine second data point. Appending one must change
    nothing about any of the four models run_fit() derives from price_series
    (power law, cycle overlay, Mayer, 200WMA)."""
    a_true, b_true = -17.0, 5.8
    series = _synthetic_power_law_series(a_true, b_true, 10, 500)

    price_path = tmp_path / "price_daily.json"
    constants_path = tmp_path / "model_constants.json"
    constants_path.write_text(json.dumps(TEST_CONSTANTS))
    monkeypatch.setattr(fit_models, "PRICE_HISTORY_PATH", price_path)
    monkeypatch.setattr(fit_models, "MODEL_CONSTANTS_PATH", constants_path)
    monkeypatch.setattr(fit_models, "MODELS_OUT_PATH", tmp_path / "models.json")

    def write_series(rows):
        price_path.write_text(json.dumps({
            "metric": "price_daily", "unit": "USD", "schema_version": 1,
            "generated_at": "2026-07-08T06:30:00Z", "series": rows,
        }))

    write_series(series)
    clean_result = fit_models.run_fit(dry_run=True)

    last = series[-1]
    carried_date = date.fromisoformat(last["date"]) + timedelta(days=1)
    carried_row = {**last, "date": carried_date.isoformat(), "carried_forward": True}
    write_series(series + [carried_row])
    result_with_carry = fit_models.run_fit(dry_run=True)

    assert result_with_carry["power_law"] == clean_result["power_law"]
    assert result_with_carry["cycle_overlay"] == clean_result["cycle_overlay"]
    assert result_with_carry["mayer_multiple"] == clean_result["mayer_multiple"]
    assert result_with_carry["wma_200"] == clean_result["wma_200"]
    # Sanity: the fixture actually exercises a non-trivial fit, so this isn't
    # vacuously true because both sides are empty.
    assert clean_result["power_law"]["params"]["n_points"] == len(series)
