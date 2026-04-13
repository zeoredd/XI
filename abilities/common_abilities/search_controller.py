#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
search_controller.py
Opinionated search escalation funnel for XI + token-budget guard.

Order:
  1) Recency (hot, tight)
  2) Recency (broad: fields=all, glob=all)
  3) RVS claims (if present)
  4) RVS file-text (if present)
  5) Vector (if present)
  6) Temporal rollups (weekly/period/quarterly)

Budgeting:
  - First `policy.early_msgs` messages: hard cap `policy.early_total_tokens` for ALL search injections.
  - After early stage: per-search cap `policy.per_search_cap_after_early` and per-turn cap `policy.turn_total_cap`.
  - Snippets are trimmed to fit budget (window around query if token-ish).

Pass in `msg_idx` (1-based) and optional `turn_tokens_used` (tokens already in this turn before search),
so the controller can compute remaining budget.

CLI (dev aid):
  python3 -m abilities.common_abilities.search_controller --query "sentinel" --agents davinci,hermes
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import argparse, json, os, re, shlex, subprocess, time
from math import ceil
from functools import lru_cache

from abilities.common_abilities.search_logging import write_run

# --- in-repo imports (hard dependencies) ---
from abilities.common_abilities import recency_search as RS
try:
    from abilities.common_abilities.time_utils import is_recent  # used by temporal scans
except Exception:
    def is_recent(ts: Optional[str], hours: int) -> bool:
        return True

# ➕ Token logging
from abilities.common_abilities.token_logger import log_retrieval_usage

# --------------------------
# Budget policy & manager
# --------------------------
@dataclass
class BudgetPolicy:
    early_msgs: int = 3
    early_total_tokens: int = 900
    per_search_cap_after_early: int = 500
    turn_total_cap: int = 4000
    # snippet shaping
    default_snippet_target_tokens: int = 180
    hard_snippet_cap_tokens: int = 240

@dataclass
class BudgetState:
    msg_idx: int
    turn_tokens_used: int
    turn_tokens_spent_on_search: int = 0

class BudgetManager:
    def __init__(self, policy: BudgetPolicy, state: BudgetState):
        self.p = policy
        self.s = state

    def _turn_remaining(self) -> int:
        cap = self.p.turn_total_cap
        return max(0, cap - self.s.turn_tokens_spent_on_search)

    def _is_early(self) -> bool:
        return self.s.msg_idx <= self.p.early_msgs

    def allowed_for_this_search(self) -> int:
        if self._is_early():
            remaining = max(0, self.p.early_total_tokens - self.s.turn_tokens_spent_on_search)
            return min(remaining, self.p.hard_snippet_cap_tokens)
        # after early stage: per-search cap but also respect turn total
        return min(self.p.per_search_cap_after_early, self._turn_remaining())

    def reserve(self, tokens: int) -> int:
        """Record the tokens we’re about to inject for search (post-trim)."""
        tokens = max(0, tokens)
        self.s.turn_tokens_spent_on_search += tokens
        return tokens


# --------------------------
# Utilities & data structs
# --------------------------
@dataclass
class Hit:
    engine: str
    score: float
    file: Optional[str] = None
    when: Optional[str] = None
    snippet: Optional[str] = None
    subject: Optional[str] = None
    predicate: Optional[str] = None
    value: Optional[str] = None
    provenance: Optional[List[str]] = None
    meta: Optional[Dict[str, Any]] = None

@dataclass
class Result:
    hits: List[Hit]
    winner: Optional[Hit]
    confidence: float
    escalation_path: List[str]
    telemetry: Dict[str, Any]


def _now_ms() -> int:
    return int(time.time() * 1000)

def _mean(xs: List[float]) -> float:
    return sum(xs) / max(1, len(xs))

def _is_tokenish(q: str) -> bool:
    return len(q) <= 40 and bool(re.fullmatch(r"[A-Za-z0-9:_\-\s]+", q or ""))

def _est_tokens(text: str) -> int:
    # rough 4 chars/token heuristic
    return ceil(len(text) / 4)

