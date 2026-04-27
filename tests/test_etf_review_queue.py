"""
test_etf_review_queue.py — Cowork audit-round-1 commit 5 (test #2).

Coverage for ``core.etf_review_queue`` (Bucket 3, 2026-04-26).

The review queue:
  1. enrich_filing() — adds suggested_ticker / suggested_category /
     suggested_underlying to a raw EDGAR filing dict.
  2. add_pending() — appends to JSON queue, dedupes against
     approved/rejected lists.
  3. approve_entry() — moves pending → approved, writes to
     ``data/etf_user_additions.json``.
  4. reject_entry() — moves pending → rejected.
  5. load_user_additions() — read approved entries for universe merge.

Tests use ``tmp_path`` to redirect QUEUE_PATH + ADDITIONS_PATH so the
real data files aren't mutated.

CLAUDE.md governance: §4 (audit protocol — Bucket 3 requires test
coverage to be production-grade).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── Test fixtures ────────────────────────────────────────────────────

@pytest.fixture
def temp_queue(tmp_path, monkeypatch):
    """Redirect the queue + additions paths to a temp dir."""
    import core.etf_review_queue as rq
    q_path = tmp_path / "queue.json"
    a_path = tmp_path / "additions.json"
    monkeypatch.setattr(rq, "QUEUE_PATH", q_path)
    monkeypatch.setattr(rq, "ADDITIONS_PATH", a_path)
    yield rq, q_path, a_path


def _filing(accession: str, name: str = "Acme Bitcoin Trust", **kwargs) -> dict:
    """Helper: realistic EDGAR filing payload."""
    base = {
        "accession_number":  accession,
        "filing_date":       "2026-04-26",
        "form_type":         "S-1",
        "filer_cik":         "0001234567",
        "filer_name":        name,
        "matched_keywords":  ["bitcoin", "spot"],
        "raw_match_text":    "Acme Bitcoin Trust filing for spot bitcoin",
    }
    base.update(kwargs)
    return base


# ── enrich_filing — heuristic enrichment ────────────────────────────

class TestEnrichFiling:
    def test_btc_spot_classification(self, temp_queue):
        rq, _, _ = temp_queue
        f = _filing("acc-1", name="Acme Bitcoin Trust (ABCT) ETF")
        out = rq.enrich_filing(f)
        assert out["suggested_category"] == "btc_spot"
        assert out["suggested_underlying"] == "BTC"
        assert out["suggested_ticker"] == "ABCT"
        assert out["review_status"] == "pending"

    def test_eth_spot_classification(self, temp_queue):
        rq, _, _ = temp_queue
        f = _filing("acc-2", name="Foo Ethereum Trust (FETH) ETF",
                    raw_match_text="ethereum spot fund",
                    matched_keywords=["ethereum", "spot"])
        out = rq.enrich_filing(f)
        assert out["suggested_category"] == "eth_spot"
        assert out["suggested_underlying"] == "ETH"
        assert out["suggested_ticker"] == "FETH"

    def test_leveraged_pattern(self, temp_queue):
        rq, _, _ = temp_queue
        f = _filing("acc-3", name="Acme 2x Bitcoin Daily ETF",
                    raw_match_text="2x leveraged bitcoin")
        out = rq.enrich_filing(f)
        assert out["suggested_category"] == "leveraged"

    def test_altcoin_solana(self, temp_queue):
        rq, _, _ = temp_queue
        f = _filing("acc-4", name="Acme Solana Trust",
                    raw_match_text="spot solana",
                    matched_keywords=["solana", "spot"])
        out = rq.enrich_filing(f)
        assert out["suggested_category"] == "altcoin_spot"
        assert out["suggested_underlying"] == "SOL"

    def test_unknown_filing_no_crash(self, temp_queue):
        rq, _, _ = temp_queue
        f = _filing("acc-5", name="Some Other Trust",
                    raw_match_text="nothing crypto here", matched_keywords=[])
        out = rq.enrich_filing(f)
        assert out["review_status"] == "pending"
        # Suggestions can be None; just don't crash.
        assert "suggested_ticker" in out
        assert "suggested_category" in out


# ── add_pending — queue persistence + dedup ─────────────────────────

class TestAddPending:
    def test_first_add_writes_queue(self, temp_queue):
        rq, q_path, _ = temp_queue
        n = rq.add_pending([_filing("acc-100")])
        assert n == 1
        assert q_path.exists()
        loaded = json.loads(q_path.read_text())
        assert len(loaded["pending"]) == 1

    def test_dedupes_against_pending(self, temp_queue):
        rq, _, _ = temp_queue
        rq.add_pending([_filing("acc-200")])
        n = rq.add_pending([_filing("acc-200")])
        assert n == 0   # already pending
        q = rq.load_queue()
        assert len(q["pending"]) == 1

    def test_skips_approved(self, temp_queue):
        rq, _, _ = temp_queue
        # Seed approved
        rq.add_pending([_filing("acc-300")])
        rq.approve_entry("acc-300", ticker_override="XXX",
                         category_override="btc_spot",
                         underlying_override="BTC")
        # Re-scan finds same accession — must NOT re-add.
        n = rq.add_pending([_filing("acc-300")])
        assert n == 0
        q = rq.load_queue()
        assert len(q["pending"]) == 0
        assert len(q["approved"]) == 1

    def test_skips_rejected(self, temp_queue):
        rq, _, _ = temp_queue
        rq.add_pending([_filing("acc-400")])
        rq.reject_entry("acc-400", notes="not a fit")
        # Re-scan — must NOT re-add a rejected accession.
        n = rq.add_pending([_filing("acc-400")])
        assert n == 0

    def test_filing_without_accession_is_skipped(self, temp_queue):
        rq, _, _ = temp_queue
        bad = {"filer_name": "no accession", "filing_date": "2026-04-26"}
        n = rq.add_pending([bad])
        assert n == 0


# ── approve_entry / reject_entry ────────────────────────────────────

class TestApproveReject:
    def test_approve_writes_additions(self, temp_queue):
        rq, _, a_path = temp_queue
        rq.add_pending([_filing("acc-500", name="Acme BTC ETF (ABTC)")])
        result = rq.approve_entry(
            "acc-500",
            ticker_override="ABTC",
            category_override="btc_spot",
            underlying_override="BTC",
            notes="approved for tier 2+",
        )
        assert result is not None
        assert result["approved_ticker"] == "ABTC"
        # Additions sidecar must contain a universe-shaped entry.
        assert a_path.exists()
        adds = json.loads(a_path.read_text())
        assert any(a["ticker"] == "ABTC" for a in adds)

    def test_approve_idempotent_on_additions(self, temp_queue):
        rq, _, _ = temp_queue
        rq.add_pending([_filing("acc-501", name="Acme BTC (ABTC)")])
        rq.approve_entry("acc-501", ticker_override="ABTC",
                         category_override="btc_spot",
                         underlying_override="BTC")
        adds_first = rq.load_user_additions()
        # Manually re-pending + re-approve same ticker (real-world: corrected
        # filing). Must not produce a duplicate row in additions.
        # (approve_entry returns None when the accession is already approved.)
        result = rq.approve_entry(
            "acc-501", ticker_override="ABTC",
            category_override="btc_spot", underlying_override="BTC",
        )
        adds_second = rq.load_user_additions()
        assert result is None
        assert len(adds_first) == len(adds_second) == 1

    def test_reject_keeps_no_additions(self, temp_queue):
        rq, _, a_path = temp_queue
        rq.add_pending([_filing("acc-600")])
        rq.reject_entry("acc-600", notes="duplicate of existing")
        # No additions file should be touched.
        if a_path.exists():
            adds = json.loads(a_path.read_text())
            assert len(adds) == 0
        q = rq.load_queue()
        assert len(q["rejected"]) == 1
        assert q["rejected"][0]["review_status"] == "rejected"
        assert q["rejected"][0]["review_notes"] == "duplicate of existing"

    def test_approve_unknown_accession_returns_none(self, temp_queue):
        rq, _, _ = temp_queue
        result = rq.approve_entry("non-existent")
        assert result is None

    def test_reject_unknown_accession_returns_none(self, temp_queue):
        rq, _, _ = temp_queue
        result = rq.reject_entry("non-existent")
        assert result is None


# ── load_user_additions for universe merge ──────────────────────────

class TestUserAdditionsLoad:
    def test_empty_when_no_file(self, temp_queue):
        rq, _, _ = temp_queue
        assert rq.load_user_additions() == []

    def test_returns_approved_shape(self, temp_queue):
        rq, _, _ = temp_queue
        rq.add_pending([_filing("acc-700", name="Acme XYZ")])
        rq.approve_entry("acc-700", ticker_override="XYZ",
                         category_override="btc_spot",
                         underlying_override="BTC")
        adds = rq.load_user_additions()
        assert len(adds) == 1
        for required in ("ticker", "issuer", "category", "underlying", "name"):
            assert required in adds[0]


# ── Robustness: malformed disk state ────────────────────────────────

class TestRobustness:
    def test_load_queue_handles_garbage_json(self, temp_queue):
        rq, q_path, _ = temp_queue
        q_path.write_text("not valid json {{{")
        loaded = rq.load_queue()
        assert loaded == {"pending": [], "approved": [], "rejected": []}

    def test_load_user_additions_handles_garbage(self, temp_queue):
        rq, _, a_path = temp_queue
        a_path.write_text("not json")
        assert rq.load_user_additions() == []
