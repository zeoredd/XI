# ===============================
# file: diver/base_diver.py
# ===============================
"""
Base Diver — Quick Relevance Engine (non-AI default), Postgres edition.

Capabilities:
- Accepts natural-language query *or* a memory UUID.
- Vector-first search if `mem_vectors` present; else Postgres FTS fallback.
- Supports filters: layer/agent/thread/time.
- Returns top-k hits with minimal provenance (1-hop links) and a simple score.

Schema assumptions (Postgres):
- memories(id TEXT PRIMARY KEY, layer TEXT, agent TEXT, thread TEXT, ts INTEGER, text TEXT)
- links(src TEXT, dst TEXT, link_type TEXT, confidence REAL)
- OPTIONAL: mem_vectors(id TEXT PRIMARY KEY, vec VECTOR/bytea/foreign index)
"""
from __future__ import annotations
import time
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

import psycopg2
import psycopg2.extras

from .diver_utils import Filters, recency_decay

_LINKS_SCHEMA_CACHE = None  # (src_col, dst_col, conf_col, type_col) or None

def _detect_links_schema(conn):
    """
    Detects column names for the links table.
    Returns tuple (src, dst, conf, ltype) or None if table/cols not found.
    Caches result in _LINKS_SCHEMA_CACHE.
    """
    global _LINKS_SCHEMA_CACHE
    if _LINKS_SCHEMA_CACHE is not None:
        return _LINKS_SCHEMA_CACHE

    # Does table exist?
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = 'links'
            LIMIT 1;
        """)
        if cur.fetchone() is None:
            _LINKS_SCHEMA_CACHE = None
            return None

        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'links';
        """)
        cols = {r[0].lower() for r in cur.fetchall()}

    # candidates

    src_cands = ['src', 'source', 'from_id', 'from', 's', 'parent_uuid']
    dst_cands = ['dst', 'dest', 'to_id', 'to', 'd', 'target', 'child_uuid']
    conf_cands = ['confidence', 'weight', 'score', 'prob']
    type_cands = ['link_type', 'type', 'kind', 'relation']

    def pick(cands):
        for c in cands:
            if c in cols:
                return c
        return None

    src = pick(src_cands)
    dst = pick(dst_cands)
    conf = pick(conf_cands)
    ltype = pick(type_cands)

    if src and dst:
        _LINKS_SCHEMA_CACHE = (src, dst, conf, ltype)
    else:
        _LINKS_SCHEMA_CACHE = None
    return _LINKS_SCHEMA_CACHE

@dataclass
class Hit:
    id: str
    layer: str
    agent: str
    thread: str
    ts: int
    excerpt: str
    score: float
    bm25: Optional[float] = None
    sim: Optional[float] = None
    links: Optional[List[Dict[str, Any]]] = None
    trace: Optional[List[str]] = None

# --- internals ---

def _has_vectors(conn) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM mem_vectors LIMIT 1;")
            _ = cur.fetchone()
            return True
    except Exception:
        return False

def _fts_search(conn, q_text: str, filters: Filters, limit: int):
    # Build tsquery (phrase if quotes present)
    if '"' in q_text or '“' in q_text or '”' in q_text:
        tsquery_sql = "phraseto_tsquery('english', %s)"
        q_param = q_text.replace('“', '"').replace('”', '"')
    else:
        tsquery_sql = "plainto_tsquery('english', %s)"
        q_param = q_text

    where, args = filters.where_clause("m")
    sql = f"""
    WITH q AS (SELECT {tsquery_sql} AS tsq)
    SELECT
      m.id, m.layer, m.agent, m.thread, m.ts, m.text,
      ts_headline('english', m.text, q.tsq, 'MaxFragments=2, ShortWord=2') AS snippet,
      ts_rank_cd(m.text_fts, q.tsq, 32) AS rank
    FROM memories m, q
    WHERE m.text_fts @@ q.tsq
      {(' AND ' + where[6:]) if where else ''}
    ORDER BY rank DESC, m.ts DESC
    LIMIT %s;
    """
    params = [q_param] + args + [limit]
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# diver/base_diver.py — replace _neighbors with this
def _neighbors(conn, ids):
    schema = _detect_links_schema(conn)
    if not ids or not schema:
        return {i: [] for i in ids} if ids else {}

    src_col, dst_col, conf_col, type_col = schema
    out = {i: [] for i in ids}
    conf_sel = conf_col if conf_col else "NULL"
    type_sel = type_col if type_col else "NULL"

    with conn.cursor() as cur:
        # outgoing
        cur.execute(
            f"SELECT {src_col}, {dst_col}, {conf_sel}, {type_sel} FROM links WHERE {src_col} = ANY(%s);",
            (ids,),
        )
        for src, dst, conf, rel in cur.fetchall():
            out[src].append({"id": dst, "confidence": float(conf or 1.0), "type": rel or "link", "dir": "out"})

        # incoming
        cur.execute(
            f"SELECT {dst_col}, {src_col}, {conf_sel}, {type_sel} FROM links WHERE {dst_col} = ANY(%s);",
            (ids,),
        )
        for dst, src, conf, rel in cur.fetchall():
            out[dst].append({"id": src, "confidence": float(conf or 1.0), "type": rel or "link", "dir": "in"})

    return out

