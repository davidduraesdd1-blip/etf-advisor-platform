"""
Portfolio View — construct + visualize a crypto-ETF basket for a selected
client, with live performance panel + Execute Basket confirmation modal.

Data transparency: every panel consuming live data renders data_source_badge()
per Day-3 design directive. No synthetic fallbacks anywhere.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import (
    BRAND_NAME,
    DEMO_MODE,
    PORTFOLIO_TIERS,
)
from core.demo_clients import DEMO_CLIENTS, get_client
from core.etf_universe import load_universe
from core.portfolio_engine import build_portfolio, run_monte_carlo
from integrations.broker_mock import submit_basket
from integrations.data_feeds import get_etf_prices
from ui.components import (
    card,
    data_source_badge,
    disclosure,
    kpi_tile,
    section_header,
    tier_pill_selector,
)
from ui.level_helpers import is_advanced, is_beginner, level_text
from ui.theme import apply_theme


st.set_page_config(page_title=f"Portfolio — {BRAND_NAME}", layout="wide")
apply_theme()


# ═══════════════════════════════════════════════════════════════════════════
# Client selection
# ═══════════════════════════════════════════════════════════════════════════

section_header(
    "Portfolio",
    level_text(
        beginner="Pick a client, pick a risk tier, and see the crypto ETF basket we recommend.",
        intermediate="5-tier risk-profiled basket construction with forward-looking risk metrics.",
        advanced="Phase-2 pairwise-correlation basket, issuer-tier adjusted, with forward MC projection.",
    ),
)

if not DEMO_MODE:
    st.warning(
        "Demo mode is OFF. This view requires either `DEMO_MODE=True` or a "
        "live client-data integration."
    )
    st.stop()

default_id = st.session_state.get("active_client_id", DEMO_CLIENTS[0]["id"])
options = {f"{c['name']} — {c['label']}": c["id"] for c in DEMO_CLIENTS}
# Map current id back to label for default selection
current_label = next(
    (label for label, cid in options.items() if cid == default_id),
    list(options.keys())[0],
)
chosen_label = st.selectbox(
    "Client",
    options=list(options.keys()),
    index=list(options.keys()).index(current_label),
)
client = get_client(options[chosen_label])
if client is None:
    st.error("Selected client not found.")
    st.stop()
st.session_state["active_client_id"] = client["id"]


# ═══════════════════════════════════════════════════════════════════════════
# Tier selector + build
# ═══════════════════════════════════════════════════════════════════════════

tier_names = list(PORTFOLIO_TIERS.keys())
default_tier_idx = tier_names.index(client["assigned_tier"])
tier_name = tier_pill_selector(tier_names, default_index=default_tier_idx, key="tier_pill_portfolio")

tier_meta = PORTFOLIO_TIERS[tier_name]
st.caption(level_text(
    beginner=(
        f"Tier {tier_meta['tier_number']} · {tier_meta['typical_client']}. "
        f"Rebalance every {tier_meta['rebalance']}."
    ),
    intermediate=(
        f"Tier {tier_meta['tier_number']} · ceiling {tier_meta['ceiling_pct']}% "
        f"of total portfolio · rebalance {tier_meta['rebalance']}."
    ),
    advanced=(
        f"Tier {tier_meta['tier_number']} · ceiling {tier_meta['ceiling_pct']}% · "
        f"max_drawdown_pct={tier_meta['max_drawdown_pct']} · rebalance {tier_meta['rebalance']}."
    ),
))


@st.cache_data(ttl=600)
def _build_cached(tier_name: str, portfolio_value: float) -> dict:
    universe = load_universe()
    return build_portfolio(tier_name, universe, portfolio_value_usd=portfolio_value)


crypto_sleeve_usd = client["total_portfolio_usd"] * client["crypto_allocation_pct"] / 100
portfolio = _build_cached(tier_name, crypto_sleeve_usd)
holdings = portfolio["holdings"]
metrics = portfolio["metrics"]


# ═══════════════════════════════════════════════════════════════════════════
# KPI row
# ═══════════════════════════════════════════════════════════════════════════

k1, k2, k3, k4 = st.columns(4)
with k1:
    kpi_tile("Crypto sleeve", f"${crypto_sleeve_usd:,.0f}")
with k2:
    kpi_tile("Expected return", f"{metrics['weighted_return_pct']:.1f}%")
with k3:
    kpi_tile("Portfolio vol", f"{metrics['portfolio_volatility_pct']:.1f}%")
with k4:
    kpi_tile("Sharpe", f"{metrics['sharpe_ratio']:.2f}")
data_source_badge("risk_free_rate")   # Sharpe consumed FRED → show state


# ═══════════════════════════════════════════════════════════════════════════
# Allocation chart (pie / bar toggle)
# ═══════════════════════════════════════════════════════════════════════════

with card("Allocation"):
    view = st.radio("View", options=["Pie", "Stacked bar"], horizontal=True, key="alloc_view")
    alloc_df = pd.DataFrame(holdings)
    if view == "Pie":
        fig = go.Figure(data=[go.Pie(
            labels=alloc_df["ticker"],
            values=alloc_df["weight_pct"],
            hole=0.45,
            hovertemplate="<b>%{label}</b><br>%{value:.2f}%<extra></extra>",
        )])
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=360,
        )
    else:
        grouped = alloc_df.groupby("category", as_index=False)["weight_pct"].sum()
        fig = go.Figure(data=[go.Bar(
            x=grouped["category"],
            y=grouped["weight_pct"],
            marker_color="#00d4aa",
            text=[f"{w:.1f}%" for w in grouped["weight_pct"]],
            textposition="outside",
        )])
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            yaxis_title="Weight %",
            height=360,
        )
    st.plotly_chart(fig, use_container_width=True)

    display_cols = ["ticker", "name", "issuer", "category", "weight_pct", "usd_value"]
    st.dataframe(
        alloc_df[display_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "weight_pct": st.column_config.NumberColumn("Weight %", format="%.2f"),
            "usd_value":  st.column_config.NumberColumn("USD", format="$%,.0f"),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# Performance panel — LIVE only; no synthetic history.
# ═══════════════════════════════════════════════════════════════════════════

with card("Performance"):
    st.caption(level_text(
        beginner="How this basket has performed historically and where it could land over the next year.",
        intermediate="Historical returns + forward Monte Carlo projection.",
        advanced="1Y/3Y/5Y historical from yfinance (live fallback chain) + 10k-path forward projection.",
    ))

    tabs = st.tabs(["Historical", "Forward projection (Monte Carlo)"])

    with tabs[0]:
        tickers = [h["ticker"] for h in holdings]
        price_data = get_etf_prices(tickers, period="5y", interval="1d")
        data_source_badge("etf_price")

        rows = []
        for h in holdings:
            p = price_data.get(h["ticker"], {}).get("prices", [])
            if not p:
                rows.append({
                    "ticker":      h["ticker"],
                    "source":      price_data.get(h["ticker"], {}).get("source", "unavailable"),
                    "1Y return %": None,
                    "3Y return %": None,
                    "5Y return %": None,
                })
                continue
            df = pd.DataFrame(p)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            close = df["close"].astype(float)

            def _ret(n_days: int) -> float | None:
                if len(close) <= n_days:
                    return None
                start = close.iloc[-n_days]
                end = close.iloc[-1]
                if start <= 0:
                    return None
                return round(((end / start) - 1) * 100, 2)

            rows.append({
                "ticker":      h["ticker"],
                "source":      price_data.get(h["ticker"], {}).get("source", "n/a"),
                "1Y return %": _ret(252),
                "3Y return %": _ret(252 * 3),
                "5Y return %": _ret(252 * 5),
            })

        hist_df = pd.DataFrame(rows)
        any_data = hist_df[["1Y return %", "3Y return %", "5Y return %"]].notna().any().any()
        if any_data:
            st.dataframe(hist_df, use_container_width=True, hide_index=True)
        else:
            st.info(
                level_text(
                    beginner=(
                        "Historical returns aren't available right now — the market-data "
                        "service is temporarily unreachable. The forward-projection tab still works."
                    ),
                    intermediate="No historical data from any fallback source. Try the Retry button on the banner, or switch to forward projection.",
                    advanced="All live price sources (yfinance / Stooq / Alpha Vantage) returned empty. Check circuit breaker state in Settings.",
                )
            )

    with tabs[1]:
        mc = run_monte_carlo(portfolio, horizon_days=252)
        if mc:
            paths = mc["sample_paths"]
            fig = go.Figure()
            for path in paths[: min(50, len(paths))]:
                fig.add_trace(go.Scatter(
                    y=path,
                    mode="lines",
                    line=dict(width=0.6, color="rgba(0,212,170,0.12)"),
                    showlegend=False,
                    hoverinfo="skip",
                ))
            fig.add_hline(
                y=mc["initial_value_usd"],
                line_dash="dash",
                line_color="#9ca3af",
                annotation_text="Initial value",
                annotation_position="top left",
            )
            fig.update_layout(
                margin=dict(l=0, r=0, t=10, b=0),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                height=320,
                yaxis_title="Portfolio value (USD)",
                xaxis_title="Trading days",
            )
            st.plotly_chart(fig, use_container_width=True)

            kk1, kk2, kk3, kk4 = st.columns(4)
            with kk1: kpi_tile("Median 1Y",   f"${mc['percentile_50']:,.0f}")
            with kk2: kpi_tile("5th pctile",  f"${mc['percentile_5']:,.0f}")
            with kk3: kpi_tile("95th pctile", f"${mc['percentile_95']:,.0f}")
            with kk4: kpi_tile("P(loss)",     f"{mc['prob_loss_pct']:.1f}%")

            if is_advanced():
                st.caption(
                    f"Paths computed: {mc['n_simulations']:,} · "
                    f"retained for render: {mc['paths_retained']:,} · "
                    f"seed: {mc['seed']}"
                )
        else:
            st.info("Monte Carlo projection unavailable (no holdings).")

disclosure(
    "Hypothetical results. Past performance does not guarantee future "
    "results. Forward projections are model-based estimates, not forecasts. "
    "See methodology for assumptions and limitations."
)


# ═══════════════════════════════════════════════════════════════════════════
# Execute Basket CTA + confirmation
# ═══════════════════════════════════════════════════════════════════════════

def _open_confirm() -> None:
    st.session_state["confirm_execute"] = True


def _render_confirm_modal() -> None:
    """
    Uses st.dialog when available (Streamlit >= 1.30). Falls back to a
    session-state gated conditional render for older versions.
    """
    if hasattr(st, "dialog"):
        @st.dialog("Confirm basket execution")
        def _dlg():
            _confirm_body()
        _dlg()
    else:
        with card("Confirm basket execution"):
            _confirm_body()


def _confirm_body() -> None:
    st.caption(level_text(
        beginner=(
            f"You are about to submit {len(holdings)} orders totalling "
            f"${crypto_sleeve_usd:,.0f} to the demo broker. No real money will move."
        ),
        intermediate=(
            f"{len(holdings)} orders · total notional ${crypto_sleeve_usd:,.0f} · "
            "demo broker (mock fills)."
        ),
        advanced=(
            f"{len(holdings)} orders · ${crypto_sleeve_usd:,.0f} gross · "
            "broker=mock · est. slippage 12.5 bps · tif=day."
        ),
    ))
    preview = pd.DataFrame([
        {
            "Ticker":     h["ticker"],
            "Side":       "BUY",
            "Shares":     round(h["usd_value"] / 100, 3),
            "Est. px":    "$100.00",   # placeholder; Day-3+ wire to last yfinance close
            "Notional":   h["usd_value"],
        }
        for h in holdings
    ])
    st.dataframe(preview, use_container_width=True, hide_index=True)
    st.caption("Estimated slippage: ~12.5 bps (mid of 5-20 bps range).")

    col_exec, col_cancel = st.columns(2)
    with col_exec:
        if st.button("Confirm and execute", use_container_width=True, type="primary"):
            orders = [
                {
                    "ticker":    h["ticker"],
                    "quantity":  round(h["usd_value"] / 100, 3),
                    "side":      "BUY",
                    "mid_price": 100.0,   # placeholder
                    "tif":       "day",
                }
                for h in holdings
            ]
            result = submit_basket(orders, client_id=client["id"], dry_run=False)
            st.session_state["last_execution"] = result
            st.session_state["confirm_execute"] = False
            st.toast(f"Basket submitted — {result['summary']['n_orders']} orders filled (mock).")
    with col_cancel:
        if st.button("Cancel", use_container_width=True):
            st.session_state["confirm_execute"] = False


col_cta, col_info = st.columns([1, 2])
with col_cta:
    st.button(
        "Execute basket →",
        on_click=_open_confirm,
        type="primary",
        use_container_width=True,
    )
with col_info:
    st.caption(level_text(
        beginner="Demo mode — no real orders will be placed.",
        intermediate="Broker = mock. Post-demo flips to Alpaca paper, then Alpaca live.",
        advanced="BROKER_PROVIDER='mock' per config.py. Day-4+ routes to alpaca_paper.",
    ))

if st.session_state.get("confirm_execute"):
    _render_confirm_modal()

# Last execution receipt
if "last_execution" in st.session_state:
    with card("Last execution receipt"):
        result = st.session_state["last_execution"]
        st.code(
            f"Basket {result['basket_id']} · {result['summary']['n_orders']} orders · "
            f"${result['summary']['gross_usd']:,.2f} gross · "
            f"avg slip {result['summary']['avg_slippage_bps']} bps",
            language=None,
        )
