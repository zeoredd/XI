#!/usr/bin/env python3
"""
🧩 memory_amendments.py — propose/accept/reject/apply/sweep memory claim amendments

Folders:
  memory/amendments/pending/
  memory/amendments/accepted/
  memory/amendments/rejected/
  memory/amendments/applied/

Amendment JSON (applied) example:
{
  "amendment_id": "uuid",
  "applied_at": "2025-08-13T16:05:00Z",
  "applied_by": "user|hermes|davinci|sentinel",
  "reason": "preference_flip|numeric_update|deprecate|merge",
  "subject": "brandon",
  "predicate": "likes_color",
  "object": "red",
  "claim_id_old": "uuid-older",
  "claim_id_new": "uuid-newer",
  "action": {"status": "superseded", "time_end": "...", "superseded_by": "uuid-newer"}
}
"""

from __future__ import annotations
import argparse, json, os, sys, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

# 🧭 project root bootstrap (so `-m` imports work everywhere)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # points at XI/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

XI_ROOT = _PROJECT_ROOT
AMEND_ROOT = XI_ROOT / "memory" / "amendments"
PENDING   = AMEND_ROOT / "pending"
ACCEPTED  = AMEND_ROOT / "accepted"
REJECTED  = AMEND_ROOT / "rejected"
APPLIED   = AMEND_ROOT / "applied"

for d in (PENDING, ACCEPTED, REJECTED, APPLIED):
    d.mkdir(parents=True, exist_ok=True)

# =============================================================================
# 🔧 Utils
# =============================================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def glob_json(folder: Path) -> List[Path]:
    return sorted(p for p in folder.glob("*.json") if p.is_file())

def short_id() -> str:
    return uuid.uuid4().hex

# =============================================================================
# 🧠 Commands
# =============================================================================

def cmd_propose(args: argparse.Namespace) -> int:
    """
    Agents call this to propose an amendment when they detect a change.
    Minimal inputs; you can include claim IDs if known.
    """
    aid = args.amendment_id or short_id()
    payload = {
        "amendment_id": aid,
        "created_at": now_iso(),
        "status": "pending",
        "proposed_by": args.proposed_by,
        "reason": args.reason,
        "subject": args.subject,
        "predicate": args.predicate,
        "object": args.object,
        "previous_claim": {
            "claim_id": args.claim_id_old or "",
            "time_start": args.old_time_start or "",
            "polarity": args.old_polarity,
        },
        "new_claim": {
            "claim_id": args.claim_id_new or "",
            "time_start": args.new_time_start or now_iso(),
            "polarity": args.new_polarity,
        },
        "actions": [
            {"op": "set_time_end", "claim_id": args.claim_id_old or "", "value": args.new_time_start or now_iso()},
            {"op": "link_superseded_by", "from": args.claim_id_old or "", "to": args.claim_id_new or ""},
            {"op": "set_status", "claim_id": args.claim_id_old or "", "value": "superseded"},
        ],
        "confidence": float(args.confidence),
        "ripple_effects": {
            "update_summaries": [],
            "notes": args.notes or ""
        }
    }
    out = PENDING / f"amendment_{aid}.json"
    write_json(out, payload)
    print(f"✅ Proposed → {out}")
    return 0

def _move(src_folder: Path, dst_folder: Path, aid: str, new_status: str) -> Path:
    matches = [p for p in glob_json(src_folder) if p.stem.endswith(aid)]
    if not matches:
        raise FileNotFoundError(f"Amendment ID not found in {src_folder}: {aid}")
    src = matches[0]
    data = load_json(src)
    data["status"] = new_status
    data["updated_at"] = now_iso()
    dst = dst_folder / src.name
    write_json(dst, data)
    src.unlink(missing_ok=True)
    return dst

def cmd_accept(args: argparse.Namespace) -> int:
    dst = _move(PENDING, ACCEPTED, args.id, "accepted")
    print(f"👍 Accepted → {dst}")
    return 0

def cmd_reject(args: argparse.Namespace) -> int:
    dst = _move(PENDING, REJECTED, args.id, "rejected")
    print(f"👎 Rejected → {dst}")
    return 0

