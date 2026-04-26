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
import os
from pathlib import Path
from typing import Any

import pytest

# Test-harness short-circuit: tells integrations/data_feeds.get_etf_prices
# + get_last_close to skip the yfinance → Stooq fallback chain and return
# the empty/unavailable shape immediately. Without this, page-render
# AppTest cases hit ~80 yfinance calls per page (universe loader) and
# blow past the AppTest timeout. Set BEFORE any import that might
# transitively touch data_feeds at module-load time.
os.environ["DEMO_MODE_NO_FETCH"] = "1"

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


# Test-isolation reset fixture moved to conftest.py so it applies across
# all test files.


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


class TestAffectedMetricsPhrase:
    """
    Option 3 badge helper that composes the "which metric is affected"
    phrase for each fallback message. Exercises the branches directly
    since rendering through Streamlit in unit tests is expensive.
    """

    def test_explicit_consumer_label_wins_over_registry(self):
        from ui.components import _affected_metrics_phrase
        result = _affected_metrics_phrase(
            "risk_free_rate", consumer_label="Sharpe ratio"
        )
        assert result == "Sharpe ratio"

    def test_registry_lookup_single_metric(self):
        from ui.components import _affected_metrics_phrase
        # etf_composition has exactly one entry in METRIC_DEPENDENCIES
        result = _affected_metrics_phrase("etf_composition", None)
        assert "Composition" in result

    def test_registry_lookup_two_metrics_uses_and(self):
        """Two-item lists render with ' and ' between them."""
        import ui.components as c
        # Patch METRIC_DEPENDENCIES view to have exactly 2 entries
        # for one category; verify the phrase joins them with "and"
        orig_fn = c._affected_metrics_phrase
        # Inspect by calling affected_metrics — etf_long_run has 1 entry;
        # let's use a synthetic case via monkey-inject.
        from core import data_source_state as dss
        dss.METRIC_DEPENDENCIES["_test_two"] = ["Alpha", "Beta"]
        try:
            result = orig_fn("_test_two", None)
            assert result == "Alpha and Beta"
        finally:
            del dss.METRIC_DEPENDENCIES["_test_two"]

    def test_registry_lookup_many_metrics_uses_and_count(self):
        from ui.components import _affected_metrics_phrase
        # etf_price has 6 registered metrics — expect first 2 + "others"
        result = _affected_metrics_phrase("etf_price", None)
        assert "and" in result
        assert "other" in result.lower()

    def test_unknown_category_falls_back_to_this_panel(self):
        from ui.components import _affected_metrics_phrase
        result = _affected_metrics_phrase("nonexistent_category", None)
        assert result == "this panel"


class TestDataSourcesPanelRenders:
    """
    Smoke-level check: the panel component must import and execute
    without raising for a default call. Full visual rendering is
    covered by the per-page AppTest run below.
    """

    def test_panel_is_importable(self):
        from ui.components import data_sources_panel
        assert callable(data_sources_panel)


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
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_page_runs_in_both_themes(page: str, theme: str) -> None:
    """
    Smoke test: every page must render without raising in both dark and
    light mode.

    2026-04-26: this is the automated half of the deferred light-mode
    walk. It catches obvious breakage (template-string crashes, divisor-
    by-zero on light-mode token math, etc.) but is NOT a substitute for
    a human visual walk. A clean run here just means no exceptions —
    not that the contrast / typography / spacing look right.
    """
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(REPO_ROOT / page), default_timeout=30)
    at.session_state["theme"] = theme
    at.run()
    assert not at.exception, f"{page} ({theme} mode) raised: {at.exception}"


@pytest.mark.parametrize(
    "page",
    [
        "pages/01_Dashboard.py",
        "pages/02_Portfolio.py",
        "pages/03_ETF_Detail.py",
        "pages/99_Settings.py",
    ],
)
@pytest.mark.parametrize("user_mode", ["Advisor", "Client"])
def test_page_runs_in_both_user_modes(page: str, user_mode: str) -> None:
    """
    Smoke test: every page must render without raising in both Advisor
    and Client modes (the 2026-04-26 taxonomy collapse).

    Catches level_text() arg-name regressions, is_advisor() / is_client()
    typo regressions, and any conditional branch that only one mode
    exercises.
    """
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(REPO_ROOT / page), default_timeout=30)
    at.session_state["user_level"] = user_mode
    at.run()
    assert not at.exception, f"{page} ({user_mode} mode) raised: {at.exception}"


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

    # default_timeout=30: page renders with DEMO_MODE_NO_FETCH=1 short-circuit
    # data fetches, but Streamlit AppTest still does full DOM build + widget
    # tree walk. Empirically 5-12s on a cold worker; 30s gives headroom for
    # CI variance (Settings.py used to pass at 10s but the others didn't —
    # bumping uniformly so the suite is consistent).
    at = AppTest.from_file(str(REPO_ROOT / page), default_timeout=30)
    at.run()
    assert not at.exception, f"{page} raised: {at.exception}"


def test_etf_detail_selectbox_change_produces_different_values() -> None:
    """
    Regression guard for the "numbers don't change when I pick a
    different ETF" concern. After selecting a non-default ticker, at
    least one of the fund-specific fields on the page (historical
    return text, volatility text, etc.) must differ from the default
    render. Confirms Streamlit's rerun correctly propagates the
    selectbox change through every downstream calculation.
    """
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    # First render — default ticker (usually index 0 of the universe)
    at_default = AppTest.from_file(
        str(REPO_ROOT / "pages/03_ETF_Detail.py"),
        default_timeout=15,
    )
    at_default.run()
    assert not at_default.exception, f"default render raised: {at_default.exception}"

    # Text content on the default render (captions + markdown blocks)
    default_blob = " ".join(
        (el.value or "") if hasattr(el, "value") else str(el)
        for el in list(at_default.markdown) + list(at_default.caption)
    )

    # Second render — swap selectbox to a different ticker (index 1).
    at_changed = AppTest.from_file(
        str(REPO_ROOT / "pages/03_ETF_Detail.py"),
        default_timeout=15,
    )
    at_changed.run()
    # Streamlit AppTest exposes the first selectbox via .selectbox[0]
    # For ETF Detail the first selectbox is the Ticker picker.
    if len(at_changed.selectbox) > 0:
        sb = at_changed.selectbox[0]
        options = list(sb.options or [])
        if len(options) >= 2:
            sb.set_value(options[1]).run()

    changed_blob = " ".join(
        (el.value or "") if hasattr(el, "value") else str(el)
        for el in list(at_changed.markdown) + list(at_changed.caption)
    )

    # Something about the rendered output MUST differ when a different
    # ticker is selected — either the ticker name in headers, the
    # category caption, or individual per-fund numbers.
    assert default_blob != changed_blob, (
        "ETF Detail rendered identical content for two different "
        "selectbox values — the selectbox change is not flowing through."
    )
