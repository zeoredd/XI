XI Memory Indexer & Daemon
Purpose

The Indexer and Daemon maintain XI’s searchable memory layer. They transform
agent journals into embeddings, store them in Postgres (xi_memory), and keep
entries consistent through fingerprints, overlays, and claim indexing. This
makes Diver, Reverse Vector Search, and Recency Search work reliably.

**Layer:** Core-Ops (background maintenance, not required for a single flow,
but essential for healthy long-term memory and search).

### Related Docs
- [memory_indexer.md](memory_indexer.md) — manual/full scan entry point
- [memory_index_daemon.md](memory_index_daemon.md) — background continuous daemon

Architecture

Files → memory/<agent>/<journal_type>/{session,weekly,period,quarterly}/*.json

Indexer → normalize text → split into chunks → embed with bge-large-v1.5 (1024d) → insert rows

Database → memory_entries table stores text, metadata, vectors

Daemon → background loop watches for file changes, runs the indexer as needed

Key Design Choices

Embedder: bge-large-v1.5 (dim=1024)

Fingerprints:

content_fingerprint = SHA-1 of chunk text

embedder_fingerprint = SHA-1 of embedder spec (model, version, dim)

Guardrails:

Hard check on embedding dimension

Integrity flags on odd entries

Amendments & Overlays:

JSON in memory/amendments/applied/ mark claims as superseded

Applied during every index run so retrieval hides outdated facts

Database Schema

Main table: memory_entries

Column	Purpose
id (PK)	unique row
entry_hash (unique)	dedupe key
uuid	source entry UUID
agent	e.g. davinci
timestamp	event time
content	normalized text (Summary/Thoughts/etc.)
source_file	path to JSON
embedding (vector)	pgvector 1024-dim
thread_type	session / weekly / period / quarterly
period	week / period / quarter
content_fingerprint	SHA-1 of text
embedder_fingerprint	SHA-1 of embedder spec
validator_flags (JSON)	audit / sentinel markers

Additional: memory_claims table for structured subject-predicate-value claims
.

Running It

Full scan (all agents):

python3 abilities/common_abilities/memory_indexer.py --all


Single agent:

python3 abilities/common_abilities/memory_indexer.py --agent davinci --all


Single file:

python3 abilities/common_abilities/memory_indexer.py \
  --file memory/davinci/idea_generator_journal/session/davinci_session_journal_20250809_8.json \
  --agent davinci


Daemon (continuous):

python3 abilities/common_abilities/memory_index_daemon.py --daemon

Healthy System Checks

Counts & fingerprints:

SELECT COUNT(*) total,
       COUNT(content_fingerprint) with_cfp,
       COUNT(embedder_fingerprint) with_efp
FROM memory_entries;


Recent rows:

SELECT id, agent, timestamp,
       LEFT(content_fingerprint,8) cfp,
       LEFT(embedder_fingerprint,8) efp,
       source_file
FROM memory_entries
ORDER BY id DESC
LIMIT 5;

Ops & Maintenance

Nightly backup:

pg_dump -h localhost -U postgres xi_memory > ~/backups/xi_memory_$(date +%F).sql


Weekly vacuum:

VACUUM (ANALYZE) memory_entries;


Systemd unit:

# /etc/systemd/system/xi-indexer.service
[Unit]
Description=XI Memory Index Daemon
After=network.target postgresql.service

[Service]
WorkingDirectory=/home/node-alpha/XI
Environment=XI_DB_DSN=postgresql://postgres:postgres@localhost:5432/xi_memory
Environment=XI_EMBEDDER_PATH=/home/node-alpha/XI/embedders/bge-large-v1.5
ExecStart=/home/node-alpha/XI/venv/bin/python3 abilities/common_abilities/memory_index_daemon.py --daemon
Restart=on-failure
User=node-alpha

[Install]
WantedBy=multi-user.target

Common Pitfalls

SQL f-string swallowing '{}'::jsonb → don’t use f""" here

Missing vector extension → run CREATE EXTENSION vector;

Embedder mismatch → ensure 1024-dim vectors

No re-index on small edits → chunk text identical → change full sentence

Amendment Workflow

Propose Amendment

python -m abilities.common_abilities.memory_amendments propose \
  --proposed-by hermes \
  --reason correction \
  --subject project \
  --predicate status \
  --value delayed \
  --claim-id-old <old_uuid> \
  --claim-id-new $(uuidgen)


Apply Amendment

python -m abilities.common_abilities.memory_amendments apply --id <amendment_id>


Re-Index

python3 runtime/routines_orchestrator.py --index


Retrieval Implication:

Default searches hide status='superseded'.

Historical queries can include them if needed

---

TL;DR

Indexer turns journals into DB rows with embeddings + fingerprints

Daemon runs it continuously (systemd recommended)

Overlays let you amend without deleting history

Claims are indexed alongside text for structured queries

Back up nightly, vacuum weekly, re-index when embedder changes
.

