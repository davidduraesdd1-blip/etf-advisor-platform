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
    if _rfr_cache["rate"] is not None and now - _rfr_cache["ts"] < _RFR_CACHE_TTL:
        age_seconds = int(now - _rfr_cache["ts"])
        # Still fresh — classify as LIVE if last successful fetch was from FRED
        # primary; CACHED if cache is older than 15 min so UI can show the age.
        if age_seconds < 900:
            return float(_rfr_cache["rate"])
        mark_cache_hit("risk_free_rate", age_seconds=age_seconds,
                       note="FRED cache still valid; next refresh on TTL expiry.")
        return float(_rfr_cache["rate"])

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
                _rfr_cache["rate"] = val
                _rfr_cache["ts"] = now
                register_fetch_attempt("risk_free_rate", "fred", success=True)
                return val
    except Exception as exc:
        logger.debug("FRED risk-free rate fetch failed: %s", exc)
        register_fetch_attempt("risk_free_rate", "fred", success=False,
                               note=f"FRED fetch error: {type(exc).__name__}")

    _rfr_cache["rate"] = RISK_FREE_RATE_FALLBACK
    _rfr_cache["ts"] = now
    mark_static_fallback("risk_free_rate",
                         note=f"FRED unavailable — using static {RISK_FREE_RATE_FALLBACK}% fallback.")
    return RISK_FREE_RATE_FALLBACK


# ═══════════════════════════════════════════════════════════════════════════
# Cornish-Fisher modified VaR (verbatim port — do not retune in Phase 1)
# ═══════════════════════════════════════════════════════════════════════════

def cornish_fisher_var(
    mean_return: float,
    vol: float,
    confidence: float = 0.95,
) -> float:
    """
    Cornish-Fisher modified parametric VaR (Favre & Galeano 2002).
    Returns a positive number representing potential loss %.

    Parameters calibrated for illiquid RWA distributions — retained as-is
    per planning-side Risk 1 directive. Day-3+ retune target:
        S = -0.25  (crypto ETFs less skewed than RWA)
        K = 2.5    (heavier-tailed than equity, lighter than RWA)
    """
    S = -0.4   # left skew (illiquid RWA calibration — flagged for Day-3 retune)
    K = 1.0    # excess kurtosis (fat tails)

    z_g = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326}.get(confidence, 1.645)
    z_cf = (
        z_g
        + (z_g**2 - 1) / 6 * S
        + (z_g**3 - 3 * z_g) / 24 * K
        - (2 * z_g**3 - 5 * z_g) / 36 * S**2
    )
    return max(-(mean_return - z_cf * vol), 0)


# ═══════════════════════════════════════════════════════════════════════════
# Portfolio construction
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# Phase 2: pairwise correlation matrix + issuer-tier preference weighting
# ═══════════════════════════════════════════════════════════════════════════

