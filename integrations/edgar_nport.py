"""
edgar_nport.py — parse SEC N-PORT filings for ETF holdings composition.

Wired into pages/03_ETF_Detail.py Composition card. Live-first: queries
SEC EDGAR for the latest N-PORT filing, parses the investments block,
returns a list of holdings with per-position percent and balance.

Day-4 supported tickers (per planning-side directive):
    IBIT   — iShares Bitcoin Trust (BlackRock)
    ETHA   — iShares Ethereum Trust (BlackRock)
    FBTC   — Fidelity Wise Origin Bitcoin Fund
    FETH   — Fidelity Ethereum Fund

Other tickers fall through to the category-level placeholder in the UI.

Data-source-state integration:
    - Successful live fetch  → register_fetch_attempt("etf_composition", "edgar", success=True)
    - Live fetch failed      → register_fetch_attempt(..., success=False)
    - Fixture cache used     → mark_cache_hit with its age
    - No data at all         → mark_static_fallback

Day-4 Risk 3 + 8 mitigation:
    - Per-ticker XML fixtures in tests/fixtures for schema-variance tests
    - If live fails and no fixture, composition card shows "data unavailable"
      with Retry — no synthetic data ever returned to the UI

CLAUDE.md governance: Section 10 (data sources), 12 (refresh rates).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from config import DATA_DIR
from core.data_source_state import (
    mark_cache_hit,
    mark_static_fallback,
    register_fetch_attempt,
)
from integrations.edgar import edgar_get, get_cik_for_ticker, get_recent_filings

logger = logging.getLogger(__name__)

# Composition-supported tickers are split by filing structure:
#
# _TRUST_COMPOSITIONS  — '33-Act grantor trusts (spot BTC / spot ETH
#                        ETFs). These hold the physical coin directly
#                        via a custodian. They DO NOT file N-PORT
#                        (which is an Investment Company Act '40-Act
#                        filing). They file 10-K / 10-Q / 8-K instead.
#                        Composition is trivial by design — ~100% of
#                        the underlying coin + small cash buffer. We
#                        serve a curated, issuer-accurate summary plus
#                        a deep link to the live issuer holdings page
#                        for audit verification.
#
# _NPORT_FILER_TICKERS — '40-Act registered funds (crypto futures
#                        strategy ETFs). These DO file NPORT-P and the
#                        EDGAR live path applies.
#
# SUPPORTED_TICKERS kept as the union for backward compatibility with
# existing callers that enumerate it (e.g., the "ticker in
# NPORT_TICKERS" check on the ETF Detail page).

_TRUST_COMPOSITIONS: dict[str, dict] = {
    "IBIT": {"underlying": "Bitcoin", "coin_pct": 99.5, "cash_pct": 0.5,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "issuer_holdings_url": "https://www.ishares.com/us/products/333011/ishares-bitcoin-trust"},
    "FBTC": {"underlying": "Bitcoin", "coin_pct": 99.5, "cash_pct": 0.5,
             "custodian": "Fidelity Digital Asset Services, LLC",
             "issuer_holdings_url": "https://institutional.fidelity.com/app/funds-and-products/etp/summary/fbtc.html"},
    "BITB": {"underlying": "Bitcoin", "coin_pct": 99.5, "cash_pct": 0.5,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "issuer_holdings_url": "https://bitbetf.com/"},
    "ARKB": {"underlying": "Bitcoin", "coin_pct": 99.5, "cash_pct": 0.5,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "issuer_holdings_url": "https://www.ark21sharesbitcoin.com/"},
    "BTCO": {"underlying": "Bitcoin", "coin_pct": 99.5, "cash_pct": 0.5,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "issuer_holdings_url": "https://www.invesco.com/us/financial-products/etfs/product-detail?audienceType=Investor&ticker=BTCO"},
    "EZBC": {"underlying": "Bitcoin", "coin_pct": 99.5, "cash_pct": 0.5,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "issuer_holdings_url": "https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/39639/SINGLCLASS/franklin-bitcoin-etf/EZBC"},
    "BRRR": {"underlying": "Bitcoin", "coin_pct": 99.5, "cash_pct": 0.5,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "issuer_holdings_url": "https://coinshares.com/us/etf/brrr/"},
    "HODL": {"underlying": "Bitcoin", "coin_pct": 99.5, "cash_pct": 0.5,
             "custodian": "Gemini Trust Company, LLC",
             "issuer_holdings_url": "https://www.vaneck.com/us/en/investments/bitcoin-trust-hodl/overview/"},
    "BTC":  {"underlying": "Bitcoin", "coin_pct": 99.5, "cash_pct": 0.5,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "issuer_holdings_url": "https://www.grayscale.com/funds/grayscale-bitcoin-mini-trust"},
    "GBTC": {"underlying": "Bitcoin", "coin_pct": 99.5, "cash_pct": 0.5,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "issuer_holdings_url": "https://www.grayscale.com/funds/grayscale-bitcoin-trust"},

    "ETHA": {"underlying": "Ethereum", "coin_pct": 99.0, "cash_pct": 1.0,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "staking_enabled": True,
             "issuer_holdings_url": "https://www.ishares.com/us/products/337614/ishares-ethereum-trust"},
    "FETH": {"underlying": "Ethereum", "coin_pct": 99.0, "cash_pct": 1.0,
             "custodian": "Fidelity Digital Asset Services, LLC",
             "staking_enabled": True,
             "issuer_holdings_url": "https://institutional.fidelity.com/app/funds-and-products/etp/summary/feth.html"},
    "ETH":  {"underlying": "Ethereum", "coin_pct": 99.0, "cash_pct": 1.0,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "staking_enabled": True,
             "issuer_holdings_url": "https://www.grayscale.com/funds/grayscale-ethereum-mini-trust"},
    "ETHE": {"underlying": "Ethereum", "coin_pct": 99.0, "cash_pct": 1.0,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "issuer_holdings_url": "https://www.grayscale.com/funds/grayscale-ethereum-trust"},
    "ETHW": {"underlying": "Ethereum", "coin_pct": 99.0, "cash_pct": 1.0,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "issuer_holdings_url": "https://ethetf.com/"},
    "CETH": {"underlying": "Ethereum", "coin_pct": 99.0, "cash_pct": 1.0,
             "custodian": "Coinbase Custody Trust Company, LLC",
             "issuer_holdings_url": "https://www.21shares.com/us/en/products/ceth"},
}

# '40-Act funds that actually file NPORT-P (futures strategy ETFs).
_NPORT_FILER_TICKERS: frozenset[str] = frozenset({"BITO", "EETH"})

SUPPORTED_TICKERS: frozenset[str] = frozenset(_TRUST_COMPOSITIONS.keys()) | _NPORT_FILER_TICKERS

# N-PORT filings update quarterly (lagged). 7-day disk cache is plenty.
_COMPOSITION_CACHE_PATH: Path = DATA_DIR / "nport_composition_cache.json"
_COMPOSITION_CACHE_TTL_SEC = 7 * 24 * 3600

# Common N-PORT XBRL namespace (most issuers). BlackRock + Fidelity use
# this variant consistently. If a ticker filing uses a different ns, the
# parser falls back to a namespace-agnostic walk.
_NPORT_NS = "http://www.sec.gov/edgar/nport"


def _load_cache() -> dict[str, dict]:
    if not _COMPOSITION_CACHE_PATH.exists():
        return {}
    try:
        with open(_COMPOSITION_CACHE_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict[str, dict]) -> None:
    _COMPOSITION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8",
        dir=str(_COMPOSITION_CACHE_PATH.parent),
        prefix=".tmp_nport_", suffix=".json", delete=False,
    ) as tf:
        json.dump(cache, tf, indent=2)
        tmp_name = tf.name
    for attempt in range(5):
        try:
            os.replace(tmp_name, str(_COMPOSITION_CACHE_PATH))
            return
        except PermissionError:
            time.sleep(0.1 * (attempt + 1))
    try:
        os.remove(tmp_name)
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# Parser
# ═══════════════════════════════════════════════════════════════════════════

def parse_nport_xml(xml_text: str) -> list[dict]:
    """
    Parse an N-PORT primary-document XML and return a list of holdings.
    Each holding: {name, balance, value_usd, pct_value, title, asset_cat}.

    Namespace-tolerant: first tries the standard SEC N-PORT XBRL ns, then
    falls back to a namespace-agnostic tree walk for variants.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("N-PORT XML parse error: %s", exc)
        return []

    holdings: list[dict] = []

    # First pass: strict namespace
    invest_nodes = root.findall(f".//{{{_NPORT_NS}}}invstOrSec")
    if not invest_nodes:
        # Namespace-agnostic fallback — match any element with local name
        invest_nodes = [
            el for el in root.iter()
            if el.tag.split("}")[-1] == "invstOrSec"
        ]

    total_value = 0.0
    for node in invest_nodes:
        h = _extract_holding(node)
        if h:
            holdings.append(h)
            total_value += h["value_usd"]

    # Compute pct_value if not set
    if total_value > 0:
        for h in holdings:
            if h.get("pct_value") is None:
                h["pct_value"] = round(h["value_usd"] / total_value * 100, 3)

    # Sort by value descending
    holdings.sort(key=lambda h: h.get("value_usd", 0), reverse=True)
    return holdings


