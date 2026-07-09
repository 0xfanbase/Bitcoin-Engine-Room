"""Bitcoin block-subsidy math: pure functions, no I/O.

Used as a last-resort supply fallback (spec Section 4: "computed from height
(subsidy schedule)") when both Coin Metrics and blockchain.info are down, and
for the halving countdown. Kept dependency-free so it can be unit tested
without touching the network.
"""

from __future__ import annotations

GENESIS_REWARD_BTC = 50.0
HALVING_INTERVAL_BLOCKS = 210_000
NEXT_HALVING_HEIGHT = 1_050_000  # per spec Section 4 / model_constants.json cycle_overlay


def block_reward_at_height(height: int) -> float:
    """Subsidy paid at `height`, in BTC."""
    epoch = height // HALVING_INTERVAL_BLOCKS
    return GENESIS_REWARD_BTC / (2**epoch)


def estimate_supply_at_height(height: int) -> float:
    """Total circulating supply through `height` (inclusive), in BTC.

    Height is 0-indexed (the genesis block is height 0), so `height` blocks
    have been mined *before* this one -- `height + 1` total blocks including
    it. Closed form: full halved epochs contribute 210,000 * reward_at_epoch
    each; the current partial epoch contributes its block count times its
    reward.
    """
    blocks_mined = height + 1
    full_epochs = blocks_mined // HALVING_INTERVAL_BLOCKS
    remainder_blocks = blocks_mined % HALVING_INTERVAL_BLOCKS

    supply = 0.0
    for epoch in range(full_epochs):
        supply += HALVING_INTERVAL_BLOCKS * (GENESIS_REWARD_BTC / (2**epoch))
    supply += remainder_blocks * (GENESIS_REWARD_BTC / (2**full_epochs))

    return supply


def blocks_until_next_halving(height: int) -> int:
    return max(NEXT_HALVING_HEIGHT - height, 0)
