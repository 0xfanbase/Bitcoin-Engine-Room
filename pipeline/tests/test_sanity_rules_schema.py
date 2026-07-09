import jsonschema


def test_sanity_rules_validates_against_schema(sanity_rules, load_schema):
    jsonschema.validate(sanity_rules, load_schema("sanity_rules"))


def test_both_profiles_present(sanity_rules):
    assert "live_snapshot" in sanity_rules
    assert "backfill_absolute" in sanity_rules

    for key in ("price_usd", "block_height", "hash_rate_eh_s", "difficulty", "supply_btc", "cross_source_variance"):
        assert key in sanity_rules["live_snapshot"], key

    for key in ("price_usd", "hash_rate_eh_s", "difficulty", "supply_btc"):
        assert key in sanity_rules["backfill_absolute"], key


def test_hashrate_cross_source_variance_widened(sanity_rules):
    # Director-reviewed correction: hash rate disagreement across providers is
    # legitimately large; the threshold must not be as tight as price's.
    variance = sanity_rules["live_snapshot"]["cross_source_variance"]
    assert variance["hashrate_pct"] > variance["price_pct"]
    assert variance["hashrate_pct"] >= 0.10


def test_backfill_price_min_allows_sub_dollar_history(sanity_rules):
    # BTC legitimately traded under $1 through 2010-2011 -- the backfill
    # profile must not reuse live_snapshot's price floor of 1000.
    assert sanity_rules["backfill_absolute"]["price_usd"]["min"] < 1
