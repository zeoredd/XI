#!/usr/bin/env python3
"""
🛡️ sentinel_routine_audit.py
Scans recent journal entries across all agents, verifies signatures,
and compares summary text to thread truth. Flags any issues.
"""

import os
import json
from pathlib import Path
import datetime as _dt
import subprocess
import sys
import argparse
from collections import defaultdict
sys.path.append(str(Path(__file__).resolve().parents[2]))

# Load .env so subprocess runs see DB creds even if the parent shell didn't export them
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

def configure_from_flags():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Light audit: recent window, skip heavy steps")
    parser.add_argument("--ai-task", action="store_true",
        help="Emit GAEL-friendly prompt + JSON bundle for AI-assisted audit (writes to memory/sentinel/ai_tasks/)")
    parser.add_argument("--ai-apply-lowrisk", action="store_true",
        help="Immediately convert low-risk conflicts (e.g., missing trio fields) into overlays")
    parser.add_argument("--ai-edgecases", action="store_true",
        help="AI-assisted audit for nuanced/ambiguous conflicts only (emits a GAEL task bundle)")
    parser.add_argument("--window-hours", type=int, default=2, help="Window in hours for quick mode")
    parser.add_argument("--agents", nargs="*", default=None, help="Limit to these agents")
    parser.add_argument("--reindex", nargs='?', const='deferred', choices=['deferred'], default=None,
                        help="Queue the audit journal for embedding later (deferred).")
    # NEW: fast, single-file inline amend path
    parser.add_argument("--file", help="Run Sentinel amendments for this single journal file")
    parser.add_argument("--apply", action="store_true", help="Apply overlays immediately (inline) for this file")

    args = parser.parse_args()

    cfg = {}
    if args.quick:
        cfg["cutoff"] = _dt.datetime.now() - _dt.timedelta(hours=args.window_hours)
        cfg["RUN_SUMMARY_LAYER"] = False
        cfg["RUN_AXIOMS"] = False
        cfg["USE_MODEL_FALLBACK"] = False
    else:
        cfg["cutoff"] = _dt.datetime.now() - _dt.timedelta(days=7)
        cfg["RUN_SUMMARY_LAYER"] = True
        cfg["RUN_AXIOMS"] = True
        cfg["USE_MODEL_FALLBACK"] = True

    cfg["agents"] = args.agents
    cfg["REINDEX_MODE"] = args.reindex  # <— add this
    cfg["FILE"] = args.file
    cfg["APPLY"] = bool(args.apply)
    cfg["AI_TASK"] = bool(args.ai_task)
    cfg["AI_EDGECASES"] = bool(getattr(args, "ai_edgecases", False))
    if getattr(args, "ai_apply_lowrisk", False):
        os.environ["XI_SENTINEL_AI_APPLY_LOWRISK"] = "1"
    cfg["_args"] = args  # keep raw for later optional checks
    return cfg

from typing import List, Dict, Tuple, Optional
from abilities.sentinel.claim_extractor import extract_claims_from_journal_file
from abilities.sentinel.conflict_detector import detect_conflicts
from abilities.sentinel.result_formatter import to_validator_flags, build_integrity_report
from abilities.common_abilities.claim_types import Claim, Conflict, claim_key
from abilities.sentinel.sentinel_contextual_validator import validate_contextual_alignment
from abilities.sentinel.thread_memory_validator import (
    validate_session_journal_against_thread,
    run_all_validations
)
import hashlib

def _enqueue_reindex(path: str):
    try:
        os.makedirs("runtime", exist_ok=True)
        with open("runtime/reindex_queue.txt", "a", encoding="utf-8") as f:
            f.write(str(path) + "\n")
    except Exception as e:
        print(f"⚠️ Failed to enqueue reindex for {path}: {e}")


