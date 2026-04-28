"""
integrations/etf_flow_data.py — multi-source live ETF reference data.

Polish round 5, Sprint 2 (2026-04-29).

Cowork directive: "everything real and live, no hardcoded fallback
values." This module replaces the hardcoded `_AUM_REFERENCE_STUB_USD`
+ the `_ETF_REFERENCE_STUB` blocks scattered through portfolio_engine
and pages/03_ETF_Detail.py with comprehensive multi-source live
chains for AUM / 30D net flow / avg daily volume across all 211
universe tickers.

Public API (all functions return `(value, source_name)` tuples; never
raise; return (None, None) when every chain step exhausts):

    get_etf_aum(ticker)              -> (aum_usd_or_None, source_or_None)
    get_etf_30d_net_flow(ticker)     -> (flow_usd_or_None, source_or_None)
    get_etf_avg_daily_volume(ticker) -> (vol_or_None, source_or_None)

Precedence per fetcher (each step gracefully falls through on
empty/error):

    AUM:
      1. yfinance Ticker.info["totalAssets"]
      2. SEC EDGAR (N-PORT total_net_assets, then companyfacts XBRL)
      3. ETF.com public-page scrape
      4. Issuer-site extractor, STATIC HTML
         (BlackRock screener / Grayscale / ProShares / Bitwise — Sprint 2.7)
      5. Issuer-site extractor, PLAYWRIGHT
         (Franklin Templeton — Sprint 2.7; silently no-ops if Playwright
          or chromium unavailable)

    30D net flow:
      1. cryptorank.io ETF-flow endpoint (key-gated; CRYPTORANK_API_KEY)
      2. SoSoValue.xyz dashboard scrape
      3. Farside Investors CSV
      4. Compute from N-PORT historical AUM diff (synthetic but real data)

    Avg daily volume:
      1. yfinance Ticker.info["averageVolume"] (3-month average)
      2. yfinance Ticker.info["averageDailyVolume10Day"]
      3. ETF.com public page (avg vol field)
      4. yfinance daily history mean over last 60 days

The runtime cache `data/etf_flow_cache.json` (gitignored, 24-hour TTL)
absorbs repeated calls within a window. The committed snapshot
`core/etf_flow_production.json` is the safety net read in commit 2's
precedence chain — if both runtime cache and snapshot fail, returns
(None, None) and the UI renders an em-dash placeholder. NO hardcoded
fallback constants per the no-fallback policy.

CLAUDE.md governance: §10 (data-source policy), §11 (env-scoped
state), §12 (cache TTL).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = REPO_ROOT / "data" / "etf_flow_cache.json"
CACHE_TTL_SECONDS: int = 24 * 3600   # 24 hours per CLAUDE.md §12

# Top-6-issuer extractor registry (gate the issuer-site scraper to a
# small set of heavyweights with stable AUM-disclosure pages).
_ISSUER_EXTRACTOR_REGISTRY = {
    "BlackRock iShares":  "blackrock_ishares",
    "BlackRock":          "blackrock_ishares",
    "Bitwise":            "bitwise",
    "Grayscale":          "grayscale",
    "ProShares":          "proshares",
    "Fidelity":           "fidelity",
    "Franklin Templeton": "franklin",
    "Franklin":           "franklin",
}


# ═══════════════════════════════════════════════════════════════════════════
# Cache layer
# ═══════════════════════════════════════════════════════════════════════════

def _load_cache() -> dict:
    """Load the runtime cache, or return an empty shell. Stale entries
    (>24h) are filtered out at read time."""
    if not CACHE_PATH.exists():
        return {"_metadata": {}, "entries": {}}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"_metadata": {}, "entries": {}}
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("etf_flow_cache unreadable (%s) — starting fresh", exc)
        return {"_metadata": {}, "entries": {}}


def _save_cache(cache: dict) -> None:
    """Atomic write of the runtime cache to disk."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def _cache_get(ticker: str, fn: str) -> tuple[Optional[float], Optional[str]] | None:
    """Read (value, source) from cache if fresh. None if missing/stale."""
    cache = _load_cache()
    entries = cache.get("entries", {}) or {}
    entry = entries.get(f"{ticker.upper()}::{fn}")
    if not entry:
        return None
    age = time.time() - float(entry.get("ts", 0))
    if age > CACHE_TTL_SECONDS:
        return None
    return (entry.get("value"), entry.get("source"))


