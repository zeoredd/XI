 (4) Flow Stage Map (the “ultimate recall” checklist)
-------------------------------------------------------
# FLOW MAP — End-to-End Stages
This document describes the lifecycle of a flow from start to finish.
Flows exercise the **Core** layer (orchestrator, flows, journaling, search),
while **Core-Ops** routines (indexer, daemon, rollups, sentinel audits)
run in the background to keep memory healthy.

## 0) Preconditions
- venv active; DB reachable; agents present; configs readable.
- (Background) Daemon & routines may already be running via systemd.
- routines_orchestrator may already be running in background (via systemd/cron) to handle rollups, indexing, daemon health, and audits.
- Sudo handler loaded: intercepts privileged commands before agent turn

Note: GAEL (Guided Agent Experience Layer) is imported by the orchestrator 
and invoked at runtime in later stages (preamble, retrieval, journaling, conflicts).
See [docs/components/gael.md](docs/components/gael.md).

## 1) Flow Start
- file: `flows/flow_orchestrator.py`
- triggers: GAEL preload; optional routines sanity check
- If input is a sudo command, flow_orchestrator routes it via sudo_command_handler.py 
  and may return a control signal (END, REFLECT, etc.) before any agent turn.

## 2) Agent Turn Preparation
- GAEL injection via `situational_awareness_prompts.py` 
  (see [docs/components/gael.md](docs/components/gael.md))
  • Pre-turn preamble (`gael_preamble`)
  • Retrieval selection hint (`gael_selection_hint`) shown before previews
  • Retrieval stack details: see docs/components/search_engines.md
  • Agents log [Memory Considered]/[Rejected]/[Selected] with IDs
- (optional) memory search (scope = **self only**) via `abilities/common_abilities/memory_search.py`
- Present hits → select **one** (or few) with `memory_selection_utils.py`
- Inject **only** the selected chunk(s) (token-efficient)
- Fallbacks (if confidence low or search disabled):
  - `recency_search.py` (very recent journals)
  - `/rvs` reverse-vector search (claims timeline) when the query is a structured fact
- Guardrails:
  - Hard cap vector queries per turn; degrade gracefully on errors (do not block the flow)
  - (Optional) show authorship metadata to the **calling agent** for provenance; still self-scoped

## 3) Agent Output → Journal Composition
- handler: `journal_response_handler.py`
- writes: `memory/{agent}/{thread_type}/session/{agent}_session_journal_YYYYMMDD.json`
- Writes GAEL trio via *{agent}_write_session_journal.py* (prints JOURNAL_PATH)
- Includes: [Summary, Thoughts, Decisions] (GAEL trio — enforced by GAEL journal prompts)

## 3.5) After Stage 3 (journal write)
Indexer run (file/agent or full scan):
  • Apply overlays (mark superseded)\n  • Inject/refresh claims (subject/predicate/value)
  • Zero-vector safe mode available via `XI_DISABLE_SEARCH=1`
  • Only then → Sentinel amend check (if signaled)

## 4) Reindex
- call: `abilities/common_abilities/memory_indexer.py`
- DB: upsert with `content_fingerprint`, normalized `embedder_fingerprint`
- overlays applied? (logs “Applied overlays …”)
- change cache: `indexed_files` / `indexed_folders` may skip unchanged folders

## 5) Inline Sentinel (if signaled)
- call: `sentinel_*` (e.g., `sentinel_routine_audit.py` lightweight check or specific amend)
- outputs: `sentinel_write_session_journal.py` on issues
- reindex again if amended
- Full Sentinel audit stack documented in [docs/components/sentinel.md](docs/components/sentinel.md)

## 6) Flow End
- flush temp state, optional sudo actions
- routine rollups scheduled separately (`routines_orchestrator.py`)

## 7) Later (Scheduled)
- weekly/period/quarterly promotions: `write_all_summaries_then_archive.py`
- full audit: `sentinel_routine_audit.py` + validators
- routines_orchestrator runs continuously in background (systemd/cron):
- ensures index daemon is alive
- runs indexing sweeps
- applies amendments
- triggers scheduled Sentinel audits

----------------------------------------------------------------------

Lightweight process inventory (for each stage)

| Stage | Files Touched                                          | DB Touch   | Logs You Expect       | Abort Criteria        | Next Step Trigger|
|------:|--------------------------------------------------------|------------|-----------------------|-----------------------|------------------|
| 1     | flow_orchestrator.py                                   | —          | "GAEL ready"          | config missing        | 2                |
| 2     | situational_awareness_prompts.py, memory_search.py     | read       | "vector hits: N"      | search fail (warn)    | 3                |
| 3     | journal_response_handler.py, memory/.../session/*.json | —          | "journal written"     | write error           | 4 (if ok)        |
| 4     | memory_indexer.py                                      | upsert     | "Applied overlays..." | DB down (warn)        | 5 (if inline)    |
| 5     | sentinel_*                                             | read/write | "amend applied"       | validator crash (skip)| 4(reindex)       |
| 6     | —                                                      | —          | "flow end"            | —                     | —                |