# Issuer tiers (Day-3 Phase-2 directive).
# Tier A +2% allocation nudge, Tier C -2% nudge, Tier B neutral.
_ISSUER_TIER_A: frozenset[str] = frozenset({"BlackRock", "Fidelity"})
_ISSUER_TIER_C_TICKERS: frozenset[str] = frozenset({
    "GBTC",   # Grayscale legacy high-fee BTC (150 bps)
    "ETHE",   # Grayscale legacy high-fee ETH (250 bps)
    "DEFI",   # Hashdex futures-based, not spot — structural Tier C
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
    ("btc_spot",    "btc_spot"):    0.97,    # same-underlying spot, different issuers
    ("eth_spot",    "eth_spot"):    0.95,
    ("btc_futures", "btc_futures"): 0.95,
    ("thematic",    "thematic"):    0.85,

    ("btc_spot",    "eth_spot"):    0.75,    # BTC↔ETH cluster crossover
    ("btc_spot",    "btc_futures"): 0.92,    # same-underlying different structure
    ("btc_spot",    "thematic"):    0.72,
    ("eth_spot",    "btc_futures"): 0.72,
    ("eth_spot",    "thematic"):    0.74,
    ("btc_futures", "thematic"):    0.70,
}


def _pair_corr(cat_i: str, cat_j: str) -> float:
    """Lookup pairwise correlation between two categories (symmetric)."""
    if cat_i == cat_j:
        return _CATEGORY_PAIR_CORR.get((cat_i, cat_j), 0.90)
    key = (cat_i, cat_j) if (cat_i, cat_j) in _CATEGORY_PAIR_CORR else (cat_j, cat_i)
    return _CATEGORY_PAIR_CORR.get(key, 0.70)


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


def _select_etfs_for_category(
    category: str,
    universe: list[dict],
) -> list[dict]:
    """
    Select up to MAX_ETFS_PER_CATEGORY ETFs from a category.
    Prefers lowest expense ratio; breaks ties by issuer diversity.
    Phase 1 has no expense_ratio data yet, so selection is deterministic
    by seed order within the category.
    """
    in_cat = [u for u in universe if u.get("category") == category]
    if not in_cat:
        return []

    # Deterministic sort: by expense_ratio asc (missing → large), then ticker
    def _key(u: dict) -> tuple[float, str]:
        er = u.get("expense_ratio_bps")
        return (float(er) if er is not None else 9999.0, str(u.get("ticker", "")))

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

    for category, cat_weight in category_allocs.items():
        if cat_weight <= 0:
            continue
        chosen = _select_etfs_for_category(category, universe)
        if not chosen:
            continue

        per_asset_weight = cat_weight / len(chosen)

        # Phase-2 issuer-tier preference nudge: apply +/- within the
        # category, then renormalize so the category total still equals cat_weight.
        raw_weights: list[float] = []
        for etf in chosen:
            nudge = _issuer_tier_nudge(etf)
            raw = max(0.5, per_asset_weight + nudge)   # floor to keep it positive
            raw_weights.append(raw)
        scale = cat_weight / sum(raw_weights) if sum(raw_weights) > 0 else 0
        adjusted = [min(w * scale, MAX_SINGLE_POSITION_PCT) for w in raw_weights]

        for etf, weight in zip(chosen, adjusted):
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
                "issuer_tier_nudge":    _issuer_tier_nudge(etf),
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

    # Magdon-Ismail max-drawdown approximation
    # E[MDD] ≈ σ × sqrt(T) × f, with f=3.0 for illiquid assets (verbatim).
    # Will retune to f=2.5 in Phase 2 — ETFs have less drag than RWA.
    tier_meta = PORTFOLIO_TIERS.get(tier_name, {})
    max_drawdown_ceiling = tier_meta.get("max_drawdown_pct", 60)
    max_drawdown = min(portfolio_vol * 3.0, max_drawdown_ceiling)

    calmar = weighted_return / max(max_drawdown, 0.01)

    # VaR (Cornish-Fisher)
    var_95 = cornish_fisher_var(weighted_return, portfolio_vol, 0.95)
    var_99 = cornish_fisher_var(weighted_return, portfolio_vol, 0.99)

    # CVaR — Student-t(5) multipliers (verbatim; flagged for retune)
    cvar_95 = var_95 * 1.40
    cvar_99 = var_99 * 1.48

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
        "var_95_pct":              _sr(var_95, 3),
        "var_99_pct":              _sr(var_99, 3),
        "cvar_95_pct":             _sr(cvar_95, 3),
        "cvar_99_pct":             _sr(cvar_99, 3),
        "diversification_ratio":   _sr(diversification_r, 3),
        "excess_return_pct":       _sr(excess_return, 3),
        "n_holdings":              n,
    }


def _empty_metrics() -> dict:
    return {
        "weighted_return_pct": 0, "annual_return_usd": 0, "monthly_income_usd": 0,
        "portfolio_volatility_pct": 0, "sharpe_ratio": 0, "sortino_ratio": 0,
        "calmar_ratio": 0, "max_drawdown_pct": 0, "var_95_pct": 0,
        "var_99_pct": 0, "cvar_95_pct": 0, "cvar_99_pct": 0,
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
    path_min = np.min(cumulative, axis=1)
    avg_drawdown = float(np.mean((1 - path_min) * 100))

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
