"""Shared sanity-bound and cross-source-variance checks.

Used by backfill.py (P1), fetch_snapshot.py (P2), and audit.py (P5) so this
logic exists in exactly one place rather than three.
"""

from __future__ import annotations

import statistics

METRIC_TO_SANITY_KEY = {
    "price_daily": "price_usd",
    "hashrate_daily": "hash_rate_eh_s",
    "difficulty_daily": "difficulty",
    "supply_daily": "supply_btc",
}


def check_ascending_no_duplicate_dates(series: list[dict]) -> list[str]:
    """Return violation descriptions; an empty list means the series is clean."""
    violations = []
    prev_date = None
    for row in series:
        date = row["date"]
        if prev_date is not None:
            if date == prev_date:
                violations.append(f"duplicate date {date}")
            elif date < prev_date:
                violations.append(f"date {date} out of order after {prev_date}")
        prev_date = date
    return violations


def check_backfill_sanity(metric: str, series: list[dict], backfill_rules: dict) -> list[dict]:
    """Validate a backfilled series against sanity_rules.json's backfill_absolute profile.

    `backfill_rules` is that profile's full dict (keyed by sanity-rule metric
    name, e.g. "price_usd"). Returns a list of violation dicts.
    """
    sanity_key = METRIC_TO_SANITY_KEY.get(metric)
    if sanity_key is None or sanity_key not in backfill_rules:
        return []

    rules = backfill_rules[sanity_key]
    violations: list[dict] = []

    bound_min = rules.get("min")
    bound_max = rules.get("max")
    for row in series:
        value = row["value"]
        if bound_min is not None and value < bound_min:
            violations.append(
                {"date": row["date"], "value": value, "rule": "min", "detail": f"< {bound_min}"}
            )
        if bound_max is not None and value > bound_max:
            violations.append(
                {"date": row["date"], "value": value, "rule": "max", "detail": f"> {bound_max}"}
            )

    if rules.get("monotonic_non_decreasing"):
        violations.extend(_check_monotonic_non_decreasing(series))

    if rules.get("series_is_sparse_step_function"):
        violations.extend(
            _check_step_function_change(series, rules.get("max_pct_change_between_distinct_values"))
        )

    return violations


def _check_monotonic_non_decreasing(series: list[dict]) -> list[dict]:
    violations = []
    prev_value = None
    for row in series:
        if prev_value is not None and row["value"] < prev_value:
            violations.append(
                {
                    "date": row["date"],
                    "value": row["value"],
                    "rule": "monotonic_non_decreasing",
                    "detail": f"dropped from {prev_value}",
                }
            )
        prev_value = row["value"]
    return violations


def _check_step_function_change(series: list[dict], max_pct_change) -> list[dict]:
    if max_pct_change is None:
        return []
    violations = []
    prev_value = None
    for row in series:
        if prev_value is not None and prev_value != 0:
            pct_change = abs(row["value"] - prev_value) / prev_value
            if pct_change > max_pct_change:
                violations.append(
                    {
                        "date": row["date"],
                        "value": row["value"],
                        "rule": "max_pct_change_between_distinct_values",
                        "detail": f"{pct_change:.2%} change from {prev_value}",
                    }
                )
        prev_value = row["value"]
    return violations


def check_cross_source_variance(value_a: float, value_b: float, max_pct: float) -> bool:
    """Return True if value_a and value_b agree within max_pct of each other."""
    if value_a == 0 and value_b == 0:
        return True
    baseline = max(abs(value_a), abs(value_b))
    return abs(value_a - value_b) / baseline <= max_pct


# --- live_snapshot checks (P2, fetch_snapshot.py) -------------------------
# Each returns a list of violation strings; empty means the candidate value
# passes and its source should be accepted. A non-empty list means
# fetch_snapshot.py should reject this source's value and try the next one
# in the chain (spec Section 6: "if not passes_sanity(...): raise SanityError").


def check_price_live(value: float, prev_value: float | None, rules: dict) -> list[str]:
    violations = []
    if value < rules["min"] or value > rules["max"]:
        violations.append(f"{value} outside [{rules['min']}, {rules['max']}]")
    if prev_value is not None and prev_value != 0:
        pct = abs(value - prev_value) / prev_value
        if pct > rules["max_day_over_day_pct_change"]:
            violations.append(f"day-over-day change {pct:.1%} exceeds {rules['max_day_over_day_pct_change']:.0%}")
    return violations


def check_hashrate_live(value: float, history_series: list[dict], rules: dict) -> list[str]:
    trailing = [row["value"] for row in history_series[-30:]]
    if not trailing:
        return []  # no history yet to compare against
    median = statistics.median(trailing)
    if median == 0:
        return []
    deviation = abs(value - median) / median
    if deviation > rules["max_pct_dev_from_trailing_30d_median"]:
        return [f"deviates {deviation:.1%} from trailing 30-day median {median}"]
    return []


def check_difficulty_live(value: float, prev_value: float | None, rules: dict) -> list[str]:
    if prev_value is None or value == prev_value:
        return []  # most days: no retarget, value unchanged -- always fine
    pct = abs(value - prev_value) / prev_value
    if pct > rules["max_pct_change_per_retarget"]:
        return [f"retarget change {pct:.1%} exceeds {rules['max_pct_change_per_retarget']:.0%}"]
    return []


def check_supply_live(
    value: float, prev_value: float | None, rules: dict, *, estimated_from_height: float | None = None
) -> list[str]:
    violations = []
    if prev_value is not None and value < prev_value:
        violations.append(f"supply decreased from {prev_value} to {value}")
    if value > rules["max_btc"]:
        violations.append(f"{value} exceeds max supply {rules['max_btc']}")
    if estimated_from_height is not None and estimated_from_height != 0:
        deviation = abs(value - estimated_from_height) / estimated_from_height
        if deviation > rules["max_pct_dev_from_subsidy_schedule"]:
            violations.append(
                f"deviates {deviation:.4%} from subsidy-schedule estimate {estimated_from_height}"
            )
    return violations
