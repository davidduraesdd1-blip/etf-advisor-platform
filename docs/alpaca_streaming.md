# Alpaca Streaming — Real-time Order Status

**Module:** `integrations/alpaca_streaming.py`
**Sprint:** Sprint 4 (2026-05-01)
**Owner:** ETF Advisor Platform

## Why

Pre-Sprint-4, `integrations/broker_alpaca_paper.py::submit_basket`
returned `mid_price` as the "fill price" for every order. The actual
fill happens asynchronously on Alpaca's side — the FA never saw the
real fill price, real fill quantity, or any rejection signal in the
UI. The submit flow was effectively fire-and-forget.

Sprint 4 wires Alpaca's `TradingStream` WebSocket into the app so
real fill events flow back into the Portfolio "Recent submissions"
expander as they happen.

## Architecture

```
                                 ┌─────────────────────────────────┐
   Streamlit page click          │ Alpaca paper-trading endpoint   │
   (Portfolio.py)                │ paper-api.alpaca.markets        │
       │                         └──────────────┬──────────────────┘
       ▼                                        │
   submit_basket_via('alpaca_paper', ...)       │ trade-update events
       │                                        │ (new/accepted/fill/...)
       ▼                                        │
   POST /v2/orders (REST) ──────► order ID ─────┘
       │                                        │
       ▼                                        ▼
   register_order_callback(order_id, cb)  ┌────────────────────────┐
                                          │ TradingStream daemon   │
                                          │ thread (one per proc)  │
                                          │  • async websocket     │
                                          │  • backoff reconnect   │
                                          └────────┬───────────────┘
                                                   │
                              ┌────────────────────┴───────────┐
                              ▼                                ▼
                  st.session_state.order_status      data/order_status_cache.json
                  (in-memory, per-session)           (atomic JSON, gitignored)
                              │                                │
                              └─────────────┬──────────────────┘
                                            ▼
                            "Recent submissions" expander
                            (Portfolio page)  +  status pill
                            (Settings page)
```

## Configuration

The module reads two env vars (or their legacy aliases):

| Spec name              | Legacy alias        | Required |
|------------------------|---------------------|----------|
| `ALPACA_API_KEY_ID`    | `ALPACA_API_KEY`    | yes      |
| `ALPACA_API_SECRET_KEY`| `ALPACA_API_SECRET` | yes      |

`is_configured()` returns `True` only when both are set. Without
them, all public functions are no-ops (logs INFO once at startup).

For local dev, place values in `.env`. For Streamlit Cloud:
**Manage app → Settings → Secrets**:

```toml
ALPACA_API_KEY_ID = "PK…"
ALPACA_API_SECRET_KEY = "…"
```

## Public API

```python
from integrations import alpaca_streaming as s

s.is_configured()           # bool — both env vars set?
s.is_streaming()            # bool — daemon thread alive?
s.start_order_stream()      # spawn daemon thread (idempotent)
s.stop_order_stream()       # graceful shutdown

s.register_order_callback(client_order_id, cb)
                            # cb(status_dict) fires on each WS event
s.get_last_status(coid)     # last-known status (from disk cache)
s.snapshot_recent(limit=10) # 10 newest orders, newest first
s.get_stream_health()       # dict — drives Settings status row
```

## Status row schema (cache + callbacks)

```python
{
    "status":          "fill",                # event type
    "last_update_iso": "2026-05-01T14:32:11.123+00:00",
    "fill_qty":        10,
    "fill_price":      65.42,
    "symbol":          "IBIT",
    "side":            "buy",
}
```

Status values mirror Alpaca's trade-update event names:
`new`, `accepted`, `pending_new`, `partial_fill`, `fill`, `filled`,
`canceled`, `expired`, `rejected`, `done_for_day`, plus a fallback
`unknown`.

## Reconnect strategy

`_stream_loop` runs `TradingStream.run()` until disconnect or
exception. On failure, it sleeps per the backoff table and reconnects:

```python
_BACKOFF_SECONDS = (1, 2, 4, 8, 16, 30)
```

After the 6th reconnect attempt, the cap holds at 30s (industry
standard backoff cap for WebSocket clients). The loop only exits
when `stop_order_stream()` sets `_STOP_EVENT`.

Reconnect attempts are reported in `get_stream_health()` and
displayed in the Settings panel — operator-diagnostic for connection
flakiness on Streamlit Cloud.

## Demo readiness checklist

- [x] Module configured iff both env vars set
- [x] register_order_callback fires on simulated event
- [x] Disk cache survives Streamlit cold-restart
- [x] Multi-order callback independence
- [x] Reconnect backoff capped at 30s
- [x] start_order_stream is idempotent
- [x] Graceful no-op when env unset
- [x] Settings panel shows Start / Stop + last error
- [x] Portfolio "Recent submissions" expander with status pills
- [x] BROKER_PROVIDER='mock' deploys never import this module

## Failure modes (CLAUDE.md §22 — no silent fallbacks)

| Failure                           | UX                                                              |
|-----------------------------------|------------------------------------------------------------------|
| `alpaca-py` not installed         | Settings card shows red error with `pip install` instructions   |
| Env vars unset                    | Settings card shows "Not configured" pill + which vars to set   |
| WebSocket disconnect              | Auto-reconnect with backoff; reconnect counter visible          |
| `TradingStream.run()` raises      | Last error string displayed via `st.error` in Settings card     |
| Disk cache corrupt                | Logged warning, treated as empty cache (no crash)               |
| Callback raises                   | Logged warning, stream continues (other callbacks still fire)   |

In no failure mode do we fabricate fills or silently downgrade — the
FA always sees what's actually happening.

## Post-demo follow-ups

Tracked in `pending_work.md`:

- **Full position streaming** — subscribe to `account_updates` to
  surface real-time portfolio NAV / buying-power changes alongside
  order status. Different WebSocket channel.
- **Crypto trading stream** — Alpaca's crypto endpoint is a separate
  WebSocket (`paper-crypto-api.alpaca.markets/stream`). When the
  ETF Advisor moves beyond spot-ETF wrappers into native crypto
  custody, this needs its own TradingStream wired in parallel.
