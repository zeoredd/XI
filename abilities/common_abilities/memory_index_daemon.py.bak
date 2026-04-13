#!/usr/bin/env python3
"""
🧠 memory_index_daemon.py
Daemon that watches XI memory folders and auto-indexes new or changed entries into PostgreSQL using pgvector.
"""

import os
import json
import time
import uuid
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_batch
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
import re
import math
from pathlib import Path
import sys
import datetime as dt
import random
# add XI/ to sys.path so "abilities.*" imports work when running the file directly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

RESET_PHRASES = ("anyway", "switching gears", "new topic", "separately,")


# === Fingerprint helpers (Aug10) ===
import hashlib as _hashlib
import json as _json

def _sha1_text(txt: str) -> str:
    return _hashlib.sha1(txt.encode("utf-8", errors="ignore")).hexdigest()

def get_embedder_fingerprint() -> str:
    payload = {"model": EMBEDDER_NAME, "version": EMBEDDER_VERSION, "dim": EMBED_DIM}
    return _sha1_text(_json.dumps(payload, sort_keys=True))

def cosine(a, b):
    dot = sum(x*y for x,y in zip(a,b))
    na = math.sqrt(sum(x*x for x in a)) + 1e-8
    nb = math.sqrt(sum(x*x for x in b)) + 1e-8
    return dot / (na * nb)

def make_chunks_semantic(sentences, embed_fn, *,
                         min_chars=80, max_chars=350, max_sentences=3,
                         sim_threshold=0.35):
    chunks, cur_sents, cur_vec, cur_count, cur_size = [], [], None, 0, 0

    def flush():
        nonlocal cur_sents, cur_vec, cur_count, cur_size
        if cur_sents:
            text = " ".join(cur_sents).strip()
            if len(text) >= 40:
                chunks.append(text)
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
            cur_sents = [s]; cur_vec = embed_fn(s); cur_count, cur_size = 1, len(s)
            continue

        v = embed_fn(s)
        if cur_vec is None:
            cur_sents = [s]; cur_vec = v; cur_count, cur_size = 1, len(s); continue

        sim = cosine(v, cur_vec)
        if (cur_size + len(s) > max_chars) or (cur_count + 1 > max_sentences):
            flush()

        if cur_vec is None:
            cur_sents = [s]; cur_vec = v; cur_count, cur_size = 1, len(s)
        elif sim >= sim_threshold or cur_size < min_chars:
            cur_sents.append(s); cur_count += 1; cur_size += len(s)
            cur_vec = [(a*(cur_count-1) + b)/cur_count for a,b in zip(cur_vec, v)]
        else:
            flush()
            cur_sents = [s]; cur_vec = v; cur_count, cur_size = 1, len(s)

        if cur_size >= min_chars and (cur_count >= 1) and (cur_size >= max_chars*0.8 or cur_count >= max_sentences):
            flush()

    flush()
    return chunks


# === ✂️ Robust paragraph/sentence splitters (for chunked vector indexing) ===
SENT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9“"(\[])')
MAX_SENTENCES_PER_FIELD = 30  # cap per field to avoid huge entries

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

# === 🧬 Load local transformer embedder ===
from transformers import AutoTokenizer, AutoModel
import torch

# === Embedder config (Aug10) ===
EMBEDDER_NAME = globals().get("EMBEDDER_NAME", "bge-large-v1.5")
EMBEDDER_VERSION = globals().get("EMBEDDER_VERSION", "1.0")
EMBED_DIM = globals().get("EMBED_DIM", 1024)


# === 🌱 Config ===
ROOT = Path(__file__).resolve().parents[2]
MEMORY_DIR = ROOT / "memory"
SCAN_INTERVAL = 60 * 60  # 1 hour
EMBEDDING_DIM = 1024  # Local model: bge-large-en-v1.5

# Load .env (for DB creds)
load_dotenv()

DB_CONFIG = {
    "dbname": "xi_memory",
    "user": "postgres",
    "password": os.getenv("POSTGRES_PASSWORD", "yourpassword"),
    "host": "localhost",
    "port": "5432"
}

# === 🧬 Load local transformer embedder ===
EMBEDDER_PATH = "/home/node-alpha/XI/embedders/bge-large-v1.5"
tokenizer = AutoTokenizer.from_pretrained(EMBEDDER_PATH, local_files_only=True)
model = AutoModel.from_pretrained(EMBEDDER_PATH, local_files_only=True)

