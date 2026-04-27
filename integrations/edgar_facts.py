"""
integrations/edgar_facts.py — SEC XBRL company-facts API for ETF AUM.

Polish round 5, Sprint 2.6 commit 4 (2026-04-30).

Provides `get_etf_aum_via_facts(ticker)` — a long-tail AUM resolver
that hits SEC's XBRL company-facts endpoint and returns the most
recent NetAssets / Assets value reported by the registrant. This
is institutional-grade primary-source data — no scraping, no anti-
bot risk, no Playwright.

Architecture:
  1. Resolve ticker → CIK via either company_tickers.json (legacy
     index) OR company_tickers_exchange.json (newer index that
     covers more recent ETF listings).
  2. Fetch https://data.sec.gov/api/xbrl/companyfacts/CIK<padded>.json
     (typical size 50-500 KB per ETF — much smaller than the full
     N-PORT XML).
  3. Walk a priority list of XBRL fact keys:
       us-gaap:NetAssets
       us-gaap:Assets
       us-gaap:InvestmentCompanyNetAssets
       us-gaap:AssetsFairValueDisclosure
       invest:InvestmentCompanyNetAssets
     For each, take the most-recent USD entry by `end` date.
  4. Sanity-bound 1e6..1e12.

Coverage realities (from the Sprint 2.6 commit-0 deeper-probe):
  - GBTC (CIK 0001588489) → us-gaap:Assets = $14.5B  ✓
  - BITB (CIK 0001763415) → us-gaap:Assets = $3.4B   ✓
  - IBIT (CIK 0001980994) → CIK found, facts available but only via
                            niche keys (AssetsFairValueDisclosure)
  - BITO / BITI / BITU / EETH / GFIL / ADAX / BITQ → not in either
    SEC ticker index. Their funds file under parent CIKs (e.g.,
    ProShares Trust II = 0001174610). The CIK lookup chain falls
    through to None for these — chain step continues to the next
    extractor.

Caching:
  - Module-level dict caches the SEC ticker indexes for the process
    lifetime (one fetch each per run, not per ticker).
  - companyfacts JSON is fetched once per (ticker, run); module
    cache bounded to ~50 entries (enough for our 211-ticker capture).

CLAUDE.md governance: §10 (multi-source provenance — primary-source
SEC XBRL is the highest-tier ingredient on the AUM chain), §11
(env-scoped: requires EDGAR_CONTACT_EMAIL).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Module-level caches ─────────────────────────────────────────────────────

_TICKER_INDEX_CACHE: dict[str, dict[str, str]] = {}   # 'legacy' / 'exchange'
_COMPANYFACTS_CACHE: dict[str, dict] = {}              # cik → facts json
# Bound the companyfacts cache so a long capture run doesn't accumulate.
_COMPANYFACTS_CACHE_MAX = 256

_USER_AGENT_FALLBACK = (
    "ETF-Advisor-Platform "
    "(EDGAR_CONTACT_EMAIL placeholder — set in env)"
)
_TIMEOUT_SEC = 12


def _user_agent() -> str:
    """SEC requires a User-Agent with a real contact email per their
    fair-access policy. Reuse the project's edgar.user_agent() if the
    env var is set; otherwise we honor the same placeholder behavior."""
    try:
        from integrations.edgar import user_agent as edgar_ua
        return edgar_ua()
    except Exception:
        # Defensive — if the edgar module is misconfigured we still
        # use a clearly-attributable UA so the SEC endpoint doesn't
        # silently 403. Caller should set EDGAR_CONTACT_EMAIL.
        return _USER_AGENT_FALLBACK


def _take_token() -> None:
    """Thin wrapper that defers to integrations.edgar.take_token() so
    we share the project's 10-req/sec token bucket."""
    try:
        from integrations.edgar import take_token
        take_token()
    except Exception:
        time.sleep(0.1)   # graceful — at minimum 10 req/sec self-cap


# ─── Ticker → CIK resolution ─────────────────────────────────────────────────

