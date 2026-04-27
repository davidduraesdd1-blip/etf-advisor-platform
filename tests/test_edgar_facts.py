"""
test_edgar_facts.py — Sprint 2.6 commit 4 coverage.

Verifies the SEC XBRL company-facts long-tail AUM resolver.
Mocks the HTTP layer with monkeypatch so tests don't hit live SEC.

Fixtures encode realistic JSON shapes captured during the smoke-test
deeper-probe round.

CLAUDE.md governance: §4 (test coverage), §10 (primary-source
provenance), §22 (no-fallback honesty).
"""
from __future__ import annotations

import json
import pytest


# Legacy ticker index — old format: {"0": {"ticker": "X", "cik_str": N, ...}}
_LEGACY_INDEX_JSON = {
    "0": {"ticker": "GBTC", "cik_str": 1588489, "title": "Grayscale Bitcoin Trust"},
    "1": {"ticker": "BITB", "cik_str": 1763415, "title": "Bitwise Bitcoin ETF"},
    "2": {"ticker": "AGG",  "cik_str": 1100663, "title": "iShares Core US Aggregate Bond ETF"},
}

# Exchange ticker index — newer format with `fields` + `data` rows.
_EXCHANGE_INDEX_JSON = {
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": [
        [1980994, "iShares Bitcoin Trust ETF",         "IBIT", "NASDAQ"],
        [1729997, "Grayscale CoinDesk Crypto 5 ETF",   "GDLC", "NYSE"],
        [1896677, "Grayscale Solana Staking ETF",      "GSOL", "NYSE"],
        [1234567, "Some Equity Fund",                  "SEF",  "NYSE"],
    ],
}

# companyfacts JSON — minimal shape with us-gaap:Assets fact.
def _make_companyfacts(asset_val_usd: float, end_date: str = "2025-12-31") -> dict:
    return {
        "cik": 1588489,
        "entityName": "Test ETF",
        "facts": {
            "us-gaap": {
                "Assets": {
                    "label": "Assets",
                    "units": {
                        "USD": [
                            {"end": "2024-12-31", "val": asset_val_usd * 0.6, "form": "10-K"},
                            {"end": end_date,     "val": asset_val_usd,        "form": "10-K"},
                        ]
                    }
                }
            }
        }
    }

def _make_companyfacts_netassets(net_val_usd: float, asset_val_usd: float) -> dict:
    """When BOTH NetAssets and Assets exist, NetAssets must win (priority order)."""
    return {
        "facts": {
            "us-gaap": {
                "NetAssets": {
                    "units": {"USD": [{"end": "2025-12-31", "val": net_val_usd}]}
                },
                "Assets": {
                    "units": {"USD": [{"end": "2025-12-31", "val": asset_val_usd}]}
                },
            }
        }
    }


class _MockResp:
    def __init__(self, status: int, text: str = "", json_obj=None):
        self.status_code = status
        self.text = text if text else (json.dumps(json_obj) if json_obj is not None else "")
        self._json = json_obj
    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


def _patch_get(monkeypatch, url_to_resp: dict):
    """Install a requests.get mock that routes by URL substring."""
    import requests
    def _fake_get(url, headers=None, timeout=None):
        for substr, resp in url_to_resp.items():
            if substr in url:
                return resp
        return _MockResp(404, text="<not-mocked>")
    monkeypatch.setattr(requests, "get", _fake_get)


def _clear_caches():
    from integrations import edgar_facts as ef
    ef._TICKER_INDEX_CACHE.clear()
    ef._COMPANYFACTS_CACHE.clear()


# ═══════════════════════════════════════════════════════════════════════════
# Ticker index resolution
# ═══════════════════════════════════════════════════════════════════════════

class TestTickerIndexResolution:
    def setup_method(self):
        _clear_caches()

    def test_resolves_via_legacy_index(self, monkeypatch):
        from integrations import edgar_facts as ef
        _patch_get(monkeypatch, {
            "company_tickers.json":          _MockResp(200, json_obj=_LEGACY_INDEX_JSON),
            "company_tickers_exchange.json": _MockResp(200, json_obj=_EXCHANGE_INDEX_JSON),
        })
        cik = ef._resolve_cik("GBTC")
        assert cik == "0001588489"

    def test_resolves_via_exchange_index_when_legacy_misses(self, monkeypatch):
        from integrations import edgar_facts as ef
        _patch_get(monkeypatch, {
            "company_tickers.json":          _MockResp(200, json_obj=_LEGACY_INDEX_JSON),
            "company_tickers_exchange.json": _MockResp(200, json_obj=_EXCHANGE_INDEX_JSON),
        })
        # IBIT only in exchange index
        cik = ef._resolve_cik("IBIT")
        assert cik == "0001980994"

    def test_returns_none_for_unindexed_ticker(self, monkeypatch):
        from integrations import edgar_facts as ef
        _patch_get(monkeypatch, {
            "company_tickers.json":          _MockResp(200, json_obj=_LEGACY_INDEX_JSON),
            "company_tickers_exchange.json": _MockResp(200, json_obj=_EXCHANGE_INDEX_JSON),
        })
        # BITO files under parent CIK; not in either index.
        assert ef._resolve_cik("BITO") is None

    def test_index_fetch_caches_for_process_lifetime(self, monkeypatch):
        from integrations import edgar_facts as ef
        import requests
        call_count = [0]
        def _counting_get(url, headers=None, timeout=None):
            call_count[0] += 1
            if "company_tickers_exchange" in url:
                return _MockResp(200, json_obj=_EXCHANGE_INDEX_JSON)
            return _MockResp(200, json_obj=_LEGACY_INDEX_JSON)
        monkeypatch.setattr(requests, "get", _counting_get)
        ef._resolve_cik("GBTC")  # triggers legacy fetch
        ef._resolve_cik("IBIT")  # triggers exchange fetch (legacy miss)
        ef._resolve_cik("GBTC")  # re-uses cached legacy
        ef._resolve_cik("IBIT")  # re-uses cached exchange
        # 2 total network fetches, not 4
        assert call_count[0] == 2


