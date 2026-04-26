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
import os
import time
from collections import deque
from typing import Any

from config import (
    CACHE_TTL,
    ETF_UNIVERSE_SEED,
    YF_CIRCUIT_BREAKER_THRESHOLD,
    YF_CIRCUIT_BREAKER_WINDOW_SEC,
)
from core.data_source_state import (
    mark_cache_hit,
    mark_static_fallback,
    register_fetch_attempt,
)

logger = logging.getLogger(__name__)

# Known-history set — only failures on these tickers count against the
# circuit breaker. The breaker design (planning-side Mod 3 / Risk 5)
# treats failures on "new listings" as legitimate misses (no history
# yet) so they don't trip the breaker. We need to honor that for the
# 2025-2026 altcoin spot ETFs (BSOL, FSOL, SSOL, XRPC, LTCO, HBR,
# AVAX, ADAX, etc.) — yfinance often hasn't picked them up yet, so
# every cold boot was tripping the breaker on the first 3 fetches
# of these tickers and putting the whole session into Stooq fallback.
#
# Heuristic: a ticker counts as "known history" only if its inception
# date is ≥ 12 months in the past. Anything newer is exempt.
_HISTORY_AGE_THRESHOLD_DAYS: int = 365


def _known_history_set() -> frozenset[str]:
    """
    Returns the set of tickers that ARE expected to have yfinance
    history. Failures on these count toward the circuit breaker.
    Failures on tickers OUTSIDE this set are treated as legitimate
    new-listing misses (don't trip the breaker).

    Best source: the precomputed analytics snapshot. If a ticker has
    a successful CAGR entry there, yfinance has confirmed it knows
    the ticker. Anything missing from the snapshot is yfinance-
    unindexed (the 12 newly-launched altcoin spots + a few older
    niche funds like EFBC / MARL that yfinance just doesn't have).

    Fallback hierarchy:
      1. Precomputed snapshot's tickers-with-data (best — confirmed)
      2. JSON registry filtered by inception ≤ 12 months (heuristic)
      3. Legacy ETF_UNIVERSE_SEED (emergency)
    """
    from datetime import datetime, timedelta, timezone

    # Tier 1 — snapshot is authoritative if it exists and is fresh.
    try:
        from core.etf_universe import _load_precomputed_analytics
        snap = _load_precomputed_analytics()
        if snap:
            etfs = snap.get("etfs", {})
            confirmed = {
                t for t, info in etfs.items()
                if info.get("expected_return_source") == "live"
            }
            if confirmed:
                return frozenset(confirmed)
    except Exception:  # pragma: no cover — defensive
        pass

    # Tier 2 — JSON registry filtered by inception age.
    try:
        from core.etf_universe import _load_registry_from_disk
        reg = _load_registry_from_disk()
        if reg:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                days=_HISTORY_AGE_THRESHOLD_DAYS
            )
            keep: set[str] = set()
            for e in reg:
                tkr = e.get("ticker")
                if not tkr:
                    continue
                inception_str = e.get("inception", "")
                try:
                    inception = datetime.fromisoformat(inception_str.split("T")[0])
                except (ValueError, AttributeError):
                    keep.add(tkr)
                    continue
                if inception <= cutoff:
                    keep.add(tkr)
            return frozenset(keep)
    except Exception:  # pragma: no cover — defensive
        pass

    # Tier 3 — emergency seed.
    return frozenset(e["ticker"] for e in ETF_UNIVERSE_SEED)


_KNOWN_HISTORY_TICKERS: frozenset[str] = _known_history_set()

# Day-4 Risk 2 mitigation: module-level memo of last-good fetch per ticker.
# Keyed on (ticker, period, interval). Reduces yfinance hits during rapid
# tier-switching in the UI. TTL respects CACHE_TTL["etf_price_market"].
_yf_memo: dict[tuple[str, str, str], dict] = {}

