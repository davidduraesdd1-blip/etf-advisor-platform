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


def _sidebar_layout_css() -> str:
    """Sidebar layout CSS.

    2026-04-26 second hotfix: previous hotfix kept Streamlit's auto-nav
    as a fallback. The custom st.page_link calls demonstrably work on
    the live deploy now (per user walkthrough), so showing both surfaces
    produced a duplicate-nav UX. Hide the auto-nav again; custom nav is
    the single click surface.

    Per-item try/except around each st.page_link in render_sidebar()
    stays in place — if a single link can't resolve (e.g., "app.py" as
    the entrypoint sometimes fails), the rest of the nav still renders.
    """
    return (
        "<style>"
        # Hide Streamlit's auto-discovered nav — custom grouped nav owns
        # this surface.
        "[data-testid='stSidebarNav'] { display: none !important; }"
        # Tighten gap between sidebar widgets so brand + nav-group headers
        # stack cleanly without rivers of whitespace.
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

    # 2026-04-26 hotfix: stop hiding Streamlit's auto-nav. Our custom
    # st.page_link calls fail on Streamlit Cloud (path resolution differs
    # from local), and when the first call raises the entire try-block
    # bails to a caption-only fallback that has no click handlers. The
    # auto-nav always works.
    st.markdown(_sidebar_layout_css(), unsafe_allow_html=True)

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

        # Per-item st.page_link with individual try/except so a single
        # failure (e.g. "app.py" not resolvable in some Streamlit
        # versions) doesn't blank the rest of the nav. If page_link is
        # entirely unavailable (older Streamlit), the auto-nav above the
        # brand block still works — these are belt + suspenders.
        def _safe_page_link(path: str, label: str, icon: str | None = None) -> bool:
            try:
                if icon:
                    st.page_link(path, label=label, icon=icon)
                else:
                    st.page_link(path, label=label)
                return True
            except Exception:
                return False

        # ── ADVISOR group ──
        st.markdown(_nav_group_header("Advisor"), unsafe_allow_html=True)
        _safe_page_link("app.py", "Home", "◐")
        _safe_page_link("pages/01_Dashboard.py", "Dashboard")
        _safe_page_link("pages/02_Portfolio.py", "Portfolio")
        _safe_page_link("pages/03_ETF_Detail.py", "ETF Detail")

        # ── RESEARCH group ──
        st.markdown(_nav_group_header("Research"), unsafe_allow_html=True)
        _safe_page_link("pages/98_Methodology.py", "Methodology")

        # ── ACCOUNT group ──
        st.markdown(_nav_group_header("Account"), unsafe_allow_html=True)
        _safe_page_link("pages/99_Settings.py", "Settings")

        # Footer — DEMO_MODE / extended-modules indicators are diagnostic
        # only (Cowork's mockup-parity directive removes them from the rail).
        # Kept available via Settings → operator panel; not rendered here.
        # Intentionally no widgets in the footer.
