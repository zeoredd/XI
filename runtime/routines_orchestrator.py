#!/usr/bin/env python3
"""
🧭 routines_orchestrator.py — MVP

Runs maintenance routines for XI:
  • Journal rollups (weekly / period / quarterly)
  • Sentinel audit pass
  • Indexer one-shot and/or ensure daemon running

Design goals (MVP):
  - Zero external deps beyond stdlib
  - Safe to run multiple times (idempotent-ish)
  - Clear logs; DRY-RUN mode for inspection
  - Pluggable thresholds & agent set

Folder assumptions are based on xi_filetree_finalized_july22 and related notes.

Brandon-style code guidelines respected:
  - Emojis in section headers
  - Consistent spacing
  - Clearly labeled function blocks for easy replacement

Usage examples:
  $ python routines/routines_orchestrator.py --run-all
  $ python routines/routines_orchestrator.py --rollups --sentinel --index --dry-run
  $ python routines/routines_orchestrator.py --watch 900 --run-all
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys, pathlib
import time, os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --- add near the very top, after standard imports ---
import sys as _sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]  # /home/.../XI
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))

# Now safe to import project packages
from datetime import date
from abilities.common_abilities.time_utils import get_current_quarter_and_period


# =============================================================================
# 🔧 Config
# =============================================================================

# Project root (auto-detected from this file's location)
ROOT = Path(__file__).resolve().parents[1] if (Path(__file__).resolve().parents[1] / "XI").exists() else Path(__file__).resolve().parents[0]
XI_ROOT = ROOT if (ROOT / "flows").exists() else Path.cwd()

# Agents whose journals we roll up (exclude sentinel from rollup generation)
AGENTS: List[str] = [
    "davinci",
    "hermes",
]

# Journal thresholds (based on July 22 structure and later clarifications)
THRESHOLDS = {
    "weekly": 7,       # every 7+ session journals → write a weekly
    "period": 4,       # every 4+ weekly journals → write a period summary
    "quarterly": 3,    # every 3+ period summaries → write a quarterly
}

# Script entrypoints (relative to XI root)
SCRIPTS = {
    "write_rollups": "abilities/common_abilities/write_all_summaries_then_archive.py",
    "sentinel_audit": "abilities/sentinel/sentinel_routine_audit.py",
    "indexer_once": "abilities/common_abilities/memory_indexer.py",
    "daemon": "abilities/common_abilities/memory_index_daemon.py",
}

# Journal roots
MEMORY_ROOT = XI_ROOT / "memory"

# Filename patterns (agent-prefixed scheme) — allow optional _HHMMSS and _N before .json
F_PATTERNS = {
    "session":   re.compile(r"^(?P<agent>[^_]+)_session_journal_\d{8}(?:_[0-9]{6}(?:_\d+)?)?\.json$"),
    "weekly":    re.compile(r"^(?P<agent>[^_]+)_weekly_journal_\d{8}(?:_[0-9]{6}(?:_\d+)?)?\.json$"),
    # Accept either YYYYMMDD or YYYYP{nn} for period
    "period":    re.compile(r"^(?P<agent>[^_]+)_period_journal_(?:\d{8}|\d{4}P\d{2})(?:_[0-9]{6}(?:_\d+)?)?\.json$"),
    # Accept either YYYYMMDD or YYYYQ{n} for quarterly
    "quarterly": re.compile(r"^(?P<agent>[^_]+)_quarterly_journal_(?:\d{8}|\d{4}Q[1-4])(?:_[0-9]{6}(?:_\d+)?)?\.json$"),
}

# Simple process name heuristics
PROCESS_HINTS = {
    "daemon": "memory_index_daemon.py",
}

# =============================================================================
# 🧱 Helpers
# =============================================================================


def sweep_sandbox(days: int = 21, min_interval_hours: int = 24):
    """
    Run tools/sandbox_retention.py --days <days> at most once per min_interval_hours.
    Safe no-op if never run or nothing to delete.
    """
    repo = Path(__file__).resolve().parents[0]  # adjust parents[...] if this file lives in a subfolder
    script = repo / "tools" / "sandbox_retention.py"
    sandbox_root = repo / "sandbox"
    logs_dir = repo / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    state_file = sandbox_root / ".last_sandbox_sweep"
    now = time.time()
    if state_file.exists():
        last = float(state_file.read_text().strip() or 0)
        if now - last < min_interval_hours * 3600:
            return  # skip; swept recently

    # run sweep (no --dry-run here)
    cmd = [sys.executable, str(script), "--root", str(sandbox_root), "--days", str(days)]
    with open(logs_dir / "sandbox_retention.log", "a") as log:
        log.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] running: {' '.join(cmd)}\n")
        subprocess.run(cmd, stdout=log, stderr=log, check=False)

    state_file.write_text(str(now))

# 🧭 Discover journal bases per agent (e.g., idea_generator_journal, unknown_journal)
def discover_journal_bases(agent: str) -> list[tuple[str, Path]]:
    agent_root = MEMORY_ROOT / agent
    out = []
    if agent_root.exists():
        for child in agent_root.iterdir():
            if child.is_dir() and child.name.endswith("_journal") and (child / "session").exists():
                out.append((child.name, child))
    return sorted(out)

def count_journals(agent: str) -> RollupStats:
    """Aggregate counts across all canonical bases discovered for this agent."""
    sessions = weeklies = periods = quarterlies = 0
    for journal_type, base in discover_journal_bases(agent):
        stats = count_journals_base(base, agent)
        sessions    += stats.sessions
        weeklies    += stats.weeklies
        periods     += stats.periods
        quarterlies += stats.quarterlies
    return RollupStats(sessions, weeklies, periods, quarterlies)

def run_sentinel_quick(agents: Optional[List[str]] = None, window_hours: int = 2, *, dry_run: bool = False) -> None:
    cmd = [sys.executable, "-m", "abilities.sentinel.sentinel_routine_audit", "--quick", "--window-hours", str(window_hours)]
    if agents:
        cmd += ["--agents", *agents]
    rc = run(cmd, cwd=XI_ROOT, dry_run=dry_run)
    log("✅ Sentinel quick audit completed" if rc == 0 else f"❌ Sentinel quick audit failed with code {rc}")

def run_module(module: str, args: List[str], *, cwd: Optional[Path] = None, dry_run: bool = False) -> int:
    return run([sys.executable, "-m", module, *args], cwd=cwd, dry_run=dry_run)


@dataclass
class RollupStats:
    sessions: int
    weeklies: int
    periods: int
    quarterlies: int


def _p(path: Path) -> str:
    return str(path.relative_to(XI_ROOT)) if str(path).startswith(str(XI_ROOT)) else str(path)


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: List[str], *, cwd: Optional[Path] = None, dry_run: bool = False) -> int:
    cmd_str = " ".join(shlex.quote(c) for c in cmd)
    log(f"$ {cmd_str}")
    if dry_run:
        return 0
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    return proc.returncode


def list_files(folder: Path, pattern: re.Pattern) -> List[Path]:
    if not folder.exists():
        return []
    return [p for p in folder.iterdir() if p.is_file() and pattern.match(p.name)]

def count_journals_base(base: Path, agent: str) -> RollupStats:
    """Count for a single {agent}/{journal_type}_journal base."""
    sessions    = len(list_files(base / "session",   F_PATTERNS["session"]))
    weeklies    = len(list_files(base / "weekly",    F_PATTERNS["weekly"]))
    periods     = len(list_files(base / "period",    F_PATTERNS["period"]))
    quarterlies = len(list_files(base / "quarterly", F_PATTERNS["quarterly"]))
    return RollupStats(sessions, weeklies, periods, quarterlies)

def sweep_amendments(*, dry_run: bool = False) -> None:
    """Apply any accepted amendments to overlays, then indexer can pick them up."""
    rc = run(
        [sys.executable, "-m", "abilities.common_abilities.memory_amendments", "sweep", "--applied-by", "routines_orchestrator"],
        cwd=XI_ROOT,
        dry_run=dry_run,
    )
    if rc == 0:
        log("🧷 Amendments sweep completed")
    else:
        log(f"❌ Amendments sweep failed with code {rc}")


# =============================================================================
# 🧠 Rollups
# =============================================================================

def rollup_due_map(agent: str) -> Tuple[RollupStats, Dict[str, bool]]:
    stats = count_journals(agent)
    due = {
        "weekly": stats.sessions >= THRESHOLDS["weekly"],
        "period": stats.weeklies >= THRESHOLDS["period"],
        "quarterly": stats.periods >= THRESHOLDS["quarterly"],
    }
    return stats, due


def _call_single_rollup_script(agent: str, tiers: List[str], *, force: bool, no_archive: bool, dry_run: bool) -> int:
    script = XI_ROOT / SCRIPTS["write_rollups"]
    if not script.exists():
        log(f"⚠️ Missing rollup script: {_p(script)}")
        return 1
    args = [sys.executable, str(script), "--agent", agent]
    if tiers:
        for t in tiers:
            args += ["--tier", t]
    if force:
        args.append("--force")
    if no_archive:
        args.append("--no-archive")
    return run(args, cwd=XI_ROOT, dry_run=dry_run)


def _call_per_tier_scripts(agent: str, tiers: List[str], *, dry_run: bool) -> int:
    # Fallback if you maintain separate files per tier.
    rc_total = 0
    mapping = {
        "weekly": SCRIPTS.get("write_weekly"),
        "period": SCRIPTS.get("write_period"),
        "quarterly": SCRIPTS.get("write_quarter"),
    }
    for t in tiers:
        path = mapping.get(t)
        if not path:
            continue
        script = XI_ROOT / path
        if not script.exists():
            log(f"⚠️ Missing {t} script: {_p(script)}")
            rc_total += 1
            continue
        rc_total += run([sys.executable, str(script), "--agent", agent], cwd=XI_ROOT, dry_run=dry_run)
    return rc_total

def do_rollups(agents: List[str], *, dry_run: bool = False) -> None:
    """
    Generate weekly/period/quarterly journals per (agent, journal_type) using
    the all-in-one script: abilities/common_abilities/write_all_summaries_then_archive.py
    Adds quarter/period visibility using time_utils.
    """
    script = XI_ROOT / SCRIPTS["write_rollups"]
    if not script.exists():
        log(f"⚠️ Missing rollup script: {_p(script)}")
        return

    for agent in agents:
        bases = discover_journal_bases(agent)
        if not bases:
            log(f"ℹ️ {agent}: no *_journal folders discovered")
            continue

        for journal_type, base in bases:
            # Strip "_journal" suffix so the rollup script gets the bare type
            jt = journal_type[:-8] if journal_type.endswith("_journal") else journal_type

            stats = count_journals_base(base, agent)

            due_weekly    = stats.sessions  >= THRESHOLDS["weekly"]
            due_period    = stats.weeklies  >= THRESHOLDS["period"]
            due_quarterly = stats.periods   >= THRESHOLDS["quarterly"]

            # Add visibility for current Q/P
            info = get_current_quarter_and_period(date.today())
            q_val = str(info["quarter"])
            p_val = str(info["period"])
            # normalize (strip any leading Q/P)
            quarter = q_val[1:] if q_val.upper().startswith("Q") else q_val
            period  = p_val[1:] if p_val.upper().startswith("P") else p_val

            if not (due_weekly or due_period or due_quarterly):
                log(f"🟢 {agent}/{jt} (Q{quarter} P{period}): no rollups due "
                    f"(sessions={stats.sessions}, weekly={stats.weeklies}, period={stats.periods})")
                continue

            tiers = ", ".join(
                t for t, v in {
                    "weekly": due_weekly,
                    "period": due_period,
                    "quarterly": due_quarterly
                }.items() if v
            )
            log(f"🟡 {agent}/{jt} (Q{quarter} P{period}): rollups due → {tiers or '(none)'}")

            # Call unified writer once per due tier so we can pass --level explicitly.
            rc = 0
            for level, is_due in (("weekly", due_weekly), ("period", due_period), ("quarterly", due_quarterly)):
                if not is_due:
                    continue
                cmd = [
                    sys.executable, "-m", "abilities.common_abilities.write_all_summaries_then_archive",
                    "--agent", agent,
                    "--journal_type", journal_type,
                    "--level", level,
                ]
                # Pass through NO_ARCHIVE via env var toggle (optional): XI_NO_ARCHIVE=1
                if os.environ.get("XI_NO_ARCHIVE") == "1":
                    cmd.append("--no-archive")
                rc = run(cmd, cwd=XI_ROOT, dry_run=dry_run) or rc

                # 🔒 Sentinel validation + conflict detection for the newest rollup at this level
                try:
                    from pathlib import Path as _Path
                    base_dir = _Path("memory") / agent / journal_type / level
                    latest = sorted(base_dir.glob("*.json"))[-1:] if base_dir.exists() else []
                    if latest:
                        newest = str(latest[0])
                        # Validator
                        vcmd = [
                            sys.executable, "-m", "abilities.sentinel.sentinel_validate_rollup",
                            "--file", newest,
                            "--strict", "--fix",
                            "--audit-root", "memory/sentinel/sentinel_audit_journal",
                        ]

                        run(vcmd, cwd=XI_ROOT, dry_run=dry_run)

                        # Conflict detector (non-blocking; marks verified=false if issues)
                        ccmd = [
                            sys.executable, "-m", "abilities.sentinel.conflict_detector",
                            "--file", newest,
                            "--emit-conflicts", "--mark-unverified",
                        ]
                        run(ccmd, cwd=XI_ROOT, dry_run=dry_run)
                except Exception as _e:
                    log(f"⚠️ Sentinel post-rollup checks skipped ({_e})")

            if rc == 0:
                log(f"✅ {agent}/{jt} (Q{quarter} P{period}): rollup script completed")
            else:
                log(f"❌ {agent}/{jt} (Q{quarter} P{period}): rollup script failed with code {rc}")


# =============================================================================
# 🧬 Indexing: one-shot + daemon ensure
# =============================================================================

def run_indexer_once(*, dry_run: bool = False) -> None:
    from inspect import currentframe
    outer = currentframe().f_back
    args = outer.f_locals.get("args") if outer else None

    # Only run if --index-v2 is enabled
    if not (args and getattr(args, "index_v2", False)):
        log("ℹ️ indexer v2 path not enabled (use --index-v2 to test). Skipping.")
        return

    script = XI_ROOT / SCRIPTS["indexer_once"]
    if not script.exists():
        log(f"⚠️ Missing indexer script: {_p(script)}")
        return

    cmd = [sys.executable, "-m", "abilities.common_abilities.memory_indexer", "--all"]

    if args:
        if getattr(args, "agents", None) and len(args.agents) == 1:
            cmd = [sys.executable, "-m", "abilities.common_abilities.memory_indexer",
                   "--agent", args.agents[0], "--all"]
        if getattr(args, "index_file", None):
            cmd = [sys.executable, "-m", "abilities.common_abilities.memory_indexer",
                   "--file", args.index_file]

    rc = run(cmd, cwd=XI_ROOT, dry_run=dry_run)
    if rc == 0:
        log("✅ Indexer completed")
    else:
        log(f"❌ Indexer failed with code {rc}")


def _process_running(hint: str) -> bool:
    """Check daemon liveness.
    Prefer systemd (if available), else fallback to `ps aux` heuristic."""
    # systemd path
    try:
        rc = subprocess.call(["systemctl", "is-active", "--quiet", "xi-indexer.service"])
        if rc == 0:
            return True
    except Exception:
        pass
    # fallback heuristic
    try:
        out = subprocess.check_output(["ps", "aux"], text=True)
    except Exception:
        return False
    return any(hint in line for line in out.splitlines())

def ensure_daemon_running(*, dry_run: bool = False) -> None:
    script = XI_ROOT / SCRIPTS["daemon"]
    if not script.exists():
        log(f"⚠️ Missing daemon script: {_p(script)}")
        return

    hint = PROCESS_HINTS["daemon"]
    if _process_running(hint):
        log("🟢 Index daemon already running")
        return

    log("🟡 Index daemon not running → starting")
    if dry_run:
        return

    # Start detached so orchestrator can exit.
    # Note: use setsid to detach the child; avoid shell to keep args clean.
    try:
        subprocess.Popen(
            [sys.executable, "-m", "abilities.common_abilities.memory_index_daemon"],
            cwd=str(XI_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log("✅ Index daemon started")
    except Exception as e:
        log(f"❌ Failed to start daemon: {e}")

# =============================================================================
# 🛡️ Sentinel Audit
# =============================================================================

def run_sentinel_audit(args: argparse.Namespace, *, dry_run: bool = False) -> None:
    """Run Sentinel's audit routine. Pass-through AI edge-case options when requested."""
    script = XI_ROOT / SCRIPTS["sentinel_audit"]
    if not script.exists():
        log(f"⚠️ Missing Sentinel audit script: {_p(script)}")
        return

    cmd = [sys.executable, "-m", "abilities.sentinel.sentinel_routine_audit"]
    # Pass-through flags
    if getattr(args, "sentinel_ai_edgecases", False):
        cmd.append("--ai-edgecases")
    if getattr(args, "ai_apply_lowrisk", False):
        cmd.append("--ai-apply-lowrisk")
        # also set env so inline helpers can respect it if needed
        os.environ["XI_SENTINEL_AI_APPLY_LOWRISK"] = "1"

    rc = run(cmd, cwd=XI_ROOT, dry_run=dry_run)
    if rc == 0:
        log("✅ Sentinel audit completed")
    else:
        log(f"❌ Sentinel audit failed with code {rc}")


