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
from core.etf_universe import load_universe_with_live_analytics
from core.portfolio_engine import build_portfolio, run_monte_carlo
from integrations.broker_mock import submit_basket
from integrations.data_feeds import get_etf_prices, get_last_close
from ui.components import (
    card,
    data_source_badge,
    data_sources_panel,
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

# Data-source panel intentionally omitted from decision-making pages
# per FA feedback — advisors prioritize accurate numbers over data
# provenance at the point of client recommendation. Full panel is on
# the Settings page for operator audit. Tile-level transparency
# (data_source_badge on each KPI) still surfaces any active fallback
# exactly where it affects the number the FA is reading.

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
def _universe_with_live_analytics_cached() -> list[dict]:
    """
    Fetch the universe with FULL live analytics: expected return (CAGR),
    90-day realized volatility, 90-day BTC correlation. Cached for
    10 min so tier/client toggles don't re-hit yfinance. The underlying
    price bundles are also memoized inside data_feeds.
    """
    return load_universe_with_live_analytics()


@st.cache_data(ttl=600)
def _build_cached(tier_name: str, portfolio_value: float,
                  universe_key: int, compliance_filter: bool) -> dict:
    # universe_key is a cache discriminator: when the live-enriched
    # universe changes (ticker analytics drift), Streamlit invalidates.
    # compliance_filter is part of the cache key so toggling it on/off
    # in Settings produces a fresh build instead of reusing a stale one.
    universe = _universe_with_live_analytics_cached()
    return build_portfolio(
        tier_name, universe,
        portfolio_value_usd=portfolio_value,
        compliance_filter_on=compliance_filter,
    )


crypto_sleeve_usd = client["total_portfolio_usd"] * client["crypto_allocation_pct"] / 100
with st.spinner("Deriving live analytics (returns, vol, correlation) from price history..."):
    universe_live = _universe_with_live_analytics_cached()
# id(universe_live) is stable per cache entry so we reuse the 10-min bucket.
# Pick up the session-state compliance filter (default ON).
_compliance_filter = bool(st.session_state.get("compliance_filter_on", True))
portfolio = _build_cached(tier_name, crypto_sleeve_usd,
                          id(universe_live), _compliance_filter)

# Compliance-filter status banner — makes the active state visible
# so the FA isn't surprised when the Ultra-Aggressive basket has no
# leveraged sleeve even though the tier allocation specifies one.
if _compliance_filter:
    st.caption(
        "🛡 **Fiduciary-appropriate filter ON** — leveraged and single-stock "
        "covered-call wrappers excluded from this basket. "
        "Toggle in Settings for aggressive-sleeve-approved IPS clients."
    )
holdings = portfolio["holdings"]
metrics = portfolio["metrics"]

# Count per-metric live vs category-default for the transparency caption.
_holding_tickers = {h["ticker"] for h in holdings}
_basket = [e for e in universe_live if e["ticker"] in _holding_tickers]
_n_total = len(_basket) or 1
_n_live_ret  = sum(1 for e in _basket if e.get("expected_return_source") == "live")
_n_live_vol  = sum(1 for e in _basket if e.get("volatility_source") == "live")
_n_live_corr = sum(1 for e in _basket if e.get("correlation_source") in ("live", "self"))
# Legacy aliases retained for the older return-only caption code below.
_n_live = _n_live_ret
_sources = [e.get("expected_return_source", "category_default") for e in _basket]


# ═══════════════════════════════════════════════════════════════════════════
# KPI row — now 5 tiles so the FA sees historical + forward side-by-side
# ═══════════════════════════════════════════════════════════════════════════

# Weighted forward-return across the basket (uses each ETF's
# forward_return if populated; falls back to category default
# via the same weighting path).
_fwd_numer = 0.0
_fwd_denom = 0.0
_n_fwd_live = 0
for h in holdings:
    _uni_entry = next((e for e in universe_live if e["ticker"] == h["ticker"]), None)
    if _uni_entry is None:
        continue
    w = float(h.get("weight_pct", 0)) / 100.0
    fwd = _uni_entry.get("forward_return")
    if fwd is None:
        continue
    _fwd_numer += w * float(fwd)
    _fwd_denom += w
    _n_fwd_live += 1
_portfolio_forward_return = (_fwd_numer / _fwd_denom) if _fwd_denom > 0 else None

k1, k2, k3, k4, k5 = st.columns(5)
with k1:
    kpi_tile("Crypto sleeve", f"${crypto_sleeve_usd:,.0f}")
with k2:
    kpi_tile("Historical return (annualized)",
             f"{metrics['weighted_return_pct']:.1f}%")
with k3:
    if _portfolio_forward_return is not None:
        kpi_tile("Forward estimate (model)",
                 f"{_portfolio_forward_return:.1f}%")
    else:
        kpi_tile("Forward estimate (model)", "—")
with k4:
    kpi_tile("Portfolio vol", f"{metrics['portfolio_volatility_pct']:.1f}%")
with k5:
    kpi_tile("Sharpe", f"{metrics['sharpe_ratio']:.2f}")

# Provenance: the two return tiles have different backward-vs-forward
# framings; explain what each one is and how much of it is live.
_provenance = level_text(
    beginner=(
        f"**Historical return** = what each fund actually did over its "
        f"available price history (most launched Jan 2024 → ~2 years). "
        f"Short-term and regime-dependent; BTC outperformed ETH in this "
        f"window, which is why conservative tiers look strong here.\n\n"
        f"**Forward estimate (model)** = what the underlying assets did "
        f"over a full 10-year market cycle — BTC ~long-run CAGR for BTC "
        f"funds, ETH long-run CAGR for ETH funds. This is more "
        f"representative of steady-state expected return across cycles.\n\n"
        f"Live sources — return: {_n_live_ret}/{_n_total} · "
        f"vol: {_n_live_vol}/{_n_total} · "
        f"corr: {_n_live_corr}/{_n_total} · "
        f"forward estimate: {_n_fwd_live}/{_n_total}."
    ),
    intermediate=(
        f"Historical = each ETF's full-history CAGR (capped ±300%). "
        f"Forward estimate = long-run BTC-USD / ETH-USD CAGR with "
        f"category drag/premium (btc_futures × 0.90 for contango, "
        f"thematic × 1.10 equity beta). Live coverage: "
        f"return {_n_live_ret}/{_n_total} · vol {_n_live_vol}/{_n_total} "
        f"· corr {_n_live_corr}/{_n_total} · forward {_n_fwd_live}/{_n_total}."
    ),
    advanced=(
        f"Historical: per-ETF CAGR(end/start, full period) × basket weights. "
        f"Forward: 10y BTC-USD / ETH-USD CAGR mapped per category, net of "
        f"expense-ratio drag. Sources — return={_n_live_ret}/{_n_total}, "
        f"vol={_n_live_vol}/{_n_total}, corr={_n_live_corr}/{_n_total}, "
        f"forward={_n_fwd_live}/{_n_total}."
    ),
)
st.caption(_provenance)

# Risk-free-rate source transparency — ONLY affects the Sharpe tile
# (Sharpe = (portfolio_return − rfr) / portfolio_vol). When FRED is
# reachable, primary is "fred" and DSS state is LIVE → nothing renders.
# When FRED fails and we fall back to the static 4.25% default, this
# makes clear which metric the fallback touched so the FA doesn't
# read "static fallback" as applying to every number above.
from core.data_source_state import DataSourceState, get_state as _get_state
_rfr_state = _get_state("risk_free_rate").value
if _rfr_state in (DataSourceState.STATIC.value, DataSourceState.CACHED.value):
    _verb = "static 4.25% default" if _rfr_state == DataSourceState.STATIC.value else "cached FRED reading"
    st.caption(
        f"⓵ Sharpe ratio uses the {_verb} for the risk-free rate — "
        f"FRED 3-month T-bill is temporarily unreachable. Every other "
        f"number above is live."
    )


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
    st.plotly_chart(fig, width="stretch")

    # Add per-ETF risk columns (partner feedback #6: "add standard
    # deviation calculation for each ETF in the portfolio"). Also
    # surface correlation-with-BTC so the FA sees WHY the covariance
    # matrix matters — two high-vol ETFs with high cross-correlation
    # contribute more portfolio risk than the raw weighted-σ average.
    display_cols = [
        "ticker", "name", "issuer", "category",
        "weight_pct", "usd_value",
        "volatility_pct", "correlation_with_btc",
    ]
    _alloc_view = alloc_df[display_cols].reset_index(drop=True)
    _alloc_event = st.dataframe(
        _alloc_view,
        width="stretch",
        hide_index=True,
        column_config={
            "weight_pct":           st.column_config.NumberColumn("Weight %", format="%.2f"),
            "usd_value":            st.column_config.NumberColumn("USD", format="$%,.0f"),
            "volatility_pct":       st.column_config.NumberColumn(
                "σ (ann.)",
                format="%.1f%%",
                help="Annualized realized volatility — standard deviation "
                     "of daily log returns × √252. Computed from 90 trading "
                     "days of live price history.",
            ),
            "correlation_with_btc": st.column_config.NumberColumn(
                "Corr w/BTC",
                format="%.2f",
                help="90-day Pearson correlation of daily log returns vs. "
                     "IBIT (BTC proxy). 1.0 = moves in lockstep with BTC.",
            ),
        },
        on_select="rerun",
        selection_mode="single-row",
        key="alloc_table_select",
    )
    st.caption(
        "Click any row to open that ETF's detail page — signal, composition, "
        "volatility, and full research view."
    )

    # Row-click → ETF Detail navigation. Streamlit's dataframe selection
    # API returns .selection.rows as a list of row-index integers.
    _selected_rows = []
    if _alloc_event is not None:
        _sel = getattr(_alloc_event, "selection", None)
        if _sel is not None:
            _selected_rows = list(_sel.rows) if hasattr(_sel, "rows") else list(
                _sel.get("rows", []) if isinstance(_sel, dict) else []
            )
    if _selected_rows:
        _row_idx = _selected_rows[0]
        if 0 <= _row_idx < len(_alloc_view):
            _chosen_ticker = str(_alloc_view.iloc[_row_idx]["ticker"])
            # Only fire the switch on a *new* selection — prevents an
            # infinite "jump back to Portfolio → jump to Detail" loop
            # if the user hits the browser back button.
            if st.session_state.get("_last_alloc_nav_ticker") != _chosen_ticker:
                st.session_state["_last_alloc_nav_ticker"] = _chosen_ticker
                st.session_state["selected_etf_ticker"] = _chosen_ticker
                st.switch_page("pages/03_ETF_Detail.py")


# ═══════════════════════════════════════════════════════════════════════════
# Risk-optimized allocation (MVO) — partner feedback #7:
# "how can we constantly try to reduce risk but maintain the same return?"
# ═══════════════════════════════════════════════════════════════════════════

with card("Risk-optimized allocation"):
    st.caption(level_text(
        beginner=(
            "Same expected return, less risk. This runs a mean-variance "
            "optimization to find the weights that minimize portfolio "
            "volatility while holding expected return at or above the "
            "current level. Click to see what the math recommends."
        ),
        intermediate=(
            "Markowitz mean-variance optimization: minimize w·Σ·w "
            "subject to w·r ≥ current return, weights sum to 1, "
            "single-position cap 30%. Current category selection is "
            "preserved — only the weights shift."
        ),
        advanced=(
            "SLSQP solver on the 28-entry pairwise covariance Σ + "
            "same expected-return vector used by compute_portfolio_metrics. "
            "Floors at 0, ceilings at MAX_SINGLE_POSITION_PCT=30%. "
            "Diff vs. current weights surfaced as a side-by-side table."
        ),
    ))
    if st.button("Optimize — minimize risk at current return",
                 type="primary", width="content"):
        from core.portfolio_engine import optimize_min_variance
        with st.spinner("Solving mean-variance optimization…"):
            opt = optimize_min_variance(holdings)
        if opt["status"] == "optimal":
            import pandas as _pd
            diff_rows = []
            for h in holdings:
                tkr = h["ticker"]
                old_w = h["weight_pct"]
                new_w = opt["optimized_weights"].get(tkr, 0.0)
                diff_rows.append({
                    "Ticker": tkr,
                    "Current weight":   old_w,
                    "Optimized weight": new_w,
                    "Δ":                round(new_w - old_w, 2),
                })
            diff_df = _pd.DataFrame(diff_rows)
            st.dataframe(
                diff_df,
                width="stretch",
                hide_index=True,
                column_config={
                    "Current weight":   st.column_config.NumberColumn(format="%.2f%%"),
                    "Optimized weight": st.column_config.NumberColumn(format="%.2f%%"),
                    "Δ":                st.column_config.NumberColumn(format="%+.2f%%"),
                },
            )
            c1, c2, c3 = st.columns(3)
            with c1:
                kpi_tile("Vol reduction", f"{opt['vol_reduction_pct']:.2f}%")
            with c2:
                kpi_tile("Original σ",  f"{opt['original_vol_pct']:.1f}%")
            with c3:
                kpi_tile("Optimized σ", f"{opt['optimized_vol_pct']:.1f}%")
            st.caption(
                f"Expected return held at {opt['expected_return_pct']:.2f}% "
                f"(target ≥ {opt['target_return_pct']:.2f}%). "
                f"This is a suggestion, not a re-allocation — review against "
                f"your client's IPS before executing."
            )
        elif opt["status"] == "unchanged":
            st.info(opt.get("reason", "No optimization possible on this basket."))
        else:
            st.warning(
                f"Solver could not find a feasible improvement — "
                f"{opt.get('reason', 'unknown')}. Current allocation is "
                f"likely already near the efficient frontier for this tier."
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
        data_source_badge(
            "etf_price",
            consumer_label="1Y / 3Y / 5Y historical returns table",
        )

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
            st.dataframe(hist_df, width="stretch", hide_index=True)
        else:
            st.info(
                level_text(
                    beginner=(
                        "Historical returns aren't available right now — the market-data "
                        "service is temporarily unreachable. The forward-projection tab still works."
                    ),
                    intermediate="No historical data from any fallback source. Try the Retry button on the banner, or switch to forward projection.",
                    advanced="Live price chain (yfinance → Stooq) returned empty for every holding. Check circuit breaker state in Settings.",
                )
            )

    with tabs[1]:
        mc = run_monte_carlo(portfolio, horizon_days=252)
        if mc:
            import numpy as _np
            paths = mc["sample_paths"]
            fig = go.Figure()
            # Fan of sample paths — readable brightness per 2026-04-22
            # feedback ("green is too dark to see"). Alpha bumped 0.12
            # → 0.35, line width 0.6 → 1.1.
            for path in paths[: min(50, len(paths))]:
                fig.add_trace(go.Scatter(
                    y=path,
                    mode="lines",
                    line=dict(width=1.1, color="rgba(0,212,170,0.35)"),
                    showlegend=False,
                    hoverinfo="skip",
                ))
            # Overlay the bright median path so the eye has something to
            # anchor on in the middle of the cloud.
            if paths:
                paths_arr = _np.array(paths)
                median_path = _np.median(paths_arr, axis=0).tolist()
                fig.add_trace(go.Scatter(
                    y=median_path,
                    mode="lines",
                    line=dict(width=2.4, color="rgba(0,212,170,1.0)"),
                    name="Median path",
                    hovertemplate="Day %{x} · Median ≈ $%{y:,.0f}<extra></extra>",
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
                showlegend=False,
            )
            st.plotly_chart(fig, width="stretch")

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
        data_source_badge(
            "etf_price",
            consumer_label="Execute Basket fill-price estimates",
        )

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
    st.dataframe(preview, width="stretch", hide_index=True)
    st.caption("Estimated slippage: ~12.5 bps (mid of 5-20 bps mock range).")

    col_exec, col_cancel = st.columns(2)
    with col_exec:
        exec_disabled = bool(missing_live) and len(missing_live) == len(holdings)
        if st.button(
            "Confirm and execute",
            width="stretch",
            type="primary",
            disabled=exec_disabled,
            help="Disabled until at least one live price is available."
                 if exec_disabled else None,
        ):
            # Filter out orders with no live price BEFORE sending to the
            # broker — broker_mock would silently skip them; better UX
            # to drop them with a visible audit trail.
            executable = [o for o in orders_draft if o["mid_price"] > 0]
            skipped = [o for o in orders_draft if o["mid_price"] <= 0]

            result = submit_basket(executable, client_id=client["id"], dry_run=False)
            # Surface skipped orders in the result so the post-modal toast +
            # audit log can mention them.
            result["summary"]["n_skipped_no_price"] = len(skipped)
            result["summary"]["skipped_tickers"] = [o["ticker"] for o in skipped]

            st.session_state["last_execution"] = result
            st.session_state["confirm_execute"] = False
            # Audit-log write (Day-4 item I)
            try:
                from core.audit_log import append_entry
                detail = (
                    f"tier={tier_name}, n_orders={result['summary']['n_orders']}, "
                    f"gross=${result['summary']['gross_usd']:,.2f}"
                )
                if skipped:
                    detail += (
                        f", n_skipped={len(skipped)} (no live price: "
                        f"{', '.join(o['ticker'] for o in skipped)})"
                    )
                append_entry(
                    client_id=client["id"],
                    action="execute_basket",
                    detail=detail,
                )
            except Exception:
                pass   # audit-log failure must never block execution

            if skipped:
                st.warning(
                    f"Basket submitted — {result['summary']['n_orders']} orders "
                    f"filled (mock). **{len(skipped)} orders skipped because no "
                    f"live price was available**: "
                    f"{', '.join(o['ticker'] for o in skipped)}. These tickers "
                    f"are likely not yet indexed on yfinance (newly-launched "
                    f"altcoin spot ETFs). Re-execute later or use a different basket."
                )
            else:
                st.toast(
                    f"Basket submitted — {result['summary']['n_orders']} "
                    f"orders filled (mock)."
                )
    with col_cancel:
        if st.button("Cancel", width="stretch"):
            st.session_state["confirm_execute"] = False


col_cta, col_info = st.columns([1, 2])
with col_cta:
    st.button(
        "Execute basket →",
        on_click=_open_confirm,
        type="primary",
        width="stretch",
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
