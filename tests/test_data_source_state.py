"""
Day-3 tests for core.data_source_state.

Critical: simulate a full fallback cascade, verify get_state() transitions
correctly through all four states (LIVE → FALLBACK_LIVE → CACHED → STATIC).
"""
from __future__ import annotations

import pytest

from core.data_source_state import (
    DataSourceState,
    get_age_minutes,
    get_source,
    get_state,
    mark_cache_hit,
    mark_static_fallback,
    register_fetch_attempt,
    reset_all,
    snapshot,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_all()
    yield
    reset_all()


class TestInitialStateIsUnknown:
    def test_untouched_category_is_unknown(self):
        assert get_state("etf_price") == DataSourceState.UNKNOWN
        assert get_source("etf_price") == ""
        assert get_age_minutes("etf_price") is None


class TestLiveState:
    def test_primary_source_success_sets_live(self):
        register_fetch_attempt("etf_price", "yfinance", success=True)
        assert get_state("etf_price") == DataSourceState.LIVE
        assert get_source("etf_price") == "yfinance"

    def test_fred_primary_for_risk_free_rate(self):
        register_fetch_attempt("risk_free_rate", "fred", success=True)
        assert get_state("risk_free_rate") == DataSourceState.LIVE


class TestFallbackLiveState:
    def test_secondary_source_success_sets_fallback_live(self):
        register_fetch_attempt("etf_price", "yfinance", success=False)
        register_fetch_attempt("etf_price", "stooq", success=True)
        assert get_state("etf_price") == DataSourceState.FALLBACK_LIVE
        assert get_source("etf_price") == "stooq"

    def test_tertiary_source_also_fallback_live(self):
        register_fetch_attempt("etf_price", "yfinance", success=False)
        register_fetch_attempt("etf_price", "stooq", success=False)
        register_fetch_attempt("etf_price", "alphavantage", success=True)
        assert get_state("etf_price") == DataSourceState.FALLBACK_LIVE
        assert get_source("etf_price") == "alphavantage"


class TestCachedState:
    def test_mark_cache_hit_sets_cached(self):
        mark_cache_hit("etf_price", age_seconds=420)
        assert get_state("etf_price") == DataSourceState.CACHED
        # age_minutes read from cache_age_seconds_at_mark
        age = get_age_minutes("etf_price")
        assert age is not None and age >= 7


class TestStaticState:
    def test_mark_static_fallback_sets_static(self):
        mark_static_fallback("risk_free_rate", note="FRED unavailable")
        assert get_state("risk_free_rate") == DataSourceState.STATIC
        assert get_source("risk_free_rate") == "static"


class TestFullCascade:
    """Simulate the full fallback chain end-to-end."""

    def test_cascade_live_to_fallback_to_cached_to_static(self):
        # Step 1: primary succeeds → LIVE
        register_fetch_attempt("etf_price", "yfinance", success=True)
        assert get_state("etf_price") == DataSourceState.LIVE

        # Step 2: primary fails, secondary succeeds → FALLBACK_LIVE
        register_fetch_attempt("etf_price", "yfinance", success=False)
        register_fetch_attempt("etf_price", "stooq", success=True)
        assert get_state("etf_price") == DataSourceState.FALLBACK_LIVE

        # Step 3: all live fail, serve cache → CACHED
        register_fetch_attempt("etf_price", "yfinance", success=False)
        register_fetch_attempt("etf_price", "stooq", success=False)
        register_fetch_attempt("etf_price", "alphavantage", success=False)
        mark_cache_hit("etf_price", age_seconds=180)
        assert get_state("etf_price") == DataSourceState.CACHED

        # Step 4: no cache either → STATIC
        mark_static_fallback("etf_price", note="no cache, no live")
        assert get_state("etf_price") == DataSourceState.STATIC

    def test_recovery_back_to_live(self):
        """After a cascade, a successful primary fetch should restore LIVE."""
        mark_static_fallback("etf_price", note="fallback")
        assert get_state("etf_price") == DataSourceState.STATIC
        register_fetch_attempt("etf_price", "yfinance", success=True)
        assert get_state("etf_price") == DataSourceState.LIVE


class TestSnapshotIntegration:
    def test_snapshot_includes_all_touched_categories(self):
        register_fetch_attempt("etf_price", "yfinance", success=True)
        mark_static_fallback("risk_free_rate", note="x")
        snap = snapshot()
        assert "etf_price" in snap
        assert "risk_free_rate" in snap
        assert snap["etf_price"]["state"] == "LIVE"
        assert snap["risk_free_rate"]["state"] == "STATIC"

    def test_reset_all_clears_snapshot(self):
        register_fetch_attempt("etf_price", "yfinance", success=True)
        assert snapshot()
        reset_all()
        assert snapshot() == {}
