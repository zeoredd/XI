# ===============================
# file: diver/cascade_diver.py
# ===============================
from __future__ import annotations
from typing import List, Dict
from collections import defaultdict, deque
import os, time
import psycopg2.extras

from .base_diver import Hit, _fts_search, _neighbors
from .diver_utils import Filters, recency_decay

# Env-tunable weights (defaults are sensible)
W_RANK    = float(os.getenv("DIVER_W_RANK",    "0.6"))
W_RECENCY = float(os.getenv("DIVER_W_RECENCY", "0.3"))
W_GRAPH   = float(os.getenv("DIVER_W_GRAPH",   "0.1"))
HOP_DECAY = float(os.getenv("DIVER_HOP_DECAY", "0.65"))

# Optional relation-specific weights
REL_W = {
    "summarizes": 1.2,
    "supports":   1.1,
    "related":    1.0,
    "contradicts":0.8,
}

def _norm_q(s: str) -> str:
    q = (s or "").strip()
    if len(q) >= 2 and ((q[0] == q[-1] == '"') or (q[0] == q[-1] == "'")):
        q = q[1:-1].strip()
    return q

def cascade_diver_search(conn,
                         query: str,
                         filters: Filters,
                         topk: int = 20,
                         depth: int = 2,
                         beam: int = 40,
                         w_rank: float | None = None,
                         w_recency: float | None = None,
                         w_graph: float | None = None,
                         hop_decay: float | None = None) -> List[Hit]:
    # Resolve weights (CLI can pass overrides; otherwise env; otherwise defaults)
    w_rank    = W_RANK    if w_rank    is None else w_rank
    w_recency = W_RECENCY if w_recency is None else w_recency
    w_graph   = W_GRAPH   if w_graph   is None else w_graph
    hop_decay = HOP_DECAY if hop_decay is None else hop_decay

    qn = _norm_q(query)

    # 1) Seed with FTS (reuse base helper; already uses stored text_fts + ts_headline)
    seed_rows = _fts_search(conn, qn, filters, limit=beam)
    seeds = {r["id"]: float(r["rank"] or 0.0) for r in seed_rows}

    frontier = deque([(sid, 0) for sid in seeds.keys()])
    seen = set(seeds.keys())
    graph_score = defaultdict(float)

    # 2) Multi-hop expand across links
    while frontier:
        node, hop = frontier.popleft()
        if hop >= depth:
            continue
        neighs = _neighbors(conn, [node]).get(node, [])
        for nb in neighs:
            nid = nb["id"]
            if nid not in seen:
                seen.add(nid)
                frontier.append((nid, hop + 1))
            rel = (nb.get("type") or "related")
            rel_w = REL_W.get(rel, 1.0)
            graph_score[nid] += seeds.get(node, 0.0) * float(nb.get("confidence") or 1.0) * rel_w * (hop_decay ** (hop + 1))

    cand_ids = list(seen)
    if not cand_ids:
        return []

    # 3) Fetch candidate rows with a snippet (use stored text_fts + headline)
    #    We build the tsquery here again to get consistent highlighting.
    if '"' in qn or '“' in qn or '”' in qn:
        tsquery_sql = "phraseto_tsquery('english', %s)"
        q_param = qn.replace('“', '"').replace('”', '"')
    else:
        tsquery_sql = "plainto_tsquery('english', %s)"
        q_param = qn

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(f"""
            WITH q AS (SELECT {tsquery_sql} AS tsq)
            SELECT id, layer, agent, thread, ts, text,
                   ts_headline('english', text, q.tsq, 'MaxFragments=2, ShortWord=2') AS snippet
            FROM memories, q
            WHERE id = ANY(%s)
        """, (q_param, cand_ids))
        rows = cur.fetchall()

    # 4) Final blend: rank + recency + graph (normalized)
    id2rank: Dict[str, float] = {r["id"]: float(next((s["rank"] for s in seed_rows if s["id"] == r["id"]), 0.0)) for r in rows}
    now = int(time.time())
    prelim: List[Hit] = []
    for r in rows:
        rank     = id2rank.get(r["id"], 0.0)
        rank_n   = rank / (rank + 1.0)
        rec      = recency_decay(now, r["ts"] or 0)
        g        = graph_score.get(r["id"], 0.0)
        g_n      = g / (g + 1.0)
        score    = w_rank*rank_n + w_recency*rec + w_graph*g_n
        excerpt  = (r.get("snippet") or r.get("text") or "")[:280]
        prelim.append(Hit(
            id=r["id"], layer=r["layer"], agent=r["agent"], thread=r["thread"], ts=r["ts"],
            excerpt=excerpt, score=score, bm25=None, sim=None,
            links=None, trace=[r["layer"]],
        ))

    prelim.sort(key=lambda h: (-h.score, -(h.ts or 0), h.id))
    top = prelim[:topk]

    # 5) Hydrate neighbors/trace for top-K only (cheap)
    id_list = [h.id for h in top]
    nbs = _neighbors(conn, id_list)

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        for h in top:
            neighs = nbs.get(h.id, [])
            h.links = [
                {
                    "id": nb["id"],
                    "type": nb.get("type") or "link",
                    "confidence": float(nb.get("confidence") or 1.0),
                    "dir": nb.get("dir") or "out",
                }
                for nb in neighs
            ]
            neighbor_layers = []
            for nb in neighs[:3]:
                cur.execute("SELECT layer FROM memories WHERE id = %s;", (nb["id"],))
                lr = cur.fetchone()
                if lr and lr[0]:
                    neighbor_layers.append(f'{nb.get("dir","out")}:{nb.get("type","link")}→{lr[0]}')
            h.trace = [h.layer] + neighbor_layers

    return top

