"""
data_feeds.py — price + reference data for the ETF universe.

Price chain (fallback order):
    1. yfinance              (primary; free, aggressive cache)
    2. Alpha Vantage         (25 req/day; spot-check fallback only)
    3. Stooq                 (free, ~15-min delayed, reliable tail)

Reference chain (holdings / AUM / expense ratio):
    1. SEC EDGAR             (free, authoritative, 10 req/sec cap)
    2. Issuer sites          (BlackRock / Fidelity / VanEck / etc.)
    3. ETF.com               (public pages; scraping-safe)

Circuit breaker (planning-side Mod 3 / Risk 5):
    yfinance failures (HTTP 429 OR empty result on tickers WE KNOW have
    history) count into a rolling 60-second window. At threshold
    (config.YF_CIRCUIT_BREAKER_THRESHOLD=3), the active price source
    flips to Stooq for the remainder of the session. In-memory state
    only — resets on process restart per CLAUDE.md §11.

    New-ETF empty results (ticker not in the seed universe) do NOT trip
    the breaker — a brand-new listing legitimately has no history yet.

CLAUDE.md governance: Sections 10, 11, 12.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from config import (
    CACHE_TTL,
    ETF_UNIVERSE_SEED,
    YF_CIRCUIT_BREAKER_THRESHOLD,
    YF_CIRCUIT_BREAKER_WINDOW_SEC,
)
from core.data_source_state import register_fetch_attempt

logger = logging.getLogger(__name__)

# Known-history set — only failures on these tickers count against the
# circuit breaker per planning-side Risk 5 direction.
_KNOWN_HISTORY_TICKERS: frozenset[str] = frozenset(
    e["ticker"] for e in ETF_UNIVERSE_SEED
)

# ═══════════════════════════════════════════════════════════════════════════
# Circuit breaker state (session-scoped, in-memory)
# ═══════════════════════════════════════════════════════════════════════════

_circuit_state: dict[str, Any] = {
    "active_source":  "yfinance",
    "failure_times":  deque(),   # monotonic timestamps of recent failures
    "tripped_at":     None,
    "new_etf_misses": 0,         # legitimately empty (new listing) — diagnostic only
}


def get_active_price_source() -> str:
    """
    Return the currently active price source name.
    UI hook on Day 3 surfaces this as a "data source: delayed" indicator
    when value is not "yfinance".
    """
    return _circuit_state["active_source"]


def _record_failure(ticker: str) -> None:
    """
    Record a yfinance failure. Only tickers in _KNOWN_HISTORY_TICKERS
    count against the breaker — brand-new listings legitimately empty.
    """
    if ticker not in _KNOWN_HISTORY_TICKERS:
        _circuit_state["new_etf_misses"] += 1
        logger.info(
            "Empty result for %s (not in seed universe) — treated as new-listing, "
            "not breaker failure. new_etf_misses=%d",
            ticker, _circuit_state["new_etf_misses"],
        )
        return

    now = time.monotonic()
    cutoff = now - YF_CIRCUIT_BREAKER_WINDOW_SEC
    times: deque = _circuit_state["failure_times"]
    times.append(now)
    while times and times[0] < cutoff:
        times.popleft()

    if (
        _circuit_state["active_source"] == "yfinance"
        and len(times) >= YF_CIRCUIT_BREAKER_THRESHOLD
    ):
        _circuit_state["active_source"] = "stooq"
        _circuit_state["tripped_at"] = time.time()
        logger.warning(
            "yfinance circuit breaker TRIPPED (%d failures in %ds) — "
            "flipping to Stooq for rest of session.",
            len(times),
            YF_CIRCUIT_BREAKER_WINDOW_SEC,
        )


def reset_circuit_breaker() -> None:
    """Test hook + manual override for the UI's 'Refresh All Data' button."""
    _circuit_state["active_source"] = "yfinance"
    _circuit_state["failure_times"].clear()
    _circuit_state["tripped_at"] = None
    _circuit_state["new_etf_misses"] = 0


