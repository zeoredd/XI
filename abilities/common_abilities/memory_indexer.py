#!/usr/bin/env python3
"""
🧠 memory_indexer.py
Indexes all XI memory JSON files into PostgreSQL using pgvector.
Extracts entries, generates 1024-dim local embeddings, and stores with metadata.
Deduplicates using SHA256 hashes.
"""

import os
# === 📴 Force offline mode for transformers ===
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
import argparse
import json
import os
import uuid
import hashlib
import sys
from pathlib import Path
# Ensure project root is importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import torch
import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv
import re, math
from abilities.common_abilities.db_write import upsert_memory, upsert_link

RESET_PHRASES = ("anyway", "switching gears", "new topic", "separately,")

import datetime as _dt

# === Embedder config (Aug10) ===
EMBEDDER_NAME = globals().get("EMBEDDER_NAME", "bge-large-v1.5")
EMBEDDER_VERSION = globals().get("EMBEDDER_VERSION", "1.0")
EMBED_DIM = globals().get("EMBED_DIM", 1024)

XI_DISABLE_SEARCH = os.getenv("XI_DISABLE_SEARCH") == "1"
_EMBED_LOGGED = False

# === Fingerprint helpers (Aug10) ===
import hashlib as _hashlib
import json as _json


# === Text normalization (Aug10) ===

# === Embedding dimension guard (Aug10) ===

# ── Lazy local embedder (air-gapped) ──────────────────────────────────────────
_EMBED = {"tok": None, "mdl": None}

def _ensure_embedder():
    """
    Loads tokenizer/model from disk once. Honors:
      XI_EMBEDDER_MODEL_PATH (preferred, a local directory),
      defaults to /home/node-alpha/XI/embedders/bge-large-v1.5
    """
    if _EMBED["tok"] is not None:
        return _EMBED["tok"], _EMBED["mdl"]
    model_path = os.environ.get("XI_EMBEDDER_MODEL_PATH") or "/home/node-alpha/XI/embedders/bge-large-v1.5"
    # lazy import so --mem-only works even if transformers isn't installed
    from transformers import AutoTokenizer, AutoModel
    tok = AutoTokenizer.from_pretrained(model_path, local_files_only=True, trust_remote_code=True, use_fast=False)
    mdl = AutoModel.from_pretrained(model_path, local_files_only=True, trust_remote_code=True)
    _EMBED["tok"], _EMBED["mdl"] = tok, mdl
    return tok, mdl

def get_embedding(text: str):
    # 👉 Add this block at the very top of the function:
    if XI_DISABLE_SEARCH:
        global _EMBED_LOGGED
        if not _EMBED_LOGGED:
            print("ℹ️ search disabled; skipping embedder init and using zero-vector embeddings")
            _EMBED_LOGGED = True
        return [0.0] * EMBED_DIM  # keep DB inserts happy

    tok, mdl = _ensure_embedder()
    inputs = tok(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        outputs = mdl(**inputs)
    last_hidden = outputs[0]
    return last_hidden.mean(dim=1).squeeze().tolist()

# === Integrity logging (Aug10) ===
def log_integrity_issue(cur, file_path: str, entry_uuid: str | None, issue: str, meta: dict | None = None):
    try:
        cur.execute(
            "INSERT INTO integrity_report(file_path, entry_uuid, issue, meta) VALUES (%s,%s,%s,%s)",
            (file_path, entry_uuid, issue, json.dumps(meta or {})),
        )
    except Exception as e:
        print(f"[indexer] error: {e}")
        # best-effort only
        pass

def assert_embed_dim(vec):
    if len(vec) != EMBED_DIM:
        raise ValueError(f"Embedder dim mismatch: got {len(vec)} expected {EMBED_DIM}")

def _flatten_for_text(obj) -> str:
    import json as __json
    if obj is None:
        return ""
    if isinstance(obj, str):
        try:
            parsed = __json.loads(obj)
            return _flatten_for_text(parsed)
        except Exception:
            return obj
    if isinstance(obj, dict):
        keys = ["summary", "thoughts", "decisions", "content", "notes"]
        ordered = []
        for k in keys:
            if k in obj and obj[k]:
                ordered.append(_flatten_for_text(obj[k]))
        for k, v in obj.items():
            if k not in keys and v:
                ordered.append(_flatten_for_text(v))
        return "\n".join(x for x in ordered if x)
    if isinstance(obj, (list, tuple)):
        return "\n".join(_flatten_for_text(x) for x in obj)
    return str(obj)

def normalize_entry_text_for_index(entry_json: dict) -> str:
    parts = []
    def add(label, value):
        v = _flatten_for_text(value).strip()
        if v:
            parts.append(f"[{label}]: {v}")
    add("Summary", entry_json.get("summary"))
    add("Thoughts", entry_json.get("thoughts"))
    add("Decisions", entry_json.get("decisions"))
    if not parts:
        parts.append(_flatten_for_text(entry_json))
    return "\n".join(parts).strip()

def _sha1_text(txt: str) -> str:
    return _hashlib.sha1(txt.encode("utf-8", errors="ignore")).hexdigest()

def get_embedder_fingerprint() -> str:
    """
    Use a portable, human-readable embedder fingerprint.
    Example: 'bge-large-v1.5-dim1024'
    """
    model_path = os.environ.get("XI_EMBEDDER_MODEL_PATH") or "/home/ghost/XI/embedders/bge-large-v1.5"
    slug = Path(model_path).name
    return f"{slug}-dim{EMBED_DIM}"


def _assert_dim(vec):
    # vec can be list or numpy array/torch tensor; get length appropriately
    try:
        n = len(vec)
    except TypeError:
        # torch tensor case
        try:
            n = vec.numel()
        except Exception:
            raise ValueError("Unsupported embedding type for dim check")
    if n != EMBED_DIM:
        raise ValueError(f"Embedder dim mismatch: got {n} expected {EMBED_DIM}")

def _parse_ts(raw_ts: str) -> _dt.datetime:
    """
    Parse an ISO string (accepts 'Z') to a UTC-aware datetime.
    Falls back to 'now(UTC)' on failure.
    """
    try:
        dt = _dt.datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.astimezone(_dt.timezone.utc)
    except Exception:
        return _dt.datetime.now(_dt.timezone.utc)

def cosine(a, b):
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a)) + 1e-8
    nb = math.sqrt(sum(x*x for x in b)) + 1e-8
    return dot / (na * nb)

