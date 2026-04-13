#!/usr/bin/env python3
from typing import List, Dict

def insert_claims(conn, claims: List[Dict]):
    """
    claims: [{subject, predicate, value, origin_file, agent, decision="insert"}]
    """
    sql = """
    INSERT INTO memory_claims
      (subject, predicate, value, status, "timestamp", file_path, agent, claim_id, journal_type)
    VALUES
      (%s, %s, %s, 'current', now(), %s, %s, gen_random_uuid()::text, 'session')
    """
    # Use the provided connection (it's already managed by the caller).
    # Don't re-enter it as a context manager — psycopg2 forbids nested connection contexts.
    with conn.cursor() as cur:
        for c in claims:
            cur.execute(sql, (
                c["subject"], c["predicate"], c["value"],
                c.get("origin_file"), c.get("agent")
            ))
    # Explicit commit since we didn't use `with conn:`
    try:
        conn.commit()
    except Exception:
        # Let the caller decide — but try not to mask errors
        raise
