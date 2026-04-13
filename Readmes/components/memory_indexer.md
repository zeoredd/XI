Purpose

Indexes all XI memory JSON files into PostgreSQL with pgvector. Runs as a manual or full-scan job (not a daemon).

Key Features

Embedding: Uses bge-large-v1.5 (1024-dim). Honors `XI_DISABLE_SEARCH=1` for zero-vector fallback.
.

+Deduplication: Each chunk hashed (`entry_hash`) to avoid duplicates (idempotent re-runs).
.

Provenance: Upserts into entries and links tables (thread type, compiled_uuids)
.

Overlays: Applies amendment overlays from memory/amendments/applied/ during every run
.

Claims: Extracts and upserts journal claims into memory_claims table
.

Integrity: Logs dimension mismatches or malformed entries into `integrity_report` (create once).
.

Diver support: Also upserts into memories (FTS) for base Diver fallback
.

Main Functions

full_scan_all_agents() — walk all memory dirs, index new/changed files, apply overlays
.

process_file_with_skips() — per-file indexing with skip logic (hash compare, mem-only, claims, overlays)
.

upsert_claims() — insert/update claim rows with normalized values
.

create_table_if_not_exists() — ensures memory_entries, entries, links, and Diver’s memories exist
.

CLI Usage
# Index everything
python3 abilities/common_abilities/memory_indexer.py --all

# Index single agent
python3 abilities/common_abilities/memory_indexer.py --agent hermes --all

# Index a single file
python3 abilities/common_abilities/memory_indexer.py --file memory/davinci/...json --agent davinci
 
# Index a folder (new; recursively indexes *.json and infers agent from path)
python3 abilities/common_abilities/memory_indexer.py --folder /path/to/memory/sentinel/sentinel_audit_journal/session --force
```

Environment
```
# DB (honored in code; falls back to xi_user if unset)
XI_DB_HOST=localhost
XI_DB_PORT=5432
XI_DB_NAME=xi_memory
XI_DB_USER=xi_user
XI_DB_PASSWORD=...

# Memory roots and embedder
XI_MEMORY_ROOT=/home/<user>/XI/memory
XI_EMBEDDER_MODEL_PATH=/home/<user>/XI/embedders/bge-large-v1.5
XI_DISABLE_SEARCH=0   # set to 1 to write zero-vectors (offline/safe)
```

Operational Notes
- Extensions (`vector`, `pg_trgm`, `pgcrypto`) are installed once by ops; the indexer does **not** create extensions at runtime.
- Objects should be owned by `xi_user` so `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` works without superuser.
- The indexer uses `indexed_files` / `indexed_folders` to skip unchanged items; clear corresponding rows to force a rewalk.