# =============================================================================
# 🖥️ CLI glue
# =============================================================================

def launch_routines_if_needed():
    """
    Shim for flows: ensures light background routines (indexer, amendments sweep).
    Called opportunistically after flow writes. Safe if already running.
    """
    try:
        run_indexer_once(dry_run=False)
        ensure_daemon_running(dry_run=False)
        sweep_amendments(dry_run=False)
    except Exception as e:
        log(f"ℹ️ launch_routines_if_needed skipped due to error: {e}")

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="XI routines orchestrator (MVP)")

    # Toggles
    p.add_argument("--rollups", action="store_true", help="Generate weekly/period/quarterly journals if due")
    p.add_argument("--sentinel", action="store_true", help="Run Sentinel audit")
    p.add_argument("--sentinel-ai-edgecases", action="store_true",
                   help="Emit AI-assist task for nuanced/ambiguous conflicts")
    p.add_argument("--ai-apply-lowrisk", action="store_true",
                   help="Auto-apply low-risk overlays during audit")
    p.add_argument("--index", action="store_true", help="Run indexer once and ensure daemon")
    p.add_argument("--index-v2", action="store_true", help="Use new indexer path (safe rollout)")
    p.add_argument("--index-file", type=str, help="Index a single file (overrides --all)")
    p.add_argument("--sentinel-ai-consume", action="store_true",
                   help="Consume latest AI task (ask sentinel agent for decisions, apply overlays, re-index)")

    p.add_argument("--sweep-sandbox", action="store_true", help="Delete old sandbox sessions (safe retention)")
    p.add_argument("--sweep-days", type=int, default=21, help="Sandbox retention days (default: 21)")

    # Convenience
    p.add_argument("--run-all", action="store_true", help="Run rollups + index + sentinel")
    p.add_argument("--agents", nargs="*", default=AGENTS, help="Agents to process for rollups")
    p.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    p.add_argument("--no-archive", action="store_true", help="Pass --no-archive to rollup writer (testing)")

    # Rollup controls
    p.add_argument("--check-due", action="store_true", help="Print due status per agent and exit")

    # Looping
    p.add_argument("--watch", type=int, default=0, help="Repeat every N seconds (0 = run once)")

    # Running Sentinel Checks
    p.add_argument("--sentinel-quick", action="store_true", help="Run quick Sentinel audit (post-flow hook)")
    p.add_argument("--sentinel-agents", nargs="*", default=None, help="Limit quick Sentinel to these agents")
    p.add_argument("--sentinel-window-hours", type=int, default=2, help="Quick mode window (hours)")

    # memory_amendments
    p.add_argument("--amendment-sweep", action="store_true", help="Apply accepted amendments before indexing")
    p.add_argument("--weekly-threshold", type=int, help="Override weekly threshold (sessions → weekly)")
    p.add_argument("--period-threshold", type=int, help="Override period threshold (weekly → period)")
    p.add_argument("--quarterly-threshold", type=int, help="Override quarterly threshold (period → quarterly)")



    return p.parse_args(argv)

