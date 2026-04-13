#!/usr/bin/env python3
"""
🛑 inline_claim_guard.py — call from flows before committing claims
Usage:
  report = guard_claims_before_commit(conn, pending_claims)

pending_claims: List[Dict] each with:
  { "subject": str, "predicate": str, "value": str,
    "origin_file": str, "agent": str }
"""
from typing import List, Dict

def _canon(v: str) -> str:
    s = (v or "").strip()
    if s.replace('.', '', 1).isdigit():
        try:
            return str(float(s))
        except Exception:
            pass
    return s.casefold()

def _lookup_active(conn, subject: str, predicate: str) -> List[Dict]:
    sql = """
    SELECT claim_id, value, "timestamp"
    FROM memory_claims
    WHERE COALESCE(status, 'current') = 'current'
      AND subject=%s AND predicate=%s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (subject, predicate))
        rows = cur.fetchall()
    return [{
        'claim_id': r[0],
        'value': r[1],
        'time_start': r[2],  # keep key name as time_start for downstream compatibility
        'weight': 1.0        # your schema has no weight column; default to 1.0
    } for r in rows]

def guard_claims_before_commit(conn, pending_claims: List[Dict]) -> Dict:
    """
    Returns:
    {
      "ok": [pending_claim with decision="insert"],
      "conflicts": [
         {
           "subject":..., "predicate":...,
           "new_value":..., "existing":[{claim_id,value,time_start,weight},...]
         }
      ]
    }
    """
    report = {"ok": [], "conflicts": []}
    for c in pending_claims:
        existing = _lookup_active(conn, c["subject"], c["predicate"])
        if not existing:
            report["ok"].append({**c, "decision": "insert"})
            continue

        values = { _canon(x["value"]) for x in existing }
        values.add(_canon(c["value"]))

        if len(values) == 1:
            report["ok"].append({**c, "decision": "insert"})
        else:
            report["conflicts"].append({
                "subject": c["subject"],
                "predicate": c["predicate"],
                "new_value": c["value"],
                "existing": existing,
            })
    return report