def get_embedding(text):
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        outputs = model(**inputs)
        last_hidden = outputs.last_hidden_state
        mean_pooled = last_hidden.mean(dim=1).squeeze()
        return mean_pooled.tolist()

# === 🔐 Hashing ===
def get_entry_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def get_file_hash(file_path):
    with open(file_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

# === 🛢️ DB ===
def connect_db():
    return psycopg2.connect(**DB_CONFIG)

def create_table_if_not_exists(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS memory_entries (
                id SERIAL PRIMARY KEY,
                entry_hash TEXT UNIQUE,
                uuid TEXT,
                agent TEXT,
                timestamp TIMESTAMPTZ,
                content TEXT,
                source_file TEXT,
                embedding VECTOR({EMBEDDING_DIM}),
                thread_type TEXT,
                period TEXT
            );
        """)
        conn.commit()

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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS entries (
              entry_uuid    TEXT PRIMARY KEY,
              agent         TEXT,
              tier          TEXT,
              thread_type   TEXT,
              period        TEXT,
              timestamp     TIMESTAMPTZ,
              source_file   TEXT,
              compiled_uuids JSONB
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS links (
              parent_uuid  TEXT,
              child_uuid   TEXT,
              relation     TEXT,
              weight       REAL,
              PRIMARY KEY (parent_uuid, child_uuid)
            );
        """)
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

# --- provenance helpers (entries + links) ---
def infer_tier_from_path(p: str) -> str:
    p = str(p)
    if "/session_journal/"   in p: return "session"
    if "/weekly_journal/"    in p: return "weekly"
    if "/period_journal/"    in p: return "period"
    if "/quarterly_journal/" in p: return "quarterly"
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

# === 🧠 Indexing Logic ===
def normalize_text_field(value):
    if isinstance(value, list):
        return " ".join(str(x) for x in value).strip()
    elif isinstance(value, str):
        return value.strip()
    elif value is None:
        return ""
    else:
        return str(value).strip()

def try_parse_embedded_json(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, (dict, list)) else value
        except json.JSONDecodeError:
            return value
    return value

def sanitize_vector_input(text):
    if isinstance(text, str) and re.search(r'{\\?"entries\\?"\s*:', text):
        return "[Redacted nested memory block]"
    return text

def index_file(file_path, agent):
    try:
        with open(file_path, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to read {file_path}: {e}")
        return 0

    if isinstance(data, dict) and "entries" in data:
        records = data["entries"]
    elif isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = [data]
    else:
        print(f"⚠️ Unrecognized data format in {file_path}. Skipping.")
        return 0

    inserted = 0
    to_insert = []

    for entry in records:
        # 🔍 Unwrap embedded JSON for thoughts/decisions
        thoughts_raw = try_parse_embedded_json(entry.get("thoughts", ""))
        decisions_raw = try_parse_embedded_json(entry.get("decisions", ""))

        # 🧠 Extract text from structured or raw formats
        if isinstance(thoughts_raw, dict) and "entries" in thoughts_raw:
                thoughts_text = "\n".join(e.get("content", "") for e in thoughts_raw["entries"])
        elif isinstance(thoughts_raw, list):
                thoughts_text = "\n".join(normalize_text_field(e) for e in thoughts_raw)
        else:
                thoughts_text = normalize_text_field(thoughts_raw)

        if isinstance(decisions_raw, list):
                decisions_text = "\n".join(normalize_text_field(e) for e in decisions_raw)
        else:
                decisions_text = normalize_text_field(decisions_raw)

        summary_text = normalize_text_field(entry.get("summary", ""))

        # 🧼 Sanitize in case of recursive memory embedding
        thoughts_text = sanitize_vector_input(thoughts_text)
        decisions_text = sanitize_vector_input(decisions_text)
        summary_text = sanitize_vector_input(summary_text)

        # === provenance: entries + links (one row per journal entry; optional child uuid links) ===
        rel_source = str(file_path.resolve().relative_to(ROOT.resolve()))
        tier = infer_tier_from_path(rel_source)

        entry_uuid = entry.get("uuid") or str(uuid.uuid4())
        raw_ts = entry.get("timestamp") or entry.get("date") or entry.get("_meta", {}).get("timestamp")
        try:
            ts = datetime.fromisoformat(raw_ts) if raw_ts else datetime.utcnow()
        except Exception:
            ts = datetime.utcnow()

        thread_type = entry.get("thread_type")
        period      = entry.get("period")

        # accept several possible field names for higher-tier children
        compiled_uuids = (
            entry.get("compiled_uuids")
            or entry.get("source_uuids")
            or entry.get("children")
            or []
        )

        # upsert the entry + write links (short-lived conn)
        try:
            _conn = connect_db()
            upsert_entry(_conn,
                entry_uuid=entry_uuid, agent=agent, tier=tier, thread_type=thread_type,
                period=period, ts=ts, source_file=rel_source, compiled_uuids=compiled_uuids)
            if tier in ("weekly", "period", "quarterly"):
                insert_links(_conn, parent_uuid=entry_uuid, child_uuids=compiled_uuids)
            _conn.commit()
        except Exception as e:
            print(f"⚠️ entries/links upsert failed for {file_path}: {e}")
        finally:
            try:
                _conn.close()
            except Exception:
                pass

        # 🧠 per-entry embed cache to avoid duplicate sentence re-embeds
        _embed_cache = {}
        def embed_once(txt: str):
            key = txt.strip()
            if key not in _embed_cache:
                _embed_cache[key] = get_embedding(key)
            return _embed_cache[key]

        # 🧩 Build sentence-level chunks (paragraph-aware), fallback to content/output
        chunks = []

        # per-entry cache
        _embed_cache = {}
        def embed_once(txt: str):
            key = txt.strip()
            if key not in _embed_cache:
                _embed_cache[key] = get_embedding(key)
            return _embed_cache[key]

        def take_sentences(text: str) -> list[str]:
            out = []
            for para in _split_paragraphs(text or ""):
                out.extend(_sentences(para))
                if len(out) >= MAX_SENTENCES_PER_FIELD:
                    break
            return [c for c in out[:MAX_SENTENCES_PER_FIELD] if c.strip()]

        # Prefer structured fields if present
        have_structured = any([summary_text, thoughts_text, decisions_text])
        if have_structured:
            for field_text in (summary_text, thoughts_text, decisions_text):
                sents = take_sentences(field_text)
                if sents:
                    chunks.extend(make_chunks_semantic(sents, embed_fn=embed_once))

        # Fallback for older formats
        if not chunks:
            for fld in ("content", "output"):
                v = normalize_text_field(try_parse_embedded_json(entry.get(fld, "")))
                if v:
                    sents = take_sentences(v)
                    if sents:
                        chunks.extend(make_chunks_semantic(sents, embed_fn=embed_once))
                    if chunks:
                        break  # got something

        # Nothing to index?
        if not chunks:
            continue

        timestamp = ts  # same timestamp used in entries/links
        # entry_uuid, thread_type, period, rel_source already set above


        # Insert one row per chunk (unique hash per chunk)
        for content in chunks:
            content = sanitize_vector_input(content)
            embedding = get_embedding(content)
            entry_hash = get_entry_hash(content)
            to_insert.append((
                entry_hash,
                entry_uuid,
                agent,
                timestamp,
                content,
                rel_source,
                embedding,
                thread_type,
                period
            ))

    if to_insert:
        try:
            conn = connect_db()
            cursor = conn.cursor()
            execute_batch(cursor, f"""
                INSERT INTO memory_entries (
                    entry_hash, uuid, agent, timestamp, content, source_file, embedding, thread_type, period
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (entry_hash) DO NOTHING;
            """, to_insert)
            conn.commit()
            cursor.close()
            conn.close()
            inserted = len(to_insert)
        except Exception as e:
            print(f"⚠️ DB insert failed for {file_path}: {e}")

    return inserted

# === 🔁 Watch Loop ===
indexed_files = {}

def watch_memory():
    print(f"🌀 XI Memory Index Daemon — scanning every {SCAN_INTERVAL}s")
    while True:
        watch_memory_once()
        time.sleep(SCAN_INTERVAL)

    while True:
        for agent_dir in MEMORY_DIR.iterdir():
            if not agent_dir.is_dir():
                continue
            print(f"🔍 Looking at agent: {agent_dir}")

            for journal_type_dir in agent_dir.iterdir():
                skip_folders = {"disputed_entries", "invalid_signatures", "logic_notes", "audit_logs"}
                if journal_type_dir.name in skip_folders:
                    continue

                if not journal_type_dir.is_dir() or not journal_type_dir.name.endswith("_journal"):
                    continue
                print(f"📁 Scanning journal type: {journal_type_dir}")

                for depth_dir in journal_type_dir.iterdir():
                    if not depth_dir.is_dir():
                        continue
                    print(f"📂 Checking subfolder: {depth_dir}")

                    for file in depth_dir.rglob("*.json"):
                        print(f"📝 Scanning file: {file}")
                        try:
                            file_hash = get_file_hash(file)
                            if file in indexed_files and indexed_files[file] == file_hash:
                                continue  # Already indexed

                            new_entries = index_file(file, agent=agent_dir.name)
                            if new_entries:
                                print(f"✅ {new_entries} new entries indexed from: {file}")

                            indexed_files[file] = file_hash
                        except Exception as e:
                            print(f"⚠️ Error during file check/indexing: {e}")

                # ✅ Check permanent_memory.json
                permanent_memory_file = agent_dir / "permanent_memory.json"
                if permanent_memory_file.exists():
                    print(f"📌 Checking permanent memory: {permanent_memory_file}")
                    try:
                        file_hash = get_file_hash(permanent_memory_file)
                        if permanent_memory_file not in indexed_files or indexed_files[permanent_memory_file] != file_hash:
                            new_entries = index_file(permanent_memory_file, agent=agent_dir.name)
                            if new_entries:
                                print(f"✅ {new_entries} new entries indexed from: {permanent_memory_file}")
                            indexed_files[permanent_memory_file] = file_hash
                    except Exception as e:
                        print(f"⚠️ Error indexing permanent_memory.json: {e}")

        time.sleep(SCAN_INTERVAL)

def watch_memory_once():
    conn = connect_db()
    create_table_if_not_exists(conn)
    create_aux_tables(conn)

    total_files, total_new = 0, 0

    for agent_dir in MEMORY_DIR.iterdir():
        if not agent_dir.is_dir():
            continue
        agent = agent_dir.name

        for journal_dir in agent_dir.iterdir():
            if not journal_dir.is_dir() or not journal_dir.name.endswith("_journal"):
                continue

            try:
                folder_path = str(journal_dir.resolve().relative_to(ROOT.resolve()))
            except ValueError:
                folder_path = str(journal_dir.resolve())

            state_hash = get_folder_state_hash(journal_dir)
            prev_hash  = get_stored_folder_hash(conn, folder_path)
            if prev_hash == state_hash:
                continue  # unchanged folder → skip

            for file in journal_dir.rglob("*.json"):
                try:
                    total_files += 1
                    total_new += process_file_with_skips(conn, file, agent)
                except Exception as e:
                    print(f"⚠️ Index error on {file}: {e}")

            upsert_folder_hash(conn, folder_path, state_hash)

    # vacuum_if_needed(total_new)  # enable if you want periodic ANALYZE on bigger ingests
    conn.close()
    print(f"✅ One-time scan done. Files scanned: {total_files}, new chunks indexed: {total_new}")

# --- folder/file skip hash helpers ---
def get_folder_state_hash(folder_path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    for file in sorted(folder_path.rglob("*.json")):
        try:
            h.update(file.name.encode())
            h.update(str(file.stat().st_mtime_ns).encode())
            h.update(str(file.stat().st_size).encode())
        except FileNotFoundError:
            continue
    return h.hexdigest()

def get_stored_folder_hash(conn, folder_path: str) -> str:
    cur = conn.cursor()
    cur.execute("SELECT state_hash FROM indexed_folders WHERE folder_path=%s", (folder_path,))
    row = cur.fetchone()
    return row[0] if row else None

def upsert_folder_hash(conn, folder_path: str, state_hash: str):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO indexed_folders(folder_path, state_hash)
            VALUES (%s, %s)
            ON CONFLICT (folder_path) DO UPDATE
            SET state_hash=EXCLUDED.state_hash, checked_at=now()
        """, (folder_path, state_hash))
    conn.commit()

# --- main processing ---
def process_file_with_skips(conn, file: Path, agent_name: str) -> int:
    # relative path for provenance + file-hash key
    try:
        rel_source = str(file.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        rel_source = str(file.resolve())

    # file-level skip
    current_file_hash = get_file_hash(file)
    stored_file_hash  = get_stored_file_hash(conn, rel_source)
    if stored_file_hash == current_file_hash:
        return 0

    # load JSON
    try:
        with open(file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to read {file}: {e}")
        return 0

    if isinstance(data, dict) and "entries" in data:
        records = data["entries"]
    elif isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = [data]
    else:
        print(f"⚠️ Unrecognized data format in {file}. Skipping.")
        return 0

    entries_to_insert = []
    total_new = 0

    for entry in records:
        if not isinstance(entry, dict):
            continue

        entry_uuid  = entry.get("uuid") or str(uuid.uuid4())
        raw_ts      = entry.get("timestamp") or entry.get("date") or datetime.now(timezone.utc).isoformat()
        try:
            ts = datetime.fromisoformat(raw_ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            ts = datetime.now(timezone.utc)

        thread_type = entry.get("thread_type")
        period      = entry.get("period")
        tier        = infer_tier_from_path(rel_source)

        compiled_uuids = (
            entry.get("compiled_uuids")
            or entry.get("source_uuids")
            or entry.get("children")
            or []
        )

        # upsert provenance
        try:
            upsert_entry(conn,
                entry_uuid=entry_uuid, agent=agent_name, tier=tier,
                thread_type=thread_type, period=period, ts=ts,
                source_file=rel_source, compiled_uuids=compiled_uuids)
            if tier in ("weekly", "period", "quarterly"):
                insert_links(conn, parent_uuid=entry_uuid, child_uuids=compiled_uuids)
        except Exception as e:
            print(f"⚠️ entries/links upsert failed for {file}: {e}")

        # normalize fields
        summary   = normalize_text_field(try_parse_embedded_json(entry.get("summary")))
        thoughts  = normalize_text_field(try_parse_embedded_json(entry.get("thoughts")))
        decisions = normalize_text_field(try_parse_embedded_json(entry.get("decisions")))

        # sentence extraction
        def take_sentences(text: str) -> list[str]:
            out = []
            for para in _split_paragraphs(text or ""):
                out.extend(_sentences(para))
                if len(out) >= MAX_SENTENCES_PER_FIELD:
                    break
            return [c for c in out[:MAX_SENTENCES_PER_FIELD] if c.strip()]

        # per-entry embed cache
        _embed_cache = {}
        def embed_once(txt: str):
            key = (txt or "").strip()
            if key not in _embed_cache:
                _embed_cache[key] = get_embedding(key)
            return _embed_cache[key]

        # chunking (semantic grouping)
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

        if not chunks:
            continue

        # chunk-level skip: only embed/insert new hashes
        pending  = [(hashlib.sha256(c.encode("utf-8")).hexdigest(), c) for c in chunks]
        only_new = filter_new_hashes(conn, [h for h,_ in pending])

        for entry_hash, content in pending:
            if entry_hash not in only_new:
                continue
            embedding = get_embedding(content)
            entries_to_insert.append((
                entry_hash,
                entry_uuid,
                agent_name,
                ts,
                content,
                rel_source,
                embedding,
                thread_type,
                period
            ))

    if entries_to_insert:
        execute_batch(conn.cursor(), f"""
            INSERT INTO memory_entries (
                entry_hash, uuid, agent, timestamp, content, source_file, embedding, thread_type, period
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (entry_hash) DO NOTHING;
        """, entries_to_insert)
        conn.commit()
        total_new = len(entries_to_insert)

    # update file hash
    upsert_file_hash(conn, rel_source, current_file_hash)
    return total_new

def vacuum_if_needed(new_rows: int, threshold: int = 500):
    if new_rows < threshold:
        return
    try:
        conn = connect_db()
        with conn.cursor() as cur:
            cur.execute("ANALYZE memory_entries;")
            # If you want heavier cleanup only on big backfills, uncomment:
            # cur.execute("VACUUM (ANALYZE) memory_entries;")
        conn.commit()
        conn.close()
        print("🧹 ANALYZE completed on memory_entries")
    except Exception as e:
        print(f"⚠️ ANALYZE/VACUUM skipped: {e}")

# === 🏁 Entry Point with CLI Mode ===
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="XI Memory Index Daemon")
    parser.add_argument("--once", action="store_true", help="Run indexing once and exit")
    parser.add_argument("--loop", action="store_true", help="Run in continuous watch mode")
    parser.add_argument("--interval", type=int, help="Override scan interval in seconds")
    args = parser.parse_args()

    if args.interval and args.interval > 0:
        globals()["SCAN_INTERVAL"] = args.interval

    conn = connect_db()
    create_table_if_not_exists(conn)
    conn.close()

    if args.once:
        print("⚡ Running one-time memory index...")
        watch_memory_once()
    elif args.loop:
        watch_memory()
    else:
        print("❌ Please specify --once or --loop")
