"""
test_etf_flow_data.py — Sprint 2 Commit 1 coverage.

Verifies the multi-source live chain for AUM / 30D net flow / avg
daily volume, including:
  - cache hit/miss/atomic-write
  - chain step ordering (step 1 wins; chain falls through on empty)
  - source-name return tuple shape
  - DEMO_MODE_NO_FETCH=1 short-circuit to production snapshot
  - key-gated cryptorank step skipped when CRYPTORANK_API_KEY unset
  - production-snapshot read precedence

CLAUDE.md governance: §4 (audit + tests), §10 (no-silent-fallback).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


# ── Cache layer ────────────────────────────────────────────────────

class TestCacheLayer:
    def test_cache_get_returns_none_when_missing(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "cache.json")
        assert efd._cache_get("IBIT", "aum") is None

    def test_cache_put_then_get_roundtrip(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "cache.json")
        efd._cache_put("IBIT", "aum", 62_400_000_000, "yfinance")
        result = efd._cache_get("IBIT", "aum")
        assert result is not None
        v, src = result
        assert v == 62_400_000_000
        assert src == "yfinance"

    def test_cache_does_not_poison_on_none_write(self, monkeypatch, tmp_path):
        """A None value must not be cached — next call retries the chain."""
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "cache.json")
        efd._cache_put("XXX", "aum", None, "yfinance")
        assert efd._cache_get("XXX", "aum") is None

    def test_stale_cache_returns_none(self, monkeypatch, tmp_path):
        """Entries older than 24h must not be served."""
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "cache.json")
        stale = {
            "_metadata": {},
            "entries": {
                "IBIT::aum": {
                    "value":  62_400_000_000,
                    "source": "yfinance",
                    "ts":     time.time() - (25 * 3600),
                }
            }
        }
        (tmp_path / "cache.json").write_text(json.dumps(stale))
        assert efd._cache_get("IBIT", "aum") is None

    def test_atomic_write_no_tempfile_leftover(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "cache.json")
        efd._cache_put("IBIT", "aum", 1.0, "x")
        leftover = list(tmp_path.glob("*.tmp"))
        assert leftover == []


# ── Production snapshot read ───────────────────────────────────────

class TestProductionSnapshotRead:
    def test_missing_snapshot_returns_none_pair(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "PRODUCTION_PATH", tmp_path / "prod.json")
        v, src = efd._production_snapshot_get("IBIT", "aum_usd")
        assert v is None and src is None

    def test_present_snapshot_returns_value_and_marked_source(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        path = tmp_path / "prod.json"
        path.write_text(json.dumps({
            "tickers": {
                "IBIT": {
                    "aum_usd": 62_400_000_000,
                    "aum_source": "yfinance",
                }
            }
        }))
        monkeypatch.setattr(efd, "PRODUCTION_PATH", path)
        v, src = efd._production_snapshot_get("IBIT", "aum_usd")
        assert v == 62_400_000_000
        # Source string carries both that we're on the snapshot AND
        # what the upstream source was at capture time.
        assert "production snapshot" in src
        assert "yfinance" in src

    def test_unknown_ticker_in_snapshot_returns_none_pair(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        path = tmp_path / "prod.json"
        path.write_text(json.dumps({"tickers": {"IBIT": {"aum_usd": 1}}}))
        monkeypatch.setattr(efd, "PRODUCTION_PATH", path)
        v, src = efd._production_snapshot_get("UNKNOWN", "aum_usd")
        assert v is None and src is None


# ── AUM chain ──────────────────────────────────────────────────────

class TestGetEtfAUM:
    def test_yfinance_step_1_wins_when_present(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "c.json")
        monkeypatch.delenv("DEMO_MODE_NO_FETCH", raising=False)

        class FakeTicker:
            def __init__(self, t): pass
            @property
            def info(self): return {"totalAssets": 5_000_000_000}
        import yfinance as yf
        monkeypatch.setattr(yf, "Ticker", FakeTicker)

        v, src = efd.get_etf_aum("IBIT")
        assert v == 5_000_000_000
        assert src == "yfinance"

    def test_falls_through_to_step_2_on_yfinance_empty(self, monkeypatch, tmp_path):
        """yfinance returns None / empty info → fall through to EDGAR
        N-PORT step 2."""
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "c.json")
        monkeypatch.delenv("DEMO_MODE_NO_FETCH", raising=False)

        class FakeTicker:
            def __init__(self, t): pass
            @property
            def info(self): return {}
        import yfinance as yf
        monkeypatch.setattr(yf, "Ticker", FakeTicker)

        # Stub EDGAR composition to return a meaningful total_value_usd.
        from integrations import edgar_nport
        def fake_comp(ticker):
            return {"total_value_usd": 9_000_000_000}
        monkeypatch.setattr(edgar_nport, "get_etf_composition", fake_comp)
        monkeypatch.setattr(edgar_nport, "SUPPORTED_TICKERS", {"IBIT"})

        v, src = efd.get_etf_aum("IBIT")
        assert v == 9_000_000_000
        assert src == "SEC EDGAR"

    def test_demo_mode_uses_only_production_snapshot(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        monkeypatch.setenv("DEMO_MODE_NO_FETCH", "1")
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "c.json")
        path = tmp_path / "prod.json"
        path.write_text(json.dumps({
            "tickers": {
                "IBIT": {"aum_usd": 1.23e10, "aum_source": "yfinance"}
            }
        }))
        monkeypatch.setattr(efd, "PRODUCTION_PATH", path)
        v, src = efd.get_etf_aum("IBIT")
        assert v == 1.23e10
        assert "production snapshot" in src

    def test_all_steps_fail_returns_none_pair(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "c.json")
        monkeypatch.setattr(efd, "PRODUCTION_PATH", tmp_path / "prod.json")
        monkeypatch.delenv("DEMO_MODE_NO_FETCH", raising=False)

        class FakeTicker:
            def __init__(self, t): pass
            @property
            def info(self): return {}
        import yfinance as yf
        monkeypatch.setattr(yf, "Ticker", FakeTicker)

        # Block step 2 (EDGAR) and steps 3-4 by raising.
        from integrations import edgar_nport
        monkeypatch.setattr(edgar_nport, "get_etf_composition",
                            lambda t: {"total_value_usd": 0})
        monkeypatch.setattr(edgar_nport, "SUPPORTED_TICKERS", set())
        monkeypatch.setattr(efd, "_scrape_etfcom_aum", lambda t: None)
        monkeypatch.setattr(efd, "_scrape_issuer_aum", lambda t: (None, None))

        v, src = efd.get_etf_aum("UNKNOWN_TICKER")
        assert v is None and src is None

    def test_empty_ticker_returns_none_pair(self):
        from integrations.etf_flow_data import get_etf_aum
        assert get_etf_aum("") == (None, None)


# ── 30D net flow chain ─────────────────────────────────────────────

class TestGet30dNetFlow:
    def test_cryptorank_step_wins_when_key_set(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "c.json")
        monkeypatch.delenv("DEMO_MODE_NO_FETCH", raising=False)
        monkeypatch.setenv("CRYPTORANK_API_KEY", "test_key")
        monkeypatch.setattr(efd, "_fetch_cryptorank_flow",
                            lambda t, k: 2_100_000_000)
        v, src = efd.get_etf_30d_net_flow("IBIT")
        assert v == 2_100_000_000
        assert src == "cryptorank.io"

    def test_cryptorank_skipped_when_key_unset(self, monkeypatch, tmp_path):
        """No CRYPTORANK_API_KEY → skip step 1 and try step 2 (SoSoValue)."""
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "c.json")
        monkeypatch.delenv("DEMO_MODE_NO_FETCH", raising=False)
        monkeypatch.delenv("CRYPTORANK_API_KEY", raising=False)
        # Track that cryptorank wasn't called
        called = {"crypto": False}
        def _watcher(t, k):
            called["crypto"] = True
            return 1.0
        monkeypatch.setattr(efd, "_fetch_cryptorank_flow", _watcher)
        monkeypatch.setattr(efd, "_scrape_sosovalue_flow",
                            lambda t: 580_000_000)
        v, src = efd.get_etf_30d_net_flow("IBIT")
        assert called["crypto"] is False
        assert src == "SoSoValue"

    def test_falls_through_to_farside(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "c.json")
        monkeypatch.delenv("DEMO_MODE_NO_FETCH", raising=False)
        monkeypatch.delenv("CRYPTORANK_API_KEY", raising=False)
        monkeypatch.setattr(efd, "_scrape_sosovalue_flow", lambda t: None)
        monkeypatch.setattr(efd, "_fetch_farside_flow", lambda t: 45_000_000)
        v, src = efd.get_etf_30d_net_flow("IBIT")
        assert v == 45_000_000
        assert src == "Farside"

    def test_all_chain_steps_fail_falls_to_production_snapshot(
        self, monkeypatch, tmp_path,
    ):
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "c.json")
        path = tmp_path / "prod.json"
        path.write_text(json.dumps({
            "tickers": {"IBIT": {"flow_30d_usd": 100, "flow_source": "Farside"}}
        }))
        monkeypatch.setattr(efd, "PRODUCTION_PATH", path)
        monkeypatch.delenv("DEMO_MODE_NO_FETCH", raising=False)
        monkeypatch.delenv("CRYPTORANK_API_KEY", raising=False)
        monkeypatch.setattr(efd, "_scrape_sosovalue_flow", lambda t: None)
        monkeypatch.setattr(efd, "_fetch_farside_flow", lambda t: None)
        monkeypatch.setattr(efd, "_synth_flow_from_nport", lambda t: None)
        v, src = efd.get_etf_30d_net_flow("IBIT")
        assert v == 100
        assert "production snapshot" in src


# ── Avg daily volume chain ─────────────────────────────────────────

class TestGetAvgDailyVolume:
    def test_yfinance_3m_step_wins(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "c.json")
        monkeypatch.delenv("DEMO_MODE_NO_FETCH", raising=False)

        class FakeTicker:
            def __init__(self, t): pass
            @property
            def info(self): return {"averageVolume": 1_240_000_000}
        import yfinance as yf
        monkeypatch.setattr(yf, "Ticker", FakeTicker)
        v, src = efd.get_etf_avg_daily_volume("IBIT")
        assert v == 1_240_000_000
        assert "yfinance" in src and "3M" in src

    def test_falls_through_to_10d_when_3m_missing(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "c.json")
        monkeypatch.delenv("DEMO_MODE_NO_FETCH", raising=False)

        class FakeTicker:
            def __init__(self, t): pass
            @property
            def info(self):
                return {
                    "averageVolume": None,
                    "averageDailyVolume10Day": 90_000_000,
                }
        import yfinance as yf
        monkeypatch.setattr(yf, "Ticker", FakeTicker)
        v, src = efd.get_etf_avg_daily_volume("IBIT")
        assert v == 90_000_000
        assert "10D" in src

    def test_falls_through_to_history_compute(self, monkeypatch, tmp_path):
        from integrations import etf_flow_data as efd
        monkeypatch.setattr(efd, "CACHE_PATH", tmp_path / "c.json")
        monkeypatch.delenv("DEMO_MODE_NO_FETCH", raising=False)

        class FakeTicker:
            def __init__(self, t): pass
            @property
            def info(self): return {}
        import yfinance as yf
        monkeypatch.setattr(yf, "Ticker", FakeTicker)
        monkeypatch.setattr(efd, "_scrape_etfcom_vol", lambda t: None)
        monkeypatch.setattr(efd, "_vol_from_history", lambda t: 50_000_000)

        v, src = efd.get_etf_avg_daily_volume("IBIT")
        assert v == 50_000_000
        assert "60D history" in src
