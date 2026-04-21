"""
ETF Detail — per-ticker drilldown.

Signal badge (BUY/HOLD/SELL, shape + color), KPI tiles (expense ratio,
issuer tier, vol), composition breakdown, live historical returns +
Monte-Carlo single-ticker projection.

Data transparency via data_source_badge per Day-3 design directive.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import BRAND_NAME
from core.etf_universe import load_universe
from core.portfolio_engine import build_portfolio, run_monte_carlo
from core.signal_adapter import composite_signal
from integrations.data_feeds import get_etf_prices
from ui.components import (
    card,
    data_source_badge,
    disclosure,
    kpi_tile,
    section_header,
    signal_badge,
)
from ui.level_helpers import is_advanced, level_text
from ui.theme import apply_theme


st.set_page_config(page_title=f"ETF Detail — {BRAND_NAME}", layout="wide")
apply_theme()

section_header(
    "ETF Detail",
    level_text(
        beginner="Research a single fund. Signal, fees, composition, and recent performance.",
        intermediate="Per-ETF research: signal + KPIs + composition + historical.",
        advanced="Per-ETF research with Phase-1 composite signal (coin-level wiring Day 4+).",
    ),
)

universe = load_universe()
tickers = [u["ticker"] for u in universe]
chosen = st.selectbox("Ticker", options=tickers, index=0)
etf = next(u for u in universe if u["ticker"] == chosen)
sig = composite_signal(etf)


# Header row: ticker info + signal
col_id, col_sig = st.columns([3, 1])
with col_id:
    st.markdown(f"### {etf['ticker']} — {etf['name']}")
    st.caption(f"{etf['issuer']} · {etf['category']}")
with col_sig:
    signal_badge(sig["signal"])
    st.caption(level_text(
        beginner=sig["plain_english"],
        intermediate=f"{sig['signal']} · score {sig['score']}",
        advanced=f"{sig['signal']} · score {sig['score']} · Phase-1 rule-based",
    ))


# KPI tiles
k1, k2, k3, k4 = st.columns(4)
with k1:
    er_bps = etf.get("expense_ratio_bps")
    kpi_tile("Expense ratio", f"{er_bps} bps" if er_bps else "—")
with k2:
    kpi_tile("Volatility (ann.)", f"{etf['volatility']:.1f}%")
with k3:
    kpi_tile("Corr with BTC", f"{etf['correlation_with_btc']:.2f}")
with k4:
    from core.portfolio_engine import _issuer_tier_nudge
    nudge = _issuer_tier_nudge(etf)
    tier_label = "A (preferred)" if nudge > 0 else ("C (discouraged)" if nudge < 0 else "B (neutral)")
    kpi_tile("Issuer tier", tier_label)


# Historical returns
with card("Historical returns"):
    prices = get_etf_prices([etf["ticker"]], period="5y", interval="1d")
    data_source_badge("etf_price")
    rows = prices.get(etf["ticker"], {}).get("prices", [])
    if not rows:
        st.info(level_text(
            beginner="Historical prices aren't available right now — the market-data service is temporarily unreachable.",
            intermediate="No price data from any live source.",
            advanced="All live price sources (yfinance / Stooq / Alpha Vantage) returned empty for this ticker.",
        ))
    else:
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        fig = go.Figure(data=[go.Scatter(
            x=df["date"], y=df["close"],
            mode="lines", line=dict(color="#00d4aa", width=2),
            name="Close",
        )])
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=320,
            yaxis_title="Close (USD)",
        )
        st.plotly_chart(fig, use_container_width=True)

        def _ret(n_days: int) -> str:
            if len(df) <= n_days:
                return "—"
            start = df["close"].iloc[-n_days]
            end = df["close"].iloc[-1]
            if start <= 0:
                return "—"
            return f"{((end / start) - 1) * 100:.1f}%"

        c1, c2, c3, c4 = st.columns(4)
        with c1: kpi_tile("1Y", _ret(252))
        with c2: kpi_tile("3Y", _ret(252 * 3))
        with c3: kpi_tile("5Y", _ret(252 * 5))
        with c4: kpi_tile("Data points", f"{len(df):,}")


# Composition (Phase-1: category-level; Phase-2 ETFs will have real N-PORT holdings)
with card("Composition"):
    category = etf.get("category", "")
    if category == "btc_spot":
        st.write("100% Bitcoin (spot) — custodied by the issuer.")
    elif category == "eth_spot":
        st.write("100% Ethereum (spot) — custodied by the issuer.")
    elif category == "btc_futures":
        st.write("Bitcoin futures contracts (CME). No spot BTC holdings.")
    else:
        st.write("Thematic / multi-asset. Holdings available from the issuer's site.")
    st.caption(level_text(
        beginner="This shows what the fund holds under the hood.",
        intermediate="Holdings update as SEC EDGAR N-PORT filings arrive (quarterly).",
        advanced="Day-4 wires get_etf_reference() → EDGAR / issuer / ETF.com fallback chain for live holdings.",
    ))


# Single-ticker Monte Carlo projection
with card("Forward projection"):
    # Build a 1-ETF portfolio using the universe-derived analytics so the
    # MC path is consistent with Portfolio-page construction math.
    temp_universe = [etf]
    try:
        p = build_portfolio("Ultra Aggressive", temp_universe, portfolio_value_usd=100_000)
        mc = run_monte_carlo(p, horizon_days=252)
    except Exception:
        p = None
        mc = None

    if mc:
        paths = mc["sample_paths"]
        fig = go.Figure()
        for path in paths[: min(40, len(paths))]:
            fig.add_trace(go.Scatter(
                y=path, mode="lines",
                line=dict(width=0.6, color="rgba(0,212,170,0.15)"),
                showlegend=False, hoverinfo="skip",
            ))
        fig.add_hline(y=mc["initial_value_usd"], line_dash="dash", line_color="#9ca3af")
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=280,
            yaxis_title="Value per $100k",
            xaxis_title="Trading days",
        )
        st.plotly_chart(fig, use_container_width=True)
        if is_advanced():
            st.caption(
                f"Paths: {mc['n_simulations']:,} · retained: {mc['paths_retained']} · seed: {mc['seed']}"
            )

disclosure(
    "Hypothetical results. Past performance does not guarantee future "
    "results. Signal shown is a Phase-1 rule-based composite; full "
    "coin-level indicator wiring lands Day 4+."
)
