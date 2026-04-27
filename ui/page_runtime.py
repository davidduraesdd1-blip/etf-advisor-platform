"""
ui/page_runtime.py — import-time hardening for Streamlit page files.

Cowork audit-round-1 finding #1: page files execute Streamlit calls at
top-level (e.g., `st.set_page_config(...)`, `apply_theme()`,
`render_sidebar()`). When something tries to *import* the page (e.g.,
`importlib.import_module("pages.01_Dashboard")` from a test or hot-
reload retry), those calls fire outside a real Streamlit context and
either crash or pollute global state.

Two helpers here:

1. ``safe_streamlit_import()``
   Imports streamlit with one ModuleNotFoundError retry that runs
   ``importlib.invalidate_caches()`` first. Streamlit's own hot-
   reload tears down sys.modules during code edits; if a partial
   teardown is in progress when the page next runs, an unrelated
   sub-import can ModuleNotFoundError. The retry handles that
   one-shot case.

2. ``run_as_page(main_fn)``
   Idiom used at the bottom of every page file:

       if __name__ == "__main__":
           main()
       elif __name__ == "__page__":
           main()

   wrapped into a single call. Streamlit's script runner sets
   ``__name__ = "__main__"`` so the page renders correctly when
   navigated to. ``importlib.import_module(...)`` does NOT set
   ``__main__`` so the body never executes during a test import.

Adopting this idiom is what makes the new ``test_import_hot_reload``
suite possible: the suite imports each page file 10× via importlib
and asserts no exceptions. Without the wrap, the very first import
crashes on ``st.set_page_config`` (no script-run-context).
"""
from __future__ import annotations

import importlib
import sys
from typing import Callable


def safe_streamlit_import():
    """Import streamlit with one ModuleNotFoundError retry."""
    try:
        import streamlit as st  # noqa: F401
        return st
    except ModuleNotFoundError:
        importlib.invalidate_caches()
        # Drop any partial streamlit submodule entries so the retry
        # doesn't pick up a half-torn-down state from sys.modules.
        for k in [k for k in sys.modules if k == "streamlit" or k.startswith("streamlit.")]:
            sys.modules.pop(k, None)
        import streamlit as st
        return st


def run_as_page(main_fn: Callable[[], None]) -> None:
    """
    Execute ``main_fn`` only when the file is run as a Streamlit page
    (``__name__ == "__main__"``). Importing the file via importlib
    skips the call so the page body never fires outside a real
    Streamlit context.

    Convention: every page file ends with::

        if __name__ == "__main__":
            main()

    Equivalent to calling ``run_as_page(main)`` from the caller's
    module scope; the explicit ``if __name__`` block is preferred for
    visibility.
    """
    if main_fn.__module__ == "__main__":
        main_fn()
