#!/usr/bin/env python3
"""
🔍 memory_search.py
Semantic search over XI memory using pgvector.
Supports filters (agent, thread_type, period, date ranges).
"""

### Retrieval Sticky — Stage-Aware + Reverse
#1) Stage budgets: Early ≤2/400t, Mid ≤5/900t, End ≤3/600t.
#2) Score = fwd_cos + rev_cos + tone + recency − penalty(bad).
#3) Reverse: Journals/rollups → threads (tier weight flip for big-picture).
#4)Need-to-pull: Early low, Mid med/conflict, End gap/drift.
#5)Serendipity: 1–2 high reverse hits (reflect/explore only).
#6)Cooldowns: Don’t reinject same memory unless plan changes.
#7) Gates: Skip invalid/fallback unless explicit; if all bad → mini-journal note.
#8) GAEL: Name the gap before pull; cite how each memory changes next step.
#9) Log: [Memory Selected] in flash; provenance in output.
#10) Goal: Context earned, not bloated; large models stay situationally aware.

print("✅ memory_search.py loaded")
import os
# === 📴 Force offline mode for transformers ===
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
import psycopg2
from dotenv import load_dotenv
from pathlib import Path
import json
from typing import List, Dict
import torch
from transformers import AutoTokenizer, AutoModel
import numpy as np

# ➕ Token logging
from abilities.common_abilities.token_logger import log_retrieval_usage

# === 🧹 Suppress HuggingFace log spam ===
from transformers import logging as hf_logging
hf_logging.set_verbosity_error()

# === 🌱 Config ===
load_dotenv()

def _env(*names, default=None):
    for n in names:
        v = os.getenv(n)
        if v not in (None, ""):
            return v
    return default

DB_CONFIG = {
    # prefer XI_DB_* then POSTGRES_* then PG* (aligns with indexer)
    "dbname": _env("XI_DB_NAME","POSTGRES_DB","PGDATABASE","DB_NAME", default="xi_memory"),
    "user":   _env("XI_DB_USER","POSTGRES_USER","PGUSER","DB_USER",   default="xi_user"),
    "password": _env("XI_DB_PASSWORD","POSTGRES_PASSWORD","PGPASSWORD","DB_PASSWORD"),
    "host":   _env("XI_DB_HOST","POSTGRES_HOST","PGHOST","DB_HOST",   default="localhost"),
    "port":   _env("XI_DB_PORT","POSTGRES_PORT","PGPORT","DB_PORT",   default="5432"),
}
EMBEDDING_DIM = 1024  # Make sure this matches your Postgres VECTOR(1024)
DEFAULT_TOP_N = 5
XI_DISABLE_SEARCH = os.getenv("XI_DISABLE_SEARCH") == "1"

# Resolve embedder source: local dir OR HF repo id.
# - If XI_EMBEDDER_MODEL_PATH points to a directory → use local_files_only=True
# - Else treat it as an HF repo id (online allowed unless env forces offline)
EMBEDDER_SOURCE = os.getenv("XI_EMBEDDER_MODEL_PATH") or "BAAI/bge-large-en-v1.5"
_is_local_dir = os.path.isdir(EMBEDDER_SOURCE)

# === 🧬 Load offline embedder ===
print("🧬 Loading embedder...")

try:
    load_path = str(Path(EMBEDDER_SOURCE).resolve()) if _is_local_dir else EMBEDDER_SOURCE
    tokenizer = AutoTokenizer.from_pretrained(
        load_path,
        trust_remote_code=True,
        local_files_only=_is_local_dir,
        use_fast=False  # ✅ Prevents fast tokenizer requirement for local-only
    )
    model = AutoModel.from_pretrained(
        load_path,
        trust_remote_code=True,
        local_files_only=_is_local_dir
    )
    model.eval()
    print(f"✅ Embedder ready. source={'local' if _is_local_dir else 'hf'} -> {load_path}")
except Exception as e:
    raise RuntimeError(f"Failed loading embedder from {EMBEDDER_SOURCE} "
                       f"({'local dir' if _is_local_dir else 'hf repo id'}): {e}")

