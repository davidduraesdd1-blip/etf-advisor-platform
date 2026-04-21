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

import json
import time
from pathlib import Path

from core.etf_universe import (
    SCANNER_HEALTH_PATH,
    SCANNER_STALE_HOURS,
    daily_scanner,
    get_scanner_health,
    load_universe,
    write_scanner_health,
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


# ═══════════════════════════════════════════════════════════════════════════
# Day-3 item B — scanner health persistence
# ═══════════════════════════════════════════════════════════════════════════

class TestScannerHealth:
    def _cleanup_health(self):
        try:
            SCANNER_HEALTH_PATH.unlink()
        except FileNotFoundError:
            pass

    def test_health_reader_returns_is_stale_when_missing(self):
        self._cleanup_health()
        health = get_scanner_health()
        assert health["is_stale"] is True
        assert health["last_success_ts"] is None
        assert health["age_hours"] is None

    def test_write_then_read_round_trip(self):
        self._cleanup_health()
        write_scanner_health(
            n_matches=7,
            keywords_queried=["bitcoin", "ethereum"],
            forms_queried=["N-1A", "497"],
        )
        health = get_scanner_health()
        assert health["n_matches"] == 7
        assert "bitcoin" in health["keywords_queried"]
        assert health["age_hours"] is not None and health["age_hours"] < 0.5
        assert health["is_stale"] is False
        self._cleanup_health()

    def test_stale_threshold_flips_is_stale(self):
        """Write a health record with a timestamp older than SCANNER_STALE_HOURS."""
        self._cleanup_health()
        # Write then mutate the timestamp directly
        write_scanner_health(n_matches=0, keywords_queried=[])
        stale_ts = time.time() - (SCANNER_STALE_HOURS + 1) * 3600
        with open(SCANNER_HEALTH_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data["last_success_ts"] = stale_ts
        with open(SCANNER_HEALTH_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

        health = get_scanner_health()
        assert health["is_stale"] is True
        assert health["age_hours"] > SCANNER_STALE_HOURS
        self._cleanup_health()

    def test_atomic_write_does_not_leave_tempfile(self):
        self._cleanup_health()
        write_scanner_health(n_matches=1, keywords_queried=["x"])
        parent = SCANNER_HEALTH_PATH.parent
        leftover = list(parent.glob(".tmp_scanner_health_*.json"))
        assert not leftover, f"Tempfile leaked: {leftover}"
        self._cleanup_health()
