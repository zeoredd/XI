# abilities/common_abilities/sentinel.py
import json, hashlib, re
import datetime as _dt

sha1 = lambda s: hashlib.sha1(s.encode("utf-8","ignore")).hexdigest()

# --- Rollups ---

def week_bounds(d: _dt.datetime):
    # ISO week: Monday..Sunday, using local date from d
    start = (d - _dt.timedelta(days=(d.weekday()))).date()
    end = (start + _dt.timedelta(days=6))
    return start, end

def period_bounds(d: _dt.datetime):
    # Simple 4-week block ending last Sunday (start is current week start - 3 weeks)
    start, _ = week_bounds(d)
    start = start - _dt.timedelta(weeks=3)
    end = start + _dt.timedelta(days=27)  # 4 weeks - 1 day
    return start, end

def quarter_bounds(d: _dt.datetime):
    q = (d.month-1)//3
    start = _dt.datetime(d.year, 3*q+1, 1).date()
    # first day of next quarter minus one
    if q == 3:
        end = _dt.datetime(d.year+1, 1, 1).date() - _dt.timedelta(days=1)
    else:
        end = _dt.datetime(d.year, 3*q+4, 1).date() - _dt.timedelta(days=1)
    return start, end

def _child_set_fp(child_hashes):
    return sha1("|".join(sorted(child_hashes)))

def ensure_rollup(conn, agent: str, level: str, start_date, end_date) -> int | None:
    """
    Create or update a rollup entry for (agent, level, start_date..end_date).
    Returns the rollup memory_entries.id or None if no children.
    """
    cur = conn.cursor()

    # fetch child session entries
    cur.execute("""
        SELECT id, entry_hash, content
        FROM memory_entries
        WHERE agent=%s
          AND thread_type='session'
          AND timestamp >= %s::date
          AND timestamp < (%s::date + INTERVAL '1 day') + INTERVAL '1 second'
          AND date(timestamp) BETWEEN %s AND %s
        ORDER BY timestamp ASC
    """, (agent, start_date, end_date, start_date, end_date))
    rows = cur.fetchall()
    if not rows:
        return None

    child_ids = [r[0] for r in rows]
    child_hashes = [r[1] for r in rows if r[1]]
    cfp = _child_set_fp(child_hashes)

    # check existing rollup
    cur.execute("""
        SELECT id, entry_id, child_set_fp FROM memory_rollups
        WHERE agent=%s AND level=%s AND start_date=%s AND end_date=%s
        LIMIT 1
    """, (agent, level, start_date, end_date))
    existing = cur.fetchone()

    # build a simple rollup text (placeholder summary stitching)
    # You can replace this with your actual summarizer.
    bullets = []
    for _, _, content in rows[:50]:  # cap to keep small
        for line in content.splitlines():
            if line.startswith("[Decisions]") or line.startswith("[Summary]"):
                bullets.append(line)
                break
    rollup_text = (
        f"[Summary]: {agent} {level} {start_date}–{end_date}\n"
        f"[Decisions]:\n- " + "\n- ".join(bullets[:8]) + "\n"
        f"[Thoughts]: High-level themes auto-collected.\n"
    ).strip()

    # fingerprints
    from abilities.common_abilities.memory_indexer import _sha1_text, get_embedder_fingerprint, get_embedding, _assert_dim
    content_fp = _sha1_text(rollup_text)
    embedder_fp = get_embedder_fingerprint()
    emb = get_embedding(rollup_text); _assert_dim(emb)

    # insert/update memory_entries (rollup entry)
    if existing:
        entry_id = existing[1]
        cur.execute("""
            UPDATE memory_entries
            SET content=%s, content_fingerprint=%s, embedder_fingerprint=%s,
                thread_type='rollup', period=%s
            WHERE id=%s
        """, (rollup_text, content_fp, embedder_fp, level, entry_id))
        cur.execute("""
            UPDATE memory_rollups
            SET child_ids=%s, child_set_fp=%s
            WHERE id=%s
        """, (child_ids, cfp, existing[0]))
    else:
        cur.execute("""
            INSERT INTO memory_entries (
              entry_hash, uuid, agent, timestamp, content, source_file, embedding, thread_type, period,
              content_fingerprint, embedder_fingerprint, validator_flags
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb)
            RETURNING id
        """, (
            sha1(rollup_text), f"rollup-{agent}-{level}-{start_date}", agent,
            _dt.datetime.now(_dt.timezone.utc),
            rollup_text, f"rollups/{agent}/{level}_{start_date}_{end_date}.md",
            emb, 'rollup', level,
            content_fp, embedder_fp
        ))
        entry_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO memory_rollups(agent, level, start_date, end_date, entry_id, child_ids, child_set_fp)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (agent, level, start_date, end_date, entry_id, child_ids, cfp))

    conn.commit()
    return entry_id

