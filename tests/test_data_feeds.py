"""
Day-2 data_feeds tests — circuit breaker behavior.

Covers:
  - initial active source = yfinance
  - 3 failures on known-history tickers in 60s flip source to stooq
  - new-ETF misses (ticker not in seed universe) do NOT trip breaker
  - reset_circuit_breaker returns state to initial
  - failures outside the 60s window do not accumulate

Run:
    pytest tests/test_data_feeds.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from config import (
    YF_CIRCUIT_BREAKER_THRESHOLD,
    YF_CIRCUIT_BREAKER_WINDOW_SEC,
)
from integrations.data_feeds import (
    _KNOWN_HISTORY_TICKERS,
    _record_failure,
    circuit_breaker_state,
    get_active_price_source,
    reset_circuit_breaker,
)


@pytest.fixture(autouse=True)
def _reset_cb():
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


class TestCircuitBreakerInitialState:
    def test_initial_source_is_yfinance(self):
        assert get_active_price_source() == "yfinance"

    def test_initial_state_clean(self):
        state = circuit_breaker_state()
        assert state["active_source"] == "yfinance"
        assert state["failure_count"] == 0
        assert state["tripped_at"] is None
        assert state["new_etf_misses"] == 0


class TestCircuitBreakerTripsAtThreshold:
    def test_threshold_failures_trip_breaker(self):
        # Pick any ticker known to have history
        known = next(iter(_KNOWN_HISTORY_TICKERS))
        for _ in range(YF_CIRCUIT_BREAKER_THRESHOLD):
            _record_failure(known)
        assert get_active_price_source() == "stooq"
        state = circuit_breaker_state()
        assert state["tripped_at"] is not None

    def test_one_less_than_threshold_does_not_trip(self):
        known = next(iter(_KNOWN_HISTORY_TICKERS))
        for _ in range(YF_CIRCUIT_BREAKER_THRESHOLD - 1):
            _record_failure(known)
        assert get_active_price_source() == "yfinance"


class TestNewEtfMissesDoNotTrip:
    """Planning-side Risk 5: brand-new listings legitimately have no history."""

    def test_new_etf_misses_do_not_trip_breaker(self):
        # Use tickers guaranteed to NOT be in the seed universe
        for ticker in ("ZZZ1", "ZZZ2", "ZZZ3", "ZZZ4", "ZZZ5"):
            assert ticker not in _KNOWN_HISTORY_TICKERS
            _record_failure(ticker)
        assert get_active_price_source() == "yfinance"
        state = circuit_breaker_state()
        assert state["failure_count"] == 0   # none counted against breaker
        assert state["new_etf_misses"] == 5  # but we DO track for visibility

    def test_mixed_misses_only_known_count_toward_trip(self):
        known = next(iter(_KNOWN_HISTORY_TICKERS))
        # Mix: 2 known + 5 unknown = still below threshold of 3 known
        for ticker in ("ZZZ1", "ZZZ2"):
            _record_failure(ticker)
        _record_failure(known)
        _record_failure(known)
        assert get_active_price_source() == "yfinance"
        # One more known failure should trip
        _record_failure(known)
        assert get_active_price_source() == "stooq"


class TestCircuitBreakerWindowExpiry:
    def test_failures_outside_window_are_dropped(self):
        """Failures older than YF_CIRCUIT_BREAKER_WINDOW_SEC shouldn't count."""
        import integrations.data_feeds as df
        known = next(iter(_KNOWN_HISTORY_TICKERS))
        # Simulate old failures by injecting timestamps BEFORE the window
        old_time = 1000.0  # fictitious monotonic time
        for _ in range(YF_CIRCUIT_BREAKER_THRESHOLD):
            df._circuit_state["failure_times"].append(old_time)

        # Now add a failure "now" — old ones should be pruned, breaker stays up
        _record_failure(known)
        # Because only 1 recent failure, breaker should NOT trip
        assert get_active_price_source() == "yfinance"


class TestResetCircuitBreaker:
    def test_reset_clears_all_state(self):
        known = next(iter(_KNOWN_HISTORY_TICKERS))
        for _ in range(YF_CIRCUIT_BREAKER_THRESHOLD):
            _record_failure(known)
        assert get_active_price_source() == "stooq"

        reset_circuit_breaker()
        state = circuit_breaker_state()
        assert state["active_source"] == "yfinance"
        assert state["failure_count"] == 0
        assert state["tripped_at"] is None
        assert state["new_etf_misses"] == 0


class TestGetActivePriceSourceContract:
    def test_returns_string_with_valid_source_value(self):
        src = get_active_price_source()
        assert isinstance(src, str)
        assert src in {"yfinance", "stooq", "alphavantage"}
