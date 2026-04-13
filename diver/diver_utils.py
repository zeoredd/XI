# ===============================
# Recommended folder structure
# ===============================
#
# xi/
#   diver/
#     __init__.py
#     diver_utils.py
#     base_diver.py
#     diver_controller.py
#     cascade_diver.py   (later)
#   scripts/
#     bootstrap_memdb.py  (for smoke test)
#   mem.db (your sqlite database)

# ===============================
# file: diver_utils.py
# ===============================
"""
Shared utilities for Diver system.
- SQLite connection helpers
- Filters builder
- Simple FTS + vector search adapters
- Time parsing
"""
from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime
import math
import os
import psycopg2
import psycopg2.extras

@dataclass
class Filters:
    layer: Optional[str] = None  # 'session'|'weekly'|'period'|'quarter'|None
    agent: Optional[str] = None
    thread: Optional[str] = None
    after: Optional[int] = None   # epoch seconds
    before: Optional[int] = None  # epoch seconds

    def where_clause(self, alias: str = "m") -> Tuple[str, List[Any]]:
        parts: List[str] = []
        args: List[Any] = []
        if self.layer and self.layer != "any":
            parts.append(f"{alias}.layer = %s"); args.append(self.layer)
        if self.agent:
            parts.append(f"{alias}.agent = %s"); args.append(self.agent)
        if self.thread:
            parts.append(f"{alias}.thread = %s"); args.append(self.thread)
        if self.after:
            parts.append(f"{alias}.ts >= %s"); args.append(self.after)
        if self.before:
            parts.append(f"{alias}.ts <= %s"); args.append(self.before)
        where = ("WHERE "+" AND ".join(parts)) if parts else ""
        return where, args

def connect():
    # Align with memory_search: prefer XI_DB_* → POSTGRES_* → PG*
    def _env(*names, default=None):
        for n in names:
            v = os.getenv(n)
            if v not in (None, ""):
                return v
        return default

    conn = psycopg2.connect(
        dbname=_env("XI_DB_NAME","POSTGRES_DB","PGDATABASE","DB_NAME", default="xi_memory"),
        user=_env("XI_DB_USER","POSTGRES_USER","PGUSER","DB_USER", default="xi_user"),
        password=_env("XI_DB_PASSWORD","POSTGRES_PASSWORD","PGPASSWORD","DB_PASSWORD"),
        host=_env("XI_DB_HOST","POSTGRES_HOST","PGHOST","DB_HOST", default="localhost"),
        port=int(_env("XI_DB_PORT","POSTGRES_PORT","PGPORT","DB_PORT", default="5432")),
    )
    conn.autocommit = True
    return conn

# --- time helpers ---

def to_epoch(ts_like: str) -> int:
    """Parse naive ISO8601 like '2025-08-20T12:30:00' to epoch seconds."""
    return int(datetime.fromisoformat(ts_like).timestamp())

# --- scoring helpers (used by Base Diver) ---

HALF_LIFE_DAYS = 14.0

def recency_decay(now_ts: int, ts: Optional[int]) -> float:
    if not ts:
        return 0.0
    days = (now_ts - ts) / 86400.0
    return math.exp(-days / HALF_LIFE_DAYS)


