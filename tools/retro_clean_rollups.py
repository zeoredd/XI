#!/usr/bin/env python3
import json, sys
from pathlib import Path
import subprocess

AGENT = "davinci"
JOURNAL_TYPE = "idea_generator_journal"
TIERS = ["weekly", "period", "quarterly"]

def normalize_file(fp: Path):
    with open(fp, "r", encoding="utf-8") as f:
        d = json.load(f)

    def norm_text(v):
        if v is None: return ""
        if isinstance(v, str): return v
        if isinstance(v, list): return "\n".join(str(x) for x in v if str(x).strip())
        return str(v)

    d["thoughts"] = norm_text(d.get("thoughts"))
    d["decisions"] = norm_text(d.get("decisions"))

    # rebuild lists
    d["decisions_list"] = [
        ln.lstrip("-*• ").strip()
        for ln in d["decisions"].splitlines()
        if ln.strip()
    ]
    d["key_thoughts"] = [
        ln.lstrip("-*• ").strip()
        for ln in d["thoughts"].splitlines()
        if ln.strip()
    ]

    with open(fp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    print(f"🧹 Cleaned {fp}")

    # reindex
    subprocess.run([
        sys.executable, "-m", "abilities.common_abilities.memory_indexer",
        "--file", str(fp), "--agent", AGENT, "--mem-only", "--force"
    ], check=True)

def main():
    base = Path("memory") / AGENT / JOURNAL_TYPE
    for tier in TIERS:
        for fp in (base / tier).glob("*.json"):
            normalize_file(fp)

if __name__ == "__main__":
    main()

