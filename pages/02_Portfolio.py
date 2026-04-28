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
    BENCHMARK_DEFAULT,
    BENCHMARK_LABEL,
    BRAND_NAME,
    COLORS,
    DEMO_MODE,
    PORTFOLIO_TIERS,
)

# 2026-04-26 audit-round-1 commit 7: Plotly traces consume the design-system
# accent token rather than the legacy "#00d4aa" hardcode. _ACCENT_HEX picks
# up the canonical advisor-teal via config.COLORS["primary"], which itself
# reads from ui/design_system.py::ACCENTS. Single source of truth.
_ACCENT_HEX: str = COLORS["primary"]


def _hex_to_rgba(hx: str, alpha: float) -> str:
    """Convert a #RRGGBB hex string into an `rgba(r,g,b,a)` literal —
    Plotly figures need rgba() (CSS color-mix() doesn't work in JSON
    figures). Used for the Monte-Carlo path-fan transparency overlays."""
    h = hx.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


_ACCENT_RGBA_FAN = _hex_to_rgba(_ACCENT_HEX, 0.35)
_ACCENT_RGBA_MEDIAN = _hex_to_rgba(_ACCENT_HEX, 1.0)
_NEUTRAL_GREY = "#9ca3af"  # Plotly hline annotation; matches ui/theme.py muted
# Sprint 3: client list flows through the active adapter.
from core.client_adapter import get_active_client as get_client
from core.client_adapter import get_active_clients
from core.etf_universe import load_universe_with_live_analytics
from core.portfolio_engine import build_portfolio, run_monte_carlo
from integrations.broker_alpaca_paper import submit_basket_via
from integrations.data_feeds import get_etf_prices, get_last_close
from ui.components import (
    card,
    data_source_badge,
    performance_summary_table,
    disclosure,
    kpi_tile,
    safe_page_link,
    section_header,
    tier_pill_selector,
)

# Defensive helper import (audit-round-1 hotfix for stale Streamlit Cloud
# deploy). Falls back to a minimal renderer if the canonical helper
# isn't present in the cached module.
try:
    from ui.components import hypothetical_results_disclosure
except ImportError:  # pragma: no cover — stale-deploy fallback
    def hypothetical_results_disclosure(body: str | None = None, *,
                                         margin_top_px: int = 24) -> None:
        st.info(
            "**Hypothetical results.** Past performance does not "
            "guarantee future results. " + (body or "")
        )
from ui.level_helpers import is_advisor, is_client, level_text
from ui.sidebar import render_sidebar
from ui.theme import apply_theme


