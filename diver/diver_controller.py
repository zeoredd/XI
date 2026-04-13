# ===============================
# file: diver/diver_controller.py
# ===============================
from __future__ import annotations
from typing import Optional
import warnings, json, argparse

from .diver_utils import Filters, connect
from .base_diver import base_diver_search
from .cascade_diver import cascade_diver_search

# ➕ Token usage logging
from abilities.common_abilities.token_logger import log_retrieval_usage

def run_diver_search(query: str, mode: str = "base",
                     topk: int = 20, layer: Optional[str] = None,
                     agent: Optional[str] = None, thread: Optional[str] = None,
                     after: Optional[int] = None, before: Optional[int] = None,
                     use_vec: bool = True, by_id: bool = False,
                     depth: int = 2, beam: int = 40):
    filters = Filters(layer=layer, agent=agent, thread=thread, after=after, before=before)
    conn = connect()
    if mode == "base":
        hits = base_diver_search(conn, query, topk=topk, filters=filters, use_vec=use_vec, by_id=by_id)
    elif mode == "cascade":
        hits = cascade_diver_search(conn, query, filters=filters, topk=topk, depth=depth, beam=beam)
    else:
        raise ValueError("mode must be 'base' or 'cascade'")

    results = [h.__dict__ for h in hits]

    # 🔧 Safeguard: hydrate missing/empty excerpts from DB (short slice)
    # This prevents "UUID-only" feelings downstream.
    missing_ids = [r["id"] for r in results if not (r.get("excerpt") or "").strip()]
    if missing_ids:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, SUBSTRING(text FOR 280) AS excerpt FROM memories WHERE id = ANY(%s);",
                (missing_ids,)
            )
            repl = {row[0]: (row[1] or "") for row in cur.fetchall()}
        for r in results:
            if not (r.get("excerpt") or "").strip():
                r["excerpt"] = repl.get(r["id"], "")

    # Count tokens + provenance logging
    token_count = sum(len((r.get("excerpt") or "").split()) for r in results)
    provenance = [r.get("id") for r in results]
    log_retrieval_usage("diver", token_count, mode=mode, provenance=provenance)

    # Mask UUIDs for agent-facing snippets
    for i, r in enumerate(results):
        if "id" in r and r["id"]:
            r["masked_id"] = f"entry-{i+1}"
        # Friendly agent-facing field for GAEL builders
        # (keeps provenance hidden but preserves readable content)
        # Build agent-facing GAEL block (do not expose UUID/provenance)
        rel = "n/a"
        try:
            if "score" in r and r["score"] is not None:
                rel = f"{float(r['score']):.2f}"
        except Exception:
            pass
        content = r.get("excerpt") or "(no excerpt)"
        r["display"] = (
            "[Memory Recall]\n"
            f"Relevance (system-computed): {rel}\n"
            "Content:\n"
            f"{content}"
        )

    return results

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--db", default=None)  # ignored; always Postgres via diver_utils.connect()
    ap.add_argument("--mode", default="base", choices=["base", "cascade"])
    ap.add_argument("--topk", type=int, default=20)
    ap.add_argument("--layer")
    ap.add_argument("--agent")
    ap.add_argument("--thread")
    ap.add_argument("--after")
    ap.add_argument("--before")
    ap.add_argument("--no_vec", action="store_true")
    ap.add_argument("--by-id", action="store_true")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--beam", type=int, default=40)
    args = ap.parse_args()

    if args.db:
        warnings.warn("--db is ignored: Diver now uses Postgres via diver_utils.connect()")

    after = int(args.after) if args.after else None
    before = int(args.before) if args.before else None

    out = run_diver_search(
        query=args.query,
        mode=args.mode,
        topk=args.topk,
        layer=args.layer,
        agent=args.agent,
        thread=args.thread,
        after=after,
        before=before,
        use_vec=(not args.no_vec),
        by_id=args.by_id,
        depth=args.depth,
        beam=args.beam,
    )
    print(json.dumps(out, indent=2, ensure_ascii=False))


