"""
ui/sidebar.py — Shared sidebar rendered on every page.

Called by app.py (landing) and every file under pages/.

Keep this the single source of truth for sidebar content. Changes to the
brand header, nav structure, or footer live here — not in any page file.

Resolution history:
- 2026-04-23 — extracted from app.py to fix DV-1 (level selector
  persistence). Previously the sidebar only rendered on the landing
  page, which meant the selector was invisible on every other page
  and the user experienced "reset on navigation."
- 2026-04-25 — sidebar restructured per advisor-etf-DASHBOARD.html
  mockup. Level selector + theme button + refresh button + mode
  indicators all REMOVED — the topbar (rendered by ui/ds_components
  .render_top_bar in each page) owns those controls. Sidebar is now
  brand block + grouped nav (Advisor / Research / Account) + thin
  footer. user_level session-state key still defaulted to
  DEFAULT_USER_LEVEL on first call so downstream level_text() calls
  don't blow up before the topbar is wired to real widgets.
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


def _hide_streamlit_auto_nav_css() -> str:
    """Streamlit auto-discovers files in pages/ and renders its own nav at
    the top of the sidebar. The redesigned rail uses a custom grouped nav
    (Advisor / Research / Account), so we hide the auto-nav element via
    CSS. Single block returned as a string; injected once by render_sidebar.
    """
    return (
        "<style>"
        "[data-testid='stSidebarNav'] { display: none !important; }"
        # The brand block + nav group dividers don't need st.divider() lines
        # so we tighten the gap between sidebar widgets.
        "[data-testid='stSidebar'] [data-testid='stVerticalBlock'] { gap: 4px; }"
        "</style>"
    )


def _nav_group_header(label: str) -> str:
    """Return the inline-styled nav-group header HTML matching the mockup."""
    return (
        f'<div class="ds-nav-group" style="margin:14px 0 4px;padding:0 12px;'
        f'color:var(--text-muted);font-size:10.5px;font-weight:500;'
        f'letter-spacing:0.1em;text-transform:uppercase;">{label}</div>'
    )


def render_sidebar() -> None:
    """Render the full sidebar. Call from every page, after apply_theme().

    Structure (matches shared-docs/design-mockups/advisor-etf-DASHBOARD.html
    aside.rail):

        Brand block        (◐ ETF Advisor + family-office subtitle)
        ── ADVISOR ──
        · Home              (app.py)
        · Dashboard         (pages/01_Dashboard.py)
        · Portfolio         (pages/02_Portfolio.py)
        · ETF Detail        (pages/03_ETF_Detail.py)
        ── RESEARCH ──
        · Methodology       (pages/98_Methodology.py)
        ── ACCOUNT ──
        · Settings          (pages/99_Settings.py)
        Thin footer         (no widgets — topbar owns level / theme / refresh)
    """
    # Initialize user_level so level_text() callers don't crash before the
    # topbar is wired to a real widget. DEFAULT_USER_LEVEL per CLAUDE.md §7.
    if "user_level" not in st.session_state:
        st.session_state["user_level"] = DEFAULT_USER_LEVEL

    # Hide Streamlit's auto-generated nav so our custom grouped nav is the
    # only nav surface. Injected at module level via st.markdown.
    st.markdown(_hide_streamlit_auto_nav_css(), unsafe_allow_html=True)

    with st.sidebar:
        # Brand block — render via ds_components helper so the
        # advisor-family glyph + serif wordmark stays consistent.
        try:
            from ui.ds_components import render_sidebar_brand
            render_sidebar_brand(
                brand_name="ETF Advisor",
                brand_sub="Crypto ETF portfolio platform",
                brand_glyph="◐",
            )
        except Exception:
            # Fallback to a plain markdown header if the design system
            # isn't importable (kept for AppTest compat).
            if BRAND_LOGO_PATH:
                st.image(BRAND_LOGO_PATH, width="stretch")
            st.markdown(f"## {BRAND_NAME}")
            st.caption("Crypto ETF portfolio platform for advisors")

        # ── ADVISOR group ──
        st.markdown(_nav_group_header("Advisor"), unsafe_allow_html=True)
        try:
            st.page_link("app.py", label="Home", icon="◐")
            st.page_link("pages/01_Dashboard.py", label="Dashboard")
            st.page_link("pages/02_Portfolio.py", label="Portfolio")
            st.page_link("pages/03_ETF_Detail.py", label="ETF Detail")

            # ── RESEARCH group ──
            st.markdown(_nav_group_header("Research"), unsafe_allow_html=True)
            st.page_link("pages/98_Methodology.py", label="Methodology")

            # ── ACCOUNT group ──
            st.markdown(_nav_group_header("Account"), unsafe_allow_html=True)
            st.page_link("pages/99_Settings.py", label="Settings")
        except Exception:
            # AppTest doesn't register sibling pages — render captions so
            # the test mode at least sees the labels.
            for lbl in ("Home", "Dashboard", "Portfolio", "ETF Detail",
                        "Methodology", "Settings"):
                st.caption(f"→ {lbl}")

        # Footer — DEMO_MODE / extended-modules indicators are diagnostic
        # only (Cowork's mockup-parity directive removes them from the rail).
        # Kept available via Settings → operator panel; not rendered here.
        # Intentionally no widgets in the footer.
