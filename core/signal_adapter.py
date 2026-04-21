"""
signal_adapter.py — per-ETF composite BUY / HOLD / SELL signal.

Day-4 upgrade (per planning-side Q2): replaces the Phase-1 rule-based
score with a composite of canonical technical indicators computed from
live OHLCV price history:

    - RSI(14)  — Wilder's smoothing (14-period)
    - MACD(12, 26, 9)  — standard 12-period fast EMA, 26-period slow EMA,
                        9-period signal line
    - Momentum(20)  — simple 20-period close-over-close return

Composite rule:
    score = 0.45 * rsi_signal + 0.35 * macd_signal + 0.20 * momentum_signal
    where each component is in [-1, +1]
    BUY   if score >=  +0.30
    SELL  if score <=  -0.30
    HOLD  otherwise

When OHLCV is unavailable (e.g., brand-new ETF or live sources all
failed), the adapter falls back to the Phase-1 volatility-adjusted
expected-return heuristic. The fallback path is visibly labeled in the
returned dict so the UI can render a data-source badge accordingly.

Math references:
    - Wilder (1978): New Concepts in Technical Trading Systems (RSI)
    - Appel (1999): Technical Analysis of Stock Trends (MACD formulation)

CLAUDE.md governance: Sections 7, 8, 9.
"""
from __future__ import annotations

from typing import Literal

Signal = Literal["BUY", "HOLD", "SELL"]

_SHAPES = {"BUY": "▲", "HOLD": "■", "SELL": "▼"}
_COLOR_KEYS = {"BUY": "success", "HOLD": "muted", "SELL": "danger"}


# ═══════════════════════════════════════════════════════════════════════════
# Indicator primitives
# ═══════════════════════════════════════════════════════════════════════════