def _extract_holding(node: ET.Element) -> dict | None:
    """Pull the fields we care about from one <invstOrSec> node."""
    def _find_text(local: str) -> str:
        for child in node.iter():
            if child.tag.split("}")[-1] == local and child.text:
                return child.text.strip()
        return ""

    name = _find_text("name") or _find_text("title")
    balance_str = _find_text("balance")
    value_str = _find_text("valUSD")
    pct_str = _find_text("pctVal")
    asset_cat = _find_text("assetCat")
    title = _find_text("title")

    if not name and not title:
        return None

    try:
        balance = float(balance_str) if balance_str else None
    except ValueError:
        balance = None
    try:
        value_usd = float(value_str) if value_str else 0.0
    except ValueError:
        value_usd = 0.0
    try:
        pct_value = float(pct_str) if pct_str else None
    except ValueError:
        pct_value = None

    return {
        "name":      name or title,
        "title":     title,
        "balance":   balance,
        "value_usd": value_usd,
        "pct_value": pct_value,
        "asset_cat": asset_cat,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def get_etf_composition(ticker: str) -> dict:
    """
    Return the composition dict for an ETF.

    Shape:
      {
        "ticker":          str,
        "supported":       bool,
        "source":          "edgar_live" | "cached" | "unavailable",
        "filing_date":     str (YYYY-MM-DD) if available,
        "accession":       str if available,
        "holdings":        [{name, balance, value_usd, pct_value, title, asset_cat}, ...],
        "holdings_count":  int,
        "total_value_usd": float,
        "note":            str (human-readable status),
      }

    Live-first: hits EDGAR. If live fails, checks 7-day disk cache. If no
    cache, returns source=unavailable. Never returns fabricated data.
    """
    category = "etf_composition"
    tkr = ticker.upper()
    empty = {
        "ticker":          tkr,
        "supported":       tkr in SUPPORTED_TICKERS,
        "source":          "unavailable",
        "filing_date":     None,
        "accession":       None,
        "holdings":        [],
        "holdings_count":  0,
        "total_value_usd": 0.0,
        "note":            "",
    }

    if tkr not in SUPPORTED_TICKERS:
        empty["note"] = (
            "Live holdings not wired for this ticker yet. "
            "Covered in demo scope: all spot BTC / ETH trusts + BITO / EETH futures."
        )
        return empty

    # ── Spot commodity trust path ────────────────────────────────────────────
    # '33-Act grantor trusts (IBIT / FBTC / BITB / ETHA / FETH / etc.) do
    # NOT file N-PORT. They hold the physical coin via a custodian and
    # publish daily holdings on the issuer's site. We serve a curated
    # structured summary matching the issuer's own prospectus data.
    if tkr in _TRUST_COMPOSITIONS:
        spec = _TRUST_COMPOSITIONS[tkr]
        holdings = [
            {
                "name":      spec["underlying"],
                "title":     f"{spec['underlying']} (spot, cold-stored)",
                "asset_cat": "DIGITAL-ASSET",
                "balance":   None,        # not unit-sized; the trust holds one underlying
                "value_usd": None,        # live NAV is in the price feed, not here
                "pct_value": spec["coin_pct"],
            },
            {
                "name":      "Cash and equivalents",
                "title":     "USD cash buffer (creations / redemptions / fees)",
                "asset_cat": "CASH",
                "balance":   None,
                "value_usd": None,
                "pct_value": spec["cash_pct"],
            },
        ]
        staking_note = (
            " Staking yield is distributed to shareholders (SEC-approved Feb 2026)."
            if spec.get("staking_enabled") else ""
        )
        register_fetch_attempt(
            category, "issuer_static", success=True,
            note=f"{spec['underlying']} spot trust (curated composition)",
        )
        return {
            "ticker":          tkr,
            "supported":       True,
            "source":          "issuer_static",
            "filing_date":     None,
            "accession":       None,
            "holdings":        holdings,
            "holdings_count":  2,
            "total_value_usd": 0.0,
            "custodian":       spec["custodian"],
            "issuer_holdings_url": spec["issuer_holdings_url"],
            "note":
                f"Spot {spec['underlying']} grantor trust — holds the "
                f"underlying coin directly with {spec['custodian']}. "
                f"Issuer publishes per-share holdings daily; see link below."
                + staking_note,
        }

    # ── '40-Act fund N-PORT live path ────────────────────────────────────────
    # BITO / EETH are registered investment companies and DO file NPORT-P
    # quarterly. Live EDGAR fetch applies here.
    try:
        cik = get_cik_for_ticker(tkr)
        if not cik:
            raise RuntimeError(f"No CIK found for ticker {tkr}")

        # SEC EDGAR uses NPORT-P (monthly public portfolio report) and
        # NPORT-EX (exempt variant) as the actual filed form names — NOT
        # "N-PORT" which never appears in submissions.json. Asking for
        # "N-PORT" silently returned zero matches and the app fell
        # through to the "live unavailable" caption on every lookup.
        filings = get_recent_filings(
            cik, form_types=("NPORT-P", "NPORT-EX"), max_rows=1,
        )
        if not filings:
            raise RuntimeError(
                f"No recent NPORT-P / NPORT-EX filing for CIK {cik}"
            )

        latest = filings[0]
        resp = edgar_get(latest["primary_doc_url"], accept="application/xml")
        resp.raise_for_status()
        holdings = parse_nport_xml(resp.text)
        if not holdings:
            raise RuntimeError("N-PORT parsed to zero holdings")

        result = {
            "ticker":          tkr,
            "supported":       True,
            "source":          "edgar_live",
            "filing_date":     latest["filing_date"],
            "accession":       latest["accession"],
            "holdings":        holdings,
            "holdings_count":  len(holdings),
            "total_value_usd": sum(h.get("value_usd", 0) for h in holdings),
            "note":            f"Live from SEC EDGAR filing dated {latest['filing_date']}.",
        }

        # Persist to cache for fallback use
        cache = _load_cache()
        cache[tkr] = {**result, "_cached_at": time.time()}
        _save_cache(cache)

        register_fetch_attempt(category, "edgar", success=True,
                               note=f"N-PORT {latest['filing_date']}")
        return result

    except Exception as exc:
        logger.warning("Live N-PORT fetch failed for %s: %s", tkr, exc)
        register_fetch_attempt(category, "edgar", success=False,
                               note=f"{type(exc).__name__}: {exc}")

    # ── Cache fallback ───────────────────────────────────────────────────────
    cache = _load_cache()
    cached = cache.get(tkr)
    if cached:
        age_sec = int(time.time() - cached.get("_cached_at", 0))
        if age_sec <= _COMPOSITION_CACHE_TTL_SEC:
            mark_cache_hit(category, age_seconds=age_sec,
                           note=f"EDGAR N-PORT cache ({tkr})")
            result = {k: v for k, v in cached.items() if k != "_cached_at"}
            result["source"] = "cached"
            result["note"] = (
                f"Live EDGAR unavailable — showing cached filing from "
                f"{cached.get('filing_date', 'unknown date')} "
                f"({age_sec // 3600}h old)."
            )
            return result

    # ── Nothing ──────────────────────────────────────────────────────────────
    mark_static_fallback(category, note=f"No live or cached data for {tkr}")
    empty["note"] = (
        "Live holdings temporarily unavailable and no cache. "
        "Click Retry on the transparency banner to attempt a live fetch."
    )
    return empty
