"""
test_client_adapter.py — Sprint 3 coverage.

Verifies the ClientAdapter ABC contract, the 5 adapter
implementations, and the get_active_adapter() factory's
fall-through-to-demo behavior.

CRM adapters are tested with monkeypatched HTTP — no live network.

CLAUDE.md governance: §4 (audit + tests), §11 (env-scoped state).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


class _MockResp:
    def __init__(self, status: int, json_obj=None, text: str = ""):
        self.status_code = status
        if json_obj is not None and not text:
            text = json.dumps(json_obj)
        self.text = text
        self._json = json_obj
    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


# ═══════════════════════════════════════════════════════════════════════════
# ABC + factory
# ═══════════════════════════════════════════════════════════════════════════

class TestRegistryAndFactory:
    def test_registry_lists_all_5_providers(self):
        import core.client_adapters  # noqa: F401 — registers
        from core.client_adapter import list_registered_providers
        names = list_registered_providers()
        for expected in ("demo", "csv_import", "wealthbox", "redtail", "salesforce_fsc"):
            assert expected in names, f"missing {expected}"
        assert names[0] == "demo"   # demo first by stable sort

    def test_get_active_adapter_defaults_to_demo(self, monkeypatch):
        monkeypatch.delenv("CLIENT_ADAPTER_PROVIDER", raising=False)
        from core.client_adapter import get_active_adapter
        a = get_active_adapter()
        assert a.provider_name() == "demo"
        assert a.is_configured() is True

    def test_get_active_adapter_falls_back_when_unconfigured(self, monkeypatch):
        # Request wealthbox without setting the API key → falls back to demo.
        monkeypatch.setenv("CLIENT_ADAPTER_PROVIDER", "wealthbox")
        monkeypatch.delenv("WEALTHBOX_API_KEY", raising=False)
        from core.client_adapter import get_active_adapter
        a = get_active_adapter()
        assert a.provider_name() == "demo"

    def test_get_active_adapter_falls_back_on_unknown_provider(self, monkeypatch):
        monkeypatch.setenv("CLIENT_ADAPTER_PROVIDER", "no_such_provider")
        from core.client_adapter import get_active_adapter
        a = get_active_adapter()
        assert a.provider_name() == "demo"


# ═══════════════════════════════════════════════════════════════════════════
# DemoClientAdapter
# ═══════════════════════════════════════════════════════════════════════════

class TestDemoAdapter:
    def test_demo_returns_3_clients_with_required_fields(self):
        from core.client_adapters.demo_adapter import DemoClientAdapter
        a = DemoClientAdapter()
        clients = a.list_clients()
        assert len(clients) == 3
        for c in clients:
            assert c.id and c.name
            assert c.total_portfolio_usd > 0
            assert c.assigned_tier != "(unassigned)"

    def test_demo_get_client_by_id(self):
        from core.client_adapters.demo_adapter import DemoClientAdapter
        a = DemoClientAdapter()
        c = a.get_client("demo_001")
        assert c is not None
        assert "Beatrice" in c.name

    def test_demo_get_unknown_client_returns_none(self):
        from core.client_adapters.demo_adapter import DemoClientAdapter
        a = DemoClientAdapter()
        assert a.get_client("nonexistent") is None


# ═══════════════════════════════════════════════════════════════════════════
# CSVImportClientAdapter
# ═══════════════════════════════════════════════════════════════════════════

class TestCSVImportAdapter:
    def test_unconfigured_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLIENT_CSV_PATH", str(tmp_path / "nope.csv"))
        from core.client_adapters.csv_import_adapter import CSVImportClientAdapter
        assert CSVImportClientAdapter().is_configured() is False
        assert CSVImportClientAdapter().list_clients() == []

    def test_reads_clients_from_csv_round_trip(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "clients.csv"
        csv_path.write_text(
            "id,name,age,assigned_tier,total_portfolio_usd,crypto_allocation_pct,rebalance_needed\n"
            "csv_001,Jane Smith,52,Moderate,750000,8.5,false\n"
            "csv_002,Carlos Rivera,38,Aggressive,420000,22.0,true\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLIENT_CSV_PATH", str(csv_path))
        from core.client_adapters.csv_import_adapter import CSVImportClientAdapter
        a = CSVImportClientAdapter()
        assert a.is_configured() is True
        clients = a.list_clients()
        assert len(clients) == 2
        jane = next(c for c in clients if c.id == "csv_001")
        assert jane.name == "Jane Smith"
        assert jane.age == 52
        assert jane.total_portfolio_usd == 750000.0
        assert jane.crypto_allocation_pct == 8.5
        assert jane.rebalance_needed is False
        carlos = next(c for c in clients if c.id == "csv_002")
        assert carlos.rebalance_needed is True

    def test_csv_skips_rows_missing_id_or_name(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "clients.csv"
        csv_path.write_text(
            "id,name,age\n"
            "csv_001,Valid,40\n"
            ",MissingID,30\n"
            "csv_003,,50\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLIENT_CSV_PATH", str(csv_path))
        from core.client_adapters.csv_import_adapter import CSVImportClientAdapter
        clients = CSVImportClientAdapter().list_clients()
        assert len(clients) == 1
        assert clients[0].id == "csv_001"

    def test_csv_tolerates_dollar_signs_and_commas_in_numbers(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "clients.csv"
        csv_path.write_text(
            'id,name,total_portfolio_usd,crypto_allocation_pct\n'
            'csv_001,Alice,"$1,250,000",12.5%\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("CLIENT_CSV_PATH", str(csv_path))
        from core.client_adapters.csv_import_adapter import CSVImportClientAdapter
        clients = CSVImportClientAdapter().list_clients()
        assert len(clients) == 1
        assert clients[0].total_portfolio_usd == 1250000.0
        assert clients[0].crypto_allocation_pct == 12.5


# ═══════════════════════════════════════════════════════════════════════════
# WealthboxClientAdapter
# ═══════════════════════════════════════════════════════════════════════════

class TestWealthboxAdapter:
    def setup_method(self):
        from core.client_adapters import wealthbox_adapter as wa
        wa._CACHE.clear()

    def test_unconfigured_without_key(self, monkeypatch):
        monkeypatch.delenv("WEALTHBOX_API_KEY", raising=False)
        from core.client_adapters.wealthbox_adapter import WealthboxClientAdapter
        a = WealthboxClientAdapter()
        assert a.is_configured() is False
        assert a.list_clients() == []

    def test_fetches_and_maps_contacts(self, monkeypatch):
        monkeypatch.setenv("WEALTHBOX_API_KEY", "test_key_42")
        # Simulate single page with 2 contacts.
        page1 = {
            "contacts": [
                {"id": 100, "first_name": "Alice", "last_name": "Cooper",
                 "birthdate": "1970-05-15", "contact_type": "Client",
                 "background_information": "VIP relationship since 2018."},
                {"id": 101, "first_name": "Bob", "last_name": "Diaz",
                 "birthdate": "1985-09-01", "contact_type": "Prospect",
                 "background_information": ""},
            ]
        }
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, params=None, headers=None, timeout=None: _MockResp(200, json_obj=page1),
        )
        from core.client_adapters.wealthbox_adapter import WealthboxClientAdapter
        clients = WealthboxClientAdapter().list_clients()
        assert len(clients) == 2
        alice = clients[0]
        assert alice.id == "wealthbox_100"
        assert alice.name == "Alice Cooper"
        assert alice.label == "Client"
        assert alice.age is not None and 50 <= alice.age <= 60
        assert "VIP" in alice.notes

    def test_handles_401_gracefully(self, monkeypatch):
        monkeypatch.setenv("WEALTHBOX_API_KEY", "bad_key")
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, params=None, headers=None, timeout=None: _MockResp(401, text="unauthorized"),
        )
        from core.client_adapters.wealthbox_adapter import WealthboxClientAdapter
        assert WealthboxClientAdapter().list_clients() == []

    def test_caches_response_within_ttl(self, monkeypatch):
        monkeypatch.setenv("WEALTHBOX_API_KEY", "test_key_cache")
        call_count = [0]
        def _fake_get(url, params=None, headers=None, timeout=None):
            call_count[0] += 1
            return _MockResp(200, json_obj={"contacts": [
                {"id": 1, "first_name": "X", "last_name": "Y"},
            ]})
        import requests
        monkeypatch.setattr(requests, "get", _fake_get)
        from core.client_adapters.wealthbox_adapter import WealthboxClientAdapter
        WealthboxClientAdapter().list_clients()
        WealthboxClientAdapter().list_clients()
        # 1 fetch shared across calls (caching).
        assert call_count[0] == 1


# ═══════════════════════════════════════════════════════════════════════════
# RedtailClientAdapter
# ═══════════════════════════════════════════════════════════════════════════

class TestRedtailAdapter:
    def setup_method(self):
        from core.client_adapters import redtail_adapter as ra
        ra._CACHE.clear()

    def test_unconfigured_without_any_credential(self, monkeypatch):
        for k in ("REDTAIL_API_KEY", "REDTAIL_USERKEY",
                  "REDTAIL_USERNAME", "REDTAIL_PASSWORD"):
            monkeypatch.delenv(k, raising=False)
        from core.client_adapters.redtail_adapter import RedtailClientAdapter
        assert RedtailClientAdapter().is_configured() is False
        assert RedtailClientAdapter().list_clients() == []

    def test_configured_via_per_app_key(self, monkeypatch):
        monkeypatch.setenv("REDTAIL_API_KEY", "ABC123")
        for k in ("REDTAIL_USERKEY", "REDTAIL_USERNAME", "REDTAIL_PASSWORD"):
            monkeypatch.delenv(k, raising=False)
        from core.client_adapters.redtail_adapter import RedtailClientAdapter
        assert RedtailClientAdapter().is_configured() is True

    def test_configured_via_basic_auth_triple(self, monkeypatch):
        monkeypatch.delenv("REDTAIL_API_KEY", raising=False)
        monkeypatch.setenv("REDTAIL_USERKEY", "USERKEY32CHARHEX")
        monkeypatch.setenv("REDTAIL_USERNAME", "advisor1")
        monkeypatch.setenv("REDTAIL_PASSWORD", "secret")
        from core.client_adapters.redtail_adapter import RedtailClientAdapter
        assert RedtailClientAdapter().is_configured() is True

    def test_fetches_and_maps_contacts(self, monkeypatch):
        monkeypatch.setenv("REDTAIL_API_KEY", "ABC")
        for k in ("REDTAIL_USERKEY", "REDTAIL_USERNAME", "REDTAIL_PASSWORD"):
            monkeypatch.delenv(k, raising=False)
        page1 = {
            "contacts": [
                {"id": 9001, "full_name": "Eleanor Chen",
                 "dob": "1965-03-22", "status": "Active Client"},
            ]
        }
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, params=None, headers=None, timeout=None: _MockResp(200, json_obj=page1),
        )
        from core.client_adapters.redtail_adapter import RedtailClientAdapter
        clients = RedtailClientAdapter().list_clients()
        assert len(clients) == 1
        assert clients[0].id == "redtail_9001"
        assert clients[0].name == "Eleanor Chen"
        assert clients[0].label == "Active Client"


# ═══════════════════════════════════════════════════════════════════════════
# SalesforceFSCClientAdapter
# ═══════════════════════════════════════════════════════════════════════════

class TestSalesforceFSCAdapter:
    def setup_method(self):
        from core.client_adapters import salesforce_fsc_adapter as sa
        sa._CACHE.clear()

    def test_unconfigured_without_token(self, monkeypatch):
        monkeypatch.delenv("SALESFORCE_FSC_INSTANCE_URL", raising=False)
        monkeypatch.delenv("SALESFORCE_FSC_ACCESS_TOKEN", raising=False)
        from core.client_adapters.salesforce_fsc_adapter import SalesforceFSCClientAdapter
        assert SalesforceFSCClientAdapter().is_configured() is False
        assert SalesforceFSCClientAdapter().list_clients() == []

    def test_unconfigured_when_only_url_set(self, monkeypatch):
        monkeypatch.setenv("SALESFORCE_FSC_INSTANCE_URL", "https://x.my.salesforce.com")
        monkeypatch.delenv("SALESFORCE_FSC_ACCESS_TOKEN", raising=False)
        from core.client_adapters.salesforce_fsc_adapter import SalesforceFSCClientAdapter
        assert SalesforceFSCClientAdapter().is_configured() is False

    def test_fetches_and_maps_records(self, monkeypatch):
        monkeypatch.setenv("SALESFORCE_FSC_INSTANCE_URL", "https://x.my.salesforce.com")
        monkeypatch.setenv("SALESFORCE_FSC_ACCESS_TOKEN", "Bearer123")
        page1 = {
            "totalSize": 1, "done": True,
            "records": [
                {"Id": "0015000000abcd",
                 "Name": "Greta Lindqvist",
                 "FinServ__BirthDate__c": "1972-08-14",
                 "FinServ__Status__c": "Active Client"},
            ]
        }
        import requests
        monkeypatch.setattr(
            requests, "get",
            lambda url, params=None, headers=None, timeout=None: _MockResp(200, json_obj=page1),
        )
        from core.client_adapters.salesforce_fsc_adapter import SalesforceFSCClientAdapter
        clients = SalesforceFSCClientAdapter().list_clients()
        assert len(clients) == 1
        assert clients[0].id == "sfdc_0015000000abcd"
        assert clients[0].name == "Greta Lindqvist"
        assert clients[0].label == "Active Client"
        assert clients[0].age is not None


# ═══════════════════════════════════════════════════════════════════════════
# Integration: get_active_clients() round-trip via factory
# ═══════════════════════════════════════════════════════════════════════════

class TestGetActiveClientsHelper:
    def test_default_returns_demo_dicts(self, monkeypatch):
        monkeypatch.delenv("CLIENT_ADAPTER_PROVIDER", raising=False)
        from core.client_adapter import get_active_clients
        clients = get_active_clients()
        assert len(clients) == 3
        # Legacy dict-shape access still works
        assert all("id" in c and "name" in c for c in clients)

    def test_csv_provider_via_env(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "c.csv"
        csv_path.write_text(
            "id,name,total_portfolio_usd,crypto_allocation_pct,assigned_tier\n"
            "csv_x,Test Client,500000,10,Moderate\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLIENT_ADAPTER_PROVIDER", "csv_import")
        monkeypatch.setenv("CLIENT_CSV_PATH", str(csv_path))
        from core.client_adapter import get_active_clients
        clients = get_active_clients()
        assert len(clients) == 1
        assert clients[0]["id"] == "csv_x"
        assert clients[0]["total_portfolio_usd"] == 500000.0

    def test_get_active_client_by_id(self, monkeypatch):
        monkeypatch.delenv("CLIENT_ADAPTER_PROVIDER", raising=False)
        from core.client_adapter import get_active_client
        c = get_active_client("demo_002")
        assert c is not None
        assert "Marcus" in c["name"]