def main() -> None:
    st.set_page_config(page_title=f"Portfolio — {BRAND_NAME}", layout="wide")
    apply_theme()
    render_sidebar()


    # ═══════════════════════════════════════════════════════════════════════════
    # Client selection
    # ═══════════════════════════════════════════════════════════════════════════

    try:
        from ui import render_top_bar as _ds_top_bar, page_header as _ds_page_header
        _ds_top_bar(breadcrumb=("Advisor", "Portfolio"),
                    user_level=st.session_state.get("user_level", "Advisor"))
        # Data-source pill row — mirrors the mockup's 4 pills (EDGAR / yfinance /
        # News / Broker mock). Tones: live=success-tick, cached=warning-tick.
        _ds_page_header(
            title="Portfolio",
            subtitle=level_text(
                         advisor="Risk-tiered crypto ETF basket for the selected client. Backtests, benchmark comparison, and execution staging — all compliance-safe for FA presentations.",
                         client="Pick a client, pick a risk tier, and see the crypto ETF basket we recommend.",
                     ),
            data_sources=[
                ("SEC EDGAR", "live"),
                ("yfinance", "live"),
                ("News", "cached"),
                ("Broker · mock", "live"),
            ],
        )
    except Exception:
        section_header(
            "Portfolio",
            level_text(
                advisor="Phase-2 pairwise-correlation basket, issuer-tier adjusted, with forward MC projection.",
                client="Pick a client, pick a risk tier, and see the crypto ETF basket we recommend.",
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

    # Sprint 3: client list from the active adapter, not hardcoded demo.
    DEMO_CLIENTS = get_active_clients()
    if not DEMO_CLIENTS:
        st.warning(
            "No clients available from the active data source. "
            "Check Settings → Client data source for adapter status."
        )
        return
    default_id = st.session_state.get("active_client_id", DEMO_CLIENTS[0]["id"])
    options = {f"{c['name']} — {c.get('label','')}": c["id"] for c in DEMO_CLIENTS}
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
                   advisor=(
            f"Tier {tier_meta['tier_number']} · ceiling {tier_meta['ceiling_pct']}% · "
            f"max_drawdown_pct={tier_meta['max_drawdown_pct']} · rebalance {tier_meta['rebalance']}."
        ),
                   client=(
            f"Tier {tier_meta['tier_number']} · {tier_meta['typical_client']}. "
            f"Rebalance every {tier_meta['rebalance']}."
        ),
               ))


    # ── 2026-04-25 redesign: render the mockup-style top KPI strip right after
    # the tier selector. Pulls 4 numbers that match the advisor-etf-portfolio.html
    # layout: Sharpe (3Y) · Max DD (5Y) · Return (1Y) · Crypto allocation ceiling.
    # Render is deferred until after `metrics` exists; we just stub the function
    # here and call it once `portfolio` has been built below.

    def _render_mockup_kpi_strip(metrics_dict: dict, ceiling_pct: float, tier_label: str) -> None:
        """Mockup-fidelity 4-up KPI strip (used near top of Portfolio page)."""
        sharpe = metrics_dict.get("sharpe_ratio")
        max_dd = metrics_dict.get("max_drawdown_pct")
        one_y = metrics_dict.get("return_1y_pct") or metrics_dict.get("weighted_return_pct")
        bench_one_y = metrics_dict.get("benchmark_return_1y_pct")
        bench_sharpe = metrics_dict.get("benchmark_sharpe")

        def _fmt_pct(v, signed=True, decimals=1):
            if v is None:
                return "—"
            try:
                fv = float(v)
                sign = "+ " if (signed and fv > 0) else ("− " if (signed and fv < 0) else "")
                return f"{sign}{abs(fv):.{decimals}f}%"
            except Exception:
                return "—"

        def _fmt_num(v, decimals=2):
            if v is None:
                return "—"
            try:
                return f"{float(v):.{decimals}f}"
            except Exception:
                return "—"

        sharpe_sub = f"vs benchmark {_fmt_num(bench_sharpe)}" if bench_sharpe is not None else "3-year rolling"
        one_y_sub = (f"vs benchmark {_fmt_pct(bench_one_y)}"
                     if bench_one_y is not None else "1-year basket return")
        one_y_color = ("var(--success)" if (one_y is not None and float(one_y) > 0)
                       else ("var(--danger)" if (one_y is not None and float(one_y) < 0) else ""))

        def _kpi(lbl: str, val: str, sub: str, *, val_color: str = "", sub_class: str = "") -> str:
            color_attr = f" style=\"color:{val_color};\"" if val_color else ""
            sub_color = ""
            if sub_class == "up":
                sub_color = "color:var(--success);"
            elif sub_class == "down":
                sub_color = "color:var(--danger);"
            return (
                "<div>"
                f'<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.08em;">{lbl}</div>'
                f'<div style="font-size:26px;font-family:var(--font-mono);font-weight:500;line-height:1.15;margin-top:4px;color:var(--text-primary);"{color_attr}>{val}</div>'
                f'<div style="font-size:12px;margin-top:4px;font-family:var(--font-mono);color:var(--text-muted);{sub_color}">{sub}</div>'
                "</div>"
            )

        st.markdown(
            '<div class="ds-card" style="margin-bottom:24px;">'
            '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:var(--gap);">'
            + _kpi("Sharpe (3Y)", _fmt_num(sharpe), sharpe_sub,
                   sub_class=("up" if (sharpe is not None and bench_sharpe is not None
                                       and float(sharpe) > float(bench_sharpe)) else ""))
            + _kpi("Max drawdown (5Y)", _fmt_pct(max_dd), "BTC basket trough",
                   val_color="var(--danger)" if max_dd is not None else "",
                   sub_class="down" if max_dd is not None else "")
            + _kpi("Return (1Y)", _fmt_pct(one_y), one_y_sub,
                   val_color=one_y_color,
                   sub_class=("up" if (one_y is not None and bench_one_y is not None
                                       and float(one_y) > float(bench_one_y)) else ""))
            + _kpi("Crypto allocation ceiling", f"{ceiling_pct:.0f}%",
                   f"{tier_label} · of total portfolio")
            + '</div></div>',
            unsafe_allow_html=True,
        )


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
            "**Fiduciary-appropriate filter ON** — leveraged and single-stock "
            "covered-call wrappers excluded from this basket. "
            "Toggle in Settings for aggressive-sleeve-approved IPS clients."
        )
    holdings = portfolio["holdings"]
    metrics = portfolio["metrics"]

    # Mockup-fidelity 4-up KPI strip (Sharpe / Max DD / 1Y / Allocation ceiling).
    # Renders BEFORE the existing 5-tile KPI row so the FA's first read is the
    # advisor-etf-portfolio.html top-card, then they can dive into the existing
    # detail tiles for sleeve/forward/vol detail.
    try:
        _render_mockup_kpi_strip(
            metrics_dict=metrics,
            ceiling_pct=float(tier_meta.get("ceiling_pct", client["crypto_allocation_pct"])),
            tier_label=f"Tier {tier_meta['tier_number']}",
        )
    except Exception as _e_kpi:
        pass  # Strip is decorative; legacy tiles below still render the same data

    # 2026-04-28 hotfix: Advanced risk metrics panel (Advisor mode only).
    # Surfaces VaR_95 / VaR_99 / CVaR_95 / CVaR_99 with $ amounts +
    # CF-boundary disclosure when the polynomial estimate exceeds the
    # 100% long-only loss bound. Hidden in Client mode (too granular for
    # screen-share) and gated behind an expander to keep the page chrome
    # uncluttered for the FA's primary view.
    if is_advisor():
        with st.expander("Advanced risk metrics (VaR / CVaR — Advisor mode)",
                         expanded=False):
            from ui.components import risk_metrics_panel
            risk_metrics_panel(metrics, sleeve_usd=crypto_sleeve_usd)

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
    # Provenance caption — historical vs forward returns + live-source coverage.
    # The 5-tile KPI strip that previously rendered here was REMOVED in the
    # 2026-04-25 mockup-parity sprint (Commit 4) — those metrics are now
    # surfaced in the holdings & performance table below, and the page-top
    # 4-up KPI strip (Sharpe / Max DD / 1Y / Allocation ceiling) replaces the
    # at-a-glance read. Forward-estimate computation is preserved here so the
    # provenance caption + footer "Read methodology →" link still has accurate
    # live-vs-fallback counts to surface.
    # ═══════════════════════════════════════════════════════════════════════════

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

    # Provenance: the two return framings (historical vs forward) need a
    # small note so the FA doesn't read short-window CAGR as steady-state.
    _provenance = level_text(
                      advisor=(
            f"Historical: per-ETF CAGR(end/start, full period) × basket weights. "
            f"Forward: 10y BTC-USD / ETH-USD CAGR mapped per category, net of "
            f"expense-ratio drag. Sources — return={_n_live_ret}/{_n_total}, "
            f"vol={_n_live_vol}/{_n_total}, corr={_n_live_corr}/{_n_total}, "
            f"forward={_n_fwd_live}/{_n_total}."
        ),
                      client=(
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
                marker_color=_ACCENT_HEX,
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
                       advisor=(
                "SLSQP solver on the 28-entry pairwise covariance Σ + "
                "same expected-return vector used by compute_portfolio_metrics. "
                "Floors at 0, ceilings at MAX_SINGLE_POSITION_PCT=30%. "
                "Diff vs. current weights surfaced as a side-by-side table."
            ),
                       client=(
                "Same expected return, less risk. This runs a mean-variance "
                "optimization to find the weights that minimize portfolio "
                "volatility while holding expected return at or above the "
                "current level. Click to see what the math recommends."
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
                       advisor="1Y/3Y/5Y historical from yfinance (live fallback chain) + 10k-path forward projection.",
                       client="How this basket has performed historically and where it could land over the next year.",
                   ))

        tabs = st.tabs(["Historical", "Forward projection (Monte Carlo)"])

        with tabs[0]:
            tickers = [h["ticker"] for h in holdings]
            price_data = get_etf_prices(tickers, period="5y", interval="1d")
            data_source_badge(
                "etf_price",
                consumer_label="1Y / 3Y / 5Y / since-inception historical returns table",
            )

            # DV-2: fetch benchmark components so the helper can append a
            # blended-benchmark row per CLAUDE.md §22 item 5.
            bench_tickers = list(BENCHMARK_DEFAULT.keys())
            benchmark_price_data = get_etf_prices(bench_tickers, period="5y", interval="1d")

            hist_df = performance_summary_table(
                tickers=tickers,
                price_data=price_data,
                benchmark_weights=BENCHMARK_DEFAULT,
                benchmark_label=BENCHMARK_LABEL,
                benchmark_price_data=benchmark_price_data,
            )

            # Trigger the no-data fallback only if EVERY cell in EVERY horizon
            # is a placeholder (em-dash or "N/A"). Real returns render as
            # signed percent strings, e.g. "+30.25%".
            has_any_return = any(
                str(cell).endswith("%")
                for col in ("1Y %", "3Y %", "5Y %", "since-inception %")
                for cell in hist_df[col]
            )

            if has_any_return:
                st.dataframe(hist_df, width="stretch", hide_index=True)
                st.caption(
                    "Benchmark: static-weight blend (no daily rebalancing). "
                    "Methodology page documents the simplification."
                )
            else:
                st.info(
                    level_text(
                        advisor="Live price chain (yfinance → Stooq) returned empty for every holding. Check circuit breaker state in Settings.",
                        client=(
                            "Historical returns aren't available right now — the market-data "
                            "service is temporarily unreachable. The forward-projection tab still works."
                        ),
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
                        line=dict(width=1.1, color=_ACCENT_RGBA_FAN),
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
                        line=dict(width=2.4, color=_ACCENT_RGBA_MEDIAN),
                        name="Median path",
                        hovertemplate="Day %{x} · Median ≈ $%{y:,.0f}<extra></extra>",
                    ))
                fig.add_hline(
                    y=mc["initial_value_usd"],
                    line_dash="dash",
                    line_color=_NEUTRAL_GREY,
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

                # Advisor mode: show MC engine internals (paths / seed) for the
                # FA's own diagnostic. Hidden in Client mode — too granular for
                # screen-share with a client.
                if is_advisor():
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
                       advisor=(
                f"{len(holdings)} orders · ${crypto_sleeve_usd:,.0f} gross · "
                "broker=mock · est. slippage 12.5 bps · tif=day."
            ),
                       client=(
                f"You are about to submit {len(holdings)} orders totalling "
                f"${crypto_sleeve_usd:,.0f} to the demo broker. No real money will move."
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

                # Provider chosen via Settings → Broker routing (session-state
                # override) or config.BROKER_PROVIDER fallback. submit_basket_via
                # routes to broker_mock.submit_basket / broker_alpaca_paper.submit_basket;
                # graceful fallback if alpaca-py isn't installed or keys missing.
                from config import BROKER_PROVIDER as _CFG_BROKER
                _provider = st.session_state.get("broker_provider_override", _CFG_BROKER)
                result = submit_basket_via(
                    _provider, executable, client_id=client["id"], dry_run=False,
                )
                # Surface skipped orders in the result so the post-modal toast +
                # audit log can mention them.
                result["summary"]["n_skipped_no_price"] = len(skipped)
                result["summary"]["skipped_tickers"] = [o["ticker"] for o in skipped]

                # Sprint 4 — register streaming callbacks for each child order
                # so Alpaca trade-update events flow back into session_state and
                # the "Recent submissions" expander renders live status. Only
                # active when the alpaca_paper provider is selected AND the
                # streaming module is configured (env vars set). For 'mock', this
                # block is a no-op — broker_mock fills synchronously so streaming
                # would have nothing to subscribe to. CLAUDE.md §22 — never
                # fabricate; we only register if real streaming is live.
                if _provider == "alpaca_paper":
                    try:
                        from integrations import alpaca_streaming as _streaming
                        if _streaming.is_configured():
                            _streaming.start_order_stream()
                            if "order_status" not in st.session_state:
                                st.session_state["order_status"] = {}
                            for fill in result.get("fills", []):
                                _coid = fill.get("order_id") or fill.get("client_order_id")
                                if not _coid:
                                    continue
                                # Seed initial state so the expander has a row
                                # to show even before the first WebSocket event
                                # arrives.
                                st.session_state["order_status"][_coid] = {
                                    "status":          fill.get("status", "submitted"),
                                    "symbol":          fill.get("ticker"),
                                    "side":            fill.get("side"),
                                    "fill_qty":        None,
                                    "fill_price":      None,
                                    "last_update_iso": result.get("submitted_at"),
                                }

                                # Closure factor needs default-arg trick to
                                # capture _coid by value in the loop.
                                def _make_cb(coid: str):
                                    def _cb(status_row: dict) -> None:
                                        try:
                                            st.session_state["order_status"][coid] = status_row
                                        except Exception:
                                            pass
                                    return _cb
                                _streaming.register_order_callback(_coid, _make_cb(_coid))
                    except Exception:
                        # Streaming wiring must never block execution; the
                        # Settings panel surfaces stream health for debugging.
                        pass

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


    # ── 2026-04-25 redesign: mockup-style exec-row + compliance callout ─────────
    # Layout matches advisor-etf-portfolio.html lines 587-622:
    #   exec-row (2 cols): "Ready to execute basket" card  |  rebalance/last-reviewed card
    #   callout: "Hypothetical results..." with methodology link

    _n_holdings = len(holdings)
    _basket_notional = sum(h.get("dollar_amount", 0) or h.get("notional_usd", 0)
                           for h in holdings) or crypto_sleeve_usd

    # Format last rebalance + next rebalance (next = last + cadence)
    def _fmt_date(iso_str: str) -> str:
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.strftime("%b %d, %Y")
        except Exception:
            return "—"

    _last_reviewed_str = _fmt_date(client.get("last_rebalance_iso", ""))
    # Estimate next rebalance from cadence
    _cadence_days = {"weekly": 7, "bi-weekly": 14, "bi-monthly": 60,
                     "monthly": 30, "quarterly": 90, "semi-annual": 180,
                     "annually": 365, "annual": 365}.get(
        str(tier_meta.get("rebalance", "")).lower(), 60
    )
    try:
        _last_dt = datetime.fromisoformat(client["last_rebalance_iso"].replace("Z", "+00:00"))
        from datetime import timedelta as _td
        _next_str = (_last_dt + _td(days=_cadence_days)).strftime("%b %d, %Y")
    except Exception:
        _next_str = "—"

    _exec_l, _exec_r = st.columns(2)

    with _exec_l:
        st.markdown(
            '<div class="ds-card" style="padding:28px;">'
            '<div style="display:flex;align-items:center;justify-content:space-between;gap:20px;">'
            '<div style="max-width:40ch;">'
            '<h3 style="font-family:var(--font-display);font-size:20px;font-weight:500;'
            'margin:0 0 6px;letter-spacing:-0.01em;color:var(--text-primary);">'
            'Ready to execute basket</h3>'
            f'<p style="margin:0;color:var(--text-secondary);font-size:13px;">'
            f'You are about to submit a {tier_name} basket for {client["name"]}. '
            f'{_n_holdings} holdings · ${_basket_notional:,.0f} notional. '
            'Mock broker — no real order is placed.</p>'
            '</div></div></div>',
            unsafe_allow_html=True,
        )
        # Real Streamlit button — sits visually under the exec card. The `primary`
        # type pulls the accent color so it reads as the page CTA.
        st.button(
            "Execute basket →",
            on_click=_open_confirm,
            type="primary",
            width="stretch",
            key="exec_basket_cta",
        )
        st.caption(level_text(
                       advisor="BROKER_PROVIDER='mock' per config.py. Day-4+ routes to alpaca_paper.",
                       client="Demo mode — no real orders will be placed.",
                   ))

    with _exec_r:
        st.markdown(
            '<div class="ds-card" style="padding:20px;">'
            '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;'
            'letter-spacing:0.08em;">Rebalance cadence</div>'
            f'<div style="font-size:18px;font-family:var(--font-mono);font-weight:500;'
            f'line-height:1.15;margin-top:4px;color:var(--text-primary);">'
            f'{tier_meta.get("rebalance", "—").title()}</div>'
            f'<div style="font-size:12px;color:var(--text-muted);margin-top:4px;'
            f'font-family:var(--font-mono);">next rebalance: {_next_str}</div>'
            '<hr style="border:none;border-top:1px solid var(--border);margin:14px 0;">'
            '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;'
            'letter-spacing:0.08em;">Last reviewed</div>'
            f'<div style="font-size:18px;font-family:var(--font-mono);font-weight:500;'
            f'line-height:1.15;margin-top:4px;color:var(--text-primary);">{_last_reviewed_str}</div>'
            f'<div style="font-size:12px;color:var(--text-muted);margin-top:4px;'
            f'font-family:var(--font-mono);">drift {client["drift_pct"]:.1f}σ · '
            f'{"rebal needed" if client["rebalance_needed"] else "on target"}</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    # ── Hypothetical-results callout — canonical wording per CLAUDE.md §22 item 5
    hypothetical_results_disclosure(margin_top_px=20)

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

    # ── Sprint 4 — Recent submissions (live order-status stream) ─────────────
    # Surfaces the latest 10 orders with a status pill so the FA can watch
    # paper-trading fills come back in real-time. Reads from the disk cache
    # in integrations/alpaca_streaming so the panel survives Streamlit cold
    # restart. Only renders when at least one order has been tracked, to
    # avoid an empty card on first visit. CLAUDE.md §22 — every value shown
    # comes from the streaming module; no fabricated rows.
    try:
        from integrations import alpaca_streaming as _streaming
        _recent = _streaming.snapshot_recent(limit=10)
    except Exception:
        _recent = []

    if _recent:
        with st.expander(f"Recent submissions ({len(_recent)})", expanded=False):
            _PILL_COLORS = {
                "submitted":     ("#f59e0b", "Submitted"),
                "new":           ("#f59e0b", "New"),
                "pending_new":   ("#f59e0b", "Pending"),
                "accepted":      ("#3b82f6", "Accepted"),
                "partial_fill":  ("#3b82f6", "Partial"),
                "fill":          ("#22c55e", "Filled"),
                "filled":        ("#22c55e", "Filled"),
                "rejected":      ("#ef4444", "Rejected"),
                "canceled":      ("#9ca3af", "Canceled"),
                "expired":       ("#9ca3af", "Expired"),
            }
            for row in _recent:
                _status = str(row.get("status", "")).lower()
                _color, _label = _PILL_COLORS.get(_status, ("#9ca3af", _status or "—"))
                _coid = row.get("client_order_id", "—")
                _sym = row.get("symbol") or "—"
                _side = (row.get("side") or "").upper()
                _qty = row.get("fill_qty")
                _price = row.get("fill_price")
                _qp = (
                    f"{_qty} @ ${_price}" if _qty and _price
                    else f"{_qty}" if _qty else "—"
                )
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:12px;'
                    f'padding:6px 0;border-bottom:1px solid var(--border);'
                    f'font-family:var(--font-mono);font-size:12px;">'
                    f'<span style="display:inline-block;padding:2px 10px;'
                    f'border-radius:10px;background:{_color};color:#fff;'
                    f'font-size:11px;font-weight:600;min-width:70px;'
                    f'text-align:center;">▲ {_label}</span>'
                    f'<span style="color:var(--text-primary);">{_sym}</span>'
                    f'<span style="color:var(--text-secondary);">{_side}</span>'
                    f'<span style="color:var(--text-muted);">{_qp}</span>'
                    f'<span style="color:var(--text-muted);margin-left:auto;'
                    f'font-size:11px;">{_coid[:16]}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            st.caption(
                "Live updates via Alpaca trade-update WebSocket. Status "
                "pill colors: amber=submitted/new, blue=accepted/partial, "
                "green=filled, red=rejected, grey=canceled. Pair color "
                "with the ▲ shape (CLAUDE.md §8 — never color-only)."
            )


if __name__ == "__main__":
    main()
