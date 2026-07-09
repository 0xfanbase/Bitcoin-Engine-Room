from pipeline.subsidy import (
    block_reward_at_height,
    blocks_until_next_halving,
    estimate_supply_at_height,
)


def test_genesis_block_reward_is_50_btc():
    assert block_reward_at_height(0) == 50.0


def test_reward_halves_at_each_epoch_boundary():
    assert block_reward_at_height(209_999) == 50.0
    assert block_reward_at_height(210_000) == 25.0
    assert block_reward_at_height(420_000) == 12.5
    assert block_reward_at_height(630_000) == 6.25


def test_supply_after_first_epoch_matches_full_epoch_payout():
    # 210,000 blocks * 50 BTC = 10,500,000 BTC paid out through height 209,999
    assert estimate_supply_at_height(209_999) == 210_000 * 50.0


def test_supply_is_monotonically_increasing_with_height():
    assert estimate_supply_at_height(100) < estimate_supply_at_height(1000)
    assert estimate_supply_at_height(1000) < estimate_supply_at_height(300_000)


def test_supply_matches_real_observed_value_within_tolerance():
    # Live-verified 2026-07: blockchain.info /q/totalbc reported ~20,053,881 BTC
    # around tip height ~957,246. Small residual (unspent/burned coin dust,
    # slight height/observation-time mismatch) is expected and fine.
    estimated = estimate_supply_at_height(957_246)
    observed = 20_053_881.0
    assert abs(estimated - observed) / observed < 0.001


def test_blocks_until_next_halving_counts_down_to_pinned_height():
    assert blocks_until_next_halving(1_049_000) == 1000
    assert blocks_until_next_halving(1_050_000) == 0
    assert blocks_until_next_halving(1_060_000) == 0  # never negative
