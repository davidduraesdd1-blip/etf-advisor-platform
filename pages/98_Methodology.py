"""
Methodology — the math, data, and compliance story behind the platform.

Linked from performance disclosures on the Portfolio and ETF Detail pages.
Written at a Beginner-level reading tone with an "Advanced" expander on
each section for the FA who wants the full story.
"""
from __future__ import annotations

import streamlit as st

from config import BRAND_NAME
from ui.components import card, disclosure, section_header
from ui.level_helpers import level_text
from ui.sidebar import render_sidebar
from ui.theme import apply_theme


st.set_page_config(page_title=f"Methodology — {BRAND_NAME}", layout="wide")
apply_theme()
render_sidebar()

try:
    from ui import render_top_bar as _ds_top_bar, page_header as _ds_page_header
    _ds_top_bar(breadcrumb=("Research", "Methodology"),
                user_level=st.session_state.get("user_level", "beginner"))
    _ds_page_header(
        title="Methodology",
        subtitle=level_text(
            beginner="How the platform constructs portfolios, measures risk, and sources data.",
            intermediate="Construction, risk metrics, signal math, and data sources.",
            advanced="Full methodology reference — linked from performance disclosures.",
        ),
    )
except Exception:
    section_header(
        "Methodology",
        level_text(
            beginner="How the platform constructs portfolios, measures risk, and sources data.",
            intermediate="Construction, risk metrics, signal math, and data sources.",
            advanced="Full methodology reference — linked from performance disclosures.",
        ),
    )


# ─── 1. Portfolio construction ───────────────────────────────────────────────

with card("How portfolios are constructed"):
    st.markdown(level_text(
        beginner=(
            "Every client is assigned one of five risk tiers, from **Ultra "
            "Conservative** (appropriate for retirees) to **Ultra Aggressive** "
            "(appropriate for younger clients with long horizons and high "
            "conviction about crypto as an asset class).\n\n"
            "For each tier, the platform picks a mix of ETFs inside the crypto "
            "sleeve. Lower tiers stick to a small number of large, low-fee "
            "Bitcoin spot ETFs. Higher tiers add Ethereum and, eventually, "
            "more diversified baskets as those ETFs come to market."
        ),
        intermediate=(
            "Five tiers (Ultra Conservative → Ultra Aggressive) with "
            "category ceilings on the crypto sleeve: **5% / 10% / 20% / "
            "35% / 50%+** of total portfolio. Inside each tier, the allocation "
            "matrix (`core/risk_tiers.py`) distributes the sleeve across "
            "9 category buckets: btc_spot / eth_spot / altcoin_spot / "
            "income_covered_call / thematic_equity / leveraged / multi_asset. "
            "btc_futures and eth_futures are excluded by design (spot strictly "
            "dominates post-approval)."
        ),
        advanced=(
            "`core/portfolio_engine.py::build_portfolio` implements: \n\n"
            "1. Look up tier allocation matrix from `TIER_CATEGORY_ALLOCATIONS`.\n"
            "2. For each category with nonzero weight, select up to "
            "`MAX_ETFS_PER_CATEGORY=3` ETFs sorted by expense ratio (ties "
            "broken on issuer diversity).\n"
            "3. Apply issuer-tier nudge: Tier A (BlackRock, Fidelity) +2pp, "
            "Tier C (GBTC / ETHE / DEFI / XRPR / BITW) −2pp, neutral otherwise. "
            "Post-nudge renormalize so each category still sums to its target.\n"
            "4. Enforce `MAX_SINGLE_POSITION_PCT=30%` diversification cap.\n"
            "5. Normalize final weights to 100%; last holding absorbs "
            "rounding remainder.\n"
            "6. Compute full metric suite via `compute_portfolio_metrics`."
        ),
    ))


# ─── 2. Backtest methodology ─────────────────────────────────────────────────

with card("Backtest methodology"):
    st.markdown(level_text(
        beginner=(
            "Historical returns are pulled directly from public market data "
            "(Yahoo Finance, with Stooq as a backup). Performance shown on "
            "Portfolio and ETF Detail pages is always labeled **Hypothetical "
            "results** per SEC Marketing Rule guidance.\n\n"
            "The benchmark is a blended **60% equity / 40% bond** mix with a "
            "20% Bitcoin spot sleeve, rebalanced quarterly."
        ),
        intermediate=(
            "Historical OHLCV from yfinance → Stooq fallback chain (cached "
            "5min during market hours, 60min off-hours). 1Y / 3Y / 5Y / "
            "since-inception tabs. Benchmark = `BENCHMARK_DEFAULT` in "
            "`config.py`. Max drawdown computed on daily close series."
        ),
        advanced=(
            "Price chain: yfinance primary → Stooq (~15-min delayed). "
            "Circuit breaker trips yfinance to Stooq on 3 failures in a "
            "60-second rolling window; new-ETF empty results do not count "
            "toward the breaker. All fallback states surface via "
            "`data_source_badge` on the affected panel. Alpha Vantage was "
            "removed from the active chain (25 req/day free tier was a "
            "false fallback); scaffold retained in integrations/data_feeds "
            "for paid-tier reactivation."
        ),
    ))


