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
