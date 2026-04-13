### About This Layout

XI is organized into three layers:

- **Core** → the interactive backbone required to run flows in real time 
  (orchestrator, flows, GAEL, journaling, search).
- **Core-Ops** → background “night crew” tasks that keep memory healthy 
  (routines orchestrator, indexer/daemon, rollups, Sentinel audits). 
- **Core-Infra** → system-level support that underpins everything 
  (systemd services, USB RTC monotonic clock, Postgres/pgvector).

This README summarizes each layer and links to component docs. 
For the authoritative call graph and truth matrix, see [CONNECTION_MAP.md](CONNECTION_MAP.md).


----------------------------------
      1   Master README (concise)
----------------------------------
# XI — Master Overview

## Purpose
Short elevator pitch of XI and its operating model (agents, flows, memory layers, Sentinel verification).

## Core Pieces
- Orchestrator: `flows/flow_orchestrator.py` (runs any registered flow via --flow=…)
- Flows: `flows/{flow_name}_flow.py` (currently: idea_generator; see [docs/components/flows.md](docs/components/flows.md))
- GAEL guidance: `situational_awareness_prompts.py` (see [docs/components/gael.md](docs/components/gael.md))
- Journaling: `journal_response_handler.py` (+ agents’ memory folders)
- Indexer: `abilities/common_abilities/memory_indexer.py` (+ pgvector)
  Notes: uses bge-large-v1.5 (1024-dim) and honors `XI_DISABLE_SEARCH` for zero-vector fallback; see component stub.
- Search engines: `abilities/common_abilities/memory_search.py`  ← CORE
- Sentinel (validation & amend): `sentinel_*` (inline + scheduled audits)
- Routines (scheduled ops): `routines_orchestrator.py`  ← CORE-OPS
- Core Infrastructure: systemd units, USB RTC/clock discipline  ← CORE INFRA
- Sudo: `sudo_command_handler.py`
  (see [docs/components/sudo.md](docs/components/sudo.md)) ← CORE


## Lifecycles (Interactive Flow)
1. Flow start (orchestrator loads selected flow from registry)
2. GAEL pre-injection → optional vector search (scope: self|team|all)
3. Agent turn → journal write (Summary/Thoughts/Decisions)
4. Reindex (pgvector upserts; fingerprints)
5. Inline Sentinel (if agent flagged or heuristics detect issues) → amend → reindex
6. Flow end (temp flush). Scheduled ops handled separately.

## Lifecycles (Scheduled Ops via Routines)
- Handled by [routines_orchestrator.py](components/routines_orchestrator.md):
   - Promotions (weekly/period/quarterly)
   - Amendments sweep (applied overlays)
   - Index sweeps (full or targeted)
   - Index daemon health (restart if needed)
   - Sentinel audits (scheduled drift/conflict checks)

## Fast Links
- Connection Map → `docs/CONNECTION_MAP.md`
- Flow Stage Map → `docs/FLOW_MAP.md`
- Component Guides → `docs/components/*`
  - [Flows](docs/components/flows.md)
  - [GAEL](docs/components/gael.md)
  - [Search Engines](docs/components/search_engines.md)
  - [Sudo](docs/components/sudo.md)
  - [Journals](docs/components/journals.md)
  - [Session Journal Writers](docs/components/write_session_journal.md)
  - [Thread → Journal Parser](docs/components/parse_thread_for_journal.md)
  - [Routines Orchestrator](docs/components/routines_orchestrator.md)
  - [Sentinel](docs/components/sentinel.md)

## Run Quickstart
source venv/bin/activate
python3 -m flows.flow_orchestrator

## New Rig Quickstart (DB + venv)
1) System deps:
```bash
sudo apt update
sudo apt install -y postgresql postgresql-client python3.12-venv python3-full build-essential libpq-dev jq ripgrep
```
2) DB & extensions (run as postgres):
```bash
sudo -u postgres psql -d xi_memory -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d xi_memory -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
sudo -u postgres psql -d xi_memory -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
```
3) venv + Python deps:
```bash
python3 -m venv venv && source venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install "torch>=2.0.0" --index-url https://download.pytorch.org/whl/cpu
pip install "transformers>=4.30.0" accelerate sentencepiece scikit-learn python-dotenv "llama-cpp-python>=0.2.11" rich tqdm psycopg2
```
4) env file (`.env` in repo root):
```
XI_DB_HOST=localhost
XI_DB_PORT=5432
XI_DB_NAME=xi_memory
XI_DB_USER=xi_user
XI_DB_PASSWORD=change_me_now
XI_MEMORY_ROOT=/home/<user>/XI/memory
XI_EMBEDDER_MODEL_PATH=/home/<user>/XI/embedders/bge-large-v1.5
XI_DISABLE_SEARCH=0
```

