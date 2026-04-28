# Client adapter chain — full specification

Polish round 5, Sprint 3 (2026-04-30).

This document specifies the pluggable client-data abstraction that
lets the platform read its advisor client roster from any of the
following sources without UI changes:

- `demo`           — built-in synthetic clients (Beatrice / Marcus / Priya)
- `csv_import`     — read from a local CSV
- `wealthbox`      — live Wealthbox CRM via REST API
- `redtail`        — live Redtail CRM via REST API
- `salesforce_fsc` — live Salesforce Financial Services Cloud via REST

Cowork directive (Sprint 3): "fully live as much as possible." Each
CRM adapter performs real HTTP calls when its credentials are set.
There are no stubs that raise NotImplementedError — adapters return
`[]` when not configured, and the factory falls back to the demo
adapter so the demo deploy never breaks.

## Architecture

```
                ┌─ DemoClientAdapter             (always configured)
                ├─ CSVImportClientAdapter        (configured iff file exists)
get_active_     ├─ WealthboxClientAdapter        (configured iff key set)
adapter() ──────┤  RedtailClientAdapter          (configured iff creds set)
                └─ SalesforceFSCClientAdapter    (configured iff URL+token set)
                            │
                            └──► ClientRecord (frozen dataclass)
                                  → consumed by Dashboard / Portfolio /
                                    scheduler.py / Settings panel
```

The factory reads the `CLIENT_ADAPTER_PROVIDER` env var (default
`"demo"`) and returns the matching adapter. If the requested provider
isn't configured (no API key, missing file, etc.), the factory falls
back to demo and logs a warning. The demo deploy is bullet-proof.

## Adapter contract

Every adapter inherits from `core.client_adapter.ClientAdapter`:

```python
class ClientAdapter(ABC):
    @abstractmethod
    def provider_name(self) -> str: ...      # "demo", "wealthbox", ...
    @abstractmethod
    def is_configured(self) -> bool: ...     # creds/file present?
    @abstractmethod
    def list_clients(self) -> list[ClientRecord]: ...
    def get_client(self, client_id: str) -> Optional[ClientRecord]: ...
```

`list_clients()` MUST return `[]` on any non-fatal upstream error
(HTTP timeout, 401, JSON parse fail, missing file). Never raises.
This is what lets the factory's fall-through-to-demo work cleanly.

## ClientRecord wire format

```python
@dataclass(frozen=True)
class ClientRecord:
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

    def to_dict(self) -> dict: ...   # legacy dict shape
```

`assigned_tier`, `total_portfolio_usd`, and `crypto_allocation_pct`
are advisor-platform-specific concepts that don't exist in any CRM.
CRM adapters leave them as `(unassigned)` / `0.0` / `0.0`. The
advisor sets them via the platform's Onboarding flow after import.
This is intentional: CRM remains source-of-truth for identity and
contact metadata; the platform owns portfolio + risk-tier data.

## Per-adapter configuration

### `demo` (default)

No configuration. Always available. Returns the 3 fictional clients
(Beatrice Chen / Marcus Avery / Priya Patel) from
`core/demo_clients.py`. Used for the demo deploy and as the
fall-back when any other adapter is misconfigured.

### `csv_import`

CSV file at `data/clients_import.csv` (gitignored). Columns:

```
id, name, age, label, assigned_tier, total_portfolio_usd,
crypto_allocation_pct, last_rebalance_iso, drift_pct,
rebalance_needed, notes, situation_today
```

Only `id` and `name` are required; missing optional columns get
sensible defaults. Tolerates `$1,250,000` and `12.5%` formatted
numbers (strips `$`, `,`, `%`).

Override path: set `CLIENT_CSV_PATH` to a custom location (e.g., a
network share mounted at `/mnt/clientshare/portfolios.csv`).

Privacy: `data/clients_import.csv` is in `.gitignore`. NEVER check
real client data into the repo.

### `wealthbox`

Env vars:
- `WEALTHBOX_API_KEY` (required)

API: `https://api.crmworkspace.com/v1/contacts` (paginated, 100/page).
Auth header: `ACCESS_TOKEN: <api_key>`.

