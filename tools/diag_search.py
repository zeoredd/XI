#!/usr/bin/env python3
# Pretty-print last N search controller runs.
import json, argparse
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--last", type=int, default=30)
    args = ap.parse_args()

    p = Path("logs/search_runs.jsonl")
    if not p.exists():
        print("No logs found at logs/search_runs.jsonl"); return

    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = rows[-args.last:]
    print(f"{'ts':>10}  {'conf':>5}  {'engine':<12}  {'ms':>6}  {'budget':>6}  query")
    for r in rows:
        print(f"{r.get('ts',0):>10}  {float(r.get('confidence',0)):>5.2f}  {str(r.get('winner_engine')):<12}  "
              f"{int(r.get('elapsed_ms',0)):>6}  {int(r.get('budget_used_tokens',0)):>6}  {r.get('query','')[:60]}")

if __name__ == "__main__":
    main()

