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
from core.etf_universe import load_universe_with_live_analytics
from core.portfolio_engine import build_portfolio
from integrations.data_feeds import get_etf_prices
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

# ═══════════════════════════════════════════════════════════════════════════
# Live per-client metrics — Q5. Each client's numbers are derived from
# their assigned tier's basket built against the live-analytics universe
# (return / vol / correlation already live per Q2). The 30-day delta is
# computed on the spot from each holding's last 30 trading-day closes.
# ═══════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=600)
def _live_universe() -> list[dict]:
    return load_universe_with_live_analytics()


@st.cache_data(ttl=600)
def _build_for_tier(tier_name: str, sleeve_usd: float,
                    universe_key: int) -> dict:
    """Build a live-analytics-backed portfolio for a tier + sleeve size."""
    return build_portfolio(tier_name, _live_universe(),
                           portfolio_value_usd=sleeve_usd)


def _basket_30d_return_pct(holdings: list[dict]) -> tuple[float | None, int]:
    """
    Weighted 30-trading-day return of a basket. Reuses cached price
    bundles inside data_feeds so this is nearly free after the universe
    cache is warm. Returns (pct_or_None, n_holdings_with_live_prices).
    """
    total_weight_with_live = 0.0
    weighted_return = 0.0
    n_live = 0
    for h in holdings:
        tkr = h["ticker"]
        bundle = get_etf_prices([tkr], period="90d", interval="1d")
        rows = bundle.get(tkr, {}).get("prices", []) or []
        closes: list[float] = []
        for row in rows:
            try:
                c = float(row.get("close"))
                if c > 0:
                    closes.append(c)
            except (TypeError, ValueError):
                continue
        if len(closes) < 31:
            continue  # insufficient recent history
        ret_30d = (closes[-1] / closes[-31]) - 1.0
        weight = float(h.get("weight_pct", 0)) / 100.0
        weighted_return += weight * ret_30d
        total_weight_with_live += weight
        n_live += 1
    if total_weight_with_live <= 0:
        return (None, 0)
    # Rescale if some holdings lacked data so partial coverage reports
    # "weighted return of the ETFs we could price" honestly.
    return (weighted_return / total_weight_with_live * 100.0, n_live)


with st.spinner("Computing live per-client metrics..."):
    universe_live = _live_universe()
    _uni_key = id(universe_live)

    # De-dup portfolio builds by (tier, sleeve_usd) — 3 demo clients so
    # at most 3 distinct portfolios.
    per_client_metrics: dict[str, dict] = {}
    for c in DEMO_CLIENTS:
        sleeve = c["total_portfolio_usd"] * c["crypto_allocation_pct"] / 100.0
        p = _build_for_tier(c["assigned_tier"], sleeve, _uni_key)
        delta_pct, n_priced = _basket_30d_return_pct(p["holdings"])
        per_client_metrics[c["id"]] = {
            "exp_return":  p["metrics"]["weighted_return_pct"],
            "port_vol":    p["metrics"]["portfolio_volatility_pct"],
            "sleeve_usd":  sleeve,
            "delta_30d":   delta_pct,
            "n_priced":    n_priced,
            "n_holdings":  len(p["holdings"]),
        }

# Aggregate live/fallback counts across the universe for the caption.
_n_total = len(universe_live) or 1
_n_live_ret = sum(1 for e in universe_live
                  if e.get("expected_return_source") == "live")
_n_live_vol = sum(1 for e in universe_live
                  if e.get("volatility_source") == "live")
_n_live_corr = sum(1 for e in universe_live
                   if e.get("correlation_source") in ("live", "self"))

# Build the roster table
df = pd.DataFrame([
    {
        "Client":       f"{c['name']} · DEMO",
        "Label":        c["label"],
        "Age":          c["age"],
        "Tier":         c["assigned_tier"],
        "Portfolio $":  c["total_portfolio_usd"],
        "Crypto %":     c["crypto_allocation_pct"],
        "Exp return":   per_client_metrics[c["id"]]["exp_return"],
        "Port vol":     per_client_metrics[c["id"]]["port_vol"],
        "30d change":   per_client_metrics[c["id"]]["delta_30d"],
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
            "Exp return":   st.column_config.NumberColumn(
                format="%.1f%%",
                help="Annualized expected return of each client's crypto "
                     "sleeve — derived live from the basket's ETF CAGRs.",
            ),
            "Port vol":     st.column_config.NumberColumn(
                format="%.1f%%",
                help="Portfolio volatility — 90-day annualized σ, "
                     "derived live per ETF and aggregated by basket weight.",
            ),
            "30d change":   st.column_config.NumberColumn(
                format="%.2f%%",
                help="Weighted basket return over the last 30 trading days.",
            ),
            "Drift %":      st.column_config.NumberColumn(format="%.1f%%"),
        },
    )
    st.caption(level_text(
        beginner=(
            f"Live metrics — {_n_live_ret} of {_n_total} ETFs report live "
            f"expected returns, {_n_live_vol} of {_n_total} report live "
            f"volatility, {_n_live_corr} of {_n_total} report live BTC "
            f"correlation. Anything that isn't live uses category averages "
            f"as a fallback and is flagged on the ETF detail page."
        ),
        intermediate=(
            f"Live coverage — return: {_n_live_ret}/{_n_total} · "
            f"vol: {_n_live_vol}/{_n_total} · "
            f"corr: {_n_live_corr}/{_n_total}. "
            f"30d change weighted by basket allocation."
        ),
        advanced=(
            f"Live: return={_n_live_ret}/{_n_total}, "
            f"vol={_n_live_vol}/{_n_total}, "
            f"corr={_n_live_corr}/{_n_total}. "
            f"30d delta = Σ(weight × 30trading-day return), rescaled by "
            f"covered weight."
        ),
    ))

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
