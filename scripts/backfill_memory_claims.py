#!/usr/bin/env python3
import os, json, sys
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
import psycopg

DB_CONFIG = {
    "dbname": "xi_memory",
    "user": "postgres",
    "password": os.getenv("POSTGRES_PASSWORD", "yourpassword"),
    "host": "localhost",
    "port": "5432"
}

def iso_from_mtime(p: Path) -> str:
    dt = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)
    return dt.isoformat()

def coerce_value(val):
    # simple coercion; store textual repr and tag type
    if isinstance(val, (int, float)):
        return str(val), "numeric"
    if isinstance(val, str):
        if val.strip().isdigit():
            return str(int(val.strip())), "numeric"
        if len(val.split()) == 1 and len(val) <= 32:
            return val, "enum"
        return val, "text"
    return json.dumps(val, ensure_ascii=False), "text"

def derive_agent_and_type(path_parts):
    # memory/<agent>/<type>_journal/session/...
    agent = path_parts[1] if len(path_parts) > 1 else "unknown"
    journal_type = path_parts[3] if len(path_parts) > 3 else "session"
    return agent, journal_type

def scan_claims():
    globs = [
        "memory/*/*_journal/session/*.json",
        "memory/*/*_journal/weekly/*.json",
        "memory/*/*_journal/period/*.json",
        "memory/*/*_journal/quarterly/*.json",
    ]
    for pat in globs:
        for f in Path(".").glob(pat):
            data = None
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            claims = data.get("claims")
            if not isinstance(claims, list) or not claims:
                continue
            agent, journal_type = derive_agent_and_type(f.parts)
            ts = data.get("date")
            # normalize: if only YYYY-MM-DD or missing, use file mtime
            if not ts or len(str(ts)) == 10:
                ts = iso_from_mtime(f)
            # ensure Z
            if isinstance(ts, str) and ts.endswith("Z") is False and "+" not in ts:
                ts += "Z"
            for row in claims:
                sub = row.get("subject")
                pred = row.get("predicate")
                obj = row.get("object", None)
                if isinstance(obj, str) and obj.strip() == "":
                    obj = None
                if not (sub and pred):
                    continue
                val_raw = row.get("value")
                val, vtype = coerce_value(val_raw)
                yield {
                    "subject": sub,
                    "predicate": pred,
                    "object": obj,
                    "value": val,
                    "value_type": vtype,
                    "claim_id": row.get("claim_id") or str(uuid4()),
                    "agent": agent,
                    "journal_type": journal_type,
                    "timestamp": ts,
                    "file_path": str(f),
                    "status": "current"
                }

def main():
    rows = list(scan_claims())
    if not rows:
        print("No claims found in journals.")
        return
    with psycopg.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN")
            for r in rows:
                cur.execute("""
                    INSERT INTO memory_claims
                      (subject, predicate, object, value, value_type, claim_id,
                       agent, journal_type, "timestamp", file_path, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,'current'))
                    ON CONFLICT (claim_id) DO UPDATE
                      SET value = EXCLUDED.value,
                          value_type = EXCLUDED.value_type,
                          agent = EXCLUDED.agent,
                          journal_type = EXCLUDED.journal_type,
                          "timestamp" = EXCLUDED."timestamp",
                          file_path = EXCLUDED.file_path,
                          status = EXCLUDED.status;
                """, (
                    r["subject"], r["predicate"], r["object"],
                    r["value"], r["value_type"], r["claim_id"],
                    r["agent"], r["journal_type"], r["timestamp"], r["file_path"], r["status"]
                ))
            cur.execute("COMMIT")
    print(f"Backfilled {len(rows)} claims into memory_claims.")

if __name__ == "__main__":
    main()

