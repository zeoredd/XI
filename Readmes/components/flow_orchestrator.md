flow_orchestrator.py
Purpose

Coordinates and runs XI flows. It acts as the universal entry point for launching a flow (--flow=...), wiring in GAEL prompts, journaling, search, indexing, and Sentinel checks.

Layer: Core (interactive backbone required for real-time flows).

Key Features

Flow registry: dispatch to any flow module (idea_generator_flow.py, etc.).

GAEL injection: ensures situational awareness prompts fire at key points.

Journaling: routes agent outputs into journal_response_handler.

Memory integration: can trigger inline indexing + sentinel amend sandwich.

Search integration: supports vector/FTS search, scope flag for self/team/all.

Ops integration: optionally checks routines_orchestrator to ensure background health.

Entry Points
# Run a specific flow
python3 -m flows.flow_orchestrator --flow=idea_generator

# Control search scope
python3 -m flows.flow_orchestrator --flow=idea_generator --search-scope=self

# Show authorship in search hits
python3 -m flows.flow_orchestrator --flow=idea_generator --show-authors=true


Calls / Called By

Calls: GAEL, chosen flow module’s run(), journal_response_handler, memory_indexer, memory_search, sentinel_inline_amend (conditional), routines_orchestrator (optional)

Called by: user directly via CLI (--flow=...)

Lifecycle Role

Runs Core logic for active flows: GAEL → journaling → indexing → sentinel.

May invoke Core-Ops (routines orchestrator) but doesn’t block on it.

Always identity-scoped: flows run per-agent context.

5-Minute Test

 Run --flow=idea_generator, confirm GAEL prompt appears.

 Journal file written under memory/<agent>/...

 Indexer invoked (logs fingerprints/overlays).

 Sentinel amend runs if flagged.

 Daemon health check runs (if launch_routines_if_needed active).
