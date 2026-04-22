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

from config import BRAND_NAME, SUPERGROK_BASE_URL, SUPERGROK_COIN_MAP
from core.etf_universe import load_universe_with_live_analytics
from core.portfolio_engine import build_portfolio, run_monte_carlo
from core.signal_adapter import composite_signal
from integrations.data_feeds import get_etf_prices
from integrations.edgar_nport import SUPPORTED_TICKERS as NPORT_TICKERS, get_etf_composition
from ui.components import (
    card,
    data_source_badge,
    data_sources_panel,
    disclosure,
    kpi_tile,
    safe_page_link,
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

# Top-of-page data-source audit panel (Option 3).
data_sources_panel(key="ds_panel_etf_detail")

@st.cache_data(ttl=600)
def _universe_with_live_analytics_cached() -> list[dict]:
    return load_universe_with_live_analytics()


with st.spinner("Loading live ETF analytics..."):
    universe = _universe_with_live_analytics_cached()
tickers = [u["ticker"] for u in universe]

# Cross-page landing hook: if the FA clicked a row on the Portfolio
# allocation table, `selected_etf_ticker` carries the target ticker.
# Honor it once, then clear so a subsequent manual selectbox change
# doesn't get silently overridden.
_default_idx = 0
_incoming = st.session_state.pop("selected_etf_ticker", None)
if _incoming and _incoming in tickers:
    _default_idx = tickers.index(_incoming)

chosen = st.selectbox("Ticker", options=tickers, index=_default_idx)
etf = next(u for u in universe if u["ticker"] == chosen)

# Fetch live price history so signal_adapter can run its Day-4 upgrade
# path (RSI + MACD + momentum). If history is unavailable the adapter
# falls back to Phase-1 internally and labels the source accordingly.
price_bundle = get_etf_prices([chosen], period="1y", interval="1d")
close_series: list[float] = []
for row in price_bundle.get(chosen, {}).get("prices", []):
    try:
        c = float(row.get("close"))
        if c > 0:
            close_series.append(c)
    except (TypeError, ValueError):
        continue

sig = composite_signal(etf, closes=close_series if len(close_series) >= 35 else None)


# Header row: ticker info + signal
col_id, col_sig = st.columns([3, 1])
with col_id:
    st.markdown(f"### {etf['ticker']} — {etf['name']}")
    st.caption(f"{etf['issuer']} · {etf['category']}")
with col_sig:
    signal_badge(sig["signal"])
    src_label = {
        "technical_composite": "RSI + MACD + momentum",
        "phase1_fallback":     "category defaults (no live history)",
    }.get(sig.get("source", ""), sig.get("source", ""))
    st.caption(level_text(
        beginner=sig["plain_english"],
        intermediate=f"{sig['signal']} · score {sig['score']} · {src_label}",
        advanced=f"{sig['signal']} · score {sig['score']} · source={sig.get('source','')}",
    ))

# Technical-indicator breakdown when the composite path was taken.
if sig.get("source") == "technical_composite" and sig.get("components"):
    comps = sig["components"]
    with card("Signal components"):
        c1, c2, c3 = st.columns(3)
        with c1:
            kpi_tile("RSI(14)", f"{comps['rsi_value']:.1f}",
                     delta=f"score {comps['rsi_score']:+.2f}")
        with c2:
            kpi_tile("MACD histogram", f"{comps['macd_hist']:.3f}",
                     delta=f"score {comps['macd_score']:+.2f}")
        with c3:
            kpi_tile("Momentum (20d)", f"{comps['mom_pct']:.1f}%",
                     delta=f"score {comps['mom_score']:+.2f}")
        st.caption(level_text(
            beginner=(
                "Three simple checks: is the price cheap or expensive "
                "relative to its recent range (RSI), is the trend turning "
                "up or down (MACD), and how much has it moved in the last "
                "month (momentum)?"
            ),
            intermediate=(
                "Weighted composite: 45% RSI + 35% MACD histogram + 20% "
                "20-day momentum. Thresholds: BUY ≥ +0.30, SELL ≤ −0.30."
            ),
            advanced=(
                "RSI(14) Wilder's smoothing · MACD(12,26,9) · "
                "simple 20-period momentum. Per-indicator scores in [−1,+1]."
            ),
        ))


# KPI tiles — row 1: fund characteristics
k1, k2, k3, k4 = st.columns(4)
with k1:
    er_bps = etf.get("expense_ratio_bps")
    kpi_tile("Expense ratio", f"{er_bps} bps" if er_bps else "—")
with k2:
    kpi_tile("Volatility (90d ann.)", f"{etf['volatility']:.1f}%")
with k3:
    kpi_tile("Corr with BTC (90d)", f"{etf['correlation_with_btc']:.2f}")
with k4:
    from core.portfolio_engine import _issuer_tier_nudge
    nudge = _issuer_tier_nudge(etf)
    tier_label = "A (preferred)" if nudge > 0 else ("C (discouraged)" if nudge < 0 else "B (neutral)")
    kpi_tile("Issuer tier", tier_label)

# KPI row 2: returns — historical (from this fund's own price history)
# + forward estimate (from long-run underlying CAGR)
r1, r2 = st.columns(2)
with r1:
    hist = etf.get("expected_return")
    kpi_tile("Historical return (annualized)",
             f"{hist:.1f}%" if hist is not None else "—")
with r2:
    fwd = etf.get("forward_return")
    kpi_tile("Forward estimate (model)",
             f"{fwd:.1f}%" if fwd is not None else "—")

# Count how many ETFs share this fund's category so the user knows
# why the forward estimate clusters with its category peers.
_cat = etf.get("category", "")
_n_same_cat = sum(1 for u in universe if u.get("category") == _cat)
if _n_same_cat > 1:
    _cat_pretty = _cat.replace("_", " ")
    st.caption(
        f"Forward estimate uses a category-level formula (long-run "
        f"BTC-USD / ETH-USD CAGR with category-specific drag/premium), "
        f"so all {_n_same_cat} funds in the **{_cat_pretty}** category "
        f"land within expense-ratio tolerance of each other. Historical "
        f"return above is per-fund and will vary by individual price "
        f"history."
    )

# Per-tile provenance — makes live/fallback explicit on ETF Detail.
_vol_src = etf.get("volatility_source", "category_default")
_corr_src = etf.get("correlation_source", "category_default")
_ret_src = etf.get("expected_return_source", "category_default")
_fwd_src = etf.get("forward_return_source", "unavailable")
_fwd_basis = etf.get("forward_return_basis", "")


def _src_label(src: str) -> str:
    return {
        "live":             "live",
        "self":             "self (BTC proxy)",
        "live_long_run":    "live 10yr underlying",
        "unavailable":      "unavailable",
        "category_default": "category default (live unavailable)",
    }.get(src, src)


st.caption(level_text(
    beginner=(
        f"Source — historical return: {_src_label(_ret_src)} · "
        f"forward estimate: {_src_label(_fwd_src)} · "
        f"volatility: {_src_label(_vol_src)} · "
        f"BTC correlation: {_src_label(_corr_src)}. "
        f"Historical = what this fund did. Forward = what BTC / ETH did "
        f"over 10 years, adjusted for this fund's category. "
        f"Forward basis: {_fwd_basis}"
    ),
    intermediate=(
        f"Historical src: {_ret_src} · Forward src: {_fwd_src} · "
        f"Vol src (90d σ·√252): {_vol_src} · "
        f"BTC-corr src (90d Pearson vs IBIT): {_corr_src}. "
        f"Forward basis: {_fwd_basis}"
    ),
    advanced=(
        f"hist={_ret_src} fwd={_fwd_src} vol={_vol_src} corr={_corr_src} · "
        f"Forward: {_fwd_basis} · "
        f"BTC proxy: {etf.get('btc_proxy_used', 'IBIT')} · "
        f"n_returns vol={etf.get('vol_n_returns', '—')} "
        f"corr={etf.get('corr_n_returns', '—')}."
    ),
))


# Historical returns
with card("Historical returns"):
    prices = get_etf_prices([etf["ticker"]], period="5y", interval="1d")
    data_source_badge(
        "etf_price",
        consumer_label=f"Historical price chart for {etf['ticker']}",
    )
    rows = prices.get(etf["ticker"], {}).get("prices", [])
    if not rows:
        st.info(level_text(
            beginner="Historical prices aren't available right now — the market-data service is temporarily unreachable.",
            intermediate="No price data from any live source.",
            advanced="Live price chain (yfinance → Stooq) returned empty for this ticker.",
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


# Composition — live from SEC EDGAR N-PORT when available (IBIT/ETHA/FBTC/FETH).
# Non-supported tickers fall back to a category-level summary. Transparency
# badge shows LIVE / FALLBACK_LIVE / CACHED state per Day-4 design directive.
with card("Composition"):
    data_source_badge(
        "etf_composition",
        consumer_label=f"Composition table for {etf['ticker']}",
    )

    if chosen in NPORT_TICKERS:
        comp = get_etf_composition(chosen)
        src = comp["source"]
        if src == "edgar_live":
            st.caption(
                f"Live from SEC EDGAR N-PORT filing · "
                f"dated {comp['filing_date']} · accession {comp['accession']}"
            )
        elif src == "issuer_static":
            # '33-Act spot commodity trust — no N-PORT filing exists;
            # composition derived from issuer's prospectus + daily
            # holdings disclosure. Intentionally honest about the
            # non-live source while providing a link out to verify.
            st.caption(
                f"Spot commodity trust · issuer-curated composition "
                f"(trusts don't file N-PORT). Custodian: {comp.get('custodian', 'N/A')}."
            )
        elif src == "cached":
            st.info(comp["note"])
        else:
            st.info(comp["note"])

        if comp["holdings"]:
            # ── Trust composition path (2 rows, coin + cash) ─────────
            # Render manually so markdown hyperlinks work. st.dataframe
            # cells don't render markdown; st.column_config.LinkColumn
            # expects cell values to be plain URLs, not [text](url)
            # syntax, which led to the previous "wrapped markdown as
            # path" routing bug.
            _has_sg_link = False
            if src == "issuer_static":
                # Column headers
                hcol1, hcol2, hcol3 = st.columns([3, 2, 2])
                hcol1.markdown("**Name**")
                hcol2.markdown("**Asset category**")
                hcol3.markdown("**% of fund**")
                st.divider()

                for h in comp["holdings"]:
                    raw_name = h.get("name", "")
                    pct = h.get("pct_value") or 0.0
                    asset_cat = h.get("asset_cat", "")
                    sg_symbol = SUPERGROK_COIN_MAP.get(raw_name)

                    c1, c2, c3 = st.columns([3, 2, 2])
                    with c1:
                        if sg_symbol:
                            _has_sg_link = True
                            sg_url = f"{SUPERGROK_BASE_URL}?coin={sg_symbol}"
                            st.markdown(f"**[{raw_name} →]({sg_url})**")
                        else:
                            st.markdown(f"**{raw_name}**")
                    with c2:
                        st.markdown(asset_cat or "—")
                    with c3:
                        st.markdown(f"{pct:.2f}%")
                st.caption(f"Total holdings: {comp['holdings_count']}")
            else:
                # ── N-PORT path (longer holdings list) — dataframe ──
                # These are futures / securities holdings, not spot
                # coins, so we don't SuperGrok-link them.
                import pandas as _pd
                df_rows = [
                    {
                        "Name":      h.get("name", ""),
                        "Asset cat": h.get("asset_cat", ""),
                        "Balance":   h.get("balance"),
                        "Value USD": h.get("value_usd"),
                        "% of fund": h.get("pct_value"),
                    }
                    for h in comp["holdings"]
                ]
                df = _pd.DataFrame(df_rows)
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Value USD": st.column_config.NumberColumn(format="$%,.0f"),
                        "% of fund": st.column_config.NumberColumn(format="%.2f%%"),
                    },
                )
                st.caption(f"Total holdings: {comp['holdings_count']}")

            # SuperGrok onboarding notice — only surface when there's
            # at least one clickable coin in this basket.
            if _has_sg_link:
                st.info(
                    "**→ SuperGrok integration:** Click any coin name "
                    "above to open full technical + on-chain research. "
                    "**First-time tip:** once SuperGrok loads, click the "
                    "'Analyze All Coins Now' button to seed the data "
                    "(nothing populates until you do)."
                )

            # For issuer-static trusts, surface the issuer's live
            # holdings page so the FA can audit the per-share coin
            # count published daily.
            if src == "issuer_static" and comp.get("issuer_holdings_url"):
                st.markdown(
                    f"📊 [Live daily holdings on issuer site]"
                    f"({comp['issuer_holdings_url']})"
                )
            if comp.get("note"):
                st.caption(comp["note"])
    else:
        category = etf.get("category", "")
        underlying = etf.get("underlying", "")
        _category_summaries = {
            "btc_spot":            "Bitcoin spot exposure, custodied by the issuer.",
            "eth_spot":            "Ethereum spot exposure, custodied by the issuer. "
                                   "Staking yield may be distributed (ETHA / FETH / ETH as of Feb 2026).",
            "btc_futures":         "Bitcoin futures (CME). No spot BTC holdings. "
                                   "Tracking error + contango drag vs. spot.",
            "eth_futures":         "Ethereum futures (CME). Tracking error + contango drag vs. spot.",
            "altcoin_spot":        f"Spot {underlying or 'altcoin'} exposure. Approved via SEC's "
                                   f"Sep-2025 generic listing standard for commodity trusts.",
            "leveraged":           f"2× daily leveraged exposure to "
                                   f"{underlying or 'crypto'} via swaps / futures. "
                                   f"Volatility decay erodes long-run multi-day returns "
                                   f"below the headline leverage factor.",
            "income_covered_call": f"Covered-call strategy on {underlying or 'underlying crypto asset'}. "
                                   f"Caps upside participation in exchange for option "
                                   f"premium distributions (typically paid weekly or monthly).",
            "thematic_equity":     "Basket of crypto-industry equities (miners, exchanges, "
                                   "blockchain infrastructure). Equity-market beta on top of "
                                   "crypto-asset exposure.",
            "multi_asset":         "Multi-asset basket of large-cap cryptocurrencies "
                                   "(typically BTC/ETH-dominant with long-tail altcoin exposure).",
        }
        st.write(f"Summary view: {_category_summaries.get(category, 'thematic / multi-asset exposure.')}")
        st.caption(
            "Live EDGAR N-PORT holdings are wired for IBIT / ETHA / FBTC / FETH "
            "in the demo scope. Full issuer coverage lands post-demo."
        )

    st.caption(level_text(
        beginner="This shows what the fund holds under the hood.",
        intermediate="Holdings come from SEC EDGAR N-PORT filings (quarterly cadence).",
        advanced="EDGAR N-PORT parser with 7-day disk cache; token-bucket rate-limited; fallback chain marks CACHED state in data_source_state.",
    ))


# Single-ticker Monte Carlo projection
with card("Forward projection"):
    # Build a synthetic 1-holding "portfolio" at 100% weight so the MC
    # engine has the single ETF's own expected_return + volatility as
    # portfolio-level drift + diffusion. Going through build_portfolio
    # with a tier mix would require the ETF's category to be allocated
    # by whatever tier we picked — which fails silently for categories
    # the tier doesn't touch (e.g., btc_futures, eth_futures are
    # EXCLUDED_CATEGORIES and wouldn't get any allocation).
    try:
        single_holding = {
            "ticker":               etf["ticker"],
            "name":                 etf.get("name", etf["ticker"]),
            "issuer":               etf.get("issuer", ""),
            "category":             etf.get("category", "btc_spot"),
            "weight_pct":           100.0,
            "usd_value":            100_000.0,
            "expected_return_pct":  float(etf.get("expected_return", 0.0)),
            "volatility_pct":       float(etf.get("volatility", 0.0)),
            "correlation_with_btc": float(etf.get("correlation_with_btc", 1.0)),
            "expense_ratio_bps":    etf.get("expense_ratio_bps"),
        }
        from core.portfolio_engine import compute_portfolio_metrics as _metrics
        synthetic_metrics = _metrics([single_holding], 100_000.0, "Ultra Aggressive")
        p = {
            "tier_name":            "single_etf",
            "portfolio_value_usd":  100_000.0,
            "holdings":             [single_holding],
            "metrics":              synthetic_metrics,
        }
        mc = run_monte_carlo(p, horizon_days=252)
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "ETF Detail forward projection failed for %s: %s", etf.get("ticker"), exc
        )
        p = None
        mc = None

    if mc:
        import numpy as _np
        paths = mc["sample_paths"]
        fig = go.Figure()
        # Fan of sample paths — alpha 0.15 → 0.35, width 0.6 → 1.1 per
        # 2026-04-22 readability feedback.
        for path in paths[: min(40, len(paths))]:
            fig.add_trace(go.Scatter(
                y=path, mode="lines",
                line=dict(width=1.1, color="rgba(0,212,170,0.35)"),
                showlegend=False, hoverinfo="skip",
            ))
        if paths:
            paths_arr = _np.array(paths)
            median_path = _np.median(paths_arr, axis=0).tolist()
            fig.add_trace(go.Scatter(
                y=median_path, mode="lines",
                line=dict(width=2.4, color="rgba(0,212,170,1.0)"),
                name="Median path",
                hovertemplate="Day %{x} · Median ≈ $%{y:,.0f}<extra></extra>",
            ))
        fig.add_hline(y=mc["initial_value_usd"], line_dash="dash", line_color="#9ca3af")
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=280,
            yaxis_title="Value per $100k",
            xaxis_title="Trading days",
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
        if is_advanced():
            st.caption(
                f"Paths: {mc['n_simulations']:,} · retained: {mc['paths_retained']} · seed: {mc['seed']}"
            )

disclosure(
    "Hypothetical results. Past performance does not guarantee future "
    "results. Technical signals are model-based estimates, not forecasts. "
    "See the Methodology page for assumptions and indicator definitions."
)
safe_page_link("pages/98_Methodology.py", label="Read methodology →", icon="📋")
