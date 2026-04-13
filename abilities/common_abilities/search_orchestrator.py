from __future__ import annotations
from typing import Optional, Dict, Any, List
import os, json, time

from diver.diver_controller import run_diver_search

DEFAULT_TOPK   = int(os.getenv("DIVER_TOPK", "8"))
DEFAULT_DEPTH  = int(os.getenv("DIVER_DEPTH", "2"))
DEFAULT_BEAM   = int(os.getenv("DIVER_BEAM", "60"))
SNIPPET_BUDGET = int(os.getenv("DIVER_SNIPPET_BUDGET", "1200"))

def _norm_q(s: str) -> str:
    q = (s or "").strip()
    if len(q) >= 2 and ((q[0] == q[-1] == '"') or (q[0] == q[-1] == "'")):
        q = q[1:-1].strip()
    return q

def _looks_like_id(q: str) -> bool:
    # your ids can be 'uuid-3' or hex UUIDs; treat id if no whitespace and length <= 64
    return " " not in q and len(q) <= 64

def _format_hits_for_prompt(hits: List[Dict[str, Any]], budget: int = SNIPPET_BUDGET) -> str:
    """
    Agent-facing context must NOT leak raw IDs.
    Build GAEL '[Memory Recall]' blocks with system-computed relevance.
    Allow suppression via XI_DIVER_SUPPRESS_CONTEXT=1.
    """
    import os
    if os.getenv("XI_DIVER_SUPPRESS_CONTEXT") == "1":
        return ""
    lines, used = [], 0
    for h in hits:
        content = (h.get("excerpt") or "").strip()
        if not content:
            continue
        if len(content) > budget - used:
            content = content[:max(0, budget - used - 3)] + "…" if budget - used > 3 else ""
        rel = "n/a"
        try:
            if h.get("score") is not None:
                rel = f"{float(h['score']):.2f}"
        except Exception:
            pass
        block = (
            "[Memory Recall]\n"
            f"Relevance (system-computed): {rel}\n"
            "Content:\n"
            f"{content}\n"
        )
        if used + len(block) > budget:
            break
        lines.append(block); used += len(block)
    return "".join(lines)

def run_search(query: str,
               agent: Optional[str] = None,
               thread: Optional[str] = None,
               *,
               topk: int = DEFAULT_TOPK,
               cascade_depth: int = DEFAULT_DEPTH,
               cascade_beam: int = DEFAULT_BEAM,
               escalate_if_few: int = 2,
               escalate_if_terms: tuple = ("why", "how", "trace", "links"),
               use_vec: bool = True) -> Dict[str, Any]:
    """
    Unified search: ID → base (FTS) → optional cascade.
    Returns: {"hits": [...], "context": "...", "mode": "base|cascade|by-id"}
    """
    q = _norm_q(query)

    # 1) ID path (explicit)
    if _looks_like_id(q):
        hits = run_diver_search(
            query=q, mode="base", topk=1,
            layer=None, agent=agent, thread=thread,
            after=None, before=None, use_vec=use_vec, by_id=True,
            depth=cascade_depth, beam=cascade_beam,
        )
        ctx = _format_hits_for_prompt(hits)
        return {"hits": hits, "context": ctx, "mode": "by-id"}

    # 2) Base (fast FTS). Keep quick timeouts by limiting topk modestly here.
    hits = run_diver_search(
        query=q, mode="base", topk=topk,
        layer=None, agent=agent, thread=thread,
        after=None, before=None, use_vec=use_vec, by_id=False,
        depth=cascade_depth, beam=cascade_beam,
    )

    need_deep = (len(hits) < escalate_if_few) or any(t in q.lower() for t in escalate_if_terms)

    # 3) Cascade (multi-hop) if needed
    if need_deep:
        deep_hits = run_diver_search(
            query=q, mode="cascade", topk=max(topk, 12),
            layer=None, agent=agent, thread=thread,
            after=None, before=None, use_vec=use_vec, by_id=False,
            depth=cascade_depth, beam=cascade_beam,
        )
        # merge unique ids keeping base order first
        seen = {h["id"] for h in hits}
        hits += [h for h in deep_hits if h["id"] not in seen]
        mode = "cascade"
    else:
        mode = "base"

    ctx = _format_hits_for_prompt(hits)
    return {"hits": hits, "context": ctx, "mode": mode}

