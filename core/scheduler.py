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
    from core.demo_clients import DEMO_CLIENTS
    from core.etf_universe import load_universe_with_live_analytics
    from core.portfolio_engine import build_portfolio

    universe = load_universe_with_live_analytics()
    snapshot: dict[str, Any] = {
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "universe_size": len(universe),
        "clients":       {},
    }

    for c in DEMO_CLIENTS:
        try:
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
    return snapshot


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