# Last-close cache for the Execute Basket modal (Day-4 item D). Populated
# automatically by get_etf_prices; read by get_last_close().
_last_close: dict[str, float] = {}

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

    Test-harness short-circuit: when DEMO_MODE_NO_FETCH=1 is set in the
    environment (set by tests/test_smoke.py at session start), bypass the
    yfinance → Stooq fallback chain entirely and return the empty/
    unavailable shape. This keeps Streamlit AppTest renders deterministic
    and under the AppTest timeout — without this short-circuit the
    universe loader hits ~80 yfinance calls during page-render tests,
    which trips the 10s default and times out (real time ~25s + circuit
    breaker).
    """
    if os.environ.get("DEMO_MODE_NO_FETCH") == "1":
        return {t: {"source": "unavailable", "prices": []} for t in tickers}

    result: dict[str, dict] = {}
    for ticker in tickers:
        result[ticker] = _fetch_single_ticker(ticker, period, interval)
    return result


def _fetch_single_ticker(ticker: str, period: str, interval: str) -> dict:
    # Module-level memo — Day-4 Risk 2 mitigation for yfinance dev throttling.
    memo_key = (ticker.upper(), period, interval)
    memo_hit = _yf_memo.get(memo_key)
    if memo_hit is not None:
        age_sec = int(time.monotonic() - memo_hit["_mono"])
        ttl = CACHE_TTL.get("etf_price_market", 300)
        if age_sec < ttl:
            return {k: v for k, v in memo_hit.items() if not k.startswith("_")}

    source = get_active_price_source()
    if source == "yfinance":
        data = _fetch_yfinance(ticker, period, interval)
        if data:
            register_fetch_attempt("etf_price", "yfinance", success=True)
            _update_last_close(ticker, data)
            result = {"source": "yfinance", "prices": data}
            _yf_memo[memo_key] = {**result, "_mono": time.monotonic()}
            return result
        register_fetch_attempt("etf_price", "yfinance", success=False,
                               note=f"{ticker}: empty or failed")
        _record_failure(ticker)
        source = get_active_price_source()

    if source == "stooq":
        data = _fetch_stooq(ticker, period)
        if data:
            register_fetch_attempt("etf_price", "stooq", success=True,
                                   note="fallback chain: primary yfinance unavailable")
            _update_last_close(ticker, data)
            result = {"source": "stooq", "prices": data}
            _yf_memo[memo_key] = {**result, "_mono": time.monotonic()}
            return result
        register_fetch_attempt("etf_price", "stooq", success=False,
                               note=f"{ticker}: stooq returned empty")

    # Alpha Vantage intentionally NOT in the active chain. Free tier is
    # 25 req/day — insufficient for even one user across the 19-ETF
    # universe, so it is a false fallback that fails silently under
    # real load. _fetch_alphavantage() + ALPHA_VANTAGE_API_KEY config
    # are retained as pre-architected scaffolding per CLAUDE.md §12 so
    # a paid-tier upgrade (75 req/min, 10k/day) can re-enable in four
    # lines. See docs/streamlit_cloud_deploy.md.

    register_fetch_attempt("etf_price", "none", success=False,
                           note=f"{ticker}: all live sources exhausted")
    return {"source": "unavailable", "prices": []}


def _update_last_close(ticker: str, prices: list[dict]) -> None:
    """Persist the most recent close for the Execute Basket modal."""
    if not prices:
        return
    try:
        last = float(prices[-1].get("close", 0))
        if last > 0:
            _last_close[ticker.upper()] = last
    except (ValueError, TypeError, KeyError):
        pass


def get_last_close(ticker: str) -> float | None:
    """Return the most recently cached close for a ticker, or None.

    Test-harness short-circuit: under DEMO_MODE_NO_FETCH=1, return None
    immediately so callers (e.g. ETF Detail hero card) render their
    placeholder dashes without triggering any cached-state side effects.
    """
    if os.environ.get("DEMO_MODE_NO_FETCH") == "1":
        return None
    return _last_close.get(ticker.upper())


# Sanity cap for CAGR derivation. Crypto can legitimately 3-6x in a year,
# so we do NOT want a tight cap — this only filters data-error artifacts
# like unadjusted splits or dust-start prices. ±300% = a 4x move per year
# sustained over the full lookback window.
_CAGR_CAP_PCT: float = 300.0


def get_historical_cagr(ticker: str, period: str = "5y") -> dict:
    """
    Annualized return from the earliest to the most recent available close,
    as a percent. Shape:
        {"cagr_pct": float|None, "source": str, "days_observed": int}
    Returns cagr_pct=None if fewer than ~30 valid closes are available or
    the CAGR math can't be computed. source mirrors the price-bundle
    source ("yfinance" / "stooq" / "unavailable") so callers can register
    data-source-state correctly.
    """
    from datetime import datetime

    bundle = get_etf_prices([ticker], period=period, interval="1d")
    entry = bundle.get(ticker, {}) or {}
    rows = entry.get("prices", []) or []
    source = entry.get("source", "unavailable")

    closes: list[tuple[str, float]] = []
    for row in rows:
        try:
            c = float(row.get("close"))
            if c > 0:
                closes.append((str(row.get("date", "")), c))
        except (TypeError, ValueError):
            continue

    if len(closes) < 30:
        return {"cagr_pct": None, "source": source, "days_observed": len(closes)}

    start_date_raw, start_close = closes[0]
    end_date_raw, end_close = closes[-1]

    try:
        start_dt = datetime.fromisoformat(start_date_raw.split("T")[0])
        end_dt = datetime.fromisoformat(end_date_raw.split("T")[0])
        days = (end_dt - start_dt).days
    except (ValueError, AttributeError):
        days = int(len(closes) * 365 / 252)

    if days < 30 or start_close <= 0:
        return {"cagr_pct": None, "source": source, "days_observed": days}

    years = days / 365.25
    try:
        ratio = end_close / start_close
        if ratio <= 0:
            return {"cagr_pct": None, "source": source, "days_observed": days}
        cagr = (ratio ** (1.0 / years)) - 1.0
    except (ValueError, ZeroDivisionError, OverflowError):
        return {"cagr_pct": None, "source": source, "days_observed": days}

    cagr_pct = max(-_CAGR_CAP_PCT, min(_CAGR_CAP_PCT, cagr * 100.0))
    return {"cagr_pct": cagr_pct, "source": source, "days_observed": days}


# ═══════════════════════════════════════════════════════════════════════════
# Premium / discount to NAV — partner feedback #4 (Apr 2026)
# ═══════════════════════════════════════════════════════════════════════════

def get_premium_discount_pct(ticker: str) -> dict:
    """
    Fetch the current market-price-vs-NAV premium/discount for an ETF.

    Returns:
        {"premium_discount_pct": float|None,
         "nav": float|None, "market_price": float|None,
         "source": str}

    Positive number = market trades above NAV (premium).
    Negative number = market trades below NAV (discount).
    For spot BTC/ETH ETFs with efficient AP arbitrage, |prem/disc|
    should stay < 0.5% during market hours. GBTC's notorious 40%+
    discount in 2022 is the tail-risk case this metric surfaces.

    Source priority:
        1. yfinance Ticker.info["navPrice"] (free, no key)
        2. None if unavailable — caller shows "—"
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        nav = info.get("navPrice") or info.get("nav")
        # Market price: use previous close as a stable anchor so the
        # premium reading doesn't jitter intraday with every tick.
        market = info.get("previousClose") or info.get("regularMarketPrice")
        if nav is None or market is None or nav <= 0:
            return {"premium_discount_pct": None, "nav": None,
                    "market_price": None, "source": "unavailable"}
        pd_pct = ((float(market) - float(nav)) / float(nav)) * 100.0
        return {"premium_discount_pct": round(pd_pct, 3),
                "nav": float(nav),
                "market_price": float(market),
                "source": "yfinance_info"}
    except Exception as exc:
        logger.info("premium/discount fetch failed for %s: %s", ticker, exc)
        return {"premium_discount_pct": None, "nav": None,
                "market_price": None, "source": "unavailable"}


# ═══════════════════════════════════════════════════════════════════════════
# Upside / downside capture vs underlying — partner feedback #5
# ═══════════════════════════════════════════════════════════════════════════