def make_chunks_semantic(sentences, embed_fn, *,
                         min_chars=80, max_chars=350, max_sentences=3,
                         sim_threshold=0.35):
    chunks, cur_sents, cur_vec, cur_count, cur_size = [], [], None, 0, 0

    def flush():
        nonlocal cur_sents, cur_vec, cur_count, cur_size
        if not cur_sents:
            return
        chunk_text = "\n".join(cur_sents).strip()
        if chunk_text:
            chunks.append(chunk_text)
        cur_sents, cur_vec, cur_count, cur_size = [], None, 0, 0

    def is_reset(s):
        s0 = s.strip()
        if re.match(r'^#{1,6}\s', s0): return True
        if s0.endswith(":"): return True
        low = s0.lower()
        return any(low.startswith(p) for p in RESET_PHRASES)

    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if is_reset(s):
            flush()
            cur_sents = [s]
            cur_vec = embed_fn(s)
            _assert_dim(cur_vec)
            cur_count, cur_size = 1, len(s)
            continue

        v = embed_fn(s)
        _assert_dim(v)
        if cur_vec is None:
            cur_sents = [s]; cur_vec = v; cur_count, cur_size = 1, len(s); continue

        sim = cosine(v, cur_vec)
        if (cur_size + len(s) > max_chars) or (cur_count + 1 > max_sentences):
            flush()

        if cur_vec is None:
            cur_sents = [s]; cur_vec = v; cur_count, cur_size = 1, len(s)
        elif sim >= sim_threshold or cur_size < min_chars:
            cur_sents.append(s); cur_count += 1; cur_size += len(s)
            cur_vec = [(a*(cur_count-1) + b)/cur_count for a, b in zip(cur_vec, v)]
        else:
            flush()
            cur_sents = [s]; cur_vec = v; cur_count, cur_size = 1, len(s)

        if cur_size >= min_chars and (cur_count >= 1) and (cur_size >= max_chars*0.8 or cur_count >= max_sentences):
            flush()

    flush()
    return chunks

# --- sentence/paragraph splitters for indexing ---
SENT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9“"(\[])')
MAX_SENTENCES_PER_FIELD = 30  # safety cap per field

def _normalize_ws(s: str) -> str:
    return " ".join((s or "").strip().split())

def _split_paragraphs(s: str) -> list[str]:
    raw = (s or "").replace("\r\n", "\n")
    paras = [p.strip() for p in re.split(r'\n\s*\n+', raw) if p.strip()]
    if not paras:
        paras = [p.strip() for p in raw.split("\n") if p.strip()]
    return paras

def _sentences(s: str) -> list[str]:
    s = _normalize_ws(s)
    if not s:
        return []
    parts = SENT_RE.split(s)
    return [p.strip().strip(":;") for p in parts if p and p.strip()]

def yield_record_chunks_from_strings(entry: dict) -> list[str]:
    chunks = []

    def take_sentences(text) -> list[str]:
        text = normalize_text_field(try_parse_embedded_json(text))  # ✅ normalize here
        out = []
        for para in _split_paragraphs(text or ""):
            out.extend(_sentences(para))
            if len(out) >= MAX_SENTENCES_PER_FIELD:
                break
        return out[:MAX_SENTENCES_PER_FIELD]

    had_any = False
    for fld in ("summary", "thoughts", "decisions"):
        if entry.get(fld):
            had_any = True
            chunks.extend(take_sentences(entry[fld]))

    if had_any and chunks:
        return [c for c in chunks if c.strip()]

    for fld in ("content", "output"):
        if entry.get(fld):
            ss = take_sentences(entry.get(fld))
            if ss:
                chunks.extend(ss)
    return [c for c in chunks if c.strip()]

def normalize_text_field(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(x).strip() for x in value if str(x).strip())
    if isinstance(value, dict):
        # choose: serialize or skip; serialize is safer to avoid crashing
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()

def try_parse_embedded_json(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, (dict, list)) else value
        except json.JSONDecodeError:
            return value
    return value

# === 🌱 Config ===
ROOT = Path(__file__).resolve().parents[2]  # /XI
MEMORY_DIR = Path(__file__).resolve().parents[2] / "memory"
EMBEDDING_DIM = 1024  # Local model: bge-large-en-v1.5

# Load .env (for DB password, not for OpenAI)
load_dotenv()

def _env(*names, default=None):
    for n in names:
        v = os.getenv(n)
        if v not in (None, ""):
            return v
    return default

DB_CONFIG = {
    "host":   _env("XI_DB_HOST","POSTGRES_HOST","PGHOST","DB_HOST", default="localhost"),
    "port":   int(_env("XI_DB_PORT","POSTGRES_PORT","PGPORT","DB_PORT", default="5432")),
    "dbname": _env("XI_DB_NAME","POSTGRES_DB","PGDATABASE","DB_NAME", default="xi_memory"),
    "user":   _env("XI_DB_USER","POSTGRES_USER","PGUSER","DB_USER",   default="xi_user"),
    "password": _env("XI_DB_PASSWORD","POSTGRES_PASSWORD","PGPASSWORD","DB_PASSWORD"),
}

