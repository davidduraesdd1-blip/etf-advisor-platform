"""
level_helpers.py — centralize Beginner / Intermediate / Advanced text.

Every page that shows user-facing copy should route through `level_text`
so the three-level scaling is one-place-to-fix instead of scattered
across pages. CLAUDE.md §7.
"""
from __future__ import annotations

import streamlit as st

from config import DEFAULT_USER_LEVEL, USER_LEVELS


def current_level() -> str:
    """Return the user's level from session_state, defaulting sensibly."""
    lvl = st.session_state.get("user_level", DEFAULT_USER_LEVEL)
    return lvl if lvl in USER_LEVELS else DEFAULT_USER_LEVEL


def level_text(beginner: str, intermediate: str, advanced: str) -> str:
    """
    Return the copy appropriate for the current user's level.
    Use anywhere you'd normally write a literal string.
    """
    lvl = current_level()
    if lvl == "Beginner":
        return beginner
    if lvl == "Intermediate":
        return intermediate
    return advanced


def is_beginner() -> bool:
    return current_level() == "Beginner"


def is_intermediate() -> bool:
    return current_level() == "Intermediate"


def is_advanced() -> bool:
    return current_level() == "Advanced"


def level_caption(beginner: str, intermediate: str, advanced: str) -> None:
    """Shortcut: st.caption(level_text(...))."""
    st.caption(level_text(beginner, intermediate, advanced))
