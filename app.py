"""
ETF Advisor Platform — main entry point.

Streamlit's multipage convention auto-discovers files in ./pages. This file
renders the landing ("Home") view + the persistent sidebar used across all
pages: brand header, user-level selector, theme toggle, Refresh All Data,
extended-modules preview indicator.

CLAUDE.md governance: Sections 6, 7, 8, 12, 22.
"""
from __future__ import annotations

import streamlit as st

from config import (
    BRAND_NAME,
    BRAND_LOGO_PATH,
    DEFAULT_USER_LEVEL,
    DEMO_MODE,
    EXTENDED_MODULES_ENABLED,
    USER_LEVELS,
)
from ui.theme import apply_theme
from ui.sidebar import render_sidebar
from ui.components import card, disclosure, safe_page_link


# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title=BRAND_NAME,
    page_icon="◐",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _render_home() -> None:
    # ── 2026-05 redesign: advisor-family top bar + page header ──
    try:
        from ui import render_top_bar as _ds_top_bar, page_header as _ds_page_header
        _ds_top_bar(breadcrumb=("Advisor", "Home"),
                    user_level=st.session_state.get("user_level", "beginner"))
        _ds_page_header(
            title=BRAND_NAME,
            subtitle="Risk-profiled crypto ETF portfolios, institutional-grade research, two-click basket execution.",
            data_sources=[("Custody feed", "live"), ("ETF pricing", "live")],
        )
    except Exception:
        st.title(f"{BRAND_NAME}")
        st.caption(
            "Risk-profiled crypto ETF portfolios, institutional-grade research, "
            "two-click basket execution."
        )

    # ── 2026-04-25 redesign: advisor-family card primitives on Home body ─────
    # Replaces legacy `card("Welcome")` + 3 `card()` nav tiles + `disclosure()`
    # chip with the design-system .ds-card look matching the other 4 ported
    # pages. No mockup exists for Home (it's a thin landing); applies tokens
    # + new primitive without redesigning content. Per Cowork's Commit 6
    # directive ("apply design system tokens... don't redesign body content").

    level = st.session_state.get("user_level", DEFAULT_USER_LEVEL)
    if level == "Beginner":
        _welcome_body = (
            "This platform helps you build and manage crypto ETF allocations "
            "for your clients with the same rigor you'd apply to any other "
            "asset class. Pick a page from the sidebar to get started."
        )
    elif level == "Intermediate":
        _welcome_body = (
            "Risk-tier portfolio construction across the US-listed crypto "
            "ETF universe. Signal engine per ETF. Backtests with benchmark, "
            "max drawdown, Sharpe/Sortino/Calmar. Mock execution for demo."
        )
    else:  # Advanced
        _welcome_body = (
            "MPT + Monte Carlo + Cornish-Fisher VaR portfolio construction. "
            "Per-ETF composite signals aggregated from underlying coin "
            "indicators. Weekly rebalance + daily monitoring engine. "
            "See <code>docs/architecture.md</code> for system layout."
        )

    st.markdown(
        '<div class="ds-card" style="margin-bottom:20px;">'
        '<div style="font-family:var(--font-display);font-weight:500;font-size:18px;'
        'color:var(--text-primary);margin:0 0 8px;letter-spacing:-0.01em;">Welcome</div>'
        f'<div style="font-size:14px;line-height:1.6;color:var(--text-secondary);max-width:68ch;">'
        f'{_welcome_body}</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # 3-up nav tiles matching the advisor-family card look. Each tile has a
    # serif title, body line, and a real Streamlit page-link below for the
    # actual click. Two-step (visual card + link) because st.page_link's
    # default look doesn't blend with the design system; rendering both
    # gives the mockup chrome AND keeps Streamlit's built-in routing.
    col1, col2, col3 = st.columns(3)

    def _nav_card(col, *, title: str, body: str, page: str, label: str) -> None:
        with col:
            st.markdown(
                '<div class="ds-card" style="margin-bottom:8px;">'
                '<div style="font-family:var(--font-display);font-weight:500;font-size:16px;'
                'color:var(--text-primary);margin:0 0 6px;letter-spacing:-0.01em;">'
                f'{title}</div>'
                f'<div style="font-size:13px;color:var(--text-secondary);line-height:1.5;">'
                f'{body}</div>'
                '</div>',
                unsafe_allow_html=True,
            )
            safe_page_link(page, label=label)

    _nav_card(col1,
              title="Dashboard",
              body="Client roster, rebalance flags, AUM snapshot, recent activity.",
              page="pages/01_Dashboard.py",
              label="Open dashboard →")
    _nav_card(col2,
              title="Portfolio",
              body="Risk-tiered ETF basket for a client. Holdings, performance, execute.",
              page="pages/02_Portfolio.py",
              label="Open portfolio →")
    _nav_card(col3,
              title="ETF detail",
              body="Per-fund hero + signal + composition + DV-2 performance table.",
              page="pages/03_ETF_Detail.py",
              label="Open ETF detail →")

    # Hypothetical-results callout matching the other 4 ported pages.
    st.markdown(
        '<div style="display:flex;gap:14px;align-items:flex-start;'
        'padding:16px 20px;margin-top:24px;'
        'background:color-mix(in srgb,var(--accent) 5%,var(--bg-1));'
        'border:1px solid color-mix(in srgb,var(--accent) 20%,var(--border));'
        'border-left:3px solid var(--accent);border-radius:8px;font-size:13px;">'
        '<div style="width:22px;height:22px;border-radius:50%;'
        'background:var(--accent-soft);color:var(--accent);'
        'display:grid;place-items:center;font-weight:600;font-size:13px;flex-shrink:0;">i</div>'
        '<div><strong style="color:var(--text-primary);">Hypothetical results.</strong> '
        'Past performance does not guarantee future results. All client profiles '
        'shown in demo mode are fictional. See the Methodology page for '
        'assumptions.</div></div>',
        unsafe_allow_html=True,
    )


def main() -> None:
    apply_theme()
    render_sidebar()
    _render_home()


if __name__ == "__main__":
    main()
