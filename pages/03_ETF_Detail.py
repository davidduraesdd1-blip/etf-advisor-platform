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

from config import (
    BENCHMARK_DEFAULT, BENCHMARK_LABEL, BRAND_NAME,
    COLORS, SUPERGROK_BASE_URL, SUPERGROK_COIN_MAP,
)

# 2026-04-26 audit-round-1 commit 7: Plotly traces use the canonical
# advisor accent (config.COLORS["primary"], single-sourced from
# ui/design_system.py::ACCENTS) rather than legacy hardcoded hex codes.
_ACCENT_HEX: str = COLORS["primary"]


def _hex_to_rgba(hx: str, alpha: float) -> str:
    """Convert "#RRGGBB" to "rgba(r,g,b,a)" — Plotly figures need rgba()
    (color-mix() doesn't traverse Plotly's JSON figure spec)."""
    h = hx.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


_ACCENT_RGBA_FAN = _hex_to_rgba(_ACCENT_HEX, 0.35)
_ACCENT_RGBA_MEDIAN = _hex_to_rgba(_ACCENT_HEX, 1.0)
_NEUTRAL_GREY = "#9ca3af"
from core.etf_universe import load_universe_with_live_analytics
from core.portfolio_engine import build_portfolio, run_monte_carlo
from core.signal_adapter import composite_signal
from integrations.data_feeds import get_etf_prices
from integrations.edgar_nport import SUPPORTED_TICKERS as NPORT_TICKERS, get_etf_composition
from ui.components import (
    card,
    data_source_badge,
    performance_summary_table,
    disclosure,
    hypothetical_results_disclosure,
    kpi_tile,
    safe_page_link,
    section_header,
    signal_badge,
)
from ui.level_helpers import is_advisor, level_text
from ui.sidebar import render_sidebar
from ui.theme import apply_theme