# 📁 Config
AGENTS = ["davinci", "hermes", "sentinel"]
JOURNAL_TYPES = [
    "idea_generator_journal",
    "daily_briefing_and_strategy_journal",
    "personal_and_group_conversation_journal"
]
AUDIT_WINDOW_DAYS = 7
AUDIT_LOG_DIR = Path("memory/sentinel/sentinel_audit_journal/session")
AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
# 🧭 Config (adjust paths as needed)
JOURNAL_GLOBS = [
    "memory/*/*_journal/session/*.json",
    # Optionally include week/period/quarter scans too
    # "memory/*/*_journal/weekly/*.json",
    # "memory/*/*_journal/period/*.json",
    # "memory/*/*_journal/quarterly/*.json",
]

OVERLAYS_DIR = Path("memory/amendments/applied")

# 📜 Load axioms (normalize IDs)
import hashlib

axioms_path = Path("memory/sentinel/permanent_memory.json")
axioms = []
if axioms_path.exists():
    try:
        data = json.loads(axioms_path.read_text())
        raw_axioms = (data.get("core_beliefs") or {}).get("axioms", [])
        axioms = []
        for i, ax in enumerate(raw_axioms, start=1):
            ax = dict(ax)
            if "id" not in ax:
                # AXIOM-### based on position, plus a stable hash for provenance
                h = hashlib.sha1(ax.get("statement", "").encode("utf-8")).hexdigest()[:8]
                ax["id"] = f"AXIOM-{i:03d}-{h}"
            axioms.append(ax)
        print(f"📜 Loaded {len(axioms)} axioms from permanent memory.")
    except Exception as e:
        print(f"❌ Error loading axioms: {e}")
else:
    print("⚠️ No permanent_memory.json found for sentinel.")

# when calling validate_contextual_alignment(...)
# pass a flag to skip fallback or rely on global USE_MODEL_FALLBACK
# simplest: just don't call the fallback when quick (you already wrapped it)


# 🧠 Track audit results
audit_summary = {
    "date": _dt.datetime.now().strftime("%Y-%m-%d"),
    "agent": "sentinel",
    "thread_type": "audit",
    "verified_files": 0,
    "signature_failures": 0,
    "conflicts_detected": [],
    "corrections_made": [],
    "thread_discrepancies": [],
    "summary_issues": [],
    "written_by": "sentinel",
    "axioms_checked": [a.get("id") for a in axioms]
}

# 🧪 Function to verify a journal + .minisig file
from typing import Tuple

