#!/usr/bin/env python3
"""
🔎 reverse_vector_search.py
Given a claim triple (subject/predicate/value — any subset),
return all occurrences and edits across agents, time-sorted.

Primary source: Postgres table `memory_claims` (via memory_indexer.py).
Fallback: Scan /memory/**/_journal/session/*.json (summary/thoughts/decisions/claims).

Usage (programmatic):
    from abilities.common_abilities.reverse_vector_search import rvs
    occ = rvs(subject="gather:launch_date", agents=["davinci","hermes"])
    print(format_report(occ))

CLI:
    python3 abilities/common_abilities/reverse_vector_search.py \
      --subject gather:launch_date --agents davinci,hermes --limit 200
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime, timezone
import argparse, json, os, re
import sys
from abilities.common_abilities.time_utils import is_recent

# ➕ Token logging
from abilities.common_abilities.token_logger import log_retrieval_usage


# ── Color helpers ──────────────────────────────────────────────────────────────
ANSI = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "cyan": "\033[36m",
    "yellow": "\033[33m",
    "magenta": "\033[35m",
}

def _supports_color() -> bool:
    return sys.stdout.isatty()

def _colorize(s: str, *styles: str, enabled: bool = True) -> str:
    if not enabled:
        return s
    seq = "".join(ANSI.get(st, "") for st in styles if st in ANSI)
    return f"{seq}{s}{ANSI['reset'] if seq else ''}"

# ── Time formatting ────────────────────────────────────────────────────────────
import re as _re
_DATE_ONLY = _re.compile(r"^\d{4}-\d{2}-\d{2}$")

def _fmt_time(ts: str | None) -> str:
    """Return a clean time string; if only a date is available, keep it as YYYY-MM-DD."""
    if not ts:
        return "unknown-time"
    ts = str(ts).strip()
    # If it's a date-only string (from journals), keep it clean
    if _DATE_ONLY.match(ts):
        return ts
    # Otherwise, show as-is (ISO or DB timestamp); trim excessive microseconds if present
    try:
        # compact microseconds if any (optional cosmetic)
        if "." in ts and "T" in ts:
            main, rest = ts.split(".", 1)
            rest = rest.split("+")[0].split("Z")[0]  # drop tz part for shortening, we'll keep suffix if present
            ms = rest[:6]  # cap microseconds
            suffix = ""
            if "+" in ts:
                suffix = "+" + ts.split("+",1)[1]
            elif ts.endswith("Z"):
                suffix = "Z"
            return f"{main}.{ms}{suffix}"
    except Exception:
        pass
    return ts


# Optional DB
try:
    import psycopg2
except Exception:
    psycopg2 = None

ROOT = Path(__file__).resolve().parents[2]  # /XI

@dataclass
class Occurrence:
    where: str              # "db:memory_claims" | "file:/path/to.json"
    agent: str | None
    subject: str | None
    predicate: str | None
    value: str | None
    time_start: str | None  # ISO 8601 if available
    origin_file: str | None # journal path (for file-scan) or DB origin_file
    extra: dict             # any extra fields (e.g., status, reason)

def _iso(dt) -> str | None:
    if not dt: return None
    if isinstance(dt, str): return dt
    try:
        # Assume naive UTC or datetime
        if getattr(dt, "tzinfo", None) is None:
            return dt.replace(tzinfo=timezone.utc).isoformat()
        return dt.isoformat()
    except Exception:
        return str(dt)

# ---------------------------
# DB claims source (preferred)
# ---------------------------
def rvs_db(subject=None, predicate=None, subject_prefix=None, value=None, agents=None,
          since=None, until=None, limit=200, dsn=None) -> list[Occurrence]:

    if psycopg2 is None:
        return []

    con = psycopg2.connect(dsn) if dsn else psycopg2.connect(
        dbname="xi_memory", user="postgres", host="localhost", port=5432
    )

    # Discover available columns
    cols = set()
    try:
        with con.cursor() as cur:
            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'memory_claims'
            """)
            cols = {r[0] for r in cur.fetchall()}
    except Exception:
        pass

    # Column aliases
    origin_col = 'origin_file' if 'origin_file' in cols else (
        'source_file' if 'source_file' in cols else (
            'file_path' if 'file_path' in cols else None
        )
    )
    status_col = 'status' if 'status' in cols else None
    reason_col = 'reason' if 'reason' in cols else ('amend_reason' if 'amend_reason' in cols else None)
    tstart_col = (
        'time_start' if 'time_start' in cols else
        ('created_at' if 'created_at' in cols else
         ('timestamp' if 'timestamp' in cols else None))
    )
    tend_col = (
        'time_end' if 'time_end' in cols else
        ('updated_at' if 'updated_at' in cols else
         ('timestamp' if 'timestamp' in cols else None))
    )

    # WHERE parts
    wh, params = [], []
    if subject:
        wh.append("subject = %s"); params.append(subject)
    if subject_prefix:
        wh.append("subject LIKE %s"); params.append(subject_prefix + "%")
    if predicate:
        wh.append("predicate = %s"); params.append(predicate)
    if value:
        wh.append("value = %s"); params.append(value)
    if agents:
        wh.append("agent = ANY(%s)"); params.append(agents)
    if since and tstart_col:
        wh.append(f"{tstart_col} >= %s"); params.append(since)
    if until and tstart_col:
        wh.append(f"{tstart_col} <= %s"); params.append(until)
    where = ("WHERE " + " AND ".join(wh)) if wh else ""

    # ORDER BY
    order_time = tstart_col or tend_col
    order_clause = f"ORDER BY COALESCE({tstart_col}, {tend_col}) DESC" if (tstart_col and tend_col) else (
        f"ORDER BY {order_time} DESC" if order_time else ""
    )

    # SELECT list
    sel_parts = [
        "subject",
        "predicate",
        "value",
        "agent",
        (origin_col or "NULL") + " AS origin_file",
        (status_col or "NULL") + " AS status",
        (reason_col or "NULL") + " AS reason",
        (tstart_col or "NULL") + " AS time_start",
        (tend_col or "NULL")   + " AS time_end",
    ]
    select_list = ", ".join(sel_parts)

    sql = f"""
    SELECT {select_list}
    FROM memory_claims
    {where}
    {order_clause}
    LIMIT %s
    """
    params.append(limit)

    out = []
    try:
        with con:
            with con.cursor() as cur:
                cur.execute(sql, params)
                for (s, p, v, a, origin, status, reason, t_start, t_end) in cur.fetchall():
                    out.append(Occurrence(
                        where="db:memory_claims",
                        agent=a, subject=s, predicate=p, value=v,
                        time_start=_iso(t_start or t_end),
                        origin_file=origin,
                        extra={"status": status, "reason": reason, "source": "db"}
                    ))
    finally:
        try: con.close()
        except Exception: pass
    return out