def circuit_breaker_state() -> dict[str, Any]:
    """Diagnostic snapshot — used in tests + Settings page."""
    return {
        "active_source":   _circuit_state["active_source"],
        "failure_count":   len(_circuit_state["failure_times"]),
        "tripped_at":      _circuit_state["tripped_at"],
        "new_etf_misses":  _circuit_state["new_etf_misses"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Price fetch with fallback chain
# ═══════════════════════════════════════════════════════════════════════════

def get_etf_prices(
    tickers: list[str],
    period: str = "1y",
    interval: str = "1d",
) -> dict[str, dict]:
    """
    Fetch OHLCV for a list of tickers. Returns:
      { ticker: {"source": "yfinance"|"stooq"|...,
                 "prices": [{date, open, high, low, close, volume}, ...]} }
    Empty list means no data available (after fallback exhausted).
    Never raises for a single ticker failure — logs and moves on.
    """
    result: dict[str, dict] = {}
    for ticker in tickers:
        result[ticker] = _fetch_single_ticker(ticker, period, interval)
    return result


def _fetch_single_ticker(ticker: str, period: str, interval: str) -> dict:
    source = get_active_price_source()
    if source == "yfinance":
        data = _fetch_yfinance(ticker, period, interval)
        if data:
            register_fetch_attempt("etf_price", "yfinance", success=True)
            return {"source": "yfinance", "prices": data}
        register_fetch_attempt("etf_price", "yfinance", success=False,
                               note=f"{ticker}: empty or failed")
        _record_failure(ticker)
        source = get_active_price_source()

    if source == "stooq":
        data = _fetch_stooq(ticker, period)
        if data:
            register_fetch_attempt("etf_price", "stooq", success=True,
                                   note="fallback chain: primary yfinance unavailable")
            return {"source": "stooq", "prices": data}
        register_fetch_attempt("etf_price", "stooq", success=False,
                               note=f"{ticker}: stooq returned empty")

    data = _fetch_alphavantage(ticker)
    if data:
        register_fetch_attempt("etf_price", "alphavantage", success=True,
                               note="fallback chain: yfinance + stooq unavailable")
        return {"source": "alphavantage", "prices": data}

    register_fetch_attempt("etf_price", "none", success=False,
                           note=f"{ticker}: all live sources exhausted")
    return {"source": "unavailable", "prices": []}


def _fetch_yfinance(ticker: str, period: str, interval: str) -> list[dict]:
    """Primary source. Returns [] on empty / failure (caller records miss)."""
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if df is None or df.empty:
            return []
        return _df_to_rows(df)
    except Exception as exc:
        logger.info("yfinance failed for %s: %s", ticker, exc)
        return []


def _fetch_stooq(ticker: str, period: str) -> list[dict]:
    """Tertiary source — free, delayed ~15min. Stooq uses lowercase + '.us' suffix."""
    try:
        import requests
        symbol = ticker.lower() + ".us"
        url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
        resp = requests.get(url, timeout=10,
                            headers={"User-Agent": "ETF-Advisor-Platform/0.1"})
        if resp.status_code != 200 or not resp.text.strip():
            return []
        lines = resp.text.strip().splitlines()
        if len(lines) < 2:
            return []
        header = [c.strip().lower() for c in lines[0].split(",")]
        idx = {col: header.index(col) for col in ("date", "open", "high", "low", "close", "volume")
               if col in header}
        rows = []
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) < 5:
                continue
            try:
                rows.append({
                    "date":   parts[idx["date"]],
                    "open":   float(parts[idx["open"]])   if "open"   in idx else None,
                    "high":   float(parts[idx["high"]])   if "high"   in idx else None,
                    "low":    float(parts[idx["low"]])    if "low"    in idx else None,
                    "close":  float(parts[idx["close"]]),
                    "volume": float(parts[idx["volume"]]) if "volume" in idx else None,
                })
            except (ValueError, KeyError):
                continue
        return rows
    except Exception as exc:
        logger.info("Stooq failed for %s: %s", ticker, exc)
        return []


def _fetch_alphavantage(ticker: str) -> list[dict]:
    """
    Last-resort: Alpha Vantage free tier — 25 req/day. Requires API key.
    Returns [] if no key configured or if the call fails.
    """
    from config import ALPHA_VANTAGE_API_KEY
    if not ALPHA_VANTAGE_API_KEY:
        return []
    try:
        import requests
        url = (
            "https://www.alphavantage.co/query"
            f"?function=TIME_SERIES_DAILY&symbol={ticker}"
            f"&outputsize=compact&apikey={ALPHA_VANTAGE_API_KEY}"
        )
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        series = data.get("Time Series (Daily)")
        if not series:
            return []
        rows = []
        for date_str, bar in series.items():
            rows.append({
                "date":   date_str,
                "open":   float(bar.get("1. open", 0)),
                "high":   float(bar.get("2. high", 0)),
                "low":    float(bar.get("3. low", 0)),
                "close":  float(bar.get("4. close", 0)),
                "volume": float(bar.get("5. volume", 0)),
            })
        rows.sort(key=lambda r: r["date"])
        return rows
    except Exception as exc:
        logger.info("Alpha Vantage failed for %s: %s", ticker, exc)
        return []


def _df_to_rows(df: Any) -> list[dict]:
    """Convert a yfinance history DataFrame to our row-dict format."""
    rows = []
    for ts, row in df.iterrows():
        rows.append({
            "date":   ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts),
            "open":   float(row.get("Open",  0)) if "Open"  in df.columns else None,
            "high":   float(row.get("High",  0)) if "High"  in df.columns else None,
            "low":    float(row.get("Low",   0)) if "Low"   in df.columns else None,
            "close":  float(row.get("Close", 0)) if "Close" in df.columns else None,
            "volume": float(row.get("Volume", 0)) if "Volume" in df.columns else None,
        })
    return rows


# ═══════════════════════════════════════════════════════════════════════════
# Reference data (holdings / AUM / expense ratio)
# ═══════════════════════════════════════════════════════════════════════════

# Phase-1: returns the seed expense_ratio_bps dict baked into etf_universe.py.
# Day-3 will wire this to EDGAR N-PORT parsing. Stub here so the fallback
# chain shape is locked in from Day 2.

def get_etf_reference(ticker: str) -> dict | None:
    """
    Fetch reference data for a single ETF.
    Phase-1 placeholder: returns the seed entry + known expense ratio.
    Day-3 wires EDGAR / issuer / ETF.com in that fallback order.
    """
    for etf in ETF_UNIVERSE_SEED:
        if etf["ticker"].upper() == ticker.upper():
            return dict(etf)
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Cache TTL surface (imported by @st.cache_data decorators on Day 3)
# ═══════════════════════════════════════════════════════════════════════════

def ttl_for(key: str) -> int:
    """Return the configured cache TTL in seconds, with fallback."""
    return int(CACHE_TTL.get(key, CACHE_TTL.get("empty_result", 30)))
