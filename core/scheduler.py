"""
core/scheduler.py — daily auto-rebalance hook.

User directive 2026-04-27: after each daily scanner run finds + auto-
approves new ETFs, the system automatically recalculates every demo
client's portfolio so the FA sees fresh allocations the next time they
load the app — no manual "refresh" click required.

Architecture:

    9 AM EST cron (.github/workflows/daily_scanner.yml)
        -> core.etf_universe.daily_scanner(days_back=3)
            -> core.etf_review_queue.add_pending(filings)
                -> auto_approve / auto_reject / pending
                -> writes data/etf_user_additions.json on auto_approve
        -> core.scheduler.recalculate_all_portfolios()
            -> for each demo client:
                build_portfolio(tier, universe_with_new_additions, sleeve)
            -> writes data/portfolio_snapshot.json
        -> git commit + push (workflow step)

Streamlit pages read data/portfolio_snapshot.json on render so the
freshly-recalculated baskets are immediately visible — no waiting for
the @st.cache_data TTL to expire.

The snapshot file is also surfaced on the Dashboard as "Last
auto-rebalance: <iso-timestamp>" so the FA has a visible audit
of when the routine last ran.

CLAUDE.md governance: §11 (Web3 Level B autonomous behaviors), §12
(data refresh + cache invalidation), §22 (post-demo polish).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = REPO_ROOT / "data" / "portfolio_snapshot.json"


def recalculate_all_portfolios(*, sleeve_basis: str = "default") -> dict[str, Any]:
    """
    Recompute the portfolio for every demo client against the current
    (possibly newly-expanded) universe and persist to disk.

    Returns a dict with:
      timestamp:      ISO UTC of the recalculation
      universe_size:  count of ETFs considered
      clients: {
          <client_id>: {
              name, tier, sleeve_usd, n_holdings,
              metrics: {weighted_return_pct, sharpe_ratio, ...},
              top_holdings: [{ticker, weight_pct, usd_value}, ...]
          }
      }

    Errors on any single client do not block the rest — that client
    gets logged + skipped so a partial bad universe entry can't break
    the whole nightly job.
    """
    from core.client_adapter import get_active_clients
    from core.etf_universe import load_universe_with_live_analytics
    from core.portfolio_engine import build_portfolio

    universe = load_universe_with_live_analytics()
    # Sprint 3: client list now flows through the pluggable adapter
    # (demo by default; can be Wealthbox / Redtail / Salesforce FSC /
    # CSV import via CLIENT_ADAPTER_PROVIDER env var).
    active_clients = get_active_clients()
    snapshot: dict[str, Any] = {
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "universe_size": len(universe),
        "clients":       {},
    }

    for c in active_clients:
        try:
            # Sprint 3 guard: CRM-imported clients ship with
            # total_portfolio_usd=0 / assigned_tier="(unassigned)"
            # until the advisor enters those values via the platform.
            # Skip them rather than feeding $0 sleeves to build_portfolio.
            if (
                c.get("total_portfolio_usd", 0) <= 0
                or c.get("assigned_tier", "(unassigned)") == "(unassigned)"
            ):
                snapshot["clients"][c["id"]] = {
                    "name":   c["name"],
                    "tier":   c.get("assigned_tier", "(unassigned)"),
                    "sleeve_usd":    0.0,
                    "n_holdings":    0,
                    "skipped":       "import-incomplete",
                    "metrics":       {},
                    "top_holdings":  [],
                }
                continue
            sleeve = c["total_portfolio_usd"] * c["crypto_allocation_pct"] / 100.0
            p = build_portfolio(
                c["assigned_tier"], universe,
                portfolio_value_usd=sleeve,
            )
            metrics = p.get("metrics", {})
            holdings = p.get("holdings", [])
            top = sorted(holdings, key=lambda h: h.get("weight_pct", 0), reverse=True)[:5]
            snapshot["clients"][c["id"]] = {
                "name":          c["name"],
                "tier":          c["assigned_tier"],
                "sleeve_usd":    sleeve,
                "n_holdings":    len(holdings),
                "metrics": {
                    "weighted_return_pct":      metrics.get("weighted_return_pct"),
                    "portfolio_volatility_pct": metrics.get("portfolio_volatility_pct"),
                    "sharpe_ratio":             metrics.get("sharpe_ratio"),
                    "max_drawdown_pct":         metrics.get("max_drawdown_pct"),
                    "var_95_pct":               metrics.get("var_95_pct"),
                },
                "top_holdings": [
                    {
                        "ticker":     h.get("ticker"),
                        "weight_pct": h.get("weight_pct"),
                        "usd_value":  h.get("usd_value"),
                        "category":   h.get("category"),
                    }
                    for h in top
                ],
            }
        except Exception as exc:
            logger.warning(
                "recalculate_all_portfolios: client %s failed: %s",
                c.get("id"), exc,
            )
            snapshot["clients"][c["id"]] = {
                "name":  c.get("name"),
                "tier":  c.get("assigned_tier"),
                "error": f"{type(exc).__name__}: {exc}",
            }

    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    tmp.replace(SNAPSHOT_PATH)
    logger.info("Auto-rebalance snapshot written: %s clients @ %s",
                len(snapshot["clients"]), snapshot["timestamp"])

    # 2026-04-29 Sprint 2 commit 4: pre-warm the ETF flow data cache
    # for all 211 universe tickers so the FA opens ETF Detail to
    # sub-100ms tile renders the next morning. Continues past per-
    # ticker failures; persists a summary that the page header reads
    # for the "data freshness" indicator.
    try:
        flow_summary = prewarm_etf_flow_cache(universe)
        snapshot["flow_prewarm"] = flow_summary
        # Re-persist the snapshot now that flow_prewarm is populated.
        tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        tmp.replace(SNAPSHOT_PATH)
    except Exception as exc:
        logger.warning("Flow prewarm failed (non-fatal): %s", exc)

    return snapshot


def prewarm_etf_flow_cache(universe: list[dict]) -> dict[str, Any]:
    """
    Walk the universe and pre-populate `data/etf_flow_cache.json` for
    every ticker by calling get_etf_aum / get_etf_30d_net_flow /
    get_etf_avg_daily_volume. By the time the FA opens ETF Detail in
    the morning, the cache is warm and tile renders are instant
    (cache-hit path inside each fetcher).

    Returns a per-source summary suitable for display in the page-
    header "data freshness" indicator:

      {
        "warmed_at_utc":   "<iso>",
        "n_total":         211,
        "aum":  {"yfinance": 18, "SEC EDGAR": 4, ..., "snapshot": 188, "none": 1},
        "flow": {...},
        "vol":  {...}
      }

    Continues past per-ticker failures so a transient yfinance hiccup
    on one ticker doesn't block the rest. The per-fetcher cache layer
    in integrations/etf_flow_data already deduplicates calls within
    24h, so this is idempotent (safe to call multiple times per day).
    """
    from collections import Counter
    from integrations.etf_flow_data import (
        get_etf_aum,
        get_etf_30d_net_flow,
        get_etf_avg_daily_volume,
    )

    aum_sources: Counter = Counter()
    flow_sources: Counter = Counter()
    vol_sources: Counter = Counter()

    for entry in universe:
        tkr = entry.get("ticker") or ""
        if not tkr:
            continue
        try:
            _, aum_src = get_etf_aum(tkr)
            aum_sources[aum_src or "none"] += 1
        except Exception as exc:
            logger.info("prewarm AUM failed for %s: %s", tkr, exc)
            aum_sources["error"] += 1
        try:
            _, flow_src = get_etf_30d_net_flow(tkr)
            flow_sources[flow_src or "none"] += 1
        except Exception as exc:
            logger.info("prewarm flow failed for %s: %s", tkr, exc)
            flow_sources["error"] += 1
        try:
            _, vol_src = get_etf_avg_daily_volume(tkr)
            vol_sources[vol_src or "none"] += 1
        except Exception as exc:
            logger.info("prewarm vol failed for %s: %s", tkr, exc)
            vol_sources["error"] += 1

    summary = {
        "warmed_at_utc":  datetime.now(timezone.utc).isoformat(),
        "n_total":        len(universe),
        "aum":            dict(aum_sources),
        "flow":           dict(flow_sources),
        "vol":            dict(vol_sources),
    }
    logger.info(
        "Flow prewarm complete: AUM live=%d/snapshot=%d/none=%d "
        "Flow live=%d/snapshot=%d/none=%d  Vol live=%d/snapshot=%d/none=%d",
        sum(c for s, c in aum_sources.items() if "snapshot" not in (s or "") and s != "none"),
        sum(c for s, c in aum_sources.items() if "snapshot" in (s or "")),
        aum_sources.get("none", 0),
        sum(c for s, c in flow_sources.items() if "snapshot" not in (s or "") and s != "none"),
        sum(c for s, c in flow_sources.items() if "snapshot" in (s or "")),
        flow_sources.get("none", 0),
        sum(c for s, c in vol_sources.items() if "snapshot" not in (s or "") and s != "none"),
        sum(c for s, c in vol_sources.items() if "snapshot" in (s or "")),
        vol_sources.get("none", 0),
    )
    return summary


def load_latest_snapshot() -> dict[str, Any] | None:
    """Read the most recent auto-rebalance snapshot, or None if missing."""
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Snapshot unreadable: %s", exc)
        return None


def snapshot_age_hours() -> float | None:
    """How many hours since the last successful auto-rebalance, or None."""
    snap = load_latest_snapshot()
    if not snap:
        return None
    try:
        ts = datetime.fromisoformat(snap["timestamp"].replace("Z", "+00:00"))
    except (ValueError, KeyError):
        return None
    delta = datetime.now(timezone.utc) - ts
    return delta.total_seconds() / 3600.0