# --- Sentinel v1: conflicts & summary verification ---

_PREF_RE = re.compile(r"\bI\s+(love|like|hate|dislike)\s+([A-Za-z][\w\- ]{0,40})", re.IGNORECASE)

def extract_simple_prefs(text: str):
    out = []
    for m in _PREF_RE.finditer(text or ""):
        val = m.group(1).lower()
        obj = m.group(2).strip().lower()
        attr = obj.replace(" ", "_") + "_pref"
        out.append(("self", attr, val))
    return out

def enqueue_event(conn, etype: str, entry_id: int, related_ids=None, severity="info", message="", meta=None):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sentinel_events(type, entry_id, related_ids, severity, message, meta)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, (etype, entry_id, related_ids or [], severity, message, json.dumps(meta or {})))
    conn.commit()

def handle_conflict_for_entry(conn, entry_id: int, agent: str):
    cur = conn.cursor()
    cur.execute("SELECT content, timestamp FROM memory_entries WHERE id=%s", (entry_id,))
    row = cur.fetchone()
    if not row: return
    content, ts = row
    prefs = extract_simple_prefs(content)
    if not prefs: return

    for _, attr, new_val in prefs:
        # look back 90 days for prior values of same attr
        cur.execute("""
            SELECT id, content FROM memory_entries
            WHERE agent=%s AND thread_type='session' AND timestamp >= (now() - INTERVAL '90 days')
            ORDER BY timestamp DESC LIMIT 200
        """, (agent,))
        contradictors = []
        for rid, c in cur.fetchall():
            for _, a, v in extract_simple_prefs(c):
                if a == attr and v != new_val:
                    contradictors.append(rid)
        if contradictors:
            message = f"Potential preference conflict on {attr}: now '{new_val}', previously different."
            enqueue_event(conn, "conflict", entry_id, related_ids=contradictors, severity="warn",
                          message=message,
                          meta={"attr": attr, "new": new_val, "prev_ids": contradictors})

            # annotate validator_flags on the new entry (flag only)
            cur.execute("""
              UPDATE memory_entries
              SET validator_flags = COALESCE(validator_flags,'{}'::jsonb) || %s::jsonb
              WHERE id=%s
            """, (json.dumps({"sentinel_conflict":{"attr":attr,"new":new_val,"contradictors":contradictors}}), entry_id))
            conn.commit()

def handle_verify_summary(conn, rollup_entry_id: int, agent: str, start_date, end_date):
    """
    Lightweight verifier: checks presence of sources in time window and flags for review.
    (You can later upgrade this to full vector-coverage scoring.)
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM memory_entries
        WHERE agent=%s AND thread_type='session'
          AND date(timestamp) BETWEEN %s AND %s
        LIMIT 1
    """, (agent, start_date, end_date))
    has_sources = cur.fetchone() is not None

    msg = "Summary verification: sources present in window." if has_sources else "No sources found in window."
    enqueue_event(conn, "verify_summary", rollup_entry_id, severity=("info" if has_sources else "warn"),
                  message=msg, meta={"start":str(start_date), "end":str(end_date), "agent":agent})
    cur.execute("""
      UPDATE memory_entries
      SET validator_flags = COALESCE(validator_flags,'{}'::jsonb) || %s::jsonb
      WHERE id=%s
    """, (json.dumps({"sentinel_verify":{"status":"needs_review","has_sources":has_sources,"window":[str(start_date),str(end_date)]}}),
          rollup_entry_id))
    conn.commit()

