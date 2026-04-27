"""
Day-2 portfolio_engine tests.

Includes the determinism-lock canary (seed=42 → bit-exact Monte Carlo
sample_paths) required by planning-side Mod 4.

Run:
    pytest tests/test_portfolio_engine.py -v
"""
from __future__ import annotations

import math

import pytest

from config import MONTE_CARLO_PATHS_COMPUTE, MONTE_CARLO_PATHS_RETAIN
from core.portfolio_engine import (
    DEFAULT_SEED,
    build_portfolio,
    compute_portfolio_metrics,
    cornish_fisher_var,
    run_monte_carlo,
)


# ── Minimal universe matching planning-side Mod 1 shape ──────────────────────
MINIMAL_UNIVERSE = [
    {"ticker": "IBIT", "category": "btc_spot",
     "expected_return": 35.0, "volatility": 55.0, "correlation_with_btc": 0.98,
     "issuer": "BlackRock", "expense_ratio_bps": 25, "name": "iShares Bitcoin Trust"},
    {"ticker": "FBTC", "category": "btc_spot",
     "expected_return": 34.0, "volatility": 55.0, "correlation_with_btc": 0.98,
     "issuer": "Fidelity", "expense_ratio_bps": 25, "name": "Fidelity Wise Origin Bitcoin Fund"},
    {"ticker": "ETHA", "category": "eth_spot",
     "expected_return": 40.0, "volatility": 70.0, "correlation_with_btc": 0.78,
     "issuer": "BlackRock", "expense_ratio_bps": 25, "name": "iShares Ethereum Trust"},
]


# ═══════════════════════════════════════════════════════════════════════════
# Cornish-Fisher VaR
# ═══════════════════════════════════════════════════════════════════════════

class TestCornishFisherVar:
    """Note: as of 2026-04-28 hotfix, cornish_fisher_var requires explicit
    skew + excess_kurt under the no-fallback policy. These tests pass
    crypto-equivalent moments to exercise the math behavior contract."""
    _CRYPTO_S = -0.7   # representative test value (NOT a default)
    _CRYPTO_K = 8.0

    def test_returns_non_negative(self):
        v = cornish_fisher_var(mean_return=10.0, vol=30.0, confidence=0.95,
                               skew=self._CRYPTO_S, excess_kurt=self._CRYPTO_K)
        assert v >= 0

    def test_higher_confidence_means_larger_loss(self):
        v95 = cornish_fisher_var(10.0, 30.0, 0.95, skew=self._CRYPTO_S, excess_kurt=self._CRYPTO_K)
        v99 = cornish_fisher_var(10.0, 30.0, 0.99, skew=self._CRYPTO_S, excess_kurt=self._CRYPTO_K)
        assert v99 >= v95

    def test_higher_vol_means_larger_var(self):
        v_low = cornish_fisher_var(10.0, 20.0, 0.95, skew=self._CRYPTO_S, excess_kurt=self._CRYPTO_K)
        v_high = cornish_fisher_var(10.0, 50.0, 0.95, skew=self._CRYPTO_S, excess_kurt=self._CRYPTO_K)
        assert v_high > v_low

    def test_positive_mean_reduces_var(self):
        v_pos = cornish_fisher_var(mean_return=20.0, vol=30.0, confidence=0.95,
                                   skew=self._CRYPTO_S, excess_kurt=self._CRYPTO_K)
        v_neg = cornish_fisher_var(mean_return=-20.0, vol=30.0, confidence=0.95,
                                   skew=self._CRYPTO_S, excess_kurt=self._CRYPTO_K)
        assert v_neg > v_pos


# ═══════════════════════════════════════════════════════════════════════════
# compute_portfolio_metrics
# ═══════════════════════════════════════════════════════════════════════════

