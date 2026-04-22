"""
edgar.py — shared SEC EDGAR access primitives.

Centralizes:
  - SEC-compliant User-Agent construction (contact email from config.py)
  - Global 10 req/sec token bucket (shared across every EDGAR caller)
  - Retry on 429 with exponential backoff
  - Ticker → CIK lookup (7-day cache via JSON file)
  - CIK → recent filings list

Every EDGAR caller (core.etf_universe::daily_scanner, integrations.edgar_nport,
integrations.data_feeds::get_etf_reference) goes through this module so the
10 req/sec SEC cap is honored globally, not per-caller.

Day-4 Risk 1 + 7 mitigation.

CLAUDE.md governance: Section 10 (data sources), 12 (refresh rates).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from config import DATA_DIR, EDGAR_CONTACT_EMAIL, EDGAR_REQS_PER_SEC

logger = logging.getLogger(__name__)

_EDGAR_PLACEHOLDER = "REPLACE_BEFORE_DEPLOY@example.com"
_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_CIK_CACHE_PATH: Path = DATA_DIR / "edgar_cik_cache.json"
_CIK_CACHE_TTL_SEC = 7 * 24 * 3600   # 7 days

# ═══════════════════════════════════════════════════════════════════════════
# Runtime guard
# ═══════════════════════════════════════════════════════════════════════════

def assert_edgar_configured() -> None:
    if EDGAR_CONTACT_EMAIL == _EDGAR_PLACEHOLDER or not EDGAR_CONTACT_EMAIL.strip():
        raise RuntimeError(
            "EDGAR_CONTACT_EMAIL is still the placeholder "
            f"({EDGAR_CONTACT_EMAIL!r}). Set EDGAR_CONTACT_EMAIL in .env or "
            "edit config.py before any programmatic EDGAR access."
        )


def user_agent() -> str:
    """SEC-policy-compliant User-Agent used on every EDGAR request."""
    return f"ETF-Advisor-Platform {EDGAR_CONTACT_EMAIL}"


# ═══════════════════════════════════════════════════════════════════════════
# Shared token bucket — 10 req/sec SEC hard cap
# ═══════════════════════════════════════════════════════════════════════════

_bucket_lock = threading.Lock()
_bucket_state: dict[str, float] = {
    "tokens": float(EDGAR_REQS_PER_SEC),
    "last_refill": time.monotonic(),
}


def take_token() -> None:
    """
    Block the caller until a token is available. Refill rate =
    EDGAR_REQS_PER_SEC tokens/sec up to a max of EDGAR_REQS_PER_SEC.

    Implementation: take the lock briefly to read + decrement the token
    counter. If empty, compute the wait time, release the lock, sleep
    outside the critical section, then retry. This avoids the busy-wait
    under lock that the earlier implementation had, which would burn
    CPU for up to 100ms per throttled request and block all other
    threads from checking the bucket during that window.
    """
    while True:
        with _bucket_lock:
            now = time.monotonic()
            elapsed = now - _bucket_state["last_refill"]
            _bucket_state["tokens"] = min(
                float(EDGAR_REQS_PER_SEC),
                _bucket_state["tokens"] + elapsed * EDGAR_REQS_PER_SEC,
            )
            _bucket_state["last_refill"] = now
            if _bucket_state["tokens"] >= 1.0:
                _bucket_state["tokens"] -= 1.0
                return
            deficit = 1.0 - _bucket_state["tokens"]
            sleep_for = deficit / EDGAR_REQS_PER_SEC
        # Sleep OUTSIDE the lock so other threads can check the bucket
        # meanwhile. Cap the wait at 1s to avoid unresponsive hangs if
        # the system clock jumps backwards (monotonic() guards against
        # that already, but defense in depth).
        time.sleep(min(max(sleep_for, 0.001), 1.0))


# ═══════════════════════════════════════════════════════════════════════════
# HTTP request wrapper — User-Agent + 429 retry + token bucket
# ═══════════════════════════════════════════════════════════════════════════

def edgar_get(url: str, params: dict | None = None, timeout: int = 10,
              accept: str = "application/json") -> Any:
    """
    Issue a rate-limited GET to EDGAR. Returns the `requests.Response` object.
    Retries on 429 with exponential backoff (2s, 4s, 8s — max 3 tries).
    Raises for connection errors after final retry.
    """
    assert_edgar_configured()
    import requests
    headers = {
        "User-Agent": user_agent(),
        "Accept": accept,
    }
    last_exc: Exception | None = None
    for attempt in range(3):
        take_token()
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("EDGAR 429 on %s — sleeping %ds", url, wait)
                time.sleep(wait)
                continue
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("EDGAR request failed (%s): %s", url, exc)
            time.sleep(1 + attempt)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"EDGAR GET {url} failed after 3 attempts (likely 429).")


# ═══════════════════════════════════════════════════════════════════════════
# Ticker → CIK lookup (7-day disk cache)
# ═══════════════════════════════════════════════════════════════════════════

def _load_cik_cache() -> dict[str, str]:
    if not _CIK_CACHE_PATH.exists():
        return {}
    try:
        with open(_CIK_CACHE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        age = time.time() - data.get("_ts", 0)
        if age > _CIK_CACHE_TTL_SEC:
            return {}
        return data.get("ticker_to_cik", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cik_cache(mapping: dict[str, str]) -> None:
    _CIK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"_ts": time.time(), "ticker_to_cik": mapping}
    import os
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8",
        dir=str(_CIK_CACHE_PATH.parent),
        prefix=".tmp_cik_", suffix=".json", delete=False,
    ) as tf:
        json.dump(payload, tf)
        tmp_name = tf.name
    for attempt in range(5):
        try:
            os.replace(tmp_name, str(_CIK_CACHE_PATH))
            return
        except PermissionError:
            time.sleep(0.1 * (attempt + 1))
    try:
        os.remove(tmp_name)
    except OSError:
        pass


def get_cik_for_ticker(ticker: str) -> str | None:
    """
    Return the zero-padded 10-digit CIK for a US-listed ticker, or None
    if not found. Caches the full ticker→CIK map for 7 days per SEC
    convention — this endpoint serves ~9000 tickers and is refreshed
    nightly by SEC.
    """
    cache = _load_cik_cache()
    key = ticker.upper()
    if key in cache:
        return cache[key]

    try:
        resp = edgar_get(_COMPANY_TICKERS_URL)
        resp.raise_for_status()
        data = resp.json()
        # Format: {"0": {"cik_str": 1234567, "ticker": "IBIT", "title": "..."}, ...}
        mapping: dict[str, str] = {}
        for entry in data.values():
            t = str(entry.get("ticker", "")).upper()
            cik = entry.get("cik_str")
            if t and cik is not None:
                mapping[t] = f"{int(cik):010d}"
        _save_cik_cache(mapping)
        return mapping.get(key)
    except Exception as exc:
        logger.warning("EDGAR CIK lookup failed for %s: %s", ticker, exc)
        return cache.get(key)   # fallback to stale cache if available


# ═══════════════════════════════════════════════════════════════════════════
# Recent filings list for a CIK
# ═══════════════════════════════════════════════════════════════════════════

def get_recent_filings(
    cik: str,
    form_types: tuple[str, ...] = ("NPORT-P", "NPORT-EX"),
    max_rows: int = 20,
) -> list[dict]:
    """
    Return a list of recent filings for the given CIK, filtered to the
    requested form_types. Each entry: {accession, form, filing_date,
    primary_document, primary_doc_url}.

    IMPORTANT: SEC EDGAR's submissions.json uses specific form-name
    strings; the generic label "N-PORT" is NOT one of them. Use:
      - NPORT-P  (monthly portfolio holdings, public — what we want)
      - NPORT-EX (exempt variant)
      - NPORT-NP (non-public)
      - 10-K, 10-Q, 8-K, S-1, N-1A, 19b-4, 497 — all exact
    """
    cik_padded = cik.lstrip("0").zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    try:
        resp = edgar_get(url)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("EDGAR recent-filings fetch failed (%s): %s", cik, exc)
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])

    results: list[dict] = []
    for i, form in enumerate(forms):
        if form not in form_types:
            continue
        acc = accessions[i] if i < len(accessions) else ""
        date = dates[i] if i < len(dates) else ""
        primary = primary_docs[i] if i < len(primary_docs) else ""
        acc_noDash = acc.replace("-", "")
        primary_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik_padded)}/{acc_noDash}/{primary}"
        )
        results.append({
            "accession":        acc,
            "form":             form,
            "filing_date":      date,
            "primary_document": primary,
            "primary_doc_url":  primary_url,
        })
        if len(results) >= max_rows:
            break
    return results
