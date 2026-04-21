"""
Portfolio View — risk-tier selector, allocation chart, performance panel,
"Execute basket" CTA.

Scaffold for Day 1. Day 2 adds portfolio_engine. Day 3 adds the UI wiring.
"""
from __future__ import annotations

import streamlit as st

from config import BRAND_NAME, PORTFOLIO_TIERS
from ui.theme import apply_theme
from ui.components import coming_soon, section_header


st.set_page_config(page_title=f"Portfolio — {BRAND_NAME}", layout="wide")
apply_theme()

section_header(
    "Portfolio",
    "Build a risk-tiered crypto ETF basket for a client.",
)

# Preview of the tier selector — behavior wires up on Day 3.
tier_names = list(PORTFOLIO_TIERS.keys())
selected = st.radio(
    "Risk tier",
    options=tier_names,
    index=2,  # default to Moderate
    horizontal=True,
)
meta = PORTFOLIO_TIERS[selected]
st.caption(
    f"Tier {meta['tier_number']} · ceiling {meta['ceiling_pct']}% · "
    f"rebalance {meta['rebalance']} · {meta['typical_client']}"
)

coming_soon("Portfolio construction")