def upsert_claims(rows):
    """
    Upsert rows into memory_claims.

    Expected keys per row:
      subject, predicate, object, value, value_type, claim_id,
      agent, journal_type, timestamp, file_path, (optional) status
    """
    if not rows:
        return 0

    import psycopg2
    from psycopg2.extras import execute_batch

    inserted = 0
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            # Ensure supporting index exists (idempotent)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_claims_claimid
                ON memory_claims (claim_id);
            """)

            sql = """
                INSERT INTO memory_claims
                  (subject, predicate, object, value, value_type, claim_id,
                   agent, journal_type, "timestamp", file_path, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,'current'))
                ON CONFLICT (claim_id) DO UPDATE
                  SET value        = EXCLUDED.value,
                      value_type   = EXCLUDED.value_type,
                      agent        = EXCLUDED.agent,
                      journal_type = EXCLUDED.journal_type,
                      "timestamp"  = EXCLUDED."timestamp",
                      file_path    = EXCLUDED.file_path,
                      status       = EXCLUDED.status
            """

            params = [
                (
                    r["subject"],
                    r["predicate"],
                    r["object"],
                    r["value"],
                    r["value_type"],
                    r["claim_id"],
                    r["agent"],
                    r["journal_type"],
                    r["timestamp"],
                    r["file_path"],
                    r.get("status") or "current",
                )
                for r in rows
            ]

            execute_batch(cur, sql, params, page_size=500)
            inserted = len(params)

        conn.commit()

    return inserted

# --- end: claims_upsert helper ---

#Overlay Merge Patch
XI_ROOT = Path(__file__).resolve().parents[2]
AMEND_APPLIED = XI_ROOT / "memory" / "amendments" / "applied"

# 🧷 --- Load overlays once at startup ---
def load_applied_overlays() -> dict:
    m = {}
    if not AMEND_APPLIED.exists():
        return m
    for p in sorted(AMEND_APPLIED.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        old_id = data.get("claim_id_old")
        if not old_id:
            continue
        m[old_id] = {
            "status": data.get("action", {}).get("status", "superseded"),
            "time_end": data.get("action", {}).get("time_end"),
            "superseded_by": data.get("action", {}).get("superseded_by"),
            "amendment_id": data.get("amendment_id"),
            "reason": data.get("reason"),
        }
    return m

OVERLAYS_BY_CLAIM = load_applied_overlays()

# 🧷 --- Apply overlays to an entry metadata dict ---
def apply_overlay_to_metadata(meta: dict) -> dict:
    """
    Expects meta to include at least: claim_id, time_start (ISO).
    Modifies: status, time_end, superseded_by (if overlay exists).
    """
    cid = meta.get("claim_id")
    if not cid:
        return meta
    overlay = OVERLAYS_BY_CLAIM.get(cid)
    if not overlay:
        return meta
    meta = dict(meta)  # copy
    meta["status"] = overlay.get("status", "superseded")
    if overlay.get("time_end"):
        meta["time_end"] = overlay["time_end"]
    if overlay.get("superseded_by"):
        meta["superseded_by"] = overlay["superseded_by"]
    meta["amendment_id"] = overlay.get("amendment_id")
    meta["amend_reason"] = overlay.get("reason")
    return meta

# --- Aux tables for fast reindex ---
def create_aux_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS indexed_files (
              source_file TEXT PRIMARY KEY,
              file_hash   TEXT,
              indexed_at  TIMESTAMPTZ DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS indexed_folders (
              folder_path TEXT PRIMARY KEY,
              state_hash  TEXT,
              checked_at  TIMESTAMPTZ DEFAULT now()
            );
        """)
    conn.commit()

# --- Fast checks ---
import os

def get_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def get_folder_state_hash(folder: Path) -> str:
    """
    Hash filenames + sizes + mtimes (not file contents) to detect changes quickly.
    """
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*.json")):
        try:
            st = p.stat()
            h.update(str(p.relative_to(folder)).encode())
            h.update(str(st.st_size).encode())
            h.update(str(int(st.st_mtime)).encode())
        except Exception:
            continue
    return h.hexdigest()

def get_stored_folder_hash(conn, folder_path: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT state_hash FROM indexed_folders WHERE folder_path=%s", (folder_path,))
        row = cur.fetchone()
        return row[0] if row else None

def upsert_folder_hash(conn, folder_path: str, state_hash: str):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO indexed_folders(folder_path, state_hash)
            VALUES (%s, %s)
            ON CONFLICT (folder_path) DO UPDATE SET state_hash=EXCLUDED.state_hash, checked_at=now()
        """, (folder_path, state_hash))
    conn.commit()

def get_stored_file_hash(conn, source_file: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT file_hash FROM indexed_files WHERE source_file=%s", (source_file,))
        row = cur.fetchone()
        return row[0] if row else None

def upsert_file_hash(conn, source_file: str, file_hash: str):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO indexed_files(source_file, file_hash)
            VALUES (%s, %s)
            ON CONFLICT (source_file) DO UPDATE SET file_hash=EXCLUDED.file_hash, indexed_at=now()
        """, (source_file, file_hash))
    conn.commit()

def filter_new_hashes(conn, hashes: list[str]) -> set[str]:
    if not hashes:
        return set()
    with conn.cursor() as cur:
        cur.execute("SELECT entry_hash FROM memory_entries WHERE entry_hash = ANY(%s)", (hashes,))
        existing = {row[0] for row in cur.fetchall()}
    return set(hashes) - existing

def apply_overlays_to_db(conn) -> int:
    """
    Push applied overlays into DB even if no new chunks are indexed.
    Returns number of rows updated.
    """
    overlays = OVERLAYS_BY_CLAIM  # already loaded by load_applied_overlays()
    if not overlays:
        return 0
    total = 0
    with conn.cursor() as cur:
        for cid, ov in overlays.items():
            status        = ov.get("status") or "superseded"
            time_end      = ov.get("time_end")         # ISO string or None
            superseded_by = ov.get("superseded_by")
            amendment_id  = ov.get("amendment_id")
            amend_reason  = ov.get("reason")
            cur.execute(
                """
                UPDATE memory_entries
                SET
                  status        = COALESCE(%s, status),
                  time_end      = COALESCE(%s, time_end),
                  superseded_by = COALESCE(%s, superseded_by),
                  amendment_id  = COALESCE(%s, amendment_id),
                  amend_reason  = COALESCE(%s, amend_reason)
                WHERE claim_id = %s
                """,
                (status, time_end, superseded_by, amendment_id, amend_reason, cid),
            )
            total += cur.rowcount
    conn.commit()
    return total

