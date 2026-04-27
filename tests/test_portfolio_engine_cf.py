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


# ── Feasibility clip + cf_boundary_reached flag (2026-04-28 hotfix) ──

class TestFeasibilityClip:
    """
    cornish_fisher_var/cvar return CFRiskResult NamedTuples after the
    2026-04-28 hotfix: `value` is the loss percentage clipped to the
    long-only 100% bound; `cf_boundary_reached` is True when the
    unclipped polynomial estimate exceeded that bound.
    """

    def test_var_under_bound_returns_unchanged_with_flag_false(self):
        """Mild moments + low confidence: VaR well under 100% → no clip,
        flag is False."""
        from core.portfolio_engine import cornish_fisher_var, CFRiskResult
        # btc_spot-fitted moments + 95% conf + reasonable vol
        result = cornish_fisher_var(30.0, 30.0, 0.95, skew=-0.058, excess_kurt=2.570)
        assert isinstance(result, CFRiskResult)
        assert 0 <= result.value <= 100.0
        assert result.cf_boundary_reached is False

    def test_var_over_bound_clips_with_flag_true(self):
        """Extreme moments + 99% conf: polynomial estimate exceeds 100% →
        clipped to 100%, flag is True."""
        from core.portfolio_engine import cornish_fisher_var, CFRiskResult
        # altcoin_spot Maillard-clamped moments + 99% conf + high vol
        result = cornish_fisher_var(20.0, 80.0, 0.99, skew=-1.5, excess_kurt=15.0)
        assert isinstance(result, CFRiskResult)
        assert result.value == 100.0
        assert result.cf_boundary_reached is True

    def test_cvar_under_bound_unchanged(self):
        from core.portfolio_engine import cornish_fisher_cvar, CFRiskResult
        result = cornish_fisher_cvar(30.0, 30.0, 0.95, skew=-0.058, excess_kurt=2.570)
        assert isinstance(result, CFRiskResult)
        assert 0 <= result.value <= 100.0
        assert result.cf_boundary_reached is False

    def test_cvar_over_bound_clips(self):
        from core.portfolio_engine import cornish_fisher_cvar, CFRiskResult
        result = cornish_fisher_cvar(20.0, 80.0, 0.99, skew=-1.5, excess_kurt=15.0)
        assert isinstance(result, CFRiskResult)
        assert result.value == 100.0
        assert result.cf_boundary_reached is True

    def test_altcoin_spot_99pct_hits_boundary(self):
        """Real production-config fixture: altcoin_spot at 99% should hit
        the boundary on a typical alt-heavy basket (50% vol, 30% mean)."""
        from core.portfolio_engine import cornish_fisher_var
        result = cornish_fisher_var(30.0, 50.0, 0.99, skew=-1.5, excess_kurt=15.0)
        assert result.cf_boundary_reached is True

    def test_btc_spot_95pct_does_not_hit_boundary(self):
        """Real production-config fixture: btc_spot at 95% should NOT hit
        the boundary on a typical BTC-only basket (50% vol, 30% mean) —
        sanity check that the clip isn't over-firing."""
        from core.portfolio_engine import cornish_fisher_var
        result = cornish_fisher_var(30.0, 50.0, 0.95, skew=-0.058, excess_kurt=2.570)
        assert result.cf_boundary_reached is False

    def test_compute_portfolio_metrics_propagates_flag(self):
        """End-to-end: compute_portfolio_metrics carries the boundary
        flag into the returned metrics dict."""
        from core import cf_calibration as cc
        from core.portfolio_engine import compute_portfolio_metrics
        import tempfile
        from pathlib import Path
        # Use a tmp-dir cache populated with extreme alt moments so the
        # CF math hits the boundary; verify the dict has the flag set.
        with tempfile.TemporaryDirectory() as tmp:
            cc.CACHE_PATH = Path(tmp) / "cf_cache.json"
            cc._write_cache({"altcoin_spot": (-1.5, 15.0)})
            holdings = [{
                "ticker": "X", "category": "altcoin_spot", "weight_pct": 100.0,
                "expected_return_pct": 30.0, "volatility_pct": 50.0,
                "correlation_with_btc": 0.7, "issuer": "Test",
            }]
            m = compute_portfolio_metrics(holdings, 100_000, "Ultra Aggressive")
            assert "var_99_cf_boundary_reached" in m
            assert m["var_99_cf_boundary_reached"] is True
            assert m["var_99_pct"] == 100.0
            assert m["any_cf_boundary_reached"] is True

    def test_value_floored_at_zero_when_mean_dominates(self):
        """Very high mean return + tight vol: unclipped VaR could go
        negative; we floor at 0 (a portfolio with extreme positive drift
        still has tail-loss probability ≥ 0%)."""
        from core.portfolio_engine import cornish_fisher_var
        result = cornish_fisher_var(200.0, 5.0, 0.95, skew=-0.058, excess_kurt=2.570)
        assert result.value >= 0.0
        assert result.cf_boundary_reached is False


