"""
test_scheduler_flow_warming.py — Sprint 2 Commit 4 coverage.

Verifies the cron pre-warming step (`core.scheduler.prewarm_etf_flow_cache`)
walks the universe, calls each fetcher per ticker, continues past
per-ticker failures, and produces a per-source summary suitable for
the ETF Detail page header's freshness indicator.

CLAUDE.md governance: §4 (test coverage), §10 (no-silent-fallback),
§12 (cache TTL).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestPrewarmEtfFlowCache:
    @pytest.fixture
    def _stub_fetchers(self, monkeypatch):
        """Stub the three fetchers to return deterministic per-ticker
        results, so the prewarm summary is testable without hitting
        any live source."""
        from integrations import etf_flow_data as efd
        # Different ticker → different source, so the summary's source-
        # distribution Counter is meaningfully populated.
        def _aum(t):
            if t == "IBIT": return (62e9, "yfinance")
            if t == "GFIL": return (None, None)   # hits "none" bucket
            return (1.0, "production snapshot (yfinance)")
        def _flow(t):
            if t == "IBIT": return (2.1e9, "cryptorank.io")
            return (None, None)
        def _vol(t):
            if t == "IBIT": return (1.24e9, "yfinance (3M avg)")
            if t == "GFIL": return (1.0, "yfinance (60D history)")
            return (None, None)
        monkeypatch.setattr(efd, "get_etf_aum", _aum)
        monkeypatch.setattr(efd, "get_etf_30d_net_flow", _flow)
        monkeypatch.setattr(efd, "get_etf_avg_daily_volume", _vol)
        yield

    def test_prewarm_returns_summary_dict_with_required_fields(self, _stub_fetchers):
        from core.scheduler import prewarm_etf_flow_cache
        universe = [
            {"ticker": "IBIT"},
            {"ticker": "GFIL"},
            {"ticker": "OTHER"},
        ]
        out = prewarm_etf_flow_cache(universe)
        assert "warmed_at_utc" in out
        assert out["n_total"] == 3
        assert "aum" in out and "flow" in out and "vol" in out

    def test_source_distribution_counted_correctly(self, _stub_fetchers):
        """3 tickers → AUM dist has yfinance:1, snapshot:1, none:1 ;
        Flow dist has cryptorank:1, none:2 ; Vol has 3M:1, 60D:1, none:1."""
        from core.scheduler import prewarm_etf_flow_cache
        universe = [
            {"ticker": "IBIT"},
            {"ticker": "GFIL"},
            {"ticker": "OTHER"},
        ]
        out = prewarm_etf_flow_cache(universe)
        aum = out["aum"]
        assert aum["yfinance"] == 1
        assert "production snapshot (yfinance)" in aum
        assert aum["none"] == 1
        flow = out["flow"]
        assert flow["cryptorank.io"] == 1
        assert flow["none"] == 2
        vol = out["vol"]
        assert vol["yfinance (3M avg)"] == 1
        assert vol["yfinance (60D history)"] == 1
        assert vol["none"] == 1

    def test_continues_past_per_ticker_failure(self, monkeypatch):
        """When a fetcher raises for one ticker, prewarm logs + buckets
        it as 'error' and continues to the next ticker."""
        from core.scheduler import prewarm_etf_flow_cache
        from integrations import etf_flow_data as efd
        call_log: list[str] = []
        def _aum(t):
            call_log.append(t)
            if t == "BAD": raise RuntimeError("boom")
            return (1e9, "yfinance")
        monkeypatch.setattr(efd, "get_etf_aum", _aum)
        monkeypatch.setattr(efd, "get_etf_30d_net_flow", lambda t: (None, None))
        monkeypatch.setattr(efd, "get_etf_avg_daily_volume", lambda t: (None, None))
        universe = [{"ticker": "IBIT"}, {"ticker": "BAD"}, {"ticker": "ETHA"}]
        out = prewarm_etf_flow_cache(universe)
        # All three were attempted (bad didn't short-circuit the loop)
        assert call_log == ["IBIT", "BAD", "ETHA"]
        assert out["aum"].get("error", 0) == 1
        assert out["aum"]["yfinance"] == 2

    def test_empty_ticker_in_universe_skipped(self, monkeypatch):
        from core.scheduler import prewarm_etf_flow_cache
        from integrations import etf_flow_data as efd
        called: list[str] = []
        def _aum(t):
            called.append(t)
            return (None, None)
        monkeypatch.setattr(efd, "get_etf_aum", _aum)
        monkeypatch.setattr(efd, "get_etf_30d_net_flow", lambda t: (None, None))
        monkeypatch.setattr(efd, "get_etf_avg_daily_volume", lambda t: (None, None))
        universe = [{"ticker": ""}, {"ticker": "IBIT"}, {}]   # empty + missing
        prewarm_etf_flow_cache(universe)
        assert called == ["IBIT"]


class TestRecalculateAllPortfoliosCallsPrewarm:
    """Sanity: recalculate_all_portfolios should call prewarm_etf_flow_cache
    and stash the summary in the snapshot dict."""

    def test_snapshot_carries_flow_prewarm_field(self, monkeypatch, tmp_path):
        from core import scheduler
        # Redirect snapshot path to tmp.
        monkeypatch.setattr(scheduler, "SNAPSHOT_PATH", tmp_path / "snap.json")
        # Stub prewarm + universe + portfolio build.
        monkeypatch.setattr(
            scheduler, "prewarm_etf_flow_cache",
            lambda universe: {"warmed_at_utc": "x", "n_total": 0,
                               "aum": {}, "flow": {}, "vol": {}},
        )
        # Stub the universe loader + build_portfolio so the test doesn't
        # need a real universe.
        import core.demo_clients as dc
        monkeypatch.setattr(dc, "DEMO_CLIENTS", [])

        snapshot = scheduler.recalculate_all_portfolios()
        assert "flow_prewarm" in snapshot
        assert snapshot["flow_prewarm"]["warmed_at_utc"] == "x"