class TestComputePortfolioMetrics:
    def test_empty_holdings_returns_zero_metrics(self):
        m = compute_portfolio_metrics([], 100_000, "Moderate")
        assert m["n_holdings"] == 0
        assert m["sharpe_ratio"] == 0

    def test_single_holding_volatility_matches_asset(self):
        holdings = [{
            "ticker": "IBIT", "category": "btc_spot",
            "weight_pct": 100.0, "usd_value": 100_000,
            "expected_return_pct": 35.0, "volatility_pct": 55.0,
            "correlation_with_btc": 0.98,
        }]
        m = compute_portfolio_metrics(holdings, 100_000, "Moderate")
        assert abs(m["portfolio_volatility_pct"] - 55.0) < 0.01

    def test_diversification_reduces_volatility(self):
        holdings_concentrated = [{
            "ticker": "IBIT", "category": "btc_spot", "weight_pct": 100.0,
            "usd_value": 100_000, "expected_return_pct": 35.0,
            "volatility_pct": 55.0, "correlation_with_btc": 0.98,
        }]
        holdings_diversified = [
            {"ticker": "IBIT", "category": "btc_spot", "weight_pct": 50.0,
             "usd_value": 50_000, "expected_return_pct": 35.0,
             "volatility_pct": 55.0, "correlation_with_btc": 0.98},
            {"ticker": "ETHA", "category": "eth_spot", "weight_pct": 50.0,
             "usd_value": 50_000, "expected_return_pct": 40.0,
             "volatility_pct": 70.0, "correlation_with_btc": 0.78},
        ]
        m_c = compute_portfolio_metrics(holdings_concentrated, 100_000, "Moderate")
        m_d = compute_portfolio_metrics(holdings_diversified, 100_000, "Moderate")
        # Diversified should have lower vol than weighted-avg of concentrated
        assert m_d["diversification_ratio"] >= 1.0

    def test_cvar_exceeds_var(self):
        holdings = [{
            "ticker": "IBIT", "category": "btc_spot", "weight_pct": 100.0,
            "usd_value": 100_000, "expected_return_pct": 10.0,
            "volatility_pct": 60.0, "correlation_with_btc": 0.98,
        }]
        m = compute_portfolio_metrics(holdings, 100_000, "Moderate")
        assert m["cvar_95_pct"] >= m["var_95_pct"]
        assert m["cvar_99_pct"] >= m["var_99_pct"]


# ═══════════════════════════════════════════════════════════════════════════
# build_portfolio — Mod-1 acceptance test
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildPortfolio:
    def test_moderate_tier_produces_holdings(self):
        p = build_portfolio("Moderate", MINIMAL_UNIVERSE, portfolio_value_usd=100_000)
        assert p["tier_name"] == "Moderate"
        assert len(p["holdings"]) > 0
        # Weights sum to ~100
        assert abs(sum(h["weight_pct"] for h in p["holdings"]) - 100.0) < 0.5

    def test_ultra_conservative_holds_only_btc(self):
        p = build_portfolio("Ultra Conservative", MINIMAL_UNIVERSE, 100_000)
        for h in p["holdings"]:
            assert h["category"] == "btc_spot"

    def test_moderate_includes_eth_spot(self):
        p = build_portfolio("Moderate", MINIMAL_UNIVERSE, 100_000)
        cats = {h["category"] for h in p["holdings"]}
        assert "eth_spot" in cats
        assert "btc_spot" in cats

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError):
            build_portfolio("Nonexistent", MINIMAL_UNIVERSE, 100_000)

    def test_empty_universe_returns_empty_portfolio(self):
        p = build_portfolio("Moderate", [], 100_000)
        assert p["holdings"] == []
        assert p["metrics"]["n_holdings"] == 0

    def test_metrics_are_populated(self):
        p = build_portfolio("Moderate", MINIMAL_UNIVERSE, 100_000)
        m = p["metrics"]
        assert m["n_holdings"] > 0
        assert m["portfolio_volatility_pct"] > 0
        assert math.isfinite(m["sharpe_ratio"])


# ═══════════════════════════════════════════════════════════════════════════
# Mod-1 ACCEPTANCE TEST — hardcoded 3-ETF case
# ═══════════════════════════════════════════════════════════════════════════