# 🔬 Confirm output dimension
with torch.no_grad():
    dummy = tokenizer("test", return_tensors="pt")
    out = model(**dummy)
    print(f"🧪 Embedder output dim: {out.last_hidden_state.shape[-1]}")  # Should be 1024

# === 🧠 Embedding Function ===
def get_embedding(text: str) -> list:
    if XI_DISABLE_SEARCH:
        # keep DB queries valid but avoid model calls
        return [0.0] * EMBEDDING_DIM
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        outputs = model(**inputs)
        last_hidden = outputs.last_hidden_state  # (1, seq_len, hidden_dim)
        pooled = last_hidden.mean(dim=1).squeeze(0)  # (hidden_dim,)
    return pooled.tolist()

# === 🔍 Filterable Search (Safe + Ordered) ===
def search_memory_entries(query, top_n=DEFAULT_TOP_N, filters=None):
    query_embedding = get_embedding(query)

    # 🛡️ Sanity checks
    if not isinstance(query_embedding, list) or not all(isinstance(x, (float, int)) for x in query_embedding):
        raise ValueError(f"❌ Invalid query_embedding: {query_embedding}")

    # 📦 Format embedding as a Postgres vector literal
    vector_literal = "[" + ",".join(f"{x:.6f}" for x in query_embedding) + "]"

    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # 🧰 Handle filters
    where_clauses = []
    filter_params = []

    if filters:
        if "agent" in filters:
            where_clauses.append("agent = %s")
            filter_params.append(filters["agent"])
        if "thread_type" in filters:
            where_clauses.append("thread_type = %s")
            filter_params.append(filters["thread_type"])
        if "period" in filters:
            where_clauses.append("period = %s")
            filter_params.append(filters["period"])
        if "start_date" in filters:
            where_clauses.append("timestamp >= %s")
            filter_params.append(filters["start_date"])
        if "end_date" in filters:
            where_clauses.append("timestamp <= %s")
            filter_params.append(filters["end_date"])

    where_clause = " AND ".join(where_clauses)
    if where_clause:
        where_clause = "WHERE " + where_clause

    query_sql = f"""
        SELECT agent, timestamp, content, source_file, embedding <-> %s::vector AS distance, uuid
        FROM memory_entries
        {where_clause}
        ORDER BY distance
        LIMIT %s;
    """

    # ✅ Correct order: embedding, filters..., top_n
    params = [vector_literal] + filter_params + [top_n]

    #print(f"🧪 Params: {params}")
    print(f"🔍 Executing SQL with filters: {filters or 'none'} | top_n={top_n}")

    cursor.execute(query_sql, params)
    results = cursor.fetchall()
    cursor.close()
    conn.close()

    out = []
    for row in results:
        # distance is smaller=better; map to a simple 0..1 relevance
        try:
            dist = float(row[4])
            rel = max(0.0, min(1.0, 1.0 - dist))
        except Exception:
            rel = None
        out.append({
            "agent": row[0],
            "timestamp": row[1],
            "content": row[2],
            "source_file": row[3],
            "distance": row[4],
            "uuid": row[5],
            # Agent-facing GAEL block for direct consumers
            "display": (
                "[Memory Recall]\n"
                f"Relevance (system-computed): {f'{rel:.2f}' if rel is not None else 'n/a'}\n"
                "Content:\n"
                f"{(row[2] or '')}"
            )
        })
    # log retrieval usage
    token_count = sum(len((r["content"] or "").split()) for r in out)
    provenance = [r.get("uuid") for r in out]
    log_retrieval_usage("vector", token_count, provenance=provenance)
    return out

# === 🎯 Simple Agent Wrapper ===
def search_memories(query_text: str, agent: str, top_k: int = DEFAULT_TOP_N) -> List[Dict]:
    """Shortcut wrapper to search by agent name only."""
    return search_memory_entries(
        query=query_text,
        top_n=top_k,
        filters={"agent": agent}
    )

# === 🧩 Canonical Query Function ===
def query_memory_vector(query: str, top_n: int = DEFAULT_TOP_N, filters: dict = None) -> dict:
    """Main entry point used by validators and external tools."""
    matches = search_memory_entries(query, top_n=top_n, filters=filters)
    return {"query": query, "results": matches}


__all__ = ["query_memory_vector"]


