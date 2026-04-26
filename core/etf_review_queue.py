"""
core/etf_review_queue.py — review queue for newly-discovered ETF filings.

The daily EDGAR scanner (core.etf_universe.daily_scanner) finds crypto-
related fund filings as soon as the SEC posts them. Until 2026-04-26
those matches were dumped to a log + manually reviewed by the FA. This
module formalizes the workflow:

    1. Scanner finds a new filing.
    2. enrich_filing() heuristically derives:
         - Suggested category (btc_spot / eth_spot / altcoin_spot /
           leveraged / income_covered_call / multi_asset)
         - Suggested underlying coin (BTC / ETH / SOL / etc.)
         - Suggested ticker (parsed from filing display name)
    3. add_pending() writes the enriched entry to the review queue
       (data/etf_review_queue.json), unless it's already in the
       approved or rejected lists from a previous scan.
    4. The Settings → "New ETFs pending review" panel surfaces
       pending entries to the FA. Each row has Approve / Reject buttons.
    5. approve_entry() moves an entry from pending to approved AND
       writes it to data/etf_user_additions.json — picked up by the
       universe loader on its next refresh, so the new ETF flows into
       Portfolio basket selection without touching config.py at runtime.
    6. reject_entry() moves an entry from pending to rejected. Future
       scans skip already-rejected accession numbers.

The split between "approved seed addition" and "rejected blacklist"
means the FA only sees a candidate ONCE per filing — even if the
scanner runs daily and re-finds the same filing.

JSON schema (data/etf_review_queue.json):

    {
      "pending":  [<entry>, ...],
      "approved": [<entry>, ...],
      "rejected": [<entry>, ...]
    }

Each <entry> is a dict with at least:
    accession_number, filing_date, form_type, filer_cik, filer_name,
    matched_keywords, raw_match_text,
    suggested_ticker, suggested_category, suggested_underlying,
    review_notes (free-form, written by FA on approve/reject).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
QUEUE_PATH = REPO_ROOT / "data" / "etf_review_queue.json"
ADDITIONS_PATH = REPO_ROOT / "data" / "etf_user_additions.json"


# ── Heuristic enrichment ──────────────────────────────────────────────

_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    # Order matters — first match wins. Most specific patterns first.
    (r"\b(2x|two[- ]times|leveraged|levered)\b",                 "leveraged"),
    (r"\b(covered[- ]?call|income[- ]?option|yield[- ]?max)\b",  "income_covered_call"),
    (r"\b(buffer|defined[- ]?outcome|laddered)\b",               "defined_outcome"),
    (r"\b(thematic|miner|mining|blockchain[- ]?equity)\b",       "thematic_equity"),
    (r"\b(multi[- ]?asset|diversified[- ]?crypto|10\b|index)\b", "multi_asset"),
    # Specific underlyings → spot
    (r"\b(bitcoin|btc)\b.*\b(futures?)\b",                       "btc_futures"),
    (r"\b(ether|eth|ethereum)\b.*\b(futures?)\b",                "eth_futures"),
    (r"\bbitcoin\b|\bbtc\b",                                     "btc_spot"),
    (r"\b(ether|ethereum|eth)\b",                                "eth_spot"),
    (r"\b(solana|sol)\b",                                        "altcoin_spot"),
    (r"\b(xrp|ripple)\b",                                        "altcoin_spot"),
    (r"\b(litecoin|ltc)\b",                                      "altcoin_spot"),
    (r"\b(dogecoin|doge)\b",                                     "altcoin_spot"),
    (r"\b(cardano|ada)\b",                                       "altcoin_spot"),
    (r"\b(avalanche|avax)\b",                                    "altcoin_spot"),
    (r"\b(hedera|hbar)\b",                                       "altcoin_spot"),
    (r"\b(polkadot|dot)\b",                                      "altcoin_spot"),
    (r"\b(chainlink|link)\b",                                    "altcoin_spot"),
]

_UNDERLYING_KEYWORDS: dict[str, str] = {
    r"\bbitcoin\b|\bbtc\b":            "BTC",
    r"\b(ether|ethereum|eth)\b":       "ETH",
    r"\b(solana|sol)\b":               "SOL",
    r"\b(xrp|ripple)\b":               "XRP",
    r"\b(litecoin|ltc)\b":             "LTC",
    r"\b(dogecoin|doge)\b":            "DOGE",
    r"\b(cardano|ada)\b":              "ADA",
    r"\b(avalanche|avax)\b":           "AVAX",
    r"\b(hedera|hbar)\b":              "HBAR",
    r"\b(polkadot|dot)\b":             "DOT",
    r"\b(chainlink|link)\b":           "LINK",
}

# Match a likely ticker symbol in the filer name or filing text.
# Tickers are 2-5 capital letters, often shown as "(XYZ)" or "XYZ ETF".
_TICKER_PATTERN = re.compile(r"\b([A-Z]{2,5})(?=\s*(?:ETF|TRUST|FUND|\)))")


def enrich_filing(filing: dict) -> dict:
    """
    Take a raw filing dict from daily_scanner and add heuristic
    suggestions: ticker, category, underlying. Never raises; missing
    suggestions become None and the FA fills them in on approval.
    """
    text_blob = " ".join(
        str(filing.get(k, "") or "")
        for k in ("filer_name", "raw_match_text", "matched_keywords")
    ).lower()

    suggested_category: str | None = None
    for pat, cat in _CATEGORY_KEYWORDS:
        if re.search(pat, text_blob, flags=re.IGNORECASE):
            suggested_category = cat
            break

    suggested_underlying: str | None = None
    for pat, sym in _UNDERLYING_KEYWORDS.items():
        if re.search(pat, text_blob, flags=re.IGNORECASE):
            suggested_underlying = sym
            break

    suggested_ticker: str | None = None
    name_for_ticker = str(filing.get("filer_name", "") or "")
    m = _TICKER_PATTERN.search(name_for_ticker.upper())
    if m:
        suggested_ticker = m.group(1)

    return {
        **filing,
        "suggested_ticker":     suggested_ticker,
        "suggested_category":   suggested_category,
        "suggested_underlying": suggested_underlying,
        "review_notes":         "",
        "review_status":        "pending",
    }


# ── Queue persistence ─────────────────────────────────────────────────

def _empty_queue() -> dict:
    return {"pending": [], "approved": [], "rejected": []}


def load_queue() -> dict[str, list[dict]]:
    """Load queue from disk; return empty queue if file missing/malformed."""
    if not QUEUE_PATH.exists():
        return _empty_queue()
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_queue()
        for k in ("pending", "approved", "rejected"):
            data.setdefault(k, [])
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("ETF review queue unreadable (%s) — starting fresh", exc)
        return _empty_queue()


def save_queue(queue: dict[str, list[dict]]) -> None:
    """Write queue to disk atomically (temp + replace)."""
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(queue, indent=2), encoding="utf-8")
    tmp.replace(QUEUE_PATH)


def _accession(entry: dict) -> str:
    """Stable identifier for a queue entry — accession_number is the
    SEC's unique per-filing key."""
    return str(entry.get("accession_number", "") or "")


