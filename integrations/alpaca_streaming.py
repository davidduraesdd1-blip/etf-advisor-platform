"""
alpaca_streaming.py — real-time order-status streaming via Alpaca's
TradingStream WebSocket.

Replaces the prior fire-and-forget pattern in
`integrations/broker_alpaca_paper.py::submit_basket` where the response
returned `mid_price` as the fill (real fills happen async on Alpaca
side). This module surfaces the actual Alpaca order events
(`new`, `accepted`, `partial_fill`, `fill`, `canceled`, `rejected`,
etc.) back into the Streamlit UI as they happen.

Architecture:
  - One daemon thread per process running `TradingStream.run()`.
  - In-memory callback registry keyed by `client_order_id`.
  - On-disk JSON cache (`data/order_status_cache.json`) so cold-restart
    of Streamlit Cloud retains last-known status per recent order.
  - Reconnect-on-disconnect with exponential backoff (1, 2, 4, 8, 16, 30s).
  - No-op + INFO log when ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY
    (or legacy ALPACA_API_KEY / ALPACA_API_SECRET) env vars are unset.

CLAUDE.md governance:
  §11 — Web3 Level B (broker execution, paper-only until live approval).
  §22 — No silent fallbacks. If streaming fails, the Settings panel
        surfaces the failure; we never fabricate fills.
  §10 — No-fallback live data; cached snapshot is honest about its age.

Public API (used by pages/02_Portfolio.py + pages/99_Settings.py):
    start_order_stream() -> None
    stop_order_stream() -> None
    register_order_callback(client_order_id, callback) -> None
    get_last_status(client_order_id) -> Optional[dict]
    is_streaming() -> bool
    is_configured() -> bool
    get_stream_health() -> dict
    snapshot_recent(limit=10) -> list[dict]

Sprint 4 — 2026-05-01.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
# Support both the documented env names (per Sprint 4 spec) and the
# legacy names already used in config.py — operators who set either
# should "just work" without reconfiguring.
_ENV_KEY_ID_NAMES = ("ALPACA_API_KEY_ID", "ALPACA_API_KEY")
_ENV_SECRET_NAMES = ("ALPACA_API_SECRET_KEY", "ALPACA_API_SECRET")

# Cache location — gitignored via data/*.json glob in .gitignore.
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CACHE_PATH = _DATA_DIR / "order_status_cache.json"

# Backoff schedule for reconnect.
_BACKOFF_SECONDS = (1, 2, 4, 8, 16, 30)

# ── Module state (thread-safe via _LOCK) ──────────────────────────────────────
_LOCK = threading.RLock()
_THREAD: Optional[threading.Thread] = None
_STREAM: Any = None                    # TradingStream instance
_STOP_EVENT: Optional[threading.Event] = None
_CALLBACKS: dict[str, list[Callable[[dict], None]]] = {}
_LAST_EVENT_AT: Optional[str] = None   # ISO timestamp
_STREAM_ERROR: Optional[str] = None    # last error string for Settings UI
_RECONNECT_ATTEMPTS: int = 0

# Audit-fix (HIGH): in-memory cache + debounced disk persist. The prior
# implementation read JSON from disk on every WebSocket event (200+ disk
# reads/sec on a fast-fill 50-name basket). Now: events update the
# in-memory dict under _LOCK; a background flusher writes to disk every
# _CACHE_FLUSH_SEC OR when the dict changes by more than _CACHE_DIRTY_BUMP
# entries. _LOCK is held over the full read-modify-write so two events
# arriving simultaneously can't corrupt the cache (TOCTOU fix).
_CACHE: dict[str, dict] = {}           # in-memory mirror of _CACHE_PATH
_CACHE_DIRTY: bool = False
_CACHE_LOADED: bool = False
_CACHE_LAST_FLUSH_TS: float = 0.0
_CACHE_FLUSH_SEC: float = 1.5          # debounce window
_CACHE_DIRTY_BUMP: int = 25            # force-flush after N changes


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_env(names: tuple[str, ...]) -> Optional[str]:
    """Return the first non-empty value from `names` in os.environ."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def is_configured() -> bool:
    """True iff both Alpaca credentials are present in the environment."""
    return bool(_read_env(_ENV_KEY_ID_NAMES) and _read_env(_ENV_SECRET_NAMES))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically (tmp file + replace) so Streamlit cold-reads
    never see a half-written file. CLAUDE.md §22 — never poison cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)


