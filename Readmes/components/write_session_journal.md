# *{agent}_write_session_journal.py
**Layer:** Core

## Purpose
Compose and persist a session journal entry for an agent using GAEL fields
([Summary], [Thoughts], [Decisions]) extracted from the current thread.
Optionally attach normalized `claims`.

## Entry
- CLI: `python3 abilities/common_abilities/{agent}_write_session_journal.py --flow_context '<JSON>'`
- Called by: `flow_orchestrator.py` (captures JOURNAL_PATH line)

## Inputs
- `--flow_context` JSON:
  - `flow_type` (becomes `{flow_type}_journal`)
  - `thread_path` *or* `thread_refs` (list)
  - `model`, `temp`, optional `context_length`
  - optional `claims_override: [{subject,predicate,value}, ...]`

## Outputs / Side Effects
- Writes: `memory/{agent}/{flow_type}_journal/session/{agent}_session_journal_YYYYMMDD[_N].json`
- Prints: `JOURNAL_PATH=/abs/path/...json`
- Signs with minisign if key exists; `_meta.signed` updated

## Calls / Called By
- Calls: `parse_thread_for_journal(agent, flow_context)` to extract GAEL fields
- Called by: flow orchestrator / flows

## Failure Modes
- Bad `--flow_context` JSON → exits with error
- Missing GAEL fields → writes `[⚠️ No GAEL fields found in thread.]` in summary
- No minisign → writes unsigned (warn)

## 5-Min Test
- [ ] Provide a thread with [Summary]/[Thoughts]/[Decisions] → file written & JOURNAL_PATH printed
- [ ] Provide `claims_override` → `entry.claims` present
- [ ] Missing minisign → unsigned but succeeds

