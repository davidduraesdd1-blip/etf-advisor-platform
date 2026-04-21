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
    container = st.container()
    with container:
        st.markdown("<div class='eap-card'>", unsafe_allow_html=True)
        if title:
            st.markdown(f"#### {title}")
    # Streamlit will close the container on `with` exit; CSS relies on the
    # wrapper div which stays open until the next markdown block. For the
    # demo scaffold that's acceptable; on Day 3 we revisit if visuals slip.
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
    st.markdown("<div class='eap-card'>", unsafe_allow_html=True)
    st.markdown(f"### Coming soon — {page_name}")
    st.caption(
        "Scaffold in place. Wiring to data + math layers lands on Day 2 and Day 3."
    )
    st.markdown("</div>", unsafe_allow_html=True)
