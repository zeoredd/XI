#!/usr/bin/env python3
"""
recency_search.py
Fast, cheap search over very-recent session journals (per agent).
Used in the flow hot path when normal vector confidence is low or user asks.

Upgrades:
- --fields (comma list or 'all'): summary,thoughts,decisions[,gael,notes,body]
- --strict-recency: require journal timestamp within --hours; skip if missing/bad
- --regex: treat --query as regex; if it matches, score=1.0 (else 0.0)
"""

from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any, Optional
import json, time, re
import datetime as dt
from abilities.common_abilities.time_utils import parse_iso, is_recent

# ➕ Token logging
from abilities.common_abilities.token_logger import log_retrieval_usage

ROOT = Path(__file__).resolve().parents[2]  # /XI

DEFAULT_FIELDS = ["summary", "thoughts", "decisions"]
EXTRA_FIELDS = ["gael", "notes", "body"]

# --- Globs (single, consolidated block) ---
STRICT_PATTERNS = [
    "memory/{agent}/*_journal/session/{agent}_session_journal_*.json",
    "memory/{agent}/*_journal/weekly/{agent}_weekly_journal_*.json",
    "memory/{agent}/*_journal/period/{agent}_period_journal_*.json",
    "memory/{agent}/*_journal/quarterly/{agent}_quarterly_journal_*.json",
]

WIDE_PATTERNS = [
    "memory/{agent}/*_journal/session/*.json",    # any agent journal/session file
    "memory/{agent}/*_journal/weekly/*.json",     # include weekly summaries
    "memory/{agent}/*_journal/period/*.json",     # include period rollups
    "memory/{agent}/*_journal/quarterly/*.json",  # include quarterly rollups
]

ALL_PATTERNS = WIDE_PATTERNS + [
    "memory/threads/**/session/*.json",
    "memory/sentinel/**/session/*.json",
    "memory/**/audit*/session/*.json",
    "memory/amendments/**/*.json",
    "memory/{agent}/*_journal/weekly/*.json",
    "memory/{agent}/*_journal/period/*.json",
    "memory/{agent}/*_journal/quarterly/*.json",
]

def _filter_patterns_by_layers(patterns: list[str], layers: list[str]) -> list[str]:
    """Keep only patterns that match the requested layers (session/weekly/period/quarterly)."""
    if not layers:
        return patterns
    selected: list[str] = []
    for p in patterns:
        for layer in layers:
            if f"/{layer}/" in p or f"_{layer}_" in p:
                selected.append(p)
                break
    return selected


def _glob_many(patterns: list[str], agent: str) -> list[Path]:
    picks = []
    for pat in patterns:
        pat = pat.format(agent=agent)
        picks.extend(sorted(Path(".").glob(pat), reverse=True))
    return picks


def _recent_session_files(agent: str, max_files: int = 3, glob_mode: str = "strict", layers: Optional[list[str]] = None) -> list[Path]:
    """
    glob_mode:
      - strict: only {agent}_session_journal_*.json under that agent
      - wide:   any *_journal/(session|weekly|period|quarterly)/*.json for that agent
      - all:    wide + sentinel/audit/amendments + threads
    """
    if glob_mode == "strict":
        pats = STRICT_PATTERNS
    elif glob_mode == "wide":
        pats = WIDE_PATTERNS
    else:  # "all"
        pats = ALL_PATTERNS

    pats = _filter_patterns_by_layers(pats, layers or [])
    candidates = _glob_many(pats, agent)

    # De-dupe and sort by mtime desc
    seen = set()
    uniq: list[Path] = []
    for p in candidates:
        s = str(p)
        if s not in seen:
            seen.add(s)
            uniq.append(p)
    uniq.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

    return uniq[:max_files]

def _score_contains(hay: str, needle: str) -> float:
    try:
        H = hay.lower()
        groups = [g.strip() for g in needle.lower().split("|") if g.strip()]
        if not groups:
            return 0.0
        best = 0.0
        for grp in groups:
            toks = [t for t in grp.split() if len(t) >= 3]
            if not toks: 
                continue
            hits = sum(1 for t in toks if t in H)
            if hits == 0:
                continue
            frac = hits / len(toks)
            best = max(best, 0.5 + 0.5 * frac)
        return best
    except Exception:
        return 0.0

