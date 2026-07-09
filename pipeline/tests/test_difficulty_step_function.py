from pipeline.validation import check_ascending_no_duplicate_dates, check_backfill_sanity

BACKFILL_RULES = {
    "difficulty": {
        "min": 1,
        "max": None,
        "series_is_sparse_step_function": True,
        "max_pct_change_between_distinct_values": 0.30,
    }
}


def test_clean_sparse_step_series_has_no_violations():
    series = [
        {"date": "2009-01-03", "value": 1.0, "source": "blockchain_info"},
        {"date": "2009-01-17", "value": 1.18, "source": "blockchain_info"},  # ~18% change, within 30%
        {"date": "2009-12-30", "value": 1.3, "source": "blockchain_info"},
    ]
    assert check_backfill_sanity("difficulty_daily", series, BACKFILL_RULES) == []


def test_step_change_beyond_threshold_is_flagged():
    series = [
        {"date": "2009-01-03", "value": 1.0, "source": "blockchain_info"},
        {"date": "2009-01-17", "value": 2.0, "source": "blockchain_info"},  # 100% change -- should flag
    ]
    violations = check_backfill_sanity("difficulty_daily", series, BACKFILL_RULES)
    assert len(violations) == 1
    assert violations[0]["rule"] == "max_pct_change_between_distinct_values"


def test_sparse_dates_do_not_trigger_a_continuity_violation():
    # Months between rows is legitimate for a step-function series -- this
    # must not be treated as a missing-date/continuity problem.
    series = [
        {"date": "2009-01-03", "value": 1.0, "source": "blockchain_info"},
        {"date": "2009-12-30", "value": 1.3, "source": "blockchain_info"},
    ]
    assert check_ascending_no_duplicate_dates(series) == []
