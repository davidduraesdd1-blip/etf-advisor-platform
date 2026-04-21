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
            "Beatrice retired from 38 years in public-school administration. "
            "Income-first: ~$55K/yr drawdown target. Explicitly asked about "
            "crypto last January after her grandson mentioned Bitcoin ETFs "
            "at Thanksgiving. Comfortable only with a small allocation in "
            "the lowest-expense BTC spot funds — I've kept her in IBIT and "
            "BITB because of the sub-25bps fees. Quarterly reviews; her "
            "daughter joins the call."
        ),
        "situation_today": (
            "Portfolio is on target. She wants to know whether to top up "
            "the crypto sleeve from 3% to 4% given year-end tax planning. "
            "Hold the line at 3% — that's inside her stated comfort zone."
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
            "Marcus is a mechanical engineer at a regional utility, 20-year "
            "horizon to target retirement. Moderate risk by his own "
            "assessment. Moved to a 14% crypto sleeve in 2025 after reading "
            "the BlackRock tokenization paper. Prefers a diversified basket "
            "over single-ETF concentration. Bi-monthly rebalance cadence."
        ),
        "situation_today": (
            "His sleeve has drifted to ~16% after BTC's recent rally — 6.2% "
            "drift flagged. Recommend rebalancing back to 14% target. The "
            "basket construction currently overweights FBTC which has "
            "outperformed since last rebalance."
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
            "Priya is a senior engineer at a derivatives trading firm. She "
            "understands the volatility profile and has explicitly signed "
            "off on a 42% crypto allocation as part of her long-horizon "
            "growth sleeve. Bi-weekly rebalance cadence. Prefers multi-ETF "
            "diversification over single-issuer concentration. She watches "
            "the EDGAR filings herself and will ask about new spot "
            "approvals (ETH, SOL) the moment they file."
        ),
        "situation_today": (
            "Sleeve is on target. No rebalance needed this week. She's "
            "asked whether we'd add Solana exposure once a spot SOL ETF "
            "clears the SEC — the daily scanner flagged a new S-1 filing "
            "two weeks ago. Confirm it's on our watchlist, no action today."
        ),
    },
]


def get_client(client_id: str) -> dict | None:
    for c in DEMO_CLIENTS:
        if c["id"] == client_id:
            return dict(c)
    return None
