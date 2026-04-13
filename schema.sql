-- ─────────────────────────────────────────────────────────────────────────────
-- XI canonical schema (entries, claims, rollups, links, sentinel)
-- Requires: CREATE EXTENSION vector;  (vector 0.6.0+)
-- ─────────────────────────────────────────────────────────────────────────────

-- 0) Extensions
CREATE EXTENSION IF NOT EXISTS plpgsql;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- 1) memory_entries — core journal + embeddings
--    NOTE: we keep an integer/bigint id as PK for easy referencing from helpers,
--    and a UNIQUE constraint on entry_hash for content identity.
CREATE TABLE IF NOT EXISTS memory_entries (
  id                   BIGSERIAL PRIMARY KEY,
  entry_hash           TEXT UNIQUE,
  uuid                 TEXT NOT NULL,
  agent                TEXT NOT NULL,
  "timestamp"          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  content              TEXT NOT NULL,
  source_file          TEXT,
  embedding            VECTOR(1024),
  thread_type          TEXT,
  period               TEXT,
  content_fingerprint  TEXT,
  embedder_fingerprint TEXT,
  validator_flags      JSONB DEFAULT '{}'::jsonb,
  claim_id             TEXT,
  status               TEXT,
  time_end             TIMESTAMPTZ,
  superseded_by        TEXT,
  amendment_id         TEXT,
  amend_reason         TEXT
);

-- indexes (id PK auto)
CREATE INDEX IF NOT EXISTS idx_memory_entries_agent_time
  ON memory_entries (agent, "timestamp" DESC);

CREATE INDEX IF NOT EXISTS idx_memory_entries_content_trgm
  ON memory_entries USING GIN (content gin_trgm_ops);

-- vector similarity (choose one or keep both; both can coexist)
CREATE INDEX IF NOT EXISTS idx_memory_entries_embedding
  ON memory_entries USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_memory_entries_embedding_hnsw
  ON memory_entries USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_memory_entries_validator_flags
  ON memory_entries USING GIN ((validator_flags));

CREATE INDEX IF NOT EXISTS idx_memory_entries_source_file ON memory_entries (source_file);
CREATE INDEX IF NOT EXISTS idx_memory_entries_thread_period ON memory_entries (thread_type, period);

-- 2) memory_claims — conflicts / amendments / verifications
CREATE TABLE IF NOT EXISTS memory_claims (
  id              BIGSERIAL PRIMARY KEY,                    -- local row id (optional)
  claim_id        TEXT UNIQUE,                              -- global claim id (prefer using this)
  agent           TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ,
  claim_type      TEXT,                                     -- conflict | amendment | assertion
  status          TEXT,                                     -- open | accepted | rejected | applied
  severity        TEXT,                                     -- low | medium | high | critical
  summary         TEXT,
  details         TEXT,
  related_entries TEXT[],                                   -- entry_hash list
  proposed_diff   JSONB,                                    -- overlay content
  validator_flags JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_memory_claims_agent_time
  ON memory_claims (agent, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_claims_status
  ON memory_claims (status);

CREATE INDEX IF NOT EXISTS idx_memory_claims_validator_flags
  ON memory_claims USING GIN ((validator_flags));

-- 3) memory_rollups — tracks rollups (session→weekly→period→quarterly)
--    Matches your existing shape.
CREATE TABLE IF NOT EXISTS memory_rollups (
  id           BIGSERIAL PRIMARY KEY,
  agent        TEXT NOT NULL,
  level        TEXT NOT NULL,                               -- week | period | quarter
  start_date   DATE NOT NULL,
  end_date     DATE NOT NULL,
  entry_id     BIGINT NOT NULL,                             -- references memory_entries.id (not FK-enforced)
  child_ids    BIGINT[] NOT NULL DEFAULT '{}',
  child_set_fp TEXT NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT memory_rollups_level_check
    CHECK (level = ANY (ARRAY['week','period','quarter']))
);

CREATE UNIQUE INDEX IF NOT EXISTS memory_rollups_agent_level_start_date_end_date_key
  ON memory_rollups (agent, level, start_date, end_date);

CREATE INDEX IF NOT EXISTS ix_memory_rollups_agent_level ON memory_rollups (agent, level);
CREATE INDEX IF NOT EXISTS ix_memory_rollups_entry       ON memory_rollups (entry_id);

-- 4) memory_links — graph of relationships between entries
CREATE TABLE IF NOT EXISTS memory_links (
  id         BIGSERIAL PRIMARY KEY,
  from_id    BIGINT NOT NULL,                               -- memory_entries.id
  to_id      BIGINT NOT NULL,                               -- memory_entries.id
  type       TEXT NOT NULL,                                 -- amends | supersedes | derived_from | refutes
  created_at TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT memory_links_type_check
    CHECK (type = ANY (ARRAY['amends','supersedes','derived_from','refutes']))
);

CREATE INDEX IF NOT EXISTS ix_memory_links_from ON memory_links (from_id);
CREATE INDEX IF NOT EXISTS ix_memory_links_to   ON memory_links (to_id);

-- 5) sentinel_events — audit trail
CREATE TABLE IF NOT EXISTS sentinel_events (
  id          BIGSERIAL PRIMARY KEY,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  type        TEXT NOT NULL,                                -- e.g., audit_run, mismatch_found, amendment_applied
  entry_id    BIGINT,                                       -- memory_entries.id (optional)
  related_ids BIGINT[] DEFAULT '{}',
  severity    TEXT NOT NULL DEFAULT 'info',                 -- info | warn | error | critical
  status      TEXT NOT NULL DEFAULT 'open',                 -- open | resolved
  message     TEXT NOT NULL,
  meta        JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_sentinel_events_type   ON sentinel_events (type);
CREATE INDEX IF NOT EXISTS ix_sentinel_events_status ON sentinel_events (status);