# add near DEFAULT_FIELDS / EXTRA_FIELDS
META_FIELDS = ["written_by", "proposed_by", "agent", "thread_type"]

def _resolve_fields(arg: Optional[str]) -> List[str]:
    if not arg or arg.strip().lower() in ("", "default"):
        return list(DEFAULT_FIELDS)
    a = arg.strip().lower()
    if a == "all":
        return DEFAULT_FIELDS + EXTRA_FIELDS + META_FIELDS
    chosen = [f.strip() for f in arg.split(",") if f.strip()]
    ordered = []
    for f in DEFAULT_FIELDS + EXTRA_FIELDS + META_FIELDS:
        if f in chosen and f not in ordered:
            ordered.append(f)
    for f in chosen:
        if f not in ordered:
            ordered.append(f)
    return ordered


def _extract_blob(data: Dict[str, Any], fields: List[str]) -> str:
    chunks: List[str] = []
    for key in fields:
        if key in data and data[key] is not None:
            v = data[key]
            if isinstance(v, str):
                chunks.append(v)
            else:
                try:
                    chunks.append(json.dumps(v, ensure_ascii=False))
                except Exception:
                    chunks.append(str(v))
    return "\n".join(chunks)

def _strict_recent_ok(data: Dict[str, Any], hours: int) -> bool:
    """
    Require a usable timestamp field and verify it's within the window.
    Checks _meta.timestamp (ISO), then date (YYYY-MM-DD), then time_end/time_start.
    """
    ts = None
    # 1) _meta.timestamp (ISO-ish)
    meta = data.get("_meta", {}) or {}
    ts = meta.get("timestamp")
    if ts and is_recent(ts, hours):
        return True
    # 2) date (YYYY-MM-DD)
    date_str = data.get("date")
    if date_str and is_recent(date_str, hours):
        return True
    # 3) time_end / time_start
    for k in ("time_end", "time_start"):
        val = data.get(k)
        if val and is_recent(val, hours):
            return True
    # 4) created_at_utc (legacy/meta)
    ts2 = meta.get("created_at_utc")
    return bool(ts2 and is_recent(ts2, hours))

