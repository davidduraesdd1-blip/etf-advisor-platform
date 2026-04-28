"""
core/client_adapters/csv_import_adapter.py — read clients from a
local CSV.

Sprint 3 commit 3 (2026-04-30). Real, working adapter. The CSV path
is `data/clients_import.csv` (gitignored — never check in real client
data). When the file is absent or malformed, the adapter returns
[] and `is_configured()` returns False so the factory falls through
to the demo adapter.

CSV schema (header row required, columns can be in any order):

    id                     str        required
    name                   str        required
    age                    int        optional
    label                  str        optional
    assigned_tier          str        optional (default "(unassigned)")
    total_portfolio_usd    float      optional (default 0)
    crypto_allocation_pct  float      optional (default 0)
    last_rebalance_iso     str        optional
    drift_pct              float      optional (default 0)
    rebalance_needed       bool       optional ("true"/"yes"/"1")
    notes                  str        optional
    situation_today        str        optional

Path override: set `CLIENT_CSV_PATH` env var to a custom location
(e.g., a network share at `/mnt/clientshare/portfolios.csv`). When
unset, defaults to `<repo>/data/clients_import.csv`.
"""
from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from typing import Optional

from core.client_adapter import ClientAdapter, ClientRecord, register_adapter

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV_PATH = REPO_ROOT / "data" / "clients_import.csv"


def _csv_path() -> Path:
    override = os.environ.get("CLIENT_CSV_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_CSV_PATH


def _to_bool(s: str) -> bool:
    return str(s).strip().lower() in ("true", "yes", "1", "y", "t")


def _to_float(s: str) -> float:
    s = (s or "").strip().replace(",", "").replace("$", "").replace("%", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _to_int_or_none(s: str) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(float(s))   # tolerate "55.0" → 55
    except (ValueError, TypeError):
        return None


def _row_to_record(row: dict) -> Optional[ClientRecord]:
    cid = str(row.get("id", "") or "").strip()
    name = str(row.get("name", "") or "").strip()
    if not cid or not name:
        return None
    return ClientRecord(
        id=cid,
        name=name,
        label=str(row.get("label", "") or ""),
        age=_to_int_or_none(row.get("age", "")),
        assigned_tier=str(row.get("assigned_tier", "") or "(unassigned)"),
        total_portfolio_usd=_to_float(row.get("total_portfolio_usd", "")),
        crypto_allocation_pct=_to_float(row.get("crypto_allocation_pct", "")),
        last_rebalance_iso=(str(row.get("last_rebalance_iso", "") or "") or None),
        drift_pct=_to_float(row.get("drift_pct", "")),
        rebalance_needed=_to_bool(row.get("rebalance_needed", "")),
        notes=str(row.get("notes", "") or ""),
        situation_today=str(row.get("situation_today", "") or ""),
    )


class CSVImportClientAdapter(ClientAdapter):
    """Read advisor clients from a local CSV file."""

    def provider_name(self) -> str:
        return "csv_import"

    def is_configured(self) -> bool:
        return _csv_path().is_file()

    def list_clients(self) -> list[ClientRecord]:
        path = _csv_path()
        if not path.is_file():
            return []
        try:
            with path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                out: list[ClientRecord] = []
                for row in reader:
                    rec = _row_to_record(row)
                    if rec is not None:
                        out.append(rec)
                return out
        except (OSError, csv.Error, UnicodeDecodeError) as exc:
            logger.info("CSV client import failed (%s): %s", path, exc)
            return []


register_adapter("csv_import", CSVImportClientAdapter)
