"""
integrations/issuer_extractors_playwright.py — Sprint 2.7 (2026-05-01).

Playwright-based AUM extractors for issuer product pages that are
JS-rendered (Franklin Templeton) or otherwise unreachable to plain
`requests.get` (which Sprint 2.6 commit 0's smoke-test confirmed —
see scripts/smoke_test_extractors.py docstring).

This module is a SIBLING to integrations/issuer_extractors.py:
  * issuer_extractors.py        → static-HTML extractors (BlackRock
                                   screener JSON, Grayscale regex,
                                   ProShares dual-path, Bitwise per-
                                   fund domains added Sprint 2.7).
  * issuer_extractors_playwright.py (this file) → JS-render extractors.

Architecture choices:
  * Sync API (`playwright.sync_api`), NOT async — keeps it compatible
    with Streamlit's sync execution model and the rest of the codebase
    (no asyncio event-loop coordination, no awaiting from sync code).
  * Module-level `_browser` singleton lazy-init via `_get_browser()`.
    Reusing one chromium across calls in a process amortizes the
    ~3-4s launch cost across all extractor invocations during a
    capture run (211 tickers × 4 issuers worst-case = ~50 calls).
  * Each extractor opens its own `context.new_page()`, so cookies/
    state don't bleed between funds. Pages are closed after use.
  * `is_playwright_available()` is the chain's gate: returns True iff
    the python module imports AND a chromium binary exists on disk.
    On Streamlit Cloud cold-start where setup.sh hasn't run yet,
    returns False → chain silently no-ops, never raises.
  * NO retries. A failing fetch returns None → AUM chain falls through
    to step 4 (issuer-site static) → snapshot-fallback → em-dash. The
    "fail loud, retry never" pattern keeps a stalled Streamlit Cloud
    instance from blocking on chromium launch hangs.

Sprint 2.7 confirmed-buildable via Playwright:
  * Franklin Templeton — long product URL, AUM tile via
                          'Total Net Assets' + sibling <dd>.

Sprint 2.7 documented dead-ends (via Playwright also blocked):
  * Fidelity — ERR_HTTP2_PROTOCOL_ERROR / connection-reset
               from datacenter IPs. Headers/UA tweaks didn't help.
               Stub returns None until residential-proxy infra
               (post-demo, paid).
  * ETF.com  — Cloudflare turnstile interstitial blocks at edge
               regardless of UA. 403 + "Just a moment..." HTML.
               Stub returns None until paid scrape infra.

CLAUDE.md governance: §10 (multi-source), §22 (no-fallback honesty —
Playwright failure is None, never a fabricated value).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Sanity bounds for any AUM value parsed from any source: between $1M
# and $1T. Anything outside is a parse error / unrelated dollar
# amount on the page (e.g. a NAV per share quoted as $42.18).
_AUM_MIN_USD = 1e6
_AUM_MAX_USD = 1e12

_NAV_TIMEOUT_MS = 30_000     # page.goto deadline
_HYDRATE_WAIT_MS = 5_000     # extra wait for JS-hydrated tiles

# Module-level singleton state. Set by _get_browser(); cleared by
# _close_browser() at process teardown (or explicitly by tests).
_PW_CONTEXT_STATE: dict = {"playwright": None, "browser": None}


# ═══════════════════════════════════════════════════════════════════════════
# Availability probe (chain gate)
# ═══════════════════════════════════════════════════════════════════════════

def is_playwright_available() -> bool:
    """True iff the playwright python module imports AND a chromium
    browser binary is reachable on disk. Chain callers gate on this
    BEFORE attempting any Playwright extractor — on a cold Streamlit
    Cloud deploy where setup.sh hasn't completed, this returns False
    and the chain silently falls through to the next step.

    Cheap: only imports playwright (already on disk) and stat()s the
    ms-playwright cache directory. Does not actually launch chromium."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    # Best-effort binary-on-disk probe. Playwright stores chromium
    # under either ~/AppData/Local/ms-playwright (Win) or
    # ~/.cache/ms-playwright (Linux). We don't require an exact
    # version — just any chromium-* directory.
    candidates = [
        Path.home() / "AppData" / "Local" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
        Path("/ms-playwright"),                  # docker convention
        Path("/opt/ms-playwright"),
    ]
    for d in candidates:
        try:
            if d.exists() and any(p.name.startswith("chromium-") for p in d.iterdir()):
                return True
        except (OSError, PermissionError):
            continue
    return False


