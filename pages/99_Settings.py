"""
Settings — broker routing, monitoring preferences, feature flag toggles.

Scaffold for Day 1. Day 3 + Day 4 build out the real controls.
"""
from __future__ import annotations

import streamlit as st

from config import (
    BRAND_NAME,
    BROKER_PROVIDER,
    DEMO_MODE,
    EXTENDED_MODULES_ENABLED,
    ETF_PRICE_SOURCE,
    ETF_REFERENCE_SOURCE,
)
from ui.theme import apply_theme
from ui.components import coming_soon, section_header


st.set_page_config(page_title=f"Settings — {BRAND_NAME}", layout="wide")
apply_theme()

section_header("Settings", "Config flags currently in effect.")

st.markdown("**Feature flags (read-only today — wired via `config.py`):**")
st.write({
    "EXTENDED_MODULES_ENABLED": EXTENDED_MODULES_ENABLED,
    "DEMO_MODE": DEMO_MODE,
    "BROKER_PROVIDER": BROKER_PROVIDER,
    "ETF_PRICE_SOURCE": ETF_PRICE_SOURCE,
    "ETF_REFERENCE_SOURCE": ETF_REFERENCE_SOURCE,
})

coming_soon("Settings controls (Day 3)")
