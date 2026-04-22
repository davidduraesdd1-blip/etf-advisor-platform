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
        # Alpha Vantage was dropped from the active chain because the
        # free tier's 25 req/day is insufficient even for one user.
        # Scaffold retained for paid-tier reactivation; not reachable
        # at runtime, so not a valid active-source value.
        assert src in {"yfinance", "stooq"}


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


# ═══════════════════════════════════════════════════════════════════════════
# Q2 — realized volatility + BTC correlation
# ═══════════════════════════════════════════════════════════════════════════

class TestRealizedVolatility:
    """
    90-day annualized realized volatility derived from daily log returns.
    Math: σ_annual = stdev(log_returns) × √252 × 100.
    """

    def _random_walk(self, n_days: int, daily_sigma: float, seed: int = 42):
        """Fixed-seed Gaussian walk — exact daily σ lets us assert on vol."""
        import random
        from datetime import date, timedelta
        rng = random.Random(seed)
        closes = [100.0]
        for _ in range(n_days - 1):
            r = rng.gauss(0.0, daily_sigma)
            closes.append(closes[-1] * (2.718281828 ** r))
        dates = [(date(2024, 1, 1) + timedelta(days=i)).isoformat()
                 for i in range(n_days)]
        return {"source": "yfinance",
                "prices": [{"date": d, "close": c}
                           for d, c in zip(dates, closes)]}

    def test_empty_bundle_returns_none(self, monkeypatch):
        from integrations import data_feeds as df
        monkeypatch.setattr(df, "get_etf_prices",
                            lambda tickers, **kw: {tickers[0]: {"source": "unavailable",
                                                                 "prices": []}})
        result = df.get_realized_volatility("NOPE")
        assert result["volatility_pct"] is None

    def test_short_series_returns_none(self, monkeypatch):
        from integrations import data_feeds as df
        bundle = self._random_walk(n_days=15, daily_sigma=0.02)
        monkeypatch.setattr(df, "get_etf_prices",
                            lambda tickers, **kw: {tickers[0]: bundle})
        result = df.get_realized_volatility("SHORT")
        assert result["volatility_pct"] is None

    def test_higher_input_sigma_yields_higher_output_vol(self, monkeypatch):
        """Monotonicity check — larger daily σ must produce larger annualized σ."""
        from integrations import data_feeds as df

        bundle_low = self._random_walk(n_days=150, daily_sigma=0.01, seed=1)
        bundle_high = self._random_walk(n_days=150, daily_sigma=0.05, seed=1)

        monkeypatch.setattr(df, "get_etf_prices",
                            lambda tickers, **kw: {tickers[0]: bundle_low})
        vol_low = df.get_realized_volatility("LOW")["volatility_pct"]

        monkeypatch.setattr(df, "get_etf_prices",
                            lambda tickers, **kw: {tickers[0]: bundle_high})
        vol_high = df.get_realized_volatility("HIGH")["volatility_pct"]

        assert vol_low is not None and vol_high is not None
        assert vol_high > vol_low * 3, \
            f"5x sigma should give ~5x vol, got low={vol_low:.1f} high={vol_high:.1f}"

    def test_annualization_math_is_correct(self, monkeypatch):
        """σ_annual ≈ σ_daily × √252 × 100. Target vol ~32% for σ_daily = 0.02."""
        from integrations import data_feeds as df
        bundle = self._random_walk(n_days=200, daily_sigma=0.02, seed=7)
        monkeypatch.setattr(df, "get_etf_prices",
                            lambda tickers, **kw: {tickers[0]: bundle})
        result = df.get_realized_volatility("IBIT", lookback_days=90)
        # σ_daily=0.02 → σ_annual = 0.02 × √252 × 100 ≈ 31.75%.
        # Random walk introduces sample noise, allow ±30% around expected.
        assert result["volatility_pct"] is not None
        assert 20 < result["volatility_pct"] < 45


