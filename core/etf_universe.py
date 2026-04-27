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

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import (
    DATA_DIR,
    EDGAR_CONTACT_EMAIL,
    EDGAR_REQS_PER_SEC,
    ETF_UNIVERSE_SEED,
)
from core.data_source_state import register_fetch_attempt
from integrations.edgar import (
    assert_edgar_configured as _assert_edgar_shared,
    take_token as _edgar_take_token_shared,
    user_agent as _edgar_user_agent_shared,
)

logger = logging.getLogger(__name__)

# Scanner health persistence — tracked for Settings-page visibility
# (Day-3 item B). Stale-threshold warning fires at 48hr given EDGAR's
# known 24-48hr full-text index lag.
SCANNER_HEALTH_PATH: Path = DATA_DIR / "scanner_health.json"
SCANNER_STALE_HOURS: int = 48

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

# EDGAR rate limiting + User-Agent + runtime guard all live in integrations.edgar now.
# Thin wrappers retained so tests that monkeypatch these names still work.

def _edgar_take_token() -> None:
    _edgar_take_token_shared()


def _edgar_user_agent() -> str:
    return _edgar_user_agent_shared()


def _assert_edgar_configured() -> None:
    _assert_edgar_shared()


# ═══════════════════════════════════════════════════════════════════════════
# Default analytic fields for portfolio_engine
# ═══════════════════════════════════════════════════════════════════════════

# Phase-1 default analytics per ETF category. These are first-pass values
# used when real price/return history hasn't been computed yet. Day 3+
# computes these live from yfinance OHLCV.
_CATEGORY_DEFAULTS: dict[str, dict[str, float]] = {
    # Original 4 — same calibration as before Option B widening.
    "btc_spot":            {"expected_return": 25.0, "volatility": 55.0, "correlation_with_btc": 0.98},
    "eth_spot":            {"expected_return": 35.0, "volatility": 70.0, "correlation_with_btc": 0.78},
    "btc_futures":         {"expected_return": 15.0, "volatility": 58.0, "correlation_with_btc": 0.95},
    "eth_futures":         {"expected_return": 25.0, "volatility": 72.0, "correlation_with_btc": 0.75},
    "thematic":            {"expected_return": 50.0, "volatility": 75.0, "correlation_with_btc": 0.70},
    # Expanded categories from the Q1 2026 universe research:
    "altcoin_spot":        {"expected_return": 40.0, "volatility": 95.0, "correlation_with_btc": 0.72},
    "leveraged":           {"expected_return": 30.0, "volatility":110.0, "correlation_with_btc": 0.85},
    "income_covered_call": {"expected_return": 25.0, "volatility": 45.0, "correlation_with_btc": 0.55},
    "thematic_equity":     {"expected_return": 35.0, "volatility": 55.0, "correlation_with_btc": 0.50},
    "multi_asset":         {"expected_return": 30.0, "volatility": 60.0, "correlation_with_btc": 0.85},
    # Defined-outcome buffered BTC ETFs (Calamos CBOJ-series): 100%/90%/80%
    # downside protection with corresponding upside caps. Realized vol runs
    # ~20% (vs BTC spot ~55%) because the buffer decouples on the downside;
    # correlation with BTC softens to ~0.55 during drawdowns for the same
    # reason. Expected return averages the cap ceiling (~10-20% p.a.
    # depending on series) with partial BTC upside above the cap.
    "defined_outcome":     {"expected_return": 15.0, "volatility": 20.0, "correlation_with_btc": 0.55},
}

def _enrich(etf: dict[str, Any]) -> dict[str, Any]:
    """Attach analytic defaults + preserve issuer metadata from JSON."""
    defaults = _CATEGORY_DEFAULTS.get(
        etf.get("category", ""),
        _CATEGORY_DEFAULTS["btc_spot"],
    )
    return {
        **etf,
        "expected_return":       defaults["expected_return"],
        "volatility":            defaults["volatility"],
        "correlation_with_btc":  defaults["correlation_with_btc"],
        # Prefer expense_ratio_bps from the JSON; fall back to None so
        # caller can treat it as unknown rather than falsely zero.
        "expense_ratio_bps":     etf.get("expense_ratio_bps"),
    }


# Path to the JSON-backed universe registry. This is the authoritative
# source post Option-2; config.ETF_UNIVERSE_SEED is kept as a minimal
# emergency fallback only.
UNIVERSE_REGISTRY_PATH: Path = DATA_DIR / "etf_universe.json"

