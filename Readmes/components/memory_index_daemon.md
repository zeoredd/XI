memory_index_daemon.py
Purpose

Background daemon that continuously watches memory folders, detects changes, and calls indexer logic to update Postgres
.

Key Features

Watch loop: Periodic scan (default 1 hour) for new/changed files
.

Chunking: Splits entries into sentences, groups semantically (reset phrases, length thresholds)
.

Embeddings: Same bge-large-v1.5 embedder, cached per-entry for efficiency
.

Skip logic: File/folder hash checks avoid redundant work
.

Permanent memory: Explicitly indexes permanent_memory.json per agent
.

Tables: Creates/maintains memory_entries, entries, links, plus indexed_files and indexed_folders for skip tracking
.

Main Functions

watch_memory() — long-running loop; scans each agent folder & permanent memory
.

watch_memory_once() — single scan; useful for cron/testing
.

process_file_with_skips() — like indexer’s version; embeds chunks, upserts provenance, inserts new rows only
.

make_chunks_semantic() — groups sentences into semantically coherent chunks before embedding
.

CLI Usage
# Run one-time scan
python3 abilities/common_abilities/memory_index_daemon.py --once

# Run continuously
python3 abilities/common_abilities/memory_index_daemon.py --daemon

Integration Note

Use memory_indexer.py for manual or full scans (startup, backfills, maintenance).

Use memory_index_daemon.py for continuous background indexing (systemd service).