# ─── 3. Signal derivation ────────────────────────────────────────────────────

with card("Signal derivation"):
    st.markdown(level_text(
        beginner=(
            "Each ETF gets a simple **BUY / HOLD / SELL** signal based on three "
            "checks on its recent price behavior:\n\n"
            "- **Is it cheap or expensive?** (RSI — Relative Strength Index)\n"
            "- **Is the trend turning up or down?** (MACD)\n"
            "- **How much has it moved recently?** (Momentum)\n\n"
            "When price history isn't available for a brand-new ETF, the "
            "signal falls back to the ETF's expected return vs volatility — "
            "and that fallback is clearly labeled in the signal display."
        ),
        intermediate=(
            "Composite = 0.45·RSI_score + 0.35·MACD_score + 0.20·Momentum_score, "
            "each component scaled to [−1, +1]. Thresholds: BUY ≥ +0.30, "
            "SELL ≤ −0.30. Fallback when OHLCV < 35 bars: Phase-1 "
            "return-to-volatility heuristic, labeled `phase1_fallback`."
        ),
        advanced=(
            "`core/signal_adapter.py`: Wilder's RSI(14), MACD(12,26,9) with "
            "standard EMA seeding, 20-period simple momentum. RSI-30/70 → "
            "linear [+1,−1]; MACD histogram clamped at ±2.0 → [−1,+1]; "
            "momentum ±10% → [−1,+1]. Future Layer-2/3/4 additions (macro, "
            "sentiment, on-chain) compose on top of this core."
        ),
    ))


# ─── 4. Risk metrics ─────────────────────────────────────────────────────────

with card("Risk metrics"):
    st.markdown(level_text(
        beginner=(
            "Every portfolio shows four standard risk numbers:\n\n"
            "- **Sharpe ratio** — return per unit of risk (higher is better).\n"
            "- **Sortino ratio** — same idea but only penalizes *downside* "
            "moves (more forgiving of upside volatility).\n"
            "- **Value at Risk (VaR)** — how much you could lose on a bad day "
            "(we show the 95th percentile).\n"
            "- **Maximum drawdown** — how far the portfolio might fall from "
            "a peak in a bad stretch.\n\n"
            "All four are **model-based estimates** — real outcomes will differ."
        ),
        intermediate=(
            "Sharpe + Sortino use a live FRED 3-month T-bill risk-free rate "
            "(4.25% static fallback when FRED is unreachable — shown as a "
            "footnote). Sortino uses the canonical Sortino & van der Meer "
            "(1991) formulation with MAR=live_rf on both sides. VaR is "
            "Cornish-Fisher parametric. Max drawdown via Magdon-Ismail-Atiya "
            "approximation. CVaR multiplier for 95/99% = 1.35 / 1.42 "
            "(crypto-ETF retuned 2026-04)."
        ),
        advanced=(
            "Cornish-Fisher VaR (Favre & Galeano 2002): S=−0.25, K=2.5 — "
            "retuned for crypto ETFs from the Phase-2 RWA calibration. "
            "MDD factor 2.7 (crypto-ETF) vs RWA 3.0 vs equity 2.3-2.5. "
            "Post-demo: proper 3-year calibration fit using BTC spot history "
            "+ ETF tracking-error extrapolation. See `docs/port_log.md` for "
            "the Phase 3 tuning entry."
        ),
    ))


# ─── 5. Data sources + transparency ──────────────────────────────────────────

with card("Data sources and transparency"):
    st.markdown(level_text(
        beginner=(
            "The platform is **live-first**. When you see a number on the "
            "screen, it came directly from a public data source — not a "
            "hardcoded value. When a primary source is temporarily "
            "unavailable, the app **tells you**:\n\n"
            "- A small amber badge means you're seeing data from a backup source.\n"
            "- An amber banner means you're seeing cached data with a timestamp.\n"
            "- A footnote means one number (like the risk-free rate) fell "
            "back to a static estimate.\n\n"
            "We never silently serve stale or fabricated data."
        ),
        intermediate=(
            "Primary sources: **yfinance** (ETF prices), **SEC EDGAR** (holdings, "
            "reference data, new-fund filings), **FRED** (risk-free rate via "
            "3-month T-bill CSV endpoint). Fallback chains per CLAUDE.md §10. "
            "Fallback state surfaced via the `data_source_badge` primitive on "
            "every data-consuming panel."
        ),
        advanced=(
            "`core/data_source_state.py` tracks four states per category: "
            "**LIVE**, **FALLBACK_LIVE**, **CACHED**, **STATIC**. Every fetch "
            "calls `register_fetch_attempt`; the UI reads the current state "
            "and renders accordingly. EDGAR calls go through a shared "
            "10-req/sec token bucket in `integrations/edgar.py`. yfinance has "
            "a module-level memo + `@st.cache_data` 24hr TTL; circuit breaker "
            "trips to Stooq after 3 failures/60s."
        ),
    ))


disclosure(
    "Hypothetical results. Past performance does not guarantee future "
    "results. All client profiles shown in demo mode are fictional. "
    "Methodology parameters are current as of the build date; retuning "
    "is documented in docs/port_log.md in the repository."
)