# ═══════════════════════════════════════════════════════════════════════════
# Browser singleton
# ═══════════════════════════════════════════════════════════════════════════

def _get_browser():
    """Lazy-initialize the module-level chromium singleton. Returns the
    Browser handle (or None on launch failure). Subsequent calls reuse.

    Sync — Streamlit is sync. Each extractor opens its own context+page
    off this shared browser, then closes both."""
    if _PW_CONTEXT_STATE.get("browser") is not None:
        return _PW_CONTEXT_STATE["browser"]
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        _PW_CONTEXT_STATE["playwright"] = pw
        _PW_CONTEXT_STATE["browser"] = browser
        logger.info("Playwright chromium browser launched (singleton)")
        return browser
    except Exception as exc:
        logger.info("Playwright launch failed: %s", exc)
        return None


def _close_browser() -> None:
    """Tear down the module-level singleton. Called by tests; capture
    scripts let process exit clean it up."""
    browser = _PW_CONTEXT_STATE.get("browser")
    pw = _PW_CONTEXT_STATE.get("playwright")
    try:
        if browser is not None:
            browser.close()
    except Exception:
        pass
    try:
        if pw is not None:
            pw.stop()
    except Exception:
        pass
    _PW_CONTEXT_STATE["browser"] = None
    _PW_CONTEXT_STATE["playwright"] = None


# ═══════════════════════════════════════════════════════════════════════════
# Extraction helpers
# ═══════════════════════════════════════════════════════════════════════════

def _parse_money(s: str) -> Optional[float]:
    """Parse strings like '$491.45 Million', '$2,979,872,605', '$11.8B',
    '$1.2 Trillion' → float USD. Returns None on parse failure or if
    value falls outside _AUM_MIN_USD..MAX."""
    if not s:
        return None
    s = s.strip()
    # 1) raw integer/decimal "$2,979,872,605" → 2979872605.0
    m_raw = re.fullmatch(r"\$?([\d,]+(?:\.\d+)?)", s)
    if m_raw:
        try:
            v = float(m_raw.group(1).replace(",", ""))
            if _AUM_MIN_USD <= v <= _AUM_MAX_USD:
                return v
        except ValueError:
            pass
    # 2) shorthand "$491.45 M" / "$11.8B" / "$1.2K"
    # 3) full-word "$491.45 Million" / "$11.8 Billion" / "$1.2 Trillion"
    #    Both shapes coalesced into one regex — the unit token is
    #    either a single B/M/K letter OR the full word billion/million/
    #    thousand/trillion. Word-suffixes case-insensitive.
    m_short = re.match(
        r"\$?\s*([\d.,]+)\s*"
        r"(thousand|million|billion|trillion|[BMKT])\b",
        s, flags=re.I,
    )
    if m_short:
        try:
            magnitude = float(m_short.group(1).replace(",", ""))
            unit = m_short.group(2).upper()
            multiplier = {
                "K": 1e3, "THOUSAND": 1e3,
                "M": 1e6, "MILLION":  1e6,
                "B": 1e9, "BILLION":  1e9,
                "T": 1e12, "TRILLION": 1e12,
            }.get(unit)
            if multiplier is None:
                return None
            v = magnitude * multiplier
            if _AUM_MIN_USD <= v <= _AUM_MAX_USD:
                return v
        except (ValueError, KeyError):
            pass
    return None


