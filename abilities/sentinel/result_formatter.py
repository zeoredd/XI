#!/usr/bin/env python3
"""
🧾 result_formatter.py
Standardize outputs: validator_flags + integrity_report.
"""

from typing import List, Dict, Any
from collections import Counter
from abilities.common_abilities.claim_types import Conflict, ConflictStatus, Severity


# 🏳️ Build validator_flags objects
def to_validator_flags(conflicts: List[Conflict]) -> List[Dict[str, Any]]:
    flags = []
    for c in conflicts:
        flags.append({
            "code": c.code,
            "severity": c.severity,
            "status": c.status,
            "target": {
                "subject": c.target[0],
                "predicate": c.target[1],
                "object": c.target[2]
            },
            "details": c.details,
            "related_files": c.related_files,
            "new_claim_file": c.new_claim.file_path,
            "new_claim_id": c.new_claim.claim_id,
        })
    return flags


# 📊 Summarize integrity report
def build_integrity_report(conflicts: List[Conflict]) -> Dict[str, Any]:
    counts = Counter([c.status for c in conflicts])
    errors = sum(1 for c in conflicts if c.severity == Severity.ERROR)
    warns = sum(1 for c in conflicts if c.severity == Severity.WARN)
    infos = sum(1 for c in conflicts if c.severity == Severity.INFO)

    pass_fail = "pass" if errors == 0 else "fail"

    return {
        "totals": {
            "conflicts": len(conflicts),
            "status_counts": {k: int(v) for k, v in counts.items()},
            "severity_counts": {"ERROR": errors, "WARN": warns, "INFO": infos},
        },
        "result": pass_fail
    }

