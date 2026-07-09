from pipeline.backfill import merge_series


def test_merge_fills_only_gap_dates_and_keeps_source_tags():
    primary = [
        {"date": "2020-01-01", "value": 100.0, "source": "coinmetrics"},
        {"date": "2020-01-03", "value": 102.0, "source": "coinmetrics"},
    ]
    fallback = [
        {"date": "2020-01-01", "value": 999.0, "source": "blockchain_info"},  # must NOT override primary
        {"date": "2020-01-02", "value": 101.0, "source": "blockchain_info"},  # gap-fill
        {"date": "2020-01-03", "value": 999.0, "source": "blockchain_info"},  # must NOT override primary
        {"date": "2020-01-04", "value": 103.0, "source": "blockchain_info"},  # trailing gap-fill
    ]

    merged = merge_series(primary, fallback)

    assert merged == [
        {"date": "2020-01-01", "value": 100.0, "source": "coinmetrics"},
        {"date": "2020-01-02", "value": 101.0, "source": "blockchain_info"},
        {"date": "2020-01-03", "value": 102.0, "source": "coinmetrics"},
        {"date": "2020-01-04", "value": 103.0, "source": "blockchain_info"},
    ]


def test_merge_with_empty_fallback_returns_primary_sorted():
    primary = [
        {"date": "2020-01-02", "value": 2.0, "source": "coinmetrics"},
        {"date": "2020-01-01", "value": 1.0, "source": "coinmetrics"},
    ]
    assert merge_series(primary, []) == [
        {"date": "2020-01-01", "value": 1.0, "source": "coinmetrics"},
        {"date": "2020-01-02", "value": 2.0, "source": "coinmetrics"},
    ]
