"""
test_import_hot_reload.py — Cowork audit-round-1 commit 5 (test #1).

Regression suite for the import-time hardening on every page file:
``app.py`` + ``pages/01_Dashboard.py`` + ``pages/02_Portfolio.py`` +
``pages/03_ETF_Detail.py`` + ``pages/99_Settings.py``.

Each page MUST be safe to import via ``importlib.import_module`` /
``runpy``-style mechanisms WITHOUT a Streamlit script-runner context.
The wrap (``def main(): ...; if __name__ == "__main__": main()``) makes
that hold; this suite asserts it stays held by importing each page 10×
and never expecting a ``RuntimeError``, ``KeyError("url_pathname")``,
``StreamlitAPIException``, or any other Streamlit-context-required
exception type.

CLAUDE.md governance: §4 (audit protocol), §22 (test coverage of demo
features), §23 (token efficiency — fast suite).
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    "app",
    "pages.01_Dashboard",
    "pages.02_Portfolio",
    "pages.03_ETF_Detail",
    "pages.99_Settings",
]


def _import_by_path(rel_path: str) -> None:
    """
    Page modules have leading-digit names (`01_Dashboard`) that aren't
    valid Python identifiers; importlib.import_module can't load them
    as `pages.01_Dashboard`. Use the file-spec route instead.
    """
    full = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(
        f"_hotreload_{full.stem}", full,
    )
    assert spec and spec.loader, f"could not build spec for {full}"
    mod = importlib.util.module_from_spec(spec)
    # Drop any prior copy so the loader actually re-executes the body.
    sys.modules.pop(spec.name, None)
    spec.loader.exec_module(mod)


# DEMO_MODE_NO_FETCH=1 ensures no network calls during the import loop.
@pytest.fixture(autouse=True)
def _demo_mode():
    prev = os.environ.get("DEMO_MODE_NO_FETCH")
    os.environ["DEMO_MODE_NO_FETCH"] = "1"
    yield
    if prev is None:
        os.environ.pop("DEMO_MODE_NO_FETCH", None)
    else:
        os.environ["DEMO_MODE_NO_FETCH"] = prev


@pytest.mark.parametrize(
    "page_path",
    [
        "app.py",
        "pages/01_Dashboard.py",
        "pages/02_Portfolio.py",
        "pages/03_ETF_Detail.py",
        "pages/99_Settings.py",
    ],
)
def test_page_imports_clean(page_path: str) -> None:
    """
    A single import must not crash. Regression for the prior state
    where importing a page triggered ``st.set_page_config`` outside a
    Streamlit context.
    """
    _import_by_path(page_path)


@pytest.mark.parametrize(
    "page_path",
    [
        "app.py",
        "pages/01_Dashboard.py",
        "pages/02_Portfolio.py",
        "pages/03_ETF_Detail.py",
        "pages/99_Settings.py",
    ],
)
def test_page_repeat_import_loop(page_path: str) -> None:
    """
    10× import loop. Catches state-leak regressions (module-level cache
    accumulators, sys.modules pollution, dataclass __post_init__ side
    effects). Cowork's audit P0 #2 lives here — the dataclass guard in
    core/data_source_state.py must hold under repeated reload.
    """
    for _ in range(10):
        _import_by_path(page_path)


def test_data_source_state_50x_reload() -> None:
    """
    50× reload of core.data_source_state. Asserts the @dataclass
    decorator + Optional→`X | None` syntax change keep the dataclass
    well-formed across hot-reload cycles.
    """
    import core.data_source_state as dss
    for _ in range(50):
        importlib.reload(dss)
    info = dss._CategoryInfo()
    assert info.state == dss.DataSourceState.UNKNOWN
    assert info.cache_age_seconds_at_mark is None


def test_page_runtime_safe_streamlit_import() -> None:
    """
    The audit-round-1 helper module ui/page_runtime.py must successfully
    return the streamlit module on a normal call.
    """
    from ui.page_runtime import safe_streamlit_import
    st = safe_streamlit_import()
    assert st is not None
    assert hasattr(st, "session_state")
