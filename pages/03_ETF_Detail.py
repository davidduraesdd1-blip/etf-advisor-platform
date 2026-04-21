"""
ETF Detail — per-ticker drilldown. Signal, holdings, backtest, composition.

Scaffold for Day 1. Day 3 wires real data + signal_adapter.
"""
from __future__ import annotations

import streamlit as st

from config import BRAND_NAME, ETF_UNIVERSE_SEED
from ui.theme import apply_theme
from ui.components import coming_soon, section_header


st.set_page_config(page_title=f"ETF Detail — {BRAND_NAME}", layout="wide")
apply_theme()

section_header(
    "ETF Detail",
    "Per-ETF signal, composition, and backtest.",
)

tickers = [e["ticker"] for e in ETF_UNIVERSE_SEED]
ticker = st.selectbox("Ticker", options=tickers, index=0)

etf = next(e for e in ETF_UNIVERSE_SEED if e["ticker"] == ticker)
st.caption(f"{etf['name']} · {etf['issuer']} · {etf['category']}")

coming_soon(f"ETF Detail — {ticker}")
