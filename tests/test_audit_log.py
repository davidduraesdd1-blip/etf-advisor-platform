"""
Day-4 tests for core.audit_log.

Covers append/read round-trip, 200-entry ring-buffer trim, atomic write
without tempfile leaks, and the demo-seed behavior.
"""
from __future__ import annotations

import json

import pytest

from core.audit_log import (
    AUDIT_LOG_PATH,
    MAX_ENTRIES,
    append_entry,
    clear_log,
    recent_entries,
    seed_demo_entries,
)


@pytest.fixture(autouse=True)
def _clean_log():
    clear_log()
    yield
    clear_log()


class TestAppendAndRead:
    def test_append_single_entry_is_readable(self):
        append_entry(client_id="c1", action="test_action", detail="hello")
        entries = recent_entries(limit=10)
        assert len(entries) == 1
        assert entries[0]["client"] == "c1"
        assert entries[0]["action"] == "test_action"
        assert entries[0]["detail"] == "hello"

    def test_multiple_entries_newest_first(self):
        append_entry(client_id="c1", action="first")
        append_entry(client_id="c1", action="second")
        append_entry(client_id="c1", action="third")
        entries = recent_entries(limit=10)
        actions = [e["action"] for e in entries]
        assert actions == ["third", "second", "first"]


class TestRingBufferCap:
    def test_cap_at_max_entries(self):
        for i in range(MAX_ENTRIES + 50):
            append_entry(client_id="bulk", action=f"action_{i}")
        entries = recent_entries(limit=MAX_ENTRIES + 100)
        assert len(entries) == MAX_ENTRIES

    def test_oldest_trimmed_first(self):
        for i in range(MAX_ENTRIES + 10):
            append_entry(client_id="bulk", action=f"action_{i}")
        entries = recent_entries(limit=MAX_ENTRIES + 10)
        # Most recent should be action_(MAX+9); oldest action in
        # the ring should be action_10 (first 10 got trimmed)
        assert entries[0]["action"] == f"action_{MAX_ENTRIES + 9}"
        assert entries[-1]["action"] == "action_10"


class TestAtomicWrite:
    def test_no_tempfile_leak_after_write(self):
        append_entry(client_id="c1", action="tempfile_check")
        parent = AUDIT_LOG_PATH.parent
        leftover = list(parent.glob(".tmp_audit_*.json"))
        assert not leftover, f"Tempfile leaked: {leftover}"

    def test_json_file_is_valid(self):
        append_entry(client_id="c1", action="json_check")
        with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert "entries" in data
        assert len(data["entries"]) >= 1


class TestSeedDemo:
    def test_seed_only_runs_on_empty_log(self):
        # First seed fills with N entries
        fake_clients = [
            {"id": "d1", "assigned_tier": "Moderate",
             "total_portfolio_usd": 100_000, "drift_pct": 2.0},
        ]
        seed_demo_entries(fake_clients)
        first_count = len(recent_entries(limit=100))
        assert first_count > 0

        # Second call is a no-op
        seed_demo_entries(fake_clients)
        second_count = len(recent_entries(limit=100))
        assert first_count == second_count

    def test_seed_covers_all_clients(self):
        clients = [
            {"id": "a", "assigned_tier": "X", "total_portfolio_usd": 1, "drift_pct": 0},
            {"id": "b", "assigned_tier": "Y", "total_portfolio_usd": 2, "drift_pct": 0},
        ]
        seed_demo_entries(clients)
        entries = recent_entries(limit=100)
        client_ids = {e["client"] for e in entries}
        assert client_ids == {"a", "b"}
