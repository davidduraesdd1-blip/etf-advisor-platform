"""
core/client_adapters/ — concrete ClientAdapter implementations.

Importing this package registers all 5 adapters (demo + csv_import +
wealthbox + redtail + salesforce_fsc) under their provider names.
The registry is consumed by `core.client_adapter.get_active_adapter()`.

Adapter registration is intentionally side-effect-on-import so the
factory in `core.client_adapter` doesn't need to know about each
adapter at import time — adapters self-register when this package
is imported.
"""
from __future__ import annotations

# Side-effect imports register each adapter with the central registry.
from core.client_adapters import demo_adapter        # noqa: F401
from core.client_adapters import csv_import_adapter  # noqa: F401
from core.client_adapters import wealthbox_adapter   # noqa: F401
from core.client_adapters import redtail_adapter     # noqa: F401
from core.client_adapters import salesforce_fsc_adapter  # noqa: F401