# ═══════════════════════════════════════════════════════════════════════════
# Companyfacts → AUM
# ═══════════════════════════════════════════════════════════════════════════

class TestCompanyFactsAum:
    def setup_method(self):
        _clear_caches()

    def test_returns_most_recent_assets_value(self, monkeypatch):
        from integrations import edgar_facts as ef
        _patch_get(monkeypatch, {
            "company_tickers.json":          _MockResp(200, json_obj=_LEGACY_INDEX_JSON),
            "company_tickers_exchange.json": _MockResp(200, json_obj=_EXCHANGE_INDEX_JSON),
            "companyfacts/CIK0001588489":    _MockResp(200, json_obj=_make_companyfacts(14_497_000_000.0)),
        })
        v = ef.get_etf_aum_via_facts("GBTC")
        assert v == pytest.approx(14_497_000_000.0, rel=1e-9)

    def test_netassets_wins_over_assets_when_both_present(self, monkeypatch):
        from integrations import edgar_facts as ef
        _patch_get(monkeypatch, {
            "company_tickers.json":          _MockResp(200, json_obj=_LEGACY_INDEX_JSON),
            "company_tickers_exchange.json": _MockResp(200, json_obj=_EXCHANGE_INDEX_JSON),
            "companyfacts/CIK0001588489":    _MockResp(200, json_obj=_make_companyfacts_netassets(
                net_val_usd=14_000_000_000.0, asset_val_usd=15_000_000_000.0,
            )),
        })
        v = ef.get_etf_aum_via_facts("GBTC")
        # Priority order: NetAssets > Assets, so we must get $14B.
        assert v == pytest.approx(14_000_000_000.0, rel=1e-9)

    def test_unknown_ticker_returns_none(self, monkeypatch):
        from integrations import edgar_facts as ef
        _patch_get(monkeypatch, {
            "company_tickers.json":          _MockResp(200, json_obj=_LEGACY_INDEX_JSON),
            "company_tickers_exchange.json": _MockResp(200, json_obj=_EXCHANGE_INDEX_JSON),
        })
        assert ef.get_etf_aum_via_facts("UNKNOWN") is None

    def test_companyfacts_403_returns_none(self, monkeypatch):
        from integrations import edgar_facts as ef
        _patch_get(monkeypatch, {
            "company_tickers.json":          _MockResp(200, json_obj=_LEGACY_INDEX_JSON),
            "company_tickers_exchange.json": _MockResp(200, json_obj=_EXCHANGE_INDEX_JSON),
            "companyfacts/CIK0001588489":    _MockResp(403, text="<forbidden>"),
        })
        assert ef.get_etf_aum_via_facts("GBTC") is None

    def test_value_below_sanity_bound_rejected(self, monkeypatch):
        """A spurious tiny value (e.g., $500 reported as Assets) must be rejected."""
        from integrations import edgar_facts as ef
        _patch_get(monkeypatch, {
            "company_tickers.json":          _MockResp(200, json_obj=_LEGACY_INDEX_JSON),
            "company_tickers_exchange.json": _MockResp(200, json_obj=_EXCHANGE_INDEX_JSON),
            "companyfacts/CIK0001588489":    _MockResp(200, json_obj=_make_companyfacts(500.0)),
        })
        # Our sanity bound is 1e6 minimum.
        assert ef.get_etf_aum_via_facts("GBTC") is None

    def test_facts_with_no_priority_keys_returns_none(self, monkeypatch):
        """A registrant with only obscure XBRL facts and no canonical
        AUM-shaped fact returns None — chain falls through."""
        from integrations import edgar_facts as ef
        _patch_get(monkeypatch, {
            "company_tickers.json":          _MockResp(200, json_obj=_LEGACY_INDEX_JSON),
            "company_tickers_exchange.json": _MockResp(200, json_obj=_EXCHANGE_INDEX_JSON),
            "companyfacts/CIK0001588489":    _MockResp(200, json_obj={
                "facts": {"us-gaap": {
                    "ProceedsFromSaleOfTrustAssetsToPayExpenses": {
                        "units": {"USD": [{"end": "2025-12-31", "val": 1_000_000.0}]}
                    }
                }}
            }),
        })
        assert ef.get_etf_aum_via_facts("GBTC") is None
