"""
core/client_adapters/wealthbox_adapter.py — live Wealthbox CRM integration.

Sprint 3 commit 4 (2026-04-30). Cowork directive: "fully live as much
as possible." This adapter performs real HTTP calls to Wealthbox's
public REST API when WEALTHBOX_API_KEY is set in the environment.

API: https://api.crmworkspace.com/v1/
Docs: https://app.crmworkspace.com/api-doc

Auth: header `ACCESS_TOKEN: <api_key>` (Wealthbox's preferred pattern).
Rate limit: 100 req/min per key (more than enough for one fetch per
page render with 24h cache TTL).

Endpoints used:
  GET /v1/contacts?per_page=100&page=N      (paginated list of clients)

Field mapping (Wealthbox → ClientRecord):
  id                    ← str(contact.id)
  name                  ← contact.first_name + " " + contact.last_name
  age                   ← computed from contact.birthdate (yyyy-mm-dd)
  label                 ← contact.contact_type (Client / Prospect / etc.)
  assigned_tier         ← "(unassigned)" — advisor sets via UI
  total_portfolio_usd   ← 0.0 — Wealthbox doesn't track portfolio value
  crypto_allocation_pct ← 0.0 — same
  notes                 ← contact.notes (truncated to 1KB for UI safety)

Custom fields like assigned_tier and crypto_allocation_pct are not
native to Wealthbox; the advisor enters them on the platform after
import. CRM remains the source-of-truth for identity + contact
metadata; the platform owns portfolio + risk-tier data.

In-memory result cache:
  Wealthbox responses are cached at module level for 5 minutes —
  long enough to absorb a Streamlit page rerun storm, short enough
  to pick up CRM updates within one quarterly review cycle.

CLAUDE.md governance: §10 (CRM as one provenance source), §11
(env-scoped state — no client data persisted to disk).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from core.client_adapter import ClientAdapter, ClientRecord, register_adapter

logger = logging.getLogger(__name__)

_API_BASE = "https://api.crmworkspace.com/v1"
_TIMEOUT_SEC = 12
_USER_AGENT = "ETF-Advisor-Platform/0.1 (advisor CRM integration)"
_PER_PAGE = 100
_MAX_PAGES = 50  # safety bound — 5,000 contacts max per fetch

# Module-level cache. Single-key (the api_key); invalidates after TTL.
_CACHE: dict[str, tuple[float, list[ClientRecord]]] = {}
_CACHE_TTL_SEC = 300  # 5 minutes


def _api_key() -> str:
    return os.environ.get("WEALTHBOX_API_KEY", "").strip()


def _compute_age(birthdate_str: Optional[str]) -> Optional[int]:
    """Wealthbox's birthdate field is `YYYY-MM-DD`. Returns None on
    missing/malformed input."""
    if not birthdate_str:
        return None
    try:
        bd = datetime.fromisoformat(str(birthdate_str)[:10])
    except (ValueError, TypeError):
        return None
    today = datetime.now(timezone.utc).date()
    years = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
    return years if 0 < years < 150 else None


def _truncate(s: str, n: int = 1024) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n - 1] + "…"


def _contact_to_record(c: dict) -> Optional[ClientRecord]:
    """Map a Wealthbox contact JSON object to a ClientRecord. Returns
    None for invalid records (missing id/name)."""
    cid = c.get("id")
    fn = (c.get("first_name") or "").strip()
    ln = (c.get("last_name") or "").strip()
    name = f"{fn} {ln}".strip()
    if cid is None or not name:
        return None
    return ClientRecord(
        id=f"wealthbox_{cid}",
        name=name,
        label=str(c.get("contact_type", "") or ""),
        age=_compute_age(c.get("birthdate")),
        assigned_tier="(unassigned)",
        total_portfolio_usd=0.0,
        crypto_allocation_pct=0.0,
        last_rebalance_iso=None,
        drift_pct=0.0,
        rebalance_needed=False,
        notes=_truncate(c.get("background_information", "")),
        situation_today="",
    )


def _fetch_all_pages(api_key: str) -> list[ClientRecord]:
    """Walk the paginated contacts endpoint. Returns [] on auth /
    network / parse failures. Logs at INFO."""
    try:
        import requests
    except ImportError:
        logger.warning("requests not installed — Wealthbox adapter inert")
        return []

    out: list[ClientRecord] = []
    headers = {
        "ACCESS_TOKEN": api_key,
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    for page in range(1, _MAX_PAGES + 1):
        try:
            resp = requests.get(
                f"{_API_BASE}/contacts",
                params={"per_page": _PER_PAGE, "page": page},
                headers=headers,
                timeout=_TIMEOUT_SEC,
            )
        except requests.RequestException as exc:
            logger.info("Wealthbox fetch failed (page %d): %s", page, exc)
            break
        if resp.status_code == 401:
            logger.info("Wealthbox auth rejected — check WEALTHBOX_API_KEY")
            break
        if resp.status_code == 429:
            # Rate-limited: stop here and return what we have.
            logger.info("Wealthbox 429 on page %d — returning partial", page)
            break
        if resp.status_code != 200:
            logger.info("Wealthbox returned %d on page %d", resp.status_code, page)
            break
        try:
            data = resp.json()
        except ValueError:
            logger.info("Wealthbox JSON parse failed on page %d", page)
            break
        contacts = data.get("contacts") if isinstance(data, dict) else None
        if not contacts:
            break
        for c in contacts:
            rec = _contact_to_record(c) if isinstance(c, dict) else None
            if rec is not None:
                out.append(rec)
        # Heuristic stopping: if we got fewer than per_page, we're at
        # the last page. Wealthbox also returns meta.total_entries but
        # this is robust to schema drift.
        if len(contacts) < _PER_PAGE:
            break
    return out


class WealthboxClientAdapter(ClientAdapter):
    """Live Wealthbox CRM adapter. Configured iff WEALTHBOX_API_KEY
    is set."""

    def provider_name(self) -> str:
        return "wealthbox"

    def is_configured(self) -> bool:
        return bool(_api_key())

    def list_clients(self) -> list[ClientRecord]:
        api_key = _api_key()
        if not api_key:
            return []
        # Cache hit?
        cache_entry = _CACHE.get(api_key)
        if cache_entry:
            ts, recs = cache_entry
            if time.time() - ts < _CACHE_TTL_SEC:
                return recs
        recs = _fetch_all_pages(api_key)
        _CACHE[api_key] = (time.time(), recs)
        return recs


register_adapter("wealthbox", WealthboxClientAdapter)
