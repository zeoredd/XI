#!/usr/bin/env python3
import os, sys, json, uuid, argparse
from datetime import datetime, timezone, timedelta
import psycopg2
import psycopg2.extras
from pathlib import Path

# --------- Config flags ----------
def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-name", default=os.environ.get("XI_DB_NAME", "xi_memory"))
    ap.add_argument("--db-user", default=os.environ.get("XI_DB_USER", "postgres"))
    ap.add_argument("--db-host", default=os.environ.get("XI_DB_HOST", "localhost"))
    ap.add_argument("--db-port", type=int, default=int(os.environ.get("XI_DB_PORT", 5432)))
    ap.add_argument("--window-hours", type=int, default=24, help="Recent window for conflicts; 0 = full scan")
    ap.add_argument("--apply", action="store_true", help="Apply safe auto-fixes (supersede losers)")
    ap.add_argument("--dry-run", action="store_true", help="No DB mutations")
    return ap.parse_args()

# --------- DB helpers ----------
def connect(args):
    return psycopg2.connect(
        dbname=args.db_name, user=args.db_user,
        host=args.db_host, port=args.db_port,
        password=os.environ.get("PGPASSWORD")
    )

def fetch_conflict_keys(conn, hours):
    where_window = 'AND "timestamp" > now() - interval %s' if hours > 0 else ""
    sql = f"""
    WITH current_claims AS (
      SELECT subject, predicate, value
      FROM memory_claims
      WHERE COALESCE(status, 'current')='current' {where_window}
    )
    SELECT subject, predicate
    FROM current_claims
    GROUP BY subject, predicate
    HAVING COUNT(DISTINCT value) > 1;
    """
    params = [f"'{hours} hours'"] if hours > 0 else []
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()  # list of (subject, predicate)

def fetch_group(conn, subject, predicate):
    sql = """
    SELECT subject, predicate, value, claim_id, "timestamp", journal_type, agent, file_path
    FROM memory_claims
    WHERE COALESCE(status, 'current')='current'
      AND subject=%s AND predicate=%s
    ORDER BY
      CASE journal_type
        WHEN 'quarterly' THEN 4
        WHEN 'period'   THEN 3
        WHEN 'weekly'   THEN 2
        WHEN 'session'  THEN 1
        ELSE 0 END DESC,
      "timestamp" DESC;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, (subject, predicate))
        return cur.fetchall()

def choose_winner(rows):
    # rows are already sorted by journal_type priority then newest timestamp
    return rows[0], rows[1:]  # (winner_row, loser_rows)

def write_audit_journal(report):
    base = Path("memory/sentinel/audit_journal/session")
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"sentinel_nightly_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"📝 Sentinel nightly audit written: {path}")
    return path

def apply_supersedes(conn, winner, losers):
    # Minimal, schema-safe: set losers.status='superseded' and add linkage in 'object'
    # (since you have no amendment table today).
    with conn:
        with conn.cursor() as cur:
            for L in losers:
                note = f"superseded_by:{winner['claim_id']}"
                cur.execute(
                    'UPDATE memory_claims SET status=%s, object=%s WHERE claim_id=%s',
                    ('superseded', note, L['claim_id'])
                )

def main():
    args = parse_args()
    conn = connect(args)

    keys = fetch_conflict_keys(conn, args.window_hours)
    conflicts = []
    fixed = []

    for subj, pred in keys:
        rows = fetch_group(conn, subj, pred)
        if len({r["value"] for r in rows}) <= 1:
            continue  # race / no longer conflicting

        winner, losers = choose_winner(rows)

        conflicts.append({
            "subject": subj,
            "predicate": pred,
            "winner": {
                "claim_id": winner["claim_id"],
                "value": winner["value"],
                "journal_type": winner["journal_type"],
                "timestamp": winner["timestamp"],
                "agent": winner["agent"],
            },
            "losers": [
                {
                    "claim_id": L["claim_id"],
                    "value": L["value"],
                    "journal_type": L["journal_type"],
                    "timestamp": L["timestamp"],
                    "agent": L["agent"],
                } for L in losers
            ]
        })

        if args.apply and not args.dry_run:
            apply_supersedes(conn, winner, losers)
            fixed.append((subj, pred, winner["claim_id"]))

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": args.window_hours,
        "conflict_groups": len(conflicts),
        "applied_fixes": len(fixed) if args.apply and not args.dry_run else 0,
        "details": conflicts,
    }
    write_audit_journal(report)

if __name__ == "__main__":
    sys.exit(main())

