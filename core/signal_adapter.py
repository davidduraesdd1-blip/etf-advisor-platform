"""
signal_adapter.py — per-ETF composite BUY / HOLD / SELL signal.

Phase 1: simple rule-based composite using the ETF's expected_return and
volatility (both already in the universe entry after enrichment). Day 3+
wires this to real coin-level indicator outputs (RSI / MACD / MVRV / etc.)
weighted across the ETF's underlying holdings.

Signal output is a dict:
    {"ticker", "signal", "score", "shape", "color_key", "plain_english"}

`signal` is one of BUY / HOLD / SELL.
`shape` is the ▲ / ■ / ▼ character per CLAUDE.md §8.

CLAUDE.md governance: Sections 7, 8, 9.
"""
from __future__ import annotations

from typing import Literal

Signal = Literal["BUY", "HOLD", "SELL"]

_SHAPES = {"BUY": "▲", "HOLD": "■", "SELL": "▼"}
_COLOR_KEYS = {"BUY": "success", "HOLD": "muted", "SELL": "danger"}


def composite_signal(etf: dict) -> dict:
    """
    Compute a composite BUY / HOLD / SELL for a single ETF.

    Phase-1 rule:
        score = (expected_return_pct / volatility_pct) * 100   # Sharpe-like
        SELL  if score <   0
        HOLD  if score < 70
        BUY   if score >= 70

    Thresholds are rough placeholders. Day 3+ replaces this with a
    weighted composite of underlying-coin technical + macro + sentiment
    + on-chain signals per CLAUDE.md §9 (LAYER 1-4 framework).
    """
    ret = float(etf.get("expected_return", 0))
    vol = float(etf.get("volatility", 1)) or 1.0
    score = (ret / vol) * 100 if vol > 0 else 0

    if score < 0:
        signal: Signal = "SELL"
    elif score < 70:
        signal = "HOLD"
    else:
        signal = "BUY"

    return {
        "ticker":         etf.get("ticker", ""),
        "signal":         signal,
        "score":          round(score, 2),
        "shape":          _SHAPES[signal],
        "color_key":      _COLOR_KEYS[signal],
        "plain_english":  _plain_english(signal, ret, vol),
    }


def _plain_english(signal: Signal, ret: float, vol: float) -> str:
    """
    Beginner-tier text. Day 3+ swaps this for level-aware rendering via
    config.USER_LEVELS.
    """
    if signal == "BUY":
        return (
            "Attractive return-to-risk profile relative to the crypto ETF "
            "universe. Consider adding or maintaining position."
        )
    if signal == "SELL":
        return (
            "Risk-adjusted return is negative. Consider reducing exposure "
            "or pairing with a more defensive ETF."
        )
    return (
        "Neutral. Return-to-risk profile is acceptable but not standout. "
        "Hold if already allocated; no urgency to add."
    )


def composite_signals_for_universe(universe: list[dict]) -> list[dict]:
    """Batch-compute signals for every ETF in a universe list."""
    return [composite_signal(etf) for etf in universe]
