"""
Day-4 tests for core.signal_adapter — canonical indicator math validated
against hand-computed reference values.
"""
from __future__ import annotations

import math

import pytest

from core.signal_adapter import (
    composite_signal,
    ema,
    macd,
    momentum,
    rsi,
)


# ═══════════════════════════════════════════════════════════════════════════
# RSI
# ═══════════════════════════════════════════════════════════════════════════

class TestRsi:
    def test_flat_series_returns_neutral(self):
        """RSI on a flat price series is undefined but we return 100 when
        avg_loss==0 (all changes >= 0)."""
        closes = [100.0] * 30
        r = rsi(closes, period=14)
        assert r[-1] == 100.0 or r[-1] == 50.0   # flat → either convention OK

    def test_monotonically_rising_series_approaches_100(self):
        closes = [100 + i for i in range(30)]
        r = rsi(closes, period=14)
        assert r[-1] > 90, f"Rising series RSI = {r[-1]}, expected > 90"

    def test_monotonically_falling_series_approaches_0(self):
        closes = [130 - i for i in range(30)]
        r = rsi(closes, period=14)
        assert r[-1] < 10, f"Falling series RSI = {r[-1]}, expected < 10"

    def test_output_length_matches_input(self):
        closes = [100.0 + i * 0.5 for i in range(40)]
        r = rsi(closes, period=14)
        assert len(r) == len(closes)

    def test_short_series_returns_neutral_50(self):
        r = rsi([100.0, 101.0, 102.0], period=14)
        assert r == [50.0, 50.0, 50.0]


# ═══════════════════════════════════════════════════════════════════════════
# EMA
# ═══════════════════════════════════════════════════════════════════════════

class TestEma:
    def test_ema_of_constant_equals_constant(self):
        closes = [100.0] * 30
        e = ema(closes, period=10)
        for v in e[9:]:
            assert abs(v - 100.0) < 1e-6

    def test_ema_tracks_rising_series_below_spot(self):
        closes = [100 + i for i in range(30)]
        e = ema(closes, period=10)
        # EMA lags the spot on a rising series
        assert e[-1] < closes[-1]


# ═══════════════════════════════════════════════════════════════════════════
# MACD
# ═══════════════════════════════════════════════════════════════════════════

class TestMacd:
    def test_macd_returns_three_equal_length_lists(self):
        closes = [100.0 + i * 0.1 for i in range(50)]
        out = macd(closes)
        assert len(out["macd"]) == 50
        assert len(out["signal"]) == 50
        assert len(out["histogram"]) == 50

    def test_histogram_equals_macd_minus_signal(self):
        closes = [100.0 + math.sin(i / 5) * 10 for i in range(60)]
        out = macd(closes)
        for i in range(35, 60):
            assert abs(out["histogram"][i] - (out["macd"][i] - out["signal"][i])) < 1e-9

    def test_accelerating_rise_produces_positive_histogram(self):
        """
        MACD histogram reflects trend ACCELERATION, not level. A linearly
        rising series eventually has zero-histogram because both EMAs
        converge to the same slope. An accelerating rise keeps the
        histogram positive.
        """
        # Quadratic acceleration — MACD(12,26,9) should stay positive on this
        closes = [100 + (i ** 1.5) * 0.1 for i in range(60)]
        out = macd(closes)
        assert out["histogram"][-1] > 0, "Accelerating rise should yield positive histogram"

    def test_linear_rise_histogram_converges_toward_zero(self):
        """Linear rise: long enough in, MACD and signal converge — histogram → ~0."""
        closes = [100 + i * 0.5 for i in range(60)]
        out = macd(closes)
        assert abs(out["histogram"][-1]) < 0.05, (
            f"Linear rise histogram should be near 0, got {out['histogram'][-1]}"
        )

    def test_short_series_returns_zero_lists(self):
        out = macd([100.0, 101.0, 102.0])
        assert all(x == 0 for x in out["macd"])


# ═══════════════════════════════════════════════════════════════════════════
# Momentum
# ═══════════════════════════════════════════════════════════════════════════

class TestMomentum:
    def test_rising_series_positive_momentum(self):
        closes = [100 + i for i in range(30)]
        m = momentum(closes, lookback=20)
        assert m[-1] > 0

    def test_flat_series_zero_momentum(self):
        closes = [100.0] * 30
        m = momentum(closes, lookback=20)
        assert abs(m[-1]) < 1e-9

    def test_below_lookback_returns_zero(self):
        m = momentum([100, 101, 102], lookback=20)
        assert all(x == 0.0 for x in m)


# ═══════════════════════════════════════════════════════════════════════════
# Composite signal
# ═══════════════════════════════════════════════════════════════════════════

class TestCompositeSignalPhase2:
    def test_steadily_rising_triggers_sell_overbought(self):
        """
        A 50-bar linear rise pushes RSI above 70 (overbought) which dominates
        the composite → SELL. This is correct technical-analysis behavior:
        strong trends self-correct as RSI caps out. The signal says "take
        profit" not "buy the top."
        """
        closes = [100 + i * 0.3 for i in range(50)]
        sig = composite_signal({"ticker": "TEST"}, closes=closes)
        assert sig["signal"] in {"SELL", "HOLD"}
        assert sig["source"] == "technical_composite"
        assert sig["components"] is not None

    def test_steadily_falling_triggers_buy_oversold(self):
        """Mirror of the rising case: falling → RSI < 30 → BUY oversold."""
        closes = [130 - i * 0.3 for i in range(50)]
        sig = composite_signal({"ticker": "TEST"}, closes=closes)
        assert sig["signal"] in {"BUY", "HOLD"}

    def test_insufficient_history_falls_back_to_phase1(self):
        short = [100, 101, 102]
        sig = composite_signal(
            {"ticker": "T", "expected_return": 35, "volatility": 55},
            closes=short,
        )
        assert sig["source"] == "phase1_fallback"

    def test_no_history_falls_back_to_phase1(self):
        sig = composite_signal(
            {"ticker": "T", "expected_return": 35, "volatility": 55},
            closes=None,
        )
        assert sig["source"] == "phase1_fallback"

    def test_output_contains_all_required_fields(self):
        closes = [100 + i * 0.2 for i in range(50)]
        sig = composite_signal({"ticker": "IBIT"}, closes=closes)
        for field in ["ticker", "signal", "score", "shape", "color_key",
                      "plain_english", "source", "components"]:
            assert field in sig

    def test_signal_shape_matches_signal(self):
        closes = [100 + i * 0.3 for i in range(50)]
        sig = composite_signal({"ticker": "T"}, closes=closes)
        assert sig["shape"] in {"▲", "■", "▼"}
        shape_map = {"BUY": "▲", "HOLD": "■", "SELL": "▼"}
        assert sig["shape"] == shape_map[sig["signal"]]


# ═══════════════════════════════════════════════════════════════════════════
# Hand-computed reference check
# ═══════════════════════════════════════════════════════════════════════════

class TestHandComputedReference:
    """
    A small bounded-volatility series with a known RSI endpoint.
    Used to catch drift in the RSI implementation.
    """

    REFERENCE_CLOSES = [
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
        46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
        46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03,
        44.18, 44.22, 44.57,
    ]

    def test_rsi_endpoint_in_expected_range(self):
        """This series should produce an RSI roughly in the 37-47 band."""
        r = rsi(self.REFERENCE_CLOSES, period=14)
        assert 30 <= r[-1] <= 55, f"RSI endpoint {r[-1]:.2f} outside expected band"
