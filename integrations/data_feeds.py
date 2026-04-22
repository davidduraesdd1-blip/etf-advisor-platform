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
from core.data_source_state import (
    mark_cache_hit,
    mark_static_fallback,
    register_fetch_attempt,
)

logger = logging.getLogger(__name__)

# Known-history set — only failures on these tickers count against the
# circuit breaker per planning-side Risk 5 direction.
_KNOWN_HISTORY_TICKERS: frozenset[str] = frozenset(
    e["ticker"] for e in ETF_UNIVERSE_SEED
)

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
    """
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
    """Return the most recently cached close for a ticker, or None."""
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
