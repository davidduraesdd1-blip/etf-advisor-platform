"""
Dashboard — advisor home view. Client roster with status, rebalance flags,
per-client Open-Portfolio link.

All clients shown are fictional demo personas per CLAUDE.md §22 item 3.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from config import BRAND_NAME, DEMO_MODE
from core.audit_log import seed_demo_entries
from core.demo_clients import DEMO_CLIENTS
from ui.components import (
    card,
    data_source_badge,
    disclosure,
    section_header,
)
from ui.level_helpers import level_text
from ui.theme import apply_theme


st.set_page_config(page_title=f"Dashboard — {BRAND_NAME}", layout="wide")
apply_theme()

section_header(
    "Dashboard",
    level_text(
        beginner="Your client list with rebalance status and quick links.",
        intermediate="Client roster: tier, drift, rebalance flags.",
        advanced="Client roster with tier / drift / flags. Filter + sort via column headers.",
    ),
)

if not DEMO_MODE:
    st.info(
        "Demo mode is currently OFF. Dashboard would be populated from the "
        "advisor's connected CRM. This view requires `DEMO_MODE=True` or a "
        "real client-data integration to render useful data."
    )
    st.stop()

# Seed the audit log with illustrative history on first visit.
try:
    seed_demo_entries(DEMO_CLIENTS)
except Exception:
    pass   # Seed failure is not demo-blocking

# Data-source transparency — consume this page's primary data. In demo
# mode clients are local; prices come from data_feeds which registers
# its own state. Show badges for each dependency.
st.markdown("**Data sources**")
badge_cols = st.columns(2)
with badge_cols[0]:
    st.caption("Client roster")
    st.caption("Source: local demo fixtures (fictional — not real client data)")
with badge_cols[1]:
    st.caption("ETF prices")
    data_source_badge("etf_price")

# Build the roster table
df = pd.DataFrame([
    {
        "Client":       f"{c['name']} · DEMO",
        "Label":        c["label"],
        "Age":          c["age"],
        "Tier":         c["assigned_tier"],
        "Portfolio $":  c["total_portfolio_usd"],
        "Crypto %":     c["crypto_allocation_pct"],
        "Drift %":      c["drift_pct"],
        "Rebalance":    "⚠ Needed" if c["rebalance_needed"] else "Aligned",
        "Last rebalance": pd.to_datetime(c["last_rebalance_iso"]).strftime("%Y-%m-%d"),
    }
    for c in DEMO_CLIENTS
])

with card("Clients"):
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Portfolio $":  st.column_config.NumberColumn(format="$%,.0f"),
            "Crypto %":     st.column_config.NumberColumn(format="%.1f%%"),
            "Drift %":      st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

# Per-client detail + navigation
with card("Open client portfolio"):
    client_options = {
        f"{c['name']} — {c['label']}": c["id"]
        for c in DEMO_CLIENTS
    }
    chosen = st.selectbox(
        "Client",
        options=list(client_options.keys()),
        label_visibility="collapsed",
    )
    chosen_id = client_options[chosen]
    c = next(x for x in DEMO_CLIENTS if x["id"] == chosen_id)
    situation = c.get("situation_today", "")
    st.caption(level_text(
        beginner=(
            f"Open {c['name']}'s portfolio to see allocation, performance, "
            f"and the Execute Basket workflow."
        ),
        intermediate=(
            f"Tier: {c['assigned_tier']} · Drift {c['drift_pct']}% · "
            f"Crypto sleeve {c['crypto_allocation_pct']}%. "
            + (f"Today: {situation}" if situation else "")
        ),
        advanced=(
            f"Drift {c['drift_pct']}% · last rebalance {c['last_rebalance_iso'][:10]} · "
            f"tier {c['assigned_tier']} · ${c['total_portfolio_usd']:,.0f} total."
        ),
    ))
    if situation:
        with st.expander("Client context"):
            st.write(c.get("notes", ""))
            st.info(f"**Today:** {situation}")
    # Stash the client selection in session_state so the Portfolio page can read it
    if st.button(f"Open {c['name']}'s portfolio →", use_container_width=True):
        st.session_state["active_client_id"] = chosen_id
        st.switch_page("pages/02_Portfolio.py")

disclosure(
    "All clients shown are fictional demo personas. Any resemblance to "
    "real advisors, clients, or portfolios is coincidental. "
    "Past performance does not guarantee future results."
)