def get_capture_ratios(
    ticker: str,
    underlying_symbol: str = "BTC-USD",
    lookback_days: int = 252,
) -> dict:
    """
    Classic Morningstar-style capture ratios vs the ETF's underlying
    coin. Classify each trading day by sign of the underlying's
    return; then:

        up_capture   = Σ(fund_ret_on_up_days)   / Σ(underlying_ret_on_up_days)
        down_capture = Σ(fund_ret_on_down_days) / Σ(underlying_ret_on_down_days)

    A well-tracking spot ETF (IBIT, FBTC) should land near 99/99.
    A futures-based ETF (BITO) typically shows up-capture < 95%
    because contango erodes on the way up. A 2x leveraged ETF
    (BITX, ETHU) should show both > 150% with vol-decay slippage.

    Returns:
        {"up_capture_pct": float|None, "down_capture_pct": float|None,
         "n_up_days": int, "n_down_days": int,
         "source": str, "underlying": str}
    """
    import math
    period = f"{max(60, int(lookback_days * 1.25))}d"
    fund_bundle  = get_etf_prices([ticker], period=period, interval="1d")
    under_bundle = get_etf_prices([underlying_symbol], period=period, interval="1d")
    fund_entry  = fund_bundle.get(ticker, {}) or {}
    under_entry = under_bundle.get(underlying_symbol, {}) or {}

    ret_f, ret_u = _aligned_log_returns(fund_entry, under_entry)
    # Trim to the trailing lookback_days window
    ret_f = ret_f[-lookback_days:]
    ret_u = ret_u[-lookback_days:]

    if len(ret_f) < _MIN_RETURNS_FOR_STATS or len(ret_u) < _MIN_RETURNS_FOR_STATS:
        return {"up_capture_pct": None, "down_capture_pct": None,
                "n_up_days": 0, "n_down_days": 0,
                "source": fund_entry.get("source", "unavailable"),
                "underlying": underlying_symbol}

    sum_f_up, sum_u_up, n_up = 0.0, 0.0, 0
    sum_f_dn, sum_u_dn, n_dn = 0.0, 0.0, 0
    for fr, ur in zip(ret_f, ret_u):
        if ur > 0:
            sum_f_up += fr
            sum_u_up += ur
            n_up += 1
        elif ur < 0:
            sum_f_dn += fr
            sum_u_dn += ur
            n_dn += 1

    up_cap = (sum_f_up / sum_u_up * 100.0) if sum_u_up > 1e-9 else None
    dn_cap = (sum_f_dn / sum_u_dn * 100.0) if sum_u_dn < -1e-9 else None
    return {
        "up_capture_pct":   round(up_cap, 1) if up_cap is not None else None,
        "down_capture_pct": round(dn_cap, 1) if dn_cap is not None else None,
        "n_up_days":   n_up,
        "n_down_days": n_dn,
        "source":     fund_entry.get("source", "unavailable"),
        "underlying": underlying_symbol,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Realized volatility + BTC correlation (Q2 — live ETF analytics)
# ═══════════════════════════════════════════════════════════════════════════
#
# Both derive from the same daily-close series we already fetch for CAGR,
# so cost is effectively zero — we piggyback on the _yf_memo cache. These
# replace the category-default `volatility` and `correlation_with_btc`
# values that portfolio_engine has been consuming up to now.
#
# BTC proxy = IBIT (most liquid spot BTC ETF with full 2024+ history).
# Falls back to FBTC if IBIT history is empty for any reason.
_BTC_PROXY_TICKER: str = "IBIT"
_BTC_PROXY_FALLBACK: str = "FBTC"
_VOL_TRADING_DAYS: int = 252
_MIN_RETURNS_FOR_STATS: int = 30


def _daily_log_returns_from_bundle(bundle: dict) -> list[float]:
    """Extract positive-close daily log returns from a price bundle."""
    import math
    closes: list[float] = []
    for row in bundle.get("prices", []) or []:
        try:
            c = float(row.get("close"))
            if c > 0:
                closes.append(c)
        except (TypeError, ValueError):
            continue
    returns: list[float] = []
    for prev, curr in zip(closes[:-1], closes[1:]):
        if prev > 0 and curr > 0:
            returns.append(math.log(curr / prev))
    return returns


def _aligned_log_returns(
    bundle_a: dict, bundle_b: dict,
) -> tuple[list[float], list[float]]:
    """
    Intersect two price bundles by date and return aligned log-return
    arrays. Used only by get_btc_correlation; handles the real-world
    case where listing dates differ (e.g., ETHA vs IBIT).
    """
    import math

    def _date_to_close(bundle: dict) -> dict[str, float]:
        out: dict[str, float] = {}
        for row in bundle.get("prices", []) or []:
            try:
                c = float(row.get("close"))
                d = str(row.get("date", ""))
                if c > 0 and d:
                    out[d.split("T")[0]] = c
            except (TypeError, ValueError):
                continue
        return out

    map_a = _date_to_close(bundle_a)
    map_b = _date_to_close(bundle_b)
    common_dates = sorted(set(map_a) & set(map_b))
    if len(common_dates) < _MIN_RETURNS_FOR_STATS + 1:
        return ([], [])

    closes_a = [map_a[d] for d in common_dates]
    closes_b = [map_b[d] for d in common_dates]

    ret_a: list[float] = []
    ret_b: list[float] = []
    for p_a, c_a, p_b, c_b in zip(closes_a[:-1], closes_a[1:],
                                   closes_b[:-1], closes_b[1:]):
        if p_a > 0 and c_a > 0 and p_b > 0 and c_b > 0:
            ret_a.append(math.log(c_a / p_a))
            ret_b.append(math.log(c_b / p_b))
    return (ret_a, ret_b)


def get_realized_volatility(ticker: str, lookback_days: int = 90) -> dict:
    """
    Annualized realized volatility as a percent, derived from daily log
    returns over the trailing `lookback_days` sessions. Shape:
        {"volatility_pct": float|None, "source": str, "n_returns": int}
    Returns None if fewer than _MIN_RETURNS_FOR_STATS daily returns are
    available. source mirrors the price-bundle source.
    """
    import statistics

    # ~1.25x lookback in calendar days to allow for weekends / holidays
    period = f"{max(90, int(lookback_days * 1.6))}d"
    bundle = get_etf_prices([ticker], period=period, interval="1d")
    entry = bundle.get(ticker, {}) or {}
    source = entry.get("source", "unavailable")

    returns = _daily_log_returns_from_bundle(entry)[-lookback_days:]
    if len(returns) < _MIN_RETURNS_FOR_STATS:
        return {"volatility_pct": None, "source": source, "n_returns": len(returns)}

    try:
        daily_std = statistics.stdev(returns)
    except statistics.StatisticsError:
        return {"volatility_pct": None, "source": source, "n_returns": len(returns)}

    annualized_pct = daily_std * (_VOL_TRADING_DAYS ** 0.5) * 100.0
    return {"volatility_pct": annualized_pct, "source": source,
            "n_returns": len(returns)}


_LONG_RUN_CAGR_MEMO: dict[str, tuple[float | None, float]] = {}
_LONG_RUN_CAGR_TTL_SEC: int = 24 * 3600   # daily refresh is plenty


def get_long_run_cagr(symbol: str, period: str = "10y") -> dict:
    """
    Long-run annualized CAGR for a reference symbol (e.g., "BTC-USD",
    "ETH-USD") over 10 years (or as much history as yfinance provides).

    Used by the Portfolio forward-return estimate — short-term CAGR
    from 2-year-old ETF launches (IBIT, ETHA) is too regime-dependent
    to be a meaningful forward estimate. 10-year BTC-USD / ETH-USD
    smooths across full market cycles and gives a calibrated long-run
    baseline.

    Shape: {"cagr_pct": float|None, "source": str, "days_observed": int}
    Cache: module-level dict with 24-hour TTL — these numbers barely
    move day-to-day, no reason to re-fetch every page load.
    """
    import time as _time
    import datetime as _dt

    cached = _LONG_RUN_CAGR_MEMO.get(symbol)
    if cached is not None:
        cagr_pct, cached_ts = cached
        if _time.monotonic() - cached_ts < _LONG_RUN_CAGR_TTL_SEC:
            return {"cagr_pct": cagr_pct, "source": "cached_long_run",
                    "days_observed": 0}

    bundle = get_etf_prices([symbol], period=period, interval="1d")
    entry = bundle.get(symbol, {}) or {}
    rows = entry.get("prices", []) or []
    source = entry.get("source", "unavailable")

    closes: list[tuple[str, float]] = []
    for row in rows:
        try:
            c = float(row.get("close"))
            if c > 0:
                closes.append((str(row.get("date", "")), c))
        except (TypeError, ValueError):
            continue

    if len(closes) < 365:  # need at least ~1 year for a long-run reading
        return {"cagr_pct": None, "source": source,
                "days_observed": len(closes)}

    start_date_raw, start_close = closes[0]
    end_date_raw, end_close = closes[-1]
    try:
        start_dt = _dt.datetime.fromisoformat(start_date_raw.split("T")[0])
        end_dt = _dt.datetime.fromisoformat(end_date_raw.split("T")[0])
        days = (end_dt - start_dt).days
    except (ValueError, AttributeError):
        days = int(len(closes) * 365 / 252)

    if days < 365 or start_close <= 0:
        return {"cagr_pct": None, "source": source, "days_observed": days}

    years = days / 365.25
    try:
        ratio = end_close / start_close
        if ratio <= 0:
            return {"cagr_pct": None, "source": source,
                    "days_observed": days}
        cagr = (ratio ** (1.0 / years)) - 1.0
    except (ValueError, ZeroDivisionError, OverflowError):
        return {"cagr_pct": None, "source": source, "days_observed": days}

    cagr_pct = max(-_CAGR_CAP_PCT, min(_CAGR_CAP_PCT, cagr * 100.0))
    _LONG_RUN_CAGR_MEMO[symbol] = (cagr_pct, _time.monotonic())
    return {"cagr_pct": cagr_pct, "source": source, "days_observed": days}


# Forward-return estimate per ETF category. Uses long-run BTC-USD and
# ETH-USD CAGR as the underlying-asset expected return (smooths across
# 2021 ATH, 2022 drawdown, 2023 recovery, 2024 halving rally), then
# applies category-specific adjustments. This is the MODEL forward
# estimate displayed alongside the 1-2yr historical CAGR so the FA
# sees both "what it did" and "what the long-run underlying suggests."
#
# Adjustments (rough, calibrated to observable drag/premium):
#   btc_spot     → BTC long-run CAGR × 0.99  (tiny expense-ratio drag)
#   eth_spot     → ETH long-run CAGR × 0.99
#   btc_futures  → BTC long-run CAGR × 0.90  (contango / roll drag ~10%)
#   thematic     → weighted avg (60% BTC, 40% ETH) × 1.10 (equity beta
#                  premium for miner/infra exposure)

def get_forward_return_estimate(
    category: str,
    expense_ratio_bps: int | None = None,
    underlying: str | None = None,
) -> dict:
    """
    Model forward-return estimate per ETF category. Returns:
        {"forward_return_pct": float|None,
         "source": "live_long_run" | "unavailable",
         "basis": str — human-readable derivation}

    `underlying` (from the universe registry) picks the correct
    reference asset for categories where it matters:
        leveraged:            ETHU → ETH long-run, BITX → BTC, etc.
        altcoin_spot:         each altcoin proxies through BTC × 0.70
                              (alts historically underperform BTC long-run;
                              using BTC as anchor is honest here)
        income_covered_call:  ETHI → ETH × 0.55, BTCI → BTC × 0.55, etc.
    If `underlying` is unset / not mappable, falls back to BTC.
    """
    btc_info = get_long_run_cagr("BTC-USD", period="10y")
    eth_info = get_long_run_cagr("ETH-USD", period="10y")
    btc_cagr = btc_info.get("cagr_pct")
    eth_cagr = eth_info.get("cagr_pct")

    # ── 2026-04-26 math audit (Bucket 2) ────────────────────────────────────
    # Previously every altcoin (SOL / XRP / LTC / DOGE / HBAR / ADA / AVAX)
    # was modeled identically as BTC × 0.70 — a uniform haircut that buried
    # significant per-coin variance. SOL has a strong 5yr CAGR; XRP / LTC
    # have flat-to-mid; DOGE has high but extremely volatile. Treating them
    # as a homogenous bucket was unfair to the better-track-record alts and
    # generous to the worse ones.
    #
    # New approach: try yfinance for the per-coin spot ticker (`<COIN>-USD`)
    # first. When data exists, use the actual long-run CAGR with no haircut
    # — the data already reflects every drawdown the coin has experienced.
    # When data is unavailable (newer launches, ticker not on yfinance),
    # fall back to BTC × 0.70 with the basis string explicitly noting the
    # fallback path. The Methodology page documents both paths.
    _ALTCOIN_YFINANCE_TICKER: dict[str, str] = {
        "SOL":   "SOL-USD",
        "XRP":   "XRP-USD",
        "LTC":   "LTC-USD",
        "DOGE":  "DOGE-USD",
        "ADA":   "ADA-USD",
        "AVAX":  "AVAX-USD",
        "HBAR":  "HBAR-USD",
        "DOT":   "DOT-USD",
        "LINK":  "LINK-USD",
    }

    def _altcoin_cagr_or_none(coin_symbol: str) -> tuple[float | None, str]:
        """Per-altcoin CAGR. Returns (cagr_pct, label_or_reason)."""
        coin = (coin_symbol or "").upper()
        yticker = _ALTCOIN_YFINANCE_TICKER.get(coin)
        if not yticker:
            return (None, f"{coin} not in yfinance map")
        info = get_long_run_cagr(yticker, period="10y")
        cagr = info.get("cagr_pct")
        if cagr is None:
            return (None, f"{yticker} long-run history unavailable")
        return (cagr, f"{yticker} long-run")

    def _underlying_cagr() -> tuple[float | None, str]:
        """
        Pick the correct long-run CAGR for the fund's actual underlying.
        Returns (cagr_pct, reference_label). Handles None gracefully.

        2026-04-26: now resolves per-altcoin underlyings (SOL / XRP / LTC /
        DOGE / ADA / AVAX / HBAR / DOT / LINK) via yfinance instead of
        always falling back to BTC. Used by leveraged + income_covered_call
        wrappers that target altcoin underlyings (SOLT, XRPT, etc.) so
        their forward-return estimate reflects the actual underlying coin
        rather than BTC-as-proxy.
        """
        u = (underlying or "").upper()
        # ETH-based wrappers (ETHU / ETHT / ETHI / YETH / ETHY)
        if u in ("ETH", "ETHA", "FETH"):
            return (eth_cagr, "ETH-USD 10yr")
        # BTC-based wrappers (BITX / BITU / BTCL / BTCI / YBIT / IBIY)
        if u in ("BTC", "IBIT", "FBTC"):
            return (btc_cagr, "BTC-USD 10yr")
        # Equity-underlying wrappers (MSTR / COIN / MARA / RIOT) —
        # high-beta BTC proxies, modeled at BTC × 1.0 baseline. The
        # category-level multiplier (e.g., leveraged 1.40) still applies.
        if u in ("MSTR", "COIN", "MARA", "RIOT"):
            return (btc_cagr, f"BTC-USD 10yr (as {u} proxy)")
        # Per-altcoin lookup — use the coin's own long-run CAGR when
        # yfinance has it. Fairer than the old BTC-fallback.
        if u in _ALTCOIN_YFINANCE_TICKER:
            alt_cagr, alt_label = _altcoin_cagr_or_none(u)
            if alt_cagr is not None:
                return (alt_cagr, alt_label)
            # No live history — fall through to BTC anchor with a clear
            # label so the basis string explains the fallback.
            return (btc_cagr, f"BTC-USD 10yr (fallback — {alt_label})")
        # Default to BTC as the crypto-asset anchor.
        return (btc_cagr, "BTC-USD 10yr")

    # Expense-ratio drag converted to decimal (25bps → 0.0025).
    er_drag = (expense_ratio_bps or 0) / 10000.0

    if category == "btc_spot":
        if btc_cagr is None:
            return {"forward_return_pct": None, "source": "unavailable",
                    "basis": "BTC-USD long-run history unavailable"}
        fwd = btc_cagr * 0.99 - er_drag * 100.0
        return {"forward_return_pct": fwd, "source": "live_long_run",
                "basis": f"BTC-USD 10yr CAGR ({btc_cagr:.1f}%) "
                         f"minus expense drag ({er_drag*100:.2f}%)"}

    if category == "eth_spot":
        if eth_cagr is None:
            return {"forward_return_pct": None, "source": "unavailable",
                    "basis": "ETH-USD long-run history unavailable"}
        fwd = eth_cagr * 0.99 - er_drag * 100.0
        return {"forward_return_pct": fwd, "source": "live_long_run",
                "basis": f"ETH-USD long-run CAGR ({eth_cagr:.1f}%) "
                         f"minus expense drag ({er_drag*100:.2f}%)"}

    if category == "btc_futures":
        if btc_cagr is None:
            return {"forward_return_pct": None, "source": "unavailable",
                    "basis": "BTC-USD long-run history unavailable"}
        # 10% futures drag (contango/roll) + expense drag
        fwd = btc_cagr * 0.90 - er_drag * 100.0
        return {"forward_return_pct": fwd, "source": "live_long_run",
                "basis": f"BTC-USD 10yr CAGR ({btc_cagr:.1f}%) × 0.90 "
                         f"for contango/roll drag, minus expenses"}

    if category in ("thematic", "thematic_equity"):
        if btc_cagr is None and eth_cagr is None:
            return {"forward_return_pct": None, "source": "unavailable",
                    "basis": "Reference assets unavailable"}
        btc_part = (btc_cagr or 0) * 0.60
        eth_part = (eth_cagr or 0) * 0.40
        base = btc_part + eth_part
        # Equity-beta premium: thematic crypto equities (miners, brokers,
        # Coinbase, etc.) historically carry beta ~1.3-1.6 vs underlying
        # crypto but with equity-market drag. Use 1.10 conservative.
        fwd = base * 1.10 - er_drag * 100.0
        return {"forward_return_pct": fwd, "source": "live_long_run",
                "basis": f"60% BTC + 40% ETH long-run CAGR × 1.10 "
                         f"(equity-beta premium), minus expenses"}

    if category == "eth_futures":
        if eth_cagr is None:
            return {"forward_return_pct": None, "source": "unavailable",
                    "basis": "ETH-USD long-run history unavailable"}
        fwd = eth_cagr * 0.90 - er_drag * 100.0
        return {"forward_return_pct": fwd, "source": "live_long_run",
                "basis": f"ETH-USD long-run CAGR ({eth_cagr:.1f}%) × 0.90 "
                         f"for contango/roll drag, minus expenses"}

    if category == "altcoin_spot":
        # 2026-04-26 math-audit fix: per-altcoin CAGR from yfinance when
        # available (SOL-USD / XRP-USD / LTC-USD / DOGE-USD / ADA-USD /
        # AVAX-USD / HBAR-USD / DOT-USD / LINK-USD). Each coin's actual
        # long-run track record drives its forward estimate, NO uniform
        # 0.70 haircut. The data already encodes drawdowns + dilution.
        # Falls back to BTC × 0.70 only when the per-coin history isn't
        # available (newer launches, ticker not on yfinance) — basis
        # string makes the path used explicit so the FA can audit.
        u = (underlying or "").upper()
        if u and u in _ALTCOIN_YFINANCE_TICKER:
            alt_cagr, alt_label = _altcoin_cagr_or_none(u)
            if alt_cagr is not None:
                fwd = alt_cagr - er_drag * 100.0
                return {
                    "forward_return_pct": fwd, "source": "live_long_run",
                    "basis": f"{alt_label} CAGR ({alt_cagr:.1f}%) "
                             f"— per-coin (no uniform haircut), minus expenses",
                }
            # Per-coin data unavailable — fall through to BTC × 0.70
            # but flag the fallback in the basis string.
            if btc_cagr is None:
                return {"forward_return_pct": None, "source": "unavailable",
                        "basis": f"{u} history unavailable AND BTC fallback unavailable"}
            fwd = btc_cagr * 0.70 - er_drag * 100.0
            return {
                "forward_return_pct": fwd, "source": "live_long_run",
                "basis": f"BTC-USD 10yr CAGR ({btc_cagr:.1f}%) × 0.70 "
                         f"(fallback — {u} history unavailable), minus expenses",
            }
        # No underlying field set on the ETF — uniform BTC × 0.70 fallback.
        if btc_cagr is None:
            return {"forward_return_pct": None, "source": "unavailable",
                    "basis": "BTC-USD long-run history unavailable"}
        fwd = btc_cagr * 0.70 - er_drag * 100.0
        return {"forward_return_pct": fwd, "source": "live_long_run",
                "basis": f"BTC-USD 10yr CAGR ({btc_cagr:.1f}%) × 0.70 "
                         f"(altcoin proxy — no underlying field set), minus expenses"}

    if category == "leveraged":
        # 2x leveraged products: naive 2x is wrong (vol decay); prior
        # multiplier 1.40 was optimistic. Recalibrated Apr 2026 per
        # Cheng & Madhavan 2009 "Dynamics of Leveraged and Inverse
        # ETFs" + issuer data showing BITX cumulative return tracks
        # ~1.0-1.2× spot BTC over volatile multi-year periods (up on
        # trend days, down on chop days, nets close to 1x). 1.10 is
        # the realistic mid-point; we add an explicit vol-decay
        # warning in the basis string.
        u_cagr, u_label = _underlying_cagr()
        if u_cagr is None:
            return {"forward_return_pct": None, "source": "unavailable",
                    "basis": f"{u_label} long-run history unavailable"}
        fwd = u_cagr * 1.10 - er_drag * 100.0
        return {"forward_return_pct": fwd, "source": "live_long_run",
                "basis": f"{u_label} CAGR ({u_cagr:.1f}%) × 1.10 "
                         f"(vol-decay-adjusted 2x — realized cumulative "
                         f"return for 2x crypto products tracks ~1× spot "
                         f"in volatile regimes, not 2×), minus expenses"}

    if category == "income_covered_call":
        # Covered-call wrappers cap upside (typically giving up 40-60%
        # of underlying price appreciation in exchange for option
        # premium distributions). Long-run total return is roughly
        # 0.55 × underlying + modest yield pickup. Uses the fund's
        # actual underlying (ETHI → ETH, BTCI → BTC, MSTY → MSTR/BTC).
        u_cagr, u_label = _underlying_cagr()
        if u_cagr is None:
            return {"forward_return_pct": None, "source": "unavailable",
                    "basis": f"{u_label} long-run history unavailable"}
        fwd = u_cagr * 0.55 - er_drag * 100.0
        return {"forward_return_pct": fwd, "source": "live_long_run",
                "basis": f"{u_label} CAGR ({u_cagr:.1f}%) × 0.55 "
                         f"(covered-call upside cap), minus expenses. "
                         f"Distribution yield is included in total return."}

    if category == "defined_outcome":
        # Calamos CBOJ-series: 100% / 90% / 80% downside-buffered BTC ETFs
        # with corresponding upside caps (typically 10-20% p.a. depending on
        # series + vol regime at roll). The buffer structure asymmetrically
        # decouples from BTC on drawdowns, so long-run expected return is
        # *not* BTC CAGR minus a constant — it's better modeled as a cap-
        # ceiling-dominated process. Academic treatment: Israelov & Nielsen
        # 2015 "Covered Calls Uncovered" (same option-overwrite decomposition
        # applies to protective-puts + short-call combos).
        # Empirically, structured-outcome products deliver ~30-50% of
        # underlying CAGR when underlying is up, 0% when underlying is
        # flat-to-down (within buffer). Model at BTC × 0.40 as the
        # blended annualized estimate.
        if btc_cagr is None:
            return {"forward_return_pct": None, "source": "unavailable",
                    "basis": "BTC-USD long-run history unavailable"}
        fwd = btc_cagr * 0.40 - er_drag * 100.0
        return {"forward_return_pct": fwd, "source": "live_long_run",
                "basis": f"BTC-USD 10yr CAGR ({btc_cagr:.1f}%) × 0.40 "
                         f"(defined-outcome upside cap + buffer floor), "
                         f"minus expenses. Real payoff is capped by the "
                         f"series outcome period — this is the annualized "
                         f"average across cap hits and flat periods."}

    if category == "multi_asset":
        if btc_cagr is None or eth_cagr is None:
            return {"forward_return_pct": None, "source": "unavailable",
                    "basis": "Reference asset unavailable"}
        # Bitwise 10 / generic multi-asset: ~75% BTC, 15% ETH, 10% alts.
        # Alts modeled at BTC × 0.70 (see altcoin_spot above).
        alt_component = btc_cagr * 0.70
        blended = 0.75 * btc_cagr + 0.15 * eth_cagr + 0.10 * alt_component
        fwd = blended - er_drag * 100.0
        return {"forward_return_pct": fwd, "source": "live_long_run",
                "basis": f"75% BTC + 15% ETH + 10% altcoin-proxy long-run "
                         f"CAGR, minus expenses"}

    # Unknown category → no estimate
    return {"forward_return_pct": None, "source": "unavailable",
            "basis": f"Unknown category: {category}"}


def get_btc_correlation(
    ticker: str,
    lookback_days: int = 90,
    btc_proxy: str = _BTC_PROXY_TICKER,
) -> dict:
    """
    Pearson correlation of daily log returns between `ticker` and a BTC
    spot ETF proxy (IBIT by default; FBTC fallback) over the trailing
    `lookback_days` sessions. Shape:
        {"correlation": float|None, "source": str, "n_returns": int,
         "btc_proxy_used": str}
    Correlation is in [-1, +1]. If ticker IS the BTC proxy, returns
    exactly 1.0. Returns None if fewer than _MIN_RETURNS_FOR_STATS
    overlapping daily returns are available.
    """
    import statistics

    tkr_upper = ticker.upper()
    if tkr_upper in (btc_proxy.upper(), _BTC_PROXY_FALLBACK):
        # Trivially perfectly correlated with itself
        return {"correlation": 1.0, "source": "self",
                "n_returns": lookback_days, "btc_proxy_used": tkr_upper}

    period = f"{max(90, int(lookback_days * 1.6))}d"

    tkr_bundle = get_etf_prices([ticker], period=period, interval="1d")
    tkr_entry = tkr_bundle.get(ticker, {}) or {}
    source = tkr_entry.get("source", "unavailable")

    btc_bundle = get_etf_prices([btc_proxy], period=period, interval="1d")
    btc_entry = btc_bundle.get(btc_proxy, {}) or {}
    if not btc_entry.get("prices"):
        # BTC proxy failed — try fallback
        btc_proxy = _BTC_PROXY_FALLBACK
        btc_bundle = get_etf_prices([btc_proxy], period=period, interval="1d")
        btc_entry = btc_bundle.get(btc_proxy, {}) or {}

    ret_tkr, ret_btc = _aligned_log_returns(tkr_entry, btc_entry)
    # Keep only the trailing `lookback_days` windows
    ret_tkr = ret_tkr[-lookback_days:]
    ret_btc = ret_btc[-lookback_days:]

    if len(ret_tkr) < _MIN_RETURNS_FOR_STATS or len(ret_btc) < _MIN_RETURNS_FOR_STATS:
        return {"correlation": None, "source": source,
                "n_returns": len(ret_tkr), "btc_proxy_used": btc_proxy}

    try:
        std_t = statistics.stdev(ret_tkr)
        std_b = statistics.stdev(ret_btc)
    except statistics.StatisticsError:
        return {"correlation": None, "source": source,
                "n_returns": len(ret_tkr), "btc_proxy_used": btc_proxy}
    if std_t == 0 or std_b == 0:
        return {"correlation": None, "source": source,
                "n_returns": len(ret_tkr), "btc_proxy_used": btc_proxy}

    mean_t = sum(ret_tkr) / len(ret_tkr)
    mean_b = sum(ret_btc) / len(ret_btc)
    cov = sum((a - mean_t) * (b - mean_b) for a, b in zip(ret_tkr, ret_btc))
    cov /= (len(ret_tkr) - 1)
    corr = cov / (std_t * std_b)
    # Clamp to [-1, +1] against floating-point drift
    corr = max(-1.0, min(1.0, corr))
    return {"correlation": corr, "source": source,
            "n_returns": len(ret_tkr), "btc_proxy_used": btc_proxy}


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


def get_etf_prices_batch(
    tickers: list[str],
    period: str = "1y",
    interval: str = "1d",
) -> dict[str, dict]:
    """
    Batched price fetch — Option 1 cold-boot performance fix.

    yfinance's `Ticker(t).history()` is one HTTP request per ticker.
    Loading 73 tickers × 2 fetches (CAGR period + vol period) = 146
    sequential requests, which Streamlit Cloud's heavily-throttled IP
    can take 5+ minutes to complete on cold boot.

    `yf.download([...], group_by='ticker')` issues a single HTTP
    request that returns OHLCV for all tickers. ~146 requests collapse
    to ~3 (one per distinct period).

    Falls back to per-ticker `_fetch_yfinance` for any ticker that
    came back empty/NaN in the batch (handles yfinance's silent
    "possibly delisted" returns for not-yet-indexed altcoin spot ETFs).

    Returns the same shape as `get_etf_prices`:
        { ticker: {"source": "yfinance"|"unavailable",
                   "prices": [{date, open, high, low, close, volume}, ...]} }

    Honors _yf_memo cache + _last_close updates per ticker exactly
    like the per-ticker path so callers can mix the two freely.
    """
    out: dict[str, dict] = {}

    # First pass: serve anything that's already in the per-ticker memo
    # so we don't re-fetch what's already cached.
    to_fetch: list[str] = []
    for tkr in tickers:
        memo_key = (tkr.upper(), period, interval)
        memo_hit = _yf_memo.get(memo_key)
        if memo_hit is not None:
            age_sec = int(time.monotonic() - memo_hit["_mono"])
            ttl = CACHE_TTL.get("etf_price_market", 300)
            if age_sec < ttl:
                out[tkr] = {k: v for k, v in memo_hit.items() if not k.startswith("_")}
                continue
        to_fetch.append(tkr)

    if not to_fetch:
        return out

    # Single batched yfinance call.
    batch_succeeded = False
    try:
        import yfinance as yf
        df_all = yf.download(
            tickers=to_fetch,
            period=period,
            interval=interval,
            group_by="ticker",
            threads=True,
            progress=False,
            auto_adjust=False,
        )
        batch_succeeded = df_all is not None and not df_all.empty
    except Exception as exc:
        logger.info("yfinance batch failed for %d tickers: %s", len(to_fetch), exc)
        df_all = None

    if batch_succeeded:
        # yf.download returns multi-level columns when multiple tickers
        # are passed; single-ticker returns a flat DataFrame. Handle both.
        for tkr in to_fetch:
            try:
                if len(to_fetch) == 1:
                    df_t = df_all
                else:
                    if tkr in df_all.columns.get_level_values(0):
                        df_t = df_all[tkr]
                    else:
                        df_t = None
                if df_t is None or df_t.empty:
                    raise ValueError("empty slice")
                # Drop fully-NaN rows (delisted / missing-data tickers
                # come back as all-NaN columns in the batch).
                df_t = df_t.dropna(how="all")
                if df_t.empty:
                    raise ValueError("all-NaN after drop")
                rows = _df_to_rows(df_t)
                if not rows:
                    raise ValueError("rows empty")
                register_fetch_attempt("etf_price", "yfinance", success=True)
                _update_last_close(tkr, rows)
                result = {"source": "yfinance", "prices": rows}
                _yf_memo[(tkr.upper(), period, interval)] = {
                    **result, "_mono": time.monotonic(),
                }
                out[tkr] = result
            except Exception:
                # Individual ticker failed inside the batch — try the
                # full per-ticker fallback chain (yfinance retry → Stooq).
                out[tkr] = _fetch_single_ticker(tkr, period, interval)
    else:
        # Whole batch failed — fall through to per-ticker for everything.
        for tkr in to_fetch:
            out[tkr] = _fetch_single_ticker(tkr, period, interval)

    return out


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

# Day-4: live reference-data chain — EDGAR → issuer → ETF.com → seed.
# Each step calls register_fetch_attempt so the UI can render the right
# data_source_badge state per the transparency requirement.

# 24hr memoization for reference data (changes rarely).
_ref_memo: dict[str, dict] = {}
_REF_MEMO_TTL = 86400


def get_etf_reference(ticker: str) -> dict:
    """
    Return reference data for a single ETF.

    Shape:
      {
        "ticker":        str,
        "name":          str,
        "issuer":        str,
        "category":      str,
        "expense_ratio_bps": float | None,
        "inception_date":    str | None,
        "aum_usd":       float | None,
        "source":        "edgar" | "issuer" | "etfcom" | "seed" | "unavailable",
        "note":          str,
      }

    Live-first with graceful degradation. Never returns fabricated data —
    missing fields come back as None and the UI surfaces the state via
    data_source_badge.
    """
    tkr = ticker.upper()
    now = time.monotonic()

    # Memo hit
    cached = _ref_memo.get(tkr)
    if cached and (now - cached.get("_mono", 0)) < _REF_MEMO_TTL:
        return {k: v for k, v in cached.items() if not k.startswith("_")}

    seed_entry = next((e for e in ETF_UNIVERSE_SEED if e["ticker"].upper() == tkr), None)
    base: dict = {
        "ticker":            tkr,
        "name":              seed_entry.get("name", tkr) if seed_entry else tkr,
        "issuer":            seed_entry.get("issuer", "") if seed_entry else "",
        "category":          seed_entry.get("category", "") if seed_entry else "",
        "expense_ratio_bps": None,
        "inception_date":    None,
        "aum_usd":           None,
        "source":            "unavailable",
        "note":              "",
    }

    # ── Primary: EDGAR (via the shared N-PORT composition cache) ─────────────
    try:
        from integrations.edgar_nport import get_etf_composition
        comp = get_etf_composition(tkr)
        if comp.get("source") == "edgar_live":
            base["aum_usd"] = comp.get("total_value_usd") or None
            base["source"] = "edgar"
            base["note"] = f"AUM derived from EDGAR N-PORT filing {comp.get('filing_date')}"
            register_fetch_attempt("etf_reference", "edgar", success=True)
            _ref_memo[tkr] = {**base, "_mono": now}
            return base
        register_fetch_attempt("etf_reference", "edgar", success=False,
                               note=f"N-PORT returned source={comp.get('source')}")
    except Exception as exc:
        logger.info("EDGAR reference path failed for %s: %s", tkr, exc)
        register_fetch_attempt("etf_reference", "edgar", success=False,
                               note=f"{type(exc).__name__}")

    # ── Secondary: issuer-site scrape (not wired in Day 4 — placeholder) ─────
    # Post-demo: implement per-issuer scrapers. For now, the issuer chain
    # records a miss and moves on. This keeps the architecture honest.
    register_fetch_attempt("etf_reference", "issuer", success=False,
                           note="issuer-site scraper lands post-demo")

    # ── Tertiary: ETF.com (not wired in Day 4 — placeholder) ─────────────────
    register_fetch_attempt("etf_reference", "etfcom", success=False,
                           note="ETF.com scraper lands post-demo")

    # ── Final: seed-file + mark as CACHED transparency state ─────────────────
    if seed_entry:
        # Seed expense_ratio comes from etf_universe._EXPENSE_RATIO_BPS
        from core.etf_universe import _EXPENSE_RATIO_BPS
        base["expense_ratio_bps"] = _EXPENSE_RATIO_BPS.get(tkr)
        base["source"] = "seed"
        base["note"] = "Live EDGAR reference unavailable — showing seed-file defaults."
        mark_cache_hit("etf_reference", age_seconds=0,
                       source_name="seed",
                       note="seed-file fallback after live EDGAR miss")
        _ref_memo[tkr] = {**base, "_mono": now}
        return base

    mark_static_fallback("etf_reference",
                         note=f"No live or seed data for {tkr}")
    base["note"] = "No data available for this ticker."
    return base


# ═══════════════════════════════════════════════════════════════════════════
# Cache TTL surface (imported by @st.cache_data decorators on Day 3)
# ═══════════════════════════════════════════════════════════════════════════

def ttl_for(key: str) -> int:
    """Return the configured cache TTL in seconds, with fallback."""
    return int(CACHE_TTL.get(key, CACHE_TTL.get("empty_result", 30)))
