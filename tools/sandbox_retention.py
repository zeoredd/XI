# tools/sandbox_retention.py
from pathlib import Path
import json, time, argparse, shutil

def load_manifest(p: Path) -> dict:
    try:
        return json.loads((p / "manifest.json").read_text())
    except Exception:
        return {}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="sandbox", help="sandbox root")
    ap.add_argument("--agent", default=None, help="only sweep this agent")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=7, help="delete sessions older than this many days if unpromoted")
    args = ap.parse_args()

    cutoff = time.time() - args.days * 86400
    root = Path(args.root)

    agents = [args.agent] if args.agent else [p.name for p in root.iterdir() if p.is_dir()]
    deleted = 0

    for agent in agents:
        sessions_dir = root / agent / "sessions"
        if not sessions_dir.exists():
            continue
        for sdir in sessions_dir.iterdir():
            if not sdir.is_dir():
                continue
            m = load_manifest(sdir)
            created = m.get("created_at", 0)
            promotions = m.get("promotions", [])
            if created and created < cutoff and not promotions:
                if args.dry_run:
                    print(f"would delete {sdir}")
                else:
                    shutil.rmtree(sdir, ignore_errors=True)
                    deleted += 1

    print(f"deleted {deleted} sessions")

if __name__ == "__main__":
    main()

