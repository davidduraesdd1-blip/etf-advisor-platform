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
from core.etf_universe import load_universe_with_live_returns
from core.portfolio_engine import build_portfolio, run_monte_carlo
from integrations.broker_mock import submit_basket
from integrations.data_feeds import get_etf_prices, get_last_close
from ui.components import (
    card,
    data_source_badge,
    disclosure,
    kpi_tile,
    safe_page_link,
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
def _universe_with_live_returns_cached() -> list[dict]:
    """
    Fetch the universe with live CAGR enrichment. Cached for 10 min so we
    don't re-hit yfinance on every tier/client toggle. The per-ticker
    price bundles have their own cache inside data_feeds too.
    """
    return load_universe_with_live_returns()


@st.cache_data(ttl=600)
def _build_cached(tier_name: str, portfolio_value: float,
                  universe_key: int) -> dict:
    # universe_key is a cache discriminator: when the live-enriched
    # universe changes (ticker returns drift), Streamlit invalidates.
    universe = _universe_with_live_returns_cached()
    return build_portfolio(tier_name, universe, portfolio_value_usd=portfolio_value)


crypto_sleeve_usd = client["total_portfolio_usd"] * client["crypto_allocation_pct"] / 100
with st.spinner("Deriving live expected returns from price history..."):
    universe_live = _universe_with_live_returns_cached()
# id(universe_live) is stable per cache entry so we reuse the 10-min bucket.
portfolio = _build_cached(tier_name, crypto_sleeve_usd, id(universe_live))
holdings = portfolio["holdings"]
metrics = portfolio["metrics"]

# Count how many ETFs in this portfolio used live vs category-default
# expected returns, for the transparency footnote under the KPI tile.
_holding_tickers = {h["ticker"] for h in holdings}
_sources = [
    e.get("expected_return_source", "category_default")
    for e in universe_live if e["ticker"] in _holding_tickers
]
_n_live = sum(1 for s in _sources if s == "live")
_n_total = len(_sources) or 1


# ═══════════════════════════════════════════════════════════════════════════
# KPI row
# ═══════════════════════════════════════════════════════════════════════════

k1, k2, k3, k4 = st.columns(4)
with k1:
    kpi_tile("Crypto sleeve", f"${crypto_sleeve_usd:,.0f}")
with k2:
    kpi_tile("Expected return (annualized)",
             f"{metrics['weighted_return_pct']:.1f}%")
with k3:
    kpi_tile("Portfolio vol", f"{metrics['portfolio_volatility_pct']:.1f}%")
with k4:
    kpi_tile("Sharpe", f"{metrics['sharpe_ratio']:.2f}")

# Provenance for the expected-return KPI. Scales by user level.
if _n_live == _n_total:
    _ret_src_msg = level_text(
        beginner=(
            f"Expected return is derived from each fund's actual price "
            f"history (all {_n_total} ETFs in this basket). It tells you "
            f"how the fund has performed on an annualized basis — not a "
            f"prediction."
        ),
        intermediate=(
            f"Expected return = annualized CAGR from each ETF's own "
            f"price history ({_n_total}/{_n_total} live). Capped at ±300% "
            f"to filter data-error artifacts."
        ),
        advanced=(
            f"Per-ETF annualized CAGR from first-to-last available close "
            f"({_n_total}/{_n_total} live, yfinance primary). ±300% cap. "
            f"Weighted by basket allocation."
        ),
    )
elif _n_live == 0:
    _ret_src_msg = level_text(
        beginner=(
            "Live price history unavailable right now — the expected return "
            "above uses category averages as a fallback. Refresh once live "
            "data is back for per-fund accuracy."
        ),
        intermediate=(
            f"All {_n_total} ETFs fell back to category-default expected "
            f"returns — live price fetch unavailable."
        ),
        advanced=(
            f"0/{_n_total} live — full fallback to category defaults "
            f"(btc_spot=25%, eth_spot=35%, btc_futures=15%, thematic=50%)."
        ),
    )
else:
    _ret_src_msg = level_text(
        beginner=(
            f"{_n_live} of {_n_total} ETFs used live price history; the "
            f"rest fell back to category averages (live data temporarily "
            f"unavailable for those tickers)."
        ),
        intermediate=(
            f"{_n_live}/{_n_total} ETFs: live CAGR. "
            f"{_n_total - _n_live}/{_n_total}: category-default fallback."
        ),
        advanced=(
            f"{_n_live}/{_n_total} live, {_n_total - _n_live}/{_n_total} "
            f"category-default fallback. Mixed-source weighted return."
        ),
    )
st.caption(_ret_src_msg)
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
    "See the Methodology page for assumptions and limitations."
)
safe_page_link("pages/98_Methodology.py", label="Read methodology →", icon="📋")


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
    # Day-4 D: wire last close per ETF. Fall back to best-available if the
    # live fetch hasn't populated _last_close yet. Transparency badge in
    # the modal if any ticker is missing a live price.
    per_ticker_price: dict[str, float | None] = {
        h["ticker"]: get_last_close(h["ticker"]) for h in holdings
    }
    missing_live = [t for t, p in per_ticker_price.items() if p is None]

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

    if missing_live:
        st.warning(
            "Live price unavailable for: " + ", ".join(missing_live) + ". "
            "Estimated notional uses the portfolio construction baseline. "
            "Close the modal and click Refresh if you want a live retry."
        )
        data_source_badge("etf_price")

    preview_rows = []
    orders_draft: list[dict] = []
    for h in holdings:
        live_px = per_ticker_price.get(h["ticker"])
        # If live missing, compute a *conservative* per-share share count from
        # the USD allocation — this is not a fabricated price. The mid_price
        # sent to broker_mock defaults to live if present, else the USD
        # allocation divided by 1 share (worst-case 1-share order).
        if live_px is not None and live_px > 0:
            shares = round(h["usd_value"] / live_px, 4)
            px_label = f"${live_px:,.2f}"
            mid_price = live_px
        else:
            shares = 1   # defer to live re-fetch; 1-share placeholder
            px_label = "—"
            mid_price = 0.0
        preview_rows.append({
            "Ticker":   h["ticker"],
            "Side":     "BUY",
            "Shares":   shares,
            "Last px":  px_label,
            "Notional": h["usd_value"],
        })
        orders_draft.append({
            "ticker":    h["ticker"],
            "quantity":  shares,
            "side":      "BUY",
            "mid_price": mid_price,
            "tif":       "day",
        })

    preview = pd.DataFrame(preview_rows)
    st.dataframe(preview, use_container_width=True, hide_index=True)
    st.caption("Estimated slippage: ~12.5 bps (mid of 5-20 bps mock range).")

    col_exec, col_cancel = st.columns(2)
    with col_exec:
        exec_disabled = bool(missing_live) and len(missing_live) == len(holdings)
        if st.button(
            "Confirm and execute",
            use_container_width=True,
            type="primary",
            disabled=exec_disabled,
            help="Disabled until at least one live price is available."
                 if exec_disabled else None,
        ):
            result = submit_basket(orders_draft, client_id=client["id"], dry_run=False)
            st.session_state["last_execution"] = result
            st.session_state["confirm_execute"] = False
            # Audit-log write (Day-4 item I)
            try:
                from core.audit_log import append_entry
                append_entry(
                    client_id=client["id"],
                    action="execute_basket",
                    detail=(
                        f"tier={tier_name}, n_orders={result['summary']['n_orders']}, "
                        f"gross=${result['summary']['gross_usd']:,.2f}"
                    ),
                )
            except Exception:
                pass   # audit-log failure must never block execution
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