def run_once(args: argparse.Namespace) -> None:

    # Threshold overrides (useful for tests)
    if args.weekly_threshold is not None:
        THRESHOLDS["weekly"] = args.weekly_threshold
    if args.period_threshold is not None:
        THRESHOLDS["period"] = args.period_threshold
    if args.quarterly_threshold is not None:
        THRESHOLDS["quarterly"] = args.quarterly_threshold

    if args.run_all:
        args.rollups = True
        args.index = True
        args.sentinel = True
        args.amendment_sweep = True

    if args.sentinel_quick:
        log("\n🛡️ Sentinel (quick)")
        run_sentinel_quick(args.sentinel_agents, args.sentinel_window_hours, dry_run=args.dry_run)

    if args.check_due:
        log("\n🧮 Due status")
        for a in args.agents:
            stats, due = rollup_due_map(a)
            log(f"- {a}: sessions={stats.sessions}, weekly={stats.weeklies}, period={stats.periods}, quarterly={stats.quarterlies} | due: {due}")
        return

    # Order: rollups → index → sentinel
    if args.rollups:
        log("\n📚 Rollups")
        # Allow --no-archive passthrough via env for the writer
        if args.no_archive:
            os.environ["XI_NO_ARCHIVE"] = "1"
        do_rollups(args.agents, dry_run=args.dry_run)
        # Placeholder call (disabled by default):
        # run([sys.executable, "-m", "abilities.sentinel.sentinel_validate_rollup", "--auto", "--strict", "--fix"],
        #     cwd=XI_ROOT, dry_run=args.dry_run)

    # 🧷 sweep here so overlays exist before we index
    if args.amendment_sweep:
        log("\n🧷 Amendments")
        sweep_amendments(dry_run=args.dry_run)

    if args.index:
        log("\n🧾 Indexing")
        run_indexer_once(dry_run=args.dry_run)
        ensure_daemon_running(dry_run=args.dry_run)

    if args.sweep_sandbox:
        log("\n🧹 Sandbox retention")
        sweep_sandbox(days=args.sweep_days)

    if args.sentinel_ai_consume:
        log("\n🤖 Sentinel AI consumer")
        rc = run([sys.executable, "-m", "abilities.sentinel.sentinel_ai_task_runner"], cwd=XI_ROOT, dry_run=args.dry_run)
        log("✅ AI task consumed" if rc == 0 else f"❌ AI consumer failed with code {rc}")

    if not any([args.rollups, args.index, args.sentinel, args.run_all, args.check_due]):
        log("(nothing selected; use --run-all or see --help)")

def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if args.watch and args.watch > 0:
        log(f"⏱️ Watching every {args.watch}s — press Ctrl+C to stop")
        try:
            while True:
                run_once(args)
                time.sleep(args.watch)
        except KeyboardInterrupt:
            log("👋 Exiting watch loop")
            return 0
    else:
        run_once(args)
        return 0


if __name__ == "__main__":
    sys.exit(main())