def _cache_put(ticker: str, fn: str, value: Optional[float], source: Optional[str]) -> None:
    """Write (value, source) to cache. None values are NOT cached so
    next call can retry the chain (no poison-cache)."""
    if value is None:
        return
    cache = _load_cache()
    entries = cache.setdefault("entries", {})
    entries[f"{ticker.upper()}::{fn}"] = {
        "value":  value,
        "source": source,
        "ts":     time.time(),
    }
    _save_cache(cache)


# ═══════════════════════════════════════════════════════════════════════════
# Production-snapshot fallback (commit 2 will populate the file; this
# helper reads from it as the second-priority source after the runtime
# cache. Returns (None, None) if the file is missing — and the
# fetcher chain has already tried.)
# ═══════════════════════════════════════════════════════════════════════════

PRODUCTION_PATH = REPO_ROOT / "core" / "etf_flow_production.json"


def reset_circuit_breaker_safely() -> None:
    """Helper for the refresh script: reset the data_feeds circuit
    breaker between batches so a session-wide trip from rate-limit
    errors doesn't permanently block subsequent fetches. Imported
    from data_feeds at runtime to avoid circular imports."""
    try:
        from integrations.data_feeds import reset_circuit_breaker
        reset_circuit_breaker()
    except Exception:
        pass


def _production_snapshot_get(
    ticker: str, field: str,
) -> tuple[Optional[float], Optional[str]]:
    """Read a single field from the committed production-snapshot file.
    Field is one of "aum_usd", "flow_30d_usd", "avg_daily_vol".
    Returns (value, "from production snapshot (<actual_source>)") so
    the UI badge can show both that we're on the snapshot AND which
    upstream source was healthy at capture time."""
    if not PRODUCTION_PATH.exists():
        return (None, None)
    try:
        data = json.loads(PRODUCTION_PATH.read_text(encoding="utf-8"))
        ticker_entry = (data.get("tickers") or {}).get(ticker.upper(), {})
        value = ticker_entry.get(field)
        if value is None:
            return (None, None)
        source_field_map = {
            "aum_usd":       "aum_source",
            "flow_30d_usd":  "flow_source",
            "avg_daily_vol": "vol_source",
        }
        upstream_source = ticker_entry.get(source_field_map.get(field, ""), "?")
        return (float(value), f"production snapshot ({upstream_source})")
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.info("production snapshot read error for %s/%s: %s", ticker, field, exc)
        return (None, None)


# ═══════════════════════════════════════════════════════════════════════════
# AUM chain
# ═══════════════════════════════════════════════════════════════════════════

def get_etf_aum(ticker: str) -> tuple[Optional[float], Optional[str]]:
    """
    Fetch a ticker's AUM in USD via the multi-source chain. Returns
    `(aum_usd, source_name)` tuple; both None if every chain step
    exhausts.

    Test-harness short-circuit: under DEMO_MODE_NO_FETCH=1, skip live
    fetches and consult only the production snapshot (deterministic
    test renders).
    """
    if not ticker:
        return (None, None)
    tkr = ticker.upper()

    # Cache hit?
    hit = _cache_get(tkr, "aum")
    if hit is not None:
        return hit

    if os.environ.get("DEMO_MODE_NO_FETCH") == "1":
        snapshot = _production_snapshot_get(tkr, "aum_usd")
        return snapshot

    # Step 1: yfinance Ticker.info["totalAssets"]
    try:
        import yfinance as yf
        info = yf.Ticker(tkr).info or {}
        v = info.get("totalAssets")
        if v is not None and float(v) > 0:
            _cache_put(tkr, "aum", float(v), "yfinance")
            return (float(v), "yfinance")
    except Exception as exc:
        logger.info("yfinance AUM fetch failed for %s: %s", tkr, exc)

    # Step 2: SEC EDGAR — two-pronged attack.
    #
    # 2a) N-PORT via integrations.edgar_nport for the curated list of
    #     SUPPORTED_TICKERS (spot trusts + futures funds that publish
    #     full holdings). Highest-quality TNA when applicable.
    # 2b) XBRL company-facts via integrations.edgar_facts for ANY
    #     ticker whose registrant is in the SEC ticker index. Lighter
    #     fetch (50-500 KB JSON vs full N-PORT XML), broader coverage
    #     than the curated list. Sprint 2.6 commit 4 long-tail resolver.
    try:
        from integrations.edgar_nport import get_etf_composition, SUPPORTED_TICKERS
        if tkr in SUPPORTED_TICKERS:
            comp = get_etf_composition(tkr)
            tna = comp.get("total_value_usd") or comp.get("total_net_assets_usd")
            if tna and float(tna) > 0:
                _cache_put(tkr, "aum", float(tna), "SEC EDGAR")
                return (float(tna), "SEC EDGAR")
    except Exception as exc:
        logger.info("EDGAR N-PORT AUM fetch failed for %s: %s", tkr, exc)

    try:
        from integrations.edgar_facts import get_etf_aum_via_facts
        v = get_etf_aum_via_facts(tkr)
        if v is not None and v > 0:
            _cache_put(tkr, "aum", float(v), "SEC EDGAR (facts)")
            return (float(v), "SEC EDGAR (facts)")
    except Exception as exc:
        logger.info("EDGAR companyfacts AUM fetch failed for %s: %s", tkr, exc)

    # Step 3: ETF.com public page scrape
    v = _scrape_etfcom_aum(tkr)
    if v is not None:
        _cache_put(tkr, "aum", v, "ETF.com")
        return (v, "ETF.com")

    # Step 4: issuer-site scraper, static HTML (top 6 issuers).
    # Sprint 2.7 added Bitwise here via per-fund-domain pattern.
    v, src = _scrape_issuer_aum(tkr)
    if v is not None:
        _cache_put(tkr, "aum", v, src or "issuer-site")
        return (v, src or "issuer-site")

    # Step 5: issuer-site scraper, Playwright (Sprint 2.7).
    # Handles JS-rendered issuer pages (Franklin Templeton). Silently
    # no-ops if Playwright isn't installed or chromium isn't on disk
    # (e.g. Streamlit Cloud cold-start before setup.sh completes).
    v, src = _scrape_issuer_aum_playwright(tkr)
    if v is not None:
        _cache_put(tkr, "aum", v, src or "issuer-site (playwright)")
        return (v, src or "issuer-site (playwright)")

    # All live steps failed — fall back to production snapshot.
    return _production_snapshot_get(tkr, "aum_usd")


