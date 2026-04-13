-- === Rollups ===
CREATE TABLE IF NOT EXISTS memory_rollups (
  id BIGSERIAL PRIMARY KEY,
  agent TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('week','period','quarter')),
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  entry_id BIGINT NOT NULL,          -- points to memory_entries.id that holds the rollup text
  child_ids BIGINT[] NOT NULL DEFAULT '{}',
  child_set_fp TEXT NOT NULL,        -- sha1 of sorted child entry_hashes (idempotency)
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(agent, level, start_date, end_date)
);
CREATE INDEX IF NOT EXISTS ix_memory_rollups_entry ON memory_rollups(entry_id);
CREATE INDEX IF NOT EXISTS ix_memory_rollups_agent_level ON memory_rollups(agent, level);

-- === Sentinel events ===
CREATE TABLE IF NOT EXISTS sentinel_events (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now(),
  type TEXT NOT NULL,                  -- conflict | verify_summary | drift
  entry_id BIGINT,                     -- primary subject
  related_ids BIGINT[] DEFAULT '{}',   -- supporting/contradicting entries
  severity TEXT NOT NULL DEFAULT 'info',  -- info | warn | critical
  status TEXT NOT NULL DEFAULT 'open',    -- open | resolved | ignored
  message TEXT NOT NULL,
  meta JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_sentinel_events_status ON sentinel_events(status);
CREATE INDEX IF NOT EXISTS ix_sentinel_events_type ON sentinel_events(type);

-- Optional: amendment links + "current truth" view (nice to have)
CREATE TABLE IF NOT EXISTS memory_links (
  id BIGSERIAL PRIMARY KEY,
  from_id BIGINT NOT NULL, -- new/correcting entry
  to_id   BIGINT NOT NULL, -- superseded/incorrect entry
  type    TEXT NOT NULL CHECK (type IN ('amends','supersedes','derived_from','refutes')),
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_memory_links_from ON memory_links(from_id);
CREATE INDEX IF NOT EXISTS ix_memory_links_to   ON memory_links(to_id);

CREATE OR REPLACE VIEW memory_truth AS
SELECT e.*
FROM memory_entries e
WHERE NOT EXISTS (
  SELECT 1 FROM memory_links ml
  WHERE ml.to_id = e.id AND ml.type = 'supersedes'
);

