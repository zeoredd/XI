-------------------------------------
  (2) Connection Map (who calls who)
-------------------------------------
This document shows how major components call each other and what each produces. 
Unlike the Flow Map (Core only), the Connection Map spans **all three layers**: 
- **Core** (orchestrator, flows, journaling, search) 
- **Core-Ops** (routines orchestrator, indexer/daemon, rollups, sentinel audits) 
- **Core-Infra** (systemd units, USB RTC clock, Postgres/pgvector).


flows/flow_orchestrator.py
  ├─ imports → sudo_command_handler.py
  │    • Intercepts all user input; sudo commands routed before GAEL/agents
  │    • Uses result_codes.py constants (END, REFLECT, etc.)
  │    • Echoes sudo command + result into thread for audit trail
  ├─ uses → situational_awareness_prompts.py (GAEL; see [docs/components/gael.md](docs/components/gael.md))
  ├─ GAEL injection points:
      • Preamble (`gael_preamble`) at turn start
      • Retrieval selection hint (`gael_selection_hint`) shown before previews
      • Journaling enforced via GAEL trio `[Summary]/[Thoughts]/[Decisions]`
      • Claim-guard conflict messaging (`build_gael_conflict_message`, `gael_claim_guard_header`)
 | Journals (session/weekly/period/quarterly + audit) | GAEL trio + rollups | flow_orchestrator, sudo handler, routines | see [docs/components/journals.md](docs/components/journals.md) |
  ├─ parses → JOURNAL_PATH from writer stdout and passes it to indexer
  ├─ on retrieval → calls the search stack (Diver, search_controller, vector, recency)
  │    • GAEL selection hint shown before previews
  │    • See [docs/components/search_engines.md](docs/components/search_engines.md) for full funnel (budgets, heuristics, escalation order)
  ├─ before committing claims → inline_claim_guard.guard_claims_before_commit(...) (surface conflicts via GAEL; non-blocking)
  ├─ Sentinel hooks → inline amend, rollup validation, audit calls
  │    • Inline amend path runs before reindex
  │    • Rollup validation non-blocking
  │    • See [docs/components/sentinel.md](docs/components/sentinel.md) for full audit stack
  ├─ after journal → calls memory_indexer.py (or via routines_orchestrator)
  ├─ selects flow via registry (flows/__init__.py → FLOW_REGISTRY)
  └─ calls/launches → routines_orchestrator.py (rollups, amendments, indexing, daemon, sentinel audits; background ops)


journal_response_handler.py
  └─ writes → memory/{agent}/.../session/*.json
     ↪ reindex → memory_indexer.py

sentinel_routine_audit.py
  ├─ reads → memory/* (journals, summaries)
  ├─ verifies via → summary_layer_validator.py, thread_memory_validator.py
  └─ writes → sentinel_write_session_journal.py (audit notes)

abilities/common_abilities/memory_indexer.py
  └─ inserts/updates → Postgres (pgvector)
     • Honors `XI_DISABLE_SEARCH` (zero-vector fallback)
     • Normalizes embedder fingerprint (no absolute paths in DB)
     • Uses `indexed_files` / `indexed_folders` as change cache

abilities/common_abilities/memory_search.py
  ├─ accepts: scope, filters, limit
  └─ returns: hits + identity metadata (agent, thread_type, timestamp, source_file, signature_status, validator_flags)

memory_indexer.py
  ├─ reads memory/<agent>/<tier>/*.json
  ├─ embeds via bge-large-v1.5 (1024d) ← honors `XI_DISABLE_SEARCH`
  ├─ writes rows to Postgres (memory_entries)
  ├─ applies amendment overlays (from memory/amendments/applied/)
  └─ reindexes claims into memory_claims

memory_index_daemon.py
  ├─ watches memory/<agent>/ folders + permanent_memory.json
  ├─ detects new/changed files
  ├─ calls memory_indexer.py functions (process_file_with_skips, upserts)
  └─ runs continuously (systemd service)

(systemd & clock)
  └─ ensures monotonic timestamps for journals, audits, and signatures (USB RTC)

-----------------------------

Truth matrix (minimal)

| Component                              | Calls/Uses        | Called By                           | Outputs/Side Effects                        |
|----------------------------------------|-------------------|-------------------------------------|---------------------------------------------|
| flows/flow_orchestrator.py             | * See List Below  | CLI/user                            | ** See List Below                           |
| journal_response_handler.py            | # See List Below  | flow_orchestrator                   | session journals → memory/...               |
| memory_indexer.py                      | Postgres/pgvector | flow_orchestrator, daemon, routines | DB rows; content/embedder fingerprints; cache rows (indexed_*)|
| Sentinel (audits+validators+overlays)  | $ See List Below  | flow&routines_orchestrator, manual  | $$$ See List Below                          |
| sudo_command_handler.py                | % See List Below  | flow_orchestrator                   | returns codes from result_codes.py          |
| situational_awareness_prompts.py (GAEL)| —                 | flow_orchestrator, writers          | injected guidance text                      |
|                                        |                   |                                     | (See also: docs/components/gael.md)         |
| Search Engines (diver + controller + vector + recency) | retrieval stack  | flow_orchestrator, agents | ## See List Below                      |
| memory_index_daemon.py                 | memory_indexer    | systemd, manual CLI                 | Continuous indexing of changed files        |
| routines_orchestrator.py               | @ See List Below  | flow_orchestrator (optional check), cron/systemd | scheduled audits, rollups, indexing, daemon ensure|


* user input, sudo_command_handler, GAEL, journal_response_handler, memory_indexer, memory_search, sentinel_*, command list is in component's script
  forces agents to do things such as write journals, also controls switching flows, ending flows, and more.
# (filesystem write), minisign (if wired)
$ summary_layer_validator, thread_memory_validator
@ sentinel_routine_audit, write_all_summaries_then_archive
% flow controls, vector toggles, journal triggers
**Journals written, JOURNAL_PATH captured → index; GAEL preamble + selection logging; sudo ops
## injected snippets (GAEL-blocks or Selected/Recent Memory); see docs/components/search_engines.md
$$$audit journals, overlays, integrity reports; see docs/components/sentinel.md