# ---------------------------
# Filesystem fallback
# ---------------------------
def _iter_journal_files(agents=None):
    mem = ROOT / "memory"
    for agent_dir in mem.iterdir():
        if not agent_dir.is_dir():
            continue
        agent = agent_dir.name
        if agents and agent not in set(agents):
            continue
        for d in agent_dir.iterdir():
            # *journal/session/*.json
            if d.is_dir() and d.name.endswith("_journal"):
                sess = d / "session"
                if sess.exists():
                    for p in sess.glob(f"{agent}_session_journal_*.json"):
                        yield agent, p

def _contains_text(hay, needle):
    try:
        return needle.lower() in hay.lower()
    except Exception:
        return False

def _as_text(x):
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, (list, tuple)):
        return "\n".join(str(i) for i in x if i)
    if isinstance(x, dict):
        # flatten dict values (best effort)
        return "\n".join(str(v) for v in x.values() if v)
    return str(x)

# Filesystem fallback
def rvs_files(subject=None, predicate=None, value=None, agents=None, limit=200,
              subject_prefix=None, hours=None) -> list[Occurrence]:
    out = []
    for agent, path in _iter_journal_files(agents):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        ts = data.get("_meta", {}).get("timestamp") or data.get("date")
        if not is_recent(ts, hours):
            continue


        # ---------- Structured claims (preferred) ----------
        claims = data.get("claims") or []
        matched = False
        for c in claims:
            s = c.get("subject"); p = c.get("predicate"); v = c.get("value")
            # subject exact OR (if no exact subject is given) prefix match
            if subject is not None:
                subj_ok = (s == subject)
            elif subject_prefix is not None:
                subj_ok = isinstance(s, str) and s.startswith(subject_prefix)
            else:
                subj_ok = True

            pred_ok = (predicate is None or p == predicate)
            val_ok  = (value is None or v == value)
            if subj_ok and pred_ok and val_ok:
                out.append(Occurrence(
                    where=f"file:{path}",
                    agent=agent, subject=s, predicate=p, value=v,
                    time_start=ts,
                    origin_file=str(path),
                    extra={"source": "claims"}
                ))
                matched = True

        # ---------- Light text search fallback ----------
        if not matched and any(x is not None for x in (subject, predicate, value, subject_prefix)):
            blob = "\n".join([
                _as_text(data.get("summary")),
                _as_text(data.get("thoughts")),
                _as_text(data.get("decisions")),
            ])
            if not blob.strip():
                blob = _as_text(data)  # fallback: whole file flattened
            wants = []
            if subject: wants.append(subject)
            if predicate: wants.append(predicate)
            if value: wants.append(value)
            if subject_prefix: wants.append(subject_prefix)
            if wants and all(_contains_text(blob, w) for w in wants):
                out.append(Occurrence(
                    where=f"file:{path}",
                    agent=agent, subject=subject, predicate=predicate, value=value,
                    time_start=ts,
                    origin_file=str(path),
                    extra={"source": "text"}
                ))

        if len(out) >= limit:
            break

    out.sort(key=lambda o: o.time_start or "", reverse=True)
    return out[:limit]


