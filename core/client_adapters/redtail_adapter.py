"""
core/client_adapters/redtail_adapter.py — live Redtail CRM integration.

Sprint 3 commit 5 (2026-04-30). Cowork directive: "fully live as much
as possible." Real HTTP calls when the required env vars are set.

API: https://api.redtailtechnology.com/crm/v1/
Docs: https://help.redtailtechnology.com/hc/en-us/articles/360045432473

Redtail's auth model is more involved than Wealthbox:
  - REDTAIL_USERKEY  — your Redtail subscriber UserKey (32-char hex)
  - REDTAIL_USERNAME — Redtail account username
  - REDTAIL_PASSWORD — Redtail account password
The adapter sends HTTP Basic Auth with `Username:UserKey:Password`
joined by colons (Redtail's documented pattern), base64-encoded.
Their other auth mode (UserKey + auth-token) is also supported via
the simpler REDTAIL_API_KEY env var if your account supports it.

Endpoints used:
  GET /crm/v1/contacts?page=N        (paginated client list)

Field mapping (Redtail → ClientRecord):
  id                    ← f"redtail_{contact.id}"
  name                  ← contact.full_name (or first+last)
  age                   ← computed from contact.dob (yyyy-mm-dd)
  label                 ← contact.status (e.g., "Active Client")
  assigned_tier         ← "(unassigned)" — advisor sets via UI
  total_portfolio_usd   ← 0.0 — Redtail doesn't store portfolio value
  notes                 ← (omitted from list endpoint; would need
                          a per-contact /notes fetch — too expensive
                          to do during initial import)

In-memory cache: 5-minute TTL keyed by the auth-tuple hash.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from core.client_adapter import ClientAdapter, ClientRecord, register_adapter

logger = logging.getLogger(__name__)

_API_BASE = "https://api.redtailtechnology.com/crm/v1"
_TIMEOUT_SEC = 12
_USER_AGENT = "ETF-Advisor-Platform/0.1 (advisor CRM integration)"
_PER_PAGE = 50
_MAX_PAGES = 100   # safety bound: 5,000 contacts max

_CACHE: dict[str, tuple[float, list[ClientRecord]]] = {}
_CACHE_TTL_SEC = 300


def _resolve_auth() -> Optional[str]:
    """Build the Authorization header value for Redtail. Supports two
    config patterns:
      a) REDTAIL_API_KEY directly (simpler — for accounts that issue
         per-app keys)
      b) REDTAIL_USERKEY + REDTAIL_USERNAME + REDTAIL_PASSWORD basic-
         auth tuple (the documented pattern for older subscriptions)
    Returns None if neither is set."""
    api_key = os.environ.get("REDTAIL_API_KEY", "").strip()
    if api_key:
        # Per-app keys go on the Authorization header verbatim.
        return f"Userkeyauth {api_key}"
    userkey = os.environ.get("REDTAIL_USERKEY", "").strip()
    username = os.environ.get("REDTAIL_USERNAME", "").strip()
    password = os.environ.get("REDTAIL_PASSWORD", "").strip()
    if userkey and username and password:
        creds = f"{username}:{userkey}:{password}"
        b64 = base64.b64encode(creds.encode("utf-8")).decode("ascii")
        return f"Basic {b64}"
    return None


def _auth_cache_key() -> str:
    """Stable hash of the active auth tuple — used as the cache key
    so a credential rotation invalidates."""
    raw = (
        os.environ.get("REDTAIL_API_KEY", "") +
        "|" + os.environ.get("REDTAIL_USERKEY", "") +
        "|" + os.environ.get("REDTAIL_USERNAME", "")
    )
    return hashlib.sha256(raw.encode()).hexdigest()


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


def _contact_to_record(c: dict) -> Optional[ClientRecord]:
    cid = c.get("id")
    full_name = (c.get("full_name") or "").strip()
    if not full_name:
        fn = (c.get("first_name") or "").strip()
        ln = (c.get("last_name") or "").strip()
        full_name = f"{fn} {ln}".strip()
    if cid is None or not full_name:
        return None
    return ClientRecord(
        id=f"redtail_{cid}",
        name=full_name,
        label=str(c.get("status") or c.get("type") or "" ),
        age=_compute_age(c.get("dob")),
        assigned_tier="(unassigned)",
        total_portfolio_usd=0.0,
        crypto_allocation_pct=0.0,
        last_rebalance_iso=None,
        drift_pct=0.0,
        rebalance_needed=False,
        notes="",  # list endpoint doesn't include notes; fetch separately if needed
        situation_today="",
    )


def _fetch_all_pages(auth_header: str) -> list[ClientRecord]:
    try:
        import requests
    except ImportError:
        logger.warning("requests not installed — Redtail adapter inert")
        return []

    out: list[ClientRecord] = []
    headers = {
        "Authorization": auth_header,
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
        "Content-Type": "application/json",
    }
    for page in range(1, _MAX_PAGES + 1):
        try:
            resp = requests.get(
                f"{_API_BASE}/contacts",
                params={"page": page, "per_page": _PER_PAGE},
                headers=headers,
                timeout=_TIMEOUT_SEC,
            )
        except requests.RequestException as exc:
            logger.info("Redtail fetch failed (page %d): %s", page, exc)
            break
        if resp.status_code == 401:
            logger.info("Redtail auth rejected — check REDTAIL_* env vars")
            break
        if resp.status_code == 429:
            logger.info("Redtail 429 on page %d — returning partial", page)
            break
        if resp.status_code != 200:
            logger.info("Redtail returned %d on page %d", resp.status_code, page)
            break
        try:
            data = resp.json()
        except ValueError:
            break
        contacts = (data.get("contacts") if isinstance(data, dict) else data) or []
        if not contacts:
            break
        for c in contacts:
            rec = _contact_to_record(c) if isinstance(c, dict) else None
            if rec is not None:
                out.append(rec)
        if len(contacts) < _PER_PAGE:
            break
    return out


class RedtailClientAdapter(ClientAdapter):
    """Live Redtail CRM adapter. Configured iff Redtail credentials
    are set in the environment (either REDTAIL_API_KEY OR the
    USERKEY+USERNAME+PASSWORD triple)."""

    def provider_name(self) -> str:
        return "redtail"

    def is_configured(self) -> bool:
        return _resolve_auth() is not None

    def list_clients(self) -> list[ClientRecord]:
        auth = _resolve_auth()
        if not auth:
            return []
        cache_key = _auth_cache_key()
        cached = _CACHE.get(cache_key)
        if cached:
            ts, recs = cached
            if time.time() - ts < _CACHE_TTL_SEC:
                return recs
        recs = _fetch_all_pages(auth)
        _CACHE[cache_key] = (time.time(), recs)
        return recs


register_adapter("redtail", RedtailClientAdapter)
