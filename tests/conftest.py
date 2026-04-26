"""
Session-level test setup.

pytest evaluates conftest.py before any test module imports. We use that
window to patch network-hitting helpers so Streamlit AppTest renders
don't hang on yfinance / FRED fetches.

No synthetic data is exposed to the app at runtime — these stubs apply
only inside the test harness.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure repo root is importable for `from core.* import ...`, etc.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Lazy-patch at first use. The core modules are imported here so the
# pages' `from integrations.data_feeds import get_etf_prices` picks up
# the stub rather than the network-hitting original.
def _empty_prices(tickers, period="1y", interval="1d"):
    return {t: {"source": "unavailable", "prices": []} for t in tickers}


def _fake_rfr():
    return 4.25


def _fast_monte_carlo(portfolio, n_simulations=None, horizon_days=365,
                     seed=42, paths_retain=None):
    """Smoke-test stub: skip 10k-path MC; return a minimal shape."""
    holdings = portfolio.get("holdings", [])
    if not holdings:
        return {}
    initial = float(portfolio.get("portfolio_value_usd", 100_000))
    flat_path = [initial] * min(30, horizon_days)
    return {
        "initial_value_usd":     initial,
        "horizon_days":          horizon_days,
        "n_simulations":         10,
        "paths_retained":        3,
        "seed":                  seed,
        "percentile_5":          initial * 0.9,
        "percentile_25":         initial * 0.95,
        "percentile_50":         initial,
        "percentile_75":         initial * 1.05,
        "percentile_95":         initial * 1.1,
        "mean_final_value":      initial,
        "prob_loss_pct":         25.0,
        "prob_10pct_gain_pct":   10.0,
        "avg_max_drawdown_pct":  5.0,
        "sample_paths":          [flat_path, flat_path, flat_path],
        "hist_counts":           [0] * 50,
        "hist_edges":            [initial + i for i in range(51)],
    }


import integrations.data_feeds as _df      # noqa: E402
import core.portfolio_engine as _pe        # noqa: E402
import integrations.edgar_nport as _en     # noqa: E402

_df.get_etf_prices = _empty_prices
_pe.get_live_risk_free_rate = _fake_rfr


def _empty_composition(ticker: str) -> dict:
    from integrations.edgar_nport import SUPPORTED_TICKERS
    tkr = ticker.upper()
    return {
        "ticker":          tkr,
        "supported":       tkr in SUPPORTED_TICKERS,
        "source":          "unavailable",
        "filing_date":     None,
        "accession":       None,
        "holdings":        [],
        "holdings_count":  0,
        "total_value_usd": 0.0,
        "note":            "smoke-test stub"
                           if tkr in SUPPORTED_TICKERS
                           else "Live holdings via SEC EDGAR not wired for this "
                                "ticker yet. Supported in demo scope: IBIT, ETHA, "
                                "FBTC, FETH.",
    }


_en.get_etf_composition = _empty_composition

# Only stub MC for Streamlit smoke tests, NOT for the deterministic
# portfolio-engine tests that validate actual math. We detect via a
# marker in the stack or, simpler: only stub in smoke when explicitly opted
# in. For now, rely on each test file controlling its own MC mock.
# (We intentionally do NOT globally stub run_monte_carlo.)


# Also ensure EDGAR_CONTACT_EMAIL default-placeholder doesn't accidentally
# get exercised during any test that tries to run the live scanner.
os.environ.setdefault("EDGAR_CONTACT_EMAIL", "ops@test.example")


# ═══════════════════════════════════════════════════════════════════════════
# Test isolation — reset module-level state between EVERY test.
# Module caches accumulate across tests and can produce cross-test hangs
# or false positives. Resetting here is zero-cost for tests that don't
# touch these modules.
# ═══════════════════════════════════════════════════════════════════════════

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_module_state_between_tests():
    from integrations.data_feeds import reset_circuit_breaker
    from core.data_source_state import reset_all as reset_dss
    reset_circuit_breaker()
    reset_dss()
    yield


@pytest.fixture(autouse=True)
def _reset_streamlit_session_state():
    """
    Drop every key from st.session_state between tests. Without this,
    a test that writes session_state (e.g. user_level toggle, theme,
    active_client_id) leaks state into the next test and produces order-
    dependent failures.

    Concrete case this guards against:
    test_user_level_persists_through_explicit_change passes in isolation
    but fails when run after the page-render AppTest cases because they
    leave the user_level write in place — the persistence test then sees
    its starting "Beginner" state pre-mutated.

    Streamlit's session_state behaves like a dict; del-on-keys is the
    documented reset path. The try/except absorbs the case where
    streamlit isn't loaded yet (pre-collection import phase, etc.).
    """
    try:
        import streamlit as st
        for key in list(st.session_state.keys()):
            del st.session_state[key]
    except Exception:
        pass
    yield
