"""
scripts/smoke_test_extractors.py — Sprint 2.6 commit 0 (2026-04-30).

Cowork's amendment #1: probe each candidate source ONCE before writing
extractors against it. Reports HTTP status, body length, presence of
expected AUM / Vol / Flow text snippets, and detected anti-bot
challenges (Cloudflare, JS-render gates, 403s).

Decides which extractors are buildable in Sprint 2.6 vs deferred to
Sprint 2.7 with Playwright.

Probe targets (one URL per source):
  1. ETF.com fund page for IBIT
  2. Bitwise fund page for BITB
  3. BlackRock iShares fund page for IBIT
  4. Grayscale fund page for GBTC
  5. ProShares fund page for BITO
  6. Fidelity fund page for FBTC
  7. Franklin Templeton fund page for EZBC

Output: simple stdout report. NO writes to repo state. NO follow-up
fetches. NO scraping logic written here — just probe + report.

Usage:
    python scripts/smoke_test_extractors.py
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from typing import Optional

try:
    import requests
except ImportError:
    print("requests not installed — pip install requests")
    sys.exit(1)


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT_SEC = 15


@dataclass
class ProbeTarget:
    source_name: str
    ticker: str
    url: str
    aum_hints: tuple[str, ...] = ("AUM", "Net Assets", "Total Net Assets",
                                  "Fund Net Assets", "Assets Under Management")
    vol_hints: tuple[str, ...] = ("Avg", "Average", "Daily Volume",
                                  "30-Day Avg")


TARGETS: list[ProbeTarget] = [
    ProbeTarget(
        "ETF.com",
        "IBIT",
        "https://www.etf.com/IBIT",
    ),
    ProbeTarget(
        "Bitwise",
        "BITB",
        "https://bitwiseinvestments.com/crypto-funds/bitb",
    ),
    ProbeTarget(
        "BlackRock iShares",
        "IBIT",
        "https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf",
    ),
    ProbeTarget(
        # NOTE: alt-URL discovery (commit 0 follow-up). The original
        # https://www.grayscale.com/funds/<slug> returns 404 for an
        # `requests.get` UA. The product detail pages live on the
        # ETF subdomain `etfs.grayscale.com/<TICKER>` which serves
        # full HTML with AUM text in the body.
        "Grayscale",
        "GBTC",
        "https://etfs.grayscale.com/gbtc",
    ),
    ProbeTarget(
        "ProShares",
        "BITO",
        "https://www.proshares.com/our-etfs/strategic/bito",
    ),
    ProbeTarget(
        "Fidelity",
        "FBTC",
        "https://www.fidelity.com/etfs/fbtc",
    ),
    ProbeTarget(
        "Franklin Templeton",
        "EZBC",
        "https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/39639/SINGLCLASS/franklin-bitcoin-etf/EZBC",
    ),
]


def detect_block(status: int, body: str) -> Optional[str]:
    """Return a label if the response looks like an anti-bot block."""
    if status == 403:
        return "403 Forbidden"
    if status == 429:
        return "429 Too Many Requests"
    if status == 503 and "cloudflare" in body.lower():
        return "503 Cloudflare challenge"
    if "Just a moment" in body and "cloudflare" in body.lower():
        return "Cloudflare interstitial"
    if "captcha" in body.lower() and len(body) < 5000:
        return "CAPTCHA gate"
    if status == 200 and len(body) < 500:
        return f"Empty body ({len(body)} chars)"
    return None


def detect_js_render(body: str) -> bool:
    """Crude heuristic: small body + no fund data text + lots of script
    tags suggests JS-rendered SPA."""
    if len(body) > 50_000:
        return False
    script_count = body.count("<script")
    has_fund_text = any(
        h.lower() in body.lower()
        for h in ("AUM", "Net Assets", "Daily Volume", "Total Assets")
    )
    return script_count > 5 and not has_fund_text


def probe_one(t: ProbeTarget) -> dict:
    """Single probe. Returns a dict; prints nothing."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = requests.get(t.url, headers=headers, timeout=TIMEOUT_SEC,
                         allow_redirects=True)
        body = r.text
        status = r.status_code
    except requests.exceptions.RequestException as exc:
        return {
            "source": t.source_name, "ticker": t.ticker, "url": t.url,
            "status": "ERROR", "body_len": 0,
            "block": f"requests.exception: {type(exc).__name__}",
            "js_render_suspected": False,
            "aum_text_present": False, "vol_text_present": False,
            "extractor_buildable": False,
        }

    block = detect_block(status, body)
    js_render = detect_js_render(body) if not block else False
    aum_present = any(re.search(re.escape(h), body, re.IGNORECASE) for h in t.aum_hints)
    vol_present = any(re.search(re.escape(h), body, re.IGNORECASE) for h in t.vol_hints)
    buildable = (status == 200 and not block and not js_render and aum_present)
    return {
        "source": t.source_name, "ticker": t.ticker, "url": t.url,
        "status": status, "body_len": len(body),
        "block": block or "—",
        "js_render_suspected": js_render,
        "aum_text_present": aum_present,
        "vol_text_present": vol_present,
        "extractor_buildable": buildable,
    }


def main() -> None:
    print("=" * 100)
    print("Sprint 2.6 commit 0 — extractor smoke-test probe")
    print("Per Cowork amendment #1: probe each candidate source ONCE before")
    print("writing extractors against it. Decide buildable vs defer-to-2.7.")
    print("=" * 100)

    results: list[dict] = []
    for i, t in enumerate(TARGETS):
        print(f"\n[{i+1}/{len(TARGETS)}] Probing {t.source_name} for {t.ticker} ...")
        print(f"    URL: {t.url}")
        r = probe_one(t)
        results.append(r)
        print(f"    Status: {r['status']}  |  Body: {r['body_len']:,} chars")
        print(f"    Block: {r['block']}")
        print(f"    JS-render suspected: {r['js_render_suspected']}")
        print(f"    AUM text present: {r['aum_text_present']}  |  Vol text present: {r['vol_text_present']}")
        print(f"    => buildable in 2.6: {r['extractor_buildable']}")
        # 1 second between probes (be polite)
        if i < len(TARGETS) - 1:
            time.sleep(1.0)

    # Summary table
    print()
    print("=" * 100)
    print("SUMMARY — extractor buildability decision")
    print("=" * 100)
    print(f"{'Source':<22} {'Status':<10} {'Body':<12} {'Block':<25} {'AUM?':<6} {'Build':<6}")
    print("-" * 100)
    for r in results:
        body_str = f"{r['body_len']:,}" if r['body_len'] else "0"
        print(f"{r['source']:<22} {str(r['status']):<10} {body_str:<12} {r['block']:<25} "
              f"{'Y' if r['aum_text_present'] else 'N':<6} {'Y' if r['extractor_buildable'] else 'N':<6}")

    buildable = [r for r in results if r["extractor_buildable"]]
    skip = [r for r in results if not r["extractor_buildable"]]
    print()
    print(f"BUILDABLE in Sprint 2.6 ({len(buildable)}): {[r['source'] for r in buildable]}")
    print(f"DEFER to 2.7 / Playwright ({len(skip)}): {[(r['source'], r['block'] if r['block'] != '—' else 'js-render') for r in skip]}")


if __name__ == "__main__":
    main()