def _find_windows(text: str, pattern: re.Pattern, window_chars: int = 600) -> List[str]:
    out: List[str] = []
    for m in pattern.finditer(text):
        start = max(0, m.start() - window_chars // 2)
        end = min(len(text), m.end() + window_chars // 2)
        out.append(text[start:end])
        if len(out) >= 2:
            break
    return out or [text[:window_chars]]

def _tight_snippet(text: str, query: str, target_tokens: int, hard_cap_tokens: int) -> str:
    if not text:
        return text
    # Prefer a window around the query if token-ish, else simple head trim
    head_chars = hard_cap_tokens * 4
    target_chars = target_tokens * 4
    try:
        rx = re.compile(query, flags=re.IGNORECASE)
        windows = _find_windows(text, rx, window_chars=min(800, head_chars))
        candidate = windows[0]
    except re.error:
        candidate = text

    candidate = candidate[: min(len(candidate), head_chars)]
    # If still bigger than target, shrink further
    if _est_tokens(candidate) > target_tokens:
        candidate = candidate[:target_chars]
    return candidate

def _gael_memory_recall(snippet: str, score: float | None) -> str:
    """Agent-facing GAEL block: Memory Recall + system-computed relevance."""
    try:
        rel = f"{float(score):.2f}" if score is not None else "n/a"
    except Exception:
        rel = "n/a"
    return (
        "[Memory Recall]\n"
        f"Relevance (system-computed): {rel}\n"
        "Content:\n"
        f"{snippet}"
    )

def _score_contains(hay: str, needle: str) -> float:
    try:
        H = hay.lower()
        toks = [t for t in needle.lower().split() if len(t) >= 3]
        if not toks:
            return 0.0
        hits = sum(1 for t in toks if t in H)
        if hits == 0:
            return 0.0
        frac = hits / len(toks)
        return 0.5 + 0.5 * frac  # 0.50..1.00
    except Exception:
        return 0.0

def _shell(cmd: str, timeout: int = 20) -> Tuple[int, str, str]:
    proc = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return 124, "", "timeout"
    return proc.returncode, out or "", err or ""

def _normalize_recency(engine_tag: str, rr: Dict[str, Any]) -> List[Hit]:
    out: List[Hit] = []
    for r in rr.get("results", []):
        score = float(r.get("score", 0.0))
        out.append(Hit(
            engine=engine_tag,
            score=score,
            file=r.get("file"),
            snippet=r.get("snippet"),
            when=None,
            meta={"source": rr.get("meta", {})}
        ))
    return out

def _try_rvs_claims(q: str, agents: List[str]) -> List[Hit]:
    hits: List[Hit] = []
    cmd = (
        f"python3 -m abilities.common_abilities.reverse_vector_search "
        f"--value {shlex.quote(q)} "
        f"--where db --source claims "
        f"--agents {shlex.quote(','.join(agents))} "
        f"--limit 10"
    )
    code, out, err = _shell(cmd)
    if code != 0 or "No occurrences found." in out:
        return hits
    hits.append(Hit(engine="rvs_claims", score=0.84, snippet=out.strip()[:1200], meta={"raw": out[-4000:]}))
    return hits

def _try_rvs_text(q: str, agents: List[str], hours: int = 720) -> List[Hit]:
    hits: List[Hit] = []
    cmd = (
        f"python3 -m abilities.common_abilities.reverse_vector_search "
        f"--value {shlex.quote(q)} "
        f"--where file --source text "
        f"--agents {shlex.quote(','.join(agents))} "
        f"--hours {hours} --limit 10"
    )
    code, out, err = _shell(cmd)
    if code != 0 or "No occurrences found." in out:
        return hits
    hits.append(Hit(engine="rvs_text", score=0.78, snippet=out.strip()[:1200], meta={"raw": out[-4000:]}))
    return hits

def _try_vector(q: str, agents: List[str]) -> List[Hit]:
    try:
        from abilities.common_abilities import vector_search as VS
        res = VS.vector_search(q, agents=agents, top_k=10)  # adapt to your API
        hits: List[Hit] = []
        for item in res.get("results", []):
            hits.append(Hit(engine="vector", score=float(item.get("score", 0.0)),
                            file=item.get("file"), snippet=item.get("snippet")))
        return hits
    except Exception:
        pass
    code, out, err = _shell(f"python3 -m abilities.common_abilities.vector_search --query {shlex.quote(q)} --top-k 10")
    if code == 0 and out:
        s = _score_contains(out, q)
        return [Hit(engine="vector", score=max(0.72, s), snippet=out[:1200], meta={"raw": out[-4000:]})]
    return []

def _temporal_rollup_search(q: str, agents: List[str], hours: int = 720) -> List[Hit]:
    patterns: List[str] = []
    for a in agents:
        patterns += [
            f"memory/{a}/**/*weekly*_journal*.json",
            f"memory/{a}/**/*period*_journal*.json",
            f"memory/{a}/**/*quarterly*_journal*.json",
        ]
    patterns += [
        "memory/**/weekly*_journal*.json",
        "memory/**/period*_journal*.json",
        "memory/**/quarterly*_journal*.json",
    ]

    regex = None
    tokenish = _is_tokenish(q)
    if tokenish:
        try:
            regex = re.compile(q, flags=re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(q), flags=re.IGNORECASE)

    seen: set[str] = set()
    hits: List[Hit] = []
    for pat in patterns:
        for p in sorted(Path(".").glob(pat), reverse=True):
            sp = str(p)
            if sp in seen:
                continue
            seen.add(sp)
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue

            ts = data.get("_meta", {}).get("timestamp") or data.get("date") or data.get("time_end") or data.get("time_start")
            if not is_recent(ts, hours):
                continue

            blob_parts: List[str] = []
            for k in ("summary", "thoughts", "decisions", "written_by", "proposed_by", "agent", "thread_type"):
                v = data.get(k)
                if v is None: continue
                blob_parts.append(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
            blob = "\n".join(blob_parts)
            if not blob:
                continue

            if regex:
                ok = bool(regex.search(blob))
                score = 1.0 if ok else 0.0
            else:
                score = _score_contains(blob, q)

            if score > 0:
                hits.append(Hit(engine="temporal", score=float(f"{score:.4f}"), file=sp, snippet=blob[:1200]))
                if len(hits) >= 10:
                    return hits
    return hits

def _decide(hits: List[Hit], steps: List[str]) -> Result:
    if not hits:
        return Result(hits=[], winner=None, confidence=0.0, escalation_path=steps, telemetry={})
    best = max(hits, key=lambda h: h.score)
    conf = min(1.0, max(0.55, best.score))
    return Result(hits=hits, winner=best, confidence=conf, escalation_path=steps, telemetry={})

def _finalize_and_log(query, agents, steps, res, t0_ms, budget_used):
    # attach telemetry
    res.telemetry = {
        "ms": _now_ms() - t0_ms,
        "engines_used": steps,
        "budget_used_tokens": budget_used,
    }
    # persist a compact record
    write_run({
        "query": query,
        "agents": agents,
        "escalation_path": steps,
        "confidence": float(res.confidence or 0.0),
        "winner_engine": getattr(res.winner, "engine", None) if res.winner else None,
        "winner_file": getattr(res.winner, "file", None) if res.winner else None,
        "budget_used_tokens": budget_used,
        "elapsed_ms": res.telemetry["ms"],
    })
    # log retrieval usage for transparency
    if res and res.winner and res.winner.snippet:
        tok_count = _est_tokens(res.winner.snippet)
        provenance = []
        if res.winner.file:
            provenance.append(res.winner.file)
        log_retrieval_usage(getattr(res.winner, "engine", "unknown"),
                            tok_count, provenance=provenance)
    return res

# --------------------------
# Public: controller
# --------------------------
def search_controller(
    query: str,
    agents: Optional[List[str]] = None,
    *,
    msg_idx: int = 1,
    turn_tokens_used: int = 0,
    policy: Optional[BudgetPolicy] = None,
) -> Result:


    """
    Run the escalation funnel with budget controls and return a normalized Result.

    Budgeting logic limits only the *injected snippet size* (search itself is cheap).
    The returned 'winner' will have its snippet trimmed to fit the budget allowance.
    """
    if not agents:
        agents = ["davinci", "hermes"]
    policy = policy or BudgetPolicy()
    budget = BudgetManager(policy, BudgetState(msg_idx=msg_idx, turn_tokens_used=turn_tokens_used))

    # Kill switch: provenance-only, no search
    if os.getenv("XI_DISABLE_SEARCH") == "1":
        steps = ["disabled"]
        res = _decide([], steps)
        return _finalize_and_log(query, agents, steps, res, _now_ms(), 0)

    _cache_key = (tuple(agents), query.strip())
    if not os.getenv("XI_DISABLE_SEARCH_CACHE"):
        # simple one-item cache; expand if you like
        if not hasattr(search_controller, "_cache"):
            search_controller._cache = {}
        if _cache_key in search_controller._cache:
            cached = search_controller._cache[_cache_key]
            return _finalize_and_log(query, agents, cached["steps"], cached["res"], _now_ms(), 0)

    steps: List[str] = []
    all_hits: List[Hit] = []
    t0 = _now_ms()

    # 1) Hot recency (cheap)
    rr1 = RS.recency_scoped_search(
        query,
        agents=agents,
        hours=24,
        max_files_per_agent=12,
        limit=5,
        fields=["summary", "thoughts", "decisions"],
        use_regex=False,
        strict_recency=False,
        glob_mode="wide",
    )
    # mark phase for logging
    if "meta" in rr1:
        rr1["meta"]["phase"] = "precheck"
    steps.append("recency_hot")
    hits1 = _normalize_recency("recency", rr1)
    all_hits += hits1
    if rr1.get("confidence", 0) >= 0.70 and hits1:
        res = _decide(hits1, steps)
        # budget-trim the winning snippet
        budget_used = 0
        if res.winner and res.winner.snippet:
            allow = budget.allowed_for_this_search()
            trimmed = _tight_snippet(
                res.winner.snippet, query,
                target_tokens=policy.default_snippet_target_tokens,
                hard_cap_tokens=allow if allow > 0 else policy.default_snippet_target_tokens,
            )
            budget_used = budget.reserve(_est_tokens(trimmed))
            res.winner.snippet = _gael_memory_recall(trimmed, getattr(res.winner, "score", None))
        # (after you compute trimmed snippet & budget_used)
        final = _finalize_and_log(query, agents, steps, res, t0, budget_used)
        if not os.getenv("XI_DISABLE_SEARCH_CACHE"):
            search_controller._cache[_cache_key] = {"res": final, "steps": steps}
        return final

    # 2) Broad recency
    rr2 = RS.recency_scoped_search(
        query,
        agents=agents,
        hours=168,
        max_files_per_agent=24,
        limit=8,
        fields=["summary", "thoughts", "decisions", "gael", "notes", "body", "written_by", "proposed_by", "agent", "thread_type"],
        use_regex=_is_tokenish(query),
        strict_recency=False,
        glob_mode="all",
    )
    # mark phase for logging
    if "meta" in rr2:
        rr2["meta"]["phase"] = "final"
    steps.append("recency_broad")
    hits2 = _normalize_recency("recency", rr2)
    all_hits += hits2
    pass_broad = (rr2.get("confidence", 0) >= 0.70 and hits2) or (_mean([h.score for h in hits2]) >= 0.6 and len(hits2) >= 3)
    if pass_broad:
        res = _decide(all_hits, steps)
        # budget-trim the winning snippet
        budget_used = 0
        if res.winner and res.winner.snippet:
            allow = budget.allowed_for_this_search()
            trimmed = _tight_snippet(
                res.winner.snippet, query,
                target_tokens=policy.default_snippet_target_tokens,
                hard_cap_tokens=allow if allow > 0 else policy.default_snippet_target_tokens,
            )
            budget_used = budget.reserve(_est_tokens(trimmed))
            res.winner.snippet = _gael_memory_recall(trimmed, getattr(res.winner, "score", None))
        # (after you compute trimmed snippet & budget_used)
        final = _finalize_and_log(query, agents, steps, res, t0, budget_used)
        if not os.getenv("XI_DISABLE_SEARCH_CACHE"):
            search_controller._cache[_cache_key] = {"res": final, "steps": steps}
        return final

    # 3) RVS claims
    hits3 = _try_rvs_claims(query, agents)
    if hits3:
        steps.append("rvs_claims")
        all_hits += hits3
        res = _decide(all_hits, steps)
        # budget-trim the winning snippet
        budget_used = 0
        if res.winner and res.winner.snippet:
            allow = budget.allowed_for_this_search()
            trimmed = _tight_snippet(
                res.winner.snippet, query,
                target_tokens=policy.default_snippet_target_tokens,
                hard_cap_tokens=allow if allow > 0 else policy.default_snippet_target_tokens,
            )
            budget_used = budget.reserve(_est_tokens(trimmed))
            res.winner.snippet = _gael_memory_recall(trimmed, getattr(res.winner, "score", None))
        # (after you compute trimmed snippet & budget_used)
        final = _finalize_and_log(query, agents, steps, res, t0, budget_used)
        if not os.getenv("XI_DISABLE_SEARCH_CACHE"):
            search_controller._cache[_cache_key] = {"res": final, "steps": steps}
        return final

    # 4) RVS text
    hits4 = _try_rvs_text(query, agents, hours=720)
    if hits4:
        steps.append("rvs_text")
        all_hits += hits4
        res = _decide(all_hits, steps)
        # budget-trim the winning snippet
        budget_used = 0
        if res.winner and res.winner.snippet:
            allow = budget.allowed_for_this_search()
            trimmed = _tight_snippet(
                res.winner.snippet, query,
                target_tokens=policy.default_snippet_target_tokens,
                hard_cap_tokens=allow if allow > 0 else policy.default_snippet_target_tokens,
            )
            budget_used = budget.reserve(_est_tokens(trimmed))
            res.winner.snippet = _gael_memory_recall(trimmed, getattr(res.winner, "score", None))
        # (after you compute trimmed snippet & budget_used)
        final = _finalize_and_log(query, agents, steps, res, t0, budget_used)
        if not os.getenv("XI_DISABLE_SEARCH_CACHE"):
            search_controller._cache[_cache_key] = {"res": final, "steps": steps}
        return final

    # 5) Vector (optional)
    hits5 = _try_vector(query, agents)
    if hits5:
        steps.append("vector")
        all_hits += hits5
        res = _decide(all_hits, steps)
        # budget-trim the winning snippet
        budget_used = 0
        if res.winner and res.winner.snippet:
            allow = budget.allowed_for_this_search()
            trimmed = _tight_snippet(
                res.winner.snippet, query,
                target_tokens=policy.default_snippet_target_tokens,
                hard_cap_tokens=allow if allow > 0 else policy.default_snippet_target_tokens,
            )
            budget_used = budget.reserve(_est_tokens(trimmed))
            res.winner.snippet = _gael_memory_recall(trimmed, getattr(res.winner, "score", None))
        # (after you compute trimmed snippet & budget_used)
        final = _finalize_and_log(query, agents, steps, res, t0, budget_used)
        if not os.getenv("XI_DISABLE_SEARCH_CACHE"):
            search_controller._cache[_cache_key] = {"res": final, "steps": steps}
        return final

    # 6) Temporal rollups
    hits6 = _temporal_rollup_search(query, agents, hours=720)
    if hits6:
        steps.append("temporal")
        all_hits += hits6
        res = _decide(all_hits, steps)
        # budget-trim the winning snippet
        budget_used = 0
        if res.winner and res.winner.snippet:
            allow = budget.allowed_for_this_search()
            trimmed = _tight_snippet(
                res.winner.snippet, query,
                target_tokens=policy.default_snippet_target_tokens,
                hard_cap_tokens=allow if allow > 0 else policy.default_snippet_target_tokens,
            )
            budget_used = budget.reserve(_est_tokens(trimmed))
            res.winner.snippet = _gael_memory_recall(trimmed, getattr(res.winner, "score", None))
        # (after you compute trimmed snippet & budget_used)
        final = _finalize_and_log(query, agents, steps, res, t0, budget_used)
        if not os.getenv("XI_DISABLE_SEARCH_CACHE"):
            search_controller._cache[_cache_key] = {"res": final, "steps": steps}
        return final

    # No joy
    steps.append("none")
    res = _decide(all_hits, steps)
    budget_used = budget.s.turn_tokens_spent_on_search
    final = _finalize_and_log(query, agents, steps, res, t0, budget_used)
    if not os.getenv("XI_DISABLE_SEARCH_CACHE"):
        search_controller._cache[_cache_key] = {"res": final, "steps": steps}
    return final

# --------------------------
# CLI wrapper (dev aid)
# --------------------------
def _print_result(res: Result):
    payload = {
        "hits": [asdict(h) for h in res.hits[:10]],
        "winner": asdict(res.winner) if res.winner else None,
        "confidence": res.confidence,
        "escalation_path": res.escalation_path,
        "telemetry": res.telemetry,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--agents", default="davinci,hermes")
    ap.add_argument("--msg-idx", type=int, default=1, help="1-based message index in current turn")
    ap.add_argument("--turn-tokens-used", type=int, default=0, help="tokens already used this turn before search")
    args = ap.parse_args()
    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    res = search_controller(args.query, agents=agents, msg_idx=args.msg_idx, turn_tokens_used=args.turn_tokens_used)
    _print_result(res)

if __name__ == "__main__":
    main()

