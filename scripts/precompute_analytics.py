"""
Precompute the per-ETF analytics snapshot — Option 2 cold-boot fast path.

Runs once a day via GH Actions (.github/workflows/nightly_analytics.yml).
Writes data/etf_analytics.json with CAGR + 90d realized vol + 90d BTC
correlation + forward-return estimate for every ticker in the universe.

The Streamlit app loads that JSON on page render (microseconds) instead of
making 146 sequential yfinance calls on cold boot (5+ minutes when Yahoo
throttles cloud-datacenter IPs).

Usage:
    python scripts/precompute_analytics.py

Exit codes:
    0  — wrote snapshot successfully
    1  — fatal error during compute (snapshot NOT written)

The script is idempotent and uses the BATCH yfinance fetch path for
performance — it should complete in under 60 seconds even with 73
tickers.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make project root importable when run as a script
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config import DATA_DIR
from core.etf_universe import (
    ANALYTICS_SNAPSHOT_PATH,
    _CATEGORY_DEFAULTS,
    load_universe,
)
from integrations.data_feeds import (
    get_btc_correlation,
    get_etf_prices_batch,
    get_forward_return_estimate,
    get_historical_cagr,
    get_realized_volatility,
    get_long_run_cagr,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def precompute() -> dict:
    """
    Compute analytics for every ticker in the universe and return the
    snapshot dict. Caller writes it atomically to disk.
    """
    universe = load_universe()
    tickers = [e["ticker"] for e in universe]
    logger.info("Pre-warming yfinance batch caches for %d tickers", len(tickers))

    # Long-run BTC-USD / ETH-USD FIRST — before yfinance throttles us
    # with the 73-ticker batches below. The forward-return model needs
    # both, and they're trivially small (1 batch of 2 tickers).
    t0 = time.monotonic()
    get_etf_prices_batch(["BTC-USD", "ETH-USD"], period="10y", interval="1d")
    logger.info("BTC/ETH 10y batch took %.2fs", time.monotonic() - t0)
    btc_long = get_long_run_cagr("BTC-USD")
    eth_long = get_long_run_cagr("ETH-USD")
    logger.info(
        "Long-run anchors: BTC=%s%% ETH=%s%%",
        btc_long.get("cagr_pct"), eth_long.get("cagr_pct"),
    )

    # Warm the price-bundle caches with batched fetches. After this,
    # the per-ticker get_historical_cagr / get_realized_volatility /
    # get_btc_correlation calls all hit the in-process memo and are
    # essentially free.
    t0 = time.monotonic()
    get_etf_prices_batch(tickers, period="5y", interval="1d")
    logger.info("5y batch took %.2fs", time.monotonic() - t0)

    t0 = time.monotonic()
    # 90d realized vol uses period ~"144d" (1.6× lookback for weekend padding)
    get_etf_prices_batch(tickers, period="144d", interval="1d")
    logger.info("144d batch took %.2fs", time.monotonic() - t0)

    per_ticker: dict[str, dict] = {}
    n_total = len(universe)
    for i, etf in enumerate(universe, start=1):
        tkr = etf["ticker"]
        if i % 10 == 0 or i == n_total:
            logger.info("[%d/%d] computing %s", i, n_total, tkr)

        snap: dict = {}

        # Historical CAGR
        try:
            cagr = get_historical_cagr(tkr)
            if cagr.get("cagr_pct") is not None:
                snap["expected_return"] = round(float(cagr["cagr_pct"]), 2)
                snap["expected_return_source"] = "live"
                snap["cagr_days_observed"] = cagr.get("days_observed")
        except Exception as exc:
            logger.warning("CAGR failed for %s: %s", tkr, exc)

        # 90-day realized volatility
        try:
            vol = get_realized_volatility(tkr, lookback_days=90)
            if vol.get("volatility_pct") is not None:
                snap["volatility"] = round(float(vol["volatility_pct"]), 2)
                snap["volatility_source"] = "live"
                snap["vol_n_returns"] = vol.get("n_returns")
        except Exception as exc:
            logger.warning("vol failed for %s: %s", tkr, exc)

        # 90-day BTC correlation
        try:
            corr = get_btc_correlation(tkr, lookback_days=90)
            c = corr.get("correlation")
            if c is not None:
                snap["correlation_with_btc"] = round(float(c), 4)
                snap["correlation_source"] = (
                    "self" if corr.get("source") == "self" else "live"
                )
                snap["corr_n_returns"] = corr.get("n_returns")
                snap["btc_proxy_used"] = corr.get("btc_proxy_used")
        except Exception as exc:
            logger.warning("corr failed for %s: %s", tkr, exc)

        # Forward-return model estimate
        try:
            fwd = get_forward_return_estimate(
                etf.get("category", ""),
                expense_ratio_bps=etf.get("expense_ratio_bps"),
                underlying=etf.get("underlying"),
            )
            f = fwd.get("forward_return_pct")
            if f is not None:
                snap["forward_return"] = round(float(f), 2)
                snap["forward_return_source"] = "live_long_run"
                snap["forward_return_basis"] = fwd.get("basis", "")
        except Exception as exc:
            logger.warning("forward failed for %s: %s", tkr, exc)

        if snap:
            per_ticker[tkr] = snap

    snapshot = {
        "_metadata": {
            "computed_at_ts":     time.time(),
            "computed_at_iso":    datetime.now(timezone.utc).isoformat(),
            "universe_size":      n_total,
            "tickers_with_data":  len(per_ticker),
            "btc_long_run_cagr_pct": btc_long.get("cagr_pct"),
            "eth_long_run_cagr_pct": eth_long.get("cagr_pct"),
            "schema_version":     1,
        },
        "etfs": per_ticker,
    }
    return snapshot


def write_snapshot(snapshot: dict) -> None:
    """Atomic write — tempfile + os.replace, with the Windows retry pattern."""
    import tempfile
    ANALYTICS_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8",
        dir=str(ANALYTICS_SNAPSHOT_PATH.parent),
        prefix=".tmp_analytics_", suffix=".json", delete=False,
    ) as tf:
        json.dump(snapshot, tf, indent=2, default=str)
        tmp = tf.name
    for attempt in range(5):
        try:
            os.replace(tmp, str(ANALYTICS_SNAPSHOT_PATH))
            logger.info("Wrote %s", ANALYTICS_SNAPSHOT_PATH)
            return
        except PermissionError:
            time.sleep(0.1 * (attempt + 1))
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise RuntimeError(f"Atomic write to {ANALYTICS_SNAPSHOT_PATH} failed after 5 retries")


def main() -> int:
    t0 = time.monotonic()
    try:
        snap = precompute()
    except Exception as exc:
        logger.exception("precompute() raised: %s", exc)
        return 1
    write_snapshot(snap)
    elapsed = time.monotonic() - t0
    logger.info(
        "Done in %.1fs · %d/%d tickers have analytics",
        elapsed,
        snap["_metadata"]["tickers_with_data"],
        snap["_metadata"]["universe_size"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
