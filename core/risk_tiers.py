"""
Tier × category allocation matrix for the crypto ETF universe.

Inputs:
  - Tier name (one of the 5 names in config.PORTFOLIO_TIERS)
  - ETF categories defined on the universe: "btc_spot", "eth_spot",
    "btc_futures", "thematic" (reserved for Solana / multi-asset ETFs
    when those land on US exchanges).

Output:
  - Per-tier dict {category: weight_pct} summing to 100.

Philosophy:
  Tier 1 — maximum BTC concentration (low-beta), only the lowest-expense
           issuers. No ETH exposure at all.
  Tier 2 — BTC dominant, small ETH allocation starts.
  Tier 3 — balanced BTC/ETH.
  Tier 4 — BTC + ETH + small thematic bucket (when available).
  Tier 5 — max diversification including futures-based and thematic.

  These are fractions of the CRYPTO SLEEVE, not total portfolio. The FA
  decides total-portfolio crypto sizing via the `ceiling_pct` in
  config.PORTFOLIO_TIERS.

CLAUDE.md §13.
"""
from __future__ import annotations


# category keys must match the `category` field on each entry in
# config.ETF_UNIVERSE_SEED.
TIER_CATEGORY_ALLOCATIONS: dict[str, dict[str, float]] = {
    "Ultra Conservative": {
        "btc_spot":              100.0,
    },
    "Conservative": {
        "btc_spot":               85.0,
        "eth_spot":               15.0,
    },
    "Moderate": {
        "btc_spot":               60.0,
        "eth_spot":               30.0,
        "income_covered_call":    10.0,
    },
    "Aggressive": {
        "btc_spot":               50.0,
        "eth_spot":               30.0,
        "altcoin_spot":           10.0,
        "income_covered_call":    10.0,
    },
    "Ultra Aggressive": {
        "btc_spot":               35.0,
        "eth_spot":               25.0,
        "altcoin_spot":           15.0,
        "thematic_equity":        10.0,
        "leveraged":               5.0,
        "income_covered_call":    10.0,
    },
}

# Categories we will NEVER auto-allocate to in any risk tier. Inverse /
# short products have asymmetric risk profiles that don't fit model
# portfolio construction. Futures-based products are redundant with
# spot post-approval (spot always cheaper + lower tracking error).
EXCLUDED_CATEGORIES: frozenset[str] = frozenset({
    "inverse", "short", "btc_futures", "eth_futures",
})

# Maximum single-ETF weight (diversification cap), per CLAUDE.md §13 /
# rwa-infinity-model convention.
MAX_SINGLE_POSITION_PCT: float = 30.0

# Maximum number of ETFs selected per category, to avoid dust positions.
MAX_ETFS_PER_CATEGORY: int = 3


def allocation_for_tier(tier_name: str) -> dict[str, float]:
    """Return the category-allocation dict for a tier. Raises on unknown name."""
    if tier_name not in TIER_CATEGORY_ALLOCATIONS:
        raise ValueError(
            f"Unknown tier: {tier_name!r}. "
            f"Expected one of {list(TIER_CATEGORY_ALLOCATIONS)}."
        )
    return dict(TIER_CATEGORY_ALLOCATIONS[tier_name])