def _fetch_with_playwright(url: str, hydrate_ms: int = _HYDRATE_WAIT_MS) -> Optional[str]:
    """Open a fresh page, navigate, wait for JS hydration, return HTML.
    Returns None on any failure (closes the page either way).
    Caller is responsible for the regex extraction."""
    browser = _get_browser()
    if browser is None:
        return None
    page = None
    try:
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()
        page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        page.wait_for_timeout(hydrate_ms)
        body = page.content()
        ctx.close()
        return body
    except Exception as exc:
        logger.info("Playwright fetch failed for %s: %s", url, exc)
        try:
            if page is not None:
                page.close()
        except Exception:
            pass
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Issuer extractors
# ═══════════════════════════════════════════════════════════════════════════

# Franklin Templeton: hardcoded slug+id per ticker. Franklin's URL
# scheme requires a numeric product ID + class slug + readable slug,
# all of which are discovered by scraping the ETF index page
# (https://www.franklintempleton.com/investments/options/exchange-
# traded-funds). The mapping below was harvested 2026-05-01 from that
# index. New Franklin crypto ETFs added later fall through to None
# gracefully — re-scrape the index and add an entry to update.
_FRANKLIN_URLS: dict[str, str] = {
    "EZBC": (
        "https://www.franklintempleton.com/investments/options/"
        "exchange-traded-funds/products/39639/SINGLCLASS/"
        "franklin-bitcoin-etf/EZBC"
    ),
    "EZET": (
        "https://www.franklintempleton.com/investments/options/"
        "exchange-traded-funds/products/40521/SINGLCLASS/"
        "franklin-ethereum-etf/EZET"
    ),
    "EZPZ": (
        # 2026 — Franklin Crypto Index ETF (multi-asset basket).
        "https://www.franklintempleton.com/investments/options/"
        "exchange-traded-funds/products/41786/SINGLCLASS/"
        "franklin-crypto-index-etf/EZPZ"
    ),
}


def extract_franklin_aum_pw(ticker: str) -> Optional[float]:
    """Franklin Templeton AUM via Playwright.

    Page structure (verified 2026-05-01 on EZBC):
      <span ...>Total Net Assets</span> ... <dd class="text-right">$491.45 M</dd>

    Returns AUM USD or None on any failure."""
    if not is_playwright_available():
        return None
    url = _FRANKLIN_URLS.get(ticker.upper())
    if url is None:
        return None
    body = _fetch_with_playwright(url)
    if not body:
        return None
    # Look for the AUM value in the <dd> sibling immediately after a
    # "Total Net Assets" label. Tolerate intermediate footnote markup
    # (e.g. <frk-footnote>) by allowing arbitrary content up to 800
    # chars between the label and the <dd>. The captured value can
    # be raw integer ($2,979,872,605), shorthand ($11.8B), or full-
    # word ($491.45 Million) — _parse_money handles all three shapes.
    m = re.search(
        r"Total Net Assets.{0,800}?<dd[^>]*>\s*([^<]+?)\s*</dd>",
        body, flags=re.I | re.S,
    )
    if not m:
        return None
    return _parse_money(m.group(1))


def extract_fidelity_aum_pw(ticker: str) -> Optional[float]:
    """Fidelity AUM — DOCUMENTED DEAD-END.

    Sprint 2.7 probe found:
      * https://www.fidelity.com/etfs/<ticker> → ERR_HTTP2_PROTOCOL_ERROR
        from chromium (datacenter IP block; not a UA issue).
      * https://digital.fidelity.com/.../dashboard/summary?symbol=<ticker>
        → also ERR_HTTP2_PROTOCOL_ERROR.
      * https://eresearch.fidelity.com/.../snapshot.jhtml?symbols=<ticker>
        → ERR_CONNECTION_RESET / Timeout.

    Playwright with custom UA, http2-disabled args, and 60s timeout
    all hit the same protocol error. This is consistent with
    Fidelity's well-known datacenter-IP filtering; only residential
    proxies (paid) work.

    Stub kept so the dispatcher in `etf_flow_data._scrape_issuer_aum`
    has a uniform interface. Returns None — chain falls through.

    Re-enable when residential-proxy infra is wired (post-demo)."""
    return None


