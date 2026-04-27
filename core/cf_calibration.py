"""
core/cf_calibration.py — Cornish-Fisher per-category skew + kurtosis fit.

Polish round 5, Sprint 1, Commit 1 (2026-04-28).

CONVENTION: this module returns EXCESS kurtosis (Fisher convention,
                K_excess = raw_kurt − 3; standard normal has K_excess = 0).
                This matches the existing portfolio_engine.cornish_fisher_var
                signature, which expects excess kurtosis per the Cornish-Fisher
                expansion (Favre & Galeano 2002) and Maillard 2012's monotone-
                domain caps (which are on excess, not raw).

The crypto-midpoint defaults (S=−0.7, K=8.0) shipped in
`portfolio_engine.cornish_fisher_var` are calibrated to BTC daily
returns. They under-estimate fat-tail risk on alt-heavy tiers, where
empirical skewness is more negative and kurtosis materially higher
(Shahzad et al. 2022; Chaim & Laurini 2018).

This module fits per-category (S, K) parameters from 5+ years of
yfinance daily log-returns across every ETF in each category, then
caches the results to `data/cf_params_cache.json` with a 30-day TTL.
The portfolio engine reads the cache via `_get_cf_params(category)`
in commit 2; absence/staleness/unknown-category falls back to the
existing crypto-midpoint so this is a strict precision improvement,
not a regression risk.

References:
    - Cornish, E.A. and Fisher, R.A. (1937). Moments and cumulants in
      the specification of distributions. Revue de l'Institut
      International de Statistique, 5(4), 307-320.
    - Maillard, D. (2012). A user's guide to the Cornish Fisher
      expansion. SSRN 1997178. Domain-of-validity caps:
        skewness ∈ [-1.5, 1.5]; excess kurtosis ∈ [0, 15].
    - Shahzad, S.J.H. et al. (2022). Risk modelling of cryptocurrencies:
      a fat-tailed approach. Annals of Operations Research.
    - Chaim, P. and Laurini, M. (2018). Volatility and return
      dependence in Bitcoin. Finance Research Letters, 26.

CLAUDE.md governance: §9 (math model architecture), §12 (cache TTLs).
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = REPO_ROOT / "data" / "cf_params_cache.json"
CACHE_TTL_SECONDS: int = 30 * 24 * 3600   # 30 days

# Maillard 2012 monotone-domain caps. Beyond these, the Cornish-Fisher
# quantile inverts (the polynomial loses monotonicity), and the VaR
# computation produces nonsense. Hard-clip every fitted moment.
SKEW_CAP_LOW: float = -1.5
SKEW_CAP_HIGH: float = 1.5
# EXCESS kurtosis: standard normal = 0, leptokurtic (fat tails) > 0.
# Maillard 2012's monotone-domain analysis caps excess kurtosis at ~15.
# Lower bound at 0 since financial returns are essentially never sub-
# Gaussian (excess < 0); we don't want a fitted -2.0 to feed bad math
# into the CF polynomial.
KURT_CAP_LOW: float = 0.0
KURT_CAP_HIGH: float = 15.0

# Fallback values when cache is empty / stale / category not present.
# Match the crypto-midpoint defaults in portfolio_engine.
FALLBACK_SKEW: float = -0.7
FALLBACK_KURT: float = 8.0

# Categories the calibrator runs the fit for. Mirrors
# core.risk_tiers and the universe loader's category set.
CATEGORY_LIST: tuple[str, ...] = (
    "btc_spot", "eth_spot", "altcoin_spot",
    "btc_futures", "eth_futures",
    "leveraged", "income_covered_call",
    "thematic_equity", "multi_asset", "defined_outcome",
)

# Default lookback window for the per-category fit. Five years of
# daily returns gives ~1260 observations per ETF — enough to stabilize
# higher-moment estimates even after the bias correction.
DEFAULT_FIT_YEARS: int = 5

# Minimum observations per ETF to include in the category fit. 252
# trading days = ~1 calendar year. Below this, the moment estimates
# are too noisy to trust.
MIN_OBSERVATIONS: int = 252


# ═══════════════════════════════════════════════════════════════════════════
# Core fit helpers
# ═══════════════════════════════════════════════════════════════════════════

def fit_skew_kurtosis(
    returns,
    *,
    min_observations: int = MIN_OBSERVATIONS,
) -> tuple[float, float]:
    """
    Fit (skew, excess_kurtosis) on a Series / array of log-returns.

    Uses scipy.stats.skew(bias=False) for the bias-corrected sample
    skewness and scipy.stats.kurtosis(fisher=True, bias=False) for
    excess kurtosis (Fisher convention: standard normal → 0).

    Returns the pair clamped to Maillard 2012 monotone-domain caps so
    downstream Cornish-Fisher quantile evaluation stays well-defined.
    Match the convention of portfolio_engine.cornish_fisher_var which
    accepts excess (not raw) kurtosis.

    Raises ValueError if `returns` is empty or has fewer than
    `min_observations` finite samples.
    """
    import numpy as np
    from scipy import stats

    arr = np.asarray(returns, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        raise ValueError("fit_skew_kurtosis: empty input series")
    if arr.size < min_observations:
        raise ValueError(
            f"fit_skew_kurtosis: only {arr.size} observations "
            f"(min {min_observations})"
        )

    # bias=False uses the unbiased estimator (Fisher-Pearson).
    skew_val = float(stats.skew(arr, bias=False))
    # fisher=True returns EXCESS kurtosis (raw - 3). Standard normal → 0.
    excess_kurt = float(stats.kurtosis(arr, fisher=True, bias=False))

    # Clamp to Maillard 2012 caps. NaN-safe.
    if not math.isfinite(skew_val):
        skew_val = FALLBACK_SKEW
    if not math.isfinite(excess_kurt):
        excess_kurt = FALLBACK_KURT
    skew_val = max(SKEW_CAP_LOW, min(SKEW_CAP_HIGH, skew_val))
    excess_kurt = max(KURT_CAP_LOW, min(KURT_CAP_HIGH, excess_kurt))

    return (skew_val, excess_kurt)


def fetch_category_returns(category: str, *, years: int = DEFAULT_FIT_YEARS):
    """
    Pull daily log-returns for every ETF in `category` from yfinance
    (via integrations.data_feeds.get_etf_prices) and return the
    concatenated DataFrame indexed by date.

    Skips ETFs that come back with fewer than MIN_OBSERVATIONS daily
    closes. When DEMO_MODE_NO_FETCH=1 is set, returns an empty frame
    (test harness short-circuit; the cache write step in
    `fit_per_category` then writes the fallback values for every
    category, preserving the demo-deterministic behavior).
    """
    import pandas as pd

    from core.etf_universe import load_universe
    from integrations.data_feeds import get_etf_prices

    if os.environ.get("DEMO_MODE_NO_FETCH") == "1":
        return pd.DataFrame()

    universe = load_universe()
    tickers = [e["ticker"] for e in universe if e.get("category") == category]
    if not tickers:
        return pd.DataFrame()

    period = f"{max(2, years)}y"
    bundle = get_etf_prices(tickers, period=period, interval="1d")

    cols: dict[str, list[float]] = {}
    for ticker in tickers:
        entry = bundle.get(ticker, {}) or {}
        rows = entry.get("prices", []) or []
        # Build close series; compute log-returns.
        closes: list[float] = []
        for row in rows:
            try:
                c = float(row.get("close"))
                if c > 0:
                    closes.append(c)
            except (TypeError, ValueError):
                continue
        if len(closes) < MIN_OBSERVATIONS:
            continue
        rets: list[float] = []
        for prev, curr in zip(closes[:-1], closes[1:]):
            if prev > 0 and curr > 0:
                rets.append(math.log(curr / prev))
        if len(rets) >= MIN_OBSERVATIONS:
            cols[ticker] = rets

    if not cols:
        return pd.DataFrame()

    # Pad to common length (right-align — most-recent dates align).
    max_len = max(len(v) for v in cols.values())
    padded = {
        k: [None] * (max_len - len(v)) + v
        for k, v in cols.items()
    }
    return pd.DataFrame(padded)


def fit_per_category(
    *,
    years: int = DEFAULT_FIT_YEARS,
    write_cache: bool = True,
) -> dict[str, tuple[float, float]]:
    """
    Fit (S, K) for each category in `CATEGORY_LIST` using
    `fetch_category_returns(category, years=...)` and `fit_skew_kurtosis`.

    Returns `{category: (S, K)}`. Categories with too little history
    (or under DEMO_MODE_NO_FETCH=1) get the crypto-midpoint fallback
    `(FALLBACK_SKEW, FALLBACK_KURT)`.

    When `write_cache=True` (default), persists the result to
    `data/cf_params_cache.json` atomically with the current monotonic
    timestamp so `_get_cf_params()` can validate the 30-day TTL.
    """
    out: dict[str, tuple[float, float]] = {}
    for category in CATEGORY_LIST:
        try:
            frame = fetch_category_returns(category, years=years)
            if frame.empty:
                out[category] = (FALLBACK_SKEW, FALLBACK_KURT)
                continue
            # Pool every ETF's returns into one series for the category fit.
            # Pooling assumes within-category correlation is high enough that
            # a category-level moment is more informative than per-ETF noise.
            import numpy as np
            pooled = frame.values.astype(float).ravel()
            pooled = pooled[np.isfinite(pooled)]
            try:
                out[category] = fit_skew_kurtosis(pooled)
            except ValueError as exc:
                logger.info("CF fit fell back for %s: %s", category, exc)
                out[category] = (FALLBACK_SKEW, FALLBACK_KURT)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("CF fit error for %s: %s", category, exc)
            out[category] = (FALLBACK_SKEW, FALLBACK_KURT)

    if write_cache:
        _write_cache(out)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Cache persistence
# ═══════════════════════════════════════════════════════════════════════════

def _write_cache(params: dict[str, tuple[float, float]]) -> None:
    """Atomic write of the per-category (S, K) cache + wall-clock + monotonic
    timestamps. Mirrors the atomic-write pattern from core/etf_universe.py."""
    payload: dict[str, Any] = {
        "_metadata": {
            "fitted_at_iso":  _now_iso(),
            "fitted_at_unix": time.time(),
            "ttl_seconds":    CACHE_TTL_SECONDS,
        },
        "params": {cat: list(sk) for cat, sk in params.items()},
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def load_cache() -> dict[str, tuple[float, float]] | None:
    """
    Load the per-category (S, K) cache, returning None if missing,
    malformed, or older than CACHE_TTL_SECONDS (30 days).
    """
    if not CACHE_PATH.exists():
        return None
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        meta = data.get("_metadata", {}) or {}
        fitted_at_unix = float(meta.get("fitted_at_unix", 0))
        if fitted_at_unix <= 0:
            return None
        age = time.time() - fitted_at_unix
        if age > CACHE_TTL_SECONDS:
            logger.info("CF cache stale (age %.1f days) — falling back", age / 86400)
            return None
        params = data.get("params", {}) or {}
        out: dict[str, tuple[float, float]] = {}
        for cat, pair in params.items():
            try:
                s_raw, k_raw = float(pair[0]), float(pair[1])
            except (TypeError, ValueError, IndexError):
                continue
            # Re-clamp on read in case the on-disk cache predates a
            # tightened cap.
            s = max(SKEW_CAP_LOW, min(SKEW_CAP_HIGH, s_raw))
            k = max(KURT_CAP_LOW, min(KURT_CAP_HIGH, k_raw))
            out[str(cat)] = (s, k)
        return out
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("CF cache unreadable (%s) — falling back", exc)
        return None


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
