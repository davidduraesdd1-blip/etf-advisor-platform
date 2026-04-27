"""
test_cf_calibration.py — Sprint 1 Commit 1 coverage.

Verifies:
  - `fit_skew_kurtosis` returns (~0, ~3) on synthetic standard-normal returns
    (per the population moments of N(0,1) — Pearson kurtosis is 3, Fisher
    excess is 0; we report raw kurtosis so should be ~3).
  - Empty input raises ValueError.
  - Under-min-observations raises ValueError.
  - Maillard 2012 cap clamping triggers on extreme inputs.
  - Cache write + read roundtrip respects 30-day TTL.
  - Stale cache (older than TTL) returns None on read.

CLAUDE.md governance: §4 (audit + test coverage requirements).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest


# ── fit_skew_kurtosis ──────────────────────────────────────────────

class TestFitSkewKurtosis:
    def test_normal_distribution_skew_near_zero(self):
        """Standard normal has population skewness 0 and raw kurtosis 3."""
        from core.cf_calibration import fit_skew_kurtosis
        rng = np.random.default_rng(42)
        # 5000 samples → bias-corrected estimator should converge
        # well within 0.1 of the population moments.
        sample = rng.standard_normal(5000)
        s, k = fit_skew_kurtosis(sample)
        assert abs(s) < 0.1, f"normal-dist skew should be near 0, got {s}"
        assert abs(k - 3.0) < 0.3, f"normal-dist raw kurtosis should be near 3, got {k}"

    def test_skewed_distribution_returns_negative_skew(self):
        """Right-skew → positive skewness; left-skew → negative skewness.
        Build a left-skewed mixture: most mass near +0.01, a few large
        negative jumps. Sample skewness should be negative."""
        from core.cf_calibration import fit_skew_kurtosis
        rng = np.random.default_rng(42)
        normal = rng.standard_normal(4500) * 0.01
        # Inject left-tail jumps
        jumps = rng.standard_normal(500) * 0.05 - 0.10
        sample = np.concatenate([normal, jumps])
        s, k = fit_skew_kurtosis(sample)
        assert s < -0.3, f"left-skewed sample should have negative skew, got {s}"
        # Mixture has heavier-than-normal tails → kurtosis > 3.
        assert k > 3.0, f"mixture should have raw kurtosis > 3, got {k}"

    def test_empty_input_raises(self):
        from core.cf_calibration import fit_skew_kurtosis
        with pytest.raises(ValueError, match="empty"):
            fit_skew_kurtosis([])

    def test_under_min_observations_raises(self):
        from core.cf_calibration import fit_skew_kurtosis
        # Default min is 252 trading days; pass 100 samples.
        with pytest.raises(ValueError, match="observations"):
            fit_skew_kurtosis(np.zeros(100))

    def test_under_min_observations_custom_threshold(self):
        from core.cf_calibration import fit_skew_kurtosis
        # If caller passes a smaller min, 100 samples should be fine.
        rng = np.random.default_rng(0)
        sample = rng.standard_normal(100)
        s, k = fit_skew_kurtosis(sample, min_observations=50)
        # Just assert no exception + finite outputs.
        assert np.isfinite(s) and np.isfinite(k)

    def test_maillard_skew_cap_clamps_extreme(self):
        """A tiny extreme tail event drives raw skewness past Maillard's
        cap; the function must clamp to ±1.5."""
        from core.cf_calibration import fit_skew_kurtosis, SKEW_CAP_HIGH, SKEW_CAP_LOW
        rng = np.random.default_rng(42)
        # Concentrate mass + add a single +10σ event to force extreme right-skew
        sample = list(rng.standard_normal(500) * 0.001) + [+10.0]
        s, k = fit_skew_kurtosis(np.array(sample))
        assert s == SKEW_CAP_HIGH, f"expected clamp to {SKEW_CAP_HIGH}, got {s}"
        # Same for left-skew.
        sample_neg = list(rng.standard_normal(500) * 0.001) + [-10.0]
        s_neg, _ = fit_skew_kurtosis(np.array(sample_neg))
        assert s_neg == SKEW_CAP_LOW, f"expected clamp to {SKEW_CAP_LOW}, got {s_neg}"

    def test_maillard_kurt_cap_clamps_extreme(self):
        """Heavy-tailed sample drives raw kurtosis past Maillard's 15 cap."""
        from core.cf_calibration import fit_skew_kurtosis, KURT_CAP_HIGH
        rng = np.random.default_rng(42)
        # Very fat-tailed: most near 0, several extreme jumps both directions.
        body = rng.standard_normal(900) * 0.001
        tails = np.array([5.0, -5.0, 6.0, -6.0, 7.0, -7.0, 8.0, -8.0, 9.0, -9.0])
        sample = np.concatenate([body, tails])
        _, k = fit_skew_kurtosis(sample)
        assert k <= KURT_CAP_HIGH, f"raw kurtosis must be clamped to {KURT_CAP_HIGH}, got {k}"


