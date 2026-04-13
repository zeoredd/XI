# Sentinel (Guardian + Auditor of XI Memory)

Sentinel is the dedicated agent responsible for **validating memory integrity** across XI. 
It runs periodic audits, writes its own audit journals, enforces axioms, and manages conflict resolution via overlays.

---

# Sentinel (Guardian + Auditor of XI Memory)

Sentinel is the dedicated agent responsible for **validating memory integrity** across XI. 
It runs periodic audits, writes its own audit journals, enforces axioms, and manages conflict resolution via overlays.

---

## Quick Reference (Overview)

| Function              | Script / Path                                   | Role                                                                 |
|-----------------------|-------------------------------------------------|----------------------------------------------------------------------|
| Rollup creation       | `abilities/common_abilities/sentinel.py`        | Ensures weekly/period/quarterly rollups exist & stitched summaries   |
| Routine audits        | `abilities/sentinel/sentinel_routine_audit.py`  | Performs scheduled audits (signatures, consistency, axioms)          |
| Nightly audit         | `abilities/sentinel/sentinel_nightly_audit.py`  | Scans DB for claim conflicts; applies supersedes if safe             |
| Contextual validation | `abilities/sentinel/sentinel_contextual_validator.py` | Compares journal sentences against threads (vector + fallback) |
| Conflict detection    | `abilities/sentinel/conflict_detector.py`       | Detects claim flips/drifts, flags pending vs overlay-resolved        |
| Claim extraction      | `abilities/sentinel/claim_extractor.py`         | Pulls structured claims from journals (explicit or heuristic)        |
| Overlay enforcement   | `abilities/sentinel/sentinel_ai_task_runner.py` | Generates AI tasks for overlays; applies `overlay_*.json`            |
| AI decision writing   | `abilities/sentinel/ai_decision_writer.py`      | Saves AI’s JSON verdicts into task dirs                              |
| Axiom enforcement     | `abilities/sentinel/axiom_enforcer.py`          | Applies AXIOM-003/004 consistency rules; logs to disputed_entries.json|
| Integrity reporting   | `abilities/sentinel/result_formatter.py`        | Builds `validator_flags` + `integrity_report` objects                |
| UUID tracing          | `abilities/sentinel/resolve_uuid_to_source.py`  | Finds all journal files containing given UUIDs                       |
| Thread validation     | `abilities/sentinel/thread_memory_validator.py` | Confirms journals reflect true thread content                        |
| Summary validation    | `abilities/sentinel/summary_layer_validator.py` | Checks weekly→session, period→weekly, quarterly→period consistency   |
| Interactive conflict resolution | `abilities/sentinel/interactive_conflict_resolver.py` | Manual review and fix of disputed entries            |
| Audit journals        | `abilities/sentinel/sentinel_write_session_journal.py` | Writes Sentinel’s own signed audit reflections                |
| Routine orchestrator  | `runtime/routines_orchestrator.py`              | Launches daily rollups, Sentinel audit, and indexer routines         |

---

## Quick Reference (Execution Context)

| Function              | Script / Path                                   | Trigger / Runner            | Output / Storage                        |
|-----------------------|-------------------------------------------------|-----------------------------|-----------------------------------------|
| **Routine audit**     | `sentinel_routine_audit.py`                     | Inline (flows) or nightly   | Flags, amendments, audit journal        |
| **Nightly audit**     | `sentinel_nightly_audit.py`                     | 03:00 via routines          | Claim conflicts, overlay supersedes     |
| **Contextual validator** | `sentinel_contextual_validator.py`           | During audits               | Sentence-to-thread match results        |
| **Thread validator**  | `thread_memory_validator.py`                    | During audits               | Thread vs session journal checks        |
| **Summary validator** | `summary_layer_validator.py`                    | During audits               | Weekly/period/quarterly rollup checks   |
| **Claim extractor**   | `claim_extractor.py`                            | Audit pipeline              | Normalized claims (subject/predicate/value) |
| **Conflict detector** | `conflict_detector.py`                          | Audit pipeline              | Detects flips/drifts, logs disputes     |
| **Axiom enforcer**    | `axiom_enforcer.py`                             | Audit pipeline              | Applies AXIOM rules, logs violations    |
| **Overlay task runner** | `sentinel_ai_task_runner.py`                  | If unresolved conflicts     | Creates tasks in `memory/sentinel/overlays/` |
| **AI decision writer** | `ai_decision_writer.py`                        | After task review           | Saves `ai_decisions.json` verdicts      |
| **Audit journal writer** | `sentinel_write_session_journal.py`          | After each audit            | Signed audit journals in `sentinel_audit_journal/` |
| **UUID resolver**     | `resolve_uuid_to_source.py`                     | During audits               | Locates UUIDs across tiers              |
| **Signature check**   | `verify_signature.py`                           | During audits               | Ensures all journals are signed         |
| **Integrity reporter**| `result_formatter.py`                           | End of audit pipeline       | `validator_flags` + `integrity_report`  |
| **Orchestration**     | `routines_orchestrator.py`                      | 03:00 daily (systemd/cron)  | Runs Sentinel + rollups + indexer       |