# --- public API ---

def base_diver_search(conn, query_or_uuid: str, topk: int = 20,
                      filters: Optional[Filters] = None, use_vec: bool = True,
                      now_ts: Optional[int] = None, by_id: bool = False) -> List[Hit]:
    filters = filters or Filters()
    now_ts = now_ts or int(time.time())
    hits: List[Hit] = []

    # normalize input
    q = (query_or_uuid or "").strip()
    if len(q) >= 2 and ((q[0] == q[-1] == '"') or (q[0] == q[-1] == "'")):
        q = q[1:-1].strip()

    # --- Explicit ID fast-path ---
    if by_id:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, layer, agent, thread, ts, text FROM memories WHERE id = %s;",
                (q,)
            )
            row = cur.fetchone()
        if not row:
            return []

        nbs = _neighbors(conn, [row[0]])
        neighs = nbs.get(row[0], [])

        base = Hit(
            id=row[0], layer=row[1], agent=row[2], thread=row[3], ts=row[4],
            excerpt=(row[5] or "")[:280], score=1.0
        )
        base.links = [
            {
                "id": nb["id"],
                "type": nb.get("type") or "link",
                "confidence": float(nb.get("confidence") or 1.0),
                "dir": nb.get("dir") or "out",
            }
            for nb in neighs
        ]

        neighbor_layers: List[str] = []
        with conn.cursor() as cur:
            for nb in neighs[:3]:
                cur.execute("SELECT layer FROM memories WHERE id = %s;", (nb["id"],))
                lr = cur.fetchone()
                if lr and lr[0]:
                    neighbor_layers.append(f'{nb.get("dir","out")}:{nb.get("type","link")}→{lr[0]}')
        base.trace = [base.layer] + neighbor_layers

        hits.append(base)
        return hits  # <-- add this

    # --- UUID-ish heuristic path (optional) ---
    is_uuid = len(q) >= 16 and all(c in "0123456789abcdef-" for c in q.lower())
    if is_uuid:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, layer, agent, thread, ts, text FROM memories WHERE id = %s;",
                (q,)
            )
            row = cur.fetchone()
        if not row:
            return []

        nbs = _neighbors(conn, [row[0]])
        neighs = nbs.get(row[0], [])

        base = Hit(
            id=row[0], layer=row[1], agent=row[2], thread=row[3], ts=row[4],
            excerpt=(row[5] or "")[:280], score=1.0
        )
        base.links = [
            {
                "id": nb["id"],
                "type": nb.get("type") or "link",
                "confidence": float(nb.get("confidence") or 1.0),
                "dir": nb.get("dir") or "out",
            }
            for nb in neighs
        ]

        neighbor_layers: List[str] = []
        with conn.cursor() as cur:
            for nb in neighs[:3]:
                cur.execute("SELECT layer FROM memories WHERE id = %s;", (nb["id"],))
                lr = cur.fetchone()
                if lr and lr[0]:
                    neighbor_layers.append(f'{nb.get("dir","out")}:{nb.get("type","link")}→{lr[0]}')
        base.trace = [base.layer] + neighbor_layers

        hits.append(base)
        return hits  # <-- add this

    # --- Vector-first (stub) → FTS fallback ---
    if use_vec and _has_vectors(conn):
        pass  # TODO: vector seed

    rows = _fts_search(conn, q, filters, topk)
    id_list = [r["id"] for r in rows]
    nbs = _neighbors(conn, id_list)

    for r in rows:
        rec = recency_decay(now_ts, r["ts"]) if r["ts"] else 0.0
        rank = float(r["rank"] or 0.0)
        rank_norm = rank / (rank + 1.0)
        score = 0.65 * rank_norm + 0.35 * rec

        neighs = nbs.get(r["id"], [])
        links = [
            {
                "id": nb["id"],
                "type": nb.get("type") or "link",
                "confidence": float(nb.get("confidence") or 1.0),
                "dir": nb.get("dir") or "out",
            }
            for nb in neighs
        ]

        neighbor_layers: List[str] = []
        with conn.cursor() as cur:
            for nb in neighs[:3]:
                cur.execute("SELECT layer FROM memories WHERE id = %s;", (nb["id"],))
                lr = cur.fetchone()
                if lr and lr[0]:
                    neighbor_layers.append(f'{nb.get("dir","out")}:{nb.get("type","link")}→{lr[0]}')

        excerpt = (r.get("snippet") or r.get("text") or "")[:280]
        hit = Hit(
            id=r["id"], layer=r["layer"], agent=r["agent"], thread=r["thread"], ts=r["ts"],
            excerpt=excerpt, score=score, bm25=None, links=links, trace=[r["layer"]] + neighbor_layers
        )
        hits.append(hit)

    # <-- make sure these are OUTSIDE the loop
    hits.sort(key=lambda h: (-h.score, -(h.ts or 0), h.id))
    return hits[:topk]

