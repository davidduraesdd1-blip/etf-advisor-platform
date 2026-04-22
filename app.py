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
from ui.theme import apply_theme, current_theme, toggle_theme
from ui.components import card, disclosure, safe_page_link


# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title=BRAND_NAME,
    page_icon="◐",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _render_sidebar() -> None:
    with st.sidebar:
        # Brand header
        if BRAND_LOGO_PATH:
            st.image(BRAND_LOGO_PATH, width="stretch")
        st.markdown(f"## {BRAND_NAME}")
        st.caption("Crypto ETF portfolio platform for advisors")

        st.divider()

        # User-level selector (CLAUDE.md §7)
        if "user_level" not in st.session_state:
            st.session_state["user_level"] = DEFAULT_USER_LEVEL
        st.session_state["user_level"] = st.radio(
            "Experience level",
            options=USER_LEVELS,
            index=USER_LEVELS.index(st.session_state["user_level"]),
            help="Scales glossary depth, chart complexity, and signal explanations.",
        )

        # Theme toggle (CLAUDE.md §8)
        theme_label = "☼ Light mode" if current_theme() == "dark" else "☾ Dark mode"
        st.button(theme_label, on_click=toggle_theme, width="stretch")

        # Refresh all data (CLAUDE.md §12)
        if st.button("⟳ Refresh all data", width="stretch"):
            st.cache_data.clear()
            st.toast("Caches cleared — data will refresh on next read.")

        st.divider()

        # Mode indicators
        if DEMO_MODE:
            st.caption("◐ Demo mode — fictional clients only")
        if EXTENDED_MODULES_ENABLED:
            st.caption("◐ Extended modules enabled (ETF + RWA + DeFi)")
        else:
            st.caption("◐ ETF-only (extended modules disabled)")


def _render_home() -> None:
    st.title(f"{BRAND_NAME}")
    st.caption(
        "Risk-profiled crypto ETF portfolios, institutional-grade research, "
        "two-click basket execution."
    )

    with card("Welcome"):
        level = st.session_state.get("user_level", DEFAULT_USER_LEVEL)
        if level == "Beginner":
            st.write(
                "This platform helps you build and manage crypto ETF allocations "
                "for your clients with the same rigor you'd apply to any other "
                "asset class. Pick a page from the sidebar to get started."
            )
        elif level == "Intermediate":
            st.write(
                "Risk-tier portfolio construction across the US-listed crypto "
                "ETF universe. Signal engine per ETF. Backtests with benchmark, "
                "max drawdown, Sharpe/Sortino/Calmar. Mock execution for demo."
            )
        else:  # Advanced
            st.write(
                "MPT + Monte Carlo + Cornish-Fisher VaR portfolio construction. "
                "Per-ETF composite signals aggregated from underlying coin "
                "indicators. Weekly rebalance + daily monitoring engine. "
                "See `docs/architecture.md` for system layout."
            )

    col1, col2, col3 = st.columns(3)
    with col1:
        with card("Dashboard"):
            st.write("Client list, rebalance flags, recent actions.")
            safe_page_link("pages/01_Dashboard.py", label="Open dashboard →")
    with col2:
        with card("Portfolio"):
            st.write("Build a risk-tiered ETF basket for a client.")
            safe_page_link("pages/02_Portfolio.py", label="Open portfolio →")
    with col3:
        with card("ETF detail"):
            st.write("Drill into a single ETF: signal, holdings, backtest.")
            safe_page_link("pages/03_ETF_Detail.py", label="Open ETF detail →")

    disclosure(
        "Hypothetical results. Past performance does not guarantee future "
        "results. All client profiles shown in demo mode are fictional. See "
        "methodology page for assumptions."
    )


def main() -> None:
    apply_theme()
    _render_sidebar()
    _render_home()


if __name__ == "__main__":
    main()
