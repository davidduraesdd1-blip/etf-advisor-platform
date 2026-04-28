"""
Sprint 4 — tests for integrations/alpaca_streaming.

Mocks alpaca-py's TradingStream and writes only to a tmp_path-pointed
cache file so no live network is touched and no repo files are
clobbered.

Coverage targets per Sprint 4 spec (>=10 tests):
  - Module configured iff both env vars set                        (2 tests)
  - register_order_callback fires on simulated event               (1)
  - get_last_status round-trip via disk cache                      (2)
  - Multi-order callback independence                              (1)
  - Reconnect-on-disconnect retries with backoff                   (2)
  - Idempotent start_order_stream                                  (1)
  - Graceful no-op when env unset                                  (1)
"""
from __future__ import annotations

import importlib
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def streaming(tmp_path, monkeypatch):
    """Reload the module fresh per test with the cache pointed at tmp_path
    and ALL alpaca env vars cleared. Tests opt-in to credentials by
    setting them BEFORE calling functions, then re-asserting state."""
    # Clear all candidate env vars.
    for n in (
        "ALPACA_API_KEY_ID", "ALPACA_API_KEY",
        "ALPACA_API_SECRET_KEY", "ALPACA_API_SECRET",
    ):
        monkeypatch.delenv(n, raising=False)
    import integrations.alpaca_streaming as mod
    importlib.reload(mod)
    # Redirect cache to tmp_path.
    mod._CACHE_PATH = tmp_path / "order_status_cache.json"
    mod._DATA_DIR = tmp_path
    # Reset module state in case a prior test left it dirty.
    mod._THREAD = None
    mod._STOP_EVENT = None
    mod._STREAM = None
    mod._CALLBACKS = {}
    mod._LAST_EVENT_AT = None
    mod._STREAM_ERROR = None
    mod._RECONNECT_ATTEMPTS = 0
    yield mod
    # Teardown — make sure no daemon thread leaks across tests.
    try:
        mod.stop_order_stream()
    except Exception:
        pass


# ── 1. is_configured: both env vars must be set ──────────────────────────────