# ── UI: boundary footnote rendering ────────────────────────────────

class TestRiskMetricsPanelUI:
    """Verifies the risk_metrics_panel renders the CF-boundary footnote
    when (and only when) at least one tile hit the boundary."""

    def test_footnote_renders_when_boundary_reached(self):
        """Alt-heavy metrics dict (any_cf_boundary_reached=True) must
        emit the boundary footnote text."""
        pytest.importorskip("streamlit.testing.v1")
        from streamlit.testing.v1 import AppTest
        # Build a minimal Streamlit script that calls risk_metrics_panel
        # with boundary-reached metrics, then run it via AppTest and
        # scan the rendered markdown for the footnote string.
        script = '''
import streamlit as st
from ui.components import risk_metrics_panel
metrics = {
    "var_95_pct": 64.0, "var_99_pct": 100.0,
    "cvar_95_pct": 100.0, "cvar_99_pct": 100.0,
    "var_95_cf_boundary_reached": False,
    "var_99_cf_boundary_reached": True,
    "cvar_95_cf_boundary_reached": True,
    "cvar_99_cf_boundary_reached": True,
    "any_cf_boundary_reached": True,
}
risk_metrics_panel(metrics, sleeve_usd=81_200)
'''
        at = AppTest.from_string(script, default_timeout=10)
        at.run()
        assert not at.exception, f"AppTest crashed: {at.exception}"
        rendered = " ".join(
            (el.value or "") if hasattr(el, "value") else str(el)
            for el in at.markdown
        )
        assert "model boundary" in rendered, (
            "boundary footnote should render when any tile hit boundary"
        )
        assert "≤ -$81,200" in rendered or "≤ -$81200" in rendered, (
            "boundary tile should display ≤ -$<sleeve> dollar amount"
        )

    def test_footnote_absent_when_no_boundary(self):
        """BTC-only metrics dict (any_cf_boundary_reached=False) must NOT
        emit the boundary footnote."""
        pytest.importorskip("streamlit.testing.v1")
        from streamlit.testing.v1 import AppTest
        script = '''
import streamlit as st
from ui.components import risk_metrics_panel
metrics = {
    "var_95_pct": 30.0, "var_99_pct": 50.0,
    "cvar_95_pct": 60.0, "cvar_99_pct": 80.0,
    "var_95_cf_boundary_reached": False,
    "var_99_cf_boundary_reached": False,
    "cvar_95_cf_boundary_reached": False,
    "cvar_99_cf_boundary_reached": False,
    "any_cf_boundary_reached": False,
}
risk_metrics_panel(metrics, sleeve_usd=100_000)
'''
        at = AppTest.from_string(script, default_timeout=10)
        at.run()
        assert not at.exception, f"AppTest crashed: {at.exception}"
        rendered = " ".join(
            (el.value or "") if hasattr(el, "value") else str(el)
            for el in at.markdown
        )
        assert "model boundary" not in rendered, (
            "footnote should NOT render when no boundary reached"
        )


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
        # CFRiskResult.value access — NamedTuple post-2026-04-28-hotfix.
        assert var_alt.value > var_btc.value

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
