#!/usr/bin/env python3
"""
🏷️ claim_types.py
Shared dataclasses & enums for Sentinel claims/conflicts/flags.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum
from uuid import uuid4
from datetime import datetime
from typing import Tuple, Optional


# 🧩 Claim value kinds (keep simple + explicit)
class ClaimValueType(str, Enum):
    ENUM = "enum"
    NUMERIC = "numeric"
    TEXT = "text"


# 📌 Conflict status for resolution state
class ConflictStatus(str, Enum):
    CONFLICT_PENDING = "CONFLICT_PENDING"
    RESOLVED_BY_OVERLAY = "RESOLVED_BY_OVERLAY"
    NO_CONFLICT = "NO_CONFLICT"


# 🚦 Severity levels for validator flags
class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


@dataclass
class Claim:
    # 🧠 identity
    claim_id: str
    agent: str
    file_path: str
    journal_type: str  # session | weekly | period | quarterly | audit
    timestamp: str     # ISO8601

    # 🔗 triple
    subject: str
    predicate: str
    object: Optional[str] = None  # optional if value-only

    # 📈 value
    value: Any = None
    value_type: ClaimValueType = ClaimValueType.TEXT

    # 📌 lifecycle / DB state
    status: Optional[str] = None   # "current", "open", "superseded"

    # 🧾 provenance
    related_files: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Conflict:
    code: str                     # e.g., CLAIM_FLIP, CLAIM_DRIFT, DUPLICATE
    status: ConflictStatus
    severity: Severity
    target: Tuple[str, str, Optional[str]]  # (subject, predicate, object)
    new_claim: Claim
    prior_claims: List[Claim]
    details: Dict[str, Any] = field(default_factory=dict)
    related_files: List[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def make_uuid() -> str:
    return str(uuid4())

# 🔑 Helper: consistent grouping key for claims
def claim_key(c: "Claim") -> Tuple[str, str, Optional[str]]:
    """
    Return a tuple key for identifying a claim across entries.
    Groups by (subject, predicate, object).
    """
    return (c.subject, c.predicate, c.object)


