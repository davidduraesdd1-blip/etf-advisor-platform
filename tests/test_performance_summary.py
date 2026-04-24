"""
test_performance_summary.py — DV-2 regression guard.

Verifies `ui.components.performance_summary_table` and its helpers
satisfy CLAUDE.md §22 item 5: every performance display shows time
horizons (1Y / 3Y / 5Y / since-inception), benchmark comparison, and
max drawdown.

Run via: pytest tests/test_performance_summary.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from ui.components import (
    _ps_max_drawdown_pct,
    _ps_simple_return_pct,
    _ps_cagr_pct,
    performance_summary_table,
)


# ── Fixture builders ─────────────────────────────────────────────────


def _daily_series(years: float, start_price: float = 100.0, end_price: float | None = None) -> list[dict]:
    """
    Build a list of {date, close} dicts spanning `years` calendar years,
    at 1 row per weekday. If end_price omitted, uses start_price * 2
    (straight-line).
    """
    days = int(years * 252)
    end_price = end_price if end_price is not None else start_price * 2.0
    step = (end_price - start_price) / max(days - 1, 1)
    start_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None) - timedelta(days=int(years * 365))
    rows = []
    for i in range(days):
        rows.append({
            "date": (start_date + timedelta(days=i)).isoformat(),
            "close": round(start_price + step * i, 4),
        })
    return rows


def _drawdown_series() -> list[dict]:
    """Series that rises 100→200, drops to 50 (−75%), recovers to 150."""
    rows = []
    base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=365)
    # up: 100 → 200 over 100 days
    for i in range(100):
        rows.append({"date": (base + timedelta(days=i)).isoformat(), "close": 100 + i})
    # down: 200 → 50 over 100 days
    for i in range(100):
        rows.append({"date": (base + timedelta(days=100 + i)).isoformat(), "close": 200 - (150 * i / 99)})
    # up: 50 → 150 over 100 days
    for i in range(100):
        rows.append({"date": (base + timedelta(days=200 + i)).isoformat(), "close": 50 + i})
    return rows


# ── Scalar helpers ───────────────────────────────────────────────────


def test_max_drawdown_matches_expected():
    closes = [100, 120, 200, 150, 50, 90, 150]  # peak 200, trough 50 = −75%
    dd = _ps_max_drawdown_pct(closes)
    assert dd is not None
    assert abs(dd - (-75.0)) < 0.01, f"expected -75%, got {dd}"


def test_max_drawdown_monotonic_up_is_zero():
    closes = [100, 110, 120, 130, 140]
    dd = _ps_max_drawdown_pct(closes)
    assert dd == 0.0


def test_simple_return_insufficient_history_returns_none():
    closes = [100, 105, 110]  # only 3 data points
    assert _ps_simple_return_pct(closes, 252) is None  # 1Y = 252 days


def test_simple_return_sufficient_history():
    # 500 points, doubling over the period — 1Y return should be well-defined
    closes = [100 + i * 0.2 for i in range(500)]
    ret = _ps_simple_return_pct(closes, 252)
    assert ret is not None
    assert ret > 0


def test_cagr_on_doubling_series_over_1_year_is_about_100():
    closes = [100.0 + (100.0 * i / 251) for i in range(252)]  # 252 days, 100→200
    cagr = _ps_cagr_pct(closes, n_calendar_days=365)
    assert cagr is not None
    assert 95 < cagr < 105, f"expected ~100%, got {cagr}"


# ── Full table integration ───────────────────────────────────────────


def test_table_with_6y_history_populates_all_horizons():
    price_data = {"FOO": {"prices": _daily_series(years=6), "source": "yfinance"}}
    df = performance_summary_table(tickers=["FOO"], price_data=price_data)

    assert list(df.columns) == [
        "ticker", "source", "inception",
        "1Y %", "3Y %", "5Y %", "since-inception %", "max drawdown %",
    ]
    row = df.iloc[0]
    assert row["ticker"] == "FOO"
    assert row["source"] == "yfinance"
    # All horizons should be real percentages, NOT placeholders
    for col in ("1Y %", "3Y %", "5Y %", "since-inception %"):
        assert row[col].endswith("%"), f"{col} should be a numeric %, got {row[col]}"
    assert row["max drawdown %"].endswith("%")


def test_table_with_18mo_shows_insufficient_history_for_3y_and_5y():
    price_data = {"NEW": {"prices": _daily_series(years=1.5), "source": "yfinance"}}
    df = performance_summary_table(tickers=["NEW"], price_data=price_data)
    row = df.iloc[0]
    # 1Y and since-inception should populate
    assert row["1Y %"].endswith("%")
    assert row["since-inception %"].endswith("%")
    # 3Y and 5Y should show the "N/A (<N>Y hist)" placeholder — NOT None, NOT blank
    assert "N/A" in row["3Y %"] and "3Y" in row["3Y %"], f"got {row['3Y %']}"
    assert "N/A" in row["5Y %"] and "5Y" in row["5Y %"], f"got {row['5Y %']}"


def test_table_with_no_history_shows_all_placeholders():
    price_data = {"EMPTY": {"prices": [], "source": "unavailable"}}
    df = performance_summary_table(tickers=["EMPTY"], price_data=price_data)
    row = df.iloc[0]
    for col in ("1Y %", "3Y %", "5Y %", "since-inception %", "max drawdown %"):
        cell = row[col]
        assert not cell.startswith("+"), f"{col} should be a placeholder, got {cell}"
        assert not cell.startswith("-"), f"{col} should be a placeholder, got {cell}"


def test_benchmark_row_appears_when_benchmark_args_provided():
    price_data = {
        "FOO": {"prices": _daily_series(years=5), "source": "yfinance"},
    }
    benchmark_price_data = {
        "SPY":  {"prices": _daily_series(years=5, start_price=400, end_price=500), "source": "yfinance"},
        "AGG":  {"prices": _daily_series(years=5, start_price=100, end_price=95),  "source": "yfinance"},
        "IBIT": {"prices": _daily_series(years=5, start_price=40,  end_price=60),  "source": "yfinance"},
    }
    df = performance_summary_table(
        tickers=["FOO"],
        price_data=price_data,
        benchmark_weights={"SPY": 0.48, "AGG": 0.32, "IBIT": 0.20},
        benchmark_label="80/20",
        benchmark_price_data=benchmark_price_data,
    )
    # Should have FOO row + one benchmark row
    assert len(df) == 2
    assert df.iloc[0]["ticker"] == "FOO"
    assert "Benchmark" in df.iloc[1]["ticker"]
    assert "80/20" in df.iloc[1]["ticker"]
    # Benchmark row should have real returns (all components are 5Y+)
    assert df.iloc[1]["1Y %"].endswith("%")


def test_benchmark_row_absent_when_no_benchmark_args():
    price_data = {"FOO": {"prices": _daily_series(years=5), "source": "yfinance"}}
    df = performance_summary_table(tickers=["FOO"], price_data=price_data)
    assert len(df) == 1
    assert df.iloc[0]["ticker"] == "FOO"


def test_drawdown_series_produces_expected_max_drawdown():
    """Full pipeline: a series that had a 75% drawdown must show it in the table."""
    price_data = {"CRASH": {"prices": _drawdown_series(), "source": "yfinance"}}
    df = performance_summary_table(tickers=["CRASH"], price_data=price_data)
    dd_str = df.iloc[0]["max drawdown %"]
    # Strip the leading + or - and the trailing %
    dd_val = float(dd_str.rstrip("%"))
    assert -76 < dd_val < -74, f"expected ~−75%, got {dd_str}"
