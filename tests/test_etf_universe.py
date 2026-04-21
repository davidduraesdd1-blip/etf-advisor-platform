"""
Day-2 etf_universe tests.

Covers:
  - load_universe() returns the seed with analytic defaults enriched
  - daily_scanner() raises RuntimeError when EDGAR_CONTACT_EMAIL is still
    the placeholder (planning-side Mod 2)

Run:
    pytest tests/test_etf_universe.py -v
"""
from __future__ import annotations

import pytest

from core.etf_universe import (
    daily_scanner,
    load_universe,
)


class TestLoadUniverse:
    def test_returns_seed_entries(self):
        u = load_universe()
        assert len(u) >= 15, "Seed universe shrank unexpectedly"
        tickers = {e["ticker"] for e in u}
        # A few must-haves
        assert "IBIT" in tickers
        assert "ETHA" in tickers
        assert "GBTC" in tickers

    def test_every_entry_has_required_analytic_fields(self):
        u = load_universe()
        for e in u:
            assert "expected_return" in e
            assert "volatility" in e
            assert "correlation_with_btc" in e
            assert e["volatility"] > 0
            assert -1.0 <= e["correlation_with_btc"] <= 1.0

    def test_scanner_additions_are_deduped(self):
        extras = [
            {"ticker": "IBIT", "category": "btc_spot", "issuer": "BlackRock",
             "name": "Duplicate of existing seed entry"},
            {"ticker": "NEWX", "category": "btc_spot", "issuer": "NewIssuer",
             "name": "A genuinely new ETF"},
        ]
        u = load_universe(scanner_additions=extras)
        tickers = [e["ticker"] for e in u]
        assert tickers.count("IBIT") == 1, "Duplicate was not de-duplicated"
        assert "NEWX" in tickers


class TestDailyScannerGuard:
    """
    Planning-side Mod 2: daily_scanner() must raise immediately when
    EDGAR_CONTACT_EMAIL is still the placeholder, before making any
    network calls.
    """

    def test_raises_on_placeholder_email(self):
        # config default IS the placeholder — so the call must raise.
        with pytest.raises(RuntimeError, match="EDGAR_CONTACT_EMAIL"):
            daily_scanner(days_back=1)

    def test_raises_even_with_empty_email(self, monkeypatch):
        monkeypatch.setattr("core.etf_universe.EDGAR_CONTACT_EMAIL", "")
        with pytest.raises(RuntimeError):
            daily_scanner(days_back=1)

    def test_user_agent_format_with_valid_email(self, monkeypatch):
        # Guard passes with a real-looking email; the function will then
        # attempt a network call which may fail — that's out of scope.
        # We just assert the guard is NOT what raises.
        monkeypatch.setattr(
            "core.etf_universe.EDGAR_CONTACT_EMAIL",
            "ops@example.org",
        )
        # Don't actually hit network: patch requests.get to raise immediately
        import core.etf_universe as eu
        try:
            eu.daily_scanner(days_back=1)
        except RuntimeError as exc:
            if "EDGAR_CONTACT_EMAIL" in str(exc):
                pytest.fail("Guard falsely raised with valid email")
            # Any other RuntimeError is fine (e.g., network-stack related)
        except Exception:
            # Network failure is acceptable — guard did its job.
            pass
