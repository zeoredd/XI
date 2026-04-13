#!/usr/bin/env python3
"""
⚔️ conflict_detector.py
Compare new claims against prior canonical/current claims, detect flips/drifts,
and tag resolution status if an overlay exists.
"""

from typing import List, Dict, Tuple, Optional
from abilities.common_abilities.claim_types import (
    Claim, Conflict, ConflictStatus, Severity
)


# 🧭 compare numerics with tolerance for "drift"
def _numeric_changed(a, b, tol: float = 0.0) -> bool:
    try:
        return float(a) != float(b) if tol == 0 else abs(float(a) - float(b)) > tol
    except Exception:
        return str(a) != str(b)


def _same_value(a: Claim, b: Claim) -> bool:
    return str(a.value) == str(b.value) and a.value_type == b.value_type


# 🧷 Key for grouping
def claim_key(c: Claim) -> Tuple[str, str, Optional[str]]:
    return (c.subject, c.predicate, c.object)


# 🧱 Overlays map shape (example):
# overlays_by_claim_key = {
#   (subject, predicate, object) : { "status": "superseded", "superseded_by": "...", ... }
# }
#
# prior_by_key: latest or canonical prior claim list per key
#
def detect_conflicts(
    new_claims: List[Claim],
    prior_by_key: Dict[Tuple[str, str, Optional[str]], List[Claim]],
    overlays_by_claim_key: Dict[Tuple[str, str, Optional[str]], Dict]
) -> List[Conflict]:

    conflicts: List[Conflict] = []

    for c in new_claims:
        key = claim_key(c)
        priors = prior_by_key.get(key, [])

        # No prior → no conflict
        if not priors:
            conflicts.append(Conflict(
                code="NO_PRIOR",
                status=ConflictStatus.NO_CONFLICT,
                severity=Severity.INFO,
                target=key,
                new_claim=c,
                prior_claims=[],
                details={"message": "No prior claim found; treating as baseline."},
                related_files=c.related_files
            ))
            continue

        # Compare against the most recent prior
        prior = priors[0]  # assume sorted by recency beforehand

        # Enum/Text flip (value changed)
        if c.value_type in ("enum", "text"):
            if not _same_value(c, prior):
                status = ConflictStatus.CONFLICT_PENDING
                if key in overlays_by_claim_key:
                    status = ConflictStatus.RESOLVED_BY_OVERLAY
                conflicts.append(Conflict(
                    code="CLAIM_FLIP",
                    status=status,
                    severity=Severity.WARN if status == ConflictStatus.RESOLVED_BY_OVERLAY else Severity.ERROR,
                    target=key,
                    new_claim=c,
                    prior_claims=priors,
                    details={"old": str(prior.value), "new": str(c.value)},
                    related_files=list({*c.related_files, *prior.related_files})
                ))
            else:
                conflicts.append(Conflict(
                    code="NO_CHANGE",
                    status=ConflictStatus.NO_CONFLICT,
                    severity=Severity.INFO,
                    target=key,
                    new_claim=c,
                    prior_claims=priors,
                    details={"value": str(c.value)},
                    related_files=c.related_files
                ))
            continue

        # Numeric drift
        if c.value_type == "numeric":
            if _numeric_changed(c.value, prior.value, tol=0.0):
                status = ConflictStatus.CONFLICT_PENDING
                if key in overlays_by_claim_key:
                    status = ConflictStatus.RESOLVED_BY_OVERLAY
                conflicts.append(Conflict(
                    code="CLAIM_DRIFT",
                    status=status,
                    severity=Severity.WARN if status == ConflictStatus.RESOLVED_BY_OVERLAY else Severity.ERROR,
                    target=key,
                    new_claim=c,
                    prior_claims=priors,
                    details={"old": prior.value, "new": c.value},
                    related_files=list({*c.related_files, *prior.related_files})
                ))
            else:
                conflicts.append(Conflict(
                    code="NO_CHANGE",
                    status=ConflictStatus.NO_CONFLICT,
                    severity=Severity.INFO,
                    target=key,
                    new_claim=c,
                    prior_claims=priors,
                    details={"value": c.value},
                    related_files=c.related_files
                ))
            continue

        # Fallback (unknown type)
        conflicts.append(Conflict(
            code="UNKNOWN_VALUE_TYPE",
            status=ConflictStatus.CONFLICT_PENDING,
            severity=Severity.WARN,
            target=key,
            new_claim=c,
            prior_claims=priors,
            details={"value_type": str(c.value_type), "value": str(c.value)},
            related_files=c.related_files
        ))

    return conflicts

