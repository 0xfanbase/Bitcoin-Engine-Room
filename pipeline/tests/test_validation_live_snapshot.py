from pipeline.validation import (
    check_difficulty_live,
    check_hashrate_live,
    check_price_live,
    check_supply_live,
)

PRICE_RULES = {"min": 1000, "max": 10_000_000, "max_day_over_day_pct_change": 0.40}


def test_price_within_bounds_and_change_passes():
    assert check_price_live(62000, 61000, PRICE_RULES) == []


def test_price_below_min_bound_flagged():
    # No prev_value, to isolate the bound check from the day-over-day check.
    violations = check_price_live(500, None, PRICE_RULES)
    assert len(violations) == 1
    assert "outside" in violations[0]


def test_price_day_over_day_spike_flagged():
    violations = check_price_live(90000, 61000, PRICE_RULES)  # +47%
    assert any("day-over-day" in v for v in violations)


def test_price_no_prev_value_skips_day_over_day_check():
    assert check_price_live(62000, None, PRICE_RULES) == []


HASHRATE_RULES = {"max_pct_dev_from_trailing_30d_median": 0.50}


def test_hashrate_within_trailing_median_passes():
    history = [{"date": f"2026-06-{d:02d}", "value": 900.0, "source": "x"} for d in range(1, 11)]
    assert check_hashrate_live(920.0, history, HASHRATE_RULES) == []


def test_hashrate_deviates_beyond_threshold_flagged():
    history = [{"date": f"2026-06-{d:02d}", "value": 900.0, "source": "x"} for d in range(1, 11)]
    violations = check_hashrate_live(2000.0, history, HASHRATE_RULES)
    assert len(violations) == 1


def test_hashrate_no_history_skips_check():
    assert check_hashrate_live(1.0, [], HASHRATE_RULES) == []


DIFFICULTY_RULES = {"max_pct_change_per_retarget": 0.30}


def test_difficulty_unchanged_always_passes():
    assert check_difficulty_live(1.0e14, 1.0e14, DIFFICULTY_RULES) == []


def test_difficulty_normal_retarget_change_passes():
    assert check_difficulty_live(1.05e14, 1.0e14, DIFFICULTY_RULES) == []  # +5%


def test_difficulty_extreme_change_flagged():
    violations = check_difficulty_live(2.0e14, 1.0e14, DIFFICULTY_RULES)  # +100%
    assert len(violations) == 1


SUPPLY_RULES = {"max_btc": 21_000_000, "max_pct_dev_from_subsidy_schedule": 0.001}


def test_supply_increase_within_subsidy_estimate_passes():
    assert check_supply_live(20_000_100, 20_000_000, SUPPLY_RULES, estimated_from_height=20_000_100) == []


def test_supply_decrease_flagged():
    violations = check_supply_live(19_999_000, 20_000_000, SUPPLY_RULES, estimated_from_height=19_999_000)
    assert any("decreased" in v for v in violations)


def test_supply_deviates_from_subsidy_estimate_flagged():
    violations = check_supply_live(20_000_000, 19_999_000, SUPPLY_RULES, estimated_from_height=15_000_000)
    assert any("subsidy-schedule" in v for v in violations)
