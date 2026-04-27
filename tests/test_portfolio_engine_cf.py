"""
test_portfolio_engine_cf.py — Sprint 1 Commit 2 coverage.

Verifies the per-category Cornish-Fisher parameter wiring:
  - `_get_cf_params(category)` reads from `data/cf_params_cache.json`
  - Cache miss / stale / unknown-category → falls back to (-0.7, 8.0)
  - `_weighted_cf_params(holdings)` linear-aggregates by holding weight
  - 60/40 btc_spot/altcoin_spot blend produces expected weighted average
  - Empty portfolio falls back to defaults
  - VaR / CVaR call site receives the weighted params (downstream effect:
    alt-heavy tiers report a different VaR than BTC-only tiers when the
    cache is populated)

CLAUDE.md governance: §4 (test coverage), §9 (math model architecture).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


# ── _get_cf_params ──────────────────────────────────────────────────

class TestGetCFParams:
    def test_missing_cache_returns_default_pair(self, monkeypatch, tmp_path):
        """No cache file → fallback to crypto-midpoint defaults."""
        from core import cf_calibration as cc
        from core.portfolio_engine import _get_cf_params, _CF_DEFAULT_SKEW, _CF_DEFAULT_KURT
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        s, k = _get_cf_params("btc_spot")
        assert s == _CF_DEFAULT_SKEW
        assert k == _CF_DEFAULT_KURT

    def test_unknown_category_returns_default(self, monkeypatch, tmp_path):
        """Cache exists but doesn't have the requested category → fallback."""
        from core import cf_calibration as cc
        from core.portfolio_engine import _get_cf_params, _CF_DEFAULT_SKEW, _CF_DEFAULT_KURT
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        cc._write_cache({"btc_spot": (-0.5, 7.0)})
        s, k = _get_cf_params("totally_unknown_category")
        assert s == _CF_DEFAULT_SKEW
        assert k == _CF_DEFAULT_KURT

    def test_btc_spot_vs_altcoin_spot_distinct(self, monkeypatch, tmp_path):
        """When both categories are in cache, _get_cf_params returns
        category-specific values (not the same default)."""
        from core import cf_calibration as cc
        from core.portfolio_engine import _get_cf_params
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        cc._write_cache({
            "btc_spot":     (-0.5, 6.0),
            "altcoin_spot": (-1.1, 12.0),
        })
        btc = _get_cf_params("btc_spot")
        alt = _get_cf_params("altcoin_spot")
        assert btc == (-0.5, 6.0)
        assert alt == (-1.1, 12.0)
        assert btc != alt   # the whole point of per-category fitting

    def test_stale_cache_triggers_fallback(self, monkeypatch, tmp_path):
        """Cache older than 30 days → load_cache returns None → fallback."""
        from core import cf_calibration as cc
        from core.portfolio_engine import _get_cf_params, _CF_DEFAULT_SKEW, _CF_DEFAULT_KURT
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        # Manually write a stale-timestamped cache.
        stale = {
            "_metadata": {
                "fitted_at_iso":  "2024-01-01T00:00:00+00:00",
                "fitted_at_unix": time.time() - (40 * 24 * 3600),
                "ttl_seconds":    30 * 24 * 3600,
            },
            "params": {"btc_spot": [-0.5, 6.0]},
        }
        (tmp_path / "cf_cache.json").write_text(json.dumps(stale))
        s, k = _get_cf_params("btc_spot")
        assert s == _CF_DEFAULT_SKEW
        assert k == _CF_DEFAULT_KURT


# ── _weighted_cf_params ────────────────────────────────────────────

class TestWeightedCFParams:
    def test_empty_holdings_returns_default(self):
        from core.portfolio_engine import _weighted_cf_params, _CF_DEFAULT_SKEW, _CF_DEFAULT_KURT
        s, k = _weighted_cf_params([])
        assert s == _CF_DEFAULT_SKEW
        assert k == _CF_DEFAULT_KURT

    def test_zero_weight_holdings_returns_default(self):
        from core.portfolio_engine import _weighted_cf_params, _CF_DEFAULT_SKEW, _CF_DEFAULT_KURT
        s, k = _weighted_cf_params([{"category": "btc_spot", "weight_pct": 0}])
        assert s == _CF_DEFAULT_SKEW
        assert k == _CF_DEFAULT_KURT

    def test_60_40_btc_alt_blend(self, monkeypatch, tmp_path):
        """A 60/40 btc_spot/altcoin_spot portfolio should produce the
        weight-average of the two categories' (S, K) — within rounding."""
        from core import cf_calibration as cc
        from core.portfolio_engine import _weighted_cf_params
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        cc._write_cache({
            "btc_spot":     (-0.5, 6.0),
            "altcoin_spot": (-1.0, 12.0),
        })
        holdings = [
            {"ticker": "IBIT", "category": "btc_spot",     "weight_pct": 60.0},
            {"ticker": "BSOL", "category": "altcoin_spot", "weight_pct": 40.0},
        ]
        s_w, k_w = _weighted_cf_params(holdings)
        # Expected: 0.6 * (-0.5) + 0.4 * (-1.0) = -0.7
        # Expected: 0.6 * 6.0   + 0.4 * 12.0   = 8.4
        assert abs(s_w - (-0.7)) < 1e-9
        assert abs(k_w - 8.4) < 1e-9

    def test_weights_renormalize_when_off_100(self, monkeypatch, tmp_path):
        """If holdings sum to 90% (rounding), the weighted aggregate
        should still produce a valid result via renormalization."""
        from core import cf_calibration as cc
        from core.portfolio_engine import _weighted_cf_params
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        cc._write_cache({
            "btc_spot":     (-0.5, 6.0),
            "altcoin_spot": (-1.0, 12.0),
        })
        holdings = [
            {"ticker": "IBIT", "category": "btc_spot",     "weight_pct": 54.0},
            {"ticker": "BSOL", "category": "altcoin_spot", "weight_pct": 36.0},
        ]
        s_w, k_w = _weighted_cf_params(holdings)
        # After renorm: btc 60% / alt 40% — same as the canonical 60/40 above
        assert abs(s_w - (-0.7)) < 1e-9
        assert abs(k_w - 8.4) < 1e-9

    def test_aggregation_clamped_to_maillard(self, monkeypatch, tmp_path):
        """Weighted aggregate should still respect Maillard caps even if
        the cache happens to contain extreme values right at the boundary."""
        from core import cf_calibration as cc
        from core.portfolio_engine import _weighted_cf_params, _CF_SKEW_CAP, _CF_KURT_CAP
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        cc._write_cache({
            "altcoin_spot": (-1.5, 15.0),   # both at cap
        })
        holdings = [{"ticker": "X", "category": "altcoin_spot", "weight_pct": 100.0}]
        s_w, k_w = _weighted_cf_params(holdings)
        assert s_w == -_CF_SKEW_CAP
        assert k_w == _CF_KURT_CAP