def _apply_one(path: Path, applied_by: str) -> Path:
    data = load_json(path)
    aid = data.get("amendment_id", short_id())
    # Build the overlay we want indexer/search to respect
    overlay = {
        "amendment_id": aid,
        "applied_at": now_iso(),
        "applied_by": applied_by,
        "reason": data.get("reason", ""),
        "subject": data.get("subject", ""),
        "predicate": data.get("predicate", ""),
        "object": data.get("object", ""),
        "claim_id_old": data.get("previous_claim", {}).get("claim_id", ""),
        "claim_id_new": data.get("new_claim", {}).get("claim_id", ""),
        "action": {
            "status": "superseded",
            "time_end": data.get("new_claim", {}).get("time_start", now_iso()),
            "superseded_by": data.get("new_claim", {}).get("claim_id", "")
        }
    }
    out = APPLIED / f"amendment_{aid}.json"
    write_json(out, overlay)

    # also mark source record as applied
    data["status"] = "applied"
    data["applied_at"] = overlay["applied_at"]
    write_json(path, data)
    print(f"🧷 Applied overlay → {out}")
    return out

def cmd_apply(args: argparse.Namespace) -> int:
    # Accept + apply from pending, or apply from accepted
    srcs = glob_json(PENDING) + glob_json(ACCEPTED)
    target = None
    for p in srcs:
        if p.stem.endswith(args.id):
            target = p
            break
    if not target:
        print(f"⚠️ Not found in pending/accepted: {args.id}")
        return 1
    if target.parent == PENDING:
        # move to accepted first
        target = _move(PENDING, ACCEPTED, args.id, "accepted")
    _apply_one(target, args.applied_by)
    return 0

def cmd_list(args: argparse.Namespace) -> int:
    folder = {"pending": PENDING, "accepted": ACCEPTED, "rejected": REJECTED, "applied": APPLIED}[args.status]
    files = glob_json(folder)
    if not files:
        print("(empty)")
        return 0
    for p in files:
        data = load_json(p)
        print(f"- {p.name} | reason={data.get('reason')} | subj={data.get('subject')} "
              f"| pred={data.get('predicate')} | obj={data.get('object')} | status={data.get('status','(n/a)')}")
    return 0

def cmd_sweep(args: argparse.Namespace) -> int:
    """
    Move all ACCEPTED (not yet applied) to APPLIED overlays.
    Use from routines_orchestrator after rollups or on --run-all.
    """
    count = 0
    for p in glob_json(ACCEPTED):
        data = load_json(p)
        if data.get("status") != "applied":
            _apply_one(p, applied_by=args.applied_by)
            count += 1
    print(f"🧹 Sweep complete: applied={count}")
    return 0

# =============================================================================
# 🖥️ CLI
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Memory amendments manager")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("propose", help="Propose an amendment")
    sp.add_argument("--proposed-by", required=True)
    sp.add_argument("--reason", required=True, choices=["preference_flip","numeric_update","deprecate","merge"])
    sp.add_argument("--subject", required=True)
    sp.add_argument("--predicate", required=True)
    sp.add_argument("--object", default="")
    sp.add_argument("--claim-id-old", dest="claim_id_old")
    sp.add_argument("--claim-id-new", dest="claim_id_new")
    sp.add_argument("--old-time-start")
    sp.add_argument("--new-time-start")
    sp.add_argument("--old-polarity", type=int, default=1)  # 1 or -1
    sp.add_argument("--new-polarity", type=int, default=-1) # 1 or -1
    sp.add_argument("--confidence", default="0.90")
    sp.add_argument("--notes", default="")
    sp.add_argument("--amendment-id")
    sp.set_defaults(func=cmd_propose)

    sa = sub.add_parser("accept", help="Accept a pending amendment")
    sa.add_argument("--id", required=True)
    sa.set_defaults(func=cmd_accept)

    sr = sub.add_parser("reject", help="Reject a pending amendment")
    sr.add_argument("--id", required=True)
    sr.set_defaults(func=cmd_reject)

    sa2 = sub.add_parser("apply", help="Apply (accept if needed) and write overlay")
    sa2.add_argument("--id", required=True)
    sa2.add_argument("--applied-by", default="user")
    sa2.set_defaults(func=cmd_apply)

    sl = sub.add_parser("list", help="List amendments by status")
    sl.add_argument("--status", choices=["pending","accepted","rejected","applied"], default="pending")
    sl.set_defaults(func=cmd_list)

    ss = sub.add_parser("sweep", help="Apply all accepted overlays")
    ss.add_argument("--applied-by", default="routines_orchestrator")
    ss.set_defaults(func=cmd_sweep)

    return p

def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())

