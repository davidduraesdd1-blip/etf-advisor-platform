"""
Shared UI primitives. Every page composes from these so visual rhythm stays
consistent.

CLAUDE.md governance: Section 8 (cards, signal badges, tap targets).
"""
from __future__ import annotations

from typing import Literal

import streamlit as st


def section_header(title: str, subtitle: str | None = None) -> None:
    """Left-accent-stripe header used on every page section."""
    st.markdown(f"### {title}")
    if subtitle:
        st.caption(subtitle)


def card(title: str | None = None) -> st.delta_generator.DeltaGenerator:
    """
    Open a themed card container.

        with card("Client overview"):
            st.write("...")
    """
    container = st.container(border=True)
    if title:
        container.markdown(f"#### {title}")
    return container


def signal_badge(signal: Literal["BUY", "HOLD", "SELL"]) -> None:
    """Shape + color-encoded signal per CLAUDE.md §8 (color-blind safe)."""
    shape = {"BUY": "▲", "HOLD": "■", "SELL": "▼"}[signal]
    cls = {"BUY": "eap-signal eap-signal-buy",
           "HOLD": "eap-signal eap-signal-hold",
           "SELL": "eap-signal eap-signal-sell"}[signal]
    st.markdown(
        f"<span class='{cls}'>{shape} {signal}</span>",
        unsafe_allow_html=True,
    )


def kpi_tile(label: str, value: str, delta: str | None = None) -> None:
    """Streamlit's metric primitive with the label visually de-emphasized."""
    st.metric(label=label, value=value, delta=delta)


def disclosure(text: str) -> None:
    """Amber-bordered disclosure banner — used for compliance text."""
    st.markdown(
        f"<div class='eap-disclosure'>{text}</div>",
        unsafe_allow_html=True,
    )


def coming_soon(page_name: str) -> None:
    """Default render for placeholder pages during Day 1 scaffold."""
    with st.container(border=True):
        st.markdown(f"### Coming soon — {page_name}")
        st.caption(
            "Scaffold in place. Wiring to data + math layers lands on Day 2 and Day 3."
        )


# ═══════════════════════════════════════════════════════════════════════════
# Fallback transparency primitive (Day-3 first-class UX requirement)
# ═══════════════════════════════════════════════════════════════════════════

_SOURCE_LABEL = {
    "yfinance":      "Yahoo Finance",
    "stooq":         "Stooq (delayed ~15min)",
    "alphavantage":  "Alpha Vantage",
    "edgar":         "SEC EDGAR",
    "fred":          "FRED",
    "cache":         "cached data",
    "static":        "static fallback",
    "none":          "no source",
}


def data_source_badge(
    category: str,
    state: "str | None" = None,
    source: "str | None" = None,
    age_minutes: "int | None" = None,
) -> None:
    """
    Render the fallback-transparency badge for a data category.

    STATE 1 (LIVE)          — renders nothing.
    STATE 2 (FALLBACK_LIVE) — small amber-dot badge + source name.
    STATE 3 (CACHED)        — amber banner with age + Retry button.
    STATIC                  — footnote-style annotation.

    Normally called without arguments beyond `category` — the current
    state, source, and age are read live from core.data_source_state.
    Explicit args are accepted for testing / UI previews.
    """
    import html as _html

    from core.data_source_state import (
        DataSourceState,
        get_age_minutes,
        get_source,
        get_state,
        reset_all,
    )

    resolved_state = state if state is not None else get_state(category).value
    resolved_source = source if source is not None else get_source(category)
    resolved_age = age_minutes if age_minutes is not None else get_age_minutes(category)

    # STATE 1 — nothing to show.
    if resolved_state in (DataSourceState.LIVE.value, DataSourceState.UNKNOWN.value):
        return

    # STATE 2 — secondary/tertiary live source active.
    if resolved_state == DataSourceState.FALLBACK_LIVE.value:
        pretty = _SOURCE_LABEL.get(resolved_source, resolved_source or "alternate")
        # HTML-escape the source name before injecting into markdown with
        # unsafe_allow_html=True. All call sites today pass hardcoded
        # source strings, so this is hygiene rather than a live XSS
        # vector — but future callers (e.g., scanner passing a filer
        # display_name that originated from an EDGAR response) would
        # be a vector without this.
        pretty_html = _html.escape(str(pretty))
        st.markdown(
            f"<span class='eap-dss-badge eap-dss-fallback' "
            f"title='Primary source unavailable — serving from {pretty_html}.'>"
            f"● Source: {pretty_html}</span>",
            unsafe_allow_html=True,
        )
        return

    # STATIC — footnote-style annotation (e.g., risk-free-rate fallback).
    if resolved_state == DataSourceState.STATIC.value:
        pretty = _SOURCE_LABEL.get(resolved_source, "static estimate")
        pretty_html = _html.escape(str(pretty))
        st.markdown(
            f"<span class='eap-dss-footnote'>"
            f"¹ Using {pretty_html} — primary live source temporarily unavailable.</span>",
            unsafe_allow_html=True,
        )
        return

    # STATE 3 — cached last-known data. Prominent banner + retry.
    age_label = f"{resolved_age} min ago" if resolved_age is not None else "unknown age"
    banner_key = f"eap_dss_retry_{category}"
    col_msg, col_btn = st.columns([4, 1])
    with col_msg:
        st.markdown(
            f"<div class='eap-dss-banner'>"
            f"⚠ Last updated {age_label} — live data temporarily unavailable."
            f"</div>",
            unsafe_allow_html=True,
        )
    with col_btn:
        if st.button("Retry live fetch", key=banner_key, use_container_width=True):
            reset_all()
            st.toast("Live fetch retry queued — refreshing caches.")


def tier_pill_selector(options: list[str], default_index: int = 2,
                        key: str = "tier_pill") -> str:
    """Horizontal radio styled as pills — used for the 5-tier selector."""
    return st.radio(
        "Risk tier",
        options=options,
        index=default_index,
        horizontal=True,
        key=key,
    )


def safe_page_link(page: str, label: str, icon: str | None = None) -> None:
    """
    Render an st.page_link, tolerating Streamlit AppTest's missing
    page-registry context. AppTest raises `KeyError: 'url_pathname'`
    because pages sibling to the one under test aren't registered.
    In a real browser the link renders normally.
    """
    try:
        st.page_link(page, label=label, icon=icon)
    except KeyError:
        st.caption(f"{icon or '→'} {label} — `{page}`")
    except Exception:
        st.caption(f"→ {label}")
