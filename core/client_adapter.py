"""
core/client_adapter.py — pluggable client-data source abstraction.

Sprint 3 (2026-04-30). Cowork directive: "make sure we are fully live
as much as possible." Each CRM adapter does real HTTP calls when its
API key/token is present in the environment; falls through to the
demo adapter when no provider is configured.

Architecture:

    ClientRecord            (dataclass — wire format the rest of the app reads)
        ↑
    ClientAdapter           (ABC — list_clients() / get_client() / is_configured() / provider_name())
        ↑
    DemoClientAdapter       (always configured; wraps core.demo_clients.DEMO_CLIENTS)
    CSVImportClientAdapter  (live; reads data/clients_import.csv when present)
    WealthboxClientAdapter  (live HTTP; api.crmworkspace.com/v1/contacts)
    RedtailClientAdapter    (live HTTP; api.redtailcrm.com/v1/contacts)
    SalesforceFSCClientAdapter (live HTTP; <instance>/services/data/v60.0/...)

`get_active_adapter()` factory reads `CLIENT_ADAPTER_PROVIDER` env var
(default `"demo"`) and returns the adapter instance. If the requested
provider isn't configured (no API key set), the factory falls back to
the demo adapter so the demo never breaks.

CLAUDE.md governance:
  §10 — multi-source provenance (CRM is one source among many)
  §11 — env-scoped runtime state (no real client data in the repo)
  §22 — no-fallback honesty (CRM API failures surface as empty list,
        never as fabricated client records)

Privacy:
  Real client data NEVER touches the repo. The CSV adapter reads
  data/clients_import.csv which is gitignored. CRM adapters fetch
  over HTTPS at runtime; nothing is persisted to disk by this module.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Wire format
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ClientRecord:
    """
    Canonical client record consumed by Dashboard / Portfolio /
    scheduler. Field set matches what core.demo_clients.DEMO_CLIENTS
    has historically exposed so existing call-sites stay unchanged
    when this is plumbed in.

    `assigned_tier` and `crypto_allocation_pct` are advisor-platform-
    specific concepts that don't typically live in a CRM. CRM
    adapters that import from upstream contact records leave these
    fields with sensible defaults — the advisor sets them via the
    Onboarding flow after import.
    """
    id:                    str
    name:                  str
    label:                 str = ""
    age:                   Optional[int] = None
    assigned_tier:         str = "(unassigned)"
    total_portfolio_usd:   float = 0.0
    crypto_allocation_pct: float = 0.0
    last_rebalance_iso:    Optional[str] = None
    drift_pct:             float = 0.0
    rebalance_needed:      bool = False
    notes:                 str = ""
    situation_today:       str = ""

    def to_dict(self) -> dict:
        """Render as the legacy dict shape consumed by Dashboard /
        scheduler. Backward compatibility with code that still
        iterates `c["name"]` etc."""
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════
# Adapter ABC
# ═══════════════════════════════════════════════════════════════════════════

class ClientAdapter(ABC):
    """Abstract base for any client-data source.

    Implementation contract:
      - `provider_name()` returns a short stable identifier ("demo",
        "wealthbox", "redtail", "salesforce_fsc", "csv_import").
        Used by the Settings panel + provenance UI badges.
      - `is_configured()` returns True iff the adapter has the
        credentials/file/etc. it needs to actually fetch records.
        Returns False on missing-key, missing-file, or any precondition
        unmet — never raises.
      - `list_clients()` returns a list of ClientRecord. Returns []
        on any non-fatal upstream error (HTTP timeout, 401, etc.) —
        callers must handle the empty case. Logs the failure at INFO.
      - `get_client(id)` returns Optional[ClientRecord]. Default
        implementation iterates list_clients() — adapters with native
        per-record fetch can override for efficiency.
    """

    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def list_clients(self) -> list[ClientRecord]: ...

    def get_client(self, client_id: str) -> Optional[ClientRecord]:
        for r in self.list_clients():
            if r.id == client_id:
                return r
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Factory + registry
# ═══════════════════════════════════════════════════════════════════════════

# Maps the CLIENT_ADAPTER_PROVIDER env var value → factory callable.
# Filled in by import side-effects from each adapter module — see
# core/client_adapters/__init__.py which registers all 5 known
# adapters at import time.
_REGISTRY: dict[str, type] = {}


def register_adapter(name: str, adapter_cls: type) -> None:
    """Register an adapter class under a provider name. Called from
    each adapter module at import time. Re-registration with the
    same name is allowed (last-writer-wins for hot-reload during dev)."""
    _REGISTRY[name.lower()] = adapter_cls


def list_registered_providers() -> list[str]:
    """Return the registered provider names in stable display order."""
    # Stable order: demo first (always available), then alphabetical
    names = sorted(_REGISTRY.keys())
    if "demo" in names:
        names.remove("demo")
        names = ["demo"] + names
    return names


def get_adapter(provider: str) -> ClientAdapter:
    """Construct the adapter for `provider`. Raises KeyError if
    unknown. Used by the Settings panel for per-adapter status rows."""
    cls = _REGISTRY.get(provider.lower())
    if cls is None:
        raise KeyError(f"Unknown client adapter provider: {provider!r}")
    return cls()


def get_active_clients() -> list[dict]:
    """Convenience for legacy call sites that iterate the demo
    client list as `[{...}, {...}]`. Returns the active adapter's
    records as the legacy dict shape (asdict()), so existing
    `c["name"]` / `c["id"]` access keeps working unchanged."""
    return [r.to_dict() for r in get_active_adapter().list_clients()]


def get_active_client(client_id: str) -> Optional[dict]:
    """Per-client variant of get_active_clients()."""
    rec = get_active_adapter().get_client(client_id)
    return rec.to_dict() if rec is not None else None


def get_active_adapter() -> ClientAdapter:
    """Return the active adapter per CLIENT_ADAPTER_PROVIDER env var.
    Defaults to "demo" when unset.

    Fall-through-to-demo: if the requested provider is registered but
    not configured (e.g., WEALTHBOX_API_KEY missing on a deploy where
    CLIENT_ADAPTER_PROVIDER=wealthbox), the factory falls back to the
    demo adapter and logs a warning. This keeps the demo deploy
    bullet-proof while still letting an operator switch providers by
    just setting one env var.
    """
    # Lazy import the adapter registry so test code can install
    # fresh registrations without import-order surprises.
    import core.client_adapters  # noqa: F401  (registers all adapters)

    requested = os.environ.get("CLIENT_ADAPTER_PROVIDER", "demo").strip().lower() or "demo"
    cls = _REGISTRY.get(requested)
    if cls is None:
        logger.warning(
            "CLIENT_ADAPTER_PROVIDER=%r not registered — falling back to demo",
            requested,
        )
        cls = _REGISTRY.get("demo")
        if cls is None:
            raise RuntimeError(
                "No 'demo' adapter registered. "
                "Did core.client_adapters import?"
            )
    inst = cls()
    if not inst.is_configured():
        logger.warning(
            "Adapter %r not configured — falling back to demo",
            requested,
        )
        demo_cls = _REGISTRY.get("demo")
        if demo_cls is None:
            raise RuntimeError("Demo adapter unavailable.")
        return demo_cls()
    return inst