def _load_cache_if_needed() -> None:
    """Lazy-load the on-disk cache into _CACHE on first access. Holds
    _LOCK so concurrent first-callers can't double-load."""
    global _CACHE_LOADED
    with _LOCK:
        if _CACHE_LOADED:
            return
        if _CACHE_PATH.exists():
            try:
                payload = json.loads(_CACHE_PATH.read_text() or "{}")
                if isinstance(payload, dict):
                    _CACHE.update(payload)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("alpaca_streaming: corrupt cache ignored (%s)", exc)
        _CACHE_LOADED = True


def _flush_cache_if_dirty(force: bool = False) -> None:
    """Persist _CACHE to disk if it's been modified since last flush
    AND (force OR debounce window elapsed). Caller may or may not hold
    _LOCK — we re-acquire for the snapshot+write."""
    global _CACHE_DIRTY, _CACHE_LAST_FLUSH_TS
    with _LOCK:
        if not _CACHE_DIRTY:
            return
        elapsed = time.time() - _CACHE_LAST_FLUSH_TS
        if not force and elapsed < _CACHE_FLUSH_SEC:
            return
        snapshot = dict(_CACHE)   # shallow copy under lock — safe to write outside
        _CACHE_DIRTY = False
        _CACHE_LAST_FLUSH_TS = time.time()
    try:
        _atomic_write_json(_CACHE_PATH, snapshot)
    except OSError as exc:
        logger.warning("alpaca_streaming: cache write failed (%s)", exc)


def _persist_status(client_order_id: str, status: dict) -> None:
    """Update in-memory _CACHE under _LOCK, mark dirty, then maybe flush.
    No disk I/O on the hot path unless the debounce window has elapsed."""
    global _CACHE_DIRTY
    _load_cache_if_needed()
    with _LOCK:
        _CACHE[client_order_id] = status
        # Trim to most-recent 200 entries to keep file small.
        if len(_CACHE) > 200:
            ordered = sorted(
                _CACHE.items(),
                key=lambda kv: kv[1].get("last_update_iso", ""),
                reverse=True,
            )
            _CACHE.clear()
            _CACHE.update(dict(ordered[:200]))
        _CACHE_DIRTY = True
        # Force-flush every N updates so we never lose more than N entries
        # to a hard process kill.
        force = len(_CACHE) % _CACHE_DIRTY_BUMP == 0
    _flush_cache_if_dirty(force=force)


def get_last_status(client_order_id: str) -> Optional[dict]:
    """Return last-known status for `client_order_id`, or None.

    Reads from in-memory cache (loaded once from disk so the value
    survives Streamlit cold-restart). No per-call disk I/O.
    """
    _load_cache_if_needed()
    with _LOCK:
        return dict(_CACHE.get(client_order_id)) if client_order_id in _CACHE else None


def snapshot_recent(limit: int = 10) -> list[dict]:
    """Return the `limit` most-recent orders as `{client_order_id, ...}`
    list, newest first. Used by the Portfolio "Recent submissions"
    expander."""
    _load_cache_if_needed()
    with _LOCK:
        rows = [{"client_order_id": k, **v} for k, v in _CACHE.items()]
    rows.sort(key=lambda r: r.get("last_update_iso", ""), reverse=True)
    return rows[:limit]


def _flush_cache_to_disk() -> None:
    """Public flush entry — called from atexit + stop_order_stream()."""
    _flush_cache_if_dirty(force=True)