# ── fit_per_category ───────────────────────────────────────────────

class TestFitPerCategory:
    def test_demo_mode_returns_fallback_for_every_category(self, monkeypatch, tmp_path):
        """DEMO_MODE_NO_FETCH=1 causes fetch_category_returns to return
        empty; fit_per_category must hand back the crypto-midpoint fallback
        for every category."""
        from core import cf_calibration as cc
        monkeypatch.setenv("DEMO_MODE_NO_FETCH", "1")
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        out = cc.fit_per_category(write_cache=True)
        assert set(out.keys()) == set(cc.CATEGORY_LIST)
        for cat, (s, k) in out.items():
            assert s == cc.FALLBACK_SKEW
            assert k == cc.FALLBACK_KURT

    def test_cache_write_atomic(self, monkeypatch, tmp_path):
        """After a successful fit, the cache JSON must exist + parse + carry
        the metadata block."""
        from core import cf_calibration as cc
        monkeypatch.setenv("DEMO_MODE_NO_FETCH", "1")
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        cc.fit_per_category(write_cache=True)
        assert (tmp_path / "cf_cache.json").exists()
        loaded = json.loads((tmp_path / "cf_cache.json").read_text())
        assert "_metadata" in loaded
        assert "params" in loaded
        assert "fitted_at_unix" in loaded["_metadata"]
        assert loaded["_metadata"]["ttl_seconds"] == 30 * 24 * 3600


# ── load_cache TTL ────────────────────────────────────────────────

class TestLoadCacheTTL:
    def test_fresh_cache_returns_dict(self, monkeypatch, tmp_path):
        from core import cf_calibration as cc
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        # Write a synthetic fresh cache.
        cc._write_cache({"btc_spot": (-0.5, 7.5), "eth_spot": (-0.6, 6.8)})
        loaded = cc.load_cache()
        assert loaded is not None
        assert loaded["btc_spot"] == (-0.5, 7.5)
        assert loaded["eth_spot"] == (-0.6, 6.8)

    def test_stale_cache_returns_none(self, monkeypatch, tmp_path):
        """Cache older than 30 days must be ignored; load_cache returns None."""
        from core import cf_calibration as cc
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        # Write a cache with a fitted_at_unix > 30 days ago.
        stale_payload = {
            "_metadata": {
                "fitted_at_iso":  "2024-01-01T00:00:00+00:00",
                "fitted_at_unix": time.time() - (40 * 24 * 3600),
                "ttl_seconds":    30 * 24 * 3600,
            },
            "params": {"btc_spot": [-0.5, 7.5]},
        }
        (tmp_path / "cf_cache.json").write_text(json.dumps(stale_payload))
        assert cc.load_cache() is None

    def test_missing_cache_returns_none(self, monkeypatch, tmp_path):
        from core import cf_calibration as cc
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        assert cc.load_cache() is None

    def test_malformed_cache_returns_none(self, monkeypatch, tmp_path):
        from core import cf_calibration as cc
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        (tmp_path / "cf_cache.json").write_text("not valid json {{{")
        assert cc.load_cache() is None

    def test_load_cache_re_clamps_on_read(self, monkeypatch, tmp_path):
        """If an on-disk cache somehow has values outside Maillard caps
        (e.g., predating a tightened cap), load_cache re-clamps on read."""
        from core import cf_calibration as cc
        monkeypatch.setattr(cc, "CACHE_PATH", tmp_path / "cf_cache.json")
        payload = {
            "_metadata": {
                "fitted_at_iso":  "2026-04-28T00:00:00+00:00",
                "fitted_at_unix": time.time(),
                "ttl_seconds":    30 * 24 * 3600,
            },
            "params": {"btc_spot": [-2.5, 20.0]},   # outside caps
        }
        (tmp_path / "cf_cache.json").write_text(json.dumps(payload))
        loaded = cc.load_cache()
        assert loaded is not None
        s, k = loaded["btc_spot"]
        assert s == cc.SKEW_CAP_LOW
        assert k == cc.KURT_CAP_HIGH