class TestBtcCorrelation:
    """
    90-day Pearson correlation of daily log returns vs IBIT.
    """

    def _paired_walks(self, n_days: int, corr_target: float, seed: int = 3):
        """Generate two correlated walks from a shared driver."""
        import random
        from datetime import date, timedelta
        rng = random.Random(seed)
        common, idiosync_a, idiosync_b = [], [], []
        for _ in range(n_days - 1):
            common.append(rng.gauss(0.0, 0.02))
            idiosync_a.append(rng.gauss(0.0, 0.02))
            idiosync_b.append(rng.gauss(0.0, 0.02))

        # Weight: corr_target via √corr on common factor, √(1-corr) on idio.
        import math
        w_c = math.sqrt(abs(corr_target))
        w_i = math.sqrt(max(0.0, 1.0 - abs(corr_target)))
        sign = 1.0 if corr_target >= 0 else -1.0

        close_a, close_b = [100.0], [100.0]
        for i in range(n_days - 1):
            r_a = w_c * common[i] + w_i * idiosync_a[i]
            r_b = sign * w_c * common[i] + w_i * idiosync_b[i]
            close_a.append(close_a[-1] * math.exp(r_a))
            close_b.append(close_b[-1] * math.exp(r_b))

        dates = [(date(2024, 1, 1) + timedelta(days=i)).isoformat()
                 for i in range(n_days)]
        to_bundle = lambda closes: {
            "source": "yfinance",
            "prices": [{"date": d, "close": c} for d, c in zip(dates, closes)],
        }
        return to_bundle(close_a), to_bundle(close_b)

    def test_self_correlation_is_one(self):
        from integrations import data_feeds as df
        result = df.get_btc_correlation("IBIT")
        assert result["correlation"] == 1.0
        assert result["source"] == "self"

    def test_uncorrelated_inputs_give_near_zero(self, monkeypatch):
        from integrations import data_feeds as df
        bundle_a, bundle_b = self._paired_walks(200, corr_target=0.0, seed=11)

        def fake_get_prices(tickers, **kw):
            return {tickers[0]: bundle_a if tickers[0] == "ARKB" else bundle_b}

        monkeypatch.setattr(df, "get_etf_prices", fake_get_prices)
        result = df.get_btc_correlation("ARKB")
        assert result["correlation"] is not None
        assert abs(result["correlation"]) < 0.35

    def test_positively_correlated_inputs_give_positive_correlation(self, monkeypatch):
        from integrations import data_feeds as df
        bundle_a, bundle_b = self._paired_walks(200, corr_target=0.75, seed=13)

        def fake_get_prices(tickers, **kw):
            return {tickers[0]: bundle_a if tickers[0] == "FETH" else bundle_b}

        monkeypatch.setattr(df, "get_etf_prices", fake_get_prices)
        result = df.get_btc_correlation("FETH")
        assert result["correlation"] is not None
        assert result["correlation"] > 0.4

    def test_result_clamped_to_valid_range(self, monkeypatch):
        """Pearson correlation must always land in [-1, +1]."""
        from integrations import data_feeds as df
        for target in [-0.8, -0.3, 0.0, 0.5, 0.9]:
            bundle_a, bundle_b = self._paired_walks(200, corr_target=target, seed=17)

            def fake_get_prices(tickers, bundle_a=bundle_a, bundle_b=bundle_b, **kw):
                return {tickers[0]: bundle_a if tickers[0] == "X" else bundle_b}

            monkeypatch.setattr(df, "get_etf_prices", fake_get_prices)
            result = df.get_btc_correlation("X")
            if result["correlation"] is not None:
                assert -1.0 <= result["correlation"] <= 1.0

    def test_empty_bundle_returns_none(self, monkeypatch):
        from integrations import data_feeds as df
        monkeypatch.setattr(df, "get_etf_prices",
                            lambda tickers, **kw: {tickers[0]: {"source": "unavailable",
                                                                 "prices": []}})
        result = df.get_btc_correlation("NOPE")
        assert result["correlation"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Forward-return estimate — Option B model layer
# ═══════════════════════════════════════════════════════════════════════════

class TestForwardReturnEstimate:
    """
    Per-ETF forward-return estimate drives the "Forward estimate (model)"
    KPI tile alongside the historical CAGR. Uses long-run BTC-USD /
    ETH-USD CAGR (10-year lookback) with category-specific drag/premium.
    """

    def _long_run_bundle(self, cagr_pct: float, n_days: int = 4000):
        """
        Build a price bundle that realizes exactly `cagr_pct` annualized
        over ~11 years. Exact geometric interpolation, no noise.
        """
        from datetime import date, timedelta
        years = n_days / 365.25
        ratio = (1.0 + cagr_pct / 100.0) ** years
        start = 100.0
        end = start * ratio
        per_step = (end / start) ** (1 / max(1, n_days - 1))
        closes = [start * (per_step ** i) for i in range(n_days)]
        dates = [(date(2014, 1, 1) + timedelta(days=i)).isoformat()
                 for i in range(n_days)]
        return {"source": "yfinance",
                "prices": [{"date": d, "close": c}
                           for d, c in zip(dates, closes)]}

    def _fake_prices(self, btc_cagr: float, eth_cagr: float):
        """Return a fake get_etf_prices that feeds exact long-run CAGRs."""
        btc_bundle = self._long_run_bundle(btc_cagr)
        eth_bundle = self._long_run_bundle(eth_cagr)

        def fake(tickers, **kw):
            t = tickers[0]
            if t == "BTC-USD":
                return {t: btc_bundle}
            if t == "ETH-USD":
                return {t: eth_bundle}
            return {t: {"source": "unavailable", "prices": []}}

        return fake

    def test_btc_spot_uses_btc_long_run(self, monkeypatch):
        from integrations import data_feeds as df
        # Clear the module-level long-run cache so our stub is observed.
        df._LONG_RUN_CAGR_MEMO.clear()
        monkeypatch.setattr(df, "get_etf_prices",
                            self._fake_prices(btc_cagr=60.0, eth_cagr=45.0))

        result = df.get_forward_return_estimate("btc_spot", expense_ratio_bps=25)
        assert result["forward_return_pct"] is not None
        # 60% × 0.99 - 0.25% ≈ 59.15%
        assert 58.5 < result["forward_return_pct"] < 59.8
        assert result["source"] == "live_long_run"

    def test_eth_spot_uses_eth_long_run(self, monkeypatch):
        from integrations import data_feeds as df
        df._LONG_RUN_CAGR_MEMO.clear()
        monkeypatch.setattr(df, "get_etf_prices",
                            self._fake_prices(btc_cagr=60.0, eth_cagr=45.0))

        result = df.get_forward_return_estimate("eth_spot", expense_ratio_bps=25)
        # 45% × 0.99 - 0.25% ≈ 44.30%
        assert 43.5 < result["forward_return_pct"] < 44.8

    def test_btc_futures_applies_roll_drag(self, monkeypatch):
        from integrations import data_feeds as df
        df._LONG_RUN_CAGR_MEMO.clear()
        monkeypatch.setattr(df, "get_etf_prices",
                            self._fake_prices(btc_cagr=60.0, eth_cagr=45.0))

        spot = df.get_forward_return_estimate("btc_spot", expense_ratio_bps=25)
        futures = df.get_forward_return_estimate("btc_futures", expense_ratio_bps=95)
        # Futures should be meaningfully lower than spot due to 10% drag
        # factor plus higher expense ratio.
        assert futures["forward_return_pct"] < spot["forward_return_pct"]
        assert spot["forward_return_pct"] - futures["forward_return_pct"] > 5

    def test_thematic_applies_equity_beta_premium(self, monkeypatch):
        from integrations import data_feeds as df
        df._LONG_RUN_CAGR_MEMO.clear()
        monkeypatch.setattr(df, "get_etf_prices",
                            self._fake_prices(btc_cagr=60.0, eth_cagr=45.0))

        result = df.get_forward_return_estimate("thematic", expense_ratio_bps=50)
        # 60% × 0.6 + 45% × 0.4 = 54% base × 1.10 = 59.4% - 0.5% = 58.9%
        assert 58.0 < result["forward_return_pct"] < 59.8

    def test_tier_monotonicity_on_underlying_cagr_spread(self, monkeypatch):
        """
        With BTC > ETH, btc_spot should exceed eth_spot. With ETH > BTC,
        the opposite. Confirms forward-estimate direction tracks underlying.
        """
        from integrations import data_feeds as df

        df._LONG_RUN_CAGR_MEMO.clear()
        monkeypatch.setattr(df, "get_etf_prices",
                            self._fake_prices(btc_cagr=70.0, eth_cagr=40.0))
        assert (df.get_forward_return_estimate("btc_spot", 25)["forward_return_pct"]
                > df.get_forward_return_estimate("eth_spot", 25)["forward_return_pct"])

        df._LONG_RUN_CAGR_MEMO.clear()
        monkeypatch.setattr(df, "get_etf_prices",
                            self._fake_prices(btc_cagr=30.0, eth_cagr=60.0))
        assert (df.get_forward_return_estimate("eth_spot", 25)["forward_return_pct"]
                > df.get_forward_return_estimate("btc_spot", 25)["forward_return_pct"])

    def test_unknown_category_returns_none(self, monkeypatch):
        from integrations import data_feeds as df
        df._LONG_RUN_CAGR_MEMO.clear()
        monkeypatch.setattr(df, "get_etf_prices",
                            self._fake_prices(btc_cagr=60.0, eth_cagr=45.0))
        result = df.get_forward_return_estimate("made_up_cat")
        assert result["forward_return_pct"] is None
        assert result["source"] == "unavailable"

    def test_long_run_cagr_caches_result(self, monkeypatch):
        """
        Once resolved, the 24-hour cache should prevent re-fetch on the
        next call. We assert by swapping the backing fetcher between
        calls — the second call should use the cached value.
        """
        from integrations import data_feeds as df
        df._LONG_RUN_CAGR_MEMO.clear()

        monkeypatch.setattr(df, "get_etf_prices",
                            self._fake_prices(btc_cagr=50.0, eth_cagr=30.0))
        first = df.get_long_run_cagr("BTC-USD")
        assert first["cagr_pct"] is not None

        # Swap to a totally different backing — if cache works, we still
        # get the original 50%, not the new 5%.
        monkeypatch.setattr(df, "get_etf_prices",
                            self._fake_prices(btc_cagr=5.0, eth_cagr=3.0))
        second = df.get_long_run_cagr("BTC-USD")
        assert second["source"] == "cached_long_run"
        assert abs(second["cagr_pct"] - first["cagr_pct"]) < 0.01