# === 🧬 Local Embedding (mean pooled BERT) ===

# === 🔑 Helper: SHA256 Hash of content ===
def get_entry_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

# === 🛢️ DB Setup ===
def connect_db():
    return psycopg2.connect(**DB_CONFIG)

def create_table_if_not_exists(conn):
    # Run DDL in autocommit so a single failure doesn't abort the whole tx
    prev_autocommit = conn.autocommit
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # ⛔️ REMOVE THIS (ops already installs extensions)
            # cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            # --- chunk-level store (existing) ---
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id SERIAL PRIMARY KEY,
                    entry_hash TEXT UNIQUE,
                    uuid TEXT,
                    agent TEXT,
                    timestamp TIMESTAMPTZ,
                    content TEXT,
                    source_file TEXT,
                    embedding VECTOR({EMBED_DIM}),
                    thread_type TEXT,
                    period TEXT,
                    claim_id TEXT,
                    status TEXT,
                    time_end TIMESTAMPTZ,
                    superseded_by TEXT,
                    amendment_id TEXT,
                    amend_reason TEXT,
                    content_fingerprint TEXT,
                    embedder_fingerprint TEXT,
                    validator_flags JSONB
                );
            """)

            for col, ddl in [
                ("claim_id", "TEXT"),
                ("status", "TEXT"),
                ("time_end", "TIMESTAMPTZ"),
                ("superseded_by", "TEXT"),
                ("amendment_id", "TEXT"),
                ("amend_reason", "TEXT"),
                ("content_fingerprint", "TEXT"),
                ("embedder_fingerprint", "TEXT"),
                ("validator_flags", "JSONB"),
            ]:
                cur.execute(f"ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS {col} {ddl};")

            # --- provenance tables used by indexer helpers ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    entry_uuid   TEXT PRIMARY KEY,
                    agent        TEXT,
                    tier         TEXT,
                    thread_type  TEXT,
                    period       TEXT,
                    timestamp    TIMESTAMPTZ,
                    source_file  TEXT,
                    compiled_uuids JSONB
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS links (
                    parent_uuid TEXT,
                    child_uuid  TEXT,
                    relation    TEXT,
                    weight      REAL
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS links_parent_idx ON links (parent_uuid);")
            cur.execute("CREATE INDEX IF NOT EXISTS links_child_idx  ON links (child_uuid);")

            # --- Diver-facing FTS table ---
            cur.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id     TEXT PRIMARY KEY,
                    agent  TEXT,
                    layer  TEXT,
                    thread TEXT,
                    ts     BIGINT,
                    text   TEXT
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS memories_text_idx
                ON memories USING gin (to_tsvector('english', text));
            """)
            cur.execute("""
                ALTER TABLE memories
                ADD COLUMN IF NOT EXISTS text_fts tsvector
                GENERATED ALWAYS AS (to_tsvector('english', COALESCE(text,''))) STORED;
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS memories_text_fts_idx
                ON memories USING gin (text_fts);
            """)
    finally:
        conn.autocommit = prev_autocommit



# --- provenance helpers (entries + links) ---
def infer_tier_from_path(p: str) -> str:
    p = str(p)
    if "/session/"   in p: return "session"
    if "/weekly/"    in p: return "weekly"
    if "/period/"    in p: return "period"
    if "/quarterly/" in p: return "quarterly"
    return "unknown"