# Precomputed analytics snapshot — written by scripts/precompute_analytics.py
# via GH Actions nightly cron (.github/workflows/nightly_analytics.yml). The
# app loads this instead of doing 146+ yfinance calls on cold boot.
ANALYTICS_SNAPSHOT_PATH: Path = DATA_DIR / "etf_analytics.json"
ANALYTICS_FRESH_HOURS: float = 25.0   # daily cron + 1hr grace


def _load_precomputed_analytics() -> dict | None:
    """
    Load data/etf_analytics.json if it exists and is fresh enough
    (≤ ANALYTICS_FRESH_HOURS old). Returns the parsed dict augmented
    with `_age_hours` so the UI can show data freshness. Returns
    None if missing, malformed, or stale — caller falls through to
    the live-enrichment path.
    """
    import time as _time
    if not ANALYTICS_SNAPSHOT_PATH.exists():
        return None
    try:
        with open(ANALYTICS_SNAPSHOT_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Precomputed analytics unreadable (%s) — using live path.", exc)
        return None

    meta = data.get("_metadata", {})
    computed_ts = meta.get("computed_at_ts")
    if not isinstance(computed_ts, (int, float)):
        return None
    age_hours = (_time.time() - float(computed_ts)) / 3600.0
    if age_hours > ANALYTICS_FRESH_HOURS:
        logger.info(
            "Precomputed analytics stale (%.1fh > %.1fh) — using live path.",
            age_hours, ANALYTICS_FRESH_HOURS,
        )
        return None
    data["_age_hours"] = round(age_hours, 2)
    return data


def _load_registry_from_disk() -> list[dict] | None:
    """Read data/etf_universe.json. Return None if missing or malformed."""
    if not UNIVERSE_REGISTRY_PATH.exists():
        return None
    try:
        with open(UNIVERSE_REGISTRY_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        etfs = payload.get("etfs")
        if not isinstance(etfs, list) or not etfs:
            return None
        return etfs
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Universe registry unreadable (%s) — falling back to seed.", exc)
        return None


def load_universe(scanner_additions: list[dict] | None = None) -> list[dict]:
    """
    Return the active ETF universe.

    Source order:
      1. data/etf_universe.json — 35+ curated US-listed crypto ETFs
         spanning spot (BTC/ETH/altcoin), futures, leveraged, income
         covered-call wrappers, thematic equity baskets, multi-asset.
         This is the authoritative production source.
      2. config.ETF_UNIVERSE_SEED — minimal (~19 ticker) legacy fallback
         only used if the JSON is missing or malformed.

    Each entry is enriched with category-default volatility + correlation
    + expense_ratio_bps. Scanner additions are merged by ticker.

    Day-3+ callers should prefer load_universe_with_live_analytics()
    which overwrites the defaults with live-derived values per ETF.
    """
    base_list = _load_registry_from_disk()
    if base_list is None:
        logger.info("Universe registry not on disk — using config.ETF_UNIVERSE_SEED.")
        base_list = list(ETF_UNIVERSE_SEED)

    base = [_enrich(e) for e in base_list]
    if scanner_additions:
        existing = {e["ticker"] for e in base}
        for new in scanner_additions:
            if new.get("ticker") and new["ticker"] not in existing:
                base.append(_enrich(new))

    # ── 2026-04-26 Bucket 3: merge FA-approved scanner additions ──
    # core/etf_review_queue.approve_entry() writes to
    # data/etf_user_additions.json. Loading them here means an FA can
    # approve a candidate from Settings + see it flow into Portfolio
    # basket selection on the next refresh, without editing config.py.
    try:
        from core.etf_review_queue import load_user_additions
        _user_adds = load_user_additions()
        existing_tickers = {e["ticker"] for e in base}
        for ua in _user_adds:
            tkr = (ua.get("ticker") or "").upper()
            if not tkr or tkr in existing_tickers:
                continue
            base.append(_enrich({
                "ticker":             tkr,
                "issuer":             ua.get("issuer", ""),
                "category":           ua.get("category", "btc_spot"),
                "underlying":         ua.get("underlying", "BTC"),
                "name":               ua.get("name", tkr),
                "expense_ratio_bps":  ua.get("expense_ratio_bps") or 50,
                "inception":          ua.get("inception", ""),
                "review_source":      "fa_approved",
            }))
            existing_tickers.add(tkr)
    except Exception as exc:
        logger.debug("FA-approved additions merge failed (non-fatal): %s", exc)

    # Default provenance flag — load_universe_with_live_returns overrides
    # it per-ticker when a live CAGR fetch succeeds.
    for e in base:
        e.setdefault("expected_return_source", "category_default")
    return base


def load_universe_with_live_returns(
    scanner_additions: list[dict] | None = None,
) -> list[dict]:
    """
    Like load_universe() but replaces each ETF's category-default
    expected_return with its live annualized historical CAGR when a
    price fetch succeeds. Falls back to the category default per-ticker.
    Sets expected_return_source = "live" | "category_default" so the
    UI can label the source.

    N.B. this performs one price-bundle fetch per ticker. Callers should
    cache the result (the Portfolio page wraps this in @st.cache_data).
    """
    from integrations.data_feeds import get_historical_cagr

    base = load_universe(scanner_additions)
    for etf in base:
        try:
            info = get_historical_cagr(etf["ticker"])
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("get_historical_cagr failed for %s: %s", etf["ticker"], exc)
            continue
        cagr_pct = info.get("cagr_pct")
        if cagr_pct is not None:
            etf["expected_return"] = round(float(cagr_pct), 2)
            etf["expected_return_source"] = "live"
            etf["cagr_days_observed"] = info.get("days_observed")
            etf["cagr_source"] = info.get("source")
        # else: keep category default + source = "category_default"
    return base


def load_universe_with_live_analytics(
    scanner_additions: list[dict] | None = None,
    vol_lookback_days: int = 90,
    corr_lookback_days: int = 90,
) -> list[dict]:
    """
    Superset of load_universe_with_live_returns: also replaces each
    ETF's category-default `volatility` with its 90-day annualized
    realized volatility, and `correlation_with_btc` with its 90-day
    Pearson correlation against the IBIT BTC proxy, when live data
    is available.

    Sets three provenance flags per ETF:
        expected_return_source   ∈ {"live", "category_default"}
        volatility_source        ∈ {"live", "category_default"}
        correlation_source       ∈ {"live", "self", "category_default"}

    "self" for correlation means the ETF IS the BTC proxy (IBIT or
    FBTC) — trivially correlated with itself at 1.0.

    All three enrichments reuse the same underlying price bundle
    per ticker (cached in data_feeds._yf_memo), so the marginal cost
    over the returns-only variant is pure math, no extra network calls.
    """
    from integrations.data_feeds import (
        get_btc_correlation,
        get_forward_return_estimate,
        get_historical_cagr,
        get_realized_volatility,
    )

    base = load_universe(scanner_additions)

    # Option-2 cold-boot fast path: if a fresh precomputed analytics
    # snapshot exists, merge it in and skip the live yfinance loop
    # entirely. The precompute job (GH Actions nightly_analytics) runs
    # once a day and writes data/etf_analytics.json. Loading the JSON
    # is microseconds vs. ~5 minutes of live network calls.
    precomputed = _load_precomputed_analytics()
    if precomputed is not None:
        per_ticker = precomputed.get("etfs", {})
        for etf in base:
            tkr = etf["ticker"]
            snap = per_ticker.get(tkr)
            if not snap:
                continue
            for field in (
                "expected_return", "volatility", "correlation_with_btc",
                "forward_return", "expected_return_source",
                "volatility_source", "correlation_source",
                "forward_return_source", "forward_return_basis",
                "btc_proxy_used", "cagr_days_observed",
                "vol_n_returns", "corr_n_returns",
            ):
                if field in snap and snap[field] is not None:
                    etf[field] = snap[field]
            etf["analytics_source"] = "precomputed"
            etf["analytics_age_hours"] = precomputed.get("_age_hours")
        # Mark default-source flags for any field the snapshot didn't fill,
        # so per-tile transparency remains honest.
        for etf in base:
            etf.setdefault("expected_return_source", "category_default")
            etf.setdefault("volatility_source", "category_default")
            etf.setdefault("correlation_source", "category_default")
            etf.setdefault("forward_return_source", "unavailable")
        return base

    # Slow path — no fresh precompute. Run the live enrichment loop.
    # Pre-warm with batched fetches so the per-ticker calls below all
    # hit the in-memory _yf_memo cache. Without batching, this loop
    # takes 5+ minutes; with it, ~10-30 seconds even when yfinance is
    # throttled.
    try:
        from integrations.data_feeds import get_etf_prices_batch
        all_tickers = [e["ticker"] for e in base]
        # 5y for CAGR + 144d for vol / correlation
        get_etf_prices_batch(all_tickers, period="5y", interval="1d")
        get_etf_prices_batch(all_tickers, period="144d", interval="1d")
        # IBIT (BTC proxy) is already in the universe so it's pre-warmed.
        # BTC-USD / ETH-USD long-run for forward returns:
        get_etf_prices_batch(["BTC-USD", "ETH-USD"], period="10y", interval="1d")
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "Batch pre-warm failed (%s) — per-ticker enrichment "
            "will fall through to individual yfinance calls.", exc,
        )

    for etf in base:
        tkr = etf["ticker"]

        # Historical return (1-2yr CAGR from this fund's own price history)
        try:
            cagr_info = get_historical_cagr(tkr)
            if cagr_info.get("cagr_pct") is not None:
                etf["expected_return"] = round(float(cagr_info["cagr_pct"]), 2)
                etf["expected_return_source"] = "live"
                etf["cagr_days_observed"] = cagr_info.get("days_observed")
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("CAGR failed for %s: %s", tkr, exc)

        # Forward-return MODEL estimate — long-run BTC / ETH CAGR with
        # category-specific drag / premium. Pass the fund's `underlying`
        # (from the JSON registry) so leveraged / income / altcoin funds
        # route to the correct reference asset — ETHU → ETH CAGR rather
        # than one-size-fits-all BTC CAGR.
        try:
            fwd_info = get_forward_return_estimate(
                etf.get("category", ""),
                expense_ratio_bps=etf.get("expense_ratio_bps"),
                underlying=etf.get("underlying"),
            )
            fwd = fwd_info.get("forward_return_pct")
            if fwd is not None:
                etf["forward_return"] = round(float(fwd), 2)
                etf["forward_return_source"] = "live_long_run"
                etf["forward_return_basis"] = fwd_info.get("basis", "")
            else:
                etf.setdefault("forward_return_source", "unavailable")
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("forward_return failed for %s: %s", tkr, exc)
            etf.setdefault("forward_return_source", "unavailable")

        # Volatility
        try:
            vol_info = get_realized_volatility(tkr, lookback_days=vol_lookback_days)
            if vol_info.get("volatility_pct") is not None:
                etf["volatility"] = round(float(vol_info["volatility_pct"]), 2)
                etf["volatility_source"] = "live"
                etf["vol_n_returns"] = vol_info.get("n_returns")
            else:
                etf.setdefault("volatility_source", "category_default")
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("realized_vol failed for %s: %s", tkr, exc)
            etf.setdefault("volatility_source", "category_default")

        # Correlation with BTC
        try:
            corr_info = get_btc_correlation(tkr, lookback_days=corr_lookback_days)
            corr = corr_info.get("correlation")
            if corr is not None:
                etf["correlation_with_btc"] = round(float(corr), 4)
                etf["correlation_source"] = (
                    "self" if corr_info.get("source") == "self" else "live"
                )
                etf["corr_n_returns"] = corr_info.get("n_returns")
                etf["btc_proxy_used"] = corr_info.get("btc_proxy_used")
            else:
                etf.setdefault("correlation_source", "category_default")
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("btc_corr failed for %s: %s", tkr, exc)
            etf.setdefault("correlation_source", "category_default")

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
    n_queries = 0
    n_successful_queries = 0
    for keyword in _CRYPTO_KEYWORDS:
        for form in _CRYPTO_FORM_TYPES:
            n_queries += 1
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

            n_successful_queries += 1
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

    results = list(dedup.values())

    # Scanner health + data-source-state book-keeping. Success depends
    # on at least one inner query actually completing — if all
    # len(_CRYPTO_KEYWORDS) × len(_CRYPTO_FORM_TYPES) requests 429'd
    # or errored, we should mark the scanner as failed instead of
    # claiming a successful scan of zero filings.
    any_query_succeeded = n_successful_queries > 0
    try:
        write_scanner_health(
            n_matches=len(results),
            keywords_queried=list(_CRYPTO_KEYWORDS),
            forms_queried=list(_CRYPTO_FORM_TYPES),
        )
    except Exception as exc:
        logger.warning("write_scanner_health failed: %s", exc)
    register_fetch_attempt(
        "edgar_scanner", "edgar",
        success=any_query_succeeded,
        note=(
            f"{len(results)} unique filings matched "
            f"across {n_successful_queries}/{n_queries} queries"
            if any_query_succeeded
            else f"All {n_queries} EDGAR queries failed (429 or network)"
        ),
    )

    # ── 2026-04-26 Bucket 3: feed each new filing through the review
    # queue. add_pending() handles dedup against approved + rejected so
    # this is idempotent across daily runs — already-decided filings
    # don't get re-flagged.
    try:
        from core.etf_review_queue import add_pending as _rq_add_pending
        counts = _rq_add_pending(results)
        # add_pending returns dict {approved, rejected, pending,
        # skipped_duplicate} since the 2026-04-27 auto-classifier landed
        # (was int previously). Stay defensive in case of partial deploy.
        if isinstance(counts, dict):
            logger.info(
                "Review queue: +%d auto-approved / +%d auto-rejected / "
                "+%d pending / %d duplicates",
                counts.get("approved", 0),
                counts.get("rejected", 0),
                counts.get("pending", 0),
                counts.get("skipped_duplicate", 0),
            )
        elif counts > 0:
            logger.info("Review queue: %d new filings added to pending", counts)
    except Exception as exc:
        logger.warning("Review-queue enqueue failed (non-fatal): %s", exc)

    return results


def _date_today() -> str:
    from datetime import date
    return date.today().isoformat()


def _date_n_days_ago(n: int) -> str:
    from datetime import date, timedelta
    return (date.today() - timedelta(days=max(1, n))).isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# Scanner health persistence (Day-3 item B)
# ═══════════════════════════════════════════════════════════════════════════

def _atomic_write_json(path: Path, payload: dict) -> None:
    """
    Write JSON atomically on Windows + OneDrive (CLAUDE.md §20).
    tempfile → os.replace with a 5-attempt backoff around PermissionError
    (OneDrive sync sometimes holds the target file briefly).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=".tmp_scanner_health_",
        suffix=".json",
        delete=False,
    ) as tf:
        json.dump(payload, tf, indent=2)
        tmp_name = tf.name

    last_exc: Exception | None = None
    for attempt in range(5):
        try:
            os.replace(tmp_name, str(path))
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(0.1 * (attempt + 1))
    # Clean up tempfile if replace failed
    try:
        os.remove(tmp_name)
    except OSError:
        pass
    if last_exc is not None:
        raise last_exc


def write_scanner_health(
    n_matches: int,
    keywords_queried: list[str],
    forms_queried: list[str] | None = None,
) -> None:
    """Record a successful scanner completion to data/scanner_health.json."""
    payload = {
        "last_success_ts":   time.time(),
        "last_success_iso":  datetime.now(timezone.utc).isoformat(),
        "n_matches":         int(n_matches),
        "keywords_queried":  list(keywords_queried),
        "forms_queried":     list(forms_queried) if forms_queried else list(_CRYPTO_FORM_TYPES),
    }
    _atomic_write_json(SCANNER_HEALTH_PATH, payload)


def get_scanner_health() -> dict:
    """
    Return {last_success_ts, last_success_iso, n_matches, keywords_queried,
    forms_queried, age_hours, is_stale}. If no scan has ever run, age_hours
    is None and is_stale is True.
    """
    base = {
        "last_success_ts":   None,
        "last_success_iso":  None,
        "n_matches":         None,
        "keywords_queried":  [],
        "forms_queried":     [],
        "age_hours":         None,
        "is_stale":          True,
    }
    if not SCANNER_HEALTH_PATH.exists():
        return base
    try:
        with open(SCANNER_HEALTH_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        ts = float(data.get("last_success_ts", 0))
        age_hours = (time.time() - ts) / 3600 if ts > 0 else None
        return {
            "last_success_ts":   ts,
            "last_success_iso":  data.get("last_success_iso"),
            "n_matches":         data.get("n_matches"),
            "keywords_queried":  data.get("keywords_queried", []),
            "forms_queried":     data.get("forms_queried", []),
            "age_hours":         round(age_hours, 2) if age_hours is not None else None,
            "is_stale":          True if age_hours is None else (age_hours > SCANNER_STALE_HOURS),
        }
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("scanner_health.json unreadable: %s", exc)
        return base