# ── End-to-end: VaR call site reads weighted params ────────────────

class TestVaRReceivesWeightedParams:
    def test_alt_heavy_cf_isolated_from_diversification(self, monkeypatch, tmp_path):
        """Isolate the CF S/K effect from portfolio-vol effects: at IDENTICAL
        portfolio_vol, alt-heavy weighted (S, K) should produce a higher VaR
        than btc-only weighted (S, K). We call cornish_fisher_var directly
        with the weighted params so the diversification-driven vol reduction
        in compute_portfolio_metrics doesn't mask the tail effect."""
        from core import cf_calibration as cc
        from core.portfolio_engine import (
            _weighted_cf_params, cornish_fisher_var,
        )
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        cc._write_cache({
            "btc_spot":     (-0.3, 4.0),    # mild tails
            "altcoin_spot": (-1.2, 13.0),   # fat tails
        })
        btc_holdings = [{"ticker":"IBIT","category":"btc_spot","weight_pct":100.0}]
        alt_holdings = [{"ticker":"BSOL","category":"altcoin_spot","weight_pct":100.0}]
        s_btc, k_btc = _weighted_cf_params(btc_holdings)
        s_alt, k_alt = _weighted_cf_params(alt_holdings)
        # Verify the per-category lookup propagated through the aggregator.
        assert s_btc == -0.3 and k_btc == 4.0
        assert s_alt == -1.2 and k_alt == 13.0
        # Hold mean_return + vol fixed; vary only (S, K).
        mr, vol = 30.0, 50.0
        var_btc = cornish_fisher_var(mr, vol, 0.95, skew=s_btc, excess_kurt=k_btc)
        var_alt = cornish_fisher_var(mr, vol, 0.95, skew=s_alt, excess_kurt=k_alt)
        assert var_alt > var_btc, (
            f"alt CF (S=-1.2,K=13) should give higher VaR than "
            f"btc CF (S=-0.3,K=4): got alt={var_alt}, btc={var_btc}"
        )

    def test_compute_portfolio_metrics_uses_weighted_params(self, monkeypatch, tmp_path):
        """Smoke-test the wire-up: changing the cached (S, K) for a
        category should change compute_portfolio_metrics' var_95_pct
        on a single-category basket, holding all other inputs constant."""
        from core import cf_calibration as cc
        from core.portfolio_engine import compute_portfolio_metrics
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        holdings = [{
            "ticker":"IBIT","category":"btc_spot","weight_pct":100.0,
            "expected_return_pct":30.0,"volatility_pct":50.0,
            "correlation_with_btc":1.0,"issuer":"BlackRock iShares",
        }]
        # Mild-tail cache.
        cc._write_cache({"btc_spot": (-0.2, 3.0)})
        m_mild = compute_portfolio_metrics(holdings, 100_000, "Moderate")
        # Fat-tail cache.
        cc._write_cache({"btc_spot": (-1.4, 14.0)})
        m_fat = compute_portfolio_metrics(holdings, 100_000, "Moderate")
        # Same portfolio, same vol — only the cached CF params changed.
        # Fat-tail VaR must be higher.
        assert m_fat["var_95_pct"] > m_mild["var_95_pct"], (
            f"fat-tail var {m_fat['var_95_pct']} should exceed "
            f"mild-tail var {m_mild['var_95_pct']}"
        )

    def test_missing_cache_returns_finite_var(self, monkeypatch, tmp_path):
        """No cache → fallback to crypto-midpoint defaults → VaR is still
        finite (regression safety: don't break when fitting hasn't happened)."""
        from core import cf_calibration as cc
        from core.portfolio_engine import compute_portfolio_metrics
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        holdings = [
            {
                "ticker": "IBIT", "category": "btc_spot",
                "weight_pct": 100.0,
                "expected_return_pct": 30.0, "volatility_pct": 50.0,
                "correlation_with_btc": 1.0, "issuer": "BlackRock iShares",
            }
        ]
        m = compute_portfolio_metrics(holdings, 100_000, "Moderate")
        # Just verify the math didn't crash and produced sane outputs.
        assert m["var_95_pct"] >= 0
        assert m["cvar_95_pct"] >= 0
        # CVaR should be at least as conservative as VaR.
        assert m["cvar_95_pct"] >= m["var_95_pct"] - 1e-6
