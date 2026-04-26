"""
level_helpers.py — centralize Advisor / Client copy variants.

Every page that shows user-facing copy should route through `level_text`
so the two-mode taxonomy is one-place-to-fix instead of scattered
across pages.

2026-04-26 taxonomy collapse: Beginner / Intermediate / Advanced → Advisor / Client.
  Advisor — full data, jargon, raw indicator names. Default.
  Client  — plain English, simpler charts, no jargon, prominent
            hypothetical-results disclaimers. Screen-share-with-client mode.

Same data both modes; different presentation layer only.

CLAUDE.md §7.
"""
from __future__ import annotations

import streamlit as st

from config import DEFAULT_USER_LEVEL, USER_LEVELS


def current_level() -> str:
    """Return the user's mode from session_state, defaulting sensibly."""
    lvl = st.session_state.get("user_level", DEFAULT_USER_LEVEL)
    return lvl if lvl in USER_LEVELS else DEFAULT_USER_LEVEL


def level_text(advisor: str, client: str) -> str:
    """
    Return the copy appropriate for the current mode.
    Use anywhere you'd normally write a literal string.

        st.caption(level_text(
            advisor="Sharpe (3Y, FRED-live rf, daily log returns)",
            client="Risk-adjusted return — higher is better.",
        ))
    """
    return client if current_level() == "Client" else advisor


def is_advisor() -> bool:
    """True when the current mode is Advisor (the full-data default)."""
    return current_level() == "Advisor"


def is_client() -> bool:
    """True when the current mode is Client (plain-English screen-share)."""
    return current_level() == "Client"


def level_caption(advisor: str, client: str) -> None:
    """Shortcut: st.caption(level_text(...))."""
    st.caption(level_text(advisor, client))
