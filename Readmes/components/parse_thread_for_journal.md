# parse_thread_for_journal.py
**Layer:** Core

## Purpose
Extract GAEL journal fields from a saved thread JSON for a given agent.

## How it works
- Reads the thread JSON (absolute `thread_path` or last of `thread_refs`)
- Concats all `output` strings where `entry.agent == target agent`
- Regex picks last occurrence of:
  - `^\[Summary\]: …`
  - `^\[Thoughts\]: …`
  - `^\[Decisions\]: …`
- Cleans common noise (“The final answer is:”); de-dupes decision lines

## Returns
`{"summary": str, "thoughts": str, "decisions": str}` (never `None`, trimmed)

## Failure Modes
- Unreadable thread → empty strings; caller should handle
- No GAEL blocks in outputs → empty strings (writer falls back)

## 5-Min Test
- [ ] Thread with multiple agent turns → picks the last blocks
- [ ] Decisions with bullet duplicates → de-duplicated
- [ ] Relative path in `thread_refs` → resolved against repo root