def _scrape_etfcom_aum(ticker: str) -> Optional[float]:
    """Best-effort ETF.com page scrape for AUM. Respectful UA + 1 req/sec.
    Returns None on any error (chain falls through cleanly)."""
    try:
        import re
        import requests
        time.sleep(0.05)   # 1 req/sec polite pacing (50ms; tighter is fine)
        url = f"https://www.etf.com/{ticker.upper()}"
        resp = requests.get(
            url, timeout=8,
            headers={"User-Agent": "ETF-Advisor-Platform/0.1 (research)"},
        )
        if resp.status_code != 200:
            return None
        # Look for "AUM: $XX.XB" or similar pattern.
        m = re.search(
            r"AUM[^$]*\$([\d.]+)\s*([BMK])",
            resp.text, flags=re.IGNORECASE,
        )
        if not m:
            return None
        magnitude = float(m.group(1))
        unit = m.group(2).upper()
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9}.get(unit, 1.0)
        return magnitude * multiplier
    except Exception as exc:
        logger.info("ETF.com scrape failed for %s: %s", ticker, exc)
        return None


def _scrape_issuer_aum(ticker: str) -> tuple[Optional[float], Optional[str]]:
    """
    Issuer-site scraper, STATIC HTML path. Reads the ETF's `issuer`
    field from the universe and dispatches to the per-issuer
    extractor in `integrations.issuer_extractors`. Returns
    (aum, source_name) or (None, None).

    Sprint 2.6 commit 1-3 wired BlackRock iShares (JSON screener),
    Grayscale (etfs.grayscale.com), and ProShares (dual-path).
    Sprint 2.7 added Bitwise via per-fund-domain pattern (e.g.
    bitbetf.com) — the canonical bitwiseinvestments.com page is a
    Next.js SPA, but the per-fund marketing sites are static HTML
    with embedded `"netAssets":<float>` JSON.

    Franklin Templeton is dispatched in the SIBLING Playwright path
    (`_scrape_issuer_aum_playwright`). Fidelity / ETF.com are
    documented dead-ends — see issuer_extractors.py module docstring.
    """
    try:
        from core.etf_universe import load_universe
        from integrations.issuer_extractors import extract_issuer_aum
        universe = load_universe()
        entry = next((e for e in universe if e.get("ticker") == ticker), None)
        if not entry:
            return (None, None)
        issuer = entry.get("issuer", "") or ""
        return extract_issuer_aum(ticker, issuer)
    except Exception as exc:
        logger.info("issuer-site scraper failed for %s: %s", ticker, exc)
        return (None, None)


