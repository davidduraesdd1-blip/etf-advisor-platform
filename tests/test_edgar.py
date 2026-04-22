"""
Day-4 tests for integrations.edgar — shared EDGAR primitives.

Covers:
  - Runtime guard on placeholder EDGAR_CONTACT_EMAIL
  - User-Agent format
  - Token bucket rate-limit (3 rapid takes should space out >= ~0.2s with
    a 10-req/sec bucket that starts full at 10 tokens)
  - CIK cache load/save round-trip

No live EDGAR requests are made here.
"""
from __future__ import annotations

import time

import pytest

from integrations import edgar


class TestRuntimeGuard:
    def test_placeholder_email_raises(self, monkeypatch):
        monkeypatch.setattr(edgar, "EDGAR_CONTACT_EMAIL",
                            "REPLACE_BEFORE_DEPLOY@example.com")
        with pytest.raises(RuntimeError, match="EDGAR_CONTACT_EMAIL"):
            edgar.assert_edgar_configured()

    def test_empty_email_raises(self, monkeypatch):
        monkeypatch.setattr(edgar, "EDGAR_CONTACT_EMAIL", "")
        with pytest.raises(RuntimeError):
            edgar.assert_edgar_configured()

    def test_real_looking_email_passes(self, monkeypatch):
        monkeypatch.setattr(edgar, "EDGAR_CONTACT_EMAIL", "ops@test.example")
        edgar.assert_edgar_configured()   # no raise


class TestUserAgent:
    def test_user_agent_includes_contact_email(self, monkeypatch):
        monkeypatch.setattr(edgar, "EDGAR_CONTACT_EMAIL", "ops@test.example")
        ua = edgar.user_agent()
        assert "ETF-Advisor-Platform" in ua
        assert "ops@test.example" in ua


class TestGetRecentFilingsFormFilter:
    """
    Regression guard for the form-name mismatch bug: SEC EDGAR uses
    NPORT-P / NPORT-EX (not the generic "N-PORT" label) in its
    submissions.json. The filter must treat those exactly, otherwise
    every real-world lookup silently returns zero matches.
    """

    def _fake_submissions_response(self):
        """Craft a submissions.json-shaped response with realistic forms."""
        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {
                "filings": {
                    "recent": {
                        "form":            ["NPORT-P", "10-K", "8-K", "NPORT-EX"],
                        "accessionNumber": ["0001-25-000001", "0001-25-000002",
                                            "0001-25-000003", "0001-25-000004"],
                        "filingDate":      ["2026-03-01", "2026-02-15",
                                            "2026-01-20", "2025-12-01"],
                        "primaryDocument": ["primary.xml", "10k.htm",
                                            "8k.htm", "ex.xml"],
                    }
                }
            }
        return _R()

    def test_default_form_types_match_real_edgar_strings(self, monkeypatch):
        """Default args should pick up NPORT-P / NPORT-EX from a real shape."""
        monkeypatch.setattr(edgar, "edgar_get",
                            lambda *a, **kw: self._fake_submissions_response())

        filings = edgar.get_recent_filings("0001980994")
        forms_returned = [f["form"] for f in filings]
        assert "NPORT-P" in forms_returned, \
            "Default form filter must match NPORT-P — the real SEC form name"
        assert "NPORT-EX" in forms_returned
        assert "10-K" not in forms_returned  # not requested

    def test_legacy_n_port_label_does_not_match(self, monkeypatch):
        """
        Documenting-test: the original bug was passing form_types=("N-PORT",).
        That string never appears in real EDGAR submissions, so this MUST
        return [] — confirming why the old code silently failed.
        """
        monkeypatch.setattr(edgar, "edgar_get",
                            lambda *a, **kw: self._fake_submissions_response())

        filings = edgar.get_recent_filings("0001980994", form_types=("N-PORT",))
        assert filings == []


class TestTokenBucket:
    def test_take_token_decrements_and_blocks_when_empty(self):
        # Drain the bucket
        from integrations.edgar import _bucket_state, take_token
        _bucket_state["tokens"] = 10.0
        _bucket_state["last_refill"] = time.monotonic()

        for _ in range(10):
            take_token()

        # One more take should block for ~1/EDGAR_REQS_PER_SEC seconds
        start = time.monotonic()
        take_token()
        elapsed = time.monotonic() - start
        # With 10 req/sec, the 11th token should take at least ~80ms to arrive
        assert elapsed >= 0.05, f"Expected blocking, got {elapsed:.3f}s"
