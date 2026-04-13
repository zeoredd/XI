#!/usr/bin/env python3
"""
✍️ amend_memory_entry.py
Appends a correction or clarification to an existing journal memory entry,
or optionally overwrites the entry if specified.
"""

import argparse
import json
from pathlib import Path
from datetime import datetime

def load_entry(path: Path):
    with open(path, "r") as f:
        return json.load(f)

def save_entry(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def append_correction(entry: dict, correction_text: str, amended_by="brandon"):
    amendments = entry.get("amendments", [])
    amendments.append({
        "timestamp": datetime.now().isoformat(),
        "by": amended_by,
        "note": correction_text
    })
    entry["amendments"] = amendments
    return entry

def overwrite_entry(entry: dict, new_text: str, amended_by="brandon"):
    entry["summary"] = new_text  # or 'thoughts' or 'content' based on target
    entry["last_overwritten"] = {
        "timestamp": datetime.now().isoformat(),
        "by": amended_by
    }
    return entry

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Amend a memory journal entry")
    parser.add_argument("file", type=str, help="Path to the journal entry JSON")
    parser.add_argument("--append", type=str, help="Correction or note to append")
    parser.add_argument("--overwrite", type=str, help="New content to overwrite with")
    parser.add_argument("--field", type=str, default="summary", help="Field to amend (default: summary)")
    parser.add_argument("--by", type=str, default="brandon", help="Who is amending the entry")

    args = parser.parse_args()
    file_path = Path(args.file)

    if not file_path.exists():
        print(f"❌ File does not exist: {file_path}")
        exit(1)

    entry = load_entry(file_path)

    if args.append:
        print(f"📎 Appending correction to {args.field}...\nNote: {args.append}")
        entry = append_correction(entry, args.append, amended_by=args.by)

    elif args.overwrite:
        print(f"⚠️ Overwriting {args.field} field...\nNew content: {args.overwrite}")
        entry[args.field] = args.overwrite
        entry = overwrite_entry(entry, args.overwrite, amended_by=args.by)

    else:
        print("❌ No amendment provided. Use --append or --overwrite.")
        exit(1)

    save_entry(file_path, entry)
    print(f"✅ Entry updated: {file_path}")

# Trigger index update
try:
    subprocess.run([
        "python3", "abilities/common_abilities/memory_index_daemon.py",
        "--reindex", str(file_path)
    ])
    print("🔁 Reindex triggered.")
except Exception as e:
    print(f"⚠️ Failed to reindex: {e}")


