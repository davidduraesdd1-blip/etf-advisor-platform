"""
Dashboard — advisor's home view. Client list, status, rebalance flags.

Scaffold for Day 1. Day 3 wires to the real client data layer.
"""
from __future__ import annotations

import streamlit as st

from config import BRAND_NAME
from ui.theme import apply_theme
from ui.components import coming_soon, section_header


st.set_page_config(page_title=f"Dashboard — {BRAND_NAME}", layout="wide")
apply_theme()

section_header(
    "Dashboard",
    "Client roster, rebalance flags, recent actions.",
)

coming_soon("Dashboard")

st.caption(
    f"User level: {st.session_state.get('user_level', 'Beginner')}. "
    "Selector lives in the sidebar on the Home page."
)
