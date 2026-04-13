# diver/search_pg.py
from .diver_utils import connect
import psycopg2.extras

# Optional: simple sanitizer → converts user text to a tsquery safely
def _make_tsquery(cur, query: str):
    # If query contains quotes, try phrase search; else plainto
    q = query.strip()
    if '"' in q or '“' in q or '”' in q:
        # Compact consecutive quoted parts into phraseto_tsquery
        return "phraseto_tsquery('english', %s)", q.replace('“', '"').replace('”', '"')
    else:
        return "plainto_tsquery('english', %s)", q

def fts_search(query: str, limit: int = 20, min_rank: float = 0.0):
    sql = """
    WITH q AS (
      SELECT {tsquery} AS tsq
    )
    SELECT
      m.id,
      m.agent,
      m.thread,
      m.ts AS ts_epoch,
      ts_headline('english', m.text, q.tsq, 'MaxFragments=2, ShortWord=2') AS snippet,
      ts_rank_cd(to_tsvector('english', m.text), q.tsq, 32) AS rank
    FROM memories m, q
    WHERE to_tsvector('english', m.text) @@ q.tsq
    {rank_filter}
    ORDER BY rank DESC, m.ts DESC
    LIMIT %s;
    """
    tsquery_sql, param = _make_tsquery(None, query)
    rank_filter = "AND ts_rank_cd(to_tsvector('english', m.text), q.tsq, 32) >= %s" if min_rank > 0 else ""
    sql = sql.format(tsquery=tsquery_sql, rank_filter=rank_filter)

    params = [param]
    if min_rank > 0:
        params.append(min_rank)
    params.append(limit)

    conn = connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            # shape result like old diver expected
            return [
                {
                    "id": r["id"],
                    "agent": r.get("agent"),
                    "thread": r.get("thread"),
                    "ts": r.get("ts_epoch"),
                    "snippet": r.get("snippet"),
                    "rank": float(r.get("rank") or 0),
                }
                for r in rows
            ]
    finally:
        conn.close()