def upsert_entry(conn, *, entry_uuid, agent, tier, thread_type, period, ts, source_file, compiled_uuids):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO entries (entry_uuid, agent, tier, thread_type, period, timestamp, source_file, compiled_uuids)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (entry_uuid) DO UPDATE SET
              agent=EXCLUDED.agent,
              tier=EXCLUDED.tier,
              thread_type=EXCLUDED.thread_type,
              period=EXCLUDED.period,
              timestamp=EXCLUDED.timestamp,
              source_file=EXCLUDED.source_file,
              compiled_uuids=EXCLUDED.compiled_uuids;
        """, (entry_uuid, agent, tier, thread_type, period, ts, source_file,
              json.dumps(compiled_uuids) if compiled_uuids else None))

def insert_links(conn, *, parent_uuid, child_uuids, relation="summarizes"):
    if not child_uuids:
        return
    w = 1.0 / max(1, len(child_uuids))
    with conn.cursor() as cur:
        execute_batch(cur, """
            INSERT INTO links (parent_uuid, child_uuid, relation, weight)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT DO NOTHING;
        """, [(parent_uuid, cu, relation, w) for cu in child_uuids])


# === 🧠 Main Indexing Logic ===
def index_memory_unused():
    conn = connect_db()
    create_table_if_not_exists(conn)
    cursor = conn.cursor()

    entries_to_insert = []

    for agent_dir in MEMORY_DIR.iterdir():
        if not agent_dir.is_dir():
            continue

        for journal_type in ["session", "weekly", "period", "quarterly"]:
            folder = agent_dir / f"{journal_type}_journal"
            if not folder.exists():
                continue

            for file in folder.rglob("*.json"):
                try:
                    with open(file, "r") as f:
                        data = json.load(f)

                    if isinstance(data, dict) and "entries" in data:
                        records = data["entries"]
                    else:
                        records = [data] if isinstance(data, dict) else data

                    for entry in records:
                        chunks = yield_record_chunks_from_strings(entry)
                        if not chunks:
                            continue
                        
                        entry_uuid = entry.get("uuid") or str(uuid.uuid4())
                        thread_type = entry.get("thread_type")
                        period = entry.get("period")

                        rel_source = str(file.relative_to(ROOT))
                        tier = infer_tier_from_path(rel_source)

                        raw_ts = (
                            entry.get("timestamp")
                            or entry.get("date")
                            or _dt.datetime.now(_dt.timezone.utc).isoformat()
                        )
                        ts = _parse_ts(raw_ts)

                        compiled_uuids = (
                            entry.get("compiled_uuids")
                            or entry.get("source_uuids")
                            or entry.get("children")
                            or []
                        )

                        # upsert entries + links once per entry (not per chunk)
                        try:
                            _conn = connect_db()
                            upsert_entry(_conn,
                                entry_uuid=entry_uuid, agent=agent_dir.name, tier=tier,
                                thread_type=thread_type, period=period, ts=ts,
                                source_file=rel_source, compiled_uuids=compiled_uuids)
                            if tier in ("weekly", "period", "quarterly"):
                                insert_links(_conn, parent_uuid=entry_uuid, child_uuids=compiled_uuids)
                            _conn.commit()
                        except Exception as e:
                            print(f"⚠️ entries/links upsert failed for {file}: {e}")
                        finally:
                            try:
                                _conn.close()
                            except Exception:
                                pass

                        _embed_cache = {}
                        def embed_once(txt: str):
                            key = txt.strip()
                            if key not in _embed_cache:
                                _embed_cache[key] = get_embedding(key)
                            return _embed_cache[key]



                        for content in chunks:
                            embedding = get_embedding(content)
                            entry_hash = get_entry_hash(content)

                            cfp = _sha1_text(content)            # content fingerprint
                            efp = get_embedder_fingerprint()     # embedder fingerprint
                            agent_name = entry.get("agent", None)
                            claim_id = entry.get("claim_id", None)
                            status = entry.get("status", None)
                            time_end_iso = entry.get("time_end_iso", None)
                            superseded_by = entry.get("superseded_by", None)
                            amendment_id = entry.get("amendment_id", None)
                            amend_reason = entry.get("amend_reason", None)

                            # keep flags simple in this legacy path
                            flags = {}
                            entries_to_insert.append((
                                entry_hash,           # entry_hash
                                entry_uuid,           # uuid
                                agent_name,           # agent
                                ts,                   # timestamp
                                content,              # content
                                rel_source,           # source_file
                                embedding,            # embedding
                                thread_type,          # thread_type
                                period,               # period
                                cfp,                  # content_fingerprint
                                efp,                  # embedder_fingerprint
                                json.dumps(flags, ensure_ascii=False),  # validator_flags
                                claim_id,             # claim_id
                                status,               # status
                                time_end_iso,         # time_end
                                superseded_by,        # superseded_by
                                amendment_id,         # amendment_id
                                amend_reason          # amend_reason
                            ))

                except Exception as e:
                    print(f"⚠️ Error reading {file}: {e}")

    print(f"📥 Preparing to insert {len(entries_to_insert)} entries...")

    sql = """
        INSERT INTO memory_entries (
          entry_hash, uuid, agent, timestamp, content, source_file, embedding, thread_type, period,
          content_fingerprint, embedder_fingerprint, validator_flags,
          claim_id, status, time_end, superseded_by, amendment_id, amend_reason
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (entry_hash) DO UPDATE SET
          content_fingerprint   = EXCLUDED.content_fingerprint,
          embedder_fingerprint  = EXCLUDED.embedder_fingerprint,
          status                = COALESCE(EXCLUDED.status, memory_entries.status),
          time_end              = COALESCE(EXCLUDED.time_end, memory_entries.time_end),
          superseded_by         = COALESCE(EXCLUDED.superseded_by, memory_entries.superseded_by),
          amendment_id          = COALESCE(EXCLUDED.amendment_id, memory_entries.amendment_id),
          amend_reason          = COALESCE(EXCLUDED.amend_reason, memory_entries.amend_reason);
    """
    execute_batch(cursor, sql, entries_to_insert)

    conn.commit()
    cursor.close()
    conn.close()
    print(f"✅ Indexed {len(entries_to_insert)} entries into PostgreSQL.")

def full_scan_all_agents():
    total_files, total_new = 0, 0
    conn = connect_db()
    create_table_if_not_exists(conn)
    create_aux_tables(conn)

    for agent_dir in MEMORY_DIR.iterdir():
        if not agent_dir.is_dir():
            continue
        agent = agent_dir.name

        for journal_dir in agent_dir.iterdir():
            if not journal_dir.is_dir() or not journal_dir.name.endswith("_journal"):
                continue

            # --- Folder-level skip ---
            try:
                folder_path = str(journal_dir.resolve().relative_to(ROOT.resolve()))
            except ValueError:
                folder_path = str(journal_dir.resolve())

            state_hash = get_folder_state_hash(journal_dir)
            prev_hash = get_stored_folder_hash(conn, folder_path)
            if prev_hash == state_hash:
                # unchanged folder → skip
                continue

            for file in journal_dir.rglob("*.json"):
                try:
                    total_files += 1
                    added = process_file_with_skips(conn, file, agent)
                    total_new += added
                except Exception as e:
                    print(f"⚠️ Index error on {file}: {e}")

            upsert_folder_hash(conn, folder_path, state_hash)

    # 🧷 Always apply overlays, even if no new chunks
    updated = apply_overlays_to_db(conn)
    if updated:
        print(f"🧷 Applied overlays to {updated} existing rows.")
    else:
        print("🧷 No overlay updates needed.")

    conn.close()
    print(f"✅ Full scan done. Files scanned: {total_files}, new chunks indexed: {total_new}")

def process_file_with_skips(conn, file: Path, agent_name: str, *, force: bool = False, mem_only: bool = False) -> int:
    """
    Reads a JSON journal file, upserts entries/links, splits into chunks,
    embeds ONLY new chunk-hashes, and upserts file hash.
    Returns number of NEW chunks inserted.
    """
    # File-level skip?
    try:
        rel_source = str(file.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        rel_source = str(file.resolve())

    # only for files under *_journal/* paths
    unix_path = rel_source.replace("\\", "/")
  
    # 🚫 Skip transient session_cache artifacts
    if "/session_cache/" in unix_path:
        return 0

    current_file_hash = get_file_hash(file)
    stored_file_hash = get_stored_file_hash(conn, rel_source)

    # Fast skip: unchanged file
    if stored_file_hash == current_file_hash and not force:
        if mem_only:
            # Minimal refresh: rewrite memories/links without embeddings
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                print(f"⚠️ Failed to read {file} for mem-only: {e}")
                return 0

            if isinstance(data, dict) and "entries" in data:
                records = data["entries"]
            elif isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                records = [data]
            else:
                records = []

            for entry in records:
                entry_uuid  = entry.get("uuid") or str(uuid.uuid4())
                meta        = entry.get("_meta", {}) or {}
                raw_ts      = (
                    meta.get("timestamp")
                    or entry.get("timestamp")
                    or meta.get("created_at_utc")
                    or entry.get("date")
                )

                # If only YYYY-MM-DD, pin to midnight UTC
                if isinstance(raw_ts, str) and len(raw_ts) == 10:
                    raw_ts = f"{raw_ts}T00:00:00+00:00"

                # Fall back to file mtime if still missing
                if not raw_ts:
                    try:
                        raw_ts = _dt.datetime.fromtimestamp(Path(file).stat().st_mtime, _dt.timezone.utc).isoformat()
                    except Exception:
                        raw_ts = _dt.datetime.now(_dt.timezone.utc).isoformat()

                ts = _parse_ts(raw_ts)

                thread_type = entry.get("thread_type")
                period      = entry.get("period")
                tier        = infer_tier_from_path(rel_source)
                compiled_uuids = entry.get("compiled_uuids") or entry.get("source_uuids") or entry.get("children") or []

                # provenance (entries + links)
                try:
                    upsert_entry(
                        conn,
                        entry_uuid=entry_uuid,
                        agent=agent_name,
                        tier=tier,
                        thread_type=thread_type,
                        period=period,
                        ts=ts,
                        source_file=rel_source,
                        compiled_uuids=compiled_uuids,
                    )
                    if tier in ("weekly", "period", "quarterly") and compiled_uuids:
                        insert_links(conn, parent_uuid=entry_uuid, child_uuids=compiled_uuids)
                except Exception as e:
                    print(f"⚠️ mem-only entries/links upsert failed for {file}: {e}")

                # memories row
                try:
                    flat_text = normalize_entry_text_for_index(entry)
                    upsert_memory(
                        conn,
                        mem_id=entry_uuid,
                        agent=agent_name,
                        layer=tier,
                        thread=thread_type or "general",
                        ts=int(ts.timestamp()),
                        text=flat_text,
                    )
                except Exception as e:
                    print(f"⚠️ mem-only memories upsert failed for {entry_uuid}: {e}")

            # ✅ commit INSIDE mem_only branch, after upserts
            conn.commit()

        # ✅ always exit the skip path (whether mem_only ran or not)
        return 0

        # Load JSON (same tolerant logic as before)
    try:
        data = json.load(open(file))
    except Exception as e:
        print(f"⚠️ Failed to read {file}: {e}")
        return 0


    # 💾 If this is a journal with top-level "claims", upsert them into memory_claims
    try:
        if isinstance(data, dict) and ("journal" in unix_path or "_journal/" in unix_path):
            claims = data.get("claims") or []
            if isinstance(claims, list) and claims:
                # derive agent / journal_type from path: memory/<agent>/<type>_journal/<tier>/*.json
                parts = Path(unix_path).parts
                agent = parts[1] if len(parts) > 1 else agent_name
                journal_type = parts[3] if len(parts) > 3 else "session"

                # timestamp: prefer 'date'; if only YYYY-MM-DD or missing, use file mtime ISO
                try:
                    mtime_iso = _dt.datetime.fromtimestamp(file.stat().st_mtime, _dt.timezone.utc).replace(microsecond=0).isoformat()
                except Exception:
                    mtime_iso = None
                ts = data.get("date")
                if not ts or (isinstance(ts, str) and len(ts) == 10):
                    ts = mtime_iso or _dt.datetime.now(_dt.timezone.utc).isoformat()

                # normalize & coerce value
                def coerce_value(val):
                    if isinstance(val, (int, float)):
                        return str(val), "numeric"
                    if isinstance(val, str):
                        if val.strip().isdigit():
                            return str(int(val.strip())), "numeric"
                        if len(val.split()) == 1 and len(val) <= 32:
                            return val, "enum"
                        return val, "text"
                    return json.dumps(val, ensure_ascii=False), "text"

                claim_rows = []
                for row in claims:
                    s = row.get("subject"); p = row.get("predicate"); o = row.get("object")
                    if isinstance(o, str) and o.strip() == "":
                        o = None
                    if not (s and p):
                        continue
                    val_str, vtype = coerce_value(row.get("value"))
                    claim_rows.append({
                        "subject": s,
                        "predicate": p,
                        "object": o,
                        "value": val_str,
                        "value_type": vtype,
                        "claim_id": row.get("claim_id") or str(uuid.uuid4()),
                        "agent": agent,
                        "journal_type": journal_type,
                        "timestamp": ts,
                        "file_path": rel_source,
                        "status": "current",
                    })

                if claim_rows:
                    try:
                        upsert_claims(claim_rows)
                    except Exception as e:
                        print(f"⚠️ claim upsert failed for {file}: {e}")
    except Exception as e:
        print(f"⚠️ claim extraction error for {file}: {e}")

    # proceed to build records for text indexing
    if isinstance(data, dict) and "entries" in data:
        records = data["entries"]
    elif isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = [data]
    else:
        print(f"⚠️ Unrecognized data format in {file}. Skipping.")
        return 0


    inserted = 0
    entries_to_insert = []

    for entry in records:
        # --- metadata + provenance ---
        entry_uuid  = entry.get("uuid") or str(uuid.uuid4())
        meta        = entry.get("_meta", {}) or {}
        raw_ts      = (
            meta.get("timestamp")
            or entry.get("timestamp")
            or meta.get("created_at_utc")
            or entry.get("date")
        )

        # If only YYYY-MM-DD, pin to midnight UTC
        if isinstance(raw_ts, str) and len(raw_ts) == 10:
            raw_ts = f"{raw_ts}T00:00:00+00:00"

        # Fall back to file mtime if still missing
        if not raw_ts:
            try:
                raw_ts = _dt.datetime.fromtimestamp(Path(file).stat().st_mtime, _dt.timezone.utc).isoformat()
            except Exception:
                raw_ts = _dt.datetime.now(_dt.timezone.utc).isoformat()

        ts = _parse_ts(raw_ts)

        thread_type = entry.get("thread_type")
        period      = entry.get("period")
        tier        = infer_tier_from_path(rel_source)

        compiled_uuids = (
            entry.get("compiled_uuids")
            or entry.get("source_uuids")
            or entry.get("children")
            or []
        )

        # Upsert entries + links
        try:
            upsert_entry(conn,
                entry_uuid=entry_uuid, agent=agent_name, tier=tier,
                thread_type=thread_type, period=period, ts=ts,
                source_file=rel_source, compiled_uuids=compiled_uuids)
            if tier in ("weekly", "period", "quarterly"):
                insert_links(conn, parent_uuid=entry_uuid, child_uuids=compiled_uuids)
        except Exception as e:
            print(f"⚠️ entries/links upsert failed for {file}: {e}")


        # --- Diver "memories" upsert (FTS) + optional graph links ---
        try:
            # Compose a readable text blob for FTS
            text_parts = []
            for fld in ("summary", "thoughts", "decisions"):
                v = entry.get(fld)
                if v is None:
                    continue
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                v = str(v).strip()
                if v:
                    text_parts.append(v)
            if not text_parts:
                # fallback if journals use other fields
                for fld in ("content", "output"):
                    v = entry.get(fld)
                    if v:
                        text_parts.append(str(v).strip())
                        break
            mem_text = "\n\n".join(text_parts)[:20000]  # keep it sane

            # epoch seconds for Diver schema
            ts_epoch = int(ts.timestamp())

            # Write the memory row
            upsert_memory(conn,
                mem_id=entry_uuid,
                agent=agent_name,
                layer=tier,
                thread=thread_type or "unknown",
                ts=ts_epoch,
                text=mem_text
            )

            # (Optional) record summarization edges for rollups
            if tier in ("weekly", "period", "quarterly") and compiled_uuids:
                w = 1.0 / max(1, len(compiled_uuids))
                for cu in compiled_uuids:
                    upsert_link(conn, parent_uuid=entry_uuid, child_uuid=cu, relation="summarizes", weight=w)

        except Exception as e:
            print(f"⚠️ Diver memories/links upsert failed for {entry_uuid}: {e}")

        # --- chunking (semantic batching) ---
        summary   = normalize_text_field(try_parse_embedded_json(entry.get("summary")))
        thoughts  = normalize_text_field(try_parse_embedded_json(entry.get("thoughts")))
        decisions = normalize_text_field(try_parse_embedded_json(entry.get("decisions")))

        def take_sentences(text: str) -> list[str]:
            out = []
            for para in _split_paragraphs(text or ""):
                out.extend(_sentences(para))
                if len(out) >= MAX_SENTENCES_PER_FIELD:
                    break
            return [c for c in out[:MAX_SENTENCES_PER_FIELD] if c.strip()]

        _embed_cache = {}
        def embed_once(txt: str):
            key = (txt or "").strip()
            if key not in _embed_cache:
                _embed_cache[key] = get_embedding(key)
            return _embed_cache[key]

        chunks = []
        if any([summary, thoughts, decisions]):
            for fld in (summary, thoughts, decisions):
                sents = take_sentences(fld)
                if sents:
                    chunks.extend(make_chunks_semantic(sents, embed_fn=embed_once))
        else:
            for fld in ("content", "output"):
                v = normalize_text_field(try_parse_embedded_json(entry.get(fld)))
                if v:                    
                    sents = take_sentences(v)
                    if sents:
                        chunks.extend(make_chunks_semantic(sents, embed_fn=embed_once))
                    if chunks:
                        break

        # 🛟 Fallback: ensure at least one chunk if trio text is nontrivial
        indexer_fallback_used = False  # 👉 add
        if not chunks:
            full_trio = "\n\n".join(
                x for x in [
                    summary or "",
                    thoughts or "",
                    decisions or ""
                ] if x.strip()
            ).strip()
            if len(full_trio) >= 280:  # threshold to avoid garbage
                print(f"⚠️ indexer: 0 semantic chunks for {rel_source}; using whole-doc fallback")
                chunks = [full_trio]
                entry.setdefault("_indexer_meta", {})["_indexer_fallback"] = True
                indexer_fallback_used = True  # 👉 add
            else:
                return 0  # nothing to index for this entry

        # --- chunk-level skip (Option A): embed only new hashes ---
        pending = [(get_entry_hash(c), c) for c in chunks]
        only_new = filter_new_hashes(conn, [h for h, _ in pending])

        # 💾 claim + overlay meta (once per entry; applies to all chunks for this entry)
        claim_id = entry_uuid  # use the entry UUID as the claim_id
        meta = apply_overlay_to_metadata({"claim_id": claim_id, "time_start": ts.isoformat()})
        status        = meta.get("status")          # "superseded" or None/"current"
        time_end_iso  = meta.get("time_end")        # ISO string or None
        superseded_by = meta.get("superseded_by")
        amendment_id  = meta.get("amendment_id")
        amend_reason  = meta.get("amend_reason")

        for entry_hash, content in pending:
            if entry_hash not in only_new:
                continue
            embedding = None if mem_only else get_embedding(content)
            if embedding is not None:
                _assert_dim(embedding)

            cfp = _sha1_text(content)                   # <-- content fingerprint
            efp = get_embedder_fingerprint()            # <-- embedder fingerprint

            flags = {}
            if indexer_fallback_used:
                flags["indexer_fallback"] = True
            if XI_DISABLE_SEARCH:
                flags["embedder_disabled"] = True

            # NOTE: use function param `agent_name`, not entry.get("agent")
            entries_to_insert.append((
                entry_hash,
                entry_uuid,
                agent_name,
                ts,
                content,
                rel_source,
                embedding,
                thread_type,
                period,
                cfp,
                efp,
                json.dumps(flags, ensure_ascii=False),
                claim_id,
                status,
                time_end_iso,
                superseded_by,
                amendment_id,
                amend_reason
            ))
            inserted += 1  # (optional) keep a real count of rows queued

    # Batch insert new chunks (if any)
    from psycopg2.extras import execute_batch  # ensure this import exists (top of file)

    if entries_to_insert:
        sql = """
            INSERT INTO memory_entries (
              entry_hash, uuid, agent, timestamp, content, source_file, embedding, thread_type, period,
              content_fingerprint, embedder_fingerprint, validator_flags,
              claim_id, status, time_end, superseded_by, amendment_id, amend_reason
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (entry_hash) DO UPDATE SET
              content_fingerprint = EXCLUDED.content_fingerprint,
              embedder_fingerprint = EXCLUDED.embedder_fingerprint,
              status = COALESCE(EXCLUDED.status, memory_entries.status),
              time_end = COALESCE(EXCLUDED.time_end, memory_entries.time_end),
              superseded_by = COALESCE(EXCLUDED.superseded_by, memory_entries.superseded_by),
              amendment_id = COALESCE(EXCLUDED.amendment_id, memory_entries.amendment_id),
              amend_reason = COALESCE(EXCLUDED.amend_reason, memory_entries.amend_reason);
        """
        execute_batch(conn.cursor(), sql, entries_to_insert)

    # Update file hash
    upsert_file_hash(conn, rel_source, current_file_hash)
    return inserted

# 🧠 Wrapper that calls the correct patched logic from memory_index_daemon.py
# === CLI entry ================================================================
if __name__ == "__main__":
    import sys
    import json
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Index XI memory into Postgres")
    parser.add_argument("--file", help="Path to a single JSON journal file to index")
    parser.add_argument("--folder", help="Index all JSON files under this folder (recursively; infers agent from path)")
    parser.add_argument("--agent", help="Agent name (required when using --file)")
    parser.add_argument("--all", action="store_true", help="Scan all agents/folders")
    parser.add_argument("--force", action="store_true", help="Reindex even if file hash unchanged")
    parser.add_argument("--mem-only", action="store_true",
                        help="Store content/metadata only; skip embeddings")
    args = parser.parse_args()

    def infer_agent_from_path(p: Path) -> str:
        s = str(p)
        if "/hermes/" in s:
            return "hermes"
        if "/davinci/" in s:
            return "davinci"
        if "/sentinel/" in s:
            return "sentinel"
        return "sentinel"  # default

    def safe_has_entries(p: Path) -> bool:
        # Be tolerant of different shapes; return True only for non-empty {entries:[...]} or a non-empty array.
        try:
            with p.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                return len(data["entries"]) > 0
            if isinstance(data, list):
                return len(data) > 0
        except Exception:
            # If unreadable or malformed, skip silently
            return False
        return False

    # 1) Single file mode
    if args.file and not args.folder and not args.all:
        if not args.agent:
            # infer agent from memory/{agent}/... path
            import re, os
            norm = os.path.normpath(args.file)
            m = re.search(r"(?:^|/)memory/([^/]+)/", norm)
            if m:
                args.agent = m.group(1)
                print(f"ℹ️ Inferred --agent={args.agent} from path.")
            else:
                print("⚠️ Please provide --agent when using --file")
                raise SystemExit(2)
        conn = connect_db()
        try:
            create_table_if_not_exists(conn)
            create_aux_tables(conn)
            added = process_file_with_skips(conn, Path(args.file), args.agent,
                                            force=args.force, mem_only=args.mem_only)
        finally:
            conn.close()
        print(f"✅ Indexed {added} chunks from {args.file}")
        sys.exit(0)

    # 2) Folder mode (recursive)
    if args.folder and not args.file:
        folder = Path(args.folder).resolve()
        if not folder.exists() or not folder.is_dir():
            print(f"❌ Folder not found: {folder}")
            raise SystemExit(2)

        files = sorted(folder.rglob("*.json"))
        if not files:
            print(f"ℹ️ No JSON files under: {folder}")
            sys.exit(0)

        conn = connect_db()
        total = 0
        try:
            create_table_if_not_exists(conn)
            create_aux_tables(conn)
            for f in files:
                # Optional pre-check to avoid noisy 0s; comment out if you prefer to try everything.
                if not safe_has_entries(f):
                    continue
                agent = infer_agent_from_path(f)
                try:
                    added = process_file_with_skips(conn, f, agent,
                                                    force=args.force, mem_only=args.mem_only)
                    total += (added or 0)
                    if added:
                        print(f"✅ Indexed {added} chunks from {f} (agent={agent})")
                except Exception as e:
                    # Don’t let one bad file abort the whole run
                    print(f"⚠️ Skipping {f}: {e}")
        finally:
            conn.close()

        print(f"🏁 Folder run complete. Files seen: {len(files)}, new chunks indexed: {total}")
        sys.exit(0)

    # 3) Default to full scan if neither --file nor --folder was provided
    if not args.all:
        print("ℹ️ No --file/--folder provided; running full scan (--all).")
    full_scan_all_agents()


