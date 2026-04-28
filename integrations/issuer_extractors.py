"""
integrations/issuer_extractors.py — per-issuer AUM extractors.

Polish round 5, Sprint 2.6 + 2.7. Static-HTML extractors:

  - BlackRock iShares  via product-screener JSON endpoint (single fetch
                        for all iShares funds; we filter by ticker)
  - Grayscale          via etfs.grayscale.com/<ticker> + regex
  - ProShares          via /our-etfs/strategic|leveraged-and-inverse
                        dual-path fall-through + regex
  - Bitwise            via per-fund domain pattern <ticker>etf.com +
                        embedded JSON {"netAssets": <float>}
                        (Sprint 2.7 — earlier Sprint 2.6 deferred this
                        to Playwright; the per-fund-domain pattern was
                        discovered during Sprint 2.7 probing and works
                        WITHOUT Playwright)

Deferred to Playwright path (integrations/issuer_extractors_playwright.py):
  - Franklin Templeton JS-rendered, AUM tile populated client-side

Documented dead-ends (even Playwright cannot reach):
  - Fidelity   ERR_HTTP2_PROTOCOL_ERROR / connection-reset
               from datacenter IPs (proxy required)
  - ETF.com    Cloudflare turnstile interstitial blocks at edge
               regardless of UA / browser fingerprint

Each extractor returns Optional[float] (AUM in USD) or None on any
failure. The `etf_flow_data.py` chain treats None as "fall through to
the next chain step" — graceful, no exceptions propagate.

Module-level cache: BlackRock screener returns 1.9 MB JSON for all
iShares funds in one fetch. Cached for the process lifetime to avoid
re-fetching for each ticker in a capture run.

CLAUDE.md governance: §10 (multi-source provenance), §22 (no-fallback
honesty), §12 (cache TTL — process-level cache here is finer than the
24h disk cache, which lives in etf_flow_data._cache_*).
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Browser-realistic UA. Same string used in the smoke test that
# established each source's reachability.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_TIMEOUT_SEC = 12

# Process-level cache for the BlackRock screener (1.9 MB JSON; one
# fetch covers every iShares ticker). Keyed by None — cleared by
# clearing the dict.
_BLACKROCK_SCREENER_CACHE: dict[str, dict] = {}


# ═══════════════════════════════════════════════════════════════════════════
# BlackRock iShares — product-screener JSON endpoint
# ═══════════════════════════════════════════════════════════════════════════

def _fetch_blackrock_screener() -> Optional[dict]:
    """One-shot fetch of the iShares product-screener JSON. Returns the
    parsed dict (~1700 funds, keyed by product ID) or None on failure.
    Cached for the process lifetime."""
    if "data" in _BLACKROCK_SCREENER_CACHE:
        return _BLACKROCK_SCREENER_CACHE["data"]
    try:
        import requests
        url = (
            "https://www.ishares.com/us/product-screener/"
            "product-screener-v3.1.jsn"
            "?dcrPath=/templatedata/config/product-screener-v3/data/"
            "en/us-ishares/ishares-product-screener-backend-config"
            "&siteEntryPassthrough=true"
        )
        resp = requests.get(
            url, timeout=_TIMEOUT_SEC,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        if resp.status_code != 200:
            logger.info("BlackRock screener returned %d", resp.status_code)
            return None
        data = json.loads(resp.text)
        if not isinstance(data, dict):
            return None
        _BLACKROCK_SCREENER_CACHE["data"] = data
        return data
    except Exception as exc:
        logger.info("BlackRock screener fetch failed: %s", exc)
        return None


def extract_blackrock_aum(ticker: str) -> Optional[float]:
    """Look up `ticker` in the iShares product-screener JSON and return
    the fund's totalNetAssets (raw USD float). None if not indexed in
    the screener (newly-listed crypto products often aren't yet)."""
    data = _fetch_blackrock_screener()
    if not data:
        return None
    tk = ticker.upper()
    for _pid, fund in data.items():
        if not isinstance(fund, dict):
            continue
        if fund.get("localExchangeTicker", "").upper() != tk:
            continue
        # Prefer fund-level total net assets; fall back to share-class.
        for key in ("totalNetAssetsFund", "totalNetAssets"):
            tna = fund.get(key, {})
            if isinstance(tna, dict):
                r = tna.get("r")
                if isinstance(r, (int, float)) and r > 0:
                    return float(r)
        return None
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Grayscale — etfs.grayscale.com/<ticker> + regex
# ═══════════════════════════════════════════════════════════════════════════

# Regex: find the FIRST "AUM" occurrence in the body and capture the
# next dollar amount that immediately follows it (with no `$` between).
# Validated against GBTC ($11.787B), BTC ($4.117B), ETHE ($1.905B),
# GDLC ($0.431B), GSOL ($0.108B), GXRP ($0.068B). Handles both the
# "GAAP AUM" variant (legacy trusts) and plain "AUM" (newer ETFs).
_GRAYSCALE_AUM_RE = re.compile(
    r"AUM[^$]*?\$([\d,]+(?:\.\d+)?)",
    flags=re.DOTALL,
)


def extract_grayscale_aum(ticker: str) -> Optional[float]:
    """Fetch etfs.grayscale.com/<ticker> and regex AUM. URL pattern
    misses for some legacy slugs (returns 404) — that's a graceful
    None and the chain falls through."""
    try:
        import requests
        url = f"https://etfs.grayscale.com/{ticker.lower()}"
        resp = requests.get(
            url, timeout=_TIMEOUT_SEC,
            headers={"User-Agent": _USER_AGENT},
        )
        if resp.status_code != 200:
            return None
        m = _GRAYSCALE_AUM_RE.search(resp.text)
        if not m:
            return None
        v = float(m.group(1).replace(",", ""))
        # Sanity bound: real ETF AUM is between $1M and $1T. Anything
        # outside that range is a parse error / unrelated dollar amount.
        if v < 1e6 or v > 1e12:
            return None
        return v
    except Exception as exc:
        logger.info("Grayscale extract failed for %s: %s", ticker, exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# ProShares — dual-path fall-through (strategic / leveraged-and-inverse)
# ═══════════════════════════════════════════════════════════════════════════

# Regex against ProShares' fund-snapshot tile:
#   <span id="snapshot-netAssets" class="...">$1,932,237,020</span>
_PROSHARES_AUM_RE = re.compile(
    r'snapshot-netAssets[^>]*>\s*\$([\d,]+(?:\.\d+)?)'
)

# Per-ticker probe found that ProShares routes funds under TWO
# category prefixes. Order matters: try the higher-coverage category
# first so cache hits are common.
_PROSHARES_CATEGORIES = ("strategic", "leveraged-and-inverse")


def extract_proshares_aum(ticker: str) -> Optional[float]:
    """Try ProShares' two URL paths in order. Returns AUM USD or None.

    Strategic path covers BITO, BETE, BETH, EETH (4 of our 11
    ProShares tickers). Leveraged-and-inverse path covers BITI,
    BITU, ETHD, ETHT, SBIT, SETH (6 more)."""
    try:
        import requests
        for i, category in enumerate(_PROSHARES_CATEGORIES):
            if i > 0:
                # Polite pacing between path attempts (only when we
                # actually fall through to a second URL).
                time.sleep(0.5)
            url = f"https://www.proshares.com/our-etfs/{category}/{ticker.lower()}"
            resp = requests.get(
                url, timeout=_TIMEOUT_SEC,
                headers={"User-Agent": _USER_AGENT},
            )
            if resp.status_code != 200:
                continue
            m = _PROSHARES_AUM_RE.search(resp.text)
            if not m:
                continue
            v = float(m.group(1).replace(",", ""))
            if v < 1e5 or v > 1e12:
                continue
            return v
        return None
    except Exception as exc:
        logger.info("ProShares extract failed for %s: %s", ticker, exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Bitwise — per-fund domain pattern (Sprint 2.7, 2026-05-01)
#
# Sprint 2.6 commit 0 deferred Bitwise to Playwright after probing
# the canonical /crypto-funds/<ticker> URL (which 404s with a Next.js
# error body). Sprint 2.7 probing found that Bitwise serves per-fund
# marketing sites at <ticker>etf.com (e.g. https://bitbetf.com/,
# https://bxrpetf.com/, https://bsoletf.com/). Each contains the
# fund AUM in two static-HTML forms:
#
#   1. Embedded JSON:    "netAssets":2979872605.13
#   2. Visible HTML:     <p class="...">$2,979,872,605</p>
#                        immediately after a <h4>Net Assets (AUM)</h4>
#
# We prefer (1) because it carries decimal precision and is less
# subject to copy-edit drift. (2) is a fallback regex if the JSON
# pattern shifts.
#
# Coverage probe (2026-05-01): 8 of 26 universe Bitwise tickers
# resolve via DNS (BITB, ETHW, BSOL, BXRP, BITQ, IMST, BITC, OWNB).
# The other 18 either DNS-fail (no <ticker>etf.com domain registered)
# or 404 / lack the JSON. New Bitwise tickers will progressively work
# as they register their per-fund domain.
# ═══════════════════════════════════════════════════════════════════════════

_BITWISE_NETASSETS_RE = re.compile(r'"netAssets"\s*:\s*([\d.]+)')
_BITWISE_HTML_RE = re.compile(
    r'Net Assets[^<]*</h4>\s*<p[^>]*>\$([\d,]+(?:\.\d+)?)',
    flags=re.I,
)


def extract_bitwise_aum(ticker: str) -> Optional[float]:
    """Bitwise per-fund domain extractor. Returns AUM USD or None.

    URL pattern: https://<ticker>etf.com/  (lowercase ticker).
    First fetch tries the JSON pattern; falls through to the HTML
    pattern if JSON is absent. Sanity-bound 1e6..1e12.
    """
    try:
        import requests
        url = f"https://{ticker.lower()}etf.com/"
        resp = requests.get(
            url, timeout=_TIMEOUT_SEC,
            headers={"User-Agent": _USER_AGENT},
        )
        if resp.status_code != 200:
            return None
        body = resp.text
        # Path 1: embedded JSON (preferred — decimal precision).
        m = _BITWISE_NETASSETS_RE.search(body)
        if m:
            try:
                v = float(m.group(1))
                if 1e6 <= v <= 1e12:
                    return v
            except ValueError:
                pass
        # Path 2: visible HTML (fallback — integer dollars).
        m2 = _BITWISE_HTML_RE.search(body)
        if m2:
            try:
                v = float(m2.group(1).replace(",", ""))
                if 1e6 <= v <= 1e12:
                    return v
            except ValueError:
                pass
        return None
    except Exception as exc:
        logger.info("Bitwise extract failed for %s: %s", ticker, exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Public dispatcher — used by integrations.etf_flow_data._scrape_issuer_aum
# ═══════════════════════════════════════════════════════════════════════════

# Issuer field (from the universe entry) → extractor callable.
# Issuers NOT in this map fall through to None — and the chain in
# etf_flow_data._scrape_issuer_aum then tries the Playwright dispatch
# in integrations/issuer_extractors_playwright.py.
_DISPATCH = {
    "BlackRock iShares":  extract_blackrock_aum,
    "BlackRock":          extract_blackrock_aum,
    "Grayscale":          extract_grayscale_aum,
    "ProShares":          extract_proshares_aum,
    "Bitwise":            extract_bitwise_aum,   # Sprint 2.7
    # Franklin / Franklin Templeton dispatched via the Playwright
    # sibling module (integrations/issuer_extractors_playwright.py).
    # Fidelity / ETF.com are documented dead-ends — no extractor here.
}


def extract_issuer_aum(ticker: str, issuer: str) -> tuple[Optional[float], Optional[str]]:
    """Dispatch a per-issuer AUM extraction. Returns (aum_usd, source_name)
    or (None, None). source_name is `issuer-site:<key>` so the UI
    badge shows correct provenance.

    Used by integrations.etf_flow_data._scrape_issuer_aum as step 4
    of the AUM chain.
    """
    fn = _DISPATCH.get(issuer)
    if fn is None:
        return (None, None)
    v = fn(ticker)
    if v is None:
        return (None, None)
    label_key = {
        extract_blackrock_aum: "blackrock_ishares",
        extract_grayscale_aum: "grayscale",
        extract_proshares_aum: "proshares",
        extract_bitwise_aum:   "bitwise",
    }.get(fn, "unknown")
    return (v, f"issuer-site:{label_key}")