def _load_ticker_index(kind: str) -> dict[str, str]:
    """Lazy-load and cache one of the SEC ticker indexes. Returns
    {TICKER_UPPER: CIK_PADDED10} mapping. Empty dict on failure."""
    if kind in _TICKER_INDEX_CACHE:
        return _TICKER_INDEX_CACHE[kind]
    try:
        import requests
        if kind == "legacy":
            url = "https://www.sec.gov/files/company_tickers.json"
        elif kind == "exchange":
            url = "https://www.sec.gov/files/company_tickers_exchange.json"
        else:
            return {}
        _take_token()
        resp = requests.get(
            url, timeout=_TIMEOUT_SEC,
            headers={"User-Agent": _user_agent()},
        )
        if resp.status_code != 200:
            logger.info("SEC ticker index %s returned %d", kind, resp.status_code)
            _TICKER_INDEX_CACHE[kind] = {}
            return {}
        data = resp.json()
    except Exception as exc:
        logger.info("SEC ticker index %s fetch failed: %s", kind, exc)
        _TICKER_INDEX_CACHE[kind] = {}
        return {}

    out: dict[str, str] = {}
    if kind == "legacy":
        # Legacy format: {"0": {"ticker": "X", "cik_str": N, ...}, ...}
        if isinstance(data, dict):
            for _key, rec in data.items():
                if not isinstance(rec, dict):
                    continue
                t = (rec.get("ticker") or "").upper()
                cik = rec.get("cik_str")
                if t and isinstance(cik, int):
                    out[t] = f"{cik:010d}"
    elif kind == "exchange":
        # Newer format: {"fields": [...], "data": [[cik, name, ticker, exchange], ...]}
        if isinstance(data, dict):
            fields = data.get("fields", []) or []
            try:
                ti = fields.index("ticker")
                ci = fields.index("cik")
            except ValueError:
                _TICKER_INDEX_CACHE[kind] = {}
                return {}
            for row in data.get("data", []) or []:
                if not isinstance(row, list) or len(row) <= max(ti, ci):
                    continue
                t = str(row[ti] or "").upper()
                cik = row[ci]
                if t and isinstance(cik, int):
                    # Don't overwrite legacy entry — exchange index is
                    # supplementary.
                    out.setdefault(t, f"{cik:010d}")
    _TICKER_INDEX_CACHE[kind] = out
    return out


def _resolve_cik(ticker: str) -> Optional[str]:
    """Resolve ticker → 10-digit zero-padded CIK. Tries legacy index
    first (broadest), then exchange index (more recent ETFs).
    Returns None if neither index has the ticker."""
    tk = ticker.upper()
    legacy = _load_ticker_index("legacy")
    if tk in legacy:
        return legacy[tk]
    exchange = _load_ticker_index("exchange")
    if tk in exchange:
        return exchange[tk]
    return None


# ─── companyfacts resolver ───────────────────────────────────────────────────

# Priority order: try us-gaap NetAssets first (cleanest fund-level AUM
# fact), fall back to broader Assets, then ETF-specific keys, then the
# spot-trust niche key. First non-empty wins.
_PRIORITY_FACT_KEYS: tuple[tuple[str, str], ...] = (
    ("us-gaap", "NetAssets"),
    ("us-gaap", "InvestmentCompanyNetAssets"),
    ("invest",  "InvestmentCompanyNetAssets"),
    ("us-gaap", "Assets"),
    ("us-gaap", "AssetsFairValueDisclosure"),
)


def _fetch_companyfacts(cik: str) -> Optional[dict]:
    """Fetch and cache the XBRL companyfacts JSON for one CIK. Module
    cache survives across calls within a process (typical companyfacts
    JSON is 50-500 KB)."""
    if cik in _COMPANYFACTS_CACHE:
        return _COMPANYFACTS_CACHE[cik]
    try:
        import requests
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        _take_token()
        resp = requests.get(
            url, timeout=_TIMEOUT_SEC,
            headers={"User-Agent": _user_agent()},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        # Bound the cache.
        if len(_COMPANYFACTS_CACHE) >= _COMPANYFACTS_CACHE_MAX:
            # Drop one arbitrary entry — our access pattern is sequential
            # so any eviction policy is fine.
            _COMPANYFACTS_CACHE.pop(next(iter(_COMPANYFACTS_CACHE)))
        _COMPANYFACTS_CACHE[cik] = data
        return data
    except Exception as exc:
        logger.info("EDGAR companyfacts fetch failed for CIK %s: %s", cik, exc)
        return None


def _latest_usd_value(fact: dict) -> Optional[float]:
    """Take the most-recent USD-denominated entry from one XBRL fact's
    `units.USD` list. Returns the `val` field. None if missing."""
    units = (fact.get("units") or {}).get("USD") or []
    if not units:
        return None
    # Each entry has 'end' (YYYY-MM-DD), 'val', 'form', etc. Sort by
    # end-date desc and take the first valid value.
    sorted_units = sorted(
        units,
        key=lambda u: u.get("end", ""),
        reverse=True,
    )
    for u in sorted_units:
        v = u.get("val")
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def get_etf_aum_via_facts(ticker: str) -> Optional[float]:
    """
    Resolve ETF AUM via SEC XBRL company-facts API.

    Returns the most-recent reported USD net-assets value (or assets,
    if NetAssets isn't reported), in USD. None if:
      - ticker not in SEC ticker indexes
      - companyfacts JSON unreadable
      - none of the priority fact keys present

    Side effects: caches SEC ticker indexes + per-CIK companyfacts
    response at the module level for the process lifetime.

    Sanity-bound 1e6..1e12 to reject parse errors / accidental cents-
    valued entries / outlier filings.
    """
    if not ticker:
        return None
    cik = _resolve_cik(ticker)
    if not cik:
        return None
    facts_doc = _fetch_companyfacts(cik)
    if not facts_doc:
        return None
    facts = facts_doc.get("facts") or {}
    for ns, key in _PRIORITY_FACT_KEYS:
        ns_facts = facts.get(ns) or {}
        fact = ns_facts.get(key)
        if not isinstance(fact, dict):
            continue
        v = _latest_usd_value(fact)
        if v is None:
            continue
        if v < 1e6 or v > 1e12:
            continue
        return v
    return None
