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

_df.get_etf_prices = _empty_prices
_pe.get_live_risk_free_rate = _fake_rfr

# Only stub MC for Streamlit smoke tests, NOT for the deterministic
# portfolio-engine tests that validate actual math. We detect via a
# marker in the stack or, simpler: only stub in smoke when explicitly opted
# in. For now, rely on each test file controlling its own MC mock.
# (We intentionally do NOT globally stub run_monte_carlo.)


# Also ensure EDGAR_CONTACT_EMAIL default-placeholder doesn't accidentally
# get exercised during any test that tries to run the live scanner.
os.environ.setdefault("EDGAR_CONTACT_EMAIL", "ops@test.example")
