#!/usr/bin/env python3
"""
⚠️ raise_memory_conflict.py
Called by agents to raise a memory conflict into disputed_entries.json
Includes optional suggested fix and file refs.
"""

import json
from pathlib import Path
from datetime import datetime
import argparse

# === 📁 Config ===
DISPUTE_LOG = Path("memory/sentinel/disputed_entries.json")
DISPUTE_LOG.parent.mkdir(parents=True, exist_ok=True)

# === 💾 Load/Save Helpers ===
def load_disputes():
    if not DISPUTE_LOG.exists():
        return []
    try:
        with open(DISPUTE_LOG, "r") as f:
            return json.load(f)
    except:
        return []

def append_dispute(entry):
    entries = load_disputes()
    entries.append(entry)
    with open(DISPUTE_LOG, "w") as f:
        json.dump(entries, f, indent=2)

# === 🚨 Raise conflict ===
def raise_conflict(agent, file_a, file_b, summary, suggestion=""):
    entry = {
        "type": "conflict_report",
        "agent": agent,
        "files_involved": [file_a, file_b],
        "summary": summary,
        "suggested_fix": suggestion,
        "status": "unresolved",
        "raised_by": agent,
        "date": datetime.now().isoformat()
    }
    append_dispute(entry)
    print("✅ Conflict raised and saved to disputed_entries.json")

# === 🧪 CLI ===
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Raise a memory conflict into Sentinel dispute log")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--file_a", required=True)
    parser.add_argument("--file_b", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--suggested_fix", default="")

    args = parser.parse_args()
    raise_conflict(
        args.agent,
        args.file_a,
        args.file_b,
        args.summary,
        args.suggested_fix
    )