Field mapping:
- `id` ← `wealthbox_<contact.id>`
- `name` ← `first_name` + `" "` + `last_name`
- `age` ← computed from `birthdate` (yyyy-mm-dd)
- `label` ← `contact_type` (e.g., "Client", "Prospect")
- `notes` ← `background_information` (truncated to 1KB)

In-memory cache: 5-minute TTL keyed by API key.

### `redtail`

Two auth patterns supported:

**(a) Per-app key (preferred):**
- `REDTAIL_API_KEY`

**(b) Basic-auth triple (older subscriptions):**
- `REDTAIL_USERKEY` (32-char hex)
- `REDTAIL_USERNAME`
- `REDTAIL_PASSWORD`

API: `https://api.redtailtechnology.com/crm/v1/contacts` (paginated).

Field mapping:
- `id` ← `redtail_<contact.id>`
- `name` ← `full_name` (or `first_name + " " + last_name`)
- `age` ← computed from `dob`
- `label` ← `status` or `type` (e.g., "Active Client")

In-memory cache: 5-minute TTL keyed by sha256 of the auth tuple, so
credential rotation invalidates the cache immediately.

### `salesforce_fsc`

Env vars:
- `SALESFORCE_FSC_INSTANCE_URL` (required) — e.g. `https://yourorg.my.salesforce.com`
- `SALESFORCE_FSC_ACCESS_TOKEN` (required) — current bearer token
- `SALESFORCE_FSC_API_VERSION` (optional) — defaults to `v60.0`
- `SALESFORCE_FSC_QUERY` (optional) — overrides the default SOQL

Default SOQL:

```sql
SELECT Id, Name, FinServ__BirthDate__c, FinServ__Status__c
FROM Account
WHERE RecordType.Name = 'Client'
LIMIT 1000
```

Walks Salesforce's `nextRecordsUrl` pagination automatically.

Field mapping:
- `id` ← `sfdc_<Account.Id>`
- `name` ← `Account.Name`
- `age` ← computed from `FinServ__BirthDate__c`
- `label` ← `FinServ__Status__c`

**Token refresh is OUT OF SCOPE for this adapter.** Salesforce
access tokens expire (typically 2 hours). The adapter assumes a
separate token-refresh process maintains a fresh
`SALESFORCE_FSC_ACCESS_TOKEN` env var. For demo purposes, generate
a token via the Salesforce CLI (`sf org login`) and paste it.
Production: wire a background OAuth 2.0 client-credentials refresh
(post-demo work, see `pending_work.md`).

## Fall-through-to-demo behavior

```python
get_active_adapter()
```

reads `CLIENT_ADAPTER_PROVIDER`. The fall-through ladder:

1. If the env var names an unregistered provider → fall back to demo.
2. If the env var names a registered provider but `is_configured()`
   returns False → fall back to demo.
3. Otherwise return the configured adapter.

Both fall-back cases log a warning. The demo deploy NEVER breaks
on misconfiguration.

## Privacy guarantees

- Real client data NEVER touches the repo.
- The CSV adapter reads from a gitignored path.
- CRM adapters fetch over HTTPS at runtime; nothing is persisted to
  disk by the adapters themselves.
- The 5-min in-memory cache is process-local and discarded on
  Streamlit Cloud cold-restart.
- No client PII appears in any test fixture or unit test in this
  repo (test fixtures use synthetic names like "Alice Cooper",
  "Greta Lindqvist").

## Adding a 6th adapter (e.g., Orion, Tamarac, Black Diamond)

1. Create `core/client_adapters/<provider>_adapter.py` implementing
   `ClientAdapter`.
2. Register it at module-import time:
   ```python
   register_adapter("<provider>", YourAdapter)
   ```
3. Import the new module in `core/client_adapters/__init__.py`.
4. Add tests to `tests/test_client_adapter.py` (un-configured-empty,
   configured-fetch-roundtrip, 401-graceful, cache-hit).
5. Add docs to this file under "Per-adapter configuration".
6. The Settings panel and Dashboard automatically pick up the new
   provider via the registry — no UI changes needed.

## CLAUDE.md governance

- §10 — multi-source provenance (CRM is one provenance source among many)
- §11 — env-scoped runtime state (no real client data in the repo)
- §22 — no-fallback honesty (CRM API failures surface as empty list,
  never as fabricated client records)