def _decided_keys(queue: dict) -> set[str]:
    """Set of accession numbers already approved or rejected — these
    must NOT be re-added to pending on subsequent scans."""
    out: set[str] = set()
    for k in ("approved", "rejected"):
        for e in queue.get(k, []):
            acc = _accession(e)
            if acc:
                out.add(acc)
    return out


def add_pending(filings: list[dict]) -> int:
    """
    Add new filings to the pending queue, with enrichment.
    Skips any filing whose accession number is already in approved or
    rejected. Skips duplicates of existing pending entries.
    Returns count of newly-pended entries.
    """
    queue = load_queue()
    decided = _decided_keys(queue)
    pending_keys = {_accession(e) for e in queue["pending"] if _accession(e)}
    n_new = 0
    for raw in filings:
        acc = str(raw.get("accession_number", "") or "")
        if not acc:
            continue
        if acc in decided or acc in pending_keys:
            continue
        queue["pending"].append(enrich_filing(raw))
        pending_keys.add(acc)
        n_new += 1
    if n_new > 0:
        save_queue(queue)
    return n_new


# ── Approval / rejection actions ──────────────────────────────────────

def _move_entry(queue: dict, accession: str, target: str) -> dict | None:
    """Move an entry from pending → target list. Returns the entry or None."""
    moved: dict | None = None
    new_pending: list[dict] = []
    for e in queue["pending"]:
        if _accession(e) == accession and moved is None:
            moved = e
        else:
            new_pending.append(e)
    if moved is None:
        return None
    queue["pending"] = new_pending
    queue[target].append(moved)
    return moved


def approve_entry(accession: str, *, ticker_override: str | None = None,
                  category_override: str | None = None,
                  underlying_override: str | None = None,
                  notes: str = "") -> dict | None:
    """
    Move a pending entry to approved + write its universe-shaped record
    to data/etf_user_additions.json. Universe loader merges these into
    the main universe on next refresh.
    """
    queue = load_queue()
    moved = _move_entry(queue, accession, "approved")
    if moved is None:
        logger.warning("approve_entry: accession %s not in pending", accession)
        return None

    # Apply FA overrides on approval
    final_ticker     = (ticker_override or moved.get("suggested_ticker") or "").upper()
    final_category   = category_override or moved.get("suggested_category") or "btc_spot"
    final_underlying = underlying_override or moved.get("suggested_underlying") or "BTC"

    moved["approved_ticker"]     = final_ticker
    moved["approved_category"]   = final_category
    moved["approved_underlying"] = final_underlying
    moved["review_notes"]        = notes
    moved["review_status"]       = "approved"

    save_queue(queue)

    # Write to user-additions sidecar for the universe loader to pick up.
    additions: list[dict] = []
    if ADDITIONS_PATH.exists():
        try:
            additions = json.loads(ADDITIONS_PATH.read_text(encoding="utf-8"))
            if not isinstance(additions, list):
                additions = []
        except (OSError, json.JSONDecodeError):
            additions = []

    if final_ticker and not any(a.get("ticker") == final_ticker for a in additions):
        additions.append({
            "ticker":   final_ticker,
            "issuer":   moved.get("filer_name", ""),
            "category": final_category,
            "underlying": final_underlying,
            "name":     moved.get("filer_name", ""),
            "expense_ratio_bps": None,  # FA can fill later
            "inception": moved.get("filing_date", ""),
            "review_source": "edgar_scanner",
            "review_accession": accession,
        })
        ADDITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ADDITIONS_PATH.write_text(json.dumps(additions, indent=2), encoding="utf-8")

    return moved


def reject_entry(accession: str, *, notes: str = "") -> dict | None:
    """Move a pending entry to rejected. Future scans skip this accession."""
    queue = load_queue()
    moved = _move_entry(queue, accession, "rejected")
    if moved is None:
        logger.warning("reject_entry: accession %s not in pending", accession)
        return None
    moved["review_notes"]  = notes
    moved["review_status"] = "rejected"
    save_queue(queue)
    return moved


# ── Universe-merge helper (used by core.etf_universe) ─────────────────

def load_user_additions() -> list[dict]:
    """Approved-but-pending-config additions, merged into the live universe."""
    if not ADDITIONS_PATH.exists():
        return []
    try:
        data = json.loads(ADDITIONS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []
