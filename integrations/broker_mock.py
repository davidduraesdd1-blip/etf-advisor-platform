"""
broker_mock.py — stubbed broker execution for demo mode.

submit_basket(orders) returns a realistic order-confirmation payload
without hitting any external API. Post-demo, BROKER_PROVIDER flips from
"mock" to "alpaca_paper" and we wire the real Alpaca REST client.

The shape of the returned dict matches what we'll get from Alpaca's
/v2/orders endpoint (subset) so the UI code written on Day 3 against
this mock will work unchanged against the real broker.

CLAUDE.md governance: §2 (BROKER_PROVIDER), §22 (demo constraints).
"""
from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Realistic demo-mode slippage bounds in basis points (0.05% - 0.20%).
# TODO: retune for real broker (Alpaca Pro: typical 8-15 bps on major
# crypto ETFs during market hours). Day-3+ once BROKER_PROVIDER flips
# to "alpaca_paper", rewire these bounds from live bid/ask spread data.
_SLIPPAGE_BPS_RANGE = (5, 20)
_ESTIMATED_SLIPPAGE_BPS = sum(_SLIPPAGE_BPS_RANGE) / 2   # midpoint = 12.5 bps


def _mock_order_id(ticker: str, qty: float, side: str) -> str:
    """Short deterministic-looking ID (not actually deterministic — uses time)."""
    raw = f"{ticker}:{qty}:{side}:{datetime.now(timezone.utc).isoformat()}:{random.random()}"
    return "mock_" + hashlib.md5(raw.encode()).hexdigest()[:16]


def _apply_slippage(price: float, side: str) -> float:
    """BUY fills slightly above mid; SELL slightly below. Bounded 5-20 bps."""
    bps = random.uniform(*_SLIPPAGE_BPS_RANGE) / 10_000
    direction = 1 if side.upper() == "BUY" else -1
    return round(price * (1 + direction * bps), 2)


def submit_basket(
    orders: list[dict],
    *,
    client_id: str | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Submit a basket of orders to the mock broker.

    Each order dict must carry: ticker, quantity (shares), side (BUY/SELL),
    and mid_price (last known mid). Optional: limit_price, tif
    (time-in-force). Returns a confirmation dict shaped like Alpaca's
    bulk-order response.

    dry_run=True returns what WOULD happen without generating fill data.
    """
    if not orders:
        return {
            "status":        "empty",
            "submitted_at":  datetime.now(timezone.utc).isoformat(),
            "broker":        "mock",
            "basket_id":     None,
            "fills":         [],
            "summary": {"n_orders": 0, "gross_usd": 0.0, "net_usd": 0.0},
        }

    basket_id = "basket_" + hashlib.md5(
        f"{client_id or 'demo'}:{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()[:12]

    fills: list[dict] = []
    gross_usd = 0.0

    for order in orders:
        ticker    = str(order.get("ticker", "")).upper()
        qty       = float(order.get("quantity", 0))
        side      = str(order.get("side", "BUY")).upper()
        mid_price = float(order.get("mid_price", 0))
        tif       = str(order.get("tif", "day")).lower()

        if ticker == "" or qty <= 0 or mid_price <= 0:
            logger.warning("Skipping malformed order: %r", order)
            continue

        fill_price = mid_price if dry_run else _apply_slippage(mid_price, side)
        notional   = round(qty * fill_price, 2)
        gross_usd += notional

        fills.append({
            "order_id":    _mock_order_id(ticker, qty, side),
            "ticker":      ticker,
            "side":        side,
            "quantity":    qty,
            "mid_price":   mid_price,
            "fill_price":  fill_price,
            "notional":    notional,
            "tif":         tif,
            "status":      "dry_run" if dry_run else "filled",
            "filled_at":   None if dry_run else datetime.now(timezone.utc).isoformat(),
            "slippage_bps": 0 if dry_run else round(
                ((fill_price / mid_price) - 1) * 10_000, 2
            ),
            # Day-3 Q3 answer: UI reads this to display expected slippage in
            # the mock confirmation modal BEFORE execution. For the real
            # Alpaca integration this will come from live bid/ask spread.
            "estimated_slippage_bps": _ESTIMATED_SLIPPAGE_BPS,
        })

    return {
        "status":        "simulated" if dry_run else "submitted",
        "submitted_at":  datetime.now(timezone.utc).isoformat(),
        "broker":        "mock",
        "basket_id":     basket_id,
        "client_id":     client_id,
        "fills":         fills,
        "summary": {
            "n_orders":               len(fills),
            "gross_usd":              round(gross_usd, 2),
            "net_usd":                round(gross_usd, 2),   # mock: no commission
            "avg_slippage_bps":       round(
                sum(f["slippage_bps"] for f in fills) / max(len(fills), 1), 2
            ),
            "estimated_slippage_bps": _ESTIMATED_SLIPPAGE_BPS,
        },
    }


def cancel_basket(basket_id: str) -> dict:
    """Cancel a basket — mock always succeeds."""
    return {
        "status":       "canceled",
        "basket_id":    basket_id,
        "canceled_at":  datetime.now(timezone.utc).isoformat(),
        "broker":       "mock",
    }