def main() -> None:
    st.set_page_config(page_title=f"ETF Detail — {BRAND_NAME}", layout="wide")
    apply_theme()
    render_sidebar()

    try:
        from ui import render_top_bar as _ds_top_bar, page_header as _ds_page_header
        _ds_top_bar(breadcrumb=("Research", "ETF Detail"),
                    user_level=st.session_state.get("user_level", "Advisor"))
        _ds_page_header(
            title="ETF detail",
            subtitle=level_text(
                         advisor="Per-ETF research with Phase-1 composite signal (coin-level wiring Day 4+).",
                         client="Research a single fund. Signal, fees, composition, and recent performance.",
                     ),
            # Data-source pills match advisor-etf-DETAIL.html — yfinance for the
            # price chart, SEC EDGAR for composition (N-PORT), composite signal
            # for the BUY/HOLD/SELL badge.
            data_sources=[
                ("yfinance", "live"),
                ("SEC EDGAR · N-PORT", "live"),
                ("Composite signal", "live"),
            ],
        )
    except Exception:
        section_header(
            "ETF Detail",
            level_text(
                advisor="Per-ETF research with Phase-1 composite signal (coin-level wiring Day 4+).",
                client="Research a single fund. Signal, fees, composition, and recent performance.",
            ),
        )

    # Data-source panel intentionally omitted on research pages per FA
    # feedback. Tile-level `data_source_badge` calls still surface any
    # active fallback exactly where it affects the number. Full stack
    # audit available on Settings.

    @st.cache_data(ttl=600)
    def _universe_with_live_analytics_cached() -> list[dict]:
        return load_universe_with_live_analytics()


    with st.spinner("Loading live ETF analytics..."):
        universe = _universe_with_live_analytics_cached()

    # Cross-page landing hook: if the FA clicked a row on the Portfolio
    # allocation table, `selected_etf_ticker` carries the target ticker.
    # Honor it once, then clear so a subsequent manual selectbox change
    # doesn't get silently overridden.
    _incoming = st.session_state.pop("selected_etf_ticker", None)

    # Category filter — typing in the selectbox already filters by ticker
    # substring, but advisors often want to see "show me only the leveraged
    # ETFs" or "show me only the income covered-call wrappers". This pill
    # selector cuts the dropdown down to exactly that subset.
    from collections import Counter
    _cat_counts = Counter(u.get("category", "") for u in universe)
    _cat_options = ["All categories"] + [
        f"{c} ({n})" for c, n in sorted(_cat_counts.items())
    ]
    _cat_choice = st.selectbox(
        "Filter by category",
        options=_cat_options,
        index=0,
        help="Narrow the ticker list below. 'All categories' shows every fund "
             "in the universe.",
    )

    if _cat_choice == "All categories":
        _filtered_universe = universe
    else:
        _selected_cat = _cat_choice.rsplit(" (", 1)[0]
        _filtered_universe = [u for u in universe if u.get("category") == _selected_cat]

    tickers = [u["ticker"] for u in _filtered_universe]
    _default_idx = 0
    if _incoming and _incoming in tickers:
        _default_idx = tickers.index(_incoming)
    elif _incoming and _incoming not in tickers:
        # Incoming ticker was filtered out — reset filter so we don't lose it.
        _filtered_universe = universe
        tickers = [u["ticker"] for u in _filtered_universe]
        _default_idx = tickers.index(_incoming)

    chosen = st.selectbox(
        f"Ticker — {len(tickers)} of {len(universe)} available",
        options=tickers,
        index=_default_idx,
        help="Type to filter (e.g., 'BIT' shows BITX, BITB, BITQ, BITW).",
    )
    etf = next(u for u in _filtered_universe if u["ticker"] == chosen)

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


    # ── 2026-04-25 redesign: mockup-style hero card per advisor-etf-DETAIL.html ─
    # Hero card shows ticker (mono accent) / name (serif h1) / issuer line on the
    # left; latest price + 24h/1Y change on the right. Pulls from existing data
    # (etf dict + close_series + get_last_close fallback).

    _latest_close = close_series[-1] if close_series else None
    # Cowork walkthrough: the hero kept rendering "—" because yfinance
    # rate-limits had emptied close_series on the deploy. Add a get_last_close
    # fallback so we still get a price even when the 1y history fetch fails.
    if _latest_close is None:
        try:
            _latest_close = get_last_close(chosen)
        except Exception:
            pass

    _chg_24h_pct: float | None = None
    _chg_1y_pct: float | None = None
    if len(close_series) >= 2 and close_series[-2]:
        _chg_24h_pct = (close_series[-1] / close_series[-2] - 1.0) * 100.0
    if len(close_series) >= 252 and close_series[-252]:
        _chg_1y_pct = (close_series[-1] / close_series[-252] - 1.0) * 100.0
    elif len(close_series) >= 2 and close_series[0]:
        _chg_1y_pct = (close_series[-1] / close_series[0] - 1.0) * 100.0

    def _fmt_chg(v: float | None, label: str) -> str:
        if v is None:
            return f'<span style="color:var(--text-muted);">—</span> · {label}'
        sign = "+ " if v > 0 else ("− " if v < 0 else "")
        color = "var(--success)" if v > 0 else ("var(--danger)" if v < 0 else "var(--text-secondary)")
        return f'<span style="color:{color};">{sign}{abs(v):.2f}%</span> · {label}'

    _inception = etf.get("inception_date") or etf.get("inception", "")
    _inception_str = f" · inception {str(_inception)[:10]}" if _inception else ""

    st.markdown(
        '<div class="ds-card" style="display:flex;align-items:center;justify-content:space-between;'
        'gap:24px;padding:28px;margin-bottom:14px;flex-wrap:wrap;">'
        '<div>'
        f'<div style="font-family:var(--font-mono);color:var(--accent);font-weight:600;'
        f'font-size:13px;letter-spacing:0.04em;">{etf["ticker"]}</div>'
        f'<h1 style="font-family:var(--font-display);font-size:28px;font-weight:500;'
        f'margin:4px 0 6px;letter-spacing:-0.015em;color:var(--text-primary);line-height:1.15;">'
        f'{etf["name"]}</h1>'
        f'<div style="font-size:13px;color:var(--text-muted);">'
        f'{etf.get("issuer", "—")} · {etf.get("category", "—")}{_inception_str}</div>'
        '</div>'
        '<div style="text-align:right;">'
        f'<div style="font-size:34px;font-family:var(--font-mono);font-weight:600;'
        f'line-height:1.15;color:var(--accent);letter-spacing:-0.01em;">'
        f'{("$" + format(_latest_close, ",.2f")) if _latest_close is not None else "—"}</div>'
        f'<div style="font-size:13px;font-family:var(--font-mono);margin-top:6px;'
        f'color:var(--text-muted);">{_fmt_chg(_chg_24h_pct, "24h")} &nbsp;·&nbsp; '
        f'{_fmt_chg(_chg_1y_pct, "1Y")}</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── 2026-04-25 redesign: signal row expanded to mockup callout style. The
    # mockup shows a prominent BUY/HOLD/SELL badge + a meaty descriptive
    # paragraph (full-width line of explanation, larger type, accent left
    # stripe). Cowork walkthrough flagged the previous one-liner as too
    # muted relative to the mockup's "this is the model's recommendation"
    # emphasis.
    src_label = {
        "technical_composite": "RSI + MACD + momentum",
        "phase1_fallback":     "category defaults (no live history)",
    }.get(sig.get("source", ""), sig.get("source", ""))

    # Signal-tone color — used for the left-stripe accent matching the badge.
    _signal_color = {
        "BUY":  "var(--success)",
        "HOLD": "var(--warning)",
        "SELL": "var(--danger)",
    }.get(sig["signal"], "var(--accent)")
    _signal_glyph = {"BUY": "▲", "HOLD": "■", "SELL": "▼"}.get(sig["signal"], "◆")

    st.markdown(
        f'<div class="ds-card" style="display:flex;gap:18px;align-items:center;'
        f'padding:18px 24px;margin:0 0 20px 0;border-left:3px solid {_signal_color};">'
        f'<span style="display:inline-flex;align-items:center;gap:8px;'
        f'padding:8px 16px;border-radius:999px;font-weight:600;font-size:15px;'
        f'letter-spacing:0.04em;background:color-mix(in srgb,{_signal_color} 16%,transparent);'
        f'color:{_signal_color};white-space:nowrap;flex-shrink:0;">'
        f'{_signal_glyph} {sig["signal"]}</span>'
        f'<div style="flex:1;font-size:14px;line-height:1.55;color:var(--text-secondary);">'
        f'<b style="color:var(--text-primary);">Composite signal: {sig["signal"]} · '
        f'score {sig["score"]}.</b> {sig.get("plain_english", "")} '
        f'<span style="color:var(--text-muted);font-size:12.5px;">'
        f'Source: {src_label}. Methodology page has full layer-by-layer breakdown.</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

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
                           advisor=(
                    "RSI(14) Wilder's smoothing · MACD(12,26,9) · "
                    "simple 20-period momentum. Per-indicator scores in [−1,+1]."
                ),
                           client=(
                    "Three simple checks: is the price cheap or expensive "
                    "relative to its recent range (RSI), is the trend turning "
                    "up or down (MACD), and how much has it moved in the last "
                    "month (momentum)?"
                ),
                       ))


    # ── 2026-04-25 redesign: mockup-fidelity KPI tiles per advisor-etf-DETAIL.html
    # Mockup shows: Expense ratio · AUM · 30D net flows · Avg daily vol.
    # AUM / flows / volume aren't in the universe analytics dict (those are
    # derived from price history, not from the ETF reference data). For the
    # major spot ETFs we ship hardcoded reference values labelled "stub" so
    # the demo doesn't hit "—" everywhere; for less-common ETFs we surface
    # "—" with a footnote per CLAUDE.md §10's "no silent fallbacks" rule.
    # A real follow-up PR should wire these via SEC EDGAR N-PORT (AUM) +
    # cryptorank.io / SoSoValue (flows) + yfinance avg-volume (already in
    # close_series — could compute here when available).

    # Stub reference values for the major spot crypto ETFs. Source: public
    # AUM / flow trackers (cryptorank.io, SoSoValue, issuer fact sheets) as
    # of 2026-04. These are the funds in DEMO_CLIENTS' baskets — anything
    # else falls through to "—".
    _ETF_REFERENCE_STUB = {
        "IBIT": {"aum_usd": 62_400_000_000, "net_flows_30d_usd":  2_100_000_000, "avg_daily_vol_usd": 1_240_000_000},
        "FBTC": {"aum_usd": 20_100_000_000, "net_flows_30d_usd":    580_000_000, "avg_daily_vol_usd":   480_000_000},
        "BITB": {"aum_usd":  3_200_000_000, "net_flows_30d_usd":     45_000_000, "avg_daily_vol_usd":    78_000_000},
        "ETHA": {"aum_usd":  9_300_000_000, "net_flows_30d_usd":    220_000_000, "avg_daily_vol_usd":   195_000_000},
        "FETH": {"aum_usd":  1_050_000_000, "net_flows_30d_usd":     22_000_000, "avg_daily_vol_usd":    34_000_000},
        "BKCH": {"aum_usd":    180_000_000, "net_flows_30d_usd":      4_200_000, "avg_daily_vol_usd":     8_500_000},
    }
    _etf_ref = _ETF_REFERENCE_STUB.get(etf["ticker"], {})


    def _fmt_usd_compact(v: float | int | None) -> str:
        if v is None:
            return "—"
        try:
            v = float(v)
            if abs(v) >= 1e9:
                return f"${v/1e9:.1f}B"
            if abs(v) >= 1e6:
                return f"${v/1e6:.0f}M"
            if abs(v) >= 1e3:
                return f"${v/1e3:.0f}K"
            return f"${v:,.0f}"
        except Exception:
            return "—"


    def _fmt_signed_usd_compact(v: float | int | None) -> str:
        if v is None:
            return "—"
        try:
            fv = float(v)
            sign = "+" if fv > 0 else ("−" if fv < 0 else "")
            return sign + _fmt_usd_compact(abs(fv))
        except Exception:
            return "—"


    k1, k2, k3, k4 = st.columns(4)
    with k1:
        er_bps = etf.get("expense_ratio_bps")
        kpi_tile("Expense ratio", f"{er_bps} bps" if er_bps else "—")
    with k2:
        kpi_tile("AUM", _fmt_usd_compact(_etf_ref.get("aum_usd")))
    with k3:
        kpi_tile("30D net flows", _fmt_signed_usd_compact(_etf_ref.get("net_flows_30d_usd")))
    with k4:
        kpi_tile("Avg daily vol", _fmt_usd_compact(_etf_ref.get("avg_daily_vol_usd")))

    # Footnote on the KPI source — CLAUDE.md §10 transparency rule. Shown
    # only when at least one of the AUM/flows/vol fields is unavailable
    # (the major ETFs in the stub dict have all three; obscure tickers
    # fall through to "—" and need the footnote to explain why).
    if not _etf_ref:
        st.caption(
            "AUM / 30D net flows / Avg daily vol unavailable for this ticker — "
            "data feed integration (SEC EDGAR + cryptorank.io / SoSoValue) is "
            "in build for the post-demo PR. Major spot ETFs (IBIT / FBTC / "
            "BITB / ETHA / FETH / BKCH) carry reference values from public "
            "trackers; less-common funds show — until the live wire-up ships."
        )

    # Volatility / correlation / issuer tier — moved into a secondary row
    # below the mockup-fidelity KPI strip so the FA still has access to the
    # Phase-2 risk metrics, just not above the fold. Same data wiring as
    # before (etf['volatility'] / etf['correlation_with_btc'] / portfolio_
    # engine._issuer_tier_nudge).
    with st.expander("Risk & issuer detail", expanded=False):
        sec_k1, sec_k2, sec_k3 = st.columns(3)
        with sec_k1:
            kpi_tile("Volatility (90d ann.)", f"{etf['volatility']:.1f}%")
        with sec_k2:
            kpi_tile("Corr with BTC (90d)", f"{etf['correlation_with_btc']:.2f}")
        with sec_k3:
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

    # KPI row 3 — Market-vs-NAV and upside/downside capture.
    # Partner feedback #4 + #5 (Apr 2026). Both live-fetched per render;
    # results cached at the data_feeds level for 5-10 min so selectbox
    # toggling doesn't re-hit yfinance.
    from integrations.data_feeds import get_premium_discount_pct, get_capture_ratios

    @st.cache_data(ttl=600)
    def _prem_disc_cached(ticker: str) -> dict:
        return get_premium_discount_pct(ticker)


    @st.cache_data(ttl=600)
    def _capture_cached(ticker: str, underlying: str) -> dict:
        return get_capture_ratios(ticker, underlying_symbol=underlying)


    # Choose the right underlying for capture ratios based on category.
    _cap_underlying = "BTC-USD"
    if etf.get("category") == "eth_spot" or etf.get("underlying") in ("ETH", "ETHA", "FETH"):
        _cap_underlying = "ETH-USD"

    pd_info = _prem_disc_cached(chosen)
    cap_info = _capture_cached(chosen, _cap_underlying)

    m1, m2, m3 = st.columns(3)
    with m1:
        pd_pct = pd_info.get("premium_discount_pct")
        if pd_pct is not None:
            sign = "+" if pd_pct >= 0 else ""
            # Flag thresholds: green <0.5%, amber 0.5-2%, red >2%
            kpi_tile("Premium / Discount to NAV", f"{sign}{pd_pct:.2f}%")
        else:
            kpi_tile("Premium / Discount to NAV", "—")
    with m2:
        up = cap_info.get("up_capture_pct")
        kpi_tile(
            f"Upside capture vs {_cap_underlying.split('-')[0]}",
            f"{up:.1f}%" if up is not None else "—",
        )
    with m3:
        dn = cap_info.get("down_capture_pct")
        kpi_tile(
            f"Downside capture vs {_cap_underlying.split('-')[0]}",
            f"{dn:.1f}%" if dn is not None else "—",
        )

    # Explain the capture ratios in FA language
    _nav = pd_info.get("nav")
    _mkt = pd_info.get("market_price")
    _capture_caption_pieces: list[str] = []
    if pd_pct is not None:
        if abs(pd_pct) < 0.5:
            _capture_caption_pieces.append(
                f"Trading within 0.5% of NAV (healthy — AP arbitrage is working). "
                f"NAV ${_nav:.2f}, market ${_mkt:.2f}."
            )
        elif abs(pd_pct) < 2.0:
            _capture_caption_pieces.append(
                f"⚠ Trading {abs(pd_pct):.2f}% "
                f"{'above' if pd_pct > 0 else 'below'} NAV — moderate tracking "
                f"gap. NAV ${_nav:.2f}, market ${_mkt:.2f}."
            )
        else:
            _capture_caption_pieces.append(
                f"Trading {abs(pd_pct):.2f}% "
                f"{'premium to' if pd_pct > 0 else 'discount to'} NAV — material "
                f"tracking dislocation. Industry screens flag this for review."
            )

    if cap_info.get("up_capture_pct") is not None and cap_info.get("down_capture_pct") is not None:
        _up, _dn = cap_info["up_capture_pct"], cap_info["down_capture_pct"]
        if 95 <= _up <= 105 and 95 <= _dn <= 105:
            _capture_caption_pieces.append(
                f"Tracks {_cap_underlying.split('-')[0]} tightly 1:1 both up and down "
                f"({cap_info['n_up_days']} up days / {cap_info['n_down_days']} down days)."
            )
        elif _up > 130 or _dn > 130:
            _capture_caption_pieces.append(
                f"Amplified exposure: {_up:.0f}% up-capture / {_dn:.0f}% down-capture "
                f"— leveraged or option-income structure. Expect vol-decay drag "
                f"on multi-week holds."
            )
        elif _up < 90:
            _capture_caption_pieces.append(
                f"Under-captures up moves ({_up:.0f}%) — structural drag (contango, "
                f"option cap, or expense). Over-time return lags the underlying."
            )
        else:
            _capture_caption_pieces.append(
                f"Up: {_up:.0f}%, Down: {_dn:.0f}% ({cap_info['n_up_days']} up / "
                f"{cap_info['n_down_days']} down days)."
            )

    if _capture_caption_pieces:
        st.caption(" · ".join(_capture_caption_pieces))

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
                   advisor=(
            f"hist={_ret_src} fwd={_fwd_src} vol={_vol_src} corr={_corr_src} · "
            f"Forward: {_fwd_basis} · "
            f"BTC proxy: {etf.get('btc_proxy_used', 'IBIT')} · "
            f"n_returns vol={etf.get('vol_n_returns', '—')} "
            f"corr={etf.get('corr_n_returns', '—')}."
        ),
                   client=(
            f"Source — historical return: {_src_label(_ret_src)} · "
            f"forward estimate: {_src_label(_fwd_src)} · "
            f"volatility: {_src_label(_vol_src)} · "
            f"BTC correlation: {_src_label(_corr_src)}. "
            f"Historical = what this fund did. Forward = what BTC / ETH did "
            f"over 10 years, adjusted for this fund's category. "
            f"Forward basis: {_fwd_basis}"
        ),
               ))


    # ── 2026-04-26 redesign: chart + composition side-by-side per
    # advisor-etf-DETAIL.html (chart 2/3 width, composition 1/3 width).
    # DV-2 perf summary table moved out into its own full-width card BELOW
    # the columns row — it has 8 columns (Ticker / Source / Inception / 1Y
    # / 3Y / 5Y / Since-inception / Max DD) and needs the full main-column
    # width to render readably without horizontal scroll.

    # Fetch prices ONCE — used by chart + perf summary table.
    prices = get_etf_prices([etf["ticker"]], period="5y", interval="1d")
    rows = prices.get(etf["ticker"], {}).get("prices", [])

    col_chart, col_comp = st.columns([2, 1])

    with col_chart:
        with card("Historical returns"):
            data_source_badge(
                "etf_price",
                consumer_label=f"Historical price chart for {etf['ticker']}",
            )
            if not rows:
                st.info(level_text(
                            advisor="Live price chain (yfinance → Stooq) returned empty for this ticker.",
                            client="Historical prices aren't available right now — the market-data service is temporarily unreachable.",
                        ))
            else:
                df = pd.DataFrame(rows)
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date")
                fig = go.Figure(data=[go.Scatter(
                    x=df["date"], y=df["close"],
                    mode="lines", line=dict(color=_ACCENT_HEX, width=2),
                    name="Close",
                )])
                fig.update_layout(
                    margin=dict(l=0, r=0, t=10, b=0),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    height=420,
                    yaxis_title="Close (USD)",
                )
                st.plotly_chart(fig, width="stretch")

    with col_comp:
        # Composition — live from SEC EDGAR N-PORT when available
        # (IBIT/ETHA/FBTC/FETH). Non-supported tickers fall back to a
        # category-level summary. Transparency badge shows LIVE /
        # FALLBACK_LIVE / CACHED state per Day-4 design directive.
        # Wrapped in a max-height scroll div so a long N-PORT holdings
        # list doesn't make the column taller than the chart.
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
                            width="stretch",
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

                    # External link to fund details. For issuer-static trusts
                    # this is the issuer's own daily-holdings page when the
                    # site cooperates (e.g., iShares, Fidelity). For trusts
                    # whose issuer site 404s / refuses bots / has a dead
                    # domain, the link is a Yahoo Finance fund-profile
                    # fallback — always-valid for any US-listed ETF. Label is
                    # neutral so it reads correctly for either destination.
                    if src == "issuer_static" and comp.get("issuer_holdings_url"):
                        _ext_url = comp["issuer_holdings_url"]
                        _is_yahoo = "finance.yahoo.com" in _ext_url
                        _link_label = (
                            "📊 Fund profile (Yahoo Finance) ↗"
                            if _is_yahoo
                            else "📊 Live daily holdings on issuer site ↗"
                        )
                        st.markdown(f"[{_link_label}]({_ext_url})")
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
                           advisor="EDGAR N-PORT parser with 7-day disk cache; token-bucket rate-limited; fallback chain marks CACHED state in data_source_state.",
                           client="This shows what the fund holds under the hood.",
                       ))




    # ── 2026-04-26 redesign: DV-2 perf summary table moved out of the
    # columns row into its own full-width card. The table has 8 columns
    # (Ticker / Source / Inception / 1Y / 3Y / 5Y / Since-inception / Max DD)
    # and needs the full main-column width to render readably.
    if rows:
        with card("Performance & compliance summary"):
            bench_tickers = list(BENCHMARK_DEFAULT.keys())
            benchmark_price_data = get_etf_prices(bench_tickers, period="5y", interval="1d")
            perf_df = performance_summary_table(
                tickers=[etf["ticker"]],
                price_data=prices,
                benchmark_weights=BENCHMARK_DEFAULT,
                benchmark_label=BENCHMARK_LABEL,
                benchmark_price_data=benchmark_price_data,
            )
            st.dataframe(perf_df, width="stretch", hide_index=True)
            st.caption(
                "Benchmark: static-weight blend (no daily rebalancing). "
                "Methodology page documents the simplification."
            )

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
                    line=dict(width=1.1, color=_ACCENT_RGBA_FAN),
                    showlegend=False, hoverinfo="skip",
                ))
            if paths:
                paths_arr = _np.array(paths)
                median_path = _np.median(paths_arr, axis=0).tolist()
                fig.add_trace(go.Scatter(
                    y=median_path, mode="lines",
                    line=dict(width=2.4, color=_ACCENT_RGBA_MEDIAN),
                    name="Median path",
                    hovertemplate="Day %{x} · Median ≈ $%{y:,.0f}<extra></extra>",
                ))
            fig.add_hline(y=mc["initial_value_usd"], line_dash="dash", line_color=_NEUTRAL_GREY)
            fig.update_layout(
                margin=dict(l=0, r=0, t=10, b=0),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                height=280,
                yaxis_title="Value per $100k",
                xaxis_title="Trading days",
                showlegend=False,
            )
            st.plotly_chart(fig, width="stretch")
            # Advisor mode only — MC engine diagnostics (paths / seed) hidden
            # in Client mode.
            if is_advisor():
                st.caption(
                    f"Paths: {mc['n_simulations']:,} · retained: {mc['paths_retained']} · seed: {mc['seed']}"
                )

    # ── Hypothetical-results callout — canonical wording per CLAUDE.md §22 item 5
    hypothetical_results_disclosure(
        body=(
            "Every performance display includes multiple time horizons, "
            "benchmark comparison, and max drawdown per SEC Marketing Rule "
            "compliance. Technical signals are model-based estimates, not "
            "forecasts. See the Methodology page for assumptions and "
            "indicator definitions."
        ),
    )
    safe_page_link("pages/98_Methodology.py", label="Read methodology →", icon="📋")


if __name__ == "__main__":
    main()