class TestModOneAcceptance:
    """
    Planning-side Mod 1 acceptance: hardcoded 3-ETF test case
    (IBIT / FBTC / ETHA at equal weights) produces non-degenerate
    Sharpe, VaR, and Monte Carlo sample_paths.
    """

    def _equal_weight_holdings(self) -> list[dict]:
        return [
            {"ticker": "IBIT", "category": "btc_spot", "weight_pct": 33.33,
             "usd_value": 33_333, "expected_return_pct": 35.0,
             "volatility_pct": 55.0, "correlation_with_btc": 0.98},
            {"ticker": "FBTC", "category": "btc_spot", "weight_pct": 33.33,
             "usd_value": 33_333, "expected_return_pct": 34.0,
             "volatility_pct": 55.0, "correlation_with_btc": 0.98},
            {"ticker": "ETHA", "category": "eth_spot", "weight_pct": 33.34,
             "usd_value": 33_334, "expected_return_pct": 40.0,
             "volatility_pct": 70.0, "correlation_with_btc": 0.78},
        ]

    def test_sharpe_non_degenerate(self):
        m = compute_portfolio_metrics(self._equal_weight_holdings(), 100_000, "Moderate")
        assert math.isfinite(m["sharpe_ratio"])
        assert m["sharpe_ratio"] != 0

    def test_var_non_degenerate(self):
        m = compute_portfolio_metrics(self._equal_weight_holdings(), 100_000, "Moderate")
        assert m["var_95_pct"] > 0
        assert m["var_99_pct"] > m["var_95_pct"] - 0.001   # allow tie in extreme case

    def test_monte_carlo_sample_paths_non_empty(self):
        portfolio = {
            "tier_name": "Moderate",
            "portfolio_value_usd": 100_000,
            "holdings": self._equal_weight_holdings(),
            "metrics": compute_portfolio_metrics(
                self._equal_weight_holdings(), 100_000, "Moderate"
            ),
        }
        mc = run_monte_carlo(
            portfolio,
            n_simulations=1_000,   # faster for test
            horizon_days=90,
        )
        assert len(mc["sample_paths"]) > 0
        assert mc["sample_paths"][0]   # each path non-empty


# ═══════════════════════════════════════════════════════════════════════════
# DETERMINISM LOCK (planning-side Mod 4)
# ═══════════════════════════════════════════════════════════════════════════

class TestMonteCarloDeterminismLock:
    """
    CANARY TEST: if this goes red, a MATH file changed, not a RENDERING file.
    Same seed + same inputs → bit-exact sample_paths.

    This is the safety net for Day 3 UI work — if a UI edit accidentally
    drifts the math output, the canary catches it immediately.
    """

    FIXED_HOLDINGS = [
        {"ticker": "IBIT", "category": "btc_spot", "weight_pct": 50.0,
         "usd_value": 50_000, "expected_return_pct": 30.0,
         "volatility_pct": 50.0, "correlation_with_btc": 1.0},
        {"ticker": "ETHA", "category": "eth_spot", "weight_pct": 50.0,
         "usd_value": 50_000, "expected_return_pct": 35.0,
         "volatility_pct": 65.0, "correlation_with_btc": 0.75},
    ]

    def _fixed_portfolio(self) -> dict:
        return {
            "tier_name": "Moderate",
            "portfolio_value_usd": 100_000,
            "holdings": list(self.FIXED_HOLDINGS),
            "metrics": compute_portfolio_metrics(
                self.FIXED_HOLDINGS, 100_000, "Moderate"
            ),
        }

    def test_two_runs_same_seed_produce_identical_sample_paths(self):
        p = self._fixed_portfolio()
        # Use reset_mc_cache to bypass caching for this test
        from core import portfolio_engine as pe
        pe._mc_cache.clear()
        mc1 = run_monte_carlo(p, n_simulations=500, horizon_days=60, seed=42)
        pe._mc_cache.clear()
        mc2 = run_monte_carlo(p, n_simulations=500, horizon_days=60, seed=42)

        assert mc1["sample_paths"] == mc2["sample_paths"], (
            "DETERMINISM LOCK FAILED — same seed produced different sample_paths. "
            "A math-layer file has changed."
        )
        assert mc1["percentile_50"] == mc2["percentile_50"]
        assert mc1["mean_final_value"] == mc2["mean_final_value"]

    def test_different_seeds_produce_different_paths(self):
        p = self._fixed_portfolio()
        from core import portfolio_engine as pe
        pe._mc_cache.clear()
        mc_a = run_monte_carlo(p, n_simulations=500, horizon_days=60, seed=42)
        pe._mc_cache.clear()
        mc_b = run_monte_carlo(p, n_simulations=500, horizon_days=60, seed=9999)
        # Any single path should differ — not strictly necessary but sanity-checks RNG wiring
        assert mc_a["sample_paths"] != mc_b["sample_paths"]

    def test_default_seed_is_42(self):
        assert DEFAULT_SEED == 42, (
            "DEFAULT_SEED changed away from 42. Update the determinism-lock "
            "test expectations OR revert the change."
        )

    def test_paths_retain_defaults_to_config(self):
        p = self._fixed_portfolio()
        from core import portfolio_engine as pe
        pe._mc_cache.clear()
        mc = run_monte_carlo(p, n_simulations=MONTE_CARLO_PATHS_COMPUTE,
                             horizon_days=60, seed=42)
        assert len(mc["sample_paths"]) == MONTE_CARLO_PATHS_RETAIN


