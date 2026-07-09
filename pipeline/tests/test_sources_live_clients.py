import responses

from pipeline.sources import (
    BlockchainInfoSimpleClient,
    CoinbaseClient,
    CoinGeckoClient,
    MempoolSpaceClient,
)


@responses.activate
def test_mempool_space_fetch_price():
    responses.add(
        responses.GET,
        "https://mempool.space/api/v1/prices",
        json={"time": 1783533250, "USD": 62142, "EUR": 54420},
        status=200,
    )
    row = MempoolSpaceClient().fetch_price()
    assert row["value"] == 62142.0
    assert row["source"] == "mempool_space"
    assert row["date"]  # today's date, not asserting exact value (real clock)


@responses.activate
def test_mempool_space_fetch_hashrate_converts_h_s_to_eh_s():
    responses.add(
        responses.GET,
        "https://mempool.space/api/v1/mining/hashrate/3d",
        json={"hashrates": [], "currentHashrate": 908_318_331_912_316_100_000, "currentDifficulty": 1.0},
        status=200,
    )
    row = MempoolSpaceClient().fetch_hashrate()
    assert abs(row["value"] - 908.318331912316) < 1e-6
    assert row["source"] == "mempool_space"


@responses.activate
def test_mempool_space_fetch_difficulty():
    # currentDifficulty lives in the hashrate/3d payload, not difficulty-adjustment
    # (verified against the real API -- see the comment in sources.py).
    responses.add(
        responses.GET,
        "https://mempool.space/api/v1/mining/hashrate/3d",
        json={"hashrates": [], "currentHashrate": 9e20, "currentDifficulty": 133869853540305.4},
        status=200,
    )
    row = MempoolSpaceClient().fetch_difficulty()
    assert row["value"] == 133869853540305.4
    assert row["source"] == "mempool_space"


@responses.activate
def test_mempool_space_fetch_tip_height_parses_plain_text():
    responses.add(responses.GET, "https://mempool.space/api/blocks/tip/height", body="957246", status=200)
    assert MempoolSpaceClient().fetch_tip_height() == 957246


@responses.activate
def test_coingecko_fetch_price():
    responses.add(
        responses.GET,
        "https://api.coingecko.com/api/v3/simple/price",
        json={"bitcoin": {"usd": 61910}},
        status=200,
    )
    row = CoinGeckoClient().fetch_price()
    assert row["value"] == 61910.0
    assert row["source"] == "coingecko"


@responses.activate
def test_coinbase_fetch_price():
    responses.add(
        responses.GET,
        "https://api.coinbase.com/v2/prices/BTC-USD/spot",
        json={"data": {"amount": "61896.685", "base": "BTC", "currency": "USD"}},
        status=200,
    )
    row = CoinbaseClient().fetch_price()
    assert row["value"] == 61896.685
    assert row["source"] == "coinbase"


@responses.activate
def test_blockchain_info_simple_difficulty_parses_scientific_notation_text():
    responses.add(
        responses.GET, "https://blockchain.info/q/getdifficulty", body="1.33869853540305E14", status=200
    )
    row = BlockchainInfoSimpleClient().fetch_difficulty()
    assert row["value"] == 1.33869853540305e14
    assert row["source"] == "blockchain_info"


@responses.activate
def test_blockchain_info_simple_total_supply_converts_satoshis_to_btc():
    responses.add(responses.GET, "https://blockchain.info/q/totalbc", body="2005388100000000", status=200)
    row = BlockchainInfoSimpleClient().fetch_total_supply()
    assert row["value"] == 20053881.0
    assert row["source"] == "blockchain_info"
