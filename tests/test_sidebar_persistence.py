"""
test_sidebar_persistence.py — DV-1 regression guard.

Verifies that user_level selection survives across multiple renders
of the sidebar, which is what page navigation amounts to in Streamlit:
each page load re-runs the sidebar function with the same session_state.

This test would have caught DV-1 (2026-04-23) where the sidebar was
only rendered on the landing page — the selector appeared to "reset"
on other pages because no widget was rendered there at all.

Run via: pytest tests/test_sidebar_persistence.py
"""
from __future__ import annotations

import pytest


@pytest.fixture
def apptest():
    """Import lazily so unit suite doesn't require streamlit at collection."""
    from streamlit.testing.v1 import AppTest
    return AppTest


def _new_test(apptest_cls, script_path: str):
    at = apptest_cls.from_file(script_path, default_timeout=30)
    return at


def test_user_level_key_canonical(apptest):
    """After landing, session_state['user_level'] is populated."""
    at = _new_test(apptest, "app.py")
    at.run()
    assert "user_level" in at.session_state, (
        "user_level should be set by render_sidebar() on first load"
    )
    from config import DEFAULT_USER_LEVEL, USER_LEVELS
    assert at.session_state["user_level"] in USER_LEVELS
    # Default on first load should be DEFAULT_USER_LEVEL
    assert at.session_state["user_level"] == DEFAULT_USER_LEVEL


def test_user_level_persists_through_explicit_change(apptest):
    """
    Simulate: landing → user picks Client → re-render (page navigation) →
    Client still selected. This mirrors what happens when the user
    navigates from / to /Dashboard.

    2026-04-26 taxonomy: was "Advanced" (3-level taxonomy), now "Client"
    (2-mode taxonomy: Advisor / Client). Same DV-1 contract — the
    user's chosen mode must survive across page navigation.
    """
    at = _new_test(apptest, "app.py")
    at.run()

    # Force the selection to Client via session_state (mirrors what the
    # mode pills would write when wired)
    at.session_state["user_level"] = "Client"
    at.run()  # re-render, as if navigation happened

    assert at.session_state["user_level"] == "Client", (
        "user_level should persist across sidebar re-renders"
    )


def test_render_sidebar_importable():
    """Quick smoke test that ui.sidebar exports render_sidebar."""
    from ui.sidebar import render_sidebar
    assert callable(render_sidebar)


def test_every_page_calls_render_sidebar():
    """
    Static guard: every file under pages/ must call render_sidebar().
    If a new page is added without the call, this test fails loudly —
    exactly the kind of regression that caused DV-1 in the first place.
    """
    import pathlib
    pages_dir = pathlib.Path(__file__).resolve().parent.parent / "pages"
    offenders = []
    for page in pages_dir.glob("*.py"):
        if page.name.startswith("_"):
            continue  # skip __init__.py and private
        content = page.read_text(encoding="utf-8")
        if "render_sidebar()" not in content:
            offenders.append(page.name)
        if "from ui.sidebar import render_sidebar" not in content:
            offenders.append(f"{page.name} (missing import)")
    assert not offenders, (
        "Every page under pages/ must `from ui.sidebar import render_sidebar` "
        f"and call render_sidebar(). Missing in: {offenders}"
    )
