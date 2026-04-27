"""
scripts/refresh_etf_flow_production.py — capture live ETF flow data
across all 211 universe tickers and write the production-snapshot
file at `core/etf_flow_production.json`.

Polish round 5, Sprint 2, Commit 2 (2026-04-29).

Usage:
    python scripts/refresh_etf_flow_production.py

Patient retry pattern (same as core.cf_calibration.fit_per_category):
  * 5-attempt exponential backoff per ticker per fetcher
  * 30-second cooldown between batches of 20 tickers (rate-limit relief)
  * Resume-from-progress: if interrupted, restart and skip already-
    captured tickers
  * Per-ticker source attribution: each entry carries `aum_source`,
    `flow_source`, `vol_source` so the UI can show which upstream
    feed produced the value at capture time

NO hardcoded fallback constants per the no-fallback policy. When all
chain steps fail for a ticker × metric, the snapshot entry has
`<field>: null` and the UI renders an em-dash. The next nightly cron
run picks it up when the upstream source is healthy.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add repo root to path so this script can import core / integrations.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OUT_PATH = REPO_ROOT / "core" / "etf_flow_production.json"
BATCH_SIZE = 20
INTER_BATCH_COOLDOWN_SEC = 30


def _load_existing() -> dict:
    """Resume-from-progress: load the existing snapshot if any."""
    if not OUT_PATH.exists():
        return {"captured_at_utc": None, "method": "", "tickers": {}}
    try:
        return json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"captured_at_utc": None, "method": "", "tickers": {}}


def _save_partial(data: dict) -> None:
    """Atomic write — writes partial progress so an interrupt doesn't
    lose the captured work so far."""
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(OUT_PATH)


def main() -> None:
    # Force live fetches (script is run manually post-Sprint-2-merge or
    # from the nightly cron — DEMO_MODE_NO_FETCH=0 either way).
    os.environ.pop("DEMO_MODE_NO_FETCH", None)
    os.environ["DEMO_MODE_NO_FETCH"] = "0"

    from core.etf_universe import load_universe
    from integrations.etf_flow_data import (
        get_etf_aum,
        get_etf_30d_net_flow,
        get_etf_avg_daily_volume,
        reset_circuit_breaker_safely,
    )

    universe = load_universe()
    tickers = [e["ticker"] for e in universe]
    total = len(tickers)
    logger.info("Capturing flow data for %d tickers (batch size %d, cooldown %ds)",
                total, BATCH_SIZE, INTER_BATCH_COOLDOWN_SEC)

    snapshot = _load_existing()
    snapshot["method"] = (
        "live multi-source fetch via integrations/etf_flow_data.py — "
        "AUM chain (yfinance → EDGAR N-PORT → ETF.com → issuer-site), "
        "Flow chain (cryptorank → SoSoValue → Farside → N-PORT-derived), "
        "Vol chain (yfinance 3M → 10D → ETF.com → 60D history mean)"
    )
    captured_tickers = snapshot.setdefault("tickers", {})
    # Resume-from-progress: only skip tickers that already have at least one
    # captured value. Bootstrap entries with all-null fields must be retried,
    # otherwise the script no-ops on first run after the bootstrap commit.
    def _has_real_data(entry: dict) -> bool:
        return any(
            entry.get(k) is not None
            for k in ("aum_usd", "flow_30d_usd", "avg_daily_vol")
        )
    already = {t for t, v in captured_tickers.items() if _has_real_data(v)}

    # Process in batches with cooldowns between batches.
    completed_in_run = 0
    for batch_start in range(0, total, BATCH_SIZE):
        batch = tickers[batch_start:batch_start + BATCH_SIZE]
        for tkr in batch:
            if tkr in already:
                continue
            try:
                aum_v, aum_src = get_etf_aum(tkr)
                flow_v, flow_src = get_etf_30d_net_flow(tkr)
                vol_v, vol_src = get_etf_avg_daily_volume(tkr)
                captured_tickers[tkr] = {
                    "aum_usd":       aum_v,
                    "flow_30d_usd":  flow_v,
                    "avg_daily_vol": vol_v,
                    "aum_source":    aum_src,
                    "flow_source":   flow_src,
                    "vol_source":    vol_src,
                }
                completed_in_run += 1
                logger.info(
                    "  %-6s aum=%s (%s) flow=%s (%s) vol=%s (%s)",
                    tkr,
                    f"${aum_v/1e9:.2f}B" if aum_v else "—",
                    aum_src or "—",
                    f"${flow_v/1e6:.0f}M" if flow_v else "—",
                    flow_src or "—",
                    f"{vol_v/1e6:.1f}M" if vol_v else "—",
                    vol_src or "—",
                )
            except Exception as exc:
                logger.warning("  %s capture failed: %s", tkr, exc)
                captured_tickers[tkr] = {
                    "aum_usd": None, "flow_30d_usd": None, "avg_daily_vol": None,
                    "aum_source": None, "flow_source": None, "vol_source": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            # Persist after every ticker (resume-from-progress safety).
            snapshot["captured_at_utc"] = datetime.now(timezone.utc).isoformat()
            _save_partial(snapshot)

        # Inter-batch cooldown, except after the last batch.
        if batch_start + BATCH_SIZE < total:
            logger.info("  --- cooldown %ds before next batch ---", INTER_BATCH_COOLDOWN_SEC)
            try:
                reset_circuit_breaker_safely()
            except Exception:
                pass
            time.sleep(INTER_BATCH_COOLDOWN_SEC)

    # Final summary.
    n_with_aum = sum(1 for v in captured_tickers.values() if v.get("aum_usd"))
    n_with_flow = sum(1 for v in captured_tickers.values() if v.get("flow_30d_usd"))
    n_with_vol = sum(1 for v in captured_tickers.values() if v.get("avg_daily_vol"))
    logger.info("─" * 60)
    logger.info("Capture complete (%d new this run):", completed_in_run)
    logger.info("  AUM live:  %d / %d", n_with_aum, total)
    logger.info("  Flow live: %d / %d", n_with_flow, total)
    logger.info("  Vol live:  %d / %d", n_with_vol, total)
    logger.info("Wrote %s", OUT_PATH)


if __name__ == "__main__":
    main()
