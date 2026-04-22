"""
data_source_state.py — per-category live/fallback state tracker.

First-class product requirement per Day-3 design directive:
no silent fallback serving. Whenever the app renders data that did not
come from the primary live source, the UI must surface that fact.

Categories tracked:
    "etf_price"          — per-ETF OHLCV
    "etf_reference"      — holdings / AUM / expense ratio
    "risk_free_rate"     — FRED DGS3MO
    "edgar_scanner"      — daily new-listing scan
    (any other string can be registered on the fly)

Four states:
    LIVE           — primary source succeeded on the most recent attempt
    FALLBACK_LIVE  — primary failed; a secondary/tertiary live source succeeded
    CACHED         — all live sources failed; serving last-known cached data
    STATIC         — all live sources failed and there is no cache; serving
                     a hardcoded fallback (e.g., RISK_FREE_RATE_FALLBACK=4.25)

The state transitions driven by register_fetch_attempt():

    register_fetch_attempt("etf_price", "yfinance",       success=True)   → LIVE
    register_fetch_attempt("etf_price", "yfinance",       success=False)
    register_fetch_attempt("etf_price", "stooq",          success=True)   → FALLBACK_LIVE
    register_fetch_attempt("etf_price", "yfinance",       success=False)
    register_fetch_attempt("etf_price", "stooq",          success=False)
    # at this point the caller falls back to cache or static
    mark_cache_hit("etf_price",  age_minutes=7)                           → CACHED
    mark_static_fallback("risk_free_rate", note="FRED unavailable")       → STATIC

State is in-memory only (session-scoped) per CLAUDE.md §11. Safe to
import from any thread; uses a module-level lock.

CLAUDE.md governance: Sections 8 (UX transparency), 10 (fallbacks), 11
(environment-scoped state), 12 (cache handling).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class DataSourceState(str, Enum):
    LIVE = "LIVE"
    FALLBACK_LIVE = "FALLBACK_LIVE"
    CACHED = "CACHED"
    STATIC = "STATIC"
    UNKNOWN = "UNKNOWN"          # category not yet touched this session


# Per-category "which source is primary" map. A fetch that succeeds from
# the primary source resets the category to LIVE. A fetch that succeeds
# from anything else transitions to FALLBACK_LIVE.
_PRIMARY_SOURCE_BY_CATEGORY: dict[str, str] = {
    "etf_price":       "yfinance",
    "etf_reference":   "edgar",
    "etf_composition": "edgar",
    "etf_long_run":    "yfinance",
    "risk_free_rate":  "fred",
    "edgar_scanner":   "edgar",
}


# ═══════════════════════════════════════════════════════════════════════════
# Metric-dependency registry — which user-facing numbers each data-source
# category feeds. The data_source_badge component + the top-of-page
# data_sources_panel use this to tell the FA EXACTLY which metric is
# affected when a source enters fallback.
# ═══════════════════════════════════════════════════════════════════════════

METRIC_DEPENDENCIES: dict[str, list[str]] = {
    "etf_price": [
        "Historical return (annualized)",
        "90-day realized volatility",
        "90-day BTC correlation",
        "30-day basket change (Dashboard)",
        "Historical returns chart (Portfolio + ETF Detail)",
        "Execute Basket last-close prices",
    ],
    "etf_long_run": [
        "Forward estimate (model)",
    ],
    "risk_free_rate": [
        "Sharpe ratio",
        "Sortino ratio",
        "Calmar ratio",
        "Excess return",
    ],
    "etf_composition": [
        "ETF Detail Composition table (per-ticker)",
    ],
    "etf_reference": [
        "Issuer / expense-ratio metadata",
    ],
    "edgar_scanner": [
        "New-listing discovery (daily cron)",
        "Scanner health indicator (Settings)",
    ],
}


def affected_metrics(category: str) -> list[str]:
    """
    Return the list of user-facing metric names that consume this
    data-source category. Used by UI components to name specifically
    which numbers go stale when a category enters fallback.
    Unknown categories return [] rather than raising so the UI
    degrades cleanly.
    """
    return list(METRIC_DEPENDENCIES.get(category, []))


def human_category_label(category: str) -> str:
    """Pretty label for the top-of-page data sources panel."""
    return {
        "etf_price":       "ETF price history (yfinance → Stooq)",
        "etf_long_run":    "Long-run BTC/ETH CAGR (10yr underlying)",
        "risk_free_rate":  "Risk-free rate (FRED 3M T-bill)",
        "etf_composition": "ETF composition (SEC EDGAR N-PORT + issuer)",
        "etf_reference":   "ETF reference data (expense, issuer)",
        "edgar_scanner":   "EDGAR new-listing scanner",
    }.get(category, category)


@dataclass
class _CategoryInfo:
    state: DataSourceState = DataSourceState.UNKNOWN
    source: str = ""
    last_update_ts: float = 0.0   # monotonic of last successful fetch/cache touch
    last_attempt_ts: float = 0.0
    cache_age_seconds_at_mark: Optional[int] = None
    note: str = ""


_state: dict[str, _CategoryInfo] = {}
# RLock (not Lock): snapshot() holds _lock and its dict-comp calls
# get_age_minutes() which also needs _lock. A plain Lock deadlocks.
_lock = threading.RLock()


# ═══════════════════════════════════════════════════════════════════════════
# Internals
# ═══════════════════════════════════════════════════════════════════════════

def _info(category: str) -> _CategoryInfo:
    info = _state.get(category)
    if info is None:
        info = _CategoryInfo()
        _state[category] = info
    return info


# ═══════════════════════════════════════════════════════════════════════════
# Registration API (called from data_feeds.py / portfolio_engine.py /
# etf_universe.py whenever a fetch attempt completes).
# ═══════════════════════════════════════════════════════════════════════════

def register_fetch_attempt(category: str, source: str, success: bool,
                           note: str = "") -> None:
    """
    Record the outcome of a single fetch attempt for `category`.

    On success:
        - If `source` matches the category's primary source → LIVE
        - Otherwise → FALLBACK_LIVE
    On failure:
        - State is NOT changed here (the fallback chain may still succeed).
        - If the caller exhausts the chain and serves cache / static, it
          must follow up with mark_cache_hit() or mark_static_fallback().
    """
    with _lock:
        info = _info(category)
        info.last_attempt_ts = time.time()
        if success:
            primary = _PRIMARY_SOURCE_BY_CATEGORY.get(category)
            info.state = (
                DataSourceState.LIVE
                if source == primary
                else DataSourceState.FALLBACK_LIVE
            )
            info.source = source
            info.last_update_ts = time.time()
            info.cache_age_seconds_at_mark = None
            info.note = note
            logger.debug("DSS [%s] %s via %s", info.state.value, category, source)


def mark_cache_hit(category: str, age_seconds: int, source_name: str = "cache",
                   note: str = "") -> None:
    """Caller served stale data from cache after all live sources failed."""
    with _lock:
        info = _info(category)
        info.state = DataSourceState.CACHED
        info.source = source_name
        info.cache_age_seconds_at_mark = int(max(0, age_seconds))
        info.last_update_ts = time.time()
        info.note = note
        logger.info("DSS CACHED [%s] age=%ds", category, age_seconds)


def mark_static_fallback(category: str, note: str = "") -> None:
    """Caller served a hardcoded static default (no live, no cache)."""
    with _lock:
        info = _info(category)
        info.state = DataSourceState.STATIC
        info.source = "static"
        info.last_update_ts = time.time()
        info.cache_age_seconds_at_mark = None
        info.note = note
        logger.info("DSS STATIC [%s] %s", category, note)


# ═══════════════════════════════════════════════════════════════════════════
# Query API (called from ui/components.py::data_source_badge)
# ═══════════════════════════════════════════════════════════════════════════

def get_state(category: str) -> DataSourceState:
    with _lock:
        return _info(category).state


def get_source(category: str) -> str:
    with _lock:
        return _info(category).source


def get_age_minutes(category: str) -> Optional[int]:
    """
    Minutes since last successful update (or last cache mark, whichever
    is more recent). None if the category was never touched.
    """
    with _lock:
        info = _info(category)
        if info.last_update_ts == 0:
            return None
        if info.state == DataSourceState.CACHED and info.cache_age_seconds_at_mark is not None:
            # Age of the cached data itself, not age of the mark
            age_sec_of_data = info.cache_age_seconds_at_mark + int(time.time() - info.last_update_ts)
            return max(0, age_sec_of_data // 60)
        return max(0, int((time.time() - info.last_update_ts) // 60))


def get_note(category: str) -> str:
    with _lock:
        return _info(category).note


def snapshot() -> dict[str, dict]:
    """Debug / Settings-page helper: full state dump."""
    with _lock:
        return {
            cat: {
                "state":  info.state.value,
                "source": info.source,
                "age_minutes": get_age_minutes(cat),
                "note":   info.note,
            }
            for cat, info in _state.items()
        }


def reset_all() -> None:
    """Test hook + 'Refresh All Data' manual override."""
    with _lock:
        _state.clear()