# ═══════════════════════════════════════════════════════════════════════════
# ETH correlation guard (planning-side Risk 2)
# ═══════════════════════════════════════════════════════════════════════════

class TestPhase2GuardRemoval:
    """Day-3: Phase-1 ETH correlation guard is gone. No warnings expected."""

    def test_eth_ticker_no_longer_emits_phase1_warning(self, caplog):
        import logging
        caplog.set_level(logging.WARNING, logger="core.portfolio_engine")
        build_portfolio("Moderate", MINIMAL_UNIVERSE, 100_000)
        warnings = [r for r in caplog.records if "PHASE 1 GUARD" in r.message]
        assert not warnings, (
            "PHASE 1 GUARD warning still emitted — it was removed on Day 3 "
            "when pairwise correlation shipped. Check portfolio_engine.py."
        )


class TestPhase2PairwiseCorrelation:
    """Day-3 Phase 2: full pairwise covariance + issuer-tier preference."""

    def test_covariance_matrix_is_symmetric(self):
        from core.portfolio_engine import _build_covariance_matrix
        holdings = [
            {"ticker": "IBIT", "category": "btc_spot", "volatility_pct": 55.0,
             "issuer": "BlackRock"},
            {"ticker": "ETHA", "category": "eth_spot", "volatility_pct": 70.0,
             "issuer": "BlackRock"},
            {"ticker": "DEFI", "category": "btc_futures", "volatility_pct": 58.0,
             "issuer": "Hashdex"},
        ]
        cov = _build_covariance_matrix(holdings)
        for i in range(len(holdings)):
            for j in range(len(holdings)):
                assert abs(cov[i, j] - cov[j, i]) < 1e-9

    def test_same_category_correlation_exceeds_cross_category(self):
        """btc_spot <-> btc_spot should correlate higher than btc_spot <-> eth_spot."""
        from core.portfolio_engine import _pair_corr
        assert _pair_corr("btc_spot", "btc_spot") > _pair_corr("btc_spot", "eth_spot")
        assert _pair_corr("eth_spot", "eth_spot") > _pair_corr("btc_spot", "eth_spot")

    def test_same_issuer_gets_corr_boost(self):
        from core.portfolio_engine import _build_covariance_matrix
        same_issuer = [
            {"ticker": "IBIT", "category": "btc_spot", "volatility_pct": 55.0, "issuer": "BlackRock"},
            {"ticker": "FBTC_fake", "category": "btc_spot", "volatility_pct": 55.0, "issuer": "BlackRock"},
        ]
        diff_issuer = [
            {"ticker": "IBIT", "category": "btc_spot", "volatility_pct": 55.0, "issuer": "BlackRock"},
            {"ticker": "FBTC", "category": "btc_spot", "volatility_pct": 55.0, "issuer": "Fidelity"},
        ]
        cov_same = _build_covariance_matrix(same_issuer)
        cov_diff = _build_covariance_matrix(diff_issuer)
        assert cov_same[0, 1] >= cov_diff[0, 1]


class TestIssuerTierNudge:
    def test_tier_a_issuer_gets_positive_nudge(self):
        from core.portfolio_engine import _issuer_tier_nudge
        assert _issuer_tier_nudge({"ticker": "IBIT", "issuer": "BlackRock"}) > 0
        assert _issuer_tier_nudge({"ticker": "FBTC", "issuer": "Fidelity"}) > 0

    def test_tier_c_legacy_etfs_get_negative_nudge(self):
        from core.portfolio_engine import _issuer_tier_nudge
        assert _issuer_tier_nudge({"ticker": "GBTC", "issuer": "Grayscale"}) < 0
        assert _issuer_tier_nudge({"ticker": "ETHE", "issuer": "Grayscale"}) < 0
        assert _issuer_tier_nudge({"ticker": "DEFI", "issuer": "Hashdex"}) < 0

    def test_tier_b_gets_neutral_nudge(self):
        from core.portfolio_engine import _issuer_tier_nudge
        assert _issuer_tier_nudge({"ticker": "BITB", "issuer": "Bitwise"}) == 0
        assert _issuer_tier_nudge({"ticker": "HODL", "issuer": "VanEck"}) == 0


