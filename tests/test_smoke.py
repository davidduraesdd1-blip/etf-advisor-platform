"""
Day 1 smoke test.

Verifies:
  1. config.py parses and exposes the expected constants with expected types.
  2. ui.theme and ui.components import without touching Streamlit runtime.
  3. Every page file + app.py parses as valid Python.
  4. Streamlit's AppTest runner can execute app.py end-to-end without error.

Run:
    pytest tests/ -v
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Network helpers are stubbed by tests/conftest.py. Monte Carlo is
# stubbed only here (smoke tests) to keep Streamlit AppTest renders
# fast — the real MC math is validated by test_portfolio_engine.py.


@pytest.fixture(autouse=True)
def _fast_mc(monkeypatch):
    from tests.conftest import _fast_monte_carlo
    import core.portfolio_engine as pe
    monkeypatch.setattr(pe, "run_monte_carlo", _fast_monte_carlo)
    yield


def _parses(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path))


def test_config_imports_and_has_expected_shape() -> None:
    cfg = importlib.import_module("config")

    # Flags
    assert isinstance(cfg.EXTENDED_MODULES_ENABLED, bool)
    assert isinstance(cfg.DEMO_MODE, bool)
    assert cfg.BROKER_PROVIDER in {"mock", "alpaca_paper", "alpaca"}

    # Brand
    assert isinstance(cfg.BRAND_NAME, str) and cfg.BRAND_NAME
    assert cfg.BRAND_LOGO_PATH is None or isinstance(cfg.BRAND_LOGO_PATH, str)

    # Colors — all hex strings
    for key in ("primary", "success", "danger", "warning", "dark_bg", "dark_card", "light_bg", "light_card"):
        value = cfg.COLORS[key]
        assert isinstance(value, str) and value.startswith("#")

    # Tiers — exactly 5, ordered by tier_number
    tiers = cfg.PORTFOLIO_TIERS
    assert len(tiers) == 5
    numbers = [t["tier_number"] for t in tiers.values()]
    assert numbers == [1, 2, 3, 4, 5]
    for t in tiers.values():
        assert t["ceiling_pct"] > 0
        assert t["rebalance"] in {"quarterly", "bi-monthly", "monthly", "bi-weekly"}

    # Universe — non-empty seed
    assert len(cfg.ETF_UNIVERSE_SEED) >= 15
    for etf in cfg.ETF_UNIVERSE_SEED:
        assert set(etf.keys()) == {"ticker", "issuer", "category", "name"}

    # Cache TTLs
    for key in ("client_statuses", "etf_price_market", "etf_holdings", "portfolio_output", "empty_result"):
        assert cfg.CACHE_TTL[key] > 0


@pytest.mark.parametrize(
    "relpath",
    [
        "app.py",
        "pages/01_Dashboard.py",
        "pages/02_Portfolio.py",
        "pages/03_ETF_Detail.py",
        "pages/99_Settings.py",
        "ui/theme.py",
        "ui/components.py",
        "config.py",
    ],
)
def test_file_parses(relpath: str) -> None:
    path = REPO_ROOT / relpath
    assert path.exists(), f"missing file: {relpath}"
    _parses(path)


def test_ui_modules_import() -> None:
    # These must not touch the Streamlit runtime at import time.
    importlib.import_module("ui.theme")
    importlib.import_module("ui.components")


def test_app_runs_via_streamlit_apptest() -> None:
    """Full end-to-end render of app.py via Streamlit's test harness."""
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(REPO_ROOT / "app.py"), default_timeout=10)
    at.run()
    assert not at.exception, f"app.py raised during render: {at.exception}"


@pytest.mark.parametrize(
    "page",
    [
        "pages/01_Dashboard.py",
        "pages/02_Portfolio.py",
        "pages/03_ETF_Detail.py",
        "pages/99_Settings.py",
    ],
)
def test_page_runs_via_streamlit_apptest(page: str) -> None:
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(REPO_ROOT / page), default_timeout=10)
    at.run()
    assert not at.exception, f"{page} raised: {at.exception}"