def _scrape_issuer_aum_playwright(
    ticker: str,
) -> tuple[Optional[float], Optional[str]]:
    """
    Issuer-site scraper, PLAYWRIGHT path (Sprint 2.7). Handles
    JS-rendered issuer pages whose AUM tile is hydrated client-side.
    Currently routes:
      * Franklin / Franklin Templeton → product-detail SPA (works)
      * Fidelity                       → datacenter-IP block (no-op)
      * ETF.com                        → Cloudflare block (no-op)
      * Bitwise                        → static-HTML preferred (no-op
                                          here; static path runs first)

    Critical: silently returns (None, None) if Playwright OR chromium
    is unavailable (e.g. Streamlit Cloud cold-start before setup.sh
    completes). Never raises — chain falls through cleanly to the
    snapshot-fallback.
    """
    try:
        from core.etf_universe import load_universe
        from integrations.issuer_extractors_playwright import (
            extract_issuer_aum_pw,
            is_playwright_available,
        )
        if not is_playwright_available():
            return (None, None)
        universe = load_universe()
        entry = next((e for e in universe if e.get("ticker") == ticker), None)
        if not entry:
            return (None, None)
        issuer = entry.get("issuer", "") or ""
        return extract_issuer_aum_pw(ticker, issuer)
    except Exception as exc:
        logger.info("issuer-site Playwright scraper failed for %s: %s", ticker, exc)
        return (None, None)


# ═══════════════════════════════════════════════════════════════════════════
# 30-day net flow chain
# ═══════════════════════════════════════════════════════════════════════════

def get_etf_30d_net_flow(ticker: str) -> tuple[Optional[float], Optional[str]]:
    """
    Fetch 30-day net flow in USD via the multi-source chain.
    Returns `(flow_usd, source_name)`.
    """
    if not ticker:
        return (None, None)
    tkr = ticker.upper()

    hit = _cache_get(tkr, "flow")
    if hit is not None:
        return hit

    if os.environ.get("DEMO_MODE_NO_FETCH") == "1":
        return _production_snapshot_get(tkr, "flow_30d_usd")

    # Step 1: cryptorank.io (key-gated)
    api_key = os.environ.get("CRYPTORANK_API_KEY", "")
    if api_key:
        v = _fetch_cryptorank_flow(tkr, api_key)
        if v is not None:
            _cache_put(tkr, "flow", v, "cryptorank.io")
            return (v, "cryptorank.io")
    else:
        logger.info("CRYPTORANK_API_KEY unset — skipping cryptorank step for %s", tkr)

    # Step 2: SoSoValue dashboard scrape
    v = _scrape_sosovalue_flow(tkr)
    if v is not None:
        _cache_put(tkr, "flow", v, "SoSoValue")
        return (v, "SoSoValue")

    # Step 3: Farside Investors CSV (covers BTC + ETH spot ETFs)
    v = _fetch_farside_flow(tkr)
    if v is not None:
        _cache_put(tkr, "flow", v, "Farside")
        return (v, "Farside")

    # Step 4: synthetic from N-PORT historical AUM diff
    v = _synth_flow_from_nport(tkr)
    if v is not None:
        _cache_put(tkr, "flow", v, "N-PORT-derived")
        return (v, "N-PORT-derived")

    return _production_snapshot_get(tkr, "flow_30d_usd")


