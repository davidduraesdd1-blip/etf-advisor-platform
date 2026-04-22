"""
Audit 2026-04-22 coverage gap: broker_mock.submit_basket had no direct
unit tests. The Execute Basket modal on the Portfolio page exercises it
via AppTest, but contract-level assertions (response shape, malformed
orders, dry-run behavior, slippage sign convention) weren't pinned.
"""
from __future__ import annotations

import pytest

from integrations.broker_mock import (
    cancel_basket,
    submit_basket,
    _ESTIMATED_SLIPPAGE_BPS,
)


def _valid_order(**overrides) -> dict:
    base = {
        "ticker":    "IBIT",
        "quantity":  5.0,
        "side":      "BUY",
        "mid_price": 65.00,
        "tif":       "day",
    }
    base.update(overrides)
    return base


class TestSubmitBasketEmpty:
    def test_empty_orders_returns_empty_shape(self):
        r = submit_basket([])
        assert r["status"] == "empty"
        assert r["fills"] == []
        assert r["summary"]["n_orders"] == 0
        assert r["summary"]["gross_usd"] == 0.0


class TestSubmitBasketHappyPath:
    def test_single_order_fills_with_slippage(self):
        orders = [_valid_order()]
        r = submit_basket(orders)
        assert r["status"] == "submitted"
        assert r["broker"] == "mock"
        assert r["basket_id"].startswith("basket_")
        assert len(r["fills"]) == 1
        fill = r["fills"][0]
        assert fill["ticker"] == "IBIT"
        assert fill["side"] == "BUY"
        assert fill["quantity"] == 5.0
        assert fill["mid_price"] == 65.00
        # BUY fills land AT or ABOVE mid (positive slippage)
        assert fill["fill_price"] >= 65.00
        assert fill["notional"] == round(5.0 * fill["fill_price"], 2)
        assert fill["status"] == "filled"
        assert fill["order_id"].startswith("mock_")
        assert fill["estimated_slippage_bps"] == _ESTIMATED_SLIPPAGE_BPS

    def test_sell_slippage_is_negative(self):
        """SELL fills should land AT or BELOW mid."""
        orders = [_valid_order(side="SELL")]
        r = submit_basket(orders)
        fill = r["fills"][0]
        assert fill["fill_price"] <= 65.00

    def test_multi_order_basket_aggregates(self):
        orders = [
            _valid_order(ticker="IBIT", quantity=10, mid_price=65),
            _valid_order(ticker="ETHA", quantity=4,  mid_price=25),
            _valid_order(ticker="BITB", quantity=8,  mid_price=40),
        ]
        r = submit_basket(orders, client_id="demo_001")
        assert r["summary"]["n_orders"] == 3
        assert r["client_id"] == "demo_001"
        # Gross ≈ 10*65 + 4*25 + 8*40 = 650 + 100 + 320 = 1070 (± slippage)
        assert 1050 < r["summary"]["gross_usd"] < 1100


class TestSubmitBasketDryRun:
    def test_dry_run_skips_slippage_and_fill_timestamp(self):
        r = submit_basket([_valid_order()], dry_run=True)
        assert r["status"] == "simulated"
        fill = r["fills"][0]
        assert fill["fill_price"] == 65.00   # exact mid, no slippage
        assert fill["slippage_bps"] == 0
        assert fill["status"] == "dry_run"
        assert fill["filled_at"] is None


class TestSubmitBasketMalformed:
    def test_empty_ticker_order_is_skipped(self):
        orders = [
            _valid_order(),
            _valid_order(ticker=""),   # should be skipped
        ]
        r = submit_basket(orders)
        assert r["summary"]["n_orders"] == 1

    def test_zero_quantity_order_is_skipped(self):
        orders = [_valid_order(), _valid_order(quantity=0)]
        r = submit_basket(orders)
        assert r["summary"]["n_orders"] == 1

    def test_zero_price_order_is_skipped(self):
        orders = [_valid_order(), _valid_order(mid_price=0)]
        r = submit_basket(orders)
        assert r["summary"]["n_orders"] == 1

    def test_negative_quantity_order_is_skipped(self):
        orders = [_valid_order(), _valid_order(quantity=-5)]
        r = submit_basket(orders)
        assert r["summary"]["n_orders"] == 1


class TestCancelBasket:
    def test_cancel_returns_canceled_status(self):
        r = cancel_basket("basket_abc123")
        assert r["status"] == "canceled"
        assert r["basket_id"] == "basket_abc123"
        assert r["broker"] == "mock"
        assert "canceled_at" in r
