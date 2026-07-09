import responses

from pipeline.sources import CoinMetricsClient


@responses.activate
def test_follows_pagination_and_concatenates_rows_in_order(load_fixture):
    page1 = load_fixture("coinmetrics_page1.json")
    page2 = load_fixture("coinmetrics_page2.json")

    responses.add(responses.GET, CoinMetricsClient.BASE_URL, json=page1, status=200)
    responses.add(
        responses.GET,
        "https://api.coinmetrics.io/v4/timeseries/asset-metrics?page=2",
        json=page2,
        status=200,
    )

    rows = CoinMetricsClient().fetch_asset_metrics(
        metrics=["PriceUSD", "HashRate", "SplyCur"], start_time="2010-01-01"
    )

    assert len(rows) == 3
    assert [row["time"][:10] for row in rows] == ["2010-07-17", "2010-07-18", "2010-07-19"]


@responses.activate
def test_single_page_response_has_no_next_page_url(load_fixture):
    page2 = load_fixture("coinmetrics_page2.json")
    responses.add(responses.GET, CoinMetricsClient.BASE_URL, json=page2, status=200)

    rows = CoinMetricsClient().fetch_asset_metrics(
        metrics=["PriceUSD", "HashRate", "SplyCur"], start_time="2010-01-01"
    )

    assert len(rows) == 1