def recency_scoped_search(
    query: str,
    agents: Optional[List[str]] = None,
    hours: int = 6,
    max_files_per_agent: int = 3,
    limit: int = 5,
    fields: Optional[List[str]] = None,
    use_regex: bool = False,
    strict_recency: bool = False,
    glob_mode: str = "strict",
    layers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Returns RetrievalResult-like dict:
    {
      "results": [{"agent","file","score","snippet"}...],
      "confidence": float,
      "freshness": float,
      "coverage": float,
      "cost_est": float,
      "meta": {"source":"recency","hours":int,"fields":[...],"strict":bool,"regex":bool}
    }
    """
    results: List[Dict[str, Any]] = []
    fields = fields or DEFAULT_FIELDS
    if not agents:
        agents = ["davinci", "hermes"]

    # Optional regex compile
    regex = None
    if use_regex:
        try:
            regex = re.compile(query, flags=re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(query), flags=re.IGNORECASE)

    for agent in agents:
        for p in _recent_session_files(
            agent,
            max_files=max_files_per_agent,
            glob_mode=glob_mode,
            layers=layers,
        ):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue

            # Non-strict behavior: accept if *any* usable timestamp is within hours
            meta = data.get("_meta", {}) or {}
            ts = meta.get("timestamp") or data.get("date") or meta.get("created_at_utc")
            if not ts:
                # last-resort: use file mtime (keeps old files discoverable)
                try:
                    mtime = dt.datetime.utcfromtimestamp(p.stat().st_mtime)
                    ts = mtime.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    ts = None
            if not is_recent(ts, hours):
                continue

            # Strict behavior (new): require *some* valid timestamp field; skip if none fits window
            if strict_recency and not _strict_recent_ok(data, hours):
                continue

            blob = _extract_blob(data, fields)
            if not blob:
                continue

            if regex:
                score = 1.0 if regex.search(blob) else 0.0
            else:
                score = _score_contains(blob, query)

            if score > 0:
                # Per-file snippet cap (150 tokens)
                snippet_tokens = (blob or "").split()
                if len(snippet_tokens) > 150:
                    snippet_tokens = snippet_tokens[:150]
                snippet = " ".join(snippet_tokens)
                results.append({
                    "agent": agent,
                    "file": str(p),
                    "score": float(f"{score:.4f}"),
                    "snippet": snippet,
                    # Agent-facing GAEL block
                    "display": (
                        "[Memory Recall]\n"
                        f"Relevance (system-computed): {float(score):.2f}\n"
                        "Content:\n"
                        f"{snippet}"
                    ),
                })
                if len(results) >= limit:
                    break

    coverage = min(1.0, len(results) / max(1, limit))
    confidence = 0.70 if results else 0.0   # tuned low; fallback
    cost_est = 0.12
    # === 🛑 Enforce total token cap (≤400) across all results ===
    total_tokens = 0
    final_results = []
    for r in results:
        tokens_here = len((r.get("snippet") or "").split())
        remaining = 400 - total_tokens
        if remaining <= 0:
            break
        if tokens_here > remaining:
            snippet_tokens = (r["snippet"] or "").split()[:remaining]
            r["snippet"] = " ".join(snippet_tokens)
            tokens_here = len(snippet_tokens)
        final_results.append(r)
        total_tokens += tokens_here

    out = {
        "results": final_results,
        "confidence": confidence,
        "freshness": 0.95,
        "coverage": coverage,
        "cost_est": cost_est,
        "meta": {
            "source": "recency",
            "hours": hours,
            "fields": fields,
            "strict": strict_recency,
            "regex": use_regex,
            "glob_mode": glob_mode,
            "layers": layers or [],
            "token_cap": 400,
        },
    }
    # log retrieval usage
    provenance = [r.get("file") for r in final_results if r.get("file")]
    # Allow caller to pass a phase tag via meta
    phase = (out.get("meta") or {}).get("phase")
    log_retrieval_usage("recency", total_tokens, provenance=provenance,
                        mode=phase if phase else None)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True, help='Search text or regex (use --regex to enable regex mode)')
    ap.add_argument("--agents", help="Comma-separated, e.g. davinci,hermes")
    ap.add_argument("--hours", type=int, default=6)
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--glob-mode", choices=["strict","wide","all"], default="strict",
                help="Where to look: strict (agent-only), wide (all agent journals), all (wide + threads/sentinel/audit/amendments)")
    ap.add_argument("--max-files", type=int, default=6, help="Recent session files per agent to scan")
    ap.add_argument("--fields", default="summary,thoughts,decisions",
                    help="Comma list or 'all' to include gael,notes,body")
    ap.add_argument("--regex", action="store_true", help="Treat --query as regex (case-insensitive)")
    ap.add_argument("--strict-recency", action="store_true", help="Require journal timestamp within --hours")
    ap.add_argument("--include-threads", action="store_true",
                    help="(Deprecated; kept for compatibility) Include memory/threads/**/session/*.json; implied by --glob-mode=all")
    ap.add_argument("--layers", type=str, default="session,weekly",
                    help="Comma-separated list of journal layers (session,weekly,period,quarterly).")

    args = ap.parse_args()

    agents = [a.strip() for a in args.agents.split(",")] if args.agents else None
    fields = _resolve_fields(args.fields)
    layers = [x.strip() for x in args.layers.split(",") if x.strip()]

    rr = recency_scoped_search(
        args.query,
        agents=agents,
        hours=args.hours,
        max_files_per_agent=args.max_files,
        limit=args.limit,
        fields=fields,
        use_regex=args.regex,
        strict_recency=args.strict_recency,
        glob_mode=args.glob_mode,
        layers=layers, 
    )
    if args.debug:
        print(f"(debug) agents={agents} hours={args.hours} limit={args.limit} fields={fields} "
              f"strict={args.strict_recency} regex={args.regex} glob_mode={args.glob_mode} layers={layers}")
        for a in (agents or ["davinci","hermes"]):
            peek = _recent_session_files(a, max_files=args.max_files, glob_mode=args.glob_mode, layers=layers)
            for p in peek[:min(10, len(peek))]:
                print("(debug) scanned:", p)

    # Print concise result count (preserve your current behavior of being quiet unless debug)
    if not rr["results"]:
        print("No recent matches.")
    else:
        print(f"✅ {len(rr['results'])} match(es)")

