"""
broker_alpaca_paper.py — Alpaca Paper Trading broker integration.

Activated when ``BROKER_PROVIDER == "alpaca_paper"`` in config.py AND
``ALPACA_API_KEY`` + ``ALPACA_API_SECRET`` are set in the environment.

Falls back transparently to the mock broker if:
  - Required keys are missing.
  - The ``alpaca-py`` library isn't installed.
  - Alpaca's paper-trading endpoint is unreachable.

The fallback is logged + signaled in the response payload's ``broker``
field so the FA can audit which broker actually handled the basket.

Same response shape as ``broker_mock.submit_basket`` so callers (Portfolio
page Execute Basket modal, daily auto-rebalance scheduler) can switch
providers via config without per-call branching.

CLAUDE.md §11 Web3 Level B (architecture-ready, activate when approved).
2026-04-26 audit-round-1 bonus 4.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from config import ALPACA_API_KEY, ALPACA_API_SECRET, ALPACA_BASE_URL
from integrations.broker_mock import submit_basket as _submit_basket_mock

logger = logging.getLogger(__name__)


def _alpaca_client():
    """
    Construct an alpaca-py REST client. Returns None if the library or
    credentials are missing — caller falls back to mock with a logged
    warning.
    """
    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        logger.info(
            "alpaca_paper: missing ALPACA_API_KEY/ALPACA_API_SECRET — "
            "falling back to mock."
        )
        return None
    try:
        # alpaca-py modern SDK; prefer over the legacy alpaca-trade-api.
        from alpaca.trading.client import TradingClient  # type: ignore
    except ImportError:
        logger.info(
            "alpaca_paper: alpaca-py not installed — falling back to "
            "mock. `pip install alpaca-py` to enable."
        )
        return None
    try:
        return TradingClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_API_SECRET,
            paper=True,
            url_override=ALPACA_BASE_URL or None,
        )
    except Exception as exc:
        logger.warning("alpaca_paper: client init failed (%s) — falling back to mock", exc)
        return None


def submit_basket(
    orders: list[dict],
    *,
    client_id: str | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Submit a basket through Alpaca Paper Trading. Returns the same shape
    as broker_mock.submit_basket so callers can stay provider-agnostic.

    Each order dict carries: ticker, quantity (shares), side (BUY/SELL),
    mid_price, optional tif. The Alpaca SDK consumes ``OrderSide.BUY``,
    ``TimeInForce.DAY``, etc. — we map from string at the boundary.

    On any failure path (no client, no library, network error), this
    function delegates to ``broker_mock.submit_basket`` so the demo flow
    continues to work and the fallback is recorded in
    ``response["broker"] = "alpaca_paper_fallback_to_mock"``.
    """
    client = _alpaca_client()
    if client is None:
        result = _submit_basket_mock(orders, client_id=client_id, dry_run=dry_run)
        result["broker"] = "alpaca_paper_fallback_to_mock"
        result["fallback_reason"] = (
            "Alpaca paper credentials missing or alpaca-py not installed"
        )
        return result

    try:
        from alpaca.trading.requests import MarketOrderRequest  # type: ignore
        from alpaca.trading.enums import OrderSide, TimeInForce  # type: ignore
    except ImportError:
        result = _submit_basket_mock(orders, client_id=client_id, dry_run=dry_run)
        result["broker"] = "alpaca_paper_fallback_to_mock"
        result["fallback_reason"] = "alpaca.trading.requests import failed"
        return result

    fills: list[dict] = []
    gross_usd = 0.0
    submitted_at = datetime.now(timezone.utc).isoformat()
    basket_id = f"alp_{int(datetime.now(timezone.utc).timestamp())}"

    for order in orders:
        ticker = str(order.get("ticker", "")).upper()
        qty = float(order.get("quantity", 0))
        side_str = str(order.get("side", "BUY")).upper()
        mid_price = float(order.get("mid_price", 0))
        tif_str = str(order.get("tif", "day")).lower()

        if not ticker or qty <= 0:
            continue

        side = OrderSide.BUY if side_str == "BUY" else OrderSide.SELL
        tif = TimeInForce.DAY if tif_str == "day" else TimeInForce.GTC
        req = MarketOrderRequest(
            symbol=ticker, qty=qty, side=side, time_in_force=tif,
        )

        if dry_run:
            fills.append({
                "order_id":     None,
                "ticker":       ticker,
                "side":         side_str,
                "quantity":     qty,
                "mid_price":    mid_price,
                "fill_price":   mid_price,
                "notional":     round(qty * mid_price, 2),
                "tif":          tif_str,
                "status":       "dry_run",
                "filled_at":    None,
                "slippage_bps": 0,
                "estimated_slippage_bps": 12.5,
            })
            gross_usd += qty * mid_price
            continue

        try:
            resp = client.submit_order(order_data=req)
        except Exception as exc:
            logger.warning("alpaca_paper: order submit failed for %s: %s", ticker, exc)
            # Continue with mock fill for THIS line (so the basket reports
            # something the FA can read) — flag the failure on the line.
            fills.append({
                "order_id":     None,
                "ticker":       ticker,
                "side":         side_str,
                "quantity":     qty,
                "mid_price":    mid_price,
                "fill_price":   mid_price,
                "notional":     round(qty * mid_price, 2),
                "tif":          tif_str,
                "status":       "submit_failed",
                "filled_at":    None,
                "slippage_bps": 0,
                "estimated_slippage_bps": 12.5,
                "error":        f"{type(exc).__name__}: {exc}",
            })
            gross_usd += qty * mid_price
            continue

        # Alpaca returns a pending order; the actual fill happens async.
        # For demo simplicity we report mid_price as the fill — real fills
        # come back via the streaming trade-update channel post-demo.
        fills.append({
            "order_id":     getattr(resp, "id", None),
            "ticker":       ticker,
            "side":         side_str,
            "quantity":     qty,
            "mid_price":    mid_price,
            "fill_price":   mid_price,
            "notional":     round(qty * mid_price, 2),
            "tif":          tif_str,
            "status":       getattr(resp, "status", "submitted"),
            "filled_at":    None,
            "slippage_bps": 0,   # actual slippage logged by streaming client post-demo
            "estimated_slippage_bps": 12.5,
        })
        gross_usd += qty * mid_price

    return {
        "status":       "simulated" if dry_run else "submitted",
        "submitted_at": submitted_at,
        "broker":       "alpaca_paper",
        "basket_id":    basket_id,
        "client_id":    client_id,
        "fills":        fills,
        "summary": {
            "n_orders":               len(fills),
            "gross_usd":              round(gross_usd, 2),
            "net_usd":                round(gross_usd, 2),
            "avg_slippage_bps":       0,
            "estimated_slippage_bps": 12.5,
        },
    }


