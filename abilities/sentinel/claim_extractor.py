#!/usr/bin/env python3
"""
🧠 claim_extractor.py
Extract lightweight (subject, predicate, object/value) claims from journals.

Sources:
- Prefer explicit `claims: [...]` blocks if present in journal JSON.
- Fallback: GAEL-tagged snippets or simple "X is Y" patterns (optional, safe no-ops if absent).
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any
from abilities.common_abilities.claim_types import (
    Claim, ClaimValueType, make_uuid, now_iso
)


# 🧪 Simple heuristic for "X is Y" lines (optional; harmless if no matches)
IS_PATTERN = re.compile(r"^\s*([A-Za-z0-9_\-\/\. ]+)\s+(is|=)\s+([A-Za-z0-9_\-\/\. ]+)\s*$")


# 🧺 helpers
def _coerce_value(val: Any) -> (Any, ClaimValueType):
    # try numeric
    try:
        if isinstance(val, str) and val.strip().isdigit():
            return int(val.strip()), ClaimValueType.NUMERIC
        if isinstance(val, (int, float)):
            return val, ClaimValueType.NUMERIC
    except Exception:
        pass
    # enum-ish short tokens
    if isinstance(val, str) and len(val.split()) == 1 and len(val) <= 32:
        return val, ClaimValueType.ENUM
    # fallback text
    return val, ClaimValueType.TEXT


# 📥 main
def extract_claims_from_journal_file(path: str, agent: str, journal_type: str) -> List[Claim]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)

    claims: List[Claim] = []

    # 1) Explicit claims list (preferred)
    explicit = data.get("claims")
    if isinstance(explicit, list):
        for row in explicit:
            sub = row.get("subject")
            pred = row.get("predicate")
            obj = row.get("object")
            val_raw = row.get("value")
            val, vtype = _coerce_value(val_raw)
            if sub and pred:
                claims.append(Claim(
                    claim_id=row.get("claim_id") or make_uuid(),
                    agent=agent,
                    file_path=str(p),
                    journal_type=journal_type,
                    timestamp=data.get("date") or now_iso(),
                    subject=sub.strip(),
                    predicate=pred.strip(),
                    object=(obj.strip() if isinstance(obj, str) else obj),
                    value=val,
                    value_type=vtype,
                    related_files=[str(p)],
                    extra={"source": "explicit_claims_list"}
                ))

    # 2) Optional GAEL-tagged or heuristic extraction (safe to skip if not desired)
    # Look inside "summary", "thoughts", "decisions"
    for field in ("summary", "thoughts", "decisions"):
        text = data.get(field)
        if not isinstance(text, str):
            continue
        for line in text.splitlines():
            m = IS_PATTERN.match(line.strip())
            if not m:
                continue
            subject, _, rhs = m.groups()
            val, vtype = _coerce_value(rhs)
            claims.append(Claim(
                claim_id=make_uuid(),
                agent=agent,
                file_path=str(p),
                journal_type=journal_type,
                timestamp=data.get("date") or now_iso(),
                subject=subject.strip(),
                predicate="is",
                object=None,
                value=val,
                value_type=vtype,
                related_files=[str(p)],
                extra={"source": "heuristic_is_line", "field": field}
            ))

    return claims