def extract_etfcom_aum_pw(ticker: str) -> Optional[float]:
    """ETF.com AUM — DOCUMENTED DEAD-END.

    Sprint 2.7 probe (2026-05-01) confirmed via Playwright that ETF.com
    serves a Cloudflare turnstile interstitial:
      * Page status: 403
      * Body length: 31,550 chars
      * Title: "Just a moment..."
      * Body contains 'challenges.cloudflare.com' script-src

    Static `requests.get` was already 403'd in Sprint 2.6 commit 0;
    Playwright (which reads turnstile JS) doesn't auto-solve the
    challenge. Defeating Cloudflare requires either:
      * residential-proxy + browser-fingerprinting infra (paid), or
      * the official ETF.com paid API.

    Stub kept for dispatcher symmetry. Returns None."""
    return None


def extract_bitwise_aum_pw(ticker: str) -> Optional[float]:
    """Bitwise — Playwright stub maintained for API symmetry, but
    Sprint 2.7 found that Bitwise is reachable via STATIC HTML on
    per-fund domains (e.g. bitbetf.com). The static-HTML path lives
    in integrations/issuer_extractors.extract_bitwise_aum and runs
    BEFORE this Playwright stub in the chain. This stub returns None
    so the chain continues if both paths somehow get exercised."""
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Public dispatcher (used by integrations.etf_flow_data)
# ═══════════════════════════════════════════════════════════════════════════

# Issuer field (from universe entry) → Playwright extractor callable.
# The chain in etf_flow_data._scrape_issuer_aum consults this AFTER
# the static-HTML dispatcher in issuer_extractors.extract_issuer_aum.
_PW_DISPATCH = {
    "Bitwise":            extract_bitwise_aum_pw,
    "Fidelity":           extract_fidelity_aum_pw,
    "Franklin":           extract_franklin_aum_pw,
    "Franklin Templeton": extract_franklin_aum_pw,
}


def extract_issuer_aum_pw(
    ticker: str, issuer: str,
) -> tuple[Optional[float], Optional[str]]:
    """Dispatch a Playwright-driven AUM extraction. Returns
    (aum_usd, source_name) or (None, None). source_name is
    `issuer-site:<key> (playwright)` so the UI badge distinguishes
    JS-rendered captures from static-HTML ones.

    Used by integrations.etf_flow_data._scrape_issuer_aum as a fall-
    through to the static-HTML dispatcher (which handles BlackRock,
    Grayscale, ProShares, Bitwise).
    """
    if not is_playwright_available():
        return (None, None)
    fn = _PW_DISPATCH.get(issuer)
    if fn is None:
        return (None, None)
    try:
        v = fn(ticker)
    except Exception as exc:
        logger.info("Playwright extractor crashed for %s/%s: %s", ticker, issuer, exc)
        return (None, None)
    if v is None:
        return (None, None)
    label_key = {
        extract_franklin_aum_pw: "franklin",
        extract_fidelity_aum_pw: "fidelity",
        extract_etfcom_aum_pw:   "etfcom",
        extract_bitwise_aum_pw:  "bitwise",
    }.get(fn, "unknown")
    return (v, f"issuer-site:{label_key} (playwright)")


# ── Process exit hook ────────────────────────────────────────────────────────
# Audit-fix: chromium + playwright subprocess lifetimes are tied to this
# module's `_PW_CONTEXT_STATE`. Without atexit, a Streamlit Cloud worker
# eviction would orphan the chromium child process. Register graceful
# teardown so the OS reclaims subprocess resources cleanly.
import atexit as _atexit
_atexit.register(_close_browser)