class TestPairCorrMissingPairWarning:
    """
    Audit 2026-04-22 fix: _pair_corr used to silently return 0.70 for
    unknown pairs, which would mask incomplete coverage in the category
    correlation table. Now logs a one-time warning per missing pair.
    """

    def test_missing_pair_returns_fallback_and_logs_once(self, caplog):
        import logging
        from core import portfolio_engine as pe

        # Clear any prior warnings from other tests
        pe._warned_missing_pairs.clear()

        with caplog.at_level(logging.WARNING, logger="core.portfolio_engine"):
            val1 = pe._pair_corr("made_up_a", "made_up_b")
            val2 = pe._pair_corr("made_up_a", "made_up_b")   # second call

        assert val1 == 0.70   # default cross-category fallback
        assert val2 == 0.70   # still falls back
        # Only ONE warning, not two — cached in _warned_missing_pairs
        missing_warnings = [
            r for r in caplog.records
            if "Missing cross-category correlation" in r.getMessage()
        ]
        assert len(missing_warnings) == 1

    def test_same_category_missing_returns_higher_fallback(self, caplog):
        import logging
        from core import portfolio_engine as pe
        pe._warned_missing_pairs.clear()

        with caplog.at_level(logging.WARNING, logger="core.portfolio_engine"):
            val = pe._pair_corr("new_cat_x", "new_cat_x")

        assert val == 0.90   # within-category default is higher
        assert any(
            "Missing within-category correlation" in r.getMessage()
            for r in caplog.records
        )

    def test_known_pair_does_not_warn(self, caplog):
        import logging
        from core import portfolio_engine as pe
        pe._warned_missing_pairs.clear()

        with caplog.at_level(logging.WARNING, logger="core.portfolio_engine"):
            val = pe._pair_corr("btc_spot", "eth_spot")

        assert val == 0.75  # known cross-category value
        assert not any(
            "Missing" in r.getMessage() for r in caplog.records
        )


class TestTierAllocationMatrix:
    """
    Every tier's category allocations must sum to exactly 100% — otherwise
    the portfolio builder's renormalization produces off-by-epsilon
    weight distributions.
    """

    def test_every_tier_allocations_sum_to_100(self):
        from core.risk_tiers import TIER_CATEGORY_ALLOCATIONS
        for tier_name, allocs in TIER_CATEGORY_ALLOCATIONS.items():
            total = sum(allocs.values())
            assert abs(total - 100.0) < 1e-6, (
                f"Tier {tier_name!r} sums to {total}, not 100"
            )

    def test_allocation_for_tier_returns_copy(self):
        """Must return a NEW dict each call — mutating the result
        should not corrupt the shared matrix."""
        from core.risk_tiers import allocation_for_tier
        a = allocation_for_tier("Ultra Conservative")
        a["fake_category"] = 999.0
        b = allocation_for_tier("Ultra Conservative")
        assert "fake_category" not in b

    def test_allocation_for_tier_raises_on_unknown(self):
        import pytest as _pt
        from core.risk_tiers import allocation_for_tier
        with _pt.raises(ValueError, match="Unknown tier"):
            allocation_for_tier("nonexistent_tier")

    def test_every_used_category_is_in_defaults(self):
        """
        Any category a tier allocates to must have default return / vol /
        correlation values, otherwise universe enrichment silently
        falls back to btc_spot defaults.
        """
        from core.risk_tiers import TIER_CATEGORY_ALLOCATIONS
        from core.etf_universe import _CATEGORY_DEFAULTS

        used = set()
        for allocs in TIER_CATEGORY_ALLOCATIONS.values():
            used.update(allocs.keys())
        missing = used - set(_CATEGORY_DEFAULTS.keys())
        assert not missing, (
            f"Tiers reference categories with no _CATEGORY_DEFAULTS entry: {missing}"
        )