---

## Core Responsibilities

### 1. Journal Validation
- **Thread-level truth:** `thread_memory_validator.py` ensures journal entries match original threads. 
- **Rollup integrity:** `summary_layer_validator.py` checks UUID chain coverage between layers. 
- **Signatures:** `verify_signature.py` validates Minisign signatures. 
- **Contextual checks:** `sentinel_contextual_validator.py` compares journal sentences to threads using vector search + fallback model:contentReference

### 2. Claim Integrity
- **Extraction:** `claim_extractor.py` derives (subject, predicate, value) triples from journals:contentReference
- **Conflict detection:** `conflict_detector.py` flags flips/drifts or unresolved differences:contentReference
- **Nightly audit:** `sentinel_nightly_audit.py` scans DB claims, supersedes losers if safe:contentReference

### 3. Overlays & Amendments
- **AI tasks:** `sentinel_ai_task_runner.py` packages unresolved issues into `ai_payload.json`, invokes Sentinel agent, and applies overlay fixes
- **Decisions:** AI or human can write `ai_decisions.json`; applied via `ai_decision_writer.py`
- **Overlay files:** Saved under `memory/sentinel/overlays/overlay_*.json`.

### 4. Axioms
- **axiom_enforcer.py:** enforces core rules across permanent and journal memory: 
  - **AXIOM-004:** newer confirmed beliefs override older ones. 
  - **AXIOM-003:** (planned) UUID chain alignment across tiers
- Violations logged to `memory/sentinel/disputed_entries.json`.

### 5. Reporting
- **result_formatter.py:** compiles conflicts into `validator_flags` and an overall `integrity_report`
- **Audit Journals:** `sentinel_write_session_journal.py` saves Sentinel’s own signed journals for traceability. 

---

## How It Fits Into XI

- **Routines:** `routines_orchestrator.py` schedules Sentinel nightly at 3 AM, alongside rollups and indexer. 
- **Audit Trail:** All Sentinel outputs (audit journals, overlays, reports) live under `memory/sentinel/`. 
- **Inline Audits:** Quick validation runs inline during flows if suspicious memory is touched. 
- **GAEL Tie-in:** Sentinel AI tasks are framed with GAEL-like scaffolds for clear reasoning.

---

## Key Guarantees

1. Every agent journal is checked for **signature, coverage, and truth alignment**. 
2. Claim-level conflicts are detected, logged, and resolved via overlays when possible. 
3. Axioms prevent long-term drift (new beliefs supersede old). 
4. Sentinel maintains its own **audit journals** as a signed record of oversight. 
5. Nothing is silently dropped: all disputes → `disputed_entries.json` or `overlays/`.

---

## See Also

- [Journals](docs/components/journals.md): details the session → weekly → period → quarterly rollup structure that Sentinel validates.  
- [Search Engines](docs/components/search_engines.md): shows how retrieved memories (selected/considered/rejected) feed into the journals Sentinel later audits.  
- [Flows](docs/components/flows.md): explains when agent journals are written and how Sentinel is invoked inline during flow execution.