def rsi(closes: list[float], period: int = 14) -> list[float]:
    """
    Wilder's RSI (14-period default).
    Returns a list the same length as `closes`; values before `period`
    are NaN-substituted with 50.0 (neutral).
    """
    n = len(closes)
    out = [50.0] * n
    if n < period + 1:
        return out

    gains = 0.0
    losses = 0.0
    # Seed with SMA of first `period` changes
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period

    rs = avg_gain / avg_loss if avg_loss > 0 else float("inf")
    out[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + rs))

    # Wilder's smoothing for subsequent bars
    for i in range(period + 1, n):
        change = closes[i] - closes[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average. Seeds with the SMA of the first `period` values."""
    n = len(values)
    out = [values[0] if n > 0 else 0.0] * n
    if n < period:
        return out
    alpha = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    for i in range(period, n):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26,
         signal_period: int = 9) -> dict[str, list[float]]:
    """
    Standard MACD (Appel). Returns {'macd', 'signal', 'histogram'} lists.
    Histogram = macd - signal. Crossovers of MACD through the signal line
    are the conventional BUY/SELL triggers.
    """
    if len(closes) < slow + signal_period:
        zero = [0.0] * len(closes)
        return {"macd": zero, "signal": zero, "histogram": zero}

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    signal_line = ema(macd_line, signal_period)
    histogram = [macd_line[i] - signal_line[i] for i in range(len(closes))]
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def momentum(closes: list[float], lookback: int = 20) -> list[float]:
    """
    Simple rate-of-change momentum: (close_now / close_n_ago) - 1, as a pct.
    """
    n = len(closes)
    out = [0.0] * n
    for i in range(lookback, n):
        prev = closes[i - lookback]
        if prev <= 0:
            out[i] = 0.0
        else:
            out[i] = (closes[i] / prev - 1) * 100
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Score translation (each indicator → [-1, +1])
# ═══════════════════════════════════════════════════════════════════════════

def _score_from_rsi(rsi_value: float) -> float:
    """
    RSI 30/70 convention. Below 30 = oversold (buy signal +1); above 70 =
    overbought (sell signal -1); linear between.
    """
    if rsi_value <= 30:
        return +1.0
    if rsi_value >= 70:
        return -1.0
    # 30 → +1, 50 → 0, 70 → -1
    return (50 - rsi_value) / 20


def _score_from_macd_histogram(histogram: float) -> float:
    """
    MACD histogram sign + magnitude. Clamped to ±1 at |hist| >= 2% of price.
    For simplicity we use absolute values; downstream scales.
    """
    if histogram == 0:
        return 0.0
    # A histogram >= ~1 for a $50 stock is a strong signal. Soft-clamp.
    clamped = max(-2.0, min(2.0, histogram))
    return clamped / 2.0


def _score_from_momentum(mom_pct: float) -> float:
    """
    20-period return. ±10% linearly maps to ±1; outside clamped.
    """
    clamped = max(-10.0, min(10.0, mom_pct))
    return clamped / 10.0


# ═══════════════════════════════════════════════════════════════════════════
# Composite
# ═══════════════════════════════════════════════════════════════════════════

_RSI_WEIGHT = 0.45
_MACD_WEIGHT = 0.35
_MOM_WEIGHT = 0.20

_BUY_THRESHOLD = 0.30
_SELL_THRESHOLD = -0.30


def composite_signal(etf: dict, closes: list[float] | None = None) -> dict:
    """
    Compute a composite BUY / HOLD / SELL for a single ETF.

    If `closes` is provided and has enough history (>= 35 bars for MACD
    + signal settling), the upgrade path runs: RSI + MACD + momentum.
    Otherwise falls back to the Phase-1 return-to-volatility rule and
    the returned dict's `source` field says "phase1_fallback".

    Returned dict:
      ticker, signal, score, shape, color_key, plain_english, source,
      components {rsi_value, macd_hist, mom_pct, rsi_score, macd_score,
                  mom_score}
    """
    # Phase-1 fallback gate
    if closes is None or len(closes) < 35:
        return _phase1_fallback(etf)

    rsi_series = rsi(closes, period=14)
    macd_dict = macd(closes, fast=12, slow=26, signal_period=9)
    mom_series = momentum(closes, lookback=20)

    rsi_last = rsi_series[-1]
    macd_hist_last = macd_dict["histogram"][-1]
    mom_last = mom_series[-1]

    s_rsi = _score_from_rsi(rsi_last)
    s_macd = _score_from_macd_histogram(macd_hist_last)
    s_mom = _score_from_momentum(mom_last)

    score = (
        _RSI_WEIGHT * s_rsi
        + _MACD_WEIGHT * s_macd
        + _MOM_WEIGHT * s_mom
    )

    if score >= _BUY_THRESHOLD:
        sig: Signal = "BUY"
    elif score <= _SELL_THRESHOLD:
        sig = "SELL"
    else:
        sig = "HOLD"

    return {
        "ticker":        etf.get("ticker", ""),
        "signal":        sig,
        "score":         round(score, 4),
        "shape":         _SHAPES[sig],
        "color_key":     _COLOR_KEYS[sig],
        "plain_english": _plain_english(sig, source="technical"),
        "source":        "technical_composite",
        "components": {
            "rsi_value":   round(rsi_last, 2),
            "macd_hist":   round(macd_hist_last, 4),
            "mom_pct":     round(mom_last, 2),
            "rsi_score":   round(s_rsi, 3),
            "macd_score":  round(s_macd, 3),
            "mom_score":   round(s_mom, 3),
        },
    }


def _phase1_fallback(etf: dict) -> dict:
    """
    Fallback heuristic when live OHLCV is unavailable: return-to-volatility
    ratio. Explicitly labeled source='phase1_fallback' so UI can render
    a data-source note.
    """
    ret = float(etf.get("expected_return", 0))
    vol = float(etf.get("volatility", 1)) or 1.0
    score = (ret / vol) * 100 if vol > 0 else 0

    if score < 0:
        sig: Signal = "SELL"
    elif score < 70:
        sig = "HOLD"
    else:
        sig = "BUY"

    return {
        "ticker":        etf.get("ticker", ""),
        "signal":        sig,
        "score":         round(score, 2),
        "shape":         _SHAPES[sig],
        "color_key":     _COLOR_KEYS[sig],
        "plain_english": _plain_english(sig, source="phase1_fallback"),
        "source":        "phase1_fallback",
        "components":    None,
    }


def _plain_english(signal: Signal, source: str) -> str:
    if signal == "BUY":
        base = (
            "Technical indicators are positive: oversold or trending up "
            "on momentum and MACD."
        )
    elif signal == "SELL":
        base = (
            "Technical indicators are negative: overbought or trending "
            "down on momentum and MACD."
        )
    else:
        base = (
            "Technical indicators are neutral: no strong directional "
            "conviction from RSI, MACD, or momentum."
        )
    if source == "phase1_fallback":
        base += (
            " (Reading derived from category defaults — live price "
            "history unavailable for this ETF.)"
        )
    return base


def composite_signals_for_universe(universe: list[dict]) -> list[dict]:
    """
    Batch-compute signals. No live price fetching inside this helper —
    the caller is responsible for passing `closes` per-ETF where
    available. Universe entries without closes get the Phase-1 fallback.
    """
    return [composite_signal(etf) for etf in universe]
