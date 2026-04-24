"""
Settings — feature flags readback, broker routing, monitoring preferences,
per-client auto-execute toggles, scanner health indicator.

Scanner health comes from core.etf_universe.get_scanner_health() which
reads data/scanner_health.json (written atomically on every successful
daily_scanner run, per Day-3 item B).
"""
from __future__ import annotations

import streamlit as st

from config import (
    BRAND_NAME,
    BROKER_PROVIDER,
    DEMO_MODE,
    EDGAR_CONTACT_EMAIL,
    EXTENDED_MODULES_ENABLED,
    ETF_PRICE_SOURCE,
    ETF_REFERENCE_SOURCE,
)
from core.audit_log import recent_entries
from core.data_source_state import snapshot as dss_snapshot
from core.demo_clients import DEMO_CLIENTS
from core.etf_universe import SCANNER_STALE_HOURS, get_scanner_health
from integrations.data_feeds import circuit_breaker_state, reset_circuit_breaker
from ui.components import card, data_sources_panel, section_header
from ui.level_helpers import level_text
from ui.sidebar import render_sidebar
from ui.theme import apply_theme


st.set_page_config(page_title=f"Settings — {BRAND_NAME}", layout="wide")
apply_theme()
render_sidebar()

try:
    from ui import render_top_bar as _ds_top_bar, page_header as _ds_page_header
    _ds_top_bar(breadcrumb=("Account", "Settings"),
                user_level=st.session_state.get("user_level", "beginner"))
    _ds_page_header(
        title="Settings",
        subtitle=level_text(
            beginner="Control how the app fetches data, routes orders, and monitors your portfolios.",
            intermediate="Broker routing, monitoring cadence, auto-execute permissions, scanner health.",
            advanced="Runtime flags + data-source state + circuit-breaker controls + per-client overrides.",
        ),
    )
except Exception:
    section_header(
        "Settings",
        level_text(
            beginner="Control how the app fetches data, routes orders, and monitors your portfolios.",
            intermediate="Broker routing, monitoring cadence, auto-execute permissions, scanner health.",
            advanced="Runtime flags + data-source state + circuit-breaker controls + per-client overrides.",
        ),
    )

# Top-of-page data-source audit panel (Option 3 transparency).
# Settings opens it expanded since this is the operator-diagnostic page.
data_sources_panel(expanded=True, key="ds_panel_settings")


# ═══════════════════════════════════════════════════════════════════════════
# Preferences — FA-facing settings
# ═══════════════════════════════════════════════════════════════════════════
st.markdown("## Preferences")

with card("Broker routing"):
    options = ["mock", "alpaca_paper", "alpaca"]
    current_idx = options.index(BROKER_PROVIDER) if BROKER_PROVIDER in options else 0
    selected = st.selectbox(
        "Provider",
        options=options,
        index=current_idx,
        help="Changing this here is session-only. To change persistently, edit config.py.",
    )
    if selected != BROKER_PROVIDER:
        st.info(
            "Session-level override only. `alpaca_paper` and `alpaca` are "
            "not yet wired — Day-4+ work. `mock` is the functional option today."
        )


# ═══════════════════════════════════════════════════════════════════════════
# Per-client auto-execute permissions
# ═══════════════════════════════════════════════════════════════════════════