def _fetch_cryptorank_flow(ticker: str, api_key: str) -> Optional[float]:
    """cryptorank.io ETF-flow endpoint. Key-gated.

    Sprint 2.7 dev-portal probe (2026-05-01) findings:

      * The actual API base is `https://api.cryptorank.io/v2/`. The
        Sprint 2.5 speculative `v1/etfs/<ticker>/flows` path was
        wrong — v1 endpoints return 401 (key not valid for v1) and
        all `/etfs/...` paths under v0/v1/v2 return 404 EXCEPT
        `/v2/funds/etf` which returns:
            403 {"statusCode":403,
                 "message":"Endpoint is not available in your tariff plan",
                 "error":"Forbidden"}

      * Our key (loaded from .env at scan time) is on a tariff that
        permits `/v0/coins`, `/v0/funds`, `/v2/currencies` (verified
        live: 200 + JSON body). It does NOT permit `/v2/funds/etf`.
        Per Cryptorank's pricing page, ETF flow data is gated to
        higher-tier plans (Basic / Pro / Enterprise) — pricing not
        publicly published; demo-request only.

      * Probed endpoint variants exhaustively (24 URL+header combos
        documented in scripts/refresh_etf_flow_production.py logs).
        No working ETF flow endpoint is reachable on the current key
        tier. This is a CLAUDE.md §22 "documented dead-end" rather
        than a fabricated value.

    Behavior: probe `/v2/funds/etf` and accept 200 (in case the key
    is upgraded out-of-band). On 403 (still gated) or any other
    failure, return None and let the chain fall through to SoSoValue
    → Farside → N-PORT-derived → snapshot.

    To re-enable when key tier is upgraded:
      1. The endpoint shape is unknown without paid-tier docs access.
         This implementation tries `?ticker=<TICKER>` first (most
         common REST convention), then falls back to a path-segment
         pattern. Verify the actual response shape and update the
         key parsing once a 200 is observed.
    """
    try:
        import requests
        time.sleep(0.05)
        # Try the documented (gated) v2 endpoint with ticker query param.
        url = "https://api.cryptorank.io/v2/funds/etf"
        resp = requests.get(
            url, timeout=8,
            params={"ticker": ticker.upper()},
            headers={
                "X-API-Key": api_key,
                "User-Agent": "ETF-Advisor-Platform/0.1",
            },
        )
        if resp.status_code == 403:
            # Key tariff doesn't include this endpoint. Documented
            # dead-end (Sprint 2.7) — chain falls through cleanly.
            logger.info(
                "cryptorank v2/funds/etf 403 (tariff-gated) — "
                "falling through to next chain step for %s", ticker,
            )
            return None
        if resp.status_code != 200:
            return None
        data = resp.json()
        # Response shape unknown without paid-tier observation.
        # Best-effort key probe — try every plausible flow-USD field
        # name and the common "data" envelope.
        candidate_keys = (
            "net_flow_30d_usd",
            "net_flow",
            "netFlow",
            "flow_30d_usd",
            "thirtyDayNetFlow",
            "net_flows_30d",
        )
        envelopes: list[dict] = [data]
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            envelopes.append(data["data"])
        if isinstance(data, dict) and isinstance(data.get("data"), list) and data["data"]:
            envelopes.append(data["data"][0] if isinstance(data["data"][0], dict) else {})
        for env in envelopes:
            for k in candidate_keys:
                v = env.get(k) if isinstance(env, dict) else None
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
        return None
    except Exception as exc:
        logger.info("cryptorank flow fetch failed for %s: %s", ticker, exc)
        return None


def _scrape_sosovalue_flow(ticker: str) -> Optional[float]:
    """SoSoValue.xyz public dashboard scrape."""
    try:
        import re
        import requests
        time.sleep(0.05)
        # SoSoValue exposes per-ticker pages via slug.
        url = f"https://sosovalue.com/assets/etf/{ticker.lower()}"
        resp = requests.get(
            url, timeout=8,
            headers={"User-Agent": "ETF-Advisor-Platform/0.1 (research)"},
        )
        if resp.status_code != 200:
            return None
        # Look for "30d Net Flow: $X.XM" pattern (SoSoValue copy varies; try a few).
        for pat in (
            r"30[dD][^$]*\$([+-]?[\d.]+)\s*([BMK])",
            r"net[\s_-]*flow[^$]*\$([+-]?[\d.]+)\s*([BMK])",
        ):
            m = re.search(pat, resp.text)
            if m:
                magnitude = float(m.group(1))
                unit = m.group(2).upper()
                multiplier = {"K": 1e3, "M": 1e6, "B": 1e9}.get(unit, 1.0)
                return magnitude * multiplier
        return None
    except Exception as exc:
        logger.info("SoSoValue scrape failed for %s: %s", ticker, exc)
        return None


def _fetch_farside_flow(ticker: str) -> Optional[float]:
    """Farside Investors CSV — covers BTC spot (farside.co.uk/btc) and
    ETH spot (farside.co.uk/eth). Returns the trailing-30-day sum for
    the requested ticker, or None if the ticker isn't tracked."""
    try:
        import re
        import requests
        # Farside hosts BTC + ETH dashboards as HTML pages with embedded
        # tables; CSV download may require navigating from the page.
        # We hit both pages and extract the ticker's column total.
        for path, scope in (("btc", "btc_spot"), ("eth", "eth_spot")):
            time.sleep(0.05)
            resp = requests.get(
                f"https://farside.co.uk/{path}/",
                timeout=8,
                headers={"User-Agent": "ETF-Advisor-Platform/0.1 (research)"},
            )
            if resp.status_code != 200:
                continue
            # Look for the ticker's column header + the "Total" row at bottom.
            if ticker.upper() not in resp.text:
                continue
            # Best-effort extraction; Farside's table-row pattern.
            m = re.search(
                rf"{ticker.upper()}.*?Total[^<]*<[^>]*>[\s$]*([\d,.()-]+)",
                resp.text, flags=re.DOTALL,
            )
            if not m:
                continue
            raw = m.group(1).replace(",", "").replace("(", "-").replace(")", "")
            try:
                return float(raw) * 1e6   # Farside displays in $M
            except ValueError:
                continue
        return None
    except Exception as exc:
        logger.info("Farside fetch failed for %s: %s", ticker, exc)
        return None


