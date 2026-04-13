#!/usr/bin/env python3
"""
🧩 claims_resolver.py — shared helpers for Sentinel + inline guards
- Normalization/canonicalization
- Optional AI adjudicator stub (nightly only)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple

# Lightweight normalize (extend as needed)

def canon(v: str) -> str:
    s = (v or "").strip()
    if s.replace('.', '', 1).isdigit():
        try:
            return str(float(s))
        except Exception:
            pass
    return s.casefold()

@dataclass
class MiniClaim:
    claim_id: str
    subject: str
    predicate: str
    value: str
    time_start: object
    weight: float = 1.0

# Decide if protocol can auto-amend safely (subject to allowlist in caller)

def rule_can_decide(values: List[str]) -> bool:
    return len({canon(v) for v in values}) == 1

# Nightly AI adjudicator (stub): replace with actual model call later

def ai_adjudicate_cases(hard_cases, threshold: float = 0.8):
    """Return list of dicts per case: {action, winner, losers, confidence}
    For now this is a placeholder that conservatively declines decisions.
    """
    decisions = []
    for grp, winner, losers in hard_cases:
        decisions.append({
            'action': 'none',
            'confidence': 0.0,
        })
    return decisions
