#!/usr/bin/env python3
"""
Index the latest *session* journal for an agent across all *_journal folders.

Usage:
  python3 tools/index_latest.py --agent davinci
  python3 tools/index_latest.py --agent hermes --force
  python3 tools/index_latest.py --agent davinci --journal-type idea_generator_journal
"""
import argparse, subprocess, sys, os, glob

def find_latest_session(agent: str, journal_type: str | None):
    root = os.path.join("memory", agent)
    candidates = []
    if journal_type:
        session_dir = os.path.join(root, journal_type, "session")
        candidates.extend(glob.glob(os.path.join(session_dir, f"{agent}_session_journal_*.json")))
    else:
        for jt in glob.glob(os.path.join(root, "*_journal")):
            session_dir = os.path.join(jt, "session")
            candidates.extend(glob.glob(os.path.join(session_dir, f"{agent}_session_journal_*.json")))
    if not candidates:
        return None, None
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    latest = candidates[0]
    # infer journal_type from path
    parts = latest.split(os.sep)
    # memory/{agent}/{journal_type}/session/file.json
    jt = parts[2] if len(parts) >= 5 else None
    return latest, jt

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--journal-type", help="e.g. idea_generator_journal; default = search all *_journal")
    ap.add_argument("--force", action="store_true", help="pass through to memory_indexer")
    args = ap.parse_args()

    latest, jt = find_latest_session(args.agent, args.journal_type)
    if not latest:
        print(f"❌ No session journals found for agent={args.agent}"
              + (f" in journal_type={args.journal_type}" if args.journal_type else ""))
        sys.exit(1)

    print(f"🧭 Latest session for {args.agent}"
          + (f" in {jt}" if jt else "")
          + f":\n    {latest}")

    cmd = [
        sys.executable, "-m", "abilities.common_abilities.memory_indexer",
        "--file", latest,
        "--agent", args.agent,
    ]
    if args.force:
        cmd.append("--force")

    print("▶️  Running:", " ".join(cmd))
    try:
        res = subprocess.run(cmd, check=False, text=True, capture_output=True)
        if res.stdout:
            print(res.stdout.rstrip())
        if res.returncode != 0:
            if res.stderr:
                print(res.stderr.rstrip(), file=sys.stderr)
            sys.exit(res.returncode)
    except FileNotFoundError:
        print("❌ Could not run memory_indexer. Are you in the repo root? Is the venv active?")
        sys.exit(2)

if __name__ == "__main__":
    main()

