"""
etf_universe.py — crypto ETF universe loader + daily new-listing scanner.

Two responsibilities:
  1. load_universe() — expose the active ETF list (seed + scanner-added)
     augmented with the minimal fields portfolio_engine needs:
     expected_return, volatility, correlation_with_btc, expense_ratio_bps.
  2. daily_scanner() — query SEC EDGAR for new crypto-related fund filings
     (N-1A, 497, S-1) to catch newly-listed ETFs before they appear in
     our seed list.

Known-good behavior (documented per planning-side Mod 2):
  The SEC EDGAR full-text search index has a 24-48 HOUR LAG behind actual
  filing submission. A scanner run at 16:30 ET on day X WILL NOT catch
  filings submitted on day X. This is a characteristic of EDGAR's
  full-text indexer, not a bug in this module. Previous-day filings
  appear reliably.

Planning-side Mod 2 runtime guard:
  daily_scanner() raises RuntimeError immediately if EDGAR_CONTACT_EMAIL
  still equals the placeholder. SEC requires an identifiable User-Agent
  with contact email for all programmatic access.

CLAUDE.md governance: Sections 10 (data sources), 12 (refresh rates), 13 (universe).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from config import (
    EDGAR_CONTACT_EMAIL,
    EDGAR_REQS_PER_SEC,
    ETF_UNIVERSE_SEED,
)

logger = logging.getLogger(__name__)

# ── EDGAR endpoints ──────────────────────────────────────────────────────────
_EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_PLACEHOLDER = "REPLACE_BEFORE_DEPLOY@example.com"
_CRYPTO_KEYWORDS = (
    "cryptocurrency",
    "bitcoin",
    "ethereum",
    "digital asset",
    "spot crypto",
)
_CRYPTO_FORM_TYPES = ("N-1A", "497", "S-1")

# ── EDGAR token bucket (in-memory, simple) ───────────────────────────────────
_edgar_tokens: dict[str, Any] = {
    "tokens": float(EDGAR_REQS_PER_SEC),
    "last_refill": time.time(),
}


def _edgar_take_token() -> None:
    """Simple token-bucket limiter — blocks the caller if bucket is empty."""
    now = time.time()
    elapsed = now - _edgar_tokens["last_refill"]
    _edgar_tokens["tokens"] = min(
        float(EDGAR_REQS_PER_SEC),
        _edgar_tokens["tokens"] + elapsed * EDGAR_REQS_PER_SEC,
    )
    _edgar_tokens["last_refill"] = now

    while _edgar_tokens["tokens"] < 1.0:
        sleep_for = (1.0 - _edgar_tokens["tokens"]) / EDGAR_REQS_PER_SEC
        time.sleep(sleep_for)
        now2 = time.time()
        elapsed = now2 - _edgar_tokens["last_refill"]
        _edgar_tokens["tokens"] = min(
            float(EDGAR_REQS_PER_SEC),
            _edgar_tokens["tokens"] + elapsed * EDGAR_REQS_PER_SEC,
        )
        _edgar_tokens["last_refill"] = now2
    _edgar_tokens["tokens"] -= 1.0


def _edgar_user_agent() -> str:
    """SEC-policy-compliant User-Agent header."""
    return f"ETF-Advisor-Platform {EDGAR_CONTACT_EMAIL}"


def _assert_edgar_configured() -> None:
    """Planning-side Mod 2 runtime guard — fail loudly on placeholder email."""
    if EDGAR_CONTACT_EMAIL == _EDGAR_PLACEHOLDER or not EDGAR_CONTACT_EMAIL.strip():
        raise RuntimeError(
            "EDGAR_CONTACT_EMAIL is still the placeholder "
            f"({EDGAR_CONTACT_EMAIL!r}). SEC requires an identifiable contact "
            "email in the User-Agent header. Update config.py before running "
            "daily_scanner() against live EDGAR."
        )


# ═══════════════════════════════════════════════════════════════════════════
# Default analytic fields for portfolio_engine
# ═══════════════════════════════════════════════════════════════════════════

# Phase-1 default analytics per ETF category. These are first-pass values
# used when real price/return history hasn't been computed yet. Day 3+
# computes these live from yfinance OHLCV.
_CATEGORY_DEFAULTS: dict[str, dict[str, float]] = {
    "btc_spot":    {"expected_return": 35.0, "volatility": 55.0, "correlation_with_btc": 0.98},
    "eth_spot":    {"expected_return": 40.0, "volatility": 70.0, "correlation_with_btc": 0.78},
    "btc_futures": {"expected_return": 28.0, "volatility": 58.0, "correlation_with_btc": 0.95},
    "thematic":    {"expected_return": 45.0, "volatility": 75.0, "correlation_with_btc": 0.70},
}

# Expense ratios (bps) sourced from public issuer pages at time of writing.
# Exact values will be refreshed on Day 2+ once data_feeds.get_etf_reference
# is live. Used here only to break ties in issuer-diversity selection.
_EXPENSE_RATIO_BPS: dict[str, float] = {
    "IBIT": 25,  "FBTC": 25,  "BITB": 20,  "ARKB": 21,  "BTCO": 25,
    "EZBC": 19,  "BRRR": 25,  "HODL": 20,  "BTC":  15,  "GBTC": 150, "DEFI": 95,
    "ETHA": 25,  "FETH": 25,  "ETHE": 250, "ETHW": 20,  "CETH": 21,
    "QETH": 25,  "EZET": 19,  "ETH":  15,
}


def _enrich(etf: dict[str, Any]) -> dict[str, Any]:
    """Attach Phase-1 analytic defaults + expense ratio if present."""
    defaults = _CATEGORY_DEFAULTS.get(etf.get("category", ""), _CATEGORY_DEFAULTS["btc_spot"])
    return {
        **etf,
        "expected_return":       defaults["expected_return"],
        "volatility":            defaults["volatility"],
        "correlation_with_btc":  defaults["correlation_with_btc"],
        "expense_ratio_bps":     _EXPENSE_RATIO_BPS.get(etf["ticker"]),
    }


def load_universe(scanner_additions: list[dict] | None = None) -> list[dict]:
    """
    Return the active ETF universe = seed + scanner-added, each entry
    enriched with Phase-1 analytic defaults. Day-3 live fetches overwrite.
    """
    base = [_enrich(e) for e in ETF_UNIVERSE_SEED]
    if scanner_additions:
        existing = {e["ticker"] for e in base}
        for new in scanner_additions:
            if new.get("ticker") and new["ticker"] not in existing:
                base.append(_enrich(new))
    return base


# ═══════════════════════════════════════════════════════════════════════════
# Daily scanner — SEC EDGAR new-filing query
# ═══════════════════════════════════════════════════════════════════════════

def daily_scanner(days_back: int = 7) -> list[dict]:
    """
    Query EDGAR full-text search for crypto-related fund filings submitted
    in the last `days_back` days. Raises if EDGAR_CONTACT_EMAIL is still
    the placeholder. Returns a list of filing metadata dicts; enrichment
    into full ETF-universe entries is the caller's job.

    Planning-side Mod 2: EDGAR's full-text index has a 24-48 HOUR LAG.
    A scanner run at 16:30 ET on day X WILL NOT catch same-day filings.
    Set `days_back` ≥ 2 to be robust against this lag.

    Returns list of dicts with keys: filing_date, form_type, filer_cik,
    filer_name, accession_number, matched_keywords, raw_match_text.
    """
    _assert_edgar_configured()

    import requests

    headers = {
        "User-Agent": _edgar_user_agent(),
        "Accept": "application/json",
    }

    matches: list[dict] = []
    for keyword in _CRYPTO_KEYWORDS:
        for form in _CRYPTO_FORM_TYPES:
            _edgar_take_token()
            params = {
                "q":     f'"{keyword}"',
                "forms": form,
                "dateRange": "custom",
                # EDGAR accepts startdt / enddt in YYYY-MM-DD
                "startdt": _date_n_days_ago(days_back),
                "enddt":   _date_today(),
            }
            try:
                resp = requests.get(
                    _EDGAR_SEARCH_URL,
                    params=params,
                    headers=headers,
                    timeout=10,
                )
                if resp.status_code == 429:
                    logger.warning("EDGAR 429 — backing off 5s")
                    time.sleep(5)
                    continue
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("EDGAR query failed (%s / %s): %s", keyword, form, exc)
                continue

            hits = data.get("hits", {}).get("hits", [])
            for hit in hits:
                source = hit.get("_source", {})
                matches.append({
                    "filing_date":       source.get("file_date", ""),
                    "form_type":         form,
                    "filer_cik":         source.get("ciks", [""])[0] if source.get("ciks") else "",
                    "filer_name":        (source.get("display_names", [""]) or [""])[0],
                    "accession_number":  source.get("adsh", ""),
                    "matched_keywords":  [keyword],
                    "raw_match_text":    (hit.get("highlight", {}).get("display_names", [])
                                          or [""])[0],
                })

    # De-duplicate by accession_number, merging matched_keywords lists
    dedup: dict[str, dict] = {}
    for m in matches:
        key = m["accession_number"] or f"{m['filer_cik']}:{m['filing_date']}:{m['form_type']}"
        if key in dedup:
            merged_kw = list(set(dedup[key]["matched_keywords"] + m["matched_keywords"]))
            dedup[key]["matched_keywords"] = merged_kw
        else:
            dedup[key] = m

    return list(dedup.values())


def _date_today() -> str:
    from datetime import date
    return date.today().isoformat()


def _date_n_days_ago(n: int) -> str:
    from datetime import date, timedelta
    return (date.today() - timedelta(days=max(1, n))).isoformat()