with card("Per-client auto-execute permissions"):
    st.caption(level_text(
        beginner="Turn on auto-execute to let the app rebalance a client's basket on its own within their risk limits.",
        intermediate="Auto-execute runs the next rebalance without manual confirmation, within tier limits.",
        advanced="Per-client discretionary flag. Only honored when BROKER_PROVIDER is non-mock.",
    ))
    for c in DEMO_CLIENTS:
        key = f"auto_exec_{c['id']}"
        st.checkbox(
            f"{c['name']} — auto-execute rebalances",
            value=False,
            key=key,
            help=f"Tier: {c['assigned_tier']}",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Extended modules toggle (demo-session override)
# ═══════════════════════════════════════════════════════════════════════════

with card("Extended modules (Framing A / B demo toggle)"):
    st.caption(
        "Toggle on to preview the RWA + DeFi tabs Day-4 will wire up. "
        "Session-only override — doesn't change config.py."
    )
    toggled = st.toggle(
        "Extended modules enabled",
        value=st.session_state.get("extended_modules_override", EXTENDED_MODULES_ENABLED),
        key="extended_modules_toggle",
    )
    st.session_state["extended_modules_override"] = toggled


# Compliance filter — partner + Claude Design feedback 2026-04-22.
# Default ON. Blocks leveraged crypto ETFs + single-stock covered-call
# wrappers (MSTY/CONY/MSFO/COII/MARO) from tier allocations — many RIA
# compliance departments prohibit these product classes with retail
# clients. FA turns OFF explicitly when advising a client (family
# office, qualified-purchaser, or IPS-specified) who can hold them.
with card("Fiduciary-appropriate instrument filter"):
    st.caption(level_text(
        beginner=(
            "When ON (default), the platform hides leveraged crypto ETFs "
            "and single-stock income wrappers (MSTY, CONY, MSFO) from "
            "tier allocations. Most RIA compliance departments prohibit "
            "these with retail clients. Turn OFF only when advising a "
            "client whose IPS explicitly permits aggressive product classes."
        ),
        intermediate=(
            "ON blocks categories: leveraged. Blocked tickers: MSTY, CONY, "
            "MSFO, COII, MARO. Weight redistributes proportionally to "
            "remaining categories. OFF shows the full universe."
        ),
        advanced=(
            "core.risk_tiers.COMPLIANCE_RESTRICTED_CATEGORIES + "
            "COMPLIANCE_RESTRICTED_TICKERS. Threaded through "
            "build_portfolio(compliance_filter_on=) and "
            "_select_etfs_for_category(compliance_filter_on=)."
        ),
    ))
    compliance_on = st.toggle(
        "Restrict to fiduciary-appropriate instruments",
        value=st.session_state.get("compliance_filter_on", True),
        key="compliance_filter_toggle",
        help="Default ON. Hides leveraged + single-stock covered-call "
             "wrappers from tier allocations. Turn off when advising "
             "a client whose IPS explicitly permits them.",
    )
    st.session_state["compliance_filter_on"] = compliance_on


# ═══════════════════════════════════════════════════════════════════════════
# Diagnostics — operator-facing runtime state
# ═══════════════════════════════════════════════════════════════════════════
st.markdown("## Diagnostics")

with card("EDGAR scanner health"):
    health = get_scanner_health()
    if health["last_success_ts"] is None:
        st.warning(
            "Scanner has never run in this environment. "
            "Production cron runs daily at 17:00 UTC via GitHub Actions "
            "(.github/workflows/daily_scanner.yml). Use the button below "
            "to run it on demand."
        )
    else:
        age = health["age_hours"]
        if health["is_stale"]:
            st.warning(
                f"⚠ Last successful scan: {age:.1f} hours ago "
                f"(threshold: {SCANNER_STALE_HOURS}h). Investigate if this persists."
            )
        else:
            st.success(
                f"Last successful scan: {age:.1f} hours ago · "
                f"{health['n_matches']} filings matched."
            )
        if health["keywords_queried"]:
            with st.expander("Last-run details"):
                st.code({
                    "last_success_iso":  health["last_success_iso"],
                    "n_matches":         health["n_matches"],
                    "keywords_queried":  health["keywords_queried"],
                    "forms_queried":     health["forms_queried"],
                    "age_hours":         age,
                    "is_stale":          health["is_stale"],
                }, language=None)

    if EDGAR_CONTACT_EMAIL.startswith("REPLACE_BEFORE_DEPLOY"):
        st.info(
            "EDGAR_CONTACT_EMAIL is still the placeholder — scanner will "
            "refuse to run. Set EDGAR_CONTACT_EMAIL in .env locally or "
            "in Streamlit Cloud Secrets + GitHub Actions Secrets for prod."
        )
    elif st.button("Run scanner now", width="content"):
        with st.spinner("Querying EDGAR full-text index…"):
            try:
                from core.etf_universe import daily_scanner
                matches = daily_scanner(days_back=3)
                st.success(f"Scan complete — {len(matches)} unique filings matched.")
            except RuntimeError as exc:
                st.error(f"Scanner refused to run: {exc}")
            except Exception as exc:
                st.warning(f"Scanner errored (will retry on next cron): {exc}")


# ═══════════════════════════════════════════════════════════════════════════
# Circuit breaker + data-source state
# ═══════════════════════════════════════════════════════════════════════════

with card("Data-source state"):
    cb = circuit_breaker_state()
    s1, s2, s3 = st.columns(3)
    with s1:
        st.metric("yfinance CB", cb["active_source"])
    with s2:
        st.metric("Failures in window", cb["failure_count"])
    with s3:
        st.metric("New-ETF misses", cb["new_etf_misses"])
    if st.button("Reset circuit breaker", width="content"):
        reset_circuit_breaker()
        st.toast("Circuit breaker reset to primary source.")

    st.caption("Per-category data-source state (from this session):")
    snap = dss_snapshot()
    if snap:
        st.dataframe([
            {"Category": cat, **info} for cat, info in snap.items()
        ], width="stretch", hide_index=True)
    else:
        st.caption("No categories touched yet this session.")


# ═══════════════════════════════════════════════════════════════════════════
# Recent actions (audit log)
# ═══════════════════════════════════════════════════════════════════════════

with card("Recent actions"):
    st.caption(level_text(
        beginner="Everything the advisor or the app has done on your clients' accounts recently.",
        intermediate="Session + historical actions. Last 50 shown.",
        advanced="Ring-buffered at 200 entries, oldest-first trim, atomic writes.",
    ))
    entries = recent_entries(limit=50)
    if entries:
        import pandas as _pd
        df = _pd.DataFrame([
            {
                "When":    e.get("iso", "")[:19].replace("T", " "),
                "User":    e.get("user", ""),
                "Client":  e.get("client", ""),
                "Action":  e.get("action", ""),
                "Detail":  e.get("detail", ""),
            }
            for e in entries
        ])
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.caption("No actions recorded yet this session.")


# ═══════════════════════════════════════════════════════════════════════════
# Runtime flag readback
# ═══════════════════════════════════════════════════════════════════════════

with card("Runtime flags (read-only — change in config.py)"):
    st.write({
        "EXTENDED_MODULES_ENABLED": EXTENDED_MODULES_ENABLED,
        "DEMO_MODE":                DEMO_MODE,
        "BROKER_PROVIDER":          BROKER_PROVIDER,
        "ETF_PRICE_SOURCE":         ETF_PRICE_SOURCE,
        "ETF_REFERENCE_SOURCE":     ETF_REFERENCE_SOURCE,
        "EDGAR_CONTACT_EMAIL":      (
            "<placeholder — set in .env>"
            if EDGAR_CONTACT_EMAIL.startswith("REPLACE_BEFORE_DEPLOY")
            else "configured"
        ),
    })
