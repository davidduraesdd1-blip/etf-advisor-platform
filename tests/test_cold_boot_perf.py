"""
test_cold_boot_perf.py — Sprint 1 Commit 3 cold-boot regression guard.

Profiles `load_universe_with_live_analytics` under simulated yfinance +
Stooq outage (both unreachable). Asserts the universe loader returns
in well under 8 seconds — the production target — even when every
external data source is failing.

Background: before the persisted-circuit-breaker fix, cold-boot took
~16.5 seconds in this scenario because `_fetch_single_ticker` walked
the yfinance → Stooq fallback chain serially for ~130 tickers missing
from the precomputed analytics snapshot, each hitting a 10-second
network timeout. With the fix, after 3 yfinance failures + 3 Stooq
failures the breaker flips to "unavailable" and every subsequent call
short-circuits in microseconds.

CI margin: assert <12s (vs production target 8s) so the test isn't
flaky on slow CI runners.

CLAUDE.md governance: §4 (audit + perf), §12 (data refresh).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def _block_all_data_sources(monkeypatch, tmp_path):
    """
    Patch every external data source the universe loader might call so
    the cold-boot exercises the fallback chain end-to-end without
    touching the network. Each mock raises after a tiny simulated
    latency (50 ms) — enough to preserve the loop ordering / breaker
    timing semantics, fast enough that the test finishes in seconds.

    Also redirects the persisted circuit-breaker state path to tmp_path
    so prior test runs don't pollute this one.
    """
    # Force the universe loader to take the live-enrichment path
    # (DEMO_MODE_NO_FETCH=0 short-circuits to empty bundles).
    monkeypatch.setenv("DEMO_MODE_NO_FETCH", "0")

    def _yf_unreachable(*args, **kwargs):
        time.sleep(0.05)
        raise ConnectionError("yfinance unreachable (test mock)")

    def _http_block(url, *args, **kwargs):
        if any(host in url for host in ("stooq.com", "stlouisfed.org", "yahoo")):
            time.sleep(0.05)
            raise ConnectionError(f"blocked: {url}")
        # Anything else (none expected on cold-boot path) → pass through.
        import requests as _r
        return _r._real_get_for_test(url, *args, **kwargs)   # set below

    # Stash the real requests.get before patching so tests that DON'T
    # block can still hit non-data hosts (none used currently).
    import requests
    if not hasattr(requests, "_real_get_for_test"):
        requests._real_get_for_test = requests.get
    monkeypatch.setattr(requests, "get", _http_block)

    import yfinance as yf
    monkeypatch.setattr(yf.Ticker, "history", _yf_unreachable)
    monkeypatch.setattr(yf, "download", _yf_unreachable)

    # Redirect persisted breaker state to a tmp dir so the test is
    # hermetic.
    import integrations.data_feeds as df
    monkeypatch.setattr(df, "CB_STATE_PATH", tmp_path / "cb_state.json")
    df.reset_circuit_breaker()

    # Drop module-level memos so the test sees a true cold boot.
    df._yf_memo.clear()
    df._last_close.clear()
    df._LONG_RUN_CAGR_MEMO.clear()

    yield

    df.reset_circuit_breaker()


def test_cold_boot_under_12s_when_yfinance_down(_block_all_data_sources):
    """
    Cold-boot the universe loader with every external source mocked
    to fail. Production target is 8s; CI margin is 12s.

    NOTE: we don't `del sys.modules[...]` to force a re-import — that
    would create two `core.etf_universe` instances and break the
    monkeypatch in unrelated tests like TestScannerHealth that use
    `monkeypatch.setattr("core.etf_universe.<attr>", ...)`. Instead
    we clear the universe-loader's module-level caches so the next
    call re-runs all the work.
    """
    from core.etf_universe import load_universe_with_live_analytics
    import integrations.data_feeds as df

    # Force a fresh enrichment loop: clear price + AUM + long-run memos.
    df._yf_memo.clear()
    df._last_close.clear()
    df._LONG_RUN_CAGR_MEMO.clear()
    # Reset breaker state so the test doesn't inherit "unavailable" from
    # a previous run.
    df.reset_circuit_breaker()

    t0 = time.perf_counter()
    universe = load_universe_with_live_analytics()
    elapsed = time.perf_counter() - t0

    assert universe, "universe loader returned empty"
    assert len(universe) >= 200, f"expected 200+ ETFs, got {len(universe)}"
    assert elapsed < 12.0, (
        f"cold-boot took {elapsed:.2f}s (target 8s, CI margin 12s) "
        f"with all data sources unreachable — circuit breaker fail-fast "
        f"is not short-circuiting properly"
    )


def test_persisted_breaker_state_initializer(_block_all_data_sources, tmp_path):
    """
    Verify that `_initialize_circuit_state()` honors a fresh persisted
    breaker-tripped state file. This is the cold-boot fast path: when
    the previous session persisted "unavailable", the new module init
    starts in "unavailable" mode rather than re-probing yfinance from
    scratch.

    (We test the initializer directly instead of via sys.modules
    teardown because dropping integrations.data_feeds also drops the
    monkeypatched CB_STATE_PATH, which would defeat the test's
    isolation.)
    """
    import json
    import integrations.data_feeds as df

    # Simulate a previous session having tripped the breaker.
    cb_state = {
        "active_source": "unavailable",
        "tripped_at":    time.time() - 30,
        "saved_at_unix": time.time() - 30,
        "saved_at_iso":  "2026-04-28T11:00:00+00:00",
    }
    df.CB_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.CB_STATE_PATH.write_text(json.dumps(cb_state))

    # Run the initializer directly — this is what runs at module-import
    # time during a real cold-boot.
    fresh_state = df._initialize_circuit_state()
    assert fresh_state["active_source"] == "unavailable", (
        f"persisted 'unavailable' state should restore on cold-boot, "
        f"got {fresh_state['active_source']}"
    )

    # Sanity-check: an aged-out persisted state (older than TTL) should
    # NOT restore — falls back to default "yfinance".
    cb_state["saved_at_unix"] = time.time() - 3600   # 1 hr old, > TTL
    df.CB_STATE_PATH.write_text(json.dumps(cb_state))
    aged_state = df._initialize_circuit_state()
    assert aged_state["active_source"] == "yfinance", (
        "persisted state older than CB_PERSIST_TTL_SEC should age out"
    )