def emit_ai_task(conflicts: list, summary_reports: list, out_dir: Path) -> Path:
    """
    Write a GAEL-ready task bundle for the sentinel agent:
      - ai_task.md : markdown task + context
      - ai_payload.json : machine-readable conflicts + validators
    Returns the path to the task folder.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    task_dir = out_dir / f"sentinel_ai_task_{stamp}"
    task_dir.mkdir(parents=True, exist_ok=True)

    # Minimal, safe serialization
    payload = {
        "conflicts": conflicts or [],
        "summary_reports": summary_reports or [],
        "guidance": {
            "goal": "Review protocol findings. Explain issues briefly. "
                    "Propose precise overlays or amendments. Keep changes minimal and reversible.",
            "constraints": [
                "Prefer overlay-style amendments over destructive edits",
                "Flag any uncertain cases instead of applying",
                "Preserve GAEL trio (summary, thoughts, decisions) integrity"
            ]
        }
    }
    (task_dir / "ai_payload.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md = []
    md.append("# Sentinel AI-Assist Task")
    md.append("")
    md.append("**Objective:** Review protocol audit results and recommend safe, minimal amendments.")
    md.append("")
    md.append("**Instructions:**")
    md.append("- Read `ai_payload.json` for conflicts & validator reports.")
    md.append("- For each conflict, propose: keep / overlay_fix / escalate.")
    md.append("- If proposing overlay_fix, specify subject, predicate, object (nullable), and rationale.")
    md.append("")
    md.append("**Deliverables:**")
    md.append("- `ai_decisions.json` with a list of decisions. Example item:")
    md.append("  ```json")
    md.append('  {"action":"overlay_fix","subject":"entry.uuid","predicate":"verified","object":true,"reason":"Validated against sources."}')
    md.append("  ```")
    (task_dir / "ai_task.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"📝 AI task emitted at {task_dir}")
    return task_dir

def _is_low_risk(conflict) -> bool:
    code = getattr(conflict, "code", None) if not isinstance(conflict, dict) else conflict.get("code")
    return code in {"missing_trio_fields", "empty_summary", "short_thoughts", "short_decisions"}

# 🎯 Nuanced/ambiguous buckets for AI-assisted night audits
NUANCED_CODES = {
    "borderline_similarity",
    "language_ambiguity",
    "source_mismatch_minor",
    "paraphrase_conflict",
    "temporal_ambiguity",
    "tone_vs_fact_mismatch",
}

def _is_nuanced(conflict) -> bool:
    code = getattr(conflict, "code", None) if not isinstance(conflict, dict) else conflict.get("code")
    return code in NUANCED_CODES

def verify_signature(journal_path: Path) -> tuple[bool, str]:
    sig_path = journal_path.with_suffix(journal_path.suffix + ".minisig")
    if not sig_path.exists():
        return False, "missing"   # distinguish missing vs invalid

    agent = journal_path.name.split("_")[0]
    pubkey = Path("keys") / f"{agent}_keys" / f"public_{agent}.txt"
    if not pubkey.exists():
        return False, "missing_key"

    result = subprocess.run(["minisign","-Vm",str(journal_path),"-x",str(sig_path),"-p",str(pubkey)], capture_output=True)
    return (result.returncode == 0, "ok" if result.returncode == 0 else "invalid")

# 🔧 Focused single-file amendment runner (fast path for flows)
def run_amendments_for_file(target: Path, apply: bool = False) -> list[dict]:
    """
    Extract claims from `target`, compare against current state, detect conflicts,
    and (optionally) write overlays into memory/amendments/applied/.
    Returns a list of detected issues/conflicts (dicts).
    """
    # 1) Extract newest claims from the target journal
    agent = target.parts[1] if len(target.parts) > 1 else "unknown"
    journal_type = target.parts[2] if len(target.parts) > 2 else "session"
    claims = extract_claims_from_journal_file(str(target), agent, journal_type) or []

    # 2) Load current-state claims from DB (non-superseded)
    index_rows = load_index_rows_current()
    prior_by_key = build_prior_by_key_from_index(index_rows)

    # 3) Load existing overlays (so we don't duplicate)
    overlays_map = load_overlays_map()

    # 4) Detect conflicts (new vs prior)
    conflicts = detect_conflicts(claims, prior_by_key, overlays_map)

    # 5) Optionally write overlays for each conflicting key
    if apply and conflicts:
        OVERLAYS_DIR.mkdir(parents=True, exist_ok=True)

        def _get(x, key, default=None):
            # Support dataclass/obj (attributes) and dicts
            try:
                return x.get(key, default)  # dict path
            except AttributeError:
                return getattr(x, key, default)  # object/dataclass path

        for c in conflicts:
            # Minimal overlay payload (normalize empty string → None for object)
            subj = (_get(c, "subject") or "").strip()
            pred = (_get(c, "predicate") or "").strip()
            obj  = _get(c, "object", None)
            obj  = (obj if (obj is not None and str(obj).strip() != "") else None)

            key_str = f"{subj}|{pred}|{obj or ''}"
            h = hashlib.sha1(key_str.encode("utf-8")).hexdigest()[:10]

            out = {
                "subject": subj,
                "predicate": pred,
                "object": obj,
                "status": "applied",
                "source": str(target),
                "timestamp": _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00","Z"),
                "details": {
                    "code": _get(c, "code"),
                    "resolution": "overlay"
                }
            }
            (OVERLAYS_DIR / f"overlay_{h}.json").write_text(
                json.dumps(out, indent=2), encoding="utf-8"
            )
    return conflicts

# 🔍 Scan all agent journal files
for agent in AGENTS:
    for journal_type in JOURNAL_TYPES:
        journal_dir = Path(f"memory/{agent}/{journal_type}/session")
        if not journal_dir.exists():
            continue

        for file in journal_dir.glob(f"{agent}_session_journal_*.json"):
            try:
                modified = _dt.datetime.fromtimestamp(file.stat().st_mtime)
                if modified < cutoff:
                    continue

                audit_summary["verified_files"] += 1

                # Signature check (distinguish missing vs invalid vs missing_key)
                ok, reason = verify_signature(file)
                if not ok:
                    if reason == "invalid":
                        audit_summary["signature_failures"] += 1  # count only true invalids
                        issue_text = "Signature present but INVALID"
                    elif reason == "missing_key":
                        issue_text = "Public key missing for agent"
                    else:  # "missing"
                        issue_text = "Signature file (.minisig) missing"

                    audit_summary["conflicts_detected"].append({
                        "agent": agent,
                        "file": str(file),
                        "issue": issue_text,
                        "action_taken": "flagged"
                    })
                    continue

                # ✅ Journal-to-thread validation
                date_str   = file.stem.split("_")[-1]
                date_core  = date_str.split("-")[0].split("_")[0]  # handles 20250809_7 → 20250809
                thread_base = journal_type.replace("_journal", "")

                candidate = Path(f"memory/threads/{thread_base}/session/{thread_base}_session_{date_core}.json")
                if not candidate.exists():
                    thread_dir = Path(f"memory/threads/{thread_base}/session")
                    matches = sorted(thread_dir.glob(f"{thread_base}_session_{date_core}*.json"))
                    thread_file = matches[0] if matches else None
                else:
                    thread_file = candidate

                # ⛔ Skip missing threads gracefully
                if not thread_file or not thread_file.exists():
                    audit_summary["thread_discrepancies"].append({
                        "agent": agent,
                        "journal": str(file),
                        "thread": str(candidate),
                        "status": "skipped",
                        "reason": "Missing thread file for this date/id"
                    })
                    continue  # Move to next journal file

                session_result = validate_session_journal_against_thread(file, thread_file)

                if session_result["status"] != "pass":
                    audit_summary["thread_discrepancies"].append(session_result)

                # 🧠 Contextual validation (sentence-level drift check)
                try:
                    date_str = file.stem.split("_")[-1]
                    date_core = date_str.split("-")[0].split("_")[0]
                    thread_base = journal_type.replace("_journal", "")
                    candidate = Path(f"memory/threads/{thread_base}/session/{thread_base}_session_{date_core}.json")
                    thread_file = candidate if candidate.exists() else next(
                        iter(sorted(Path(f"memory/threads/{thread_base}/session").glob(f"{thread_base}_session_{date_core}*.json"))),
                        None
                    )

                    if thread_file.exists():
                        context_result = validate_contextual_alignment(thread_file, file)
                        if context_result.get("entries"):
                            audit_summary.setdefault("contextual_issues", []).append(context_result)

                except Exception as e:
                    audit_summary.setdefault("contextual_issues", []).append({
                        "agent": agent,
                        "journal_file": str(file),
                        "issue": f"Contextual validator failed: {str(e)}"
                    })

            except Exception as e:
                audit_summary["conflicts_detected"].append({
                    "agent": agent,
                    "file": str(file),
                    "issue": f"Audit error: {str(e)}",
                    "action_taken": "skipped"
                })

    # 🔁 Run weekly + period summary checks per agent
    summary_result = run_all_validations(agent)
    # print(summary_result)  # noisy
    if summary_result and summary_result.get("validations"):
        failures = sum(1 for v in summary_result["validations"] if v.get("status") not in ("pass", "skipped"))
        print(f"🔎 {agent}: validations={len(summary_result['validations'])}, non-pass={failures}")
        audit_summary["summary_issues"].append(summary_result)

# 🧩 Run summary layer hierarchy check
try:
    subprocess.run(["python3", "abilities/sentinel/summary_layer_validator.py"], check=True)
except Exception as e:
    audit_summary["conflicts_detected"].append({
        "agent": "sentinel",
        "file": "summary_layer_validator.py",
        "issue": f"Failed to run summary layer validator: {str(e)}",
        "action_taken": "skipped"
    })

# 📥 Load overlays as a map keyed by (subject, predicate, object)
def load_overlays_map() -> Dict[Tuple[str, str, Optional[str]], Dict]:
    overlays: Dict[Tuple[str, str, Optional[str]], Dict] = {}
    if not OVERLAYS_DIR.exists():
        return overlays

    for f in OVERLAYS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        s = data.get("subject")
        p = data.get("predicate")
        o = data.get("object", None)

        # 🔑 normalize: treat empty string as None so keys match claim_key(...)
        if isinstance(o, str) and o.strip() == "":
            o = None

        if s and p:
            overlays[(s, p, o)] = data
    return overlays


# 🗄️ Fetch prior claims (you can swap to DB-backed queries)
def build_prior_by_key_from_index(index_rows: List[Dict]) -> Dict[Tuple[str, str, Optional[str]], List[Claim]]:
    """Reconstruct prior claims keyed by (subject, predicate, object) → List[Claim]."""
    prior: Dict[Tuple[str, str, Optional[str]], List[Claim]] = {}
    built_count = 0
    for r in (index_rows or []):
        obj = r.get("object") if "object" in r else None
        c = Claim(
            subject=r.get("subject"),
            predicate=r.get("predicate"),
            object=obj,
            value=r.get("value"),
            claim_id=r.get("claim_id"),
            file_path=r.get("file_path"),
            agent=r.get("agent"),
            journal_type=r.get("journal_type"),
            timestamp=r.get("timestamp"),
            status=r.get("status") if "status" in r else "current",  # temporary "if" until wipe clean Sept 18 2025
        )
        key = (c.subject, c.predicate, c.object)
        prior.setdefault(key, []).append(c)
        built_count += 1
    print(f"✅ Built {built_count} prior claims from baseline")
    return prior

def load_index_rows_current() -> List[Dict]:
    import psycopg2, os
    DB_CONFIG = {
        "dbname": os.getenv("POSTGRES_DB", "xi_memory"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "yourpassword"),
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
        "connect_timeout": int(os.getenv("POSTGRES_CONNECT_TIMEOUT", "5")),
    }

    rows: List[Dict] = []
    try:
        with psycopg2.connect(**DB_CONFIG) as conn:
            # optional: keep it simple for read-only baseline
            conn.autocommit = True
            with conn.cursor() as cur:
                query = """
                    SELECT subject, predicate, value,
                           claim_id, file_path, agent, journal_type, "timestamp",
                           COALESCE(status,'current') AS status
                    FROM memory_claims
                    WHERE COALESCE(status,'current') <> 'superseded'
                """
                cur.execute(query)
                cols = [d.name for d in cur.description]
                for rec in cur.fetchall():
                    rows.append({k: v for k, v in zip(cols, rec)})
        print(f"✅ DB baseline fetch returned {len(rows)} rows (db={DB_CONFIG['dbname']})")
    except psycopg2.Error as e:
        # Best-effort message: prefer pgerror, then diag.message_primary, then str(e)
        code = getattr(e, "pgcode", None)
        diag = getattr(e, "diag", None)
        primary = getattr(diag, "message_primary", None) if diag else None
        msg = getattr(e, "pgerror", None) or primary or str(e) or repr(e)
        print(
            f"⚠️ DB baseline fetch failed (user={DB_CONFIG['user']} host={DB_CONFIG['host']} "
            f"port={DB_CONFIG['port']} db={DB_CONFIG['dbname']} code={code}): {msg}"
        )
    except Exception as e:
        print(
            f"⚠️ Unexpected error during DB baseline fetch (db={DB_CONFIG['dbname']}): {e}"
        )
    return rows

# 🚀 Main audit entrypoint
def run_sentinel_conflict_scan() -> Dict:
    # 1) Extract new claims from journals
    new_claims: List[Claim] = []
    for pattern in JOURNAL_GLOBS:
        for f in Path(".").glob(pattern):
            # Derive agent/journal_type from path (e.g., memory/davinci/session/...)
            parts = f.parts
            agent = parts[1] if len(parts) > 1 else "unknown"
            journal_type = parts[3] if len(parts) > 3 else "session"

            try:
                # Extract and normalize timestamps
                claims = extract_claims_from_journal_file(str(f), agent, journal_type)
                if claims:
                    try:
                        # Use file mtime as an ISO timestamp if claim has only a date or missing timestamp
                        mtime_iso = _dt.datetime.fromtimestamp(f.stat().st_mtime, _dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    except Exception:
                        mtime_iso = None

                    for c in claims:
                        # If timestamp missing or looks like YYYY-MM-DD without time, enrich with file mtime
                        if not getattr(c, "timestamp", None) or (isinstance(c.timestamp, str) and len(c.timestamp) == 10):
                            if mtime_iso:
                                c.timestamp = mtime_iso
                new_claims.extend(claims)
            except Exception:
                # non-fatal; continue
                pass

    from collections import defaultdict
    def keep_newest_per_key(claims: List[Claim]) -> List[Claim]:
        latest = {}
        for c in claims:
            key = (c.subject, c.predicate, c.object)
            ts = c.timestamp or "1970-01-01T00:00:00Z"  # ISO-like, string compare is fine
            prev = latest.get(key)
            if not prev or ts > prev[0]:
                latest[key] = (ts, c)
        return [pair[1] for pair in latest.values()]
        
    new_claims = keep_newest_per_key(new_claims)

    # 2) Load prior current-state claims from index (DB)
    index_rows = load_index_rows_current()
    prior_by_key = build_prior_by_key_from_index(index_rows)

    # 🔁 Fallback: if DB returned no priors for a key, derive priors from the journals we just scanned
    if not prior_by_key:
        
        grouped = defaultdict(list)
        for c in new_claims:
            ts = c.timestamp or "1970-01-01T00:00:00Z"
            grouped[(c.subject, c.predicate, c.object)].append((ts, c))

        # For each key, sort newest→oldest; treat all but newest as "prior"
        for k, rows in grouped.items():
            rows.sort(key=lambda x: x[0], reverse=True)
            priors = [r[1] for r in rows[1:]]  # older entries become priors for the newest claim
            if priors:
                prior_by_key[k] = priors

    # 3) Load overlays
    overlays_map = load_overlays_map()

    # 4) Detect conflicts
    conflicts: List[Conflict] = detect_conflicts(new_claims, prior_by_key, overlays_map)

    # 5) Format outputs
    validator_flags = to_validator_flags(conflicts)
    integrity_report = build_integrity_report(conflicts)

    return {
        "validator_flags": validator_flags,
        "integrity_report": integrity_report
    }

def main():
    # Configure from flags
    cfg = configure_from_flags()
    global RUN_SUMMARY_LAYER, RUN_AXIOMS, USE_MODEL_FALLBACK, AGENTS
    RUN_SUMMARY_LAYER = cfg["RUN_SUMMARY_LAYER"]
    RUN_AXIOMS = cfg["RUN_AXIOMS"]
    USE_MODEL_FALLBACK = cfg["USE_MODEL_FALLBACK"]
    cutoff = cfg["cutoff"]
    if cfg["agents"]:
        AGENTS = cfg["agents"]
    # make cutoff visible where needed
    globals()["cutoff"] = cutoff

    # 🚀 Inline fast-path: if a single --file is provided, only amend that file and exit.
    if cfg.get("FILE"):
        target = Path(cfg["FILE"]).resolve()
        if not target.exists():
            print(f"❌ no such file: {target}", file=sys.stderr)
            sys.exit(2)
        try:
            issues = run_amendments_for_file(target, apply=cfg.get("APPLY", False))
            print(f"✅ sentinel_inline_amend: file={target} applied={cfg.get('APPLY', False)} issues={len(issues)}")
        except Exception as e:
            print(f"❌ sentinel_inline_amend failed for file={target}: {e}")
            sys.exit(1)
        # Skip heavy validations in inline mode
        return

    # Early conditional runs
    if RUN_SUMMARY_LAYER:
        subprocess.run(["python3", "abilities/sentinel/summary_layer_validator.py"], check=True)

    if RUN_AXIOMS:
        try:
            from abilities.sentinel.axiom_enforcer import enforce_axiom_004, enforce_axiom_003
            enforce_axiom_004()
            enforce_axiom_003()
        except Exception as e:
            print(f"⚠️ Axiom enforcement failed early: {e}")

    # >>> your existing audit logic above has already built `audit_summary` <<<

    # Run conflict scan and attach results
    result = run_sentinel_conflict_scan()
    audit_summary["conflict_scan"] = {
        "validator_flags": result.get("validator_flags", []),
        "integrity_report": result.get("integrity_report", {})
    }

    # quick console view
    flags = audit_summary.get("conflict_scan", {}).get("validator_flags", [])
    errs  = sum(1 for f in flags if f.get("severity") == "ERROR")
    warns = sum(1 for f in flags if f.get("severity") == "WARN")
    print(f"🧮 Conflict scan: flags={len(flags)}, ERROR={errs}, WARN={warns}")
    if flags:
        ff = flags[0]
        print("➡️ first_flag:", json.dumps({
            "code": ff.get("code"),
            "status": ff.get("status"),
            "target": ff.get("target"),
            "details": ff.get("details")
        }, indent=2))

    # Optionally emit AI tasks (full audit path only; inline --file exits earlier)
    if cfg.get("AI_TASK"):
        ai_dir = Path("memory/sentinel/ai_tasks")
        emit_ai_task(
            audit_summary.get("conflicts_detected", []),
            audit_summary.get("summary_issues", []),
            ai_dir
        )
    if cfg.get("AI_EDGECASES"):
        # Filter to nuanced conflicts only
        nuanced = [c for c in audit_summary.get("conflicts_detected", []) if _is_nuanced(c)]
        if nuanced or audit_summary.get("summary_issues"):
            ai_dir = Path("memory/sentinel/ai_tasks")
            emit_ai_task(nuanced, audit_summary.get("summary_issues", []), ai_dir)
            print(f"🕒 AI-edgecases task created with {len(nuanced)} nuanced items")

    # Write audit log
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = AUDIT_LOG_DIR / f"sentinel_session_journal_{stamp}.json"
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(audit_summary, f, indent=2)
    print(f"✅ Sentinel audit complete. Verified: {audit_summary['verified_files']}, Failures: {audit_summary['signature_failures']}")

    # Queue the audit journal for deferred reindex (GPU-safe)
    try:
        # unified: use cfg props
        if cfg.get("REINDEX_MODE") == "deferred":
            _enqueue_reindex(str(log_file))
            print(f"🗂️ Deferred reindex queued: {log_file}")
    except Exception as e:
        print(f"⚠️ Failed to queue reindex: {e}")

    # Trigger Sentinel journal writer
    flow_context = {
        "flow_type": "audit",
        "temp": 0.3,
        "model": "sentinel.fp16.1b",
        "context_length": 4096,
        "thread_refs": []
    }
    try:
        res = subprocess.run(
            ["python3", "abilities/sentinel/sentinel_write_session_journal.py", "--flow_context", json.dumps(flow_context)],
            capture_output=True,
            text=True
        )
        if res.returncode == 0:
            print("📝 Sentinel journal entry written after audit.")
        else:
            print(f"⚠️ Failed to write Sentinel journal (exit {res.returncode}).")
            if res.stderr:
                print(res.stderr.strip())
    except Exception as e:
        print(f"⚠️ Failed to write Sentinel journal: {e}")

if __name__ == "__main__":
    main()




