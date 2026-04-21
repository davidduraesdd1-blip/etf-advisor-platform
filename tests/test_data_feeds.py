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


# ═══════════════════════════════════════════════════════════════════════════
# get_historical_cagr — live CAGR derivation used by Portfolio expected return
# ═══════════════════════════════════════════════════════════════════════════

class TestHistoricalCagr:
    """
    get_historical_cagr is the Portfolio-page hook replacing hardcoded
    category-default expected returns. Tests cover:
      - plausible-shape 100-day rising series → sensible positive CAGR
      - declining series → negative CAGR
      - empty bundle → None with source registered
      - single-day bundle (insufficient data) → None
      - ±300% cap preserves legitimate 3-6x crypto moves but filters
        pathological divisor-by-zero / data-error artifacts
    """

    def _rising_bundle(self, start: float, end: float, n_days: int = 400):
        """Build a price bundle that grows from start → end over n_days."""
        from datetime import date, timedelta
        dates = [(date(2024, 1, 1) + timedelta(days=i)).isoformat()
                 for i in range(n_days)]
        # Geometric interpolation → CAGR math is exact
        ratio = (end / start) ** (1 / max(1, n_days - 1))
        closes = [start * (ratio ** i) for i in range(n_days)]
        return {"source": "yfinance",
                "prices": [{"date": d, "close": c}
                           for d, c in zip(dates, closes)]}

    def test_rising_series_produces_positive_cagr(self, monkeypatch):
        from integrations import data_feeds as df

        bundle = self._rising_bundle(100.0, 150.0, n_days=365)
        monkeypatch.setattr(df, "get_etf_prices",
                            lambda tickers, **kw: {tickers[0]: bundle})

        result = df.get_historical_cagr("IBIT")
        assert result["cagr_pct"] is not None
        # Geometric ratio 1.5 over ~1 year → CAGR ≈ 50%
        assert 48 < result["cagr_pct"] < 53
        assert result["source"] == "yfinance"

    def test_declining_series_produces_negative_cagr(self, monkeypatch):
        from integrations import data_feeds as df

        bundle = self._rising_bundle(100.0, 70.0, n_days=365)
        monkeypatch.setattr(df, "get_etf_prices",
                            lambda tickers, **kw: {tickers[0]: bundle})

        result = df.get_historical_cagr("ETHA")
        assert result["cagr_pct"] is not None
        # 100 → 70 in ~1 year → CAGR ≈ -30%
        assert -32 < result["cagr_pct"] < -28

    def test_empty_bundle_returns_none(self, monkeypatch):
        from integrations import data_feeds as df

        monkeypatch.setattr(df, "get_etf_prices",
                            lambda tickers, **kw: {tickers[0]: {"source": "unavailable", "prices": []}})

        result = df.get_historical_cagr("NOPE")
        assert result["cagr_pct"] is None
        assert result["source"] == "unavailable"

    def test_short_series_returns_none(self, monkeypatch):
        from integrations import data_feeds as df

        bundle = self._rising_bundle(100.0, 110.0, n_days=15)
        monkeypatch.setattr(df, "get_etf_prices",
                            lambda tickers, **kw: {tickers[0]: bundle})

        result = df.get_historical_cagr("IBIT")
        assert result["cagr_pct"] is None

    def test_cap_preserves_legitimate_crypto_moves(self, monkeypatch):
        """
        A 3x move over 1 year = 200% CAGR — that's a legitimate crypto
        year (think BTC 2020). Must NOT be capped at a too-tight value.
        """
        from integrations import data_feeds as df

        bundle = self._rising_bundle(100.0, 300.0, n_days=365)
        monkeypatch.setattr(df, "get_etf_prices",
                            lambda tickers, **kw: {tickers[0]: bundle})

        result = df.get_historical_cagr("IBIT")
        # ~200% CAGR — well under the 300% cap, must pass through.
        assert result["cagr_pct"] is not None
        assert 195 < result["cagr_pct"] < 205

    def test_cap_filters_pathological_values(self, monkeypatch):
        """
        A 20x move over 1 year = 1900% CAGR — implausible for any ETF,
        must be clamped to the ±300% cap.
        """
        from integrations import data_feeds as df

        bundle = self._rising_bundle(1.0, 20.0, n_days=365)
        monkeypatch.setattr(df, "get_etf_prices",
                            lambda tickers, **kw: {tickers[0]: bundle})

        result = df.get_historical_cagr("WILD")
        assert result["cagr_pct"] is not None
        assert result["cagr_pct"] == 300.0  # exact cap
