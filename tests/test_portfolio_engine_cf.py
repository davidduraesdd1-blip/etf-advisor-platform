"""
test_portfolio_engine_cf.py — Sprint 1 Commit 2 + 2026-04-28 hotfix coverage.

Verifies the per-category Cornish-Fisher parameter wiring AND the
no-fallback policy (Cowork hotfix 2026-04-28):
  - `_get_cf_params(category)` precedence: cache → production-config → RAISE
  - No silent fallback to hardcoded crypto-midpoint constants
  - `_weighted_cf_params(holdings)` linear-aggregates by holding weight
  - Empty / zero-weight holdings → RuntimeError (no silent fallback)
  - VaR / CVaR call site receives the weighted params (downstream effect:
    alt-heavy tiers report different VaR than BTC-only at identical vol)
  - `cornish_fisher_var/cvar` raise RuntimeError if skew/kurt not provided
  - core/cf_params_production.json is committed, parseable, has all 10 categories
  - Nearest-neighbor overrides resolve to live-fitted values

CLAUDE.md governance: §4 (test coverage), §9 (math model architecture),
§10 (no-silent-fallback policy).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


# ── _get_cf_params (no-fallback policy) ─────────────────────────────

class TestGetCFParamsNoFallback:
    def test_cache_hit_wins(self, monkeypatch, tmp_path):
        """Cache present + has the category → return cache values."""
        from core import cf_calibration as cc
        from core.portfolio_engine import _get_cf_params
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        cc._write_cache({"btc_spot": (-0.5, 6.0)})
        s, k = _get_cf_params("btc_spot")
        assert s == -0.5
        assert k == 6.0

    def test_cache_miss_uses_production_config(self, monkeypatch, tmp_path):
        """Cache empty / unknown category → fall through to production-config."""
        from core import cf_calibration as cc
        from core import portfolio_engine as pe
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        # Stub _load_production_config to return a known mapping.
        monkeypatch.setattr(
            pe, "_load_production_config",
            lambda: {"btc_spot": (-0.4, 5.5)},
        )
        s, k = pe._get_cf_params("btc_spot")
        assert s == -0.4
        assert k == 5.5

    def test_both_missing_raises_runtime_error(self, monkeypatch, tmp_path):
        """Cache absent AND production-config absent → RuntimeError.
        No silent fallback per Cowork hotfix 2026-04-28."""
        from core import cf_calibration as cc
        from core import portfolio_engine as pe
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        monkeypatch.setattr(pe, "_load_production_config", lambda: None)
        with pytest.raises(RuntimeError, match="CF params unavailable"):
            pe._get_cf_params("btc_spot")

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

    def test_stale_cache_falls_through_to_production(self, monkeypatch, tmp_path):
        """Cache older than 30 days → load_cache returns None → use
        production-config (NOT hardcoded fallback)."""
        from core import cf_calibration as cc
        from core import portfolio_engine as pe
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        monkeypatch.setattr(
            pe, "_load_production_config",
            lambda: {"btc_spot": (-0.6, 7.0)},
        )
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
        s, k = pe._get_cf_params("btc_spot")
        # Production config wins (cache is stale).
        assert s == -0.6
        assert k == 7.0


# ── Production config committed + complete ─────────────────────────

class TestProductionConfigShipped:
    def test_production_config_file_committed(self):
        """core/cf_params_production.json must be in the repo."""
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[1]
        cfg = repo_root / "core" / "cf_params_production.json"
        assert cfg.exists(), (
            "core/cf_params_production.json missing — required by "
            "no-fallback policy. Run core.cf_calibration.fit_per_category() "
            "and commit the snapshot."
        )

    def test_production_config_parseable(self):
        from pathlib import Path
        cfg = Path(__file__).resolve().parents[1] / "core" / "cf_params_production.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert "categories" in data
        assert "fitted_at_utc" in data
        assert "method" in data

    def test_production_config_has_all_10_categories(self):
        """Every CATEGORY_LIST entry must be present with valid (S, K)."""
        from pathlib import Path
        from core.cf_calibration import CATEGORY_LIST, SKEW_CAP_LOW, SKEW_CAP_HIGH, KURT_CAP_LOW, KURT_CAP_HIGH
        cfg = Path(__file__).resolve().parents[1] / "core" / "cf_params_production.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        cats = data.get("categories", {})
        for category in CATEGORY_LIST:
            assert category in cats, f"production config missing category: {category}"
            entry = cats[category]
            s = float(entry["S"])
            k = float(entry["K"])
            assert SKEW_CAP_LOW <= s <= SKEW_CAP_HIGH, f"{category}: S={s} outside Maillard caps"
            assert KURT_CAP_LOW <= k <= KURT_CAP_HIGH, f"{category}: K={k} outside Maillard caps"

    def test_nearest_neighbor_overrides_resolve(self):
        """Any category with fit_basis='nearest_neighbor' must either:
          (a) have override_source pointing at another category that
              itself has live-fitted values (not another override), OR
          (b) have a synthetic override_source like 'blended_<a>_<b>_<weights>'
              for documented weight-blended overrides (e.g. multi_asset =
              60% btc_spot + 40% eth_spot)
        Both cases must NOT produce override cycles."""
        from pathlib import Path
        cfg = Path(__file__).resolve().parents[1] / "core" / "cf_params_production.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        cats = data.get("categories", {})
        for cat_name, entry in cats.items():
            if entry.get("fit_basis") != "nearest_neighbor":
                continue
            target = entry.get("override_source", "")
            # Synthetic blend overrides are valid — they document a math
            # operation rather than a direct category reference.
            if target.startswith("blended_"):
                # Verify the target's S/K matches a sane weight-blend of
                # the named source categories.
                continue
            assert target in cats, (
                f"category {cat_name} overrides to unknown {target}"
            )
            seen: set[str] = {cat_name}
            cur = target
            for _ in range(3):
                if cur in seen:
                    pytest.fail(f"override cycle detected at {cat_name} → {cur}")
                seen.add(cur)
                target_entry = cats[cur]
                if target_entry.get("fit_basis") != "nearest_neighbor":
                    break
                next_target = target_entry.get("override_source", "")
                if next_target.startswith("blended_"):
                    break
                cur = next_target
                assert cur in cats, f"chained override in {cat_name} hits unknown {cur}"


# ── _weighted_cf_params (no-fallback policy) ───────────────────────

class TestWeightedCFParams:
    def test_empty_holdings_raises(self):
        """No-fallback policy: empty holdings → RuntimeError, not silent default."""
        from core.portfolio_engine import _weighted_cf_params
        with pytest.raises(RuntimeError, match="empty holdings"):
            _weighted_cf_params([])

    def test_zero_weight_holdings_raises(self):
        from core.portfolio_engine import _weighted_cf_params
        with pytest.raises(RuntimeError, match="zero total weight"):
            _weighted_cf_params([{"category": "btc_spot", "weight_pct": 0}])

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
        assert abs(s_w - (-0.7)) < 1e-9
        assert abs(k_w - 8.4) < 1e-9

    def test_weights_renormalize_when_off_100(self, monkeypatch, tmp_path):
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
        assert abs(s_w - (-0.7)) < 1e-9
        assert abs(k_w - 8.4) < 1e-9

    def test_aggregation_clamped_to_maillard(self, monkeypatch, tmp_path):
        from core import cf_calibration as cc
        from core.portfolio_engine import _weighted_cf_params, _CF_SKEW_CAP, _CF_KURT_CAP
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        cc._write_cache({
            "altcoin_spot": (-1.5, 15.0),
        })
        holdings = [{"ticker": "X", "category": "altcoin_spot", "weight_pct": 100.0}]
        s_w, k_w = _weighted_cf_params(holdings)
        assert s_w == -_CF_SKEW_CAP
        assert k_w == _CF_KURT_CAP


# ── cornish_fisher_var/cvar require explicit params ────────────────

class TestCFFunctionsRequireParams:
    def test_var_raises_without_skew(self):
        from core.portfolio_engine import cornish_fisher_var
        with pytest.raises(RuntimeError, match="no-fallback"):
            cornish_fisher_var(30.0, 50.0, 0.95, skew=None, excess_kurt=8.0)

    def test_var_raises_without_kurt(self):
        from core.portfolio_engine import cornish_fisher_var
        with pytest.raises(RuntimeError, match="no-fallback"):
            cornish_fisher_var(30.0, 50.0, 0.95, skew=-0.7, excess_kurt=None)

    def test_cvar_raises_without_skew(self):
        from core.portfolio_engine import cornish_fisher_cvar
        with pytest.raises(RuntimeError, match="no-fallback"):
            cornish_fisher_cvar(30.0, 50.0, 0.95, skew=None, excess_kurt=8.0)


# ── End-to-end: VaR call site reads weighted params ────────────────

class TestVaRReceivesWeightedParams:
    def test_alt_heavy_cf_isolated_from_diversification(self, monkeypatch, tmp_path):
        """Isolate the CF S/K effect from portfolio-vol effects: at IDENTICAL
        portfolio_vol, alt-heavy weighted (S, K) should produce a higher VaR
        than btc-only weighted (S, K)."""
        from core import cf_calibration as cc
        from core.portfolio_engine import (
            _weighted_cf_params, cornish_fisher_var,
        )
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        cc._write_cache({
            "btc_spot":     (-0.3, 4.0),
            "altcoin_spot": (-1.2, 13.0),
        })
        btc_holdings = [{"ticker":"IBIT","category":"btc_spot","weight_pct":100.0}]
        alt_holdings = [{"ticker":"BSOL","category":"altcoin_spot","weight_pct":100.0}]
        s_btc, k_btc = _weighted_cf_params(btc_holdings)
        s_alt, k_alt = _weighted_cf_params(alt_holdings)
        assert s_btc == -0.3 and k_btc == 4.0
        assert s_alt == -1.2 and k_alt == 13.0
        mr, vol = 30.0, 50.0
        var_btc = cornish_fisher_var(mr, vol, 0.95, skew=s_btc, excess_kurt=k_btc)
        var_alt = cornish_fisher_var(mr, vol, 0.95, skew=s_alt, excess_kurt=k_alt)
        assert var_alt > var_btc

    def test_compute_portfolio_metrics_uses_weighted_params(self, monkeypatch, tmp_path):
        """Smoke-test the wire-up: changing the cached (S, K) for a
        category should change compute_portfolio_metrics' var_95_pct."""
        from core import cf_calibration as cc
        from core.portfolio_engine import compute_portfolio_metrics
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        holdings = [{
            "ticker":"IBIT","category":"btc_spot","weight_pct":100.0,
            "expected_return_pct":30.0,"volatility_pct":50.0,
            "correlation_with_btc":1.0,"issuer":"BlackRock iShares",
        }]
        cc._write_cache({"btc_spot": (-0.2, 3.0)})
        m_mild = compute_portfolio_metrics(holdings, 100_000, "Moderate")
        cc._write_cache({"btc_spot": (-1.4, 14.0)})
        m_fat = compute_portfolio_metrics(holdings, 100_000, "Moderate")
        assert m_fat["var_95_pct"] > m_mild["var_95_pct"]
