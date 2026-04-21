"""
demo_clients.py — 3 fictional advisor clients used when DEMO_MODE is True.

All data is synthetic and explicitly labeled as demo — CLAUDE.md §22 item 3.
No real client data anywhere in the repo.

Each client profile carries:
  id, name, age, label, assigned_tier, total_portfolio_usd,
  crypto_allocation_pct, last_rebalance_iso, drift_pct, rebalance_needed,
  notes
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _iso_days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


DEMO_CLIENTS: list[dict] = [
    {
        "id":                    "demo_001",
        "name":                  "Beatrice Chen",
        "label":                 "Retired, risk-averse",
        "age":                   68,
        "assigned_tier":         "Ultra Conservative",
        "total_portfolio_usd":   1_250_000,
        "crypto_allocation_pct": 3.0,
        "last_rebalance_iso":    _iso_days_ago(92),
        "drift_pct":             0.8,
        "rebalance_needed":      False,
        "notes": (
            "Long-term client; income-focused. Recently added 3% crypto "
            "allocation after explicit client request. Quarterly rebalance."
        ),
    },
    {
        "id":                    "demo_002",
        "name":                  "Marcus Avery",
        "label":                 "Mid-career, moderate",
        "age":                   44,
        "assigned_tier":         "Moderate",
        "total_portfolio_usd":   580_000,
        "crypto_allocation_pct": 14.0,
        "last_rebalance_iso":    _iso_days_ago(35),
        "drift_pct":             6.2,
        "rebalance_needed":      True,
        "notes": (
            "20-year horizon. Crypto sleeve drifted above target after "
            "recent rally — recommend rebalance at next review."
        ),
    },
    {
        "id":                    "demo_003",
        "name":                  "Priya Patel",
        "label":                 "High-conviction allocator",
        "age":                   31,
        "assigned_tier":         "Ultra Aggressive",
        "total_portfolio_usd":   310_000,
        "crypto_allocation_pct": 42.0,
        "last_rebalance_iso":    _iso_days_ago(11),
        "drift_pct":             2.1,
        "rebalance_needed":      False,
        "notes": (
            "Self-directed background; understands tail risk. "
            "Bi-weekly rebalance cadence. Client prefers diversified basket "
            "over concentrated BTC spot."
        ),
    },
]


def get_client(client_id: str) -> dict | None:
    for c in DEMO_CLIENTS:
        if c["id"] == client_id:
            return dict(c)
    return None