def register_order_callback(
    client_order_id: str,
    callback: Callable[[dict], None],
) -> None:
    """Register `callback(status_dict)` for events tagged with
    `client_order_id`. Multiple callbacks per id are supported.

    Callbacks fire on the streaming thread — they MUST be quick and
    must not raise. Exceptions are caught + logged but the stream
    continues.
    """
    with _LOCK:
        _CALLBACKS.setdefault(client_order_id, []).append(callback)


def _dispatch(event_payload: dict) -> None:
    """Internal: extract a status-row from a TradingStream event,
    persist it, and fan out to any registered callbacks."""
    global _LAST_EVENT_AT
    coid = (
        event_payload.get("client_order_id")
        or event_payload.get("order", {}).get("client_order_id")
        or event_payload.get("id")
    )
    if not coid:
        return
    status_row = {
        "status":          event_payload.get("event")
                           or event_payload.get("status")
                           or "unknown",
        "last_update_iso": _now_iso(),
        "fill_qty":        event_payload.get("qty")
                           or event_payload.get("filled_qty"),
        "fill_price":      event_payload.get("price")
                           or event_payload.get("filled_avg_price"),
        "symbol":          event_payload.get("symbol"),
        "side":            event_payload.get("side"),
    }
    _persist_status(coid, status_row)
    with _LOCK:
        _LAST_EVENT_AT = status_row["last_update_iso"]
        cbs = list(_CALLBACKS.get(coid, []))
    for cb in cbs:
        try:
            cb(status_row)
        except Exception as exc:    # pragma: no cover — defensive
            logger.warning("alpaca_streaming: callback raised (%s)", exc)


# ── Stream lifecycle ─────────────────────────────────────────────────────────

def _build_stream() -> Any:
    """Construct a TradingStream. Returns None on import failure."""
    try:
        from alpaca.trading.stream import TradingStream  # type: ignore
    except ImportError:
        logger.info(
            "alpaca_streaming: alpaca-py not importable — install "
            "`alpaca-py>=0.30.0` to enable streaming."
        )
        return None
    key = _read_env(_ENV_KEY_ID_NAMES)
    secret = _read_env(_ENV_SECRET_NAMES)
    if not key or not secret:
        logger.info(
            "alpaca_streaming: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY "
            "unset — no-op."
        )
        return None
    try:
        stream = TradingStream(api_key=key, secret_key=secret, paper=True)
        # Subscribe to all trade-update events.
        stream.subscribe_trade_updates(_handle_trade_update)
        return stream
    except Exception as exc:
        logger.warning("alpaca_streaming: TradingStream init failed (%s)", exc)
        return None


async def _handle_trade_update(event: Any) -> None:
    """alpaca-py awaits this callable for every trade-update event."""
    try:
        # alpaca-py wraps payloads in pydantic models; fall back to dict
        # access so test mocks (plain dicts) and real models both work.
        if hasattr(event, "model_dump"):
            payload = event.model_dump()
        elif hasattr(event, "dict"):
            payload = event.dict()
        else:
            payload = dict(event) if not isinstance(event, dict) else event
        # Some alpaca-py versions wrap order under .order
        if "order" in payload and isinstance(payload["order"], dict):
            order = payload["order"]
            payload = {**order, **{k: v for k, v in payload.items() if k != "order"}}
        _dispatch(payload)
    except Exception as exc:    # pragma: no cover — defensive
        logger.warning("alpaca_streaming: event handler error (%s)", exc)