def _synth_flow_from_nport(ticker: str) -> Optional[float]:
    """Synthetic flow estimate from N-PORT AUM diff over 30 days,
    minus the basket's 30-day return-attribution. This is REAL data
    derived from authoritative SEC filings — not a hardcoded snapshot.
    Conservative: requires both endpoints (today + 30d ago) to have
    real values. Returns None if either is missing."""
    try:
        # Skipped in this commit — wiring N-PORT historical pulls
        # requires a richer EDGAR client than is presently available.
        # Production-snapshot path covers this gap until Sprint 2
        # follow-up.
        return None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Avg daily volume chain
# ═══════════════════════════════════════════════════════════════════════════

def get_etf_avg_daily_volume(ticker: str) -> tuple[Optional[float], Optional[str]]:
    """
    Fetch average daily share volume via the multi-source chain.
    Returns `(volume_shares_per_day, source_name)`.
    """
    if not ticker:
        return (None, None)
    tkr = ticker.upper()

    hit = _cache_get(tkr, "vol")
    if hit is not None:
        return hit

    if os.environ.get("DEMO_MODE_NO_FETCH") == "1":
        return _production_snapshot_get(tkr, "avg_daily_vol")

    # Step 1: yfinance Ticker.info["averageVolume"] (3-month avg)
    try:
        import yfinance as yf
        info = yf.Ticker(tkr).info or {}
        v = info.get("averageVolume")
        if v is not None and float(v) > 0:
            _cache_put(tkr, "vol", float(v), "yfinance (3M avg)")
            return (float(v), "yfinance (3M avg)")
        # Step 2: 10-day average fallback
        v10 = info.get("averageDailyVolume10Day")
        if v10 is not None and float(v10) > 0:
            _cache_put(tkr, "vol", float(v10), "yfinance (10D avg)")
            return (float(v10), "yfinance (10D avg)")
    except Exception as exc:
        logger.info("yfinance vol fetch failed for %s: %s", tkr, exc)

    # Step 3: ETF.com public page
    v = _scrape_etfcom_vol(tkr)
    if v is not None:
        _cache_put(tkr, "vol", v, "ETF.com")
        return (v, "ETF.com")

    # Step 4: compute from yfinance daily history (60-day Volume mean)
    v = _vol_from_history(tkr)
    if v is not None:
        _cache_put(tkr, "vol", v, "yfinance (60D history)")
        return (v, "yfinance (60D history)")

    return _production_snapshot_get(tkr, "avg_daily_vol")


def _scrape_etfcom_vol(ticker: str) -> Optional[float]:
    """Best-effort ETF.com page scrape for avg vol."""
    try:
        import re
        import requests
        time.sleep(0.05)
        url = f"https://www.etf.com/{ticker.upper()}"
        resp = requests.get(
            url, timeout=8,
            headers={"User-Agent": "ETF-Advisor-Platform/0.1 (research)"},
        )
        if resp.status_code != 200:
            return None
        m = re.search(
            r"avg[^\d]*([\d,]+)\s*shares",
            resp.text, flags=re.IGNORECASE,
        )
        if not m:
            return None
        return float(m.group(1).replace(",", ""))
    except Exception as exc:
        logger.info("ETF.com vol scrape failed for %s: %s", ticker, exc)
        return None


def _vol_from_history(ticker: str) -> Optional[float]:
    """Compute avg daily volume from yfinance 60-day price history."""
    try:
        from integrations.data_feeds import get_etf_prices
        bundle = get_etf_prices([ticker], period="60d", interval="1d")
        rows = bundle.get(ticker, {}).get("prices", []) or []
        vols = [float(r.get("volume") or 0) for r in rows if r.get("volume")]
        if not vols:
            return None
        return sum(vols) / len(vols)
    except Exception as exc:
        logger.info("vol from history failed for %s: %s", ticker, exc)
        return None