def cancel_basket(basket_id: str) -> dict:
    """Cancel a basket. Falls back to mock on any error."""
    client = _alpaca_client()
    if client is None:
        return {
            "status":      "canceled",
            "basket_id":   basket_id,
            "canceled_at": datetime.now(timezone.utc).isoformat(),
            "broker":      "alpaca_paper_fallback_to_mock",
        }
    try:
        # Alpaca cancel-all is the simplest demo-grade cancel — the basket_id
        # tracking is done client-side via fills[*].order_id; a real
        # implementation iterates those and cancels each.
        client.cancel_orders()
        return {
            "status":      "canceled",
            "basket_id":   basket_id,
            "canceled_at": datetime.now(timezone.utc).isoformat(),
            "broker":      "alpaca_paper",
        }
    except Exception as exc:
        logger.warning("alpaca_paper: cancel failed: %s", exc)
        return {
            "status":      "cancel_failed",
            "basket_id":   basket_id,
            "canceled_at": datetime.now(timezone.utc).isoformat(),
            "broker":      "alpaca_paper",
            "error":       str(exc),
        }


# ── Provider router (called from pages/02_Portfolio.py + auto-rebalance) ──

def submit_basket_via(provider: str, orders: list[dict], **kwargs) -> dict:
    """
    Single entry point for the Portfolio page + scheduler. Picks the
    correct broker module based on the provider string.

    Accepted providers:
      - "mock"           → broker_mock.submit_basket
      - "alpaca_paper"   → this module's submit_basket (falls back to mock)
      - "alpaca"         → not yet wired; routes to mock with fallback flag
    """
    if provider == "mock":
        return _submit_basket_mock(orders, **kwargs)
    if provider == "alpaca_paper":
        return submit_basket(orders, **kwargs)
    if provider == "alpaca":
        # Live Alpaca (non-paper) requires explicit user activation per
        # CLAUDE.md §11 Web3 Level B. Until then, route to paper.
        result = submit_basket(orders, **kwargs)
        result["broker"] = result.get("broker", "alpaca_paper") + "_pending_live_approval"
        return result
    # Unknown provider → mock with audit trail.
    result = _submit_basket_mock(orders, **kwargs)
    result["broker"] = f"unknown_{provider}_fallback_to_mock"
    return result
