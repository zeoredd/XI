🌌 XI System Overview (Big Picture)

XI is a local AI operating system built from modular components.
It combines flows, journaling, retrieval, and validation into a self-healing memory ecosystem.

Runtime Flow

Flows (flows/flow_orchestrator.py)

Orchestrated loops (e.g., idea_generator_flow.py).

Define when agents speak, when journaling occurs, and when retrieval is triggered.

GAEL (situational_awareness_prompts.py)

Inserts situational prompts at key moments (before vector search, journaling, etc.).

Ensures every memory write has the Summary / Thoughts / Decisions trio.

Search Engines (abilities/common_abilities/*, XI/diver/*)

Pull memory with budget (Diver → Search Controller → Vector → Recency).

GAEL wraps memory selection with “Considered / Rejected / Selected.”

Provenance logged to flash cache.

Sudo (sudo_command_handler.py)

Founder-only commands: sudo write journals, sudo end flow, etc.

Allows direct control over journaling, cache management.

Memory Backbone

Journals (*_write_session_journal.py)

Per-agent reflections written at end of flows.

GAEL-enforced trio (Summary, Thoughts, Decisions).

Signed with Minisign.

Temporal Rollups (write_all_summaries_then_archive.py)

Session → Weekly (7) → Period (4) → Quarterly (3).

Aligned to 4-4-5 business calendar (time_utils.py).

Archives rotated to archive/{year}/.

Indexing (memory_indexer.py, memory_index_daemon.py)

Embeds journals (BGE-large v1.5 → pgvector).

Supports semantic, claim-based, and temporal searches.

Integrity & Validation

Sentinel (abilities/sentinel/*)

Guardian auditor.

Validates signatures, UUID chains, contextual truth.

Detects claim conflicts, flips, drifts.

Enforces axioms (e.g., new beliefs supersede old).

Writes its own audit journals (sentinel_write_session_journal.py).

Amendments & Overlays

Conflicts → disputed_entries.json.

Resolved by overlays (overlay_*.json).

Tasks can be AI-assisted (sentinel_ai_task_runner.py) or manual (interactive_conflict_resolver.py).

Routines (routines_orchestrator.py)

Nightly at 03:00: rollups, Sentinel audit, indexer sweep.

DB hygiene.

Core Infra timers ensure monotonic scheduling (via USB RTC, systemd, or cron).

Key Guarantees

Every agent thought → journaled → rolled up → audited.

No silent corruption: all conflicts logged, flagged, or amended.

Provenance preserved: journals are signed and traceable.

GAEL scaffolds ensure structured, repeatable reflections.

System time monotonic: RTC enforces forward-only clock.

Human control: sudo commands and interactive conflict resolution are always available.

The Whole Picture

Think of XI as a living library:

Flows are conversations in motion.

GAEL is the librarian, making sure notes are clean and structured.

Journals are the shelves, filling with signed reflections.

Search Engines are the index, finding relevant entries fast.

Sudo is the master key, giving you ultimate control.

Sentinel is the archivist-guardian, constantly patrolling the stacks, checking for errors, enforcing rules, and ensuring the library never loses truth.

Core Infra is the clock and janitor, keeping everything on time and in order.

✨ Result: XI isn’t just an AI rig. It’s a self-auditing memory ecosystem — transparent, resilient, and explainable, built for both human trust and long-term survival.
