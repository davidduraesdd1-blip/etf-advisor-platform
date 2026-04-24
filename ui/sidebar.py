"""
ui/sidebar.py — Shared sidebar rendered on every page.

Called by app.py (landing) and every file under pages/. Implements CLAUDE.md
§§6 (brand), §7 (user-level selector), §8 (theme toggle), §12 (refresh),
and §22 demo-mode indicators.

Keep this the single source of truth for sidebar content. Changes to the
brand header, level selector, theme toggle, refresh button, or mode
indicators live here — not in any page file.

Resolution history:
- 2026-04-23 — extracted from app.py to fix DV-1 (level selector
  persistence). Previously the sidebar only rendered on the landing
  page, which meant the selector was invisible on every other page
  and the user experienced "reset on navigation."
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
from ui.theme import current_theme, toggle_theme


# Widget key for the user-level radio. Explicit key prevents Streamlit's
# implicit call-site hashing from producing per-page widget instances
# that would break session_state persistence across navigation.
_USER_LEVEL_WIDGET_KEY = "user_level_radio"


def render_sidebar() -> None:
    """Render the full sidebar. Call from every page, after apply_theme()."""
    with st.sidebar:
        # Brand header (CLAUDE.md §6)
        if BRAND_LOGO_PATH:
            st.image(BRAND_LOGO_PATH, width="stretch")
        st.markdown(f"## {BRAND_NAME}")
        st.caption("Crypto ETF portfolio platform for advisors")

        st.divider()

        # User-level selector (CLAUDE.md §7)
        # Initialize persistent value first, then render the widget
        # using session_state as the source of truth.
        if "user_level" not in st.session_state:
            st.session_state["user_level"] = DEFAULT_USER_LEVEL
        selected = st.radio(
            "Experience level",
            options=USER_LEVELS,
            index=USER_LEVELS.index(st.session_state["user_level"]),
            help="Scales glossary depth, chart complexity, and signal explanations.",
            key=_USER_LEVEL_WIDGET_KEY,
        )
        # Mirror the widget's current value back to the canonical key so
        # any code path reading st.session_state["user_level"] stays in
        # sync regardless of which page rendered the sidebar.
        st.session_state["user_level"] = selected

        # Theme toggle (CLAUDE.md §8)
        theme_label = "☼ Light mode" if current_theme() == "dark" else "☾ Dark mode"
        st.button(theme_label, on_click=toggle_theme, width="stretch")

        # Refresh all data (CLAUDE.md §12)
        if st.button("⟳ Refresh all data", width="stretch"):
            st.cache_data.clear()
            st.toast("Caches cleared — data will refresh on next read.")

        st.divider()

        # Mode indicators (CLAUDE.md §22)
        if DEMO_MODE:
            st.caption("◐ Demo mode — fictional clients only")
        if EXTENDED_MODULES_ENABLED:
            st.caption("◐ Extended modules enabled (ETF + RWA + DeFi)")
        else:
            st.caption("◐ ETF-only (extended modules disabled)")
