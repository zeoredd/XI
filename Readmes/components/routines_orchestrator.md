routines_orchestrator.py
Purpose

Coordinates XI’s background maintenance routines. Runs rollups (weekly/period/quarterly), applies amendments, triggers indexing, ensures the indexer daemon is alive, and calls Sentinel audits. This script is the backbone of the “night crew.”

**Layer:** Core-Ops (background maintenance, scheduled or invoked by flows;
not required to run a single flow, but essential for long-term health).

---

Key Features

Rollups: promotes session → weekly → period → quarterly journals.

Amendments: sweeps applied amendments to keep overlays consistent.

Indexing: runs a one-shot indexer pass; can also target a specific file or agent.

Daemon: ensures the indexer daemon is running (systemd preferred, ps aux fallback).

Sentinel: launches audits (drift/conflict checks) at the end of cycles.

CLI flags: flexible entry points (dry-run safe, selective tasks).

Main Functions

run_rollups() — checks thresholds and promotes journal summaries.

sweep_amendments() — applies overlays to keep truth view clean.

run_indexer_once() — one-shot index; supports --index-v2 for the corrected path.

ensure_daemon_running() — starts or confirms indexer daemon.

run_sentinel() — launches Sentinel audit routines.

sweep_sandbox() — cleans old sandbox sessions (retention days).

CLI Usage
# Dry run all routines (no changes)
python3 runtime/routines_orchestrator.py --dry-run

# Run rollups + index + daemon + sentinel
python3 runtime/routines_orchestrator.py

# Index using new path (safe rollout)
python3 runtime/routines_orchestrator.py --index --index-v2

# Index single file
python3 runtime/routines_orchestrator.py --index --index-v2 --index-file memory/davinci/...json

# Sweep sandbox sessions older than 21 days
python3 runtime/routines_orchestrator.py --sweep-sandbox --sweep-days 21

Lifecycle Position

Flows (via flow_orchestrator) can call launch_routines_if_needed().

This ensures the indexer daemon and audit routines are running in the background without blocking interactive sessions.

Routines Orchestrator is also run standalone (cron/systemd timer) for scheduled maintenance.

Failure Modes

Daemon missing → started automatically if not found.

Indexer dimension mismatch → flagged in logs.

Amendment errors → logged, but do not block.

Sentinel audit failure → continues, flagged in summary log.

5-Minute Test

 Run --dry-run, confirm log sections: rollups, amendments, indexer, daemon, sentinel.

 Run --index --index-v2, confirm indexer command logs and daemon heartbeat.

 Check logs/routines.log (if configured) for ✅/❌ lines.

 Confirm daemon stays running (systemctl is-active xi-indexer.service or ps aux).