class TestConfigured:
    def test_unconfigured_when_both_missing(self, streaming, monkeypatch):
        for n in ("ALPACA_API_KEY_ID", "ALPACA_API_KEY",
                  "ALPACA_API_SECRET_KEY", "ALPACA_API_SECRET"):
            monkeypatch.delenv(n, raising=False)
        assert streaming.is_configured() is False

    def test_configured_when_both_set(self, streaming, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY_ID", "PK_TEST")
        monkeypatch.setenv("ALPACA_API_SECRET_KEY", "SECRET_TEST")
        assert streaming.is_configured() is True

    def test_unconfigured_when_only_key_set(self, streaming, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY_ID", "PK_TEST")
        monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
        monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
        assert streaming.is_configured() is False

    def test_configured_via_legacy_env_names(self, streaming, monkeypatch):
        # Existing config.py uses ALPACA_API_KEY + ALPACA_API_SECRET. Both
        # legacy names must keep working so existing Streamlit Cloud
        # Secrets don't need re-keying.
        monkeypatch.setenv("ALPACA_API_KEY", "PK_LEGACY")
        monkeypatch.setenv("ALPACA_API_SECRET", "SECRET_LEGACY")
        assert streaming.is_configured() is True


# ── 2. register_order_callback fires on event ────────────────────────────────

class TestCallbackDispatch:
    def test_callback_fires_on_event(self, streaming):
        seen: list[dict] = []
        streaming.register_order_callback("client_order_abc", seen.append)
        streaming._test_inject_event({
            "client_order_id": "client_order_abc",
            "event":           "fill",
            "symbol":          "IBIT",
            "side":            "buy",
            "qty":             10,
            "price":           65.42,
        })
        assert len(seen) == 1
        assert seen[0]["status"] == "fill"
        assert seen[0]["fill_qty"] == 10
        assert seen[0]["fill_price"] == 65.42


# ── 3. get_last_status round-trip via disk cache ─────────────────────────────

class TestDiskRoundTrip:
    def test_status_persists_to_disk(self, streaming):
        streaming._test_inject_event({
            "client_order_id": "coid_persist_1",
            "event":           "accepted",
            "symbol":          "FBTC",
        })
        # File created
        assert streaming._CACHE_PATH.exists()
        last = streaming.get_last_status("coid_persist_1")
        assert last is not None
        assert last["status"] == "accepted"

    def test_status_survives_module_reload(self, streaming, tmp_path):
        # Inject, then reload module pretending Streamlit cold-restart.
        streaming._test_inject_event({
            "client_order_id": "coid_persist_2",
            "event":           "filled",
            "symbol":          "ETHA",
            "filled_qty":      5,
            "filled_avg_price": 22.10,
        })
        cache_path = streaming._CACHE_PATH
        # Reload
        import integrations.alpaca_streaming as mod
        importlib.reload(mod)
        mod._CACHE_PATH = cache_path
        last = mod.get_last_status("coid_persist_2")
        assert last is not None
        assert last["status"] == "filled"
        assert last["fill_qty"] == 5

    def test_unknown_id_returns_none(self, streaming):
        assert streaming.get_last_status("never_seen") is None


# ── 4. Multi-order callback independence ─────────────────────────────────────

class TestMultiOrderIndependence:
    def test_callbacks_isolated_per_order(self, streaming):
        seen_a, seen_b = [], []
        streaming.register_order_callback("coid_A", seen_a.append)
        streaming.register_order_callback("coid_B", seen_b.append)
        streaming._test_inject_event({
            "client_order_id": "coid_A",
            "event":           "fill",
            "symbol":          "IBIT",
        })
        assert len(seen_a) == 1
        assert len(seen_b) == 0
        streaming._test_inject_event({
            "client_order_id": "coid_B",
            "event":           "rejected",
            "symbol":          "FBTC",
        })
        assert len(seen_a) == 1
        assert len(seen_b) == 1
        assert seen_b[0]["status"] == "rejected"


# ── 5. Reconnect-on-disconnect with exponential backoff ──────────────────────

class TestReconnectBackoff:
    def test_backoff_table_is_exponential_and_capped(self, streaming):
        # 1, 2, 4, 8, 16, 30 — capped at 30s.
        assert streaming._BACKOFF_SECONDS == (1, 2, 4, 8, 16, 30)
        # Strictly non-decreasing
        for a, b in zip(streaming._BACKOFF_SECONDS,
                         streaming._BACKOFF_SECONDS[1:]):
            assert b >= a
        # Final cap is 30
        assert streaming._BACKOFF_SECONDS[-1] == 30

    def test_reconnect_attempts_increment_on_disconnect(self, streaming, monkeypatch):
        # Simulate the inner loop's reconnect counter logic without
        # touching real WebSockets. The loop bumps _RECONNECT_ATTEMPTS
        # before sleeping per backoff.
        monkeypatch.setenv("ALPACA_API_KEY_ID", "PK")
        monkeypatch.setenv("ALPACA_API_SECRET_KEY", "SK")

        call_count = {"n": 0}

        def fake_build_stream():
            call_count["n"] += 1
            if call_count["n"] >= 3:
                # Stop after 2 reconnects so the test terminates.
                streaming._STOP_EVENT.set()
                return None
            # Return a fake stream whose .run() raises immediately to
            # trigger the reconnect path.
            class FakeStream:
                def run(self):
                    raise ConnectionResetError("simulated disconnect")
                def stop(self):
                    pass
            return FakeStream()

        monkeypatch.setattr(streaming, "_build_stream", fake_build_stream)
        # Patch wait() to not actually sleep — test must not block.
        original_event_cls = threading.Event
        class FastEvent(original_event_cls):
            def wait(self, timeout=None):    # noqa: ARG002
                return self.is_set()
        streaming._STOP_EVENT = FastEvent()
        streaming._RECONNECT_ATTEMPTS = 0
        streaming._stream_loop()
        # We should have attempted reconnect at least twice.
        assert streaming._RECONNECT_ATTEMPTS >= 2


# ── 6. Idempotent start_order_stream ─────────────────────────────────────────

class TestIdempotentStart:
    def test_start_twice_is_noop(self, streaming, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY_ID", "PK")
        monkeypatch.setenv("ALPACA_API_SECRET_KEY", "SK")

        # Replace _stream_loop with a function that just blocks until stop.
        block_evt = threading.Event()

        def fake_loop():
            block_evt.wait(timeout=5.0)

        monkeypatch.setattr(streaming, "_stream_loop", fake_loop)
        streaming.start_order_stream()
        first_thread = streaming._THREAD
        assert first_thread is not None
        assert first_thread.is_alive()
        # Second call must not spawn a second thread.
        streaming.start_order_stream()
        assert streaming._THREAD is first_thread
        # Cleanup
        block_evt.set()
        first_thread.join(timeout=2.0)


# ── 7. Graceful no-op when env unset ─────────────────────────────────────────

class TestGracefulUnconfigured:
    def test_start_noop_when_env_unset(self, streaming, monkeypatch):
        for n in ("ALPACA_API_KEY_ID", "ALPACA_API_KEY",
                  "ALPACA_API_SECRET_KEY", "ALPACA_API_SECRET"):
            monkeypatch.delenv(n, raising=False)
        streaming.start_order_stream()
        assert streaming._THREAD is None
        assert streaming.is_streaming() is False


# ── 8. Health snapshot for Settings UI ───────────────────────────────────────

class TestHealthSnapshot:
    def test_health_reports_unconfigured_state(self, streaming, monkeypatch):
        for n in ("ALPACA_API_KEY_ID", "ALPACA_API_KEY",
                  "ALPACA_API_SECRET_KEY", "ALPACA_API_SECRET"):
            monkeypatch.delenv(n, raising=False)
        h = streaming.get_stream_health()
        assert h["configured"] is False
        assert h["streaming"] is False
        assert h["tracked_orders"] == 0

    def test_health_counts_tracked_orders(self, streaming):
        for i in range(3):
            streaming._test_inject_event({
                "client_order_id": f"coid_{i}",
                "event":           "submitted",
                "symbol":          "IBIT",
            })
        h = streaming.get_stream_health()
        assert h["tracked_orders"] == 3
        assert h["last_event_iso"] is not None


# ── 9. Snapshot recent for Portfolio expander ───────────────────────────────

class TestSnapshotRecent:
    def test_snapshot_recent_returns_newest_first(self, streaming):
        import time as _t
        for i in range(5):
            streaming._test_inject_event({
                "client_order_id": f"coid_seq_{i}",
                "event":           "accepted",
                "symbol":          "IBIT",
            })
            _t.sleep(0.01)   # ensure ISO timestamps differ
        rows = streaming.snapshot_recent(limit=3)
        assert len(rows) == 3
        # Newest first
        assert rows[0]["client_order_id"] == "coid_seq_4"
