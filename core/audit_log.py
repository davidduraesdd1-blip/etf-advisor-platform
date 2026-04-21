"""
audit_log.py — simple append-only advisor-action log.

Every Execute / Rebalance / Override-setting action writes one entry to
data/audit_log.json. Settings page reads and renders as a table.

Ring-buffered at 200 entries with oldest-first trim — Day-4 Risk 6
mitigation so the file never grows unbounded over multi-session use.

Atomic write via tempfile + os.replace with the Windows+OneDrive retry
pattern (CLAUDE.md §20).

CLAUDE.md governance: Sections 11 (env-scoped), 12 (refresh rates), 22
(demo constraints — clients are fictional, actions are all demo-mode).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger(__name__)

AUDIT_LOG_PATH: Path = DATA_DIR / "audit_log.json"
MAX_ENTRIES: int = 200

_lock = threading.Lock()


def _load_entries() -> list[dict]:
    if not AUDIT_LOG_PATH.exists():
        return []
    try:
        with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        return data.get("entries", [])
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("audit_log.json unreadable: %s", exc)
        return []


def _save_entries(entries: list[dict]) -> None:
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": entries[-MAX_ENTRIES:]}
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8",
        dir=str(AUDIT_LOG_PATH.parent),
        prefix=".tmp_audit_", suffix=".json", delete=False,
    ) as tf:
        json.dump(payload, tf, indent=2)
        tmp_name = tf.name
    for attempt in range(5):
        try:
            os.replace(tmp_name, str(AUDIT_LOG_PATH))
            return
        except PermissionError:
            time.sleep(0.1 * (attempt + 1))
    try:
        os.remove(tmp_name)
    except OSError:
        pass


def append_entry(client_id: str, action: str, detail: str = "",
                 user: str = "demo_advisor") -> None:
    """Record one advisor action. Safe to call from anywhere."""
    with _lock:
        entries = _load_entries()
        entries.append({
            "ts":     time.time(),
            "iso":    datetime.now(timezone.utc).isoformat(),
            "user":   user,
            "client": client_id,
            "action": action,
            "detail": detail,
        })
        _save_entries(entries)


def recent_entries(limit: int = 50) -> list[dict]:
    """Return the `limit` most recent audit entries, newest first."""
    with _lock:
        entries = _load_entries()
    return list(reversed(entries[-limit:]))


def clear_log() -> None:
    """Test hook + Settings 'Clear audit log' button (post-demo)."""
    with _lock:
        _save_entries([])


def seed_demo_entries(demo_clients: list[dict]) -> None:
    """
    Seed the audit log with 3-5 illustrative entries per demo client.
    Called from pages/01_Dashboard.py on first render when the log is
    empty so the demo has realistic history on page load.
    """
    with _lock:
        if _load_entries():
            return
        entries: list[dict] = []
        base_ts = time.time() - 7 * 86400   # start a week ago
        for idx, c in enumerate(demo_clients):
            for k in range(4):
                entries.append({
                    "ts":     base_ts + idx * 3600 + k * 18000,
                    "iso":    datetime.fromtimestamp(
                        base_ts + idx * 3600 + k * 18000,
                        tz=timezone.utc,
                    ).isoformat(),
                    "user":   "demo_advisor",
                    "client": c["id"],
                    "action": ["view_portfolio", "rebalance_recommendation",
                               "execute_basket", "update_risk_profile"][k],
                    "detail": {
                        0: f"opened portfolio view · tier={c['assigned_tier']}",
                        1: f"system flagged rebalance · drift={c['drift_pct']}%",
                        2: f"basket executed (mock) · ${c['total_portfolio_usd'] * 0.03:,.0f} gross",
                        3: f"risk tolerance reviewed · no change",
                    }[k],
                })
        _save_entries(entries)
