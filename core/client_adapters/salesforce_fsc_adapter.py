"""
core/client_adapters/salesforce_fsc_adapter.py — live Salesforce FSC integration.

Sprint 3 commit 6 (2026-04-30). Cowork directive: "fully live as much
as possible." Real HTTP calls to Salesforce Financial Services Cloud
when env vars are set.

API: <instance>.salesforce.com/services/data/v60.0/
Docs: https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/

Auth: bearer access token. Salesforce's standard pattern is OAuth 2.0
client-credentials or JWT-bearer flow. This adapter takes the access
token directly via env vars — assumes the operator (or a separate
token-refresh script) maintains a fresh token. Two env vars required:
  SALESFORCE_FSC_INSTANCE_URL  — e.g. https://yourorg.my.salesforce.com
  SALESFORCE_FSC_ACCESS_TOKEN  — current bearer access token

Optional: SALESFORCE_FSC_API_VERSION (defaults to v60.0).

FSC stores advisor client data on the standard Account object with
the FinServ__FinancialAccount__c child object holding portfolio
balances. For initial import we pull Account records of RecordType =
"Client" via SOQL:

  SELECT Id, Name, FinServ__BirthDate__c, FinServ__Status__c
  FROM Account
  WHERE RecordType.Name = 'Client'
  LIMIT 1000

Field mapping (FSC Account → ClientRecord):
  id                    ← f"sfdc_{Id}"
  name                  ← Account.Name
  age                   ← computed from FinServ__BirthDate__c
  label                 ← FinServ__Status__c (e.g., "Active Client")

Portfolio_USD aggregation across child financial accounts is post-
demo work — wires through FSC's `FinancialAccount__c` records with
a separate query. Tracked in pending_work.md.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

from core.client_adapter import ClientAdapter, ClientRecord, register_adapter

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 15
_USER_AGENT = "ETF-Advisor-Platform/0.1 (advisor CRM integration)"
_DEFAULT_API_VERSION = "v60.0"

# SOQL for the Client-record-type pull. Limit is generous; FSC orgs
# above 1k clients can override via SALESFORCE_FSC_QUERY env var.
_DEFAULT_SOQL = (
    "SELECT Id, Name, FinServ__BirthDate__c, FinServ__Status__c "
    "FROM Account "
    "WHERE RecordType.Name = 'Client' "
    "LIMIT 1000"
)

_CACHE: dict[str, tuple[float, list[ClientRecord]]] = {}
_CACHE_TTL_SEC = 300


def _instance_url() -> str:
    return os.environ.get("SALESFORCE_FSC_INSTANCE_URL", "").strip().rstrip("/")


def _access_token() -> str:
    return os.environ.get("SALESFORCE_FSC_ACCESS_TOKEN", "").strip()


def _api_version() -> str:
    return os.environ.get("SALESFORCE_FSC_API_VERSION", _DEFAULT_API_VERSION).strip()


def _soql_query() -> str:
    return os.environ.get("SALESFORCE_FSC_QUERY", _DEFAULT_SOQL).strip()


def _compute_age(dob_str: Optional[str]) -> Optional[int]:
    if not dob_str:
        return None
    try:
        bd = datetime.fromisoformat(str(dob_str)[:10])
    except (ValueError, TypeError):
        return None
    today = datetime.now(timezone.utc).date()
    yrs = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
    return yrs if 0 < yrs < 150 else None


def _record_to_clientrecord(r: dict) -> Optional[ClientRecord]:
    rid = r.get("Id")
    name = (r.get("Name") or "").strip()
    if not rid or not name:
        return None
    return ClientRecord(
        id=f"sfdc_{rid}",
        name=name,
        label=str(r.get("FinServ__Status__c") or "" ),
        age=_compute_age(r.get("FinServ__BirthDate__c")),
        assigned_tier="(unassigned)",
        total_portfolio_usd=0.0,
        crypto_allocation_pct=0.0,
        last_rebalance_iso=None,
        drift_pct=0.0,
        rebalance_needed=False,
        notes="",
        situation_today="",
    )


def _fetch_all_records(instance_url: str, token: str, version: str) -> list[ClientRecord]:
    try:
        import requests
    except ImportError:
        logger.warning("requests not installed — Salesforce adapter inert")
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    out: list[ClientRecord] = []
    # Salesforce REST query supports nextRecordsUrl for pagination.
    next_url = (
        f"{instance_url}/services/data/{version}/query/?"
        + urlencode({"q": _soql_query()})
    )
    page = 0
    while next_url and page < 100:   # safety bound
        try:
            resp = requests.get(next_url, headers=headers, timeout=_TIMEOUT_SEC)
        except requests.RequestException as exc:
            logger.info("Salesforce fetch failed: %s", exc)
            break
        if resp.status_code == 401:
            logger.info("Salesforce auth rejected — refresh SALESFORCE_FSC_ACCESS_TOKEN")
            break
        if resp.status_code == 429:
            logger.info("Salesforce 429 rate-limit — returning partial")
            break
        if resp.status_code != 200:
            logger.info("Salesforce returned %d", resp.status_code)
            break
        try:
            data = resp.json()
        except ValueError:
            break
        records = (data.get("records") if isinstance(data, dict) else None) or []
        for r in records:
            rec = _record_to_clientrecord(r) if isinstance(r, dict) else None
            if rec is not None:
                out.append(rec)
        # Pagination: nextRecordsUrl is a relative path like
        # /services/data/v60.0/query/01g... — prepend instance URL.
        nxt = data.get("nextRecordsUrl") if isinstance(data, dict) else None
        if nxt and isinstance(nxt, str):
            next_url = f"{instance_url}{nxt}"
        else:
            break
        page += 1
    return out


class SalesforceFSCClientAdapter(ClientAdapter):
    """Live Salesforce Financial Services Cloud adapter."""

    def provider_name(self) -> str:
        return "salesforce_fsc"

    def is_configured(self) -> bool:
        return bool(_instance_url() and _access_token())

    def list_clients(self) -> list[ClientRecord]:
        instance = _instance_url()
        token = _access_token()
        if not (instance and token):
            return []
        version = _api_version()
        cache_key = f"{instance}|{version}|{hash(token)}"
        cached = _CACHE.get(cache_key)
        if cached:
            ts, recs = cached
            if time.time() - ts < _CACHE_TTL_SEC:
                return recs
        recs = _fetch_all_records(instance, token, version)
        _CACHE[cache_key] = (time.time(), recs)
        return recs


register_adapter("salesforce_fsc", SalesforceFSCClientAdapter)