# ---------------------------
# Public API
# ---------------------------
def rvs(subject=None, predicate=None, value=None, agents=None,
        since=None, until=None, limit=200, dsn=None,
        subject_prefix=None, hours=None) -> list[Occurrence]:
    occ_db = rvs_db(
        subject=subject,
        predicate=predicate,
        subject_prefix=subject_prefix,
        value=value,
        agents=agents,
        since=since,
        until=until,
        limit=limit,
        dsn=dsn,
    )

    occ_fs = rvs_files(subject, predicate, value, agents, limit,
                       subject_prefix=subject_prefix, hours=hours)
    merged = occ_db + [o for o in occ_fs if not _dup_in_db(o, occ_db)]
    merged.sort(key=lambda o: o.time_start or "", reverse=True)
    final = merged[:limit]
    # log retrieval usage
    tok_count = sum(len(str(o.value or "").split()) for o in final)
    provenance = [o.origin_file for o in final if o.origin_file]
    log_retrieval_usage("reverse", tok_count, provenance=provenance)
    return final


def _dup_in_db(o: Occurrence, occ_db: list[Occurrence]) -> bool:
    for d in occ_db:
        if (d.agent == o.agent and d.subject == o.subject and d.predicate == o.predicate
            and d.value == o.value and (d.origin_file == o.origin_file or not o.origin_file)):
            return True
    return False

def format_report(occ: list[Occurrence], source: str | None = None,
                  where_filter: str | None = None,
                  color: bool = True) -> str:
    """
    Pretty-print occurrences.
    - source: filter by extra['source'] ('db', 'claims', 'text')
    - where_filter: filter by 'db' or 'file' (prefix on o.where)
    - color: ANSI colorize output (set False to disable)
    """
    # Normalize filters
    if source:
        source = source.strip().lower()
    if where_filter:
        where_filter = where_filter.strip().lower()

    # Apply filters
    filtered = []
    for o in occ:
        src_type = (o.extra or {}).get("source")
        if source and (src_type or "").lower() != source:
            continue
        if where_filter:
            if where_filter == "db" and not str(o.where).startswith("db:"):
                continue
            if where_filter == "file" and not str(o.where).startswith("file:"):
                continue
        filtered.append(o)

    if not filtered:
        return "No occurrences found."

    lines = []
    for o in filtered:
        t = _fmt_time(o.time_start)
        agent = o.agent or "unknown-agent"
        subj = o.subject or "-"
        pred = o.predicate or "-"
        val = (o.value or "-").replace("\n"," ")
        src = (o.extra or {}).get("source", o.where)
        where_str = f"[{o.where}]" if o.where else ""

        # Colors
        t_s = _colorize(t, "dim", enabled=color)
        agent_s = _colorize(agent, "bold", enabled=color)
        sp_s = _colorize(f"{subj}.{pred}", "cyan", enabled=color)
        src_s = _colorize(f"({src})", "yellow", enabled=color)
        where_s = _colorize(where_str, "magenta", enabled=color)

        line = f"{t_s} | {agent_s} {src_s} | {sp_s} = {val} {where_s}"
        lines.append(line)

    body = "\n".join(lines)
    # GAEL-style agent-facing wrapper (controller shells this CLI and injects the text)
    header = (
        "[Memory Recall]\n"
        "Relevance (system-computed): 1.00\n"
        "Content:\n"
    )
    return header + body


# ---------------------------
# CLI
# ---------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    ap.add_argument("--subject")
    ap.add_argument("--predicate")
    ap.add_argument("--value")
    ap.add_argument("--agents", help="comma-separated, e.g. davinci,hermes")
    ap.add_argument("--since", help="ISO8601")
    ap.add_argument("--until", help="ISO8601")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dsn", help="psycopg2 DSN (optional)")
    ap.add_argument("--source", help="Filter by source: db | claims | text")
    ap.add_argument("--where", help="Filter by origin: db | file")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    ap.add_argument("--subject-prefix")
    ap.add_argument("--hours", type=int, help="Only include journal files within the last N hours (filesystem fallback).")
    args = ap.parse_args()

    agents = [a.strip() for a in args.agents.split(",")] if args.agents else None
    occ = rvs(
        subject=args.subject,
        predicate=args.predicate,
        value=args.value,
        agents=agents,
        since=args.since,
        until=args.until,
        limit=args.limit,
        dsn=args.dsn,
        subject_prefix=args.subject_prefix,
        hours=args.hours,
    )

    # Optional filters
    if args.source:
        occ = [o for o in occ if (o.extra or {}).get("source") == args.source]
    if args.where:
        if args.where == "db":
            occ = [o for o in occ if str(o.where).startswith("db:")]
        elif args.where == "file":
            occ = [o for o in occ if str(o.where).startswith("file:")]

    if args.json:
        hits = []
        for o in occ:
            hits.append({
                "where": o.where,
                "source": (o.extra or {}).get("source"),
                "subject": o.subject,
                "predicate": o.predicate,
                "value": o.value,
                "file": o.origin_file,
                "timestamp": o.time_start,
                "agent": o.agent,
            })
        print(json.dumps({"hits": hits}, ensure_ascii=False))
    else:
        print(format_report(
            occ,
            source=args.source,
            where_filter=args.where,
            color=not args.no_color and _supports_color()
        ))



