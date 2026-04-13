# Journals (backbone of XI memory)

Journals are the persistent memory backbone of XI. 
They capture agent reflections (Summary / Thoughts / Decisions), roll up into temporal layers, and are validated by Sentinel.

---

## Quick Reference

| Tier              | Path                                           | Trigger / Writer                        | Promotion threshold | Notes                                      |
|-------------------|-----------------------------------------------|-----------------------------------------|---------------------|--------------------------------------------|
| **Session**       | `memory/{agent}/{thread_type}/session/`       | End of flow (`*_write_session_journal.py`), or `sudo write journals` | Every flow / checkpoint | Compact GAEL trio + metadata (uuid, sig)   |
| **Weekly**        | `memory/{agent}/{thread_type}/weekly/`        | `write_all_summaries_then_archive.py`   | 7 sessions          | Summarizes a week of sessions              |
| **Period**        | `memory/{agent}/{thread_type}/period/`        | `run_daily_summary_promotions.py`       | 4 weeklies          | Aligned to 4-4-5 calendar periods          |
| **Quarterly**     | `memory/{agent}/{thread_type}/quarterly/`     | daily promotion runner                  | 3 periods           | End-of-quarter reflections                 |
| **Audit (Sentinel)** | `memory/sentinel/sentinel_audit_journal/`  | `sentinel_write_session_journal.py`     | N/A                 | Validates all others; fallback if corrupted|

---


## How Journals Are Written

- **Writers:** 
  - `davinci_write_session_journal.py`, `hermes_write_session_journal.py`, etc. 
  - GAEL parses `[Summary]/[Thoughts]/[Decisions]` from thread. 
  - Includes metadata: date, quarter, period, model, temp, uuid, signature, `_meta.source_files`.

- **When:** 
  - End of flow (always). 
  - `sudo write journals` (manual checkpoint). 
  - Routine promotion scripts roll journals upward. 
  - Sentinel writes after audits.

- **Signing:** 
  - All journals Minisign-signed (`verify_signature.py` validates). 
  - Ensures integrity and non-repudiation.

---

## Temporal Structure

- **Calendar alignment:** `time_utils.py` provides quarter/period IDs (4-4-5 calendar). 
- **Promotion:** 
  - `write_all_summaries_then_archive.py`: promotes session → weekly → period → quarterly. 
  - `run_daily_summary_promotions.py`: ensures rollups run daily. 
- **Archiving:** Old entries moved to `archive/{year}/`.

---

## Sentinel Journals

- **Writer:** `sentinel_write_session_journal.py` 
  - Inserts GAEL trio, fallback content if corrupted. 
  - Always writes to `sentinel_audit_journal/`.

- **Validators:** 
  - `summary_layer_validator.py` → checks rollup coverage vs sources. 
  - `resolve_uuid_to_source.py` → traces UUIDs across tiers. 
  - `verify_signature.py` → ensures entries are signed.

---

## Agent Use of Journals

- **Continuity:** Journals loaded into context (`context_prepper.py`) at flow start. 
- **Search:** Indexed via memory indexer → searchable in flows. 
- **Validation:** Sentinel audits prevent drift/hallucination. 
- **Claims:** Journals can include normalized claims for claim-guard checks.

---

## Key Guarantees

1. Every flow → a signed session journal. 
2. Journals roll up on calendar thresholds (7/4/3). 
3. Archives keep history under `archive/{year}/`. 
4. Sentinel creates its own audit journals, independent and fallback-safe. 
5. GAEL ensures every journal has a Summary / Thoughts / Decisions trio.