## Safety + Integrity
- Signed journals, validator flags, amendment overlays
- Sentinel audit chain (session→weekly→period→quarterly)

## Glossary
- GAEL: Guided Agent Experience Layer — situational prompts injected at key moments 
  (see [docs/components/gael.md](docs/components/gael.md))
- Search Engines: retrieval stack including Diver, search_controller, vector, recency; 
  GAEL wraps previews/selection and logs provenance 
  (see [docs/components/search_engines.md](docs/components/search_engines.md))
- Journals: backbone of XI memory — per-agent reflections (Summary/Thoughts/Decisions), 
  rolled up weekly/period/quarterly, signed and validated by 
  [Sentinel](docs/components/sentinel.md) 
  (see [docs/components/journals.md](docs/components/journals.md))
- Sudo: privileged commands (`sudo …`) for founder-only flow control,
  journaling, and cache management(see [docs/components/sudo.md](docs/components/sudo.md))
- Flows: orchestrated runtime loops (e.g., idea_generator) that define agent turns,
  journaling points, and exit signals (see [docs/components/flows.md](docs/components/flows.md))
- Sentinel: Guardian auditor — validates journals and rollups, enforces axioms,
  detects conflicts, writes its own audit journals, and manages overlays
  (see [docs/components/sentinel.md](docs/components/sentinel.md))

Search Layer (Indexer & Daemon):
- Maintains searchable memory (vectors, fingerprints, claims). See component stub and commands in `memory_indexer.md`.


---------------------------------
Core-Ops: Indexer & Daemon
---------------------------------
The Memory Indexer & Daemon maintain XI’s searchable memory database. They transform journal JSON into vector embeddings, apply amendment overlays, and keep the Postgres memory_entries table current for Diver, Reverse Vector Search, and Recency Search.

README_Indexer_Daemon.md
 — Full overview: architecture, schema, ops, amendments, workflows.

memory_indexer.md
 — Component stub: manual/full scan entry point.

memory_index_daemon.md
 — Component stub: background daemon for continuous indexing.

When to Use

Indexer → one-off or full scans (startup, maintenance, recovery).

Daemon → continuous operation (runs as a systemd service).

Key Outputs

memory_entries table with vectors, fingerprints, provenance.

entries and links tables for tier relationships (session→weekly→period→quarterly).

memory_claims table for structured subject-predicate-value assertions.

Quick Commands
# Full system scan
python3 abilities/common_abilities/memory_indexer.py --all

# One-time daemon scan
python3 abilities/common_abilities/memory_index_daemon.py --once

# Continuous daemon (systemd recommended)
python3 abilities/common_abilities/memory_index_daemon.py --daemon

---------------------------------
Core-Ops: Routines Orchestrator
---------------------------------
The Routines Orchestrator coordinates XI’s background maintenance tasks. It
handles rollups (weekly/period/quarterly), applies amendments, triggers indexing,
ensures the indexer daemon is alive, and calls Sentinel audits. This is the
“night crew” that keeps the system healthy outside of interactive flows.

[routines_orchestrator.md](components/routines_orchestrator.md)
 — Component stub: quick reference for CLI flags, main functions, and lifecycle position.

When to Use

- Run standalone (cron/systemd timer) for scheduled ops
- Or let `flow_orchestrator.py` call `launch_routines_if_needed()` to ensure background health

Key Outputs

- Promoted journal rollups (weekly, period, quarterly)
- Applied amendment overlays
- Indexed rows kept fresh, daemon restarted if needed
- Sentinel audit logs

Quick Commands
```bash
# Dry run all routines
python3 runtime/routines_orchestrator.py --dry-run

# Run full maintenance (rollups, index, daemon, sentinel)
python3 runtime/routines_orchestrator.py

# Index using new safe path
python3 runtime/routines_orchestrator.py --index --index-v2
```

