"""
portfolio_engine.py — ETF Advisor Platform v0.1 (Day-2 Phase 1 port)

Mathematical portfolio construction for the crypto ETF universe.

Ported from rwa-infinity-model/portfolio.py. See docs/port_log.md for
per-function disposition (copied verbatim / adapted / dropped).

Phase 1 scope (blocking for Day 3):
  - build_portfolio(tier_name, universe, portfolio_value_usd) → holdings + weights
  - compute_portfolio_metrics(holdings, portfolio_value, tier_name) → full metric dict
  - run_monte_carlo(portfolio, n_simulations, horizon_days) → distribution + sample_paths
  - cornish_fisher_var(mean_return, vol, confidence) → parametric VaR %

Preserved from rwa-infinity-model:
  - Cornish-Fisher VaR expansion (Favre & Galeano 2002)
  - Student-t (ν=4) diffusion (Cont & Tankov 2004)
  - Merton jump-diffusion (Merton 1976)
  - Magdon-Ismail & Atiya (2004) max-drawdown approximation
  - FRED live risk-free rate with 4.25% fallback

Planning-side Risk directives applied in this file:
  - Risk 1 (RWA-calibrated distribution params preserved as-is; flagged in port_log)
  - Risk 2 (ETH correlation-with-btc simplification guarded at runtime)
  - Risk 3 (FRED via public CSV endpoint, no API key)
  - Risk 4 (compute 10,000 paths, retain MONTE_CARLO_PATHS_RETAIN for UI)
  - Risk 6 (np.random.default_rng(42) for determinism lock)

CLAUDE.md governance: Sections 9, 10, 12, 19.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from config import (
    MONTE_CARLO_PATHS_COMPUTE,
    MONTE_CARLO_PATHS_RETAIN,
    PORTFOLIO_TIERS,
)
from core.data_source_state import (
    mark_cache_hit,
    mark_static_fallback,
    register_fetch_attempt,
)
from core.risk_tiers import (
    MAX_ETFS_PER_CATEGORY,
    MAX_SINGLE_POSITION_PCT,
    allocation_for_tier,
)

logger = logging.getLogger(__name__)

# ── Constants (from rwa portfolio.py — preserved verbatim) ───────────────────
RISK_FREE_RATE_FALLBACK: float = 4.25   # % — used when FRED unavailable
TRADING_DAYS: int = 252
MC_HORIZON_DAYS: int = 365
VAR_CONFIDENCE: tuple[float, ...] = (0.95, 0.99)
DEFAULT_SEED: int = 42                   # determinism lock per Mod 4 / Risk 6

# ── FRED risk-free rate cache (module-scoped, 2-hour TTL) ────────────────────
# Audit-fix (MEDIUM): thread-safety lock. Sprint 4's Alpaca daemon thread
# can call into portfolio_engine paths simultaneously with Streamlit's
# rerun threads — without the lock, the read-modify-write of the cache
# dict could corrupt the in-flight rate. The TTL pattern was likely
# benign in practice (last-writer-wins is OK semantically) but the lock
# eliminates the race entirely.
import threading as _rfr_threading
_rfr_lock = _rfr_threading.Lock()
_rfr_cache: dict[str, Any] = {"rate": None, "ts": 0.0}
_RFR_CACHE_TTL: int = 3600 * 2
_FRED_CSV_URL: str = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO"

# ── Monte Carlo cache (module-scoped, 10-minute TTL) ─────────────────────────
_mc_cache: dict[str, dict] = {}
_MC_CACHE_TTL: int = 600


# ═══════════════════════════════════════════════════════════════════════════
# Live risk-free rate (FRED 3-month T-bill, CSV endpoint)
# ═══════════════════════════════════════════════════════════════════════════

def get_live_risk_free_rate() -> float:
    """
    Fetch the current 3-month US Treasury bill yield (DGS3MO) from FRED's
    public CSV endpoint. No API key required. 2-hour cache. Falls back to
    RISK_FREE_RATE_FALLBACK (4.25%) on any error.
    """
    now = time.time()
    # Audit-fix: read snapshot under lock so a concurrent writer can't
    # tear the (rate, ts) tuple.
    with _rfr_lock:
        cached_rate = _rfr_cache["rate"]
        cached_ts = _rfr_cache["ts"]
    if cached_rate is not None and now - cached_ts < _RFR_CACHE_TTL:
        age_seconds = int(now - cached_ts)
        # Still fresh — classify as LIVE if last successful fetch was from FRED
        # primary; CACHED if cache is older than 15 min so UI can show the age.
        if age_seconds < 900:
            return float(cached_rate)
        mark_cache_hit("risk_free_rate", age_seconds=age_seconds,
                       note="FRED cache still valid; next refresh on TTL expiry.")
        return float(cached_rate)

    try:
        import requests
        resp = requests.get(
            _FRED_CSV_URL,
            headers={"User-Agent": "ETF-Advisor-Platform/0.1"},
            timeout=6,
        )
        resp.raise_for_status()
        # Format: header line + rows "YYYY-MM-DD,rate" with trailing "." for holidays
        lines = [ln.strip() for ln in resp.text.splitlines() if ln.strip()]
        for line in reversed(lines[1:]):   # skip header; walk back to most recent real value
            parts = line.split(",")
            if len(parts) < 2:
                continue
            val_str = parts[1].strip()
            if val_str in ("", "."):
                continue
            try:
                val = float(val_str)
            except ValueError:
                continue
            if 0 < val < 20:
                with _rfr_lock:
                    _rfr_cache["rate"] = val
                    _rfr_cache["ts"] = now
                register_fetch_attempt("risk_free_rate", "fred", success=True)
                return val
    except Exception as exc:
        logger.debug("FRED risk-free rate fetch failed: %s", exc)
        register_fetch_attempt("risk_free_rate", "fred", success=False,
                               note=f"FRED fetch error: {type(exc).__name__}")

    with _rfr_lock:
        _rfr_cache["rate"] = RISK_FREE_RATE_FALLBACK
        _rfr_cache["ts"] = now
    mark_static_fallback("risk_free_rate",
                         note=f"FRED unavailable — using static {RISK_FREE_RATE_FALLBACK}% fallback.")
    return RISK_FREE_RATE_FALLBACK


# ═══════════════════════════════════════════════════════════════════════════
# Cornish-Fisher modified VaR (verbatim port — do not retune in Phase 1)
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# Cornish-Fisher modified VaR — audit 2026-04-22 P0 recalibration
# ═══════════════════════════════════════════════════════════════════════════
#
# Prior values (S=-0.25, K=2.5) were equity-ETF moments — they suppress
# crypto's real fat tails. Empirical crypto literature (Shahzad et al.
# 2022; Chaim & Laurini 2018; CME Group 2023) puts daily BTC returns at:
#   skewness:         -0.4 to -1.2
#   excess kurtosis:  6 to 15
# Spot BTC ETFs (IBIT/FBTC) track the underlying <10bps/day, so the ETF
# wrapper doesn't dampen tails materially.
#
# Fix: accept optional skew + excess-kurt inputs, cap to the Maillard
# (2012) domain-of-validity where the CF quantile remains monotone. If
# inputs not provided, fall back to crypto-calibrated defaults that
# match the literature midpoint, not equity-ETF values.
# 2026-04-28 hotfix: NO-FALLBACK POLICY per Cowork directive.
# The crypto-midpoint defaults (-0.7, 8.0) that previously lived here
# as `_CF_DEFAULT_SKEW` / `_CF_DEFAULT_KURT` have been REMOVED. Any
# silent fallback to hardcoded constants is forbidden. Per-category
# (S, K) are read from one of:
#   1. data/cf_params_cache.json — runtime cache populated by the
#      patient `core.cf_calibration.fit_per_category()` job (gitignored,
#      regenerated nightly when cron is wired)
#   2. core/cf_params_production.json — committed production-config
#      snapshot, ALWAYS present in the repo, fitted manually + stored
#      with metadata (fit timestamp, git ref, n_funds/n_observations
#      per category, fit_basis = "live" or "nearest_neighbor")
# If both fail, `_get_cf_params` raises RuntimeError. No third level.

# Maillard (2012) monotone-domain caps — applied after weighted
# aggregation so the polynomial stays well-defined even if a
# pathological per-category value somehow gets cached.
_CF_SKEW_CAP: float = 1.5
_CF_KURT_CAP: float = 15.0

# 2026-04-28 hotfix — feasibility clip. The Cornish-Fisher polynomial
# extrapolates beyond the feasible range when (S, K) hit the Maillard
# caps and confidence is high (e.g., 99% on alt-heavy baskets). For
# long-only crypto ETF baskets, the true upper bound of loss is 100%
# of allocated principal — a hard mathematical constraint of the asset
# class. We clip CF-derived VaR/CVaR to this bound and return a
# `cf_boundary_reached` flag so the UI can surface that the model has
# reached its feasibility boundary rather than display a polynomial
# extrapolation that exceeds reality.
_LONG_ONLY_LOSS_BOUND_PCT: float = 100.0


from typing import NamedTuple


class CFRiskResult(NamedTuple):
    """Output of cornish_fisher_var / cornish_fisher_cvar after the
    feasibility clip lands. `value` is the loss in percent (0..100);
    `cf_boundary_reached` is True when the unclipped polynomial
    estimate exceeded the long-only 100% loss bound and was clipped.

    Existing call sites that only need the magnitude can read the
    `.value` field; the UI risk panel reads `.cf_boundary_reached` to
    decide whether to display "≤ -100% / model boundary" with the
    accompanying CF-boundary footnote.
    """
    value: float
    cf_boundary_reached: bool


def _clip_to_loss_bound(value: float) -> CFRiskResult:
    """Clip a CF-derived loss percentage to the long-only feasibility
    range [0, 100]. Returns the (clipped value, boundary_reached) pair."""
    if value > _LONG_ONLY_LOSS_BOUND_PCT:
        return CFRiskResult(value=_LONG_ONLY_LOSS_BOUND_PCT, cf_boundary_reached=True)
    return CFRiskResult(value=max(0.0, value), cf_boundary_reached=False)


def _load_production_config() -> dict[str, tuple[float, float]] | None:
    """
    Read the committed production-config snapshot at
    core/cf_params_production.json. Always present in the repo after
    the 2026-04-28 hotfix. Returns {category: (S, K)} or None if the
    file is missing / malformed (which is a deployment error).
    """
    from pathlib import Path as _Path
    import json as _json
    cfg_path = _Path(__file__).resolve().parents[1] / "core" / "cf_params_production.json"
    if not cfg_path.exists():
        logger.error("cf_params_production.json missing — repository is misconfigured")
        return None
    try:
        data = _json.loads(cfg_path.read_text(encoding="utf-8"))
        out: dict[str, tuple[float, float]] = {}
        for cat, info in (data.get("categories") or {}).items():
            try:
                s = float(info["S"])
                k = float(info["K"])
            except (KeyError, TypeError, ValueError):
                continue
            # Re-clamp on read against Maillard caps.
            s = max(-_CF_SKEW_CAP, min(_CF_SKEW_CAP, s))
            k = max(0.0, min(_CF_KURT_CAP, k))
            out[str(cat)] = (s, k)
        return out
    except (OSError, _json.JSONDecodeError, ValueError) as exc:
        logger.error("cf_params_production.json unreadable (%s) — repo misconfigured", exc)
        return None


def _get_cf_params(category: str) -> tuple[float, float]:
    """
    Return per-category (skew, excess_kurtosis) for the Cornish-Fisher
    quantile expansion under the NO-FALLBACK policy.

    Precedence:
      1. data/cf_params_cache.json (runtime cache, <30 days TTL,
         populated by core.cf_calibration.fit_per_category nightly)
      2. core/cf_params_production.json (committed production snapshot,
         always present in repo)

    If both fail (production config missing / corrupted / category not
    present in either), raises RuntimeError with diagnostic message.
    NEVER returns hardcoded defaults — Cowork's 2026-04-28 directive.
    """
    try:
        from core.cf_calibration import load_cache
        cache = load_cache()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("cf_calibration cache load failed (%s) — falling through", exc)
        cache = None
    if cache and category in cache:
        return cache[category]

    prod = _load_production_config()
    if prod and category in prod:
        return prod[category]

    raise RuntimeError(
        f"CF params unavailable for category={category!r}. "
        f"Run core.cf_calibration.fit_per_category() to populate "
        f"data/cf_params_cache.json, and/or restore "
        f"core/cf_params_production.json. No silent fallback per "
        f"the no-fallback policy (Cowork hotfix 2026-04-28)."
    )


def _weighted_cf_params(holdings: list[dict]) -> tuple[float, float]:
    """
    Weight-aggregate (S, K) across the holdings of a multi-category
    portfolio.

    Method: linear weighted average of per-category (S, K), with each
    holding contributing its `weight_pct / 100` to its category's slot:
        S_portfolio = Σ (w_i × S_{cat(i)})
        K_portfolio = Σ (w_i × K_{cat(i)})

    Linear aggregation is the standard cumulant-based first-order
    approximation when the per-asset return distributions are weakly
    dependent. Conservative vs. the exact higher-moment aggregation
    (which would require the full multivariate cumulant tensor) but
    captures the dominant signal: alt-heavy tiers get fatter tails
    than BTC-only tiers.

    Empty holdings or zero-weight holdings → raises RuntimeError per
    no-fallback policy. Caller is responsible for not invoking this on
    an empty portfolio (compute_portfolio_metrics already guards).
    """
    if not holdings:
        raise RuntimeError(
            "_weighted_cf_params called with empty holdings. "
            "Caller must guard against this (no-fallback policy)."
        )
    total_w = sum((h.get("weight_pct") or 0) / 100.0 for h in holdings)
    if total_w <= 0:
        raise RuntimeError(
            "_weighted_cf_params: holdings have zero total weight"
        )
    s_sum = 0.0
    k_sum = 0.0
    for h in holdings:
        w = (h.get("weight_pct") or 0) / 100.0
        if w <= 0:
            continue
        s_h, k_h = _get_cf_params(h.get("category", ""))
        s_sum += w * s_h
        k_sum += w * k_h
    s_w = s_sum / total_w
    k_w = k_sum / total_w
    # Re-clamp to Maillard caps after aggregation.
    s_w = max(-_CF_SKEW_CAP, min(_CF_SKEW_CAP, s_w))
    k_w = max(0.0, min(_CF_KURT_CAP, k_w))
    return (s_w, k_w)


def cornish_fisher_var(
    mean_return: float,
    vol: float,
    confidence: float = 0.95,
    skew: float | None = None,
    excess_kurt: float | None = None,
) -> CFRiskResult:
    """
    Cornish-Fisher modified parametric VaR (Boudt, Peterson, Croux 2008;
    Favre & Galeano 2002).

    Returns `CFRiskResult(value, cf_boundary_reached)` — a NamedTuple
    where `value` is the clipped loss percentage (0..100) and
    `cf_boundary_reached` is True when the unclipped polynomial
    extrapolation exceeded the long-only 100% loss bound. The flag
    flows through to the UI risk panel which displays
    "≤ -100% / model boundary" when set.

    `skew` and `excess_kurt` (Fisher convention; standard normal → 0)
    are REQUIRED — the no-fallback policy (Cowork 2026-04-28) removed
    the implicit crypto-midpoint defaults. Callers must source the
    pair from `_get_cf_params(category)` / `_weighted_cf_params(holdings)`,
    which read from `data/cf_params_cache.json` (live nightly fit) or
    `core/cf_params_production.json` (committed snapshot) and raise
    RuntimeError if neither is available.

    Both inputs are hard-capped to the Maillard (2012) monotone domain
    so the modified-VaR quantile stays well-defined.

    2026-04-28 sign-convention fix: the prior implementation used
    z_g = +Φ⁻¹(c) (right-tail) instead of the correct left-tail
    quantile z_α = -Φ⁻¹(c) for VaR. The fix uses z_α = -Φ⁻¹(c) and
    applies the Boudt et al. 2008 CF expansion:
        z_α^CF = z_α + (z_α²-1)/6·γ₁
                 + (z_α³-3z_α)/24·γ₂
                 - (2z_α³-5z_α)/36·γ₁²
        mVaR = -(μ + σ·z_α^CF)

    2026-04-28 feasibility clip: at extreme moments (e.g., altcoin_spot
    at the Maillard caps S=−1.5, K=15) and high confidence (99%), the
    CF polynomial extrapolates past the long-only 100% loss bound. We
    clip and return cf_boundary_reached=True. Better tail models
    (NIG / POT / generalized hyperbolic) are post-demo work.
    """
    if skew is None or excess_kurt is None:
        raise RuntimeError(
            "cornish_fisher_var: skew and excess_kurt are required "
            "(no-fallback policy 2026-04-28). Source via "
            "_get_cf_params(category) or _weighted_cf_params(holdings)."
        )
    S = max(-_CF_SKEW_CAP, min(_CF_SKEW_CAP, float(skew)))
    K = max(0.0, min(_CF_KURT_CAP, float(excess_kurt)))

    # Audit-fix (LOW): raise on unrecognized confidence rather than
    # silently default to 95%. Caller bug shouldn't quietly produce
    # "wrong but plausible" risk numbers.
    _Z_TABLE = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326}
    if confidence not in _Z_TABLE:
        raise ValueError(
            f"cornish_fisher_var: confidence={confidence!r} not in "
            f"supported set {sorted(_Z_TABLE.keys())}. "
            f"Caller must pass one of the standard quantiles."
        )
    z_alpha = -_Z_TABLE[confidence]
    z_cf = (
        z_alpha
        + (z_alpha ** 2 - 1) / 6 * S
        + (z_alpha ** 3 - 3 * z_alpha) / 24 * K
        - (2 * z_alpha ** 3 - 5 * z_alpha) / 36 * S ** 2
    )
    raw = -(mean_return + vol * z_cf)
    return _clip_to_loss_bound(raw)


def cornish_fisher_cvar(
    mean_return: float,
    vol: float,
    confidence: float = 0.95,
    skew: float | None = None,
    excess_kurt: float | None = None,
    n_quantiles: int = 500,
) -> CFRiskResult:
    """
    CVaR (Expected Shortfall) computed CONSISTENTLY with the Cornish-
    Fisher distributional assumption used by cornish_fisher_var.

    Prior implementation used fixed multipliers on VaR (1.35 at 95%,
    1.42 at 99%) — those were Gaussian ratios inflated for "fat-tail
    feel" but internally inconsistent with the CF quantile (Rockafellar
    & Uryasev 2000). Fix: numerically integrate the CF-adjusted
    quantile function from the tail.

    Returns `CFRiskResult(value, cf_boundary_reached)` where `value` is
    the expected loss CONDITIONAL on being in the tail beyond VaR,
    clipped to the long-only 100% loss bound. `cf_boundary_reached` is
    True when the unclipped polynomial integral exceeded that bound.
    """
    if skew is None or excess_kurt is None:
        raise RuntimeError(
            "cornish_fisher_cvar: skew and excess_kurt are required "
            "(no-fallback policy 2026-04-28). Source via "
            "_get_cf_params(category) or _weighted_cf_params(holdings)."
        )
    # Audit-fix (2026-04-30): same defensive bound as cornish_fisher_var
    # for consistency. CVaR's numeric integration is mathematically
    # valid over a wider range than VaR's discrete z-table, but a caller
    # passing 0.05 (instead of 0.95) would silently compute the WRONG
    # tail. Reject anything outside a sensible quantile band.
    if not (0.5 < float(confidence) < 0.999):
        raise ValueError(
            f"cornish_fisher_cvar: confidence={confidence!r} out of "
            f"sensible band (0.5, 0.999). Pass a tail-quantile like "
            f"0.95 or 0.99."
        )
    S = max(-_CF_SKEW_CAP, min(_CF_SKEW_CAP, float(skew)))
    K = max(0.0, min(_CF_KURT_CAP, float(excess_kurt)))

    def _cf_quantile(p: float) -> float:
        # Inverse-normal approximation (Beasley-Springer-Moro for p in
        # the tail): we only need p in (0.0001, 0.1) so use a small
        # Newton on the standard-normal CDF.
        from math import erf, sqrt, pi, exp
        # Abramowitz-Stegun 26.2.23 rational approximation for inverse
        # normal — sufficient precision for VaR/CVaR use.
        if p < 0.5:
            t = (-2.0 * math.log(p)) ** 0.5
        else:
            t = (-2.0 * math.log(1 - p)) ** 0.5
        c0, c1, c2 = 2.515517, 0.802853, 0.010328
        d1, d2, d3 = 1.432788, 0.189269, 0.001308
        z = t - (c0 + c1*t + c2*t**2) / (1 + d1*t + d2*t**2 + d3*t**3)
        if p < 0.5:
            z = -z
        # Cornish-Fisher adjustment
        z_cf = (
            z
            + (z**2 - 1) / 6 * S
            + (z**3 - 3*z) / 24 * K
            - (2*z**3 - 5*z) / 36 * S**2
        )
        return z_cf

    # Integrate from p=1e-4 to p=(1-confidence) in log-spaced bins,
    # then average: CVaR_α = -E[X | X ≤ VaR_α] = -∫_0^α q(p) dp / α
    alpha = 1.0 - confidence
    ps = [alpha * (i + 0.5) / n_quantiles for i in range(n_quantiles)]
    z_cfs = [_cf_quantile(p) for p in ps]
    loss_samples = [-(mean_return + z * vol) for z in z_cfs]
    cvar = sum(loss_samples) / len(loss_samples)
    return _clip_to_loss_bound(cvar)


# ═══════════════════════════════════════════════════════════════════════════
# Portfolio construction
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# Phase 2: pairwise correlation matrix + issuer-tier preference weighting
# ═══════════════════════════════════════════════════════════════════════════

# Issuer tiers (Day-3 Phase-2 directive).
# Tier A +2% allocation nudge, Tier C -2% nudge, Tier B neutral.
_ISSUER_TIER_A: frozenset[str] = frozenset({
    "BlackRock iShares", "BlackRock", "Fidelity",
})
_ISSUER_TIER_C_TICKERS: frozenset[str] = frozenset({
    "GBTC",   # Grayscale legacy high-fee BTC (150 bps)
    "ETHE",   # Grayscale legacy high-fee ETH (250 bps)
    "DEFI",   # Hashdex futures-based, not spot — structural Tier C
    "XRPR",   # REX-Osprey swaps-wrapped XRP (94 bps) — higher-fee 40-Act
    "BITW",   # Bitwise 10 closed-end during conversion — 250 bps legacy
})
# Everything else → Tier B (neutral).

_TIER_A_NUDGE: float = +2.0
_TIER_B_NUDGE: float =  0.0
_TIER_C_NUDGE: float = -2.0


def _issuer_tier_nudge(etf: dict) -> float:
    """Return the issuer-tier allocation nudge (pct points) for an ETF."""
    if etf.get("ticker") in _ISSUER_TIER_C_TICKERS:
        return _TIER_C_NUDGE
    if etf.get("issuer") in _ISSUER_TIER_A:
        return _TIER_A_NUDGE
    return _TIER_B_NUDGE


# Category-pair correlation targets (Phase-2 pairwise model).
# Applied on top of each ETF's individual volatility to build the full NxN cov matrix.
_CATEGORY_PAIR_CORR: dict[tuple[str, str], float] = {
    # Within-category (different issuers, same underlying exposure)
    ("btc_spot",            "btc_spot"):            0.97,
    ("eth_spot",            "eth_spot"):            0.95,
    ("btc_futures",         "btc_futures"):         0.95,
    ("eth_futures",         "eth_futures"):         0.93,
    ("altcoin_spot",        "altcoin_spot"):        0.65,  # different alts ≠ same risk
    ("thematic",            "thematic"):            0.85,
    ("thematic_equity",     "thematic_equity"):     0.80,
    ("leveraged",           "leveraged"):           0.80,
    ("income_covered_call", "income_covered_call"): 0.75,
    ("multi_asset",         "multi_asset"):         0.95,

    # Cross-category (symmetric — lookup handles order)
    ("btc_spot",  "eth_spot"):        0.75,
    ("btc_spot",  "btc_futures"):     0.92,
    ("btc_spot",  "eth_futures"):     0.70,
    ("btc_spot",  "altcoin_spot"):    0.72,
    ("btc_spot",  "thematic"):        0.72,
    ("btc_spot",  "thematic_equity"): 0.60,
    ("btc_spot",  "leveraged"):       0.88,  # most leveraged is BTC-linked
    ("btc_spot",  "income_covered_call"): 0.70,
    ("btc_spot",  "multi_asset"):     0.85,

    ("eth_spot",  "btc_futures"):     0.72,
    ("eth_spot",  "eth_futures"):     0.92,
    ("eth_spot",  "altcoin_spot"):    0.72,
    ("eth_spot",  "thematic"):        0.74,
    ("eth_spot",  "thematic_equity"): 0.55,
    ("eth_spot",  "leveraged"):       0.70,
    ("eth_spot",  "income_covered_call"): 0.62,
    ("eth_spot",  "multi_asset"):     0.82,

    ("altcoin_spot", "thematic"):        0.68,
    ("altcoin_spot", "thematic_equity"): 0.55,
    ("altcoin_spot", "leveraged"):       0.65,
    ("altcoin_spot", "income_covered_call"): 0.55,
    ("altcoin_spot", "multi_asset"):     0.75,

    ("thematic_equity", "leveraged"):       0.60,
    ("thematic_equity", "income_covered_call"): 0.70,  # many covered-call ETFs are on COIN/MSTR equity
    ("thematic_equity", "multi_asset"):     0.58,

    ("leveraged",       "income_covered_call"): 0.68,
    ("leveraged",       "multi_asset"):     0.75,

    ("income_covered_call", "multi_asset"): 0.58,

    ("btc_futures", "thematic"):     0.70,

    # Defined-outcome buffered BTC ETFs (Calamos CBOJ-series).
    # Within-category: all track BTC with the same buffer structure, so
    # they are highly self-correlated (~0.95) but decoupled from BTC on
    # drawdown days (hence the 0.55 cross with btc_spot — asymmetric
    # correlation in reality, we model the blended annualized number).
    ("defined_outcome", "defined_outcome"):     0.95,
    ("defined_outcome", "btc_spot"):            0.55,
    ("defined_outcome", "eth_spot"):            0.45,
    ("defined_outcome", "btc_futures"):         0.55,
    ("defined_outcome", "eth_futures"):         0.42,
    ("defined_outcome", "altcoin_spot"):        0.45,
    ("defined_outcome", "thematic"):            0.40,
    ("defined_outcome", "thematic_equity"):     0.35,
    ("defined_outcome", "leveraged"):           0.50,
    ("defined_outcome", "income_covered_call"): 0.45,
    ("defined_outcome", "multi_asset"):         0.50,

    # 2026-04-27 audit-round-3 follow-up: explicit cross-pairs for the
    # remaining 11 combinations the audit found falling through to the
    # generic 0.70 fallback. Values calibrated to keep the pairwise
    # correlation matrix internally consistent with the BTC/ETH cluster
    # already defined above.
    ("altcoin_spot",        "btc_futures"):     0.65,
    ("altcoin_spot",        "eth_futures"):     0.62,
    ("btc_futures",         "eth_futures"):     0.85,
    ("btc_futures",         "income_covered_call"): 0.62,
    ("btc_futures",         "leveraged"):       0.82,
    ("btc_futures",         "multi_asset"):     0.78,
    ("btc_futures",         "thematic_equity"): 0.55,
    ("eth_futures",         "income_covered_call"): 0.55,
    ("eth_futures",         "leveraged"):       0.65,
    ("eth_futures",         "multi_asset"):     0.72,
    ("eth_futures",         "thematic_equity"): 0.50,
}


# Track cross-category pairs we've already warned about so the log doesn't
# spam on every covariance-matrix build. Session-scoped.
_warned_missing_pairs: set[tuple[str, str]] = set()


def _pair_corr(cat_i: str, cat_j: str) -> float:
    """
    Lookup pairwise correlation between two categories (symmetric).
    Emits a one-time warning per missing pair so incomplete category
    coverage surfaces in the logs during development, rather than
    silently falling back to the generic 0.70 estimate.
    """
    if cat_i == cat_j:
        val = _CATEGORY_PAIR_CORR.get((cat_i, cat_j))
        if val is None:
            key = (cat_i, cat_i)
            if key not in _warned_missing_pairs:
                _warned_missing_pairs.add(key)
                logger.warning(
                    "Missing within-category correlation for %r — using 0.90 default.",
                    cat_i,
                )
            return 0.90
        return val

    key = (cat_i, cat_j) if (cat_i, cat_j) in _CATEGORY_PAIR_CORR else (cat_j, cat_i)
    val = _CATEGORY_PAIR_CORR.get(key)
    if val is None:
        canonical = tuple(sorted((cat_i, cat_j)))
        if canonical not in _warned_missing_pairs:
            _warned_missing_pairs.add(canonical)
            logger.warning(
                "Missing cross-category correlation for %r × %r — using 0.70 default.",
                cat_i, cat_j,
            )
        return 0.70
    return val


def _build_covariance_matrix(holdings: list[dict]) -> np.ndarray:
    """
    Phase-2 pairwise covariance: cov[i,j] = corr(cat_i, cat_j) * vol_i * vol_j.

    Same-issuer holdings within a category get a small extra correlation
    boost (+0.02) — operational overlap (custodian, rehypothecation path).
    """
    n = len(holdings)
    cov = np.zeros((n, n))
    vols = np.array([h["volatility_pct"] for h in holdings], dtype=float)
    cats = [h["category"] for h in holdings]
    issuers = [h.get("issuer", "") for h in holdings]

    for i in range(n):
        cov[i, i] = vols[i] ** 2
        for j in range(i + 1, n):
            base = _pair_corr(cats[i], cats[j])
            # Same-issuer boost within the same category
            if cats[i] == cats[j] and issuers[i] and issuers[i] == issuers[j]:
                base = min(0.99, base + 0.02)
            cov_ij = base * vols[i] * vols[j]
            cov[i, j] = cov_ij
            cov[j, i] = cov_ij
    return cov


# 2026-04-26 audit-round-1 bonus 1+5 (audit-fix 2026-04-30): per-ETF AUM
# lookup used as a liquidity tiebreaker inside _select_etfs_for_category.
# When two ETFs in the same category share the same expense ratio, prefer
# the larger fund (smaller bid/ask, less rebalance market impact, lower
# closure risk).
#
# Source: routed through `integrations.etf_flow_data.get_etf_aum()` —
# the canonical no-fallback chain (yfinance → SEC EDGAR N-PORT → EDGAR
# XBRL companyfacts → ETF.com → issuer-site → production-snapshot).
# None → fund treated as "smaller than any priced peer" so it loses ties;
# never blocks selection.
#
# Audit-fix (HIGH, 2026-04-30): the prior `_AUM_REFERENCE_STUB_USD` dict
# (14 hardcoded values for major spot ETFs) violated CLAUDE.md §22's
# no-fallback policy — the values silently aged and quietly biased
# selection over time. Removed. The chain's production-snapshot step
# (`core/etf_flow_production.json`, refreshed by the nightly capture
# script) plays the same "still works when yfinance is down" role
# without the staleness-rot risk: when the snapshot ages past usefulness,
# the entry returns None and the tiebreaker degrades gracefully.

_AUM_LIVE_MEMO: dict[str, tuple[float | None, float]] = {}
_AUM_MEMO_TTL_SEC: int = 24 * 3600


def _get_aum_usd(ticker: str) -> float | None:
    """
    Best-effort AUM in USD for a ticker. Returns None when the chain
    exhausts — caller treats None as "smallest in tie group" so the
    tiebreaker degrades gracefully.

    Routes through `integrations.etf_flow_data.get_etf_aum()` which
    handles the full no-fallback chain (yfinance → EDGAR → ETF.com →
    issuer-site → production-snapshot). 24h memo on the result so the
    portfolio-build hot path doesn't re-traverse the chain per call.

    DEMO_MODE_NO_FETCH=1 (test harness) makes the chain consult only
    the production-snapshot — fast + deterministic for AppTest.
    """
    import os
    import time as _time

    tkr = (ticker or "").upper()
    if not tkr:
        return None

    cached = _AUM_LIVE_MEMO.get(tkr)
    if cached is not None:
        val, ts = cached
        if _time.monotonic() - ts < _AUM_MEMO_TTL_SEC:
            return val

    try:
        from integrations.etf_flow_data import get_etf_aum
        v, _src = get_etf_aum(tkr)
        # Guard against test-time mocks returning surrogate types
        if v is None:
            _AUM_LIVE_MEMO[tkr] = (None, _time.monotonic())
            return None
        try:
            v_float = float(v)
        except (TypeError, ValueError):
            _AUM_LIVE_MEMO[tkr] = (None, _time.monotonic())
            return None
        if v_float > 0:
            _AUM_LIVE_MEMO[tkr] = (v_float, _time.monotonic())
            return v_float
        _AUM_LIVE_MEMO[tkr] = (None, _time.monotonic())
        return None
    except Exception:  # pragma: no cover — defensive; chain must never raise
        _AUM_LIVE_MEMO[tkr] = (None, _time.monotonic())
        return None


def _select_etfs_for_category(
    category: str,
    universe: list[dict],
    compliance_filter_on: bool = True,
) -> list[dict]:
    """
    Select up to MAX_ETFS_PER_CATEGORY ETFs from a category.
    Prefers lowest expense ratio; breaks ties by AUM (larger first), then
    issuer diversity, then ticker for deterministic ordering.

    `compliance_filter_on=True` (default) applies the fiduciary-
    appropriate restrictions — blocks single-stock covered-call
    wrappers (MSTY/CONY/MSFO/etc.) even when the tier allocates to
    the income_covered_call category. Leveraged is blocked at the
    category level upstream of this function.

    2026-04-26 audit-round-1 bonus 1: AUM tiebreaker added between
    expense-ratio sort and the issuer-diversity pass. When two funds
    have the same fee (e.g., BlackRock + Bitwise BTC spots both at
    25 bps), the larger fund wins the slot — fairer liquidity proxy
    than coin-flip-equivalent ties.
    """
    from core.risk_tiers import category_allowed

    in_cat = [
        u for u in universe
        if u.get("category") == category
        and category_allowed(category, u.get("ticker", ""), compliance_filter_on)
    ]
    if not in_cat:
        return []

    # Deterministic sort: by expense_ratio asc (missing → large), then by
    # AUM desc (larger first — None treated as 0 so it loses ties), then
    # ticker for tertiary determinism.
    def _key(u: dict) -> tuple[float, float, str]:
        er = u.get("expense_ratio_bps")
        aum = _get_aum_usd(u.get("ticker", ""))
        return (
            float(er) if er is not None else 9999.0,
            -(float(aum) if aum is not None else 0.0),   # negate for desc
            str(u.get("ticker", "")),
        )

    in_cat.sort(key=_key)

    # Issuer diversity pass: prefer not to pick two from same issuer
    selected: list[dict] = []
    seen_issuers: set[str] = set()
    for etf in in_cat:
        issuer = str(etf.get("issuer", "")).strip()
        if issuer in seen_issuers and len(selected) < MAX_ETFS_PER_CATEGORY:
            continue
        selected.append(etf)
        seen_issuers.add(issuer)
        if len(selected) >= MAX_ETFS_PER_CATEGORY:
            break

    # If issuer-diversity left us short, fill from remaining (ignoring diversity)
    if len(selected) < MAX_ETFS_PER_CATEGORY:
        for etf in in_cat:
            if etf in selected:
                continue
            selected.append(etf)
            if len(selected) >= MAX_ETFS_PER_CATEGORY:
                break

    return selected


def build_portfolio(
    tier_name: str,
    universe: list[dict],
    portfolio_value_usd: float = 100_000,
    compliance_filter_on: bool = True,
) -> dict:
    """
    Build a fully specified crypto-ETF portfolio for a given risk tier.

    Minimal universe-entry shape (each entry is a dict):
        ticker:                  str  (required)
        category:                str  (required; keys in TIER_CATEGORY_ALLOCATIONS)
        expected_return:         float  annualized %, used by MC drift
        volatility:              float  annualized %, used by MC diffusion + VaR
        correlation_with_btc:    float  in [-1, 1], used by covariance
        issuer:                  str  optional, used by diversity selection
        expense_ratio_bps:       float  optional, used by selection sort
        name:                    str  optional, for display only

    Returns a dict with: tier metadata, holdings list, category_summary,
    metrics, timestamp.
    """
    if not universe:
        return _empty_portfolio(tier_name, portfolio_value_usd)

    # Phase-1 ETH correlation guard removed on Day 3 — pairwise correlation
    # model (Phase 2) handles ETH-based ETFs correctly without a blanket
    # warning. See docs/port_log.md for the disposition record.

    tier_meta = PORTFOLIO_TIERS.get(tier_name)
    if tier_meta is None:
        raise ValueError(
            f"Unknown tier: {tier_name!r}. "
            f"Expected one of {list(PORTFOLIO_TIERS)}."
        )

    category_allocs = allocation_for_tier(tier_name)

    holdings: list[dict] = []
    used_weight_pct = 0.0

    # When compliance filter is ON, skip categories that are fully
    # restricted (e.g., leveraged) — their allocation weight redistributes
    # proportionally to the remaining categories.
    from core.risk_tiers import COMPLIANCE_RESTRICTED_CATEGORIES

    if compliance_filter_on:
        blocked_cats = {
            c for c in category_allocs
            if c in COMPLIANCE_RESTRICTED_CATEGORIES
        }
        if blocked_cats:
            blocked_weight = sum(category_allocs[c] for c in blocked_cats)
            kept_allocs = {
                c: w for c, w in category_allocs.items()
                if c not in blocked_cats
            }
            if kept_allocs:
                scale = 100.0 / sum(kept_allocs.values())
                category_allocs = {c: w * scale for c, w in kept_allocs.items()}
            # else: everything was blocked (unlikely); leave as-is

    for category, cat_weight in category_allocs.items():
        if cat_weight <= 0:
            continue
        chosen = _select_etfs_for_category(
            category, universe, compliance_filter_on=compliance_filter_on
        )
        if not chosen:
            continue

        per_asset_weight = cat_weight / len(chosen)

        # Phase-2 issuer-tier preference nudge: apply +/- within the
        # category, then renormalize so the category total still equals cat_weight.
        nudges = [_issuer_tier_nudge(etf) for etf in chosen]
        raw_weights: list[float] = [
            max(0.5, per_asset_weight + n) for n in nudges
        ]
        total_raw = sum(raw_weights)
        scale = cat_weight / total_raw if total_raw > 0 else 0
        adjusted = [min(w * scale, MAX_SINGLE_POSITION_PCT) for w in raw_weights]

        for etf, weight, nudge in zip(chosen, adjusted, nudges):
            usd_val = portfolio_value_usd * weight / 100
            holdings.append({
                "ticker":               etf["ticker"],
                "name":                 etf.get("name", etf["ticker"]),
                "issuer":               etf.get("issuer", ""),
                "category":             category,
                "weight_pct":           round(weight, 4),
                "usd_value":            round(usd_val, 2),
                "expected_return_pct":  float(etf.get("expected_return", 0.0)),
                "volatility_pct":       float(etf.get("volatility", 0.0)),
                "correlation_with_btc": float(etf.get("correlation_with_btc", 1.0)),
                "expense_ratio_bps":    etf.get("expense_ratio_bps"),
                "issuer_tier_nudge":    nudge,
            })
            used_weight_pct += weight

    # Normalize to 100 — last holding absorbs rounding remainder
    if holdings and used_weight_pct > 0:
        scale = 100.0 / used_weight_pct
        for h in holdings[:-1]:
            h["weight_pct"] = round(h["weight_pct"] * scale, 4)
            h["usd_value"] = round(portfolio_value_usd * h["weight_pct"] / 100, 2)
        holdings[-1]["weight_pct"] = round(
            100 - sum(h["weight_pct"] for h in holdings[:-1]), 4
        )
        holdings[-1]["usd_value"] = round(
            portfolio_value_usd * holdings[-1]["weight_pct"] / 100, 2
        )

    metrics = compute_portfolio_metrics(holdings, portfolio_value_usd, tier_name)

    category_summary: dict[str, dict] = {}
    for h in holdings:
        cat = h["category"]
        entry = category_summary.setdefault(cat, {
            "weight_pct": 0.0,
            "usd_value": 0.0,
            "count": 0,
        })
        entry["weight_pct"] += h["weight_pct"]
        entry["usd_value"]  += h["usd_value"]
        entry["count"]      += 1
    for entry in category_summary.values():
        entry["weight_pct"] = round(entry["weight_pct"], 2)
        entry["usd_value"]  = round(entry["usd_value"], 2)

    return {
        "tier_name":              tier_name,
        "tier_number":            tier_meta["tier_number"],
        "tier_meta":              dict(tier_meta),
        "portfolio_value_usd":    portfolio_value_usd,
        "holdings":               holdings,
        "category_summary":       category_summary,
        "metrics":                metrics,
        "timestamp":              datetime.now(timezone.utc).isoformat(),
    }


def _empty_portfolio(tier_name: str, portfolio_value_usd: float) -> dict:
    tier_meta = PORTFOLIO_TIERS.get(tier_name, {})
    return {
        "tier_name":           tier_name,
        "tier_number":         tier_meta.get("tier_number"),
        "tier_meta":           dict(tier_meta),
        "portfolio_value_usd": portfolio_value_usd,
        "holdings":            [],
        "category_summary":    {},
        "metrics":             _empty_metrics(),
        "timestamp":           datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Portfolio metrics
# ═══════════════════════════════════════════════════════════════════════════

def compute_portfolio_metrics(
    holdings: list[dict],
    portfolio_value: float,
    tier_name: str,
) -> dict:
    """
    Full risk/return metric suite for a portfolio.

    Returns dict with: weighted_return_pct, annual_return_usd,
    monthly_income_usd, portfolio_volatility_pct, sharpe_ratio,
    sortino_ratio, calmar_ratio, max_drawdown_pct, var_95_pct,
    var_99_pct, cvar_95_pct, cvar_99_pct, diversification_ratio,
    excess_return_pct, n_holdings.

    Phase-2 correlation model: full pairwise via _build_covariance_matrix.
    Retuned cluster targets per Day-3 directive:
      btc_spot internal:    0.97
      eth_spot internal:    0.95
      btc_futures internal: 0.95
      thematic internal:    0.85
      btc_spot ↔ eth_spot:  0.75  (BTC↔ETH cluster crossover)
      btc_spot ↔ futures:   0.92  (same underlying, different structure)
      Same-issuer within category: +0.02 boost (operational overlap).
    """
    if not holdings:
        return _empty_metrics()

    n = len(holdings)
    weights = np.array([h["weight_pct"] / 100 for h in holdings])
    returns = np.array([h["expected_return_pct"] for h in holdings], dtype=float)
    vols    = np.array([h["volatility_pct"] for h in holdings], dtype=float)

    # Portfolio expected return
    weighted_return = float(np.dot(weights, returns))
    annual_return_usd = portfolio_value * weighted_return / 100

    # Covariance matrix — Phase-2 pairwise
    cov_matrix = _build_covariance_matrix(holdings)

    portfolio_var = float(weights @ cov_matrix @ weights)
    portfolio_vol = math.sqrt(max(portfolio_var, 0))

    live_rf = get_live_risk_free_rate()
    excess_return = weighted_return - live_rf
    sharpe = excess_return / max(portfolio_vol, 0.01)

    # Sortino — MAR=live_rf on both sides (Sortino & van der Meer 1991) — verbatim
    d_ratio = (weighted_return - live_rf) / max(portfolio_vol, 1e-6)
    phi_d = math.exp(-0.5 * d_ratio**2) / math.sqrt(2.0 * math.pi)
    cdf_neg_d = 0.5 * (1.0 - math.erf(d_ratio / math.sqrt(2.0)))
    dd_var = portfolio_vol**2 * max(
        0.0, (d_ratio**2 + 1) * cdf_neg_d - d_ratio * phi_d
    )
    downside_vol = math.sqrt(dd_var) if dd_var > 0 else portfolio_vol * 0.5
    sortino = excess_return / max(downside_vol, 0.01)

    # Magdon-Ismail & Atiya (2004) max-drawdown approximation.
    # Prior code used fixed multiplier f=2.7. Apr 2026 P1 recalibration
    # makes f Sharpe-dependent per the Magdon-Ismail paper's own
    # Table 1 (higher Sharpe → lower f because drift overpowers vol):
    #   Sharpe = -0.5  →  f ≈ 3.2
    #   Sharpe =  0    →  f ≈ 2.7  (zero-drift Brownian)
    #   Sharpe =  0.5  →  f ≈ 2.4
    #   Sharpe =  1.0  →  f ≈ 2.1
    # Crypto portfolios often run Sharpe in [0.2, 1.0] so the correct
    # f lands in [2.1, 2.5], making the prior constant 2.7 slightly
    # conservative but not wildly wrong.
    f_mdd = float(np.interp(
        sharpe,
        [-0.5, 0.0, 0.5, 1.0, 1.5],
        [3.2, 2.7, 2.4, 2.1, 1.9],
    ))
    tier_meta = PORTFOLIO_TIERS.get(tier_name, {})
    max_drawdown_ceiling = tier_meta.get("max_drawdown_pct", 60)
    max_drawdown = min(portfolio_vol * f_mdd, max_drawdown_ceiling)

    calmar = weighted_return / max(max_drawdown, 0.01)

    # VaR + CVaR — Cornish-Fisher with PER-CATEGORY S/K aggregated by
    # holding weight (2026-04-28 polish round 5 Sprint 1 commit 2).
    # Prior single-pair S=-0.7 / K=8.0 was a crypto-midpoint default;
    # now we look up each category's calibrated values from
    # data/cf_params_cache.json (built by core.cf_calibration.fit_per_category)
    # and weight-aggregate. Cache miss / stale → falls back to the prior
    # crypto-midpoint defaults (no regression risk).
    skew_w, kurt_w = _weighted_cf_params(holdings)
    # 2026-04-28 hotfix: cornish_fisher_var/cvar return CFRiskResult
    # (NamedTuple). The `.value` is the loss percentage clipped to the
    # long-only 100% bound; `.cf_boundary_reached` is True when the
    # unclipped polynomial estimate exceeded that bound. We propagate
    # the boundary flag into the metrics dict so the UI risk panel can
    # surface "≤ -100% / model boundary" with the CF-boundary footnote.
    var_95 = cornish_fisher_var(weighted_return, portfolio_vol, 0.95,
                                skew=skew_w, excess_kurt=kurt_w)
    var_99 = cornish_fisher_var(weighted_return, portfolio_vol, 0.99,
                                skew=skew_w, excess_kurt=kurt_w)
    cvar_95 = cornish_fisher_cvar(weighted_return, portfolio_vol, 0.95,
                                  skew=skew_w, excess_kurt=kurt_w)
    cvar_99 = cornish_fisher_cvar(weighted_return, portfolio_vol, 0.99,
                                  skew=skew_w, excess_kurt=kurt_w)

    weighted_avg_vol = float(np.dot(weights, vols))
    diversification_r = weighted_avg_vol / max(portfolio_vol, 0.01)

    monthly_income_usd = annual_return_usd / 12

    def _sr(val: float, decimals: int = 3) -> float:
        try:
            return round(val, decimals) if math.isfinite(val) else 0.0
        except (TypeError, ValueError):
            return 0.0

    return {
        "weighted_return_pct":     _sr(weighted_return, 3),
        "annual_return_usd":       _sr(annual_return_usd, 2),
        "monthly_income_usd":      _sr(monthly_income_usd, 2),
        "portfolio_volatility_pct":_sr(portfolio_vol, 3),
        "sharpe_ratio":            _sr(sharpe, 3),
        "sortino_ratio":           _sr(sortino, 3),
        "calmar_ratio":            _sr(calmar, 3),
        "max_drawdown_pct":        _sr(max_drawdown, 3),
        "var_95_pct":              _sr(var_95.value, 3),
        "var_99_pct":              _sr(var_99.value, 3),
        "cvar_95_pct":             _sr(cvar_95.value, 3),
        "cvar_99_pct":             _sr(cvar_99.value, 3),
        # CF-boundary flags (2026-04-28 hotfix). True at any confidence
        # → UI risk panel shows "≤ -100% / model boundary" + the
        # CF-boundary footnote linking to methodology.
        "var_95_cf_boundary_reached":  bool(var_95.cf_boundary_reached),
        "var_99_cf_boundary_reached":  bool(var_99.cf_boundary_reached),
        "cvar_95_cf_boundary_reached": bool(cvar_95.cf_boundary_reached),
        "cvar_99_cf_boundary_reached": bool(cvar_99.cf_boundary_reached),
        "any_cf_boundary_reached":     bool(
            var_95.cf_boundary_reached or var_99.cf_boundary_reached
            or cvar_95.cf_boundary_reached or cvar_99.cf_boundary_reached
        ),
        "diversification_ratio":   _sr(diversification_r, 3),
        "excess_return_pct":       _sr(excess_return, 3),
        "n_holdings":              n,
    }


def optimize_min_variance(
    holdings: list[dict],
    target_return_pct: float | None = None,
    max_single_position_pct: float = 30.0,
) -> dict:
    """
    Mean-Variance Optimization (Markowitz 1952) — partner feedback
    #7: "reduce risk while maintaining the same or similar return."

    Given the CURRENT basket (same tickers, same category weights'
    constituents), solve for the weight vector that MINIMIZES
    portfolio variance subject to:
        1. weights sum to 1
        2. each weight in [0, max_single_position_pct / 100]
        3. portfolio expected return >= target_return_pct (if given);
           default is the current weighted return.

    Returns a dict with:
        "optimized_weights":   {ticker: pct} — new allocation
        "original_vol_pct":    float — portfolio σ under current weights
        "optimized_vol_pct":   float — portfolio σ under optimized weights
        "vol_reduction_pct":   float — (original - optimized) / original × 100
        "expected_return_pct": float — met target
        "status":              "optimal" | "infeasible" | "unchanged"

    Uses scipy.optimize.minimize with SLSQP. Covariance matrix is the
    same pairwise one that compute_portfolio_metrics uses, so math
    is internally consistent.
    """
    if not holdings or len(holdings) < 2:
        return {"status": "unchanged",
                "reason": "need at least 2 holdings to optimize"}

    import numpy as _np
    try:
        from scipy.optimize import minimize
    except ImportError:
        return {"status": "infeasible",
                "reason": "scipy not available at runtime"}

    n = len(holdings)
    w_current = _np.array([h["weight_pct"] / 100.0 for h in holdings])
    returns = _np.array([h["expected_return_pct"] for h in holdings], dtype=float)
    cov = _build_covariance_matrix(holdings)

    def _portfolio_variance(w: _np.ndarray) -> float:
        return float(w @ cov @ w)

    def _portfolio_return(w: _np.ndarray) -> float:
        return float(w @ returns)

    # Current-state benchmarks
    current_vol = math.sqrt(max(_portfolio_variance(w_current), 0))
    current_return = _portfolio_return(w_current)

    # If no target given, use the current portfolio return as the
    # constraint — "same return, lower vol."
    target = target_return_pct if target_return_pct is not None else current_return

    w_max = max_single_position_pct / 100.0
    bounds = [(0.0, w_max) for _ in range(n)]
    constraints = [
        {"type": "eq", "fun": lambda w: float(_np.sum(w) - 1.0)},
        {"type": "ineq", "fun": lambda w, t=target: _portfolio_return(w) - t},
    ]

    # 2026-04-27 audit-round-4: SLSQP can fail with "Positive directional
    # derivative for linesearch" on tightly-constrained baskets where
    # the current weights sit near the efficient frontier. Three-attempt
    # robustness sequence:
    #   1. SLSQP from current weights with strict target.
    #   2. SLSQP from equal-weight starting point with strict target.
    #   3. SLSQP with target relaxed by 5% (e.g., target 28% -> 26.6%).
    # If all three fail we fall back to "unchanged" rather than the
    # alarming "infeasible" verdict — the FA is already close to optimal.
    def _try_slsqp(w_init: "_np.ndarray", t_target: float) -> "object":
        cs = [
            {"type": "eq",   "fun": lambda w: float(_np.sum(w) - 1.0)},
            {"type": "ineq", "fun": lambda w, t=t_target: _portfolio_return(w) - t},
        ]
        try:
            return minimize(
                _portfolio_variance, w_init,
                method="SLSQP", bounds=bounds, constraints=cs,
                options={"maxiter": 300, "ftol": 1e-9},
            )
        except Exception:
            return None

    result = _try_slsqp(w_current, target)
    if result is None or not result.success:
        # Attempt 2: equal-weight start.
        w_eq = _np.full(n, 1.0 / n)
        result = _try_slsqp(w_eq, target)
    if result is None or not result.success:
        # Attempt 3: relax target by 5%.
        result = _try_slsqp(w_current, target * 0.95)
    if result is None or not result.success:
        # Graceful fallback: report unchanged with current vol so the UI
        # tells the FA "already near efficient frontier" rather than
        # "infeasible" (which sounds alarming for a non-error).
        return {
            "status": "unchanged",
            "reason": "Current allocation is already near the efficient "
                      "frontier for this tier (solver could not find a "
                      "feasible improvement after 3 attempts).",
            "original_vol_pct":    round(current_vol, 3),
            "expected_return_pct": round(current_return, 3),
        }

    w_opt = _np.clip(result.x, 0.0, w_max)
    # Re-normalize in case the solver drifts fractionally.
    w_opt = w_opt / max(_np.sum(w_opt), 1e-9)

    opt_vol = math.sqrt(max(float(w_opt @ cov @ w_opt), 0))
    opt_return = float(w_opt @ returns)

    optimized_weights = {
        holdings[i]["ticker"]: round(float(w_opt[i]) * 100.0, 3)
        for i in range(n)
    }

    reduction = (current_vol - opt_vol) / current_vol * 100.0 if current_vol > 0 else 0.0

    return {
        "status":               "optimal",
        "optimized_weights":    optimized_weights,
        "original_vol_pct":     round(current_vol, 3),
        "optimized_vol_pct":    round(opt_vol, 3),
        "vol_reduction_pct":    round(reduction, 2),
        "expected_return_pct":  round(opt_return, 3),
        "target_return_pct":    round(target, 3),
        "n_holdings":           n,
    }


def _empty_metrics() -> dict:
    return {
        "weighted_return_pct": 0, "annual_return_usd": 0, "monthly_income_usd": 0,
        "portfolio_volatility_pct": 0, "sharpe_ratio": 0, "sortino_ratio": 0,
        "calmar_ratio": 0, "max_drawdown_pct": 0, "var_95_pct": 0,
        "var_99_pct": 0, "cvar_95_pct": 0, "cvar_99_pct": 0,
        # CF-boundary flags (2026-04-28 hotfix) — empty portfolio never
        # reaches the boundary since it has no risk to model.
        "var_95_cf_boundary_reached":  False,
        "var_99_cf_boundary_reached":  False,
        "cvar_95_cf_boundary_reached": False,
        "cvar_99_cf_boundary_reached": False,
        "any_cf_boundary_reached":     False,
        "diversification_ratio": 1, "excess_return_pct": 0, "n_holdings": 0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Monte Carlo simulation
# ═══════════════════════════════════════════════════════════════════════════

def _mc_cache_key(
    portfolio: dict,
    n_simulations: int,
    paths_retain: int,
    seed: int,
) -> str:
    """
    Cache key includes seed + tier + universe hash + paths-retained count
    per planning-side Risk 4 directive.
    """
    try:
        holdings = portfolio.get("holdings", [])
        payload = json.dumps({
            "tier_name":     portfolio.get("tier_name"),
            "value":         portfolio.get("portfolio_value_usd"),
            "n_holdings":    len(holdings),
            "tickers":       sorted(h.get("ticker", "") for h in holdings),
            "weights":       sorted(round(h.get("weight_pct", 0), 4) for h in holdings),
            "n_simulations": n_simulations,
            "paths_retain":  paths_retain,
            "seed":          seed,
        }, sort_keys=True)
        return hashlib.md5(payload.encode()).hexdigest()
    except Exception as exc:
        logger.debug("MC cache key failed: %s", exc)
        return hashlib.md5(str(time.time()).encode()).hexdigest()


def run_monte_carlo(
    portfolio: dict,
    n_simulations: int | None = None,
    horizon_days: int = MC_HORIZON_DAYS,
    seed: int = DEFAULT_SEED,
    paths_retain: int | None = None,
) -> dict:
    """
    Jump-diffusion Monte Carlo simulation (Merton 1976 + Student-t ν=4).

    Computes `n_simulations` paths (default MONTE_CARLO_PATHS_COMPUTE=10,000)
    for accurate percentile math, but only retains `paths_retain` paths
    (default MONTE_CARLO_PATHS_RETAIN=250) in the output `sample_paths`
    for UI rendering — per planning-side Risk 4.

    Seed defaults to DEFAULT_SEED=42 so the determinism-lock canary test
    in tests/test_portfolio_engine.py can assert bit-exact reproduction.

    Returns dict with: initial_value_usd, horizon_days, n_simulations,
    percentile_5/25/50/75/95, mean_final_value, prob_loss_pct,
    prob_10pct_gain_pct, avg_max_drawdown_pct, sample_paths, hist_counts,
    hist_edges.
    """
    holdings = portfolio.get("holdings", [])
    if not holdings:
        return {}

    if n_simulations is None:
        n_simulations = MONTE_CARLO_PATHS_COMPUTE
    if paths_retain is None:
        paths_retain = MONTE_CARLO_PATHS_RETAIN
    paths_retain = min(paths_retain, n_simulations)

    cache_key = _mc_cache_key(portfolio, n_simulations, paths_retain, seed)
    cached = _mc_cache.get(cache_key)
    if cached and (time.time() - cached.get("_ts", 0)) < _MC_CACHE_TTL:
        return {k: v for k, v in cached.items() if k != "_ts"}

    metrics = portfolio.get("metrics") or {}
    initial_value = float(portfolio.get("portfolio_value_usd", 100_000))
    daily_return = float(metrics.get("weighted_return_pct", 5)) / 100 / TRADING_DAYS
    daily_vol = float(metrics.get("portfolio_volatility_pct", 5)) / 100 / math.sqrt(TRADING_DAYS)

    rng = np.random.default_rng(seed)

    dt = 1
    mu_gbm = daily_return - 0.5 * daily_vol**2
    sigma = daily_vol

    # Merton jump-diffusion — preserved verbatim from rwa portfolio.py
    jump_intensity = 0.50 / TRADING_DAYS   # ~0.5 jumps/year
    jump_mean = -0.04
    jump_std = 0.09

    # Student-t ν=4 diffusion — preserved verbatim
    T_DOF = 4
    t_std = (T_DOF / (T_DOF - 2)) ** 0.5   # ≈ 1.414
    z_raw = rng.standard_t(T_DOF, size=(n_simulations, horizon_days))
    z = z_raw / t_std                      # standardize to unit variance
    jump_counts = rng.poisson(jump_intensity, (n_simulations, horizon_days))
    jump_sizes = rng.normal(jump_mean, jump_std, (n_simulations, horizon_days))

    log_returns = mu_gbm * dt + sigma * math.sqrt(dt) * z + jump_counts * jump_sizes
    cumulative = np.exp(np.cumsum(log_returns, axis=1))
    final_values = initial_value * cumulative[:, -1]

    p5  = float(np.percentile(final_values,  5))
    p25 = float(np.percentile(final_values, 25))
    p50 = float(np.percentile(final_values, 50))
    p75 = float(np.percentile(final_values, 75))
    p95 = float(np.percentile(final_values, 95))
    mean = float(np.mean(final_values))

    prob_loss = float(np.mean(final_values < initial_value) * 100)
    prob_10pct = float(np.mean(final_values > initial_value * 1.10) * 100)
    # Audit-fix (MEDIUM): true running-max drawdown per path. The prior
    # `1 - path_min` formulation reports trough vs starting value, NOT
    # classical max-drawdown (trough vs running peak). Paths that go up
    # then down were under-reporting drawdown.
    #
    # For each path: at every step, drawdown_t = (running_max_t -
    # cumulative_t) / running_max_t. Max drawdown for the path is the
    # max of that array. Average across paths is the mean of those.
    running_max = np.maximum.accumulate(cumulative, axis=1)
    drawdowns = (running_max - cumulative) / running_max
    per_path_max_dd = np.max(drawdowns, axis=1)
    avg_drawdown = float(np.mean(per_path_max_dd) * 100)

    # Retain only the first `paths_retain` paths for UI — deterministic slice
    # (not a random choice) so the determinism-lock test is bit-exact.
    retain = min(paths_retain, n_simulations)
    sample_paths = (initial_value * cumulative[:retain]).tolist()

    hist_counts, hist_edges = np.histogram(final_values, bins=50)

    result = {
        "initial_value_usd":    initial_value,
        "horizon_days":         horizon_days,
        "n_simulations":        n_simulations,
        "paths_retained":       retain,
        "seed":                 seed,
        "percentile_5":         round(p5, 2),
        "percentile_25":        round(p25, 2),
        "percentile_50":        round(p50, 2),
        "percentile_75":        round(p75, 2),
        "percentile_95":        round(p95, 2),
        "mean_final_value":     round(mean, 2),
        "prob_loss_pct":        round(prob_loss, 2),
        "prob_10pct_gain_pct":  round(prob_10pct, 2),
        "avg_max_drawdown_pct": round(avg_drawdown, 2),
        "sample_paths":         sample_paths,
        "hist_counts":          hist_counts.tolist(),
        "hist_edges":           hist_edges.tolist(),
    }

    _mc_cache[cache_key] = {**result, "_ts": time.time()}
    return result