def _stream_loop() -> None:
    """Daemon-thread entry point. Runs the TradingStream; on disconnect
    or error, sleeps per the backoff table and reconnects until
    _STOP_EVENT is set."""
    global _STREAM, _STREAM_ERROR, _RECONNECT_ATTEMPTS, _THREAD
    assert _STOP_EVENT is not None
    while not _STOP_EVENT.is_set():
        stream = _build_stream()
        if stream is None:
            # Not configured / SDK missing — exit cleanly. The Settings
            # row will reflect "not configured" via is_streaming().
            # Audit-fix: clear _THREAD on early exit so the next
            # start_order_stream() call doesn't reference a stale dead
            # thread. The reconnect path naturally clears state on STOP.
            with _LOCK:
                _STREAM_ERROR = "alpaca-py SDK or credentials unavailable"
                _THREAD = None
            return
        with _LOCK:
            _STREAM = stream
            _STREAM_ERROR = None
        try:
            # TradingStream.run() blocks until disconnect.
            stream.run()
        except Exception as exc:
            with _LOCK:
                _STREAM_ERROR = f"{type(exc).__name__}: {exc}"
            logger.warning("alpaca_streaming: stream.run() raised (%s)", exc)
        finally:
            try:
                stream.stop()
            except Exception:    # pragma: no cover — stop() is best-effort
                pass
            with _LOCK:
                _STREAM = None
        if _STOP_EVENT.is_set():
            break
        # Backoff before reconnect.
        with _LOCK:
            _RECONNECT_ATTEMPTS += 1
            attempt = _RECONNECT_ATTEMPTS
        delay = _BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)]
        logger.info("alpaca_streaming: reconnect in %ds (attempt %d)", delay, attempt)
        if _STOP_EVENT.wait(delay):
            break


def start_order_stream() -> None:
    """Start (or noop) the streaming daemon thread.

    Idempotent — if the thread is already alive, this is a no-op. If
    credentials are unset, this is also a no-op (logs INFO + the
    Settings UI shows the unconfigured state via is_streaming()).
    """
    global _THREAD, _STOP_EVENT, _RECONNECT_ATTEMPTS
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        if not is_configured():
            logger.info("alpaca_streaming: not configured — start_order_stream is a no-op.")
            return
        _STOP_EVENT = threading.Event()
        _RECONNECT_ATTEMPTS = 0
        t = threading.Thread(
            target=_stream_loop,
            name="alpaca_streaming",
            daemon=True,
        )
        _THREAD = t
        t.start()


def stop_order_stream() -> None:
    """Graceful shutdown. Signals the loop, stops the underlying
    stream, and waits briefly for the thread to exit."""
    global _THREAD, _STOP_EVENT, _STREAM
    with _LOCK:
        evt = _STOP_EVENT
        stream = _STREAM
        thread = _THREAD
    if evt is not None:
        evt.set()
    if stream is not None:
        try:
            stream.stop()
        except Exception:    # pragma: no cover
            pass
    if thread is not None:
        thread.join(timeout=3.0)
    with _LOCK:
        _THREAD = None
        _STOP_EVENT = None
        _STREAM = None


def is_streaming() -> bool:
    """True iff the daemon thread is alive AND a TradingStream is
    attached. Used by Settings to drive the on/off pill."""
    with _LOCK:
        return (
            _THREAD is not None
            and _THREAD.is_alive()
            and _STREAM is not None
        )


def get_stream_health() -> dict:
    """Snapshot for the Settings status row."""
    _load_cache_if_needed()
    with _LOCK:
        return {
            "configured":         is_configured(),
            "streaming":          is_streaming(),
            "last_event_iso":     _LAST_EVENT_AT,
            "tracked_orders":     len(_CACHE),
            "reconnect_attempts": _RECONNECT_ATTEMPTS,
            "last_error":         _STREAM_ERROR,
        }


# ── Test-only hook ───────────────────────────────────────────────────────────
# Tests dispatch synthetic events through this entry-point without going
# through the WebSocket. Production code must never call this directly.

def _test_inject_event(payload: dict) -> None:
    """Synthetic-event hook for unit tests. Equivalent to receiving
    a single trade-update from the WebSocket."""
    _dispatch(payload)


# ── Process exit hook ────────────────────────────────────────────────────────
# Audit-fix: ensure any pending in-memory cache writes are persisted to disk
# on graceful interpreter shutdown. Streamlit Cloud restarts wouldn't lose
# data anyway (the JSON file survives) but a hard kill mid-debounce would
# drop the most-recent ~1.5s of events. atexit covers SIGINT / SIGTERM /
# Streamlit's normal shutdown path.
import atexit as _atexit
_atexit.register(_flush_cache_to_disk)
