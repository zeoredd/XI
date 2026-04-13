
-- XI Indexer/Daemon Schema Migration (Aug 10, 2025)
-- Safe to run multiple times.

-- 0) Ensure pgvector extension (requires superuser or appropriate privileges)
CREATE EXTENSION IF NOT EXISTS vector;

-- 1) memory_vectors core columns
ALTER TABLE IF EXISTS memory_vectors
  ADD COLUMN IF NOT EXISTS content_fingerprint TEXT,
  ADD COLUMN IF NOT EXISTS embedder_fingerprint TEXT,
  ADD COLUMN IF NOT EXISTS source_files TEXT,
  ADD COLUMN IF NOT EXISTS validator_flags JSONB DEFAULT '{}'::jsonb;

-- 2) Unique upsert key on path + entry UUID
CREATE UNIQUE INDEX IF NOT EXISTS ux_memory_vectors_path_uuid
ON memory_vectors (file_path, entry_uuid);

-- 3) Fingerprint index for quick dup checks
CREATE INDEX IF NOT EXISTS ix_memory_vectors_fingerprint
ON memory_vectors (content_fingerprint);

-- 4) Vector ANN index (tune lists as corpus grows)
-- Note: make sure 'embedding' is VECTOR(1024)
CREATE INDEX IF NOT EXISTS ivf_memory_vectors
ON memory_vectors USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- 5) Integrity report table
CREATE TABLE IF NOT EXISTS integrity_report (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now(),
  file_path TEXT NOT NULL,
  entry_uuid TEXT,
  issue TEXT NOT NULL,
  meta JSONB DEFAULT '{}'::jsonb
);

-- 6) Optional meta table for indexer state (e.g., embedder fingerprint)
CREATE TABLE IF NOT EXISTS indexer_meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
