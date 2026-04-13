# Search Engines (Diver + Controller + Vector + Recency)

This is the retrieval funnel the flows use before an agent replies. GAEL wraps it with selection hints + flash logging.

---

## Quick Reference

| Engine            | File / Entry                  | Cost / Mode         | When it’s called                                       | Output format                |
|-------------------|-------------------------------|---------------------|--------------------------------------------------------|-------------------------------|
| **Diver (base)**  | `XI/diver/base_diver.py`      | FTS (+ optional vec)| First choice if Diver enabled; fast lookup + neighbors | GAEL block w/ ids + snippet  |
| **Diver (cascade)** | `XI/diver/cascade_diver.py` | Multi-hop graph     | If base returns <2 hits or query is “why/how/trace”    | GAEL block + trace/links     |
| **Search Controller** | `abilities/common_abilities/search_controller.py` | Escalation funnel | Called if Diver absent or empty; escalates by budget   | GAEL block w/ provenance     |
| **Vector Search** | `abilities/common_abilities/memory_search.py` | pgvector query     | Runs inside GAEL selection step; agent/user picks ≤2   | “Selected Memory” injected   |
| **Recency Search**| `abilities/common_abilities/recency_search.py` | cheap JSON scan    | Last resort if nothing injected earlier                | “Recent Memory” injected     |
| **RVS (claims/text)** | `abilities/common_abilities/reverse_vector_search.py` | mid cost | Inside controller escalation, before vector            | GAEL block w/ claim excerpts |
| **Temporal Search** | `abilities/common_abilities/temporal_summary_search.py` | rollup scan | Only if explicitly asked for weekly/period/quarterly   | Rollup snippet               |

---


## Call sequence (at a glance)

1) Flow calls `run_agent_with_runtime(...)`
2) If the turn is a GAEL prompt → skip retrieval
3) Try **pull_memory_with_budget()**
   - Prefer **Diver** if enabled
   - Else fall back to **Search Controller**
4) If nothing injected yet → **Vector search** with previews → **GAEL selection** → inject chosen snippet
5) If still nothing → **Recency fallback**
6) Pass enriched input to the agent

## Engines

### Diver (`XI/diver/*`)
- Controller entrypoint: `run_diver_search(query, mode="base"|"cascade", ...)` returns list of hits (id/agent/thread/ts/excerpt/score/links/trace)
- `base_diver`: FTS-first (optionally vector); filters by layer/agent/thread/time; builds neighbor links; sorts by rank+recency; supports direct `by_id` lookup
- `cascade_diver`: multi-hop link expansion (beam/graph scoring), then re-ranks with rank/recency/graph and hydrates neighbors for top-K 
- Postgres FTS helper example (used inside Diver): `search_pg.fts_search()` shapes rows with `ts_headline` snippet + rank
- Quickstart doc is included in `README_diver.md` (CLI examples; “cascade” reserved; vector stub note)

### Search Controller (`abilities/common_abilities/search_controller.py`)
- Escalation funnel (typical order):
  1. Recency (strict)
  2. Recency (wide)
  3. Reverse-Vector Search (claims, then text)
  4. Vector (pgvector over `memory_entries`)
  5. Temporal summaries (weekly/period/quarterly)
- Enforces per-turn budgets; stops as soon as an injection candidate is found (then GAEL wraps selection).

### Vector Search (`abilities/common_abilities/memory_search.py`)
- pgvector embeddings (BGE-large v1.5) in Postgres; filters by agent/thread/period/time.
- Results shown as previews; **GAEL selection hint** is printed; user/agent picks ≤2; the selected snippet is injected and flash-logged with stable `id=`.

### Recency Search (`abilities/common_abilities/recency_search.py`)
- Fast JSON scan over recent session/weekly files (e.g., last 6–24h), used as a cheap fallback if nothing else injected.

### Reverse Vector Search (`abilities/common_abilities/reverse_vector_search.py`)
- Claim-centric search (subject/predicate/value), then a text pass. Used by the controller to front-load factual constraints.

### Temporal Summary Search (`abilities/common_abilities/temporal_summary_search.py`)
- Queries rollups (weekly/period/quarterly). Handy when you want “what we concluded last week” style retrieval.

## GAEL integration (selection + logging)

- Before vector previews, show the **GAEL selection hint**. Agents (or the user) log:
  - `[Memory Considered]: id=<ID> <why>`
  - `[Memory Rejected]: id=<ID> <why>`
  - `[Memory Selected]: id=<ID> <why>`
- The chosen snippet is injected as “Selected Memory” above the prompt.
- Flash cache records the decision with `id=...` for later auditing.

## Diver details (for reference)

- `diver_controller.run_diver_search(...)` chooses **base** vs **cascade**, applies filters (layer/agent/thread/time), and returns serializable hits
- **Base Diver** pipeline: detect vectors→(stub)→FTS seed→neighbors→rank by rank_norm⨉0.65 + recency⨉0.35→top-K; neighbor linking uses an auto-detected `links` schema; supports UUID fast-path and `by_id` lookups for exact fetches
- **Cascade Diver** pipeline: FTS seed (beam) → multi-hop neighbor expansion (weighted by relation + hop decay) → blend score (rank/recency/graph) → hydrate links/trace for top-K :contentReference[oaicite:7]{index=7}
- Postgres FTS helper example (`search_pg.py`) demonstrates `plainto_tsquery`/`phraseto_tsquery` and `ts_headline` shaping for snippets 
- Quickstart notes (CLI usage, vector stub remark) live in `README_diver.md` 

## When retrieval is skipped
- If the turn is a **pure GAEL prompt** (journal scaffolding, conflict prompt), we bypass retrieval and go straight to agent output formatting.

## Budget & heuristics

- **should_vector_search()**: skips vector retrieval if input is too short, trivial, or already GAEL-shaped (e.g., `[Summary]:` prompts).
- **vector_allowed()**: each agent has a budget counter per turn; once exhausted, no more vector queries until reset.
- **BudgetPolicy** (in search_controller): sets per-turn token/ops budgets; escalates stepwise (recency → RVS → vector → rollups) until budget is spent or a hit is found.
- **Fallback ordering** ensures cost-effective retrieval:
  1. Try Diver (graph-rich, if enabled)
  2. Try search_controller stack (cheap-to-expensive escalation)
  3. Vector search with GAEL selection
  4. Recency search as last resort


## Why it’s set up this way
- Keeps each turn grounded with **the smallest helpful memory** (≤2 picks).
- Keeps a **paper trail** (flash logs with `id=`) so Sentinel/audits can trace how a decision used memory.
- Allows **Diver** to provide graph-aware signal when available, while the controller provides a robust fallback stack.


