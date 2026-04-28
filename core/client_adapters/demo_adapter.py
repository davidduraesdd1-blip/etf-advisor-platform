"""
core/client_adapters/demo_adapter.py — wraps core.demo_clients.

Sprint 3 commit 2 (2026-04-30). Always-available adapter. Used as
the default when CLIENT_ADAPTER_PROVIDER is unset, and as the safe
fallback when any other adapter is misconfigured.

`core/demo_clients.py` keeps its bare DEMO_CLIENTS constant + helpers
unchanged — old import paths still work, this module just provides
the ClientAdapter face.
"""
from __future__ import annotations

from core.client_adapter import ClientAdapter, ClientRecord, register_adapter


_REQUIRED_KEYS = (
    "id", "name", "label", "age", "assigned_tier",
    "total_portfolio_usd", "crypto_allocation_pct",
    "last_rebalance_iso", "drift_pct", "rebalance_needed",
    "notes", "situation_today",
)


def _dict_to_record(d: dict) -> ClientRecord:
    """Convert a DEMO_CLIENTS dict to a ClientRecord. Tolerant of
    missing fields — defaults from the dataclass kick in."""
    return ClientRecord(
        id=str(d.get("id", "")),
        name=str(d.get("name", "")),
        label=str(d.get("label", "") or ""),
        age=d.get("age"),
        assigned_tier=str(d.get("assigned_tier", "(unassigned)") or "(unassigned)"),
        total_portfolio_usd=float(d.get("total_portfolio_usd", 0.0) or 0.0),
        crypto_allocation_pct=float(d.get("crypto_allocation_pct", 0.0) or 0.0),
        last_rebalance_iso=d.get("last_rebalance_iso"),
        drift_pct=float(d.get("drift_pct", 0.0) or 0.0),
        rebalance_needed=bool(d.get("rebalance_needed", False)),
        notes=str(d.get("notes", "") or ""),
        situation_today=str(d.get("situation_today", "") or ""),
    )


class DemoClientAdapter(ClientAdapter):
    """Wraps the 3 fictional advisor clients in core.demo_clients."""

    def provider_name(self) -> str:
        return "demo"

    def is_configured(self) -> bool:
        return True   # demo data is always available — synthetic, no creds needed

    def list_clients(self) -> list[ClientRecord]:
        from core.demo_clients import DEMO_CLIENTS
        return [_dict_to_record(d) for d in DEMO_CLIENTS]


register_adapter("demo", DemoClientAdapter)
