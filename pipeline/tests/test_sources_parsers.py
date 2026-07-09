from pipeline.sources import AlternativeMeFngClient, BlockchainInfoChartsClient, CoinMetricsClient


def test_split_metric_series_drops_nulls_and_normalizes(load_fixture):
    rows = load_fixture("coinmetrics_page1.json")["data"]

    price_series = CoinMetricsClient.split_metric_series(rows, "PriceUSD")
    assert price_series == [
        {"date": "2010-07-17", "value": 0.04951, "source": "coinmetrics"},
        {"date": "2010-07-18", "value": 0.0858, "source": "coinmetrics"},
    ]

    # Both fixture rows have HashRate: null -- must be dropped, not coerced to 0.
    assert CoinMetricsClient.split_metric_series(rows, "HashRate") == []


def test_blockchain_info_hashrate_converts_th_s_to_eh_s():
    values = [{"x": 1279324800, "y": 500_000}]  # 500,000 TH/s == 0.5 EH/s
    rows = BlockchainInfoChartsClient.parse_chart_values("hash-rate", values)
    assert rows[0]["value"] == 0.5
    assert rows[0]["date"] == "2010-07-17"
    assert rows[0]["source"] == "blockchain_info"


def test_parse_chart_values_dedupes_multiple_points_per_date_keeping_last():
    # total-bitcoins is natively per-block, not per-day, even with
    # sampled=false -- multiple points can land on the same UTC date.
    values = [
        {"x": 1231006505, "y": 50.0},  # 2009-01-03
        {"x": 1231010105, "y": 100.0},  # same date, later block
        {"x": 1231469744, "y": 150.0},  # next date
    ]
    rows = BlockchainInfoChartsClient.parse_chart_values("total-bitcoins", values)
    assert rows == [
        {"date": "2009-01-03", "value": 100.0, "source": "blockchain_info"},
        {"date": "2009-01-09", "value": 150.0, "source": "blockchain_info"},
    ]


def test_blockchain_info_market_price_has_no_unit_conversion():
    values = [{"x": 1279324800, "y": 0.05}]
    rows = BlockchainInfoChartsClient.parse_chart_values("market-price", values)
    assert rows[0]["value"] == 0.05


def test_alternative_me_parses_classification_and_date(load_fixture):
    payload = load_fixture("alternative_me_fng.json")
    rows = AlternativeMeFngClient.parse_fng_data(payload["data"])

    assert rows[0] == {
        "date": "2020-07-09",
        "value": 50,
        "classification": "Neutral",
        "source": "alternative_me",
    }
    assert all(isinstance(row["value"], int) for row in rows)
